"""
Multi-Stream Concurrent Reasoning Orchestrator & TUI.
Provides full interactive user control over prompts, agent selection,
compute nodes, and models across the Aevum cluster.
Renders real-time side-by-side streams with live <think> traces and speeds.
"""

import os
import sys
import time
import json
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

# Safe encoding for Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console, Group
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.prompt import Prompt

from ..config import fleet_config
from ..core.llama_client import LlamaClient
from ..core.context_fabric import context_fabric
from ..core.aevum_mesh import aevum_mesh
from ..core.openclaw_engine import openclaw_engine

logger = logging.getLogger("harness.cli.multi_stream")
console = Console()


@dataclass
class AgentStreamConfig:
    agent_id: str
    agent_name: str
    role: str
    node_id: str
    node_name: str
    endpoint_url: str
    model_name: Optional[str] = None
    system_prompt: str = ""
    slot_id: int = 0


@dataclass
class AgentStreamState:
    agent_id: str
    agent_name: str
    node_id: str
    node_name: str
    model_name: str
    slot_id: int
    status: str = "connecting"  # connecting, thinking, executing, done, interrupted, error
    current_thought: str = ""
    current_content: str = ""
    tokens_generated: int = 0
    tokens_per_sec: float = 0.0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    last_update: float = field(default_factory=time.time)
    error_message: str = ""

    def append_chunk(self, thought: str = "", content: str = "", tps: float = 0.0) -> None:
        if thought:
            self.current_thought += thought
            self.status = "thinking"
        if content:
            self.current_content += content
            self.status = "executing"
        if tps > 0:
            self.tokens_per_sec = tps
        self.tokens_generated += len((thought + content).split())
        self.last_update = time.time()


class MultiStreamTUI:
    """Manages live side-by-side terminal rendering of active agents."""

    def __init__(self, task_title: str = "Concurrent Agent Execution"):
        self.task_title = task_title
        self.streams: Dict[str, AgentStreamState] = {}

    def register_stream(
        self,
        agent_id: str,
        agent_name: str,
        node_id: str,
        node_name: str,
        model_name: str,
        slot_id: int
    ) -> AgentStreamState:
        state = AgentStreamState(
            agent_id=agent_id,
            agent_name=agent_name,
            node_id=node_id,
            node_name=node_name,
            model_name=model_name,
            slot_id=slot_id
        )
        self.streams[agent_id] = state
        return state

    def update_stream(self, agent_id: str, thought: str = "", content: str = "", tps: float = 0.0, status: Optional[str] = None) -> None:
        if agent_id in self.streams:
            s = self.streams[agent_id]
            if status:
                s.status = status
            s.append_chunk(thought, content, tps)

    def mark_done(self, agent_id: str) -> None:
        if agent_id in self.streams:
            self.streams[agent_id].status = "done"
            self.streams[agent_id].end_time = time.time()

    def mark_interrupted(self, agent_id: str) -> None:
        if agent_id in self.streams:
            self.streams[agent_id].status = "interrupted"
            self.streams[agent_id].end_time = time.time()

    def mark_error(self, agent_id: str, err: str) -> None:
        if agent_id in self.streams:
            s = self.streams[agent_id]
            s.status = "error"
            s.error_message = err
            s.current_content += f"\n[bold red][Error: {err}][/bold red]"
            s.end_time = time.time()

    def render_view(self) -> Any:
        table = Table.grid(expand=True, padding=1)
        active_items = list(self.streams.values())
        if not active_items:
            return Panel(Text("[No active agent reasoning streams]", justify="center"), title="Autonomous Fleet TUI")

        columns_count = min(len(active_items), 3)
        for _ in range(columns_count):
            table.add_column(ratio=1)

        panels = []
        for state in active_items:
            # Color code based on status
            if state.status == "thinking":
                border_color = "cyan"
                status_display = "[bold cyan][THINKING][/bold cyan]"
            elif state.status == "executing":
                border_color = "green"
                status_display = "[bold green][EXECUTING][/bold green]"
            elif state.status == "connecting":
                border_color = "yellow"
                status_display = "[bold yellow][CONNECTING][/bold yellow]"
            elif state.status == "interrupted":
                border_color = "yellow"
                status_display = "[bold yellow][INTERRUPTED][/bold yellow]"
            elif state.status == "error":
                border_color = "red"
                status_display = "[bold red][ERROR][/bold red]"
            else:
                border_color = "bright_blue"
                status_display = "[bold bright_blue][DONE][/bold bright_blue]"

            thought_snippet = state.current_thought[-350:] if state.current_thought else "(awaiting thought trace...)"
            content_snippet = state.current_content[-350:] if state.current_content else "(awaiting action / code...)"

            group = Group(
                Text(f"Node: {state.node_name} | Model: {state.model_name[:28]}", style="bold yellow"),
                Text(f"Slot: #{state.slot_id} | Speed: {state.tokens_per_sec:.1f} t/s | Status: ", style="dim") + Text.from_markup(status_display),
                Panel(Text(thought_snippet, style="italic cyan"), title="[Think Trace]", border_style="cyan"),
                Panel(Text(content_snippet, style="white"), title="[Action / Code]", border_style="green"),
                Text(f"Tokens: {state.tokens_generated}", style="dim")
            )

            p = Panel(
                group,
                title=f"Agent: [bold white]{state.agent_name}[/bold white] ([cyan]{state.agent_id}[/cyan])",
                border_style=border_color,
            )
            panels.append(p)

        for i in range(0, len(panels), columns_count):
            row_slice = panels[i:i + columns_count]
            while len(row_slice) < columns_count:
                row_slice.append(Text(""))
            table.add_row(*row_slice)

        header_title = f"[bold cyan][AEVUM CLUSTER // CONCURRENT AGENT STREAMS][/bold cyan] - [dim]{self.task_title[:45]}[/dim]"
        return Panel(table, title=header_title, border_style="blue")


def interactive_stream_wizard(prompt_arg: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Socratic Interactive Configuration Wizard for Concurrent Agent Reasoning Streams.
    Allows full operator control over task prompt, agents, compute nodes, and models.
    """
    console.print(Panel(
        "[bold cyan]⚡ AEVUM MULTI-STREAM CONCURRENT ORCHESTRATOR WIZARD[/bold cyan]\n"
        "[dim]Full operator control: select task prompt, participating agents, and compute node endpoints.[/dim]",
        border_style="cyan"
    ))

    # 1. Prompt / Task Configuration
    if prompt_arg and prompt_arg.strip():
        user_prompt = prompt_arg.strip()
        console.print(f"[bold green]Target Task/Prompt:[/bold green] [white]{user_prompt}[/white]\n")
    else:
        user_prompt = Prompt.ask(
            "[bold green]Enter Prompt / Challenge for Concurrent Execution[/bold green]",
            default="Analyze concurrency hazards and implement a lock-free ring buffer in Python"
        ).strip()
        if not user_prompt:
            user_prompt = "Analyze concurrency hazards and implement a lock-free ring buffer in Python"

    # 2. Number of streams
    stream_count_str = Prompt.ask(
        "[bold green]Number of concurrent streams[/bold green]",
        choices=["2", "3"],
        default="2"
    )
    num_streams = int(stream_count_str)

    # 3. Discover Nodes and poll models live
    console.print("\n[dim]Polling homelab compute nodes and active models...[/dim]")
    node_inventory = fleet_config.poll_node_models()

    node_table = Table(title="[*] Available Homelab Compute Nodes", border_style="cyan")
    node_table.add_column("Node ID", style="bold green")
    node_table.add_column("Endpoint", style="white")
    node_table.add_column("Hardware / Description", style="dim")
    node_table.add_column("Live Status", style="bold")
    node_table.add_column("Active Model", style="cyan")

    node_choices = []
    for nid, n in fleet_config.nodes.items():
        node_choices.append(nid)
        inv = node_inventory.get(nid, {})
        st = inv.get("status", "unknown")
        st_markup = "[green]ONLINE[/green]" if st == "online" else "[red]OFFLINE[/red]"
        mdl = inv.get("model") or "(none)"
        node_table.add_row(nid, n.base_url, n.name, st_markup, mdl)
    node_choices.append("custom")
    node_table.add_row("custom", "User Defined", "External / Roaming HTTP Endpoint", "[yellow]MANUAL[/yellow]", "User Defined")
    console.print(node_table)

    # 4. Discover Available Agents
    registered_agents = aevum_mesh.list_agents()
    agent_id_map = {a["agent_id"]: a for a in registered_agents}
    
    # Ensure default built-in archetypes exist in choices
    if "coordinator" not in agent_id_map:
        agent_id_map["coordinator"] = {"agent_id": "coordinator", "name": f"Coordinator ({fleet_config.model('coordinator')})", "role": "Architectural Logic & Deep Reasoning"}
    if "worker" not in agent_id_map:
        agent_id_map["worker"] = {"agent_id": "worker", "name": f"Worker ({fleet_config.model('worker')})", "role": "Rapid Speed Ideation & Invariant Tester"}
    if "sentinel" not in agent_id_map:
        agent_id_map["sentinel"] = {"agent_id": "sentinel", "name": "Nexus Sentinel", "role": "Security & Concurrency Auditor"}

    agent_choices = list(agent_id_map.keys())

    # Build stream configs
    configs: List[AgentStreamConfig] = []
    default_nodes = ["node1_primary", "node2_edge", "node1_secondary"]
    
    for i in range(1, num_streams + 1):
        console.print(f"\n[bold cyan]── Stream #{i} Configuration ──────────────────────────────[/bold cyan]")
        
        # Agent selection
        def_agent = agent_choices[(i - 1) % len(agent_choices)]
        chosen_agent_id = Prompt.ask(
            f"[bold green]Select Agent #{i}[/bold green] (or type new name)",
            default=def_agent
        ).strip().lower()

        if chosen_agent_id in agent_id_map:
            ag_data = agent_id_map[chosen_agent_id]
            ag_name = ag_data.get("name", chosen_agent_id.title())
            ag_role = ag_data.get("role", "Autonomous Agent")
        else:
            ag_name = chosen_agent_id.replace("-", " ").title()
            ag_role = "Custom Reasoning Agent"

        # Node selection with dynamic slot capacity check & retry loop
        def_node = "node1_primary"
        for candidate in ["node1_primary", "node1_secondary", "node2_edge"]:
            if candidate in fleet_config.nodes:
                c_node = fleet_config.nodes[candidate]
                c_assigned = sum(1 for c in configs if c.node_id == candidate)
                if c_assigned < c_node.slots:
                    def_node = candidate
                    break

        while True:
            chosen_node = Prompt.ask(
                f"[bold green]Select Compute Node for Agent #{i}[/bold green]",
                choices=node_choices,
                default=def_node
            )

            if chosen_node != "custom":
                node_obj = fleet_config.nodes.get(chosen_node)
                if node_obj:
                    already_assigned_count = sum(1 for c in configs if c.node_id == chosen_node)
                    if already_assigned_count >= node_obj.slots:
                        assigned_stream_nums = [str(c.slot_id + 1) for c in configs if c.node_id == chosen_node]
                        console.print(
                            f"\n[bold red]❌ Capacity Exceeded on '{chosen_node}'[/bold red]\n"
                            f"[white]• Target Node:[/white] [magenta]{node_obj.name}[/magenta]\n"
                            f"[white]• Total Slots:[/white] [yellow]{node_obj.slots}[/yellow] slot(s)\n"
                            f"[white]• Already Assigned To:[/white] [cyan]Stream #{', #'.join(assigned_stream_nums)}[/cyan]\n\n"
                            f"[yellow]Assigning multiple concurrent streams to a single-slot instance causes request blocking and server freeze.[/yellow]\n"
                            f"[green]Please select an alternate available node (e.g. node1_secondary) or configure a custom endpoint.[/green]\n"
                        )
                        # Suggest next node with capacity
                        for candidate in node_choices:
                            if candidate != "custom" and candidate in fleet_config.nodes:
                                if sum(1 for c in configs if c.node_id == candidate) < fleet_config.nodes[candidate].slots:
                                    def_node = candidate
                                    break
                        continue
            break

        if chosen_node == "custom":
            endpoint_url = Prompt.ask("[bold green]Enter Custom API Base URL[/bold green]", default="http://localhost:8000/v1").strip()
            node_name = "Custom Endpoint"
            model_name = Prompt.ask("[bold green]Enter Model Name[/bold green]", default="custom-model").strip()
        else:
            node_obj = fleet_config.nodes[chosen_node]
            endpoint_url = node_obj.base_url
            node_name = node_obj.name
            
            # Check if multiple models available (e.g. LM Studio)
            inv = node_inventory.get(chosen_node, {})
            model_list = inv.get("models", [])
            if len(model_list) > 1:
                available_model_ids = [m.get("id") for m in model_list if m.get("id")]
                chosen_model = Prompt.ask(
                    f"[bold green]Select Model on {chosen_node}[/bold green]",
                    choices=available_model_ids[:8],
                    default=inv.get("model", available_model_ids[0])
                )
                model_name = chosen_model
            else:
                model_name = inv.get("model") or "local-model"

        # Build dynamic context/system prompt for the agent
        system_content = context_fabric.compile_dynamic_turn(user_query=user_prompt, agent_id=chosen_agent_id)

        cfg = AgentStreamConfig(
            agent_id=chosen_agent_id,
            agent_name=ag_name,
            role=ag_role,
            node_id=chosen_node,
            node_name=node_name,
            endpoint_url=endpoint_url,
            model_name=model_name,
            system_prompt=system_content,
            slot_id=i - 1
        )
        configs.append(cfg)

    # 5. Confirmation Panel
    summary_table = Table(title="[*] Concurrent Stream Execution Plan", border_style="yellow")
    summary_table.add_column("Stream", justify="center", style="bold yellow")
    summary_table.add_column("Agent Name", style="bold white")
    summary_table.add_column("Role", style="cyan")
    summary_table.add_column("Assigned Node", style="magenta")
    summary_table.add_column("Target Model", style="green")
    summary_table.add_column("Endpoint", style="dim")

    for idx, c in enumerate(configs, 1):
        summary_table.add_row(
            f"#{idx}",
            c.agent_name,
            c.role,
            c.node_name,
            str(c.model_name),
            c.endpoint_url
        )

    console.print("\n", summary_table)
    console.print(f"[dim]Task:[/dim] [white]{user_prompt}[/white]\n")

    confirm = Prompt.ask("[bold green]Launch Concurrent Streams now? [Y/n][/bold green]", default="y")
    if confirm.lower() not in ("y", "yes", ""):
        console.print("[yellow]Concurrent stream launch cancelled by operator.[/yellow]")
        return None

    return {
        "prompt": user_prompt,
        "configs": configs
    }


async def run_concurrent_streams(plan: Dict[str, Any]):
    """
    Executes concurrent live reasoning streams with side-by-side terminal TUI.
    Streams real tokens, <think> traces, speed (tps), and token counts from physical endpoints.
    """
    prompt = plan["prompt"]
    configs: List[AgentStreamConfig] = plan["configs"]

    tui = MultiStreamTUI(task_title=prompt)
    for c in configs:
        tui.register_stream(
            agent_id=c.agent_id,
            agent_name=c.agent_name,
            node_id=c.node_id,
            node_name=c.node_name,
            model_name=c.model_name or "local-model",
            slot_id=c.slot_id
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Background non-blocking key listener for ESC, q, Ctrl+C on Windows
    def key_listener():
        if sys.platform == "win32":
            try:
                import msvcrt
                while not stop_event.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b'\x1b', b'q', b'Q', b'\x03'):
                            loop.call_soon_threadsafe(stop_event.set)
                            return
                    time.sleep(0.04)
            except Exception:
                pass

    listener_thread = threading.Thread(target=key_listener, daemon=True)
    listener_thread.start()

    interrupted = False

    async def stream_worker(cfg: AgentStreamConfig, live: Live):
        client = LlamaClient(base_url=cfg.endpoint_url)
        messages = [
            {"role": "system", "content": cfg.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        tui.update_stream(cfg.agent_id, status="connecting")
        live.update(tui.render_view())

        try:
            async for chunk in client.chat_stream(
                messages=messages,
                request_id=f"stream_{cfg.agent_id}",
                model=cfg.model_name
            ):
                if stop_event.is_set():
                    client.abort_request(f"stream_{cfg.agent_id}")
                    tui.mark_interrupted(cfg.agent_id)
                    live.update(tui.render_view())
                    return

                if chunk.chunk_type == "thought":
                    tui.update_stream(cfg.agent_id, thought=chunk.content, tps=chunk.tokens_per_sec)
                elif chunk.chunk_type == "output":
                    tui.update_stream(cfg.agent_id, content=chunk.content, tps=chunk.tokens_per_sec)
                elif chunk.chunk_type == "nudged":
                    tui.update_stream(cfg.agent_id, content=f"\n[NUDGED: {chunk.content}]\n")
                elif chunk.chunk_type == "error":
                    tui.mark_error(cfg.agent_id, chunk.content)

                live.update(tui.render_view())

            tui.mark_done(cfg.agent_id)
            live.update(tui.render_view())

        except (asyncio.CancelledError, KeyboardInterrupt):
            tui.mark_interrupted(cfg.agent_id)
            live.update(tui.render_view())
        except Exception as ex:
            tui.mark_error(cfg.agent_id, str(ex))
            live.update(tui.render_view())

    console.print("\n[bold cyan][STREAMS] Starting live multiplexed agent reasoning execution...[/bold cyan]")
    console.print("[dim]Press [ESC] or [Ctrl+C] at any time to interrupt streams and return to prompt.[/dim]\n")

    try:
        with Live(tui.render_view(), refresh_per_second=6, console=console) as live:
            tasks = [stream_worker(c, live) for c in configs]
            await asyncio.gather(*tasks, return_exceptions=True)

    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_event.set()
        interrupted = True
    finally:
        stop_event.set()

    # Final Benchmark Summary
    if stop_event.is_set():
        console.print("\n[bold yellow]⚡ [Concurrent Streams Interrupted] Halted by operator (ESC / Ctrl+C). Session remains active.[/bold yellow]\n")
    else:
        console.print("\n[bold green][OK] Concurrent execution completed across all participating nodes.[/bold green]\n")

    report_table = Table(title="[*] Concurrent Execution Metrics & Benchmarks", border_style="cyan")
    report_table.add_column("Agent", style="bold white")
    report_table.add_column("Compute Node", style="magenta")
    report_table.add_column("Model ID", style="cyan")
    report_table.add_column("Tokens", justify="right", style="yellow")
    report_table.add_column("Speed (t/s)", justify="right", style="green")
    report_table.add_column("Status", style="bold")

    for s in tui.streams.values():
        st_color = "green" if s.status == "done" else ("yellow" if s.status == "interrupted" else "red")
        report_table.add_row(
            s.agent_name,
            s.node_name,
            s.model_name[:30],
            str(s.tokens_generated),
            f"{s.tokens_per_sec:.1f}",
            f"[{st_color}]{s.status.upper()}[/{st_color}]"
        )
    console.print(report_table)


if __name__ == "__main__":
    plan = interactive_stream_wizard()
    if plan:
        asyncio.run(run_concurrent_streams(plan))
