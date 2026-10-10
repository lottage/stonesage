"""
Interactive Autocomplete & Dropdown Menu Completer for Aevum CLI.
Provides real-time narrowing popup menus, subcommands, arrow-key navigation,
and Tab-completion via prompt_toolkit with graceful standard fallback.
"""

import os
import sys
from typing import Iterable, Dict, Any, Optional

try:
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import has_completions
    from prompt_toolkit.styles import Style
    from prompt_toolkit.shortcuts import CompleteStyle
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


class HarnessCompleter(Completer if PROMPT_TOOLKIT_AVAILABLE else object):
    """
    Hierarchical slash command & subcommand completer with live metadata hints.
    Narrows options in real-time as the user types.
    """
    COMMANDS = {
        "/project": {
            "desc": "Cross-endpoint project workspace manager & in-repo Agent DNA",
            "sub": {
                "list": "List all registered projects, endpoints, and bound agents",
                "enter": "Enter a project and activate its in-repo Agent DNA",
                "create": "Scaffold a new project directory with .stonesage Agent DNA",
                "leave": "Detach from active project and return to global workspace",
                "status": "Inspect active project invariants and handover state",
                "assign": "Bind an agent to a project",
                "invariant": "Record an architectural invariant to active project",
                "promote": "Promote agent soul & invariants from project (overwrite or spawn new)",
            }
        },
        "/proj": {
            "desc": "Cross-endpoint project workspace manager (alias)",
            "sub": {
                "list": "List all registered projects, endpoints, and bound agents",
                "enter": "Enter a project and activate its in-repo Agent DNA",
                "create": "Scaffold a new project directory with .stonesage Agent DNA",
                "leave": "Detach from active project and return to global workspace",
                "status": "Inspect active project invariants and handover state",
                "assign": "Bind an agent to a project",
                "invariant": "Record an architectural invariant to active project",
                "promote": "Promote agent soul & invariants from project (overwrite or spawn new)",
            }
        },
        "/agent": {
            "desc": "Enter OpenClaw agent sub-shell",
            "sub": {
                "list": "List registered agents, assigned nodes, and tasks",
                "build": "Launch Socratic interview to build an agent",
                "status": "View live slot occupancy across cluster nodes",
                "select": "Attach to a specific agent session",
                "detach": "Detach from active agent and revert to blank model",
                "bind": "Bind agent to compute node (e.g. node2_edge, node1_primary)",
                "task": "Assign an active user task to an agent",
                "play": "Trigger autonomous play (Agora chatboard or research dossier)",
                "mate": "Bilateral digital reproduction with another agent into Gen-(N+1)",
                "promote": "Promote agent soul & invariants from a project to cluster-wide",
            }
        },
        "/node": {
            "desc": "Device-agnostic compute node & endpoint fleet manager",
            "sub": {
                "list": "List all registered nodes, endpoints, and statuses",
                "use": "Switch active compute node for session (/node use <node_id>)",
                "select": "Alias for 'use'",
                "status": "View active loaded model & memory on target node",
                "models": "Poll local GGUF models on target node",
                "load": "Run interactive parameter walkthrough (Auto-Best vs Custom)",
                "bind": "Bind an agent to a compute node",
                "unload": "Unload active model from target node RAM",
            }
        },
        "/fleet": {
            "desc": "Cluster fleet nodes & endpoints manager (alias for /node)",
            "sub": {
                "list": "List all registered nodes, endpoints, and statuses",
                "use": "Switch active compute node for session (/fleet use <node_id>)",
                "select": "Alias for 'use'",
                "status": "View active loaded model & memory on target node",
                "models": "Poll local GGUF models on target node",
                "load": "Run interactive parameter walkthrough (Auto-Best vs Custom)",
                "bind": "Bind an agent to a compute node",
                "unload": "Unload active model from target node RAM",
            }
        },
        "/ally": {
            "desc": "Legacy alias for /node (deprecated)",
            "sub": {
                "status": "View active loaded model & memory on edge node",
                "models": "Poll local GGUF models on edge node",
                "load": "Run interactive parameter walkthrough (Auto-Best vs Custom)",
                "bind": "Bind an agent to compute node",
                "unload": "Unload active model from edge node RAM",
            }
        },
        "/slots": {
            "desc": "Rescale parallel execution slots & verify parameter preservation",
            "sub": {
                "1": "Single slot (Full context / deep reasoning)",
                "2": "2 parallel slots (Balanced daily driver)",
                "3": "3 parallel slots (Edge agent cluster)",
                "4": "4 parallel slots (High-throughput worker pool)",
                "8": "8 parallel slots (Lightweight subagent swarm)",
            }
        },
        "/mesh": {
            "desc": "Aevum Mesh: Sovereign Agent Hive Network",
            "sub": {
                "status": "List all agents, assigned nodes, and user tasks",
                "play": "Dispatch autonomous play turn (Agora chatboard or dossier)",
                "mate": "Bilateral digital reproduction of two mature agents into Gen-(N+1)",
                "task": "Assign an active user directive to an agent",
                "clear": "Clear user task, returning agent to idle play",
            }
        },
        "/nudge": {
            "desc": "Send out-of-band non-destructive intervention to an agent",
            "sub": {}
        },
        "/streams": {
            "desc": "Concurrent side-by-side agent reasoning TUI",
            "sub": {}
        },
        "/models": {
            "desc": "Poll live loaded models or switch models across nodes",
            "sub": {
                "poll": "Poll live loaded models across cluster nodes",
                "switch": "Switch models on a compute or edge node",
            }
        },
        "/spec": {
            "desc": "Client-side speculative decoding (worker drafts, coordinator verifies)",
            "sub": {
                "status": "Check speculative mode, target/draft latency, and vocabulary alignment",
                "on": "Activate speculative decoding",
                "off": "Deactivate speculative decoding",
                "bench": "Measure the real speedup",
                "config": "Configure draft lookahead window gamma",
            }
        },
        "/runtime": {
            "desc": "Select or switch compute runtime backend (v.03 Vulkan vs v.03-rocm HIP)",
            "sub": {
                "status": "Inspect active compute runtime (Vulkan vs ROCm 10 HIP)",
                "select": "Interactive runtime switcher walkthrough",
                "rocm": "Switch primary compute to native ROCm 10 / HIP",
                "vulkan": "Revert compute to baseline Vulkan (RADV/Mesa)",
                "benchmark": "Run empirical TTFT & prefill benchmark across runtimes",
            }
        },
        "/speculative": {
            "desc": "Client-side speculative decoding (worker drafts, coordinator verifies)",
            "sub": {
                "status": "Check speculative mode, target/draft latency, and vocabulary alignment",
                "on": "Activate speculative decoding",
                "off": "Deactivate speculative decoding",
                "bench": "Measure the real speedup",
                "config": "Configure draft lookahead window gamma",
            }
        },
        "/hf": {
            "desc": "Search Hugging Face GGUF Hub with dynamic capacity math",
            "sub": {}
        },
        "/train": {
            "desc": "Inspect Unsloth/QLoRA training & 10 Golden Invariants",
            "sub": {
                "status": "View active training job status",
                "sft": "Trigger 4-bit NF4 QLoRA fine-tuning",
                "dpo": "Run Direct Preference Optimization",
                "verify": "Run 10 Golden Invariant verification",
            }
        },
        "/mem": {
            "desc": "Search Valkey A-MEM atomic fact cards (< 35 tokens)",
            "sub": {}
        },
        "/policy": {
            "desc": "View or switch security profile",
            "sub": {
                "strict": "Strict deterministic execution & verified AST",
                "tiered": "Tiered coordination with human fallback",
                "autonomous": "Full 24/7 unattended autonomous clearance",
            }
        },
        "/status": {
            "desc": "Live cluster telemetry, 24/7 autonomous thinking activity & slot occupancy",
            "sub": {}
        },
        "/activity": {
            "desc": "Live cluster telemetry, 24/7 autonomous thinking activity & slot occupancy (alias)",
            "sub": {}
        },
        "/consensus": {
            "desc": "3-Stage Multi-Agent Cross-Examination & Founder Adjudication",
            "sub": {}
        },
        "/presence": {
            "desc": "Dynamic multi-signal presence & GPU activity governor",
            "sub": {
                "status": "Inspect current presence state and workstation idle time",
                "override": "Manually override presence mode (interactive vs idle)",
                "clear": "Clear manual override and revert to automatic presence fusion",
            }
        },
        "/help": {
            "desc": "Display available slash commands and usage guide",
            "sub": {}
        },
        "/exit": {
            "desc": "Quit the harness CLI",
            "sub": {}
        }
    }

    def _get_agent_id_subs(self) -> Dict[str, str]:
        """Dynamically discover all registered agents and active sessions."""
        subs = {}
        try:
            from ..core.openclaw_engine import openclaw_engine
            for p in openclaw_engine.list_profiles():
                aid = p.get("agent_id")
                if aid:
                    subs[aid] = f"{p.get('name')} ({str(p.get('role', 'Agent'))[:25]})"
        except Exception:
            pass

        try:
            from ..data_fabric.pg_storage import relational_storage
            for s in relational_storage.list_sessions():
                aid = s.get("agent_id")
                if aid and aid not in subs:
                    subs[aid] = f"Session on {s.get('assigned_node', 'cluster')}"
        except Exception:
            pass
        return subs

    def _get_node_id_subs(self) -> Dict[str, str]:
        """Dynamically return registered node IDs and human labels."""
        try:
            from ..config import fleet_config
            return {
                nid: f"{n.name} [{n.status}]"
                for nid, n in fleet_config.nodes.items()
            }
        except Exception:
            return {"node1_primary": "Coordinator", "node1_secondary": "Worker"}

    def get_completions(self, document, complete_event):
        if not PROMPT_TOOLKIT_AVAILABLE:
            return

        text = document.text_before_cursor

        # Autocomplete popup triggers when typing slash commands
        if not text.startswith("/"):
            return

        words = text.split()

        # Case A: User typed root command followed by space or subcommands
        if len(words) >= 2 or (len(words) == 1 and text.endswith(" ")):
            root_cmd = words[0].lower()

            # Sub-case A0: /node use <node_id>, /node select <node_id>, /node load <node_id>, /node status <node_id>
            if root_cmd in ("/node", "/fleet", "/ally") and len(words) >= 2 and words[1].lower() in ("use", "select", "load", "status", "models"):
                node_subs = self._get_node_id_subs()
                current_arg = "" if text.endswith(" ") else words[-1]
                if not text.endswith(" ") and len(words) == 2:
                    current_arg = ""
                for nid, desc in node_subs.items():
                    if nid.lower().startswith(current_arg.lower()):
                        yield Completion(
                            nid,
                            start_position=-len(current_arg),
                            display=nid,
                            display_meta=desc
                        )
                return

            # Sub-case A1: /agent select <agent_id>, /agent bind <agent_id>, /node bind <agent_id>, /fleet bind <agent_id>
            if (root_cmd == "/agent" and len(words) >= 2 and words[1].lower() in ("select", "bind")) or \
               (root_cmd in ("/node", "/fleet", "/ally") and len(words) >= 2 and words[1].lower() == "bind"):
                agent_subs = self._get_agent_id_subs()
                current_arg = "" if text.endswith(" ") else words[-1]
                if not text.endswith(" ") and len(words) == 2:
                    current_arg = ""
                for aid, desc in agent_subs.items():
                    if aid.lower().startswith(current_arg.lower()):
                        yield Completion(
                            aid,
                            start_position=-len(current_arg),
                            display=aid,
                            display_meta=desc
                        )
                return

            # Sub-case A2: /nudge <agent_id>
            if root_cmd == "/nudge" and (len(words) == 1 or (len(words) == 2 and not text.endswith(" "))):
                agent_subs = self._get_agent_id_subs()
                current_arg = "" if text.endswith(" ") else words[-1]
                for aid, desc in agent_subs.items():
                    if aid.lower().startswith(current_arg.lower()):
                        yield Completion(
                            aid,
                            start_position=-len(current_arg),
                            display=aid,
                            display_meta=desc
                        )
                return

            # Sub-case A3: Root subcommands (e.g., /agent list, /policy strict)
            if root_cmd in self.COMMANDS:
                sub_dict = dict(self.COMMANDS[root_cmd].get("sub", {}))
                current_sub = "" if text.endswith(" ") else words[-1]
                for subcmd, desc in sub_dict.items():
                    if subcmd.lower().startswith(current_sub.lower()):
                        yield Completion(
                            subcmd,
                            start_position=-len(current_sub),
                            display=subcmd,
                            display_meta=desc
                        )
            return

        # Case B: User is typing root command (e.g., "/" or "/a" or "/agent")
        prefix = words[0] if words else text
        for cmd, info in self.COMMANDS.items():
            if cmd.lower().startswith(prefix.lower()):
                yield Completion(
                    cmd,
                    start_position=-len(prefix),
                    display=cmd,
                    display_meta=info["desc"]
                )
            # If user has typed the command name (e.g., "/agent"), also surface available subcommands!
            if prefix.lower() == cmd.lower():
                sub_dict = dict(self.COMMANDS.get(cmd, {}).get("sub", {}))
                for subcmd, desc in sub_dict.items():
                    full_sub = f"{cmd} {subcmd}"
                    yield Completion(
                        full_sub,
                        start_position=-len(prefix),
                        display=f"{cmd} {subcmd}",
                        display_meta=desc
                    )


def create_prompt_session() -> Optional[Any]:
    """
    Initializes a prompt_toolkit PromptSession with a custom cyber-brutalist theme,
    arrow-key dropdown navigation, and tab-completion.
    Falls back gracefully to None in non-console or headless environments.
    """
    if not PROMPT_TOOLKIT_AVAILABLE:
        return None

    try:
        os.makedirs("data", exist_ok=True)
        history_file = os.path.join("data", ".cli_history")

        kb = KeyBindings()

        # When completion menu is visible, Down/Up arrows navigate choices
        @kb.add("down", filter=has_completions)
        def _down(event):
            event.current_buffer.complete_next()

        @kb.add("up", filter=has_completions)
        def _up(event):
            event.current_buffer.complete_previous()

        # Tab triggers completion or cycles to next option
        @kb.add("tab")
        def _tab(event):
            b = event.current_buffer
            if b.complete_state:
                b.complete_next()
            else:
                b.start_completion(select_first=True)

        # Shift-Tab navigates backwards through completions
        @kb.add("s-tab", filter=has_completions)
        def _stab(event):
            event.current_buffer.complete_previous()

        # StoneSage 90s cyber-brutalist palette (#008080 teal highlight)
        custom_style = Style.from_dict({
            "prompt": "bold ansicyan",
            "completion-menu.completion": "bg:#1e1e1e #cccccc",
            "completion-menu.completion.current": "bg:#008080 #ffffff bold",
            "completion-menu.meta.completion": "bg:#2a2a2a #888888",
            "completion-menu.meta.completion.current": "bg:#005555 #ffffff italic",
            "scrollbar.background": "bg:#1e1e1e",
            "scrollbar.button": "bg:#008080",
        })

        return PromptSession(
            completer=HarnessCompleter(),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            key_bindings=kb,
            style=custom_style,
            history=FileHistory(history_file),
        )
    except Exception:
        # Fallback for environments lacking standard Windows console buffer
        return None
