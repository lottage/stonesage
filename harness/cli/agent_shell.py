"""
Interactive /agent Management Sub-Shell.
Provides agent lifecycle commands, node assignment, selection, and OpenClaw Socratic builder in the CLI.
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from ..config import fleet_config
from ..core.node_scheduler import node_scheduler
from ..core.openclaw_engine import openclaw_engine
from ..core.aevum_mesh import aevum_mesh
from ..data_fabric.pg_storage import relational_storage

console = Console()


class AgentShell:
    active_agent: Optional[Dict[str, Any]] = None
    active_project: Optional[Dict[str, Any]] = None

    @classmethod
    def handle_agent_command(cls, args: list):
        if not args:
            cls.print_help()
            return

        subcmd = args[0].lower()
        subargs = args[1:]

        if subcmd in ("list", "-l"):
            cls.list_agents()
        elif subcmd == "build":
            cls.build_agent_interactive()
        elif subcmd == "select":
            cls.select_agent(subargs)
        elif subcmd in ("detach", "deselect", "unselect", "clear-active"):
            cls.detach_agent()
        elif subcmd in ("bind", "node", "assign"):
            cls.handle_bind(subargs)
        elif subcmd == "task":
            cls.handle_task(subargs)
        elif subcmd == "clear":
            cls.handle_clear(subargs)
        elif subcmd == "play":
            cls.handle_play(subargs)
        elif subcmd == "status":
            cls.print_fleet_slots()
        elif subcmd in ("promote", "export", "extract"):
            from .commands import CommandRegistry
            CommandRegistry.handle_project(["promote"] + subargs)
        elif subcmd in ("mate", "reproduce", "cross", "crossover"):
            from .commands import CommandRegistry
            # If an active agent is attached and user didn't specify parent_a, pass active agent as parent_a
            passed_args = ["mate"]
            if cls.active_agent and (not subargs or len(subargs) == 1):
                passed_args.append(cls.active_agent["agent_id"])
                if subargs:
                    passed_args.append(subargs[0])
            else:
                passed_args.extend(subargs)
            CommandRegistry.handle_mesh(passed_args)
        else:
            console.print(f"[red]Unknown /agent subcommand '{subcmd}'. Type /agent for help.[/red]")

    @classmethod
    def print_help(cls):
        active_label = f" (Active: [bold green]{cls.active_agent['name']}[/bold green])" if cls.active_agent else ""
        console.print(Panel(
            f"[bold cyan][*] Aevum Mesh & OpenClaw Agent Management Sub-Shell{active_label}[/bold cyan]\n\n"
            "[bold green]/agent list[/bold green]                  - List all registered agents, assigned nodes, tasks & status\n"
            "[bold green]/agent select <id>[/bold green]             - Attach to a specific agent's stream\n"
            "[bold green]/agent detach[/bold green]                  - Detach from active agent & revert to blank direct model\n"
            "[bold green]/agent bind <id> [node][/bold green]       - Bind agent to a node (see /models poll for ids)\n"
            "[bold green]/agent task <id> <desc>[/bold green]        - Assign an operator task to an agent\n"
            "[bold green]/agent clear <id>[/bold green]              - Clear user task and return agent to idle status\n"
            "[bold green]/agent play [id] [mode][/bold green]        - Dispatch agent to autonomous play (The Agora / Dossier)\n"
            "[bold green]/agent mate [partner][/bold green]           - Bilateral digital reproduction with another agent into Gen-(N+1)\n"
            "[bold green]/agent promote [proj][/bold green]          - Promote & crystallize agent soul from project to cluster-wide\n"
            "[bold green]/agent build[/bold green]                 - Launch OpenClaw Socratic interview to create an agent\n"
            "[bold green]/agent status[/bold green]                - View live slot occupancy across cluster and edge fleet nodes\n",
            title="Agent Shell Help",
            border_style="cyan"
        ))

    @classmethod
    def detach_agent(cls):
        if cls.active_agent:
            old_name = cls.active_agent.get("name", "Agent")
            cls.active_agent = None
            console.print(f"[bold green][OK] Detached from Agent '{old_name}'.[/bold green]")
            console.print("[dim]Reverted to raw, non-personal direct response mode with blank model.[/dim]")
        else:
            console.print("[dim]No agent currently attached. Already in direct blank model mode.[/dim]")

    @classmethod
    def handle_bind(cls, args: list):
        if not args:
            console.print("[yellow]Usage: /agent bind <agent_id> [node_id][/yellow]")
            console.print("[dim]Available nodes: node2_edge (Edge Worker Node), node1_primary (Coordinator), node1_secondary (Worker)[/dim]")
            return
        aid = args[0].strip().lower()
        target_node = args[1].strip().lower() if len(args) > 1 else "node2_edge"
        if target_node in ("edge", "ally", "ally_x", "ally-x", "node2_edge", "node2_edge"):
            target_node = "node2_edge"
        elif target_node in ("primary", "vm102", "coordinator"):
            target_node = "node1_primary"
        elif target_node in ("secondary", "worker"):
            target_node = "node1_secondary"

        success = relational_storage.heartbeat_hive_agent(
            agent_id=aid,
            node_id=target_node,
            status="idle"
        )
        if success:
            console.print(f"[bold green][OK] Successfully bound agent '{aid}' to node '{target_node}'.[/bold green]")
            if cls.active_agent and cls.active_agent.get("agent_id") == aid:
                cls.active_agent["assigned_node"] = target_node
        else:
            console.print(f"[bold red][ERR] Failed to bind agent '{aid}'.[/bold red]")

    @classmethod
    def handle_task(cls, args: list):
        if len(args) < 2:
            console.print("[yellow]Usage: /agent task <agent_id> <task description>[/yellow]")
            return
        aid = args[0].strip().lower()
        task_desc = " ".join(args[1:]).strip()
        success = aevum_mesh.assign_user_task(aid, task_desc)
        if success:
            console.print(f"[bold green][OK] Assigned task to agent '{aid}':[/bold green] [white]{task_desc}[/white]")
        else:
            console.print(f"[bold red][ERR] Failed to assign task to '{aid}'.[/bold red]")

    @classmethod
    def handle_clear(cls, args: list):
        if not args:
            if cls.active_agent:
                aid = cls.active_agent["agent_id"]
            else:
                console.print("[yellow]Usage: /agent clear <agent_id>[/yellow]")
                return
        else:
            aid = args[0].strip().lower()
        success = aevum_mesh.clear_user_task(aid)
        if success:
            console.print(f"[bold green][OK] Cleared active task for agent '{aid}'. Agent returned to idle state.[/bold green]")
        else:
            console.print(f"[bold red][ERR] Failed to clear task for '{aid}'.[/bold red]")

    @classmethod
    def handle_play(cls, args: list):
        aid = None
        mode = "auto"
        if args:
            aid = args[0].strip().lower()
            if len(args) > 1:
                mode = args[1].strip().lower()
        elif cls.active_agent:
            aid = cls.active_agent["agent_id"]

        result = aevum_mesh.trigger_autonomous_play(agent_id=aid, mode=mode)
        if result.get("status") == "blocked":
            console.print(f"[bold yellow][BLOCKED] {result.get('message')}[/bold yellow]")
        elif result.get("status") == "dispatched":
            console.print(Panel(
                f"[bold green][OK] Autonomous Play Dispatched for '{result.get('agent_id')}'[/bold green]\n\n"
                f"[white]• Mode:[/white] [cyan]{result.get('mode')}[/cyan]\n"
                f"[white]• Topic:[/white] [bold yellow]{result.get('topic')}[/bold yellow]\n"
                f"[white]• Target:[/white] [magenta]{result.get('target')}[/magenta]\n"
                f"[white]• Details:[/white] [dim]{result.get('message')}[/dim]",
                title="Aevum Mesh - Autonomous Activity",
                border_style="cyan"
            ))
        else:
            console.print(f"[yellow]{result.get('message', 'No action taken.')}[/yellow]")

    @classmethod
    def list_agents(cls):
        from ..core.founders import get_all_founders
        founders = get_all_founders()
        mesh_agents = aevum_mesh.list_agents()

        active_id = cls.active_agent.get("agent_id") if cls.active_agent else None

        # Build unified agent registry: Valar Pantheon Founders first, then dynamic mesh agents
        seen_ids = set()
        unified_agents = []
        for f in founders:
            if f.id not in seen_ids:
                seen_ids.add(f.id)
                assigned = "node1_primary" if f.id in ("aule", "aevum") else "node1_secondary"
                unified_agents.append({
                    "agent_id": f.id,
                    "name": f.name,
                    "assigned_node": assigned,
                    "current_task": f"Pillar Council ({f.title})",
                    "status": "idle",
                    "last_heartbeat_hive": "Founding Pillar (Always Online)"
                })
        for a in mesh_agents:
            if a.get("agent_id") not in seen_ids:
                seen_ids.add(a.get("agent_id"))
                unified_agents.append(a)

        if not unified_agents:
            console.print("[yellow]No registered agents found. Type '/agent build' to create one.[/yellow]")
            return

        table = Table(title="[*] Aevum Mesh - Sovereign Agent Registry & Valar Pantheon", border_style="cyan")
        table.add_column("Agent ID", style="bold green")
        table.add_column("Display Name", style="cyan")
        table.add_column("Assigned Node", style="magenta")
        table.add_column("Current Task (Operator / Council)", style="white")
        table.add_column("Mesh Status", style="bold")
        table.add_column("Heartbeat / Availability", style="dim")

        for a in unified_agents:
            aid = a["agent_id"]
            is_selected = (aid == active_id)

            status = a.get("status", "idle")
            if is_selected:
                status_display = "[bold green][ACTIVE-SESSION][/bold green]"
            elif status == "active":
                status_display = "[bold yellow][BUSY-TASK][/bold yellow]"
            elif status == "autonomous_dossier":
                status_display = "[bold cyan][DOSSIER][/bold cyan]"
            elif status == "autonomous_agora":
                status_display = "[bold magenta][AGORA][/bold magenta]"
            else:
                status_display = "[dim]Idle (Ready)[/dim]"

            task = a.get("current_task")
            if task:
                task_display = f"[bold yellow]{task[:45]}[/bold yellow]"
            elif a.get("autonomous_focus"):
                task_display = f"[dim cyan]{a.get('autonomous_focus')[:45]}[/dim cyan]"
            else:
                task_display = "[dim]None (Idle / Free for Play)[/dim]"

            heartbeat = a.get("last_heartbeat_hive")
            if isinstance(heartbeat, (int, float)):
                import datetime
                hb_str = datetime.datetime.fromtimestamp(heartbeat).strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(heartbeat, str) and heartbeat:
                hb_str = heartbeat.split(".")[0].replace("T", " ")
            else:
                hb_str = "Never"

            table.add_row(
                aid,
                a.get("name", aid.title()),
                a.get("assigned_node", "node1_primary"),
                task_display,
                status_display,
                hb_str
            )

        console.print(table)
        if cls.active_agent:
            console.print(f"[dim]Currently attached to:[/dim] [bold green]{cls.active_agent.get('name')}[/bold green] ([cyan]{cls.active_agent.get('agent_id')}[/cyan])")

    @classmethod
    def select_agent(cls, args: list):
        from ..core.founders import get_all_founders, get_founder
        founders = get_all_founders()
        founder_ids = [f.id for f in founders]

        profiles = openclaw_engine.list_profiles()
        available_ids = list(founder_ids)
        for p in profiles:
            if p["agent_id"] not in available_ids:
                available_ids.append(p["agent_id"])

        sessions = relational_storage.list_sessions()
        for s in sessions:
            aid = s.get("agent_id")
            if aid and aid not in available_ids:
                available_ids.append(aid)

        if not args:
            if not available_ids:
                console.print("[yellow]No agents available to select. Type '/agent build' to create one first.[/yellow]")
                return
            target_id = Prompt.ask("[bold green]Select Agent ID[/bold green]", choices=available_ids, default=available_ids[0])
        else:
            target_id = args[0].strip().lower()

        # Load profile from disk or session or Founder registry
        profile = openclaw_engine.get_profile(target_id)
        if not profile:
            founder = get_founder(target_id)
            if founder:
                profile = {
                    "agent_id": founder.id,
                    "name": founder.name,
                    "role": founder.title,
                    "assigned_node": "node1_primary" if founder.id in ("aule", "aevum") else "node1_secondary",
                    "autonomy_level": "tiered",
                    "is_founder": True
                }
        if not profile:
            for s in sessions:
                if s.get("agent_id", "").lower() == target_id:
                    profile = {
                        "agent_id": s["agent_id"],
                        "name": s.get("title", s["agent_id"].title()),
                        "role": "Session Agent",
                        "assigned_node": s.get("assigned_node", "node1_primary"),
                        "autonomy_level": s.get("permissions", {}).get("autonomy_level", "tiered")
                    }
                    break

        if not profile:
            console.print(f"[bold red][ERR] Agent '{target_id}' not found.[/bold red]")
            console.print(f"[dim]Available agents: {', '.join(available_ids) if available_ids else 'None'}[/dim]")
            return

        cls.active_agent = profile

        # Ensure active session in relational storage
        session_id = f"sess_{target_id}"
        relational_storage.save_session(
            session_id=session_id,
            agent_id=target_id,
            assigned_node=profile.get("assigned_node", "node1_primary"),
            status="active",
            metadata={
                "title": profile.get("name", target_id.title()),
                "role": profile.get("role", "Autonomous Agent"),
                "autonomy_level": profile.get("autonomy_level", "tiered"),
            }
        )

        console.print(Panel(
            f"[bold green][OK] Successfully attached to Agent '{profile.get('name')}'[/bold green]\n\n"
            f"[white]• Agent ID:[/white] [cyan]{profile.get('agent_id')}[/cyan]\n"
            f"[white]• Role:[/white] [white]{profile.get('role', 'Autonomous Agent')}[/white]\n"
            f"[white]• Mission:[/white] [dim]{profile.get('mission', '(General purpose)')}[/dim]\n"
            f"[white]• Assigned Node:[/white] [magenta]{profile.get('assigned_node', 'node1_primary')}[/magenta]\n"
            f"[white]• Autonomy Level:[/white] [yellow]{profile.get('autonomy_level', 'tiered')}[/yellow]\n\n"
            "[dim]Conversations in the CLI will now execute in this agent's persona and routing context.[/dim]",
            title="Agent Attached",
            border_style="green"
        ))

    @classmethod
    def print_fleet_slots(cls):
        status = node_scheduler.get_fleet_status()
        table = Table(title="[*] Cluster Fleet Slot Allocations", border_style="cyan")
        table.add_column("Node Name", style="bold green")
        table.add_column("Role", style="magenta")
        table.add_column("Slots Occupied", justify="center")
        table.add_column("Active Model", style="white")

        for n in status:
            table.add_row(
                n["name"],
                n["role"],
                f"{n['occupied_slots']}/{n['total_slots']}",
                str(n["active_model"])
            )
        console.print(table)

    @classmethod
    def build_agent_interactive(cls):
        console.print(Panel(
            "[bold cyan][*] OpenClaw Socratic Agent Builder Wizard[/bold cyan]\n"
            "This wizard will compile IDENTITY.md, SOUL.md, AGENTS.md, and TOOLS.md for your new agent.",
            border_style="cyan"
        ))
        name = Prompt.ask("[bold green]Agent Display Name[/bold green]", default="Systems Auditor")
        agent_id = name.lower().replace(" ", "-")
        role = Prompt.ask("[bold green]Specialized Role[/bold green]", default="Concurrency & Invariant Auditor")
        mission = Prompt.ask("[bold green]Primary Mission[/bold green]", default="Inspect code for race conditions and mathematical soundness.")
        node = Prompt.ask(
            "[bold green]Assigned Compute Node[/bold green] (" + " / ".join(fleet_config.nodes) + ")",
            default="node1_primary"
        )
        autonomy = Prompt.ask("[bold green]Autonomy Level[/bold green] (tiered / strict / autonomous)", default="tiered")

        console.print(f"\n[cyan]Compiling OpenClaw contracts for '{name}'...[/cyan]")
        contract = openclaw_engine.build_contracts(
            agent_id=agent_id,
            name=name,
            role=role,
            mission=mission,
            autonomy_level=autonomy,
            assigned_node=node,
        )

        # Register session in relational storage
        session_id = f"sess_{agent_id}"
        relational_storage.save_session(
            session_id=session_id,
            agent_id=agent_id,
            assigned_node=node,
            status="active",
            metadata={
                "title": name,
                "role": role,
                "autonomy_level": autonomy
            }
        )

        # Automatically attach newly created agent
        cls.active_agent = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "mission": mission,
            "assigned_node": node,
            "autonomy_level": autonomy
        }

        console.print(f"[bold green][OK] Successfully generated 4 OpenClaw workspace contracts in server profiles![/bold green]")
        console.print(f"[bold green][OK] Agent '{name}' is now active and attached to your CLI session.[/bold green]")
