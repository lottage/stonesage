"""
Local CLI 1st Entrypoint for the Unified LLM Harness.
Interactive hacker-grade terminal shell with slash commands, streaming, and rich UI.
"""

import sys
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from ..config import fleet_config
from .commands import CommandRegistry
from .agent_shell import AgentShell
from .completer import create_prompt_session
from ..core.llama_client import LlamaClient
from ..core.context_fabric import context_fabric

console = Console()

def print_banner():
    console.print(Panel(
        "[bold cyan]⚡ AEVUM UNIFIED LLM HARNESS & AGENT FLEET[/bold cyan]\n"
        "[dim]llama.cpp Orchestrator • 5-Tier Memory • OpenClaw • Distributed Edge Fleet[/dim]\n\n"
        "[bold green]Available Slash Commands:[/bold green]\n"
        "  [cyan]/project[/cyan] - Cross-endpoint project workspaces (list, enter, create, leave, status)\n"
        "  [cyan]/agent[/cyan]   - OpenClaw agent sub-shell (list, build, select, detach, bind, task)\n"
        "  [cyan]/node[/cyan]    - Device-agnostic compute node & fleet manager (status, list, models, load, bind)\n"
        "  [cyan]/slots[/cyan]   - Rescale parallel slots & verify parameter preservation (/slots <N>)\n"
        "  [cyan]/mesh[/cyan]    - Aevum Mesh Hive Network (status, play, task, clear)\n"
        "  [cyan]/nudge[/cyan]   - Send out-of-band non-destructive intervention to an agent\n"
        "  [cyan]/streams[/cyan]   - Multiplexed background thought streams ('/stream consensus <prompt>')\n"
        "  [cyan]/consensus[/cyan] - 3-stage multi-agent cross-examination & Founder consensus\n"
        "  [cyan]/presence[/cyan]  - Dynamic multi-signal presence governor (status, override, clear)\n"
        "  [cyan]/models[/cyan]    - Inspect fleet models ('/models switch' to swap models)\n"
        "  [cyan]/runtime[/cyan]   - Switch or benchmark compute runtime (v.03 Vulkan vs v.03-rocm HIP)\n"
        "  [cyan]/spec[/cyan]      - Client-side speculative decoding (worker drafts, coordinator verifies)\n"
        "  [cyan]/hf[/cyan]        - Search HuggingFace GGUF models\n"
        "  [cyan]/train[/cyan]     - Trigger QLoRA fine-tuning run\n"
        "  [cyan]/mem[/cyan]       - Search Valkey A-MEM atomic fact cards (< 35 tokens)\n"
        "  [cyan]/status[/cyan]    - Live cluster telemetry & 24/7 background agent activity\n"
        "  [cyan]/exit[/cyan]      - Quit the harness CLI\n\n"
        "  [dim]• Interrupt Hotkeys:[/dim] [bold yellow]ESC[/bold yellow] [dim]or[/dim] [bold yellow]Ctrl+C[/bold yellow] [dim]halts generation and nudges agent without closing shell.[/dim]\n",
        border_style="cyan"
    ))

async def async_chat_turn(primary_client: LlamaClient, user_query: str):
    from ..core.speculative import speculative_engine

    agent_id = AgentShell.active_agent.get("agent_id") if AgentShell.active_agent else None
    assistant_name = AgentShell.active_agent.get("name", "Assistant") if AgentShell.active_agent else "Assistant"
    project_dir = AgentShell.active_project.get("path") if AgentShell.active_project else None
    system_content = context_fabric.compile_dynamic_turn(user_query=user_query, agent_id=agent_id, project_dir=project_dir)

    # Route inference dynamically to active node or agent's assigned node
    target_node = fleet_config.get_active_node_id()
    if AgentShell.active_agent and "assigned_node" in AgentShell.active_agent:
        target_node = AgentShell.active_agent["assigned_node"]

    use_speculative = speculative_engine.should_use_speculative(
        is_interactive=True, task_type="user_turn"
    ) and target_node == "node1_primary"

    if use_speculative:
        client = primary_client
        node_badge = f"Speculative ({fleet_config.gpu('coordinator') or 'coordinator'} + {fleet_config.gpu('worker') or 'worker'})"
    else:
        node_info = fleet_config.nodes.get(target_node, fleet_config.get_active_node())
        client = LlamaClient(base_url=node_info.base_url)
        node_badge = node_info.name

    proj_suffix = f" • Project: {AgentShell.active_project['name']}" if AgentShell.active_project else ""

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_query}
    ]
    if AgentShell.active_agent:
        console.print(f"\n[bold green]{assistant_name}[/bold green] [dim]({node_badge}{proj_suffix})[/dim]: ", end="")
    else:
        console.print(f"\n[bold white]Assistant[/bold white] [dim]({node_badge}{proj_suffix} • Direct Model)[/dim]: ", end="")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def key_listener():
        if sys.platform == "win32":
            try:
                import msvcrt
                import time
                while not stop_event.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b'\x1b', b'q', b'Q', b'\x03'):  # ESC, 'q', Ctrl+C
                            loop.call_soon_threadsafe(stop_event.set)
                            return
                    time.sleep(0.04)
            except Exception:
                pass

    import threading
    listener_thread = threading.Thread(target=key_listener, daemon=True)
    listener_thread.start()

    interrupted = False
    try:
        if use_speculative:
            stream_iter = speculative_engine.generate_speculative_stream(messages=messages, request_id="cli_turn").__aiter__()
        else:
            stream_iter = client.chat_stream(messages=messages, request_id="cli_turn").__aiter__()
        while not stop_event.is_set():
            chunk_task = asyncio.create_task(stream_iter.__anext__())
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait([chunk_task, stop_task], return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()

            if stop_event.is_set():
                interrupted = True
                client.abort_request("cli_turn")
                break

            try:
                chunk = chunk_task.result()
            except StopAsyncIteration:
                break
            except asyncio.CancelledError:
                break

            if chunk.chunk_type == "thought":
                console.print(f"[dim italic]{chunk.content}[/dim italic]", end="")
                sys.stdout.flush()
            elif chunk.chunk_type == "output":
                console.print(f"[white]{chunk.content}[/white]", end="")
                sys.stdout.flush()
            elif chunk.chunk_type == "nudged":
                console.print(f"[bold yellow]{chunk.content}[/bold yellow]", end="")
                sys.stdout.flush()
            elif chunk.chunk_type == "error":
                console.print(f"\n[bold red]❌ [Model Error]: {chunk.content}[/bold red]\n")
                sys.stdout.flush()
                break

        console.print("\n")
        if interrupted:
            console.print("[bold yellow]⚡ [Interrupted / Nudged] Generation halted by operator (ESC). Session remains active.[/bold yellow]\n")

    except (KeyboardInterrupt, asyncio.CancelledError):
        stop_event.set()
        client.abort_request("cli_turn")
        console.print("\n[bold yellow]⚡ [Interrupted / Nudged] Generation halted by operator (Ctrl+C). Session remains active.[/bold yellow]\n")
    finally:
        stop_event.set()
        if 'stream_iter' in locals() and hasattr(stream_iter, "aclose"):
            try:
                await stream_iter.aclose()
            except Exception:
                pass

def get_prompt_badge() -> tuple[str, str]:
    node = fleet_config.get_active_node()
    model = node.active_model or "No Model"
    if "/" in model:
        model = model.split("/")[-1]
    elif "\\" in model:
        model = model.split("\\")[-1]
    if model.endswith(".gguf"):
        model = model[:-5]
    if len(model) > 22:
        model = model[:20] + "…"

    # short badge from live data: the GPU for engine nodes, the configured name for everything else
    role = {"node1_primary": "coordinator", "node1_secondary": "worker"}.get(node.node_id)
    short_node = (fleet_config.gpu(role) if role else "") or node.name.split(" (")[0][:16]
    return model, short_node

def main():
    print_banner()
    primary_client = LlamaClient(base_url=fleet_config.coordinator_url)
    prompt_session = create_prompt_session()

    # Pre-poll cluster nodes once at startup
    try:
        fleet_config.poll_node_models()
    except Exception:
        pass

    while True:
        try:
            m_label, n_label = get_prompt_badge()
            node_tag_html = f"[<b><ansimagenta>{m_label}</ansimagenta></b> @ <b><ansiblue>{n_label}</ansiblue></b>]"
            node_tag_raw = f"[[bold magenta]{m_label}[/bold magenta] @ [bold blue]{n_label}[/bold blue]]"

            if AgentShell.active_agent and AgentShell.active_project:
                ag_name = AgentShell.active_agent.get("name", "Agent")
                pr_name = AgentShell.active_project.get("name", "Project")
                prompt_html = f"\n<b><ansicyan>aevum</ansicyan></b> [<b><ansigreen>{ag_name}</ansigreen></b>] [<b><ansiyellow>{pr_name}</ansiyellow></b>] {node_tag_html}> "
                prompt_raw = f"\n[bold cyan]aevum[/bold cyan] [[bold green]{ag_name}[/bold green]] [[bold yellow]{pr_name}[/bold yellow]] {node_tag_raw}> "
            elif AgentShell.active_agent:
                ag_name = AgentShell.active_agent.get("name", "Agent")
                prompt_html = f"\n<b><ansicyan>aevum</ansicyan></b> [<b><ansigreen>{ag_name}</ansigreen></b>] {node_tag_html}> "
                prompt_raw = f"\n[bold cyan]aevum[/bold cyan] [[bold green]{ag_name}[/bold green]] {node_tag_raw}> "
            elif AgentShell.active_project:
                pr_name = AgentShell.active_project.get("name", "Project")
                prompt_html = f"\n<b><ansicyan>aevum</ansicyan></b> [<b><ansiyellow>{pr_name}</ansiyellow></b>] {node_tag_html}> "
                prompt_raw = f"\n[bold cyan]aevum[/bold cyan] [[bold yellow]{pr_name}[/bold yellow]] {node_tag_raw}> "
            else:
                prompt_html = f"\n<b><ansicyan>aevum</ansicyan></b> {node_tag_html}> "
                prompt_raw = f"\n[bold cyan]aevum[/bold cyan] {node_tag_raw}> "

            if prompt_session is not None:
                try:
                    from prompt_toolkit.formatted_text import HTML
                    user_input = prompt_session.prompt(HTML(prompt_html))
                except KeyboardInterrupt:
                    console.print("\n[dim]Input cleared. (Type /exit to quit)[/dim]")
                    continue
                except EOFError:
                    console.print("\n[dim]Session closed.[/dim]")
                    break
                except Exception:
                    user_input = Prompt.ask(prompt_raw)
            else:
                try:
                    user_input = Prompt.ask(prompt_raw)
                except KeyboardInterrupt:
                    console.print("\n[dim]Input cleared. (Type /exit to quit)[/dim]")
                    continue
                except EOFError:
                    console.print("\n[dim]Session closed.[/dim]")
                    break

            if not user_input or not user_input.strip():
                continue

            clean = user_input.strip()
            if clean in ("/exit", "exit", "quit", ":q"):
                console.print("[dim]Exiting Aevum Harness. Keep silicon humming.[/dim]")
                break

            # Record user interaction for Dynamic Presence Engine
            from ..core.presence_engine import presence_engine
            presence_engine.record_activity("cli")

            if clean.startswith("/"):
                parts = clean.split()
                cmd = parts[0].lower()
                args = parts[1:]

                if cmd in ("/project", "/proj"):
                    CommandRegistry.handle_project(args)
                elif cmd == "/agent":
                    AgentShell.handle_agent_command(args)
                elif cmd in ("/node", "/fleet", "/endpoint"):
                    CommandRegistry.handle_node(args)
                elif cmd == "/ally":
                    console.print("[dim]Note: '/ally' is deprecated. Use '/node' for device-agnostic fleet management.[/dim]")
                    CommandRegistry.handle_node(args)
                elif cmd in ("/slots", "/slot"):
                    CommandRegistry.handle_slots(args)
                elif cmd in ("/mesh", "/hive"):
                    CommandRegistry.handle_mesh(args)
                elif cmd == "/nudge":
                    CommandRegistry.handle_nudge(args)
                elif cmd in ("/streams", "/stream"):
                    CommandRegistry.handle_streams(args)
                elif cmd in ("/consensus", "/debate"):
                    CommandRegistry.handle_consensus(args)
                elif cmd in ("/presence", "/who"):
                    if args and args[0].lower() == "override":
                        ov = args[1].lower() if len(args) > 1 else "interactive"
                        if ov in ("interactive", "active"):
                            presence_engine.set_manual_override("INTERACTIVE_PRIORITY")
                            console.print("[green][OK] Manual override set to INTERACTIVE_PRIORITY.[/green]")
                        elif ov in ("idle", "rumination", "background"):
                            presence_engine.set_manual_override("SOVEREIGN_IDLE_RUMINATION")
                            console.print("[yellow][OK] Manual override set to SOVEREIGN_IDLE_RUMINATION.[/yellow]")
                        else:
                            console.print(f"[red]Unknown override '{ov}'. Use 'interactive' or 'idle'.[/red]")
                    elif args and args[0].lower() in ("clear", "reset", "auto"):
                        presence_engine.set_manual_override(None)
                        console.print("[green][OK] Manual override cleared. Reverted to automatic presence fusion.[/green]")
                    else:
                        st = presence_engine.evaluate_presence()
                        color = "green" if st["is_interactive"] else "yellow"
                        console.print(Panel(
                            f"[bold cyan]⚡ DYNAMIC MULTI-SIGNAL PRESENCE & DUTY CYCLE ENGINE[/bold cyan]\n\n"
                            f"[white]• Current State:[/white] [bold {color}]{st['state']}[/bold {color}]\n"
                            f"[white]• Interactive Priority:[/white] [cyan]{st['is_interactive']}[/cyan]\n"
                            f"[white]• Workstation Inactivity:[/white] [magenta]{st['workstation_idle_minutes']} minutes[/magenta]\n"
                            f"[white]• Phone Presence (HA):[/white] [yellow]{st['phone_presence']}[/yellow]\n"
                            f"[white]• Diagnostic Reason:[/white] [dim]{st['reason']}[/dim]\n\n"
                            f"[dim]Commands: '/presence override interactive', '/presence override idle', '/presence clear'[/dim]",
                            border_style="cyan"
                        ))
                elif cmd in ("/model", "/models"):
                    CommandRegistry.handle_models(args)
                elif cmd in ("/runtime", "/backend"):
                    CommandRegistry.handle_runtime(args)
                elif cmd in ("/speculative", "/spec"):
                    CommandRegistry.handle_speculative(args)
                elif cmd == "/hf":
                    CommandRegistry.handle_hf(args)
                elif cmd == "/train":
                    CommandRegistry.handle_train(args)
                elif cmd == "/mem":
                    CommandRegistry.handle_mem(args)
                elif cmd == "/policy":
                    CommandRegistry.handle_policy(args)
                elif cmd in ("/status", "/activity", "/cluster"):
                    CommandRegistry.handle_status(args)
                elif cmd in ("/help", "/h"):
                    print_banner()
                else:
                    console.print(f"[red]Unknown slash command '{cmd}'. Type /help for available commands.[/red]")
                continue

            # Standard chat interaction with safe KeyboardInterrupt trapping
            try:
                asyncio.run(async_chat_turn(primary_client, clean))
            except KeyboardInterrupt:
                primary_client.abort_request("cli_turn")
                console.print("\n[bold yellow]⚡ [Interrupted / Nudged] Generation halted by operator (Ctrl+C). Session remains active.[/bold yellow]\n")

        except KeyboardInterrupt:
            console.print("\n[dim]Input cleared. (Type /exit to quit)[/dim]")
            continue
        except EOFError:
            console.print("\n[dim]Session closed.[/dim]")
            break

if __name__ == "__main__":
    main()
