import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from ..config import fleet_config
from ..core.nudge_tool import harness_nudge
from ..security.policy_engine import policy_engine
from ..data_fabric.valkey_amem import valkey_amem
from ..core.offload_calc import offload_engine

console = Console()

class CommandRegistry:
    @staticmethod
    def handle_nudge(args: List[str]):
        if not args:
            console.print("[yellow]Usage: /nudge <agent_id> [optional directive][/yellow]")
            return
        agent_id = args[0]
        directive = " ".join(args[1:]) if len(args) > 1 else None
        console.print(f"[bold cyan][NUDGE] Sending nudge to agent '[green]{agent_id}[/green]'...[/bold cyan]")
        res = harness_nudge.nudge(agent_id, directive=directive)
        if res.get("ok"):
            console.print(f"[bold green][OK] Agent '{agent_id}' nudged and redirected successfully.[/bold green]")
        else:
            console.print(f"[bold red][ERR] Failed to nudge: {res.get('error', res.get('message'))}[/bold red]")

    @staticmethod
    def handle_slots(args: List[str]):
        from ..edge_fleet.ally_model_manager import node_manager
        from rich.prompt import Prompt

        target_slots = None
        if args and args[0].isdigit():
            target_slots = int(args[0])
        else:
            # Interactive prompt
            active_inst = node_manager.get_active_instance()
            current_slots = active_inst.get("config", {}).get("parallel", 1) if active_inst else 1
            console.print(Panel(
                "[bold cyan]⚡ DYNAMIC PARALLEL EXECUTION SLOT SCALER[/bold cyan]\n"
                f"[white]• Target Compute Node:[/white] [magenta]{node_manager.node_name} ({node_manager.node_url})[/magenta]\n"
                f"[white]• Current Parallel Slots:[/white] [yellow]{current_slots}[/yellow]\n"
                "[dim]Strict Invariant: Audits & preserves 100% of runtime parameters before reloading with N slots.[/dim]",
                border_style="cyan"
            ))
            slot_choice = Prompt.ask(
                "[bold green]Select Desired Parallel Slots[/bold green]",
                choices=["1", "2", "3", "4", "8"],
                default="4" if current_slots != 4 else "2"
            )
            target_slots = int(slot_choice)

        console.print(f"\n[cyan][*] Rescaling parallel slots to [bold yellow]{target_slots}[/bold yellow]...[/cyan]")
        console.print("[dim]Auditing current runtime parameters, checking memory safety, and reloading instance...[/dim]")

        res = node_manager.change_parallel_slots(target_slots)

        if not res.get("ok"):
            console.print(f"[bold red][ERR] Failed to rescale slots: {res.get('error')}[/bold red]")
            return

        if res.get("unchanged"):
            console.print(f"[yellow]{res.get('message')}[/yellow]")
            return

        # Render Parameter Pre-Unload vs Post-Reload Verification Audit Table
        report = res.get("verification_report", [])
        table = Table(title="⚡ Parameter Pre-Unload vs Post-Reload Verification Audit", border_style="cyan")
        table.add_column("Parameter", style="cyan")
        table.add_column("Pre-Unload State", style="white")
        table.add_column("Target Requested", style="yellow")
        table.add_column("Post-Reload State", style="bold white")
        table.add_column("Verification Verdict", justify="center", style="bold")

        for item in report:
            v_status = "[bold green][MATCH][/bold green]" if item.get("verified") else "[bold red][MISMATCH][/bold red]"
            p_name = item.get("parameter", "")
            pre_val = str(item.get("pre_unload"))
            tgt_val = str(item.get("target", item.get("pre_unload")))
            post_val = str(item.get("post_reload"))
            table.add_row(p_name, pre_val, tgt_val, post_val, v_status)

        console.print(table)

        if res.get("memory_warning"):
            console.print(f"[bold yellow]⚠️  [Memory Headroom Notice]:[/bold yellow] {res['memory_warning']}")

        if res.get("all_matched"):
            console.print(Panel(
                f"[bold green][OK] Parallel Slots Successfully Rescaled to {target_slots}![/bold green]\n\n"
                f"[white]• Active Model:[/white] [bold white]{res.get('model_key')}[/bold white]\n"
                f"[white]• Old Parallel Slots:[/white] [yellow]{res.get('old_slots')}[/yellow] → [white]New Parallel Slots:[/white] [bold green]{target_slots}[/bold green]\n"
                f"[white]• Parameter Integrity:[/white] [bold green]100% Verified & Preserved[/bold green]\n"
                f"[white]• Node Slot Capacity:[/white] [cyan]Updated in fleet scheduler registry[/cyan]",
                title="Slot Scaling Succeeded",
                border_style="green"
            ))
        else:
            console.print(Panel(
                "[bold red]⚠️  Discrepancy Detected During Post-Reload Parameter Verification![/bold red]\n"
                "Review the audit table above. One or more parameters differed from the pre-unload state.",
                title="Verification Warning",
                border_style="red"
            ))

    @staticmethod
    @staticmethod
    def handle_models(args: Optional[List[str]] = None):
        from ..edge_fleet.ally_model_manager import node_manager, EdgeFleetModelManager
        from ..config import fleet_config
        from rich.prompt import Prompt

        if args and args[0].lower() == "switch":
            nodes_list = [
                (nid, n) for nid, n in fleet_config.nodes.items()
                if not nid.endswith("_ally_x")
            ]
            choices = [str(i) for i in range(1, len(nodes_list) + 1)]

            opts_text = ""
            for idx, (nid, n) in enumerate(nodes_list, 1):
                opts_text += f"[bold yellow][{idx}][/bold yellow] [bold white]{n.name}[/bold white] ([dim]{n.base_url}[/dim] - [cyan]{n.role}[/cyan])\n"

            console.print(Panel(
                "[bold cyan]⚡ FLEET MODEL SWITCHER WIZARD[/bold cyan]\n"
                "Select which compute node or GPU partition to switch/tune models on:\n\n" + opts_text,
                border_style="cyan"
            ))
            node_choice = Prompt.ask(f"[bold green]Select Target Node [1-{len(nodes_list)}][/bold green]", choices=choices, default="1")
            sel_idx = int(node_choice) - 1
            selected_nid, selected_node = nodes_list[sel_idx]

            target_mgr = EdgeFleetModelManager(node_id=selected_nid, node_url=selected_node.base_url)
            target_mgr.interactive_model_wizard(target_node_id=selected_nid)
            return

        # Show currently active CLI target compute node
        active_node = fleet_config.get_active_node()
        console.print(Panel(
            f"[bold green]⚡ ACTIVE CLI TARGET COMPUTE NODE[/bold green]\n\n"
            f"[white]• Active Node:[/white] [bold cyan]{active_node.name}[/bold cyan] ([magenta]{active_node.node_id}[/magenta])\n"
            f"[white]• Base URL:[/white] [dim]{active_node.base_url}[/dim]\n"
            f"[white]• Active Model:[/white] [bold white]{active_node.active_model or 'Auto-Detected'}[/bold white]\n"
            f"[white]• Context Window:[/white] [yellow]{active_node.active_context:,} tokens[/yellow]\n"
            f"[white]• Parallel Slots:[/white] [cyan]{active_node.slots}[/cyan]\n\n"
            f"[dim]Commands: '/node use <node_id>' to switch active target | '/models switch' to load/tune models[/dim]",
            border_style="green"
        ))

        console.print("[bold cyan][FLEET] Polling live fleet models...[/bold cyan]")
        status = fleet_config.poll_node_models()
        table = Table(title="[FLEET] Cluster Fleet Runtime Models & Endpoints", border_style="cyan")
        table.add_column("Node ID", style="bold green")
        table.add_column("Device / Role", style="cyan")
        table.add_column("Status", style="magenta")
        table.add_column("Active Model", style="bold white")
        table.add_column("Context", justify="right", style="yellow")
        table.add_column("Slots", justify="center", style="dim")

        for nid, data in status.items():
            node = fleet_config.nodes.get(nid)
            name_str = node.name if node else nid
            st = data.get("status", "unknown")
            st_style = "green" if st.startswith("online") else ("yellow" if st == "idle" else "red")
            m_str = str(data.get("model", "None"))
            ctx_str = f"{data.get('context', 0):,}" if data.get('context') else "-"
            slots_str = str(data.get("slots", node.slots if node else "-"))

            is_cur = (nid == active_node.node_id)
            nid_display = f"[bold white]* {nid}[/bold white]" if is_cur else nid
            table.add_row(
                nid_display,
                name_str[:35],
                f"[{st_style}]{st}[/{st_style}]",
                m_str,
                ctx_str,
                slots_str
            )
        console.print(table)
        console.print("[dim]Tip: Use '/node use <node_id>', '/node load', or '/models switch' to configure models.[/dim]")

    @staticmethod
    def handle_node(args: List[str]):
        """
        Device-agnostic compute node & endpoint fleet manager.
        Inspects, lists, polls models, loads/unloads models, and binds agents
        to any compute node or endpoint across the cluster and edge fleet.
        """
        from ..edge_fleet.ally_model_manager import node_manager, EdgeFleetModelManager
        from ..config import fleet_config
        from rich.prompt import Prompt
        sub = args[0].lower() if args else "status"

        if sub in ("use", "select", "switch", "set"):
            target_nid = args[1].lower() if len(args) > 1 else None
            nodes_list = [
                (nid, n) for nid, n in fleet_config.nodes.items()
                if not nid.endswith("_ally_x")
            ]
            if not target_nid:
                opts_text = ""
                for idx, (nid, n) in enumerate(nodes_list, 1):
                    opts_text += f"[bold yellow][{idx}][/bold yellow] [bold white]{n.name}[/bold white] ([magenta]{nid}[/magenta])\n"
                console.print(Panel(
                    "[bold cyan]⚡ SELECT ACTIVE CLI COMPUTE TARGET[/bold cyan]\n"
                    "Select which node endpoint your interactive conversational prompts will target:\n\n" + opts_text,
                    border_style="cyan"
                ))
                choice = Prompt.ask(f"[bold green]Select Node [1-{len(nodes_list)}][/bold green]", choices=[str(i) for i in range(1, len(nodes_list) + 1)], default="1")
                target_nid = nodes_list[int(choice) - 1][0]

            if fleet_config.set_active_node(target_nid):
                node = fleet_config.get_active_node()
                console.print(Panel(
                    f"[bold green][OK] Active CLI Target Compute Node Switched![/bold green]\n\n"
                    f"[white]• Target Node:[/white] [bold cyan]{node.name}[/bold cyan]\n"
                    f"[white]• Node ID:[/white] [magenta]{node.node_id}[/magenta]\n"
                    f"[white]• Base URL:[/white] [dim]{node.base_url}[/dim]\n"
                    f"[white]• Active Model:[/white] [bold white]{node.active_model or 'Auto-Detected'}[/bold white]",
                    border_style="green"
                ))
            else:
                console.print(f"[bold red][ERR] Unknown node ID '{target_nid}'. Type '/node list' to inspect registered nodes.[/bold red]")
            return

        if sub in ("list", "fleet", "endpoints"):
            table = Table(title="⚡ Registered Compute Fleet Nodes & Endpoints", border_style="cyan")
            table.add_column("Node ID", style="bold green")
            table.add_column("Name", style="bold white")
            table.add_column("Endpoint URL", style="dim")
            table.add_column("Role", style="cyan")
            table.add_column("Memory", justify="right", style="magenta")
            table.add_column("Slots", justify="center", style="yellow")
            table.add_column("Status", justify="center")

            active_nid = fleet_config.active_node_id
            for nid, node in fleet_config.nodes.items():
                if nid.endswith("_ally_x"):
                    continue
                mem_str = f"{node.total_memory_mb // 1024} GB" if node.total_memory_mb else "-"
                st_color = "green" if node.status.startswith("online") else ("dim" if node.status == "unknown" else "red")
                nid_str = f"[bold yellow]* {nid}[/bold yellow]" if nid == active_nid else nid
                table.add_row(
                    nid_str,
                    node.name,
                    node.base_url,
                    node.role,
                    mem_str,
                    str(node.slots),
                    f"[{st_color}]{node.status}[/{st_color}]"
                )
            console.print(table)
            console.print("[dim]Use '/node use <node_id>' to switch active node, or '/node load' to manage any endpoint.[/dim]")
            return

        if sub in ("status", "info"):
            target_nid = args[1] if len(args) > 1 else fleet_config.active_node_id
            mgr = EdgeFleetModelManager(node_id=target_nid)
            inst = mgr.get_active_instance()
            audit = mgr.audit_node_resources()
            node_info = fleet_config.nodes.get(target_nid)
            node_disp_name = node_info.name if node_info else f"Node {target_nid}"

            if inst:
                cfg = inst.get("config", {})
                console.print(Panel(
                    f"[bold green]Node Status: ONLINE[/bold green]\n"
                    f"[white]• Node ID:[/white] [cyan]{target_nid}[/cyan] ({node_disp_name})\n"
                    f"[white]• Active Model:[/white] [bold white]{inst['name']}[/bold white] ([cyan]{inst['key']}[/cyan])\n"
                    f"[white]• Model Size:[/white] [magenta]{inst.get('size_gb', 0)} GB[/magenta] ({inst.get('params', 'N/A')})\n"
                    f"[white]• Context Length:[/white] [yellow]{cfg.get('context_length', 'N/A')}[/yellow] tokens\n"
                    f"[white]• Parallel Slots:[/white] [bold yellow]{cfg.get('parallel', 1)}[/bold yellow] (use '/slots <N>' to adjust)\n"
                    f"[white]• Flash Attention:[/white] [green]{cfg.get('flash_attention', 'N/A')}[/green]\n"
                    f"[white]• GPU KV Cache Offload:[/white] [green]{cfg.get('offload_kv_cache_to_gpu', 'N/A')}[/green]\n"
                    f"[white]• Endpoint:[/white] [dim]{mgr.node_url}[/dim]\n"
                    f"[white]• Hardware Memory:[/white] [cyan]{audit['total_hardware_ram_gb']:.0f}GB[/cyan]",
                    title=f"[*] Compute Node: {node_disp_name}",
                    border_style="cyan"
                ))
            else:
                console.print(Panel(
                    f"[yellow]Node '{node_disp_name}' is ONLINE at {mgr.node_url}, but no model is currently loaded.[/yellow]\n"
                    f"[dim]Hardware Memory: {audit['total_hardware_ram_gb']:.0f}GB[/dim]\n"
                    "[dim]Use '/node load' to launch the interactive model selection & parameter walkthrough.[/dim]",
                    title=f"[*] Compute Node: {node_disp_name}",
                    border_style="yellow"
                ))
        elif sub in ("models", "-l"):
            target_nid = args[1] if len(args) > 1 and not args[1].startswith("-") else fleet_config.active_node_id
            mgr = EdgeFleetModelManager(node_id=target_nid)
            models = mgr.list_local_models()
            node_info = fleet_config.nodes.get(target_nid)
            node_disp_name = node_info.name if node_info else f"Node {target_nid}"

            if not models:
                console.print(f"[bold red][ERR] Unable to fetch models from node '{node_disp_name}' at {mgr.node_url}[/bold red]")
                return
            table = Table(title=f"[*] Local GGUF Models on {node_disp_name}", border_style="cyan")
            table.add_column("#", justify="right", style="bold yellow")
            table.add_column("Model Key", style="bold white")
            table.add_column("Architecture", style="dim")
            table.add_column("Params", justify="center", style="cyan")
            table.add_column("Size (GB)", justify="right", style="magenta")
            table.add_column("Quant", style="green")
            table.add_column("Max Context", justify="right", style="bold yellow")
            table.add_column("Status", justify="center", style="bold")

            for idx, m in enumerate(models, 1):
                st = "[bold green][ACTIVE][/bold green]" if m.get("is_loaded") else "[dim]Available[/dim]"
                max_c = m.get("max_context", 32768)
                table.add_row(
                    str(idx),
                    m["key"][:40],
                    m.get("architecture", "-"),
                    m.get("params", "-"),
                    f"{m.get('size_gb', 0):.1f}",
                    m.get("quant", "-"),
                    f"{max_c:,}",
                    st
                )
            console.print(table)
            console.print(f"[dim]Run '/node load {target_nid} <# or key>' to load a model with Auto-Best or Custom parameters on {node_disp_name}.[/dim]")
        elif sub == "load":
            target_nid = args[1] if len(args) > 1 and not args[1].startswith("-") else None
            default_key = args[2] if len(args) > 2 else None
            mgr = EdgeFleetModelManager(node_id=target_nid) if target_nid else node_manager
            mgr.interactive_model_wizard(default_key=default_key, target_node_id=target_nid)
        elif sub in ("unload", "stop"):
            target_nid = args[1] if len(args) > 1 else fleet_config.active_node_id
            mgr = EdgeFleetModelManager(node_id=target_nid)
            node_info = fleet_config.nodes.get(target_nid)
            node_disp_name = node_info.name if node_info else f"Node {target_nid}"
            console.print(f"[cyan][*] Requesting {node_disp_name} to unload active model instances...[/cyan]")
            ok = mgr.unload_active_instances()
            if ok:
                console.print(f"[bold green][OK] Active model successfully unloaded from {node_disp_name} RAM.[/bold green]")
            else:
                console.print(f"[yellow]No active models were running on {node_disp_name}.[/yellow]")
        elif sub == "bind":
            if len(args) < 2:
                console.print("[yellow]Usage: /node bind <agent_id> [model_key] [node_id][/yellow]")
                return
            aid = args[1]
            m_key = args[2] if len(args) > 2 else None
            target_nid = args[3] if len(args) > 3 else fleet_config.active_node_id
            node_manager.bind_agent(agent_id=aid, model_key=m_key, target_node_id=target_nid)
        else:
            console.print("[yellow]Usage: /node [use <node_id> | status [node_id] | list | models [node_id] | load [node_id] [key] | unload [node_id] | bind <agent_id> [model_key] [node_id]][/yellow]")

    @staticmethod
    def handle_ally(args: List[str]):
        """Backward-compatible alias for /node."""
        CommandRegistry.handle_node(args)

    @staticmethod
    def handle_speculative(args: List[str]):
        import asyncio
        from ..core.speculative import speculative_engine
        sub = args[0].lower() if args else "status"

        if sub in ("status", "info"):
            st = speculative_engine.get_status()
            state_color = "bold green" if st["is_enabled"] else "bold yellow"
            state_str = "ENABLED (draft + verify)" if st["is_enabled"] else "DISABLED (Single Target Model)"
            t_status = f"[green]ONLINE[/green] ({st['target_latency_ms']}ms)" if st["target_online"] else "[red]OFFLINE[/red]"
            d_status = f"[green]ONLINE[/green] ({st['draft_latency_ms']}ms)" if st["draft_online"] else "[red]OFFLINE[/red]"

            console.print(Panel(
                f"[bold cyan]⚡ CLIENT-SIDE SPECULATIVE DECODING[/bold cyan]\n"
                f"[dim]Target {fleet_config.label('coordinator')} + draft {fleet_config.label('worker')}[/dim]\n\n"
                f"[{state_color}]• Speculative Mode:[/ {state_color}] [{state_color}]{state_str}[/{state_color}]\n"
                f"[white]• Interactive Prompts Default:[/white] [bold green]ON[/bold green] (draft + verify for operator prompts)\n"
                f"[white]• Background Automation Default:[/white] [yellow]OFF (Single-Model)[/yellow] (Keeps secondary GPU free during mesh play & dossiers)\n"
                f"[white]• Target Model (Verifier):[/white] [bold white]{st['target_model']}[/bold white] on [magenta]{st['target_device']}[/magenta] [{t_status}]\n"
                f"[white]• Draft Model (Proposer):[/white] [bold white]{st['draft_model']}[/bold white] on [cyan]{st['draft_device']}[/cyan] [{d_status}]\n"
                f"[white]• Vocabulary Alignment:[/white] [green]{st['alignment']}[/green]\n"
                f"[white]• Lookahead Window (γ):[/white] [bold yellow]{st['gamma']}[/bold yellow] tokens\n"
                f"[white]• Speedup:[/white] measure it with '/speculative bench' (depends on draft/target vocabulary match)\n\n"
                f"[dim]Commands: '/speculative on', '/speculative off', '/speculative bench', '/speculative config'[/dim]",
                title="Speculative Decoding Architecture",
                border_style="cyan"
            ))

        elif sub == "on":
            gamma = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
            speculative_engine.enable(gamma=gamma)
            console.print(Panel(
                f"[bold green][OK] Speculative Decoding Mode Activated![/bold green]\n\n"
                f"[white]• Target Verifier:[/white] [magenta]{fleet_config.label('coordinator')}[/magenta]\n"
                f"[white]• Draft Accelerator:[/white] [cyan]{fleet_config.label('worker')}[/cyan]\n"
                f"[white]• Draft Lookahead (γ):[/white] [yellow]{gamma} tokens[/yellow]\n\n"
                "[dim]Subsequent CLI turns will use speculative streaming.[/dim]",
                title="Speculative Decoding Active",
                border_style="green"
            ))

        elif sub == "off":
            speculative_engine.disable()
            console.print("[bold yellow][OK] Speculative Decoding Disabled.[/bold yellow] [dim]Reverted to standard standalone inference.[/dim]")

        elif sub == "bench":
            prompt = " ".join(args[1:]) if len(args) > 1 else "Implement an asynchronous thread-safe priority queue in Python."
            console.print(f"[bold cyan][*] Running empirical speculative benchmark...[/bold cyan]\n[dim]Prompt: '{prompt}'[/dim]")
            res = asyncio.run(speculative_engine.run_benchmark(prompt=prompt))

            table = Table(title="⚡ Speculative Decoding Benchmark Results", border_style="cyan")
            table.add_column("Configuration", style="bold white")
            table.add_column("Hardware Device", style="magenta")
            table.add_column("Throughput (tok/s)", justify="right", style="bold green")
            table.add_column("Latency / Speedup", justify="right", style="bold yellow")

            def measured(key, unit=" tok/s"):
                return f"{res[key]}{unit}" if res.get(key) is not None else "n/a"

            table.add_row(f"Target alone ({fleet_config.model('coordinator')})", fleet_config.gpu("coordinator") or "?",
                          measured("target_tps"), "1.0x (baseline)")
            table.add_row(f"Draft alone ({fleet_config.model('worker')})", fleet_config.gpu("worker") or "?",
                          measured("draft_tps"), "draft only")
            table.add_row("[bold green]Speculative[/bold green]",
                          f"[cyan]{fleet_config.gpu('coordinator')} + {fleet_config.gpu('worker')}[/cyan]",
                          f"[bold green]{measured('speculative_tps')}[/bold green]",
                          f"[bold green]{measured('speedup_ratio', 'x')}[/bold green]")
            console.print(table)
            console.print(f"[dim]• Lookahead γ: {res.get('lookahead_gamma')} tokens | Est. Acceptance Rate: {res.get('estimated_acceptance_rate')} | Math loss: 0%[/dim]\n")

        elif sub == "config":
            from rich.prompt import Prompt
            g_str = Prompt.ask("[bold green]Lookahead Draft Window (gamma tokens)[/bold green]", choices=["3", "4", "5", "6", "8"], default=str(speculative_engine.gamma))
            speculative_engine.gamma = int(g_str)
            console.print(f"[bold green][OK] Speculative lookahead set to {speculative_engine.gamma} tokens.[/bold green]")
        else:
            console.print("[yellow]Usage: /speculative [status | on [gamma] | off | bench [prompt] | config][/yellow]")

    @staticmethod
    def handle_mem(args: List[str]):
        if not args:
            console.print("[yellow]Usage: /mem <search query>[/yellow]")
            return
        query = " ".join(args)
        cards = valkey_amem.recall(query, max_atoms=4)
        if not cards:
            console.print(f"[dim]No atomic cards found matching '{query}'.[/dim]")
            return
        table = Table(title=f"[AMEM] Recalled Knowledge Atoms for '{query}'", border_style="green")
        table.add_column("Atom ID", style="cyan")
        table.add_column("Confidence", justify="right")
        table.add_column("Content", style="white")
        for c in cards:
            is_core = " [bold green](core)[/bold green]" if c.get("is_core_memory") else ""
            table.add_row(c["id"] + is_core, f"{c.get('confidence', 1.0):.2f}", c["atom"])
        console.print(table)

    @staticmethod
    def handle_policy(args: List[str]):
        if not args:
            console.print(f"[cyan]Current active profile: [bold]{policy_engine.profile.name}[/bold][/cyan]")
            console.print(f"[dim]{policy_engine.profile.description}[/dim]")
            return
        target = args[0]
        if policy_engine.set_profile(target):
            console.print(f"[green][OK] Switched security profile to [bold]{policy_engine.profile.name}[/bold].[/green]")
        else:
            console.print(f"[red][ERR] Unknown profile '{target}'. Available: strict, tiered, autonomous.[/red]")

    @staticmethod
    def handle_streams(args: Optional[List[str]] = None):
        if args and args[0].lower() in ("consensus", "debate"):
            CommandHandler.handle_consensus(args[1:])
            return
        from .multi_stream_view import interactive_stream_wizard, run_concurrent_streams
        import asyncio
        prompt_arg = " ".join(args) if args else None
        try:
            plan = interactive_stream_wizard(prompt_arg=prompt_arg)
            if plan:
                asyncio.run(run_concurrent_streams(plan))
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[bold yellow]⚡ [Streams Cancelled] Returned to CLI shell.[/bold yellow]\n")
        except Exception as e:
            console.print(f"[red][ERR] Error running stream orchestrator: {e}[/red]")

    @staticmethod
    def handle_consensus(args: Optional[List[str]] = None):
        """
        Executes a 3-Stage Multi-Agent Cross-Examination & Consensus Run.
        Stage 1: Parallel Generation -> Stage 2: Bilateral Peer Critique -> Stage 3: Weighted Founder Synthesis.
        """
        import asyncio
        from ..core.consensus_engine import consensus_engine
        from ..core.founders import get_founder, FOUNDERS

        prompt = " ".join(args).strip() if args else ""
        if not prompt:
            from rich.prompt import Prompt
            prompt = Prompt.ask(
                "[bold green]Enter Challenge / Directive for Consensus Adjudication[/bold green]",
                default="Design a resilient zero-copy shared memory IPC protocol in Python with error handling"
            ).strip()

        console.print(Panel(
            f"[bold cyan]⚡ 3-STAGE MULTI-AGENT CROSS-EXAMINATION & CONSENSUS ENGINE[/bold cyan]\n"
            f"[white]Challenge:[/white] [yellow]{prompt}[/yellow]\n"
            f"[dim]Stages: (1) Parallel Drafts → (2) Peer Cross-Critiques → (3) Founder Weighted Adjudication[/dim]",
            border_style="cyan"
        ))

        plan = consensus_engine.build_plan(prompt=prompt, agent_ids=["atlas", "sentinel"])

        def progress_cb(stage_type, data):
            if stage_type == "stage_start":
                console.print(f"\n[bold yellow]▶ Stage {data['stage']}: {data['title']}[/bold yellow]")
            elif stage_type == "solution_ready":
                console.print(f"  [green]✓[/green] [cyan]{data['agent_id'].upper()}[/cyan] generated initial draft: [dim]{data['solution_snippet']}...[/dim]")
            elif stage_type == "critique_ready":
                console.print(f"  [yellow]⚡[/yellow] [cyan]{data['reviewer'].upper()}[/cyan] critiqued [magenta]{data['peer'].upper()}[/magenta]: [dim]{data['critique_snippet']}...[/dim]")
            elif stage_type == "consensus_complete":
                console.print(f"\n[bold green]✓ Consensus Synthesis Complete in {data['elapsed_s']}s[/bold green]")

        try:
            result = asyncio.run(consensus_engine.execute_consensus(plan, progress_callback=progress_cb))
            console.print(Panel(
                result.final_synthesis,
                title=f"⚡ Founder Adjudication & Verified Consensus ({result.lead_founder_id.upper()})",
                border_style="green"
            ))
        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print("\n[bold yellow]⚡ [Consensus Halted] Operation aborted by operator.[/bold yellow]\n")
        except Exception as e:
            console.print(f"[red][ERR] Consensus execution failed: {e}[/red]")

    @staticmethod
    def handle_hf(args: List[str]):
        from ..trainer_bridge.hf_browser import HuggingFaceBrowser
        query = " ".join(args) if args else "qwen2.5-coder gguf"
        console.print(f"[bold cyan][HF] Querying Hugging Face GGUF Hub for '{query}'...[/bold cyan]")
        browser = HuggingFaceBrowser()
        models = browser.search_models(query=query, limit=5)

        table = Table(title=f"[HF] Hugging Face Models ({query})", border_style="yellow")
        table.add_column("Model ID", style="bold green")
        table.add_column("Downloads", justify="right", style="cyan")
        table.add_column("Likes", justify="right", style="magenta")
        table.add_column("GGUF Quants Discovered", style="white")

        for m in models:
            quants = ", ".join(f"{v.quant_type} ({v.size_gb}GB)" for v in m.variants[:3])
            table.add_row(m.model_id, f"{m.downloads:,}", str(m.likes), quants or "GGUF")
        console.print(table)

    @staticmethod
    def handle_train(args: List[str]):
        from ..trainer_bridge.trainer_hub import TrainerHub, TrainingJobConfig
        hub = TrainerHub()
        sub = args[0] if args else "status"
        if sub == "status":
            st = hub.get_job_status()
            console.print(Panel(f"[bold]Training Pipeline Status:[/bold] {st.get('status', 'idle')}\n[dim]/opt/pipeline-gguf-trainer[/dim]", border_style="magenta"))
        elif sub == "invariants":
            console.print("[bold cyan][INVARIANTS] Running 10 Golden Safety Invariants...[/bold cyan]")
            res = hub.run_golden_invariants()
            table = Table(title=f"[SAFETY] Golden Invariant Report (Verdict: {'PASS' if res['verdict'] else 'FAIL'})", border_style="green" if res['verdict'] else "red")
            table.add_column("Invariant ID", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Result", style="bold")
            for d in res["details"]:
                status_str = "[green]PASSED[/green]" if d["passed"] else "[red]FAILED[/red]"
                table.add_row(d["id"], d["name"], status_str)
            console.print(table)
        else:
            console.print("[yellow]Usage: /train [status | invariants][/yellow]")

    @staticmethod
    def handle_mesh(args: List[str]):
        from ..core.stonesage_mesh import stonesage_mesh
        sub = args[0].lower() if args else "status"

        if sub in ("status", "list", "-l"):
            agents = stonesage_mesh.list_agents()
            table = Table(title="[*] StoneSage Mesh: Sovereign Agent Hive Network", border_style="cyan")
            table.add_column("Agent ID", style="bold green")
            table.add_column("Display Name", style="cyan")
            table.add_column("Assigned Node", style="magenta")
            table.add_column("User Task", style="white")
            table.add_column("Mesh Status", style="bold")
            table.add_column("Autonomous Focus", style="dim")
            table.add_column("Last Heartbeat", style="yellow")

            for a in agents:
                task_str = a.get("current_task") or "[dim italic]None (Idle / Free to Play)[/dim italic]"
                hb = a.get("last_heartbeat_hive", 0)
                hb_str = datetime.fromtimestamp(hb).strftime("%H:%M:%S") if hb else "Never"
                status_raw = a.get("status", "idle")
                if status_raw == "user_active":
                    status_style = "[bold green][USER TASK][/bold green]"
                elif "agora" in status_raw:
                    status_style = "[cyan][AGORA PLAY][/cyan]"
                elif "dossier" in status_raw:
                    status_style = "[magenta][DOSSIER PLAY][/magenta]"
                else:
                    status_style = "[dim][IDLE][/dim]"

                focus_str = a.get("autonomous_focus") or "(None)"
                table.add_row(
                    a["agent_id"],
                    a.get("name", a["agent_id"].title()),
                    a.get("assigned_node", "node1_primary"),
                    task_str[:30],
                    status_style,
                    focus_str[:35],
                    hb_str
                )
            console.print(table)

        elif sub == "play":
            target = args[1] if len(args) > 1 else None
            console.print("[bold cyan][STONESAGE MESH] Dispatching autonomous play turn...[/bold cyan]")
            res = stonesage_mesh.trigger_autonomous_play(target)
            if res.get("ok"):
                mode = res.get("mode")
                name = res.get("agent_name")
                topic = res.get("topic")
                if mode == "agora":
                    chan = res.get("channel")
                    remote = "[green]Delivered to :8766[/green]" if res.get("remote_delivery") else "[dim]Archived locally[/dim]"
                    console.print(Panel(
                        f"[bold green]Autonomous Agora Dialectic Triggered[/bold green]\n\n"
                        f"[white]• Agent:[/white] [cyan]{name}[/cyan]\n"
                        f"[white]• Channel:[/white] [magenta]{chan}[/magenta]\n"
                        f"[white]• Self-Selected Topic:[/white] {topic}\n"
                        f"[white]• Delivery:[/white] {remote}\n",
                        title="StoneSage Mesh • The Agora Chatboard",
                        border_style="cyan"
                    ))
                else:
                    dossier = res.get("dossier_path")
                    console.print(Panel(
                        f"[bold green]Autonomous Research Dossier Generated[/bold green]\n\n"
                        f"[white]• Agent:[/white] [cyan]{name}[/cyan]\n"
                        f"[white]• Self-Selected Topic:[/white] {topic}\n"
                        f"[white]• Saved To:[/white] [yellow]{dossier}[/yellow]\n",
                        title="StoneSage Mesh • Autonomous Dossier",
                        border_style="magenta"
                    ))
            else:
                console.print(f"[yellow]{res.get('message')}[/yellow]")

        elif sub == "task":
            if len(args) < 3:
                console.print("[yellow]Usage: /mesh task <agent_id> <task description>[/yellow]")
                return
            aid = args[1]
            task_desc = " ".join(args[2:])
            stonesage_mesh.assign_user_task(aid, task_desc)
            console.print(f"[bold green][OK] Assigned task to agent '{aid}':[/bold green] [white]{task_desc}[/white]")

        elif sub in ("clear", "clear-task"):
            if len(args) < 2:
                console.print("[yellow]Usage: /mesh clear <agent_id>[/yellow]")
                return
            aid = args[1]
            stonesage_mesh.clear_user_task(aid)
            console.print(f"[bold green][OK] Cleared user task for agent '{aid}'. Agent is now idle and free to play in autonomous areas.[/bold green]")

        elif sub in ("mate", "reproduce", "cross", "crossover"):
            from rich.prompt import Prompt
            agents = stonesage_mesh.list_agents()
            avail_ids = [a["agent_id"] for a in agents]
            if len(avail_ids) < 2:
                console.print("[yellow]At least two registered agents are required for digital reproduction.[/yellow]")
                return

            p_a = args[1].strip().lower() if len(args) > 1 else None
            p_b = args[2].strip().lower() if len(args) > 2 else None
            intent = " ".join(args[3:]).strip() if len(args) > 3 else None

            if not p_a:
                console.print(Panel(
                    "[bold cyan]⚡ BILATERAL DIGITAL REPRODUCTION & GENETIC CROSSOVER[/bold cyan]\n"
                    "[dim]Select two mature parent agents to combine their identities, invariants, heuristics,\n"
                    "and soul parameters into a novel Generation-(N+1) hybrid offspring agent.[/dim]",
                    border_style="cyan"
                ))
                p_a = Prompt.ask("[bold green]Select Parent Agent A[/bold green]", choices=avail_ids, default=avail_ids[0])

            remaining_ids = [aid for aid in avail_ids if aid != p_a]
            if not p_b:
                p_b = Prompt.ask("[bold green]Select Parent Agent B[/bold green]", choices=remaining_ids, default=remaining_ids[0])

            if not intent:
                intent_input = Prompt.ask("[bold green]Optional Crossover Focus Intent / Problem Specialization[/bold green] (Enter for default)", default="")
                intent = intent_input.strip() or None

            console.print(f"\n[cyan][*] Initiating bilateral genetic crossover between [bold green]{p_a}[/bold green] × [bold green]{p_b}[/bold green]...[/cyan]")
            res = stonesage_mesh.reproduce_agents(p_a, p_b, focus_intent=intent)
            if not res.get("ok"):
                console.print(f"[bold red][ERR] Reproduction blocked: {res.get('error')}[/bold red]")
                return

            child = res.get("child_agent", {})
            lineage = res.get("lineage", {})
            gen = lineage.get("generation", 2)
            bridge_badge = "[bold green]Cluster Bridge (:8765)[/bold green]" if res.get("cluster_bridge") else "[cyan]Local Neural Genesis Engine[/cyan]"

            traits_str = ", ".join(lineage.get("traits", []))
            ratios = lineage.get("blend_ratio", {})
            ratio_str = f"{p_a} ({ratios.get('parent_a', 0.5)*100:.0f}%) / {p_b} ({ratios.get('parent_b', 0.5)*100:.0f}%)"

            console.print(Panel(
                f"[bold green]⚡ NOVEL DIGITAL PERSON BORN: {child.get('name')}[/bold green]\n\n"
                f"[white]• Child Agent ID:[/white] [cyan]{child.get('agent_id')}[/cyan]\n"
                f"[white]• Lineage Generation:[/white] [bold yellow]Generation {gen}[/bold yellow] ({p_a} × {p_b})\n"
                f"[white]• Blend Ratio:[/white] [magenta]{ratio_str}[/magenta]\n"
                f"[white]• Specialized Role:[/white] [bold white]{child.get('role')}[/bold white]\n"
                f"[white]• Core Mission:[/white] {child.get('mission')}\n"
                f"[white]• Inherited Traits:[/white] [yellow]{traits_str}[/yellow]\n"
                f"[white]• Assigned Node:[/white] [magenta]{child.get('assigned_node')}[/magenta]\n"
                f"[white]• Engine Pathway:[/white] {bridge_badge}\n\n"
                f"[dim]Offspring registered into StoneSage Mesh, OpenClaw contracts, and eternal memory.[/dim]\n"
                f"[dim]Attach to this new agent via '/agent select {child.get('agent_id')}' or assign tasks via '/mesh task'.[/dim]",
                title="Digital Reproduction Complete",
                border_style="green"
            ))

        else:
            console.print("[yellow]Usage: /mesh [status | play <id> | mate [parent_a] [parent_b] [intent] | task <id> <desc> | clear <id>][/yellow]")

    @staticmethod
    def handle_project(args: List[str]):
        import os
        from .agent_shell import AgentShell
        from ..core.project_manager import project_manager
        from ..core.openclaw_engine import openclaw_engine
        from rich.prompt import Prompt

        sub = args[0].lower() if args else "list"

        if sub in ("list", "-l", "projects"):
            projects = project_manager.list_all_projects()
            table = Table(title="[*] Cross-Endpoint Project Registry & Workspaces", border_style="cyan")
            table.add_column("Project ID", style="bold green")
            table.add_column("Project Name", style="cyan")
            table.add_column("Endpoint Node", style="magenta")
            table.add_column("Path", style="white")
            table.add_column("Bound Agent", style="yellow")
            table.add_column("Active State", justify="center", style="bold")

            active_proj_id = AgentShell.active_project.get("project_id") if AgentShell.active_project else None

            for p in projects:
                pid = p.get("project_id", "")
                is_active = (pid == active_proj_id)
                st_badge = "[bold green][ENTERED][/bold green]" if is_active else "[dim]Registered[/dim]"
                agent_badge = p.get("bound_agent_id") or "[dim](none)[/dim]"
                table.add_row(
                    pid,
                    p.get("name", pid),
                    p.get("node_id", "local"),
                    p.get("path", "")[:45],
                    agent_badge,
                    st_badge
                )

            console.print(table)
            if AgentShell.active_project:
                console.print(f"[dim]Active Project Context:[/dim] [bold green]{AgentShell.active_project.get('name')}[/bold green] ([cyan]{AgentShell.active_project.get('path')}[/cyan])")
            else:
                console.print("[dim]Tip: Use '/project enter <id>' to switch into a project context or '/project create' to scaffold a new one.[/dim]")

        elif sub in ("enter", "select", "cd", "open"):
            projects = project_manager.list_all_projects()
            avail_ids = [p["project_id"] for p in projects]

            if not args or len(args) < 2:
                if not avail_ids:
                    console.print("[yellow]No projects registered yet. Use '/project create' to scaffold your first project.[/yellow]")
                    return
                target_id = Prompt.ask("[bold green]Select Project ID to Enter[/bold green]", choices=avail_ids, default=avail_ids[0])
            else:
                target_id = args[1].strip()

            proj, agent_profile = project_manager.enter_project(target_id)
            if not proj:
                console.print(f"[bold red][ERR] Project '{target_id}' not found in registry or local paths.[/bold red]")
                console.print(f"[dim]Available projects: {', '.join(avail_ids) if avail_ids else 'None'}[/dim]")
                return

            AgentShell.active_project = proj
            if agent_profile:
                AgentShell.active_agent = agent_profile

            # Extract invariants & handover info if available
            inv_count = 0
            handover_snippet = "(None)"
            if os.path.exists(proj["path"]):
                p_agent = openclaw_engine.load_project_agent(proj["path"])
                if p_agent:
                    inv_raw = p_agent.get("invariants_md", "")
                    inv_count = len([line for line in inv_raw.splitlines() if line.strip().startswith("-")])
                    ho_raw = p_agent.get("handover_md", "")
                    for line in ho_raw.splitlines():
                        if line.strip().startswith("-") and line.strip() != "-":
                            handover_snippet = line.strip()[:60]
                            break

            agent_name = agent_profile.get("name", "Specialist") if agent_profile else "None"
            agent_role = agent_profile.get("role", "General") if agent_profile else "None"

            console.print(Panel(
                f"[bold green]⚡ ENTERED PROJECT: {proj.get('name')}[/bold green]\n\n"
                f"[white]• Project ID:[/white] [cyan]{proj.get('project_id')}[/cyan]\n"
                f"[white]• Repository Path:[/white] [bold white]{proj.get('path')}[/bold white]\n"
                f"[white]• Endpoint Host:[/white] [magenta]{proj.get('node_id', 'local')}[/magenta]\n"
                f"[white]• Dedicated Agent:[/white] [bold green]{agent_name}[/bold green] [dim]({agent_role})[/dim]\n"
                f"[white]• Active Invariants:[/white] [yellow]{inv_count} recorded[/yellow]\n"
                f"[white]• Latest Handover:[/white] [dim]{handover_snippet}[/dim]\n\n"
                "[dim]All subsequent CLI turns will automatically inject this project's In-Repo Agent DNA, invariants, and repository path.[/dim]",
                title="Project Active Context",
                border_style="green"
            ))

        elif sub in ("leave", "exit", "detach", "close"):
            if AgentShell.active_project:
                old_name = AgentShell.active_project.get("name", "Project")
                AgentShell.active_project = None
                console.print(f"[bold green][OK] Exited project context '{old_name}'. Returned to global workspace.[/bold green]")
            else:
                console.print("[dim]No active project context. Already in global workspace.[/dim]")

        elif sub in ("create", "new", "init"):
            console.print(Panel(
                "[bold cyan]⚡ SCAFFOLD NEW PROJECT & IN-REPOSITORY AGENT DNA[/bold cyan]\n"
                "Generates .stonesage/ DNA (agent.json, IDENTITY.md, SOUL.md, INVARIANTS.md, HANDOVER.md)\n"
                "and binds a dedicated specialist agent to the repository.",
                border_style="cyan"
            ))

            if len(args) > 1:
                target_path = args[1]
                default_name = args[2] if len(args) > 2 else os.path.basename(os.path.normpath(target_path))
            else:
                default_ws = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "projects", "new_project")
                target_path = Prompt.ask("[bold green]Project Directory Path[/bold green]", default=default_ws)
                default_name = os.path.basename(os.path.normpath(target_path)) or "MyProject"

            proj_name = Prompt.ask("[bold green]Project Display Name[/bold green]", default=default_name)
            agent_name = Prompt.ask("[bold green]Dedicated Specialist Agent Name[/bold green]", default=f"{proj_name} Specialist")
            agent_role = Prompt.ask("[bold green]Agent Engineering Role[/bold green]", default=f"Lead Architect for {proj_name}")
            agent_mission = Prompt.ask("[bold green]Agent Primary Mission[/bold green]", default=f"Maintain technical rigor and advance {proj_name}.")
            autonomy = Prompt.ask("[bold green]Autonomy Level[/bold green]", choices=["tiered", "strict", "autonomous"], default="tiered")

            console.print(f"\n[cyan][*] Scaffolding project and Agent DNA in '{target_path}'...[/cyan]")
            proj = project_manager.create_project(
                path=target_path,
                name=proj_name,
                agent_name=agent_name,
                agent_role=agent_role,
                agent_mission=agent_mission,
                autonomy_level=autonomy
            )

            # Automatically enter the new project
            entered_proj, entered_agent = project_manager.enter_project(proj["project_id"])
            AgentShell.active_project = entered_proj
            if entered_agent:
                AgentShell.active_agent = entered_agent

            console.print(Panel(
                f"[bold green][OK] Successfully Created and Entered Project '{proj_name}'![/bold green]\n\n"
                f"[white]• Repository Path:[/white] [cyan]{proj['path']}[/cyan]\n"
                f"[white]• Dedicated Agent:[/white] [bold green]{agent_name}[/bold green] (ID: `{entered_agent.get('agent_id') if entered_agent else 'N/A'}`)\n"
                f"[white]• Agent DNA Files:[/white] [yellow].stonesage/ (IDENTITY.md, SOUL.md, INVARIANTS.md, HANDOVER.md)[/yellow]\n"
                f"[white]• Cross-Platform Parity:[/white] [dim]Indexed in StoneSage & SQLite project registry[/dim]",
                title="Project Scaffolding Complete",
                border_style="green"
            ))

        elif sub in ("status", "info"):
            if not AgentShell.active_project:
                console.print("[yellow]No active project. Type '/project list' or '/project enter <id>' to select one.[/yellow]")
                return

            proj = AgentShell.active_project
            p_path = proj.get("path", "")
            agent = AgentShell.active_agent or {}

            invariants_text = "(None)"
            handover_text = "(None)"
            if os.path.exists(p_path):
                p_agent = openclaw_engine.load_project_agent(p_path)
                if p_agent:
                    invariants_text = p_agent.get("invariants_md", "(No custom invariants recorded yet)")
                    handover_text = p_agent.get("handover_md", "(Workspace initialized)")

            console.print(Panel(
                f"[bold cyan]⚡ ACTIVE PROJECT STATUS: {proj.get('name')}[/bold cyan]\n\n"
                f"[white]• Project ID:[/white] [cyan]{proj.get('project_id')}[/cyan]\n"
                f"[white]• Repository Root:[/white] [bold white]{p_path}[/bold white]\n"
                f"[white]• Endpoint Node:[/white] [magenta]{proj.get('node_id', 'local')}[/magenta]\n"
                f"[white]• Bound Agent:[/white] [bold green]{agent.get('name', 'None')}[/bold green] ([dim]{agent.get('agent_id', 'None')}[/dim])\n"
                f"[white]• Agent Role:[/white] {agent.get('role', 'N/A')}\n\n"
                f"[bold yellow]Active Architectural Invariants:[/bold yellow]\n{invariants_text}\n\n"
                f"[bold green]Rolling Milestone Handover:[/bold green]\n{handover_text}",
                title="Active Project Status",
                border_style="cyan"
            ))

        elif sub in ("assign", "bind"):
            if len(args) < 3:
                console.print("[yellow]Usage: /project assign <project_id> <agent_id>[/yellow]")
                return
            pid = args[1].strip()
            aid = args[2].strip().lower()
            ok = project_manager.assign_agent(pid, aid)
            if ok:
                console.print(f"[bold green][OK] Successfully bound agent '{aid}' to project '{pid}'.[/bold green]")
                if AgentShell.active_project and AgentShell.active_project.get("project_id") == pid:
                    AgentShell.active_project["bound_agent_id"] = aid
                    hive_a = relational_storage.get_hive_agent(aid)
                    if hive_a:
                        AgentShell.active_agent = hive_a
            else:
                console.print(f"[bold red][ERR] Failed to bind agent '{aid}' to project '{pid}'.[/bold red]")

        elif sub in ("invariant", "add-invariant"):
            if not AgentShell.active_project:
                console.print("[yellow]No project currently active. Enter a project first with '/project enter <id>'.[/yellow]")
                return
            if len(args) < 2:
                console.print("[yellow]Usage: /project invariant <rule statement>[/yellow]")
                return
            inv_text = " ".join(args[1:]).strip()
            p_path = AgentShell.active_project.get("path")
            ok, msg = openclaw_engine.append_project_invariant(p_path, inv_text)
            if ok:
                console.print(f"[bold green][OK] Recorded new architectural invariant for '{AgentShell.active_project.get('name')}':[/bold green]\n[white]{inv_text}[/white]")
            else:
                console.print(f"[yellow]{msg}[/yellow]")

        elif sub in ("promote", "export", "extract", "extract-agent", "export-agent"):
            # 1. Identify target project
            proj_id = None
            if len(args) > 1 and not args[1].startswith("-"):
                proj_id = args[1]
            elif AgentShell.active_project:
                proj_id = AgentShell.active_project["project_id"]
            else:
                projects = project_manager.list_all_projects()
                avail_ids = [p["project_id"] for p in projects]
                if not avail_ids:
                    console.print("[yellow]No projects found to promote an agent from.[/yellow]")
                    return
                proj_id = Prompt.ask("[bold green]Select Source Project[/bold green]", choices=avail_ids, default=avail_ids[0])

            proj = project_manager.get_project(proj_id)
            if not proj:
                console.print(f"[bold red][ERR] Project '{proj_id}' not found.[/bold red]")
                return

            base_agent = proj.get("bound_agent_id", f"agent_{proj['project_id']}")
            console.print(Panel(
                f"[bold cyan]⚡ PROMOTE & CRYSTALLIZE AGENT FROM PROJECT: {proj.get('name')}[/bold cyan]\n"
                f"[dim]Extracts the agent's evolved soul, identity, accumulated invariants, and handover state\n"
                f"from '{proj.get('path')}' into a standalone cluster-wide agent profile.[/dim]\n\n"
                f"[white]• Source Project:[/white] [bold white]{proj.get('name')}[/bold white] ([cyan]{proj.get('project_id')}[/cyan])\n"
                f"[white]• Base Project Agent:[/white] [bold yellow]{base_agent}[/bold yellow]\n\n"
                f"[bold green]Select Promotion Mode:[/bold green]\n"
                f"  [bold yellow][1] Overwrite Base Agent[/bold yellow] - Update base agent '{base_agent}' with evolved soul & invariants\n"
                f"  [bold yellow][2] Spawn New Standalone Agent[/bold yellow] - Crystallize as a new sovereign agent for cluster fleet",
                border_style="cyan"
            ))

            choice = Prompt.ask("[bold green]Select Option [1 or 2][/bold green]", choices=["1", "2"], default="2")

            if choice == "1":
                target_aid = Prompt.ask("[bold green]Agent ID to Overwrite[/bold green]", default=base_agent)
                res = project_manager.promote_project_agent(
                    project_identifier=proj["project_id"],
                    mode="overwrite",
                    target_agent_id=target_aid
                )
            else:
                new_aid = Prompt.ask("[bold green]New Standalone Agent ID[/bold green]", default=f"{base_agent}-evolved")
                new_name = Prompt.ask("[bold green]New Agent Display Name[/bold green]", default=f"{proj.get('name')} Architect")
                node_id = Prompt.ask(
                    "[bold green]Assigned Compute Node[/bold green] (" + " / ".join(fleet_config.nodes) + ")",
                    default="node1_primary"
                )
                res = project_manager.promote_project_agent(
                    project_identifier=proj["project_id"],
                    mode="new",
                    target_agent_id=new_aid,
                    new_name=new_name,
                    assigned_node=node_id
                )

            if not res.get("ok"):
                console.print(f"[bold red][ERR] Promotion failed: {res.get('error')}[/bold red]")
                return

            console.print(Panel(
                f"[bold green][OK] Agent Soul & Invariants Successfully Promoted![/bold green]\n\n"
                f"[white]• Agent Name:[/white] [bold white]{res.get('name')}[/bold white] (ID: [cyan]{res.get('agent_id')}[/cyan])\n"
                f"[white]• Mode:[/white] [bold yellow]{'Overwrote Base Agent' if res.get('mode') == 'overwrite' else 'Spawned New Sovereign Agent'}[/bold yellow]\n"
                f"[white]• Assigned Node:[/white] [magenta]{res.get('assigned_node')}[/magenta]\n"
                f"[white]• Inherited Invariants:[/white] [bold yellow]{res.get('invariants_count', 0)} architectural rules[/bold yellow]\n"
                f"[white]• Contracts Path:[/white] [dim]{res.get('profile_dir')}[/dim]\n\n"
                f"[dim]This agent is now available cluster-wide in '/agent list', '/mesh', '/ally bind', and across other projects.[/dim]",
                title="Agent Promotion Complete",
                border_style="green"
            ))

        else:
            console.print("[yellow]Usage: /project [list | enter <id> | create [path] | leave | status | assign <proj> <agent> | invariant <text> | promote [proj]][/yellow]")

    @staticmethod
    def handle_runtime(args: List[str]):
        import subprocess
        from rich.prompt import Prompt

        sub = args[0].lower() if args else "select"
        vm_user, vm_host = fleet_config.inference_ssh.split("@", 1)      # the engines' host (config.json)

        def run_remote_ssh(cmd: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["ssh", "-n", "-o", "BatchMode=yes", f"{vm_user}@{vm_host}", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )

        if sub in ("status", "info"):
            console.print(Panel(
                f"[bold cyan]⚡ INFERENCE HOST RUNTIME STATUS[/bold cyan]\n"
                f"[dim]Host: {vm_user}@{vm_host} • {' & '.join(g['name'] for g in (fleet_config.stonesage_profile().get('gpus') or [])) or 'GPUs unknown'}[/dim]",
                border_style="cyan"
            ))
            res = run_remote_ssh("ps aux | grep llama-server | grep -v grep")
            units = run_remote_ssh("systemctl list-units --type=service | grep -E '(llama|cluster)'")

            raw = res.stdout
            if "llama-server-rocm" in raw:
                active_backend = "[bold green]ROCm 10 / HIP Native (v.03-rocm)[/bold green]"
                badge = "⚡ HIP Accelerated"
            elif "llama-server" in raw:
                active_backend = "[bold yellow]Vulkan Baseline (v.03 Mesa RADV)[/bold yellow]"
                badge = "🛡️ Vulkan Baseline"
            else:
                active_backend = "[bold red]Offline / Inactive[/bold red]"
                badge = "❌ Inactive"

            table = Table(title="Live Compute Services", border_style="cyan")
            table.add_column("Unit / Service", style="cyan")
            table.add_column("Port", style="yellow")
            table.add_column("Backend", style="white")
            table.add_column("Status", style="bold")

            lines = [l for l in units.stdout.strip().split("\n") if l]
            for l in lines:
                parts = l.split()
                if len(parts) >= 4:
                    u_name = parts[0]
                    u_stat = parts[3]
                    port = ":8001" if "moe" in u_name or "coordinator" in u_name else (":8002" if "worker" in u_name else (":8003" if "embed" in u_name else ":8765"))
                    backend_label = "ROCm 10 (HIP)" if "rocm" in u_name else ("Vulkan" if "llama" in u_name else "Bridge (SSE)")
                    s_style = "[bold green]active (running)[/bold green]" if u_stat == "running" else f"[yellow]{u_stat}[/yellow]"
                    table.add_row(u_name, port, backend_label, s_style)

            console.print(f"[white]• Active Backend:[/white] {active_backend} [dim]({badge})[/dim]\n")
            console.print(table)

        elif sub in ("switch", "select", "set"):
            target = args[1].lower() if len(args) > 1 else None
            mode = args[2].lower() if len(args) > 2 else "moe"

            if not target:
                console.print(Panel(
                    "[bold cyan]⚡ DUAL-GPU COMPUTE RUNTIME SELECTOR[/bold cyan]\n"
                    "[dim]Switch inference engine backends with zero downtime and automatic rollback protection.[/dim]\n\n"
                    "  [bold green][1] v.03-rocm (ROCm 10.0 / HIP Native)[/bold green] - ~2.5x-3.5x prefill boost, native Flash Attention, rocBLAS\n"
                    "  [bold yellow][2] v.03 (Vulkan Baseline - RADV/Mesa)[/bold yellow] - Stable fallback baseline\n"
                    "  [bold cyan][3] Empirical Benchmark (Vulkan vs ROCm)[/bold cyan] - Measure TTFT, t/s, and latency\n"
                    "  [bold white][4] View Live Status & Services[/bold white]",
                    border_style="cyan"
                ))
                choice = Prompt.ask("[bold green]Select Runtime Option[/bold green]", choices=["1", "2", "3", "4"], default="1")
                if choice == "1":
                    target = "rocm"
                elif choice == "2":
                    target = "vulkan"
                elif choice == "3":
                    CommandRegistry.handle_runtime(["benchmark"])
                    return
                else:
                    CommandRegistry.handle_runtime(["status"])
                    return

            console.print(f"\n[cyan][*] Executing runtime transition to [bold yellow]{target.upper()}[/bold yellow] (Mode: {mode})...[/cyan]")
            console.print("[dim]Stopping previous backend, starting target units, and polling /health...[/dim]")

            res = run_remote_ssh(f"/tmp/v.03-rocm/switch_stack.sh {target} {mode}")
            console.print(res.stdout)
            if res.returncode == 0:
                console.print(f"[bold green][OK] Compute runtime successfully switched to {target.upper()}![/bold green]")
            else:
                console.print(f"[bold red][ERR] Switch to {target.upper()} encountered an issue or triggered rollback.[/bold red]")

        elif sub in ("benchmark", "bench"):
            console.print(Panel(
                "[bold cyan]⚡ EMPIRICAL RUNTIME BENCHMARK HARNESS[/bold cyan]\n"
                "[dim]Running multi-context TTFT & prefill/generation throughput suite against the active engine host (port :8001)...[/dim]",
                border_style="cyan"
            ))
            import os
            bench_script = os.path.join(os.path.dirname(__file__), "..", "..", "server setup", "v.03-rocm", "benchmark_v03_vs_rocm.py")
            if os.path.exists(bench_script):
                subprocess.run([sys.executable, bench_script, "--host", vm_host, "--port", "8001"])
            else:
                res = run_remote_ssh("python3 /tmp/v.03-rocm/benchmark_v03_vs_rocm.py --host 127.0.0.1 --port 8001")
                console.print(res.stdout)

        else:
            console.print("[yellow]Usage: /runtime [status | select | switch <rocm|vulkan> [moe|dual] | benchmark][/yellow]")

    @staticmethod
    def handle_status(args: List[str]):
        """Queries live cluster status, autonomous thinking activity, wildlife sentry, and node slots."""
        import urllib.request
        import json

        console.print(Panel(
            "[bold cyan]⚡ STONESAGE CLUSTER & AGENT ACTIVITY DASHBOARD[/bold cyan]\n"
            "[dim]Live telemetry across the engines, Autonomous Thinking Loop, FaunaSentinel, and Slot Occupancy[/dim]",
            border_style="cyan"
        ))

        # 1. Fleet Model & Slot Status
        from ..core.node_scheduler import node_scheduler
        status = node_scheduler.get_fleet_status(auto_poll=True)
        table = Table(title="[*] Compute Fleet & Slot Status", border_style="green")
        table.add_column("Node", style="bold white")
        table.add_column("Role", style="magenta")
        table.add_column("Slots", justify="center")
        table.add_column("Active Model", style="cyan")
        table.add_column("Status", justify="center")

        for n in status:
            st = n.get("status", "unknown")
            st_style = "green" if st == "online" else "red"
            table.add_row(
                n["name"],
                n["role"],
                f"{n['occupied_slots']}/{n['total_slots']}",
                str(n["active_model"]),
                f"[{st_style}]{st.upper()}[/{st_style}]"
            )
        console.print(table)

        # 2. Autonomous Thinking Engine Activity (port 8765)
        try:
            req = urllib.request.Request(f"{fleet_config.cluster_mcp_url}/status", headers={"User-Agent": "Harness-CLI"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    at_status = data.get("autonomous_thinking", {})
                    is_running = at_status.get("is_running", False)
                    cycles = at_status.get("total_cycles", 0)
                    tokens = at_status.get("total_tokens_generated", 0)
                    domain = at_status.get("current_focus_domain", "Unknown")
                    last_domain = at_status.get("last_domain", "N/A")

                    status_badge = "[bold green]ACTIVE (24/7)[/bold green]" if is_running else "[yellow]PAUSED[/yellow]"
                    console.print(Panel(
                        f"[white]• State:[/white] {status_badge}\n"
                        f"[white]• Total Cycles:[/white] [cyan]{cycles:,}[/cyan] cycles executed\n"
                        f"[white]• Total Tokens:[/white] [yellow]{tokens:,}[/yellow] tokens generated across the cluster\n"
                        f"[white]• Current Focus:[/white] [magenta]{domain}[/magenta]\n"
                        f"[white]• Last Domain:[/white] [dim]{last_domain}[/dim]\n"
                        f"[dim]Background loop explores architecture limits & adversarial edge-cases every 120s on the engine host.[/dim]",
                        title="24/7 Autonomous Thinking Machine",
                        border_style="magenta"
                    ))
        except Exception:
            console.print("[dim]• Autonomous thinking engine telemetry: probe skipped (bridge busy or offline).[/dim]")

        # 3. Sovereign Mesh Fleet
        from ..core.stonesage_mesh import stonesage_mesh
        agents = stonesage_mesh.list_agents()
        active_tasks = [a for a in agents if a.get("current_task")]
        if active_tasks:
            task_table = Table(title="[*] Active Operator Tasks on Fleet Agents", border_style="yellow")
            task_table.add_column("Agent ID", style="cyan")
            task_table.add_column("Name", style="bold white")
            task_table.add_column("Assigned Task", style="white")
            for a in active_tasks:
                task_table.add_row(a.get("agent_id"), a.get("name"), a.get("current_task"))
            console.print(task_table)
        else:
            console.print("[dim]• Active operator tasks: None locked (idle agents engage in autonomous play/reflection).[/dim]\n")



