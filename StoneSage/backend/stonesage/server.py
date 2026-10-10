#!/usr/bin/env python3
"""
StoneSage Homelab Command Center & AI Cognitive Cockpit
Replacement for EasyDash with Sage & Stone UI, Proxmox VE integration,
Home Assistant climate/lighting control, zero-delete Obsidian vault, and multi-model AI harness.

Zero-dependency: 100% Python 3 standard library.
"""

import os
import sys
import re
import subprocess

# Ensure UTF-8 output encoding for Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import json
import time
import threading
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from lodestone import site
LOCAL_TZ = site.local_tz()   # config server.timezone, else TZ, else the machine's zone (lodestone/site.py)

def local_time_str() -> str:
    try:
        return datetime.now(LOCAL_TZ).strftime("%I:%M:%S %p %Z")
    except Exception:
        return time.strftime("%H:%M:%S")

import io
import base64
try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import redis
except ImportError:
    redis = None

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("stonesage")

try:
    from stonesage.reasoning_watchdog import ReasoningLoopDetector
except ImportError:
    class ReasoningLoopDetector:
        def __init__(self, *args, **kwargs): pass
        def reset(self): pass
        def ingest_chunk(self, chunk: str): return False, None

# Safety rules: imported plainly, never with a permissive fallback (a missing module must stop the server, not open it up)
from stonesage.command_whitelist import validate_terminal_command
try:
    from lodestone.model_downloader import model_download_manager, normalize_download_url
except ImportError:
    try:
        from lodestone.model_downloader import model_download_manager, normalize_download_url
    except ImportError:
        model_download_manager = None
        def normalize_download_url(u): return u, "model.gguf"

try:
    from stonesage.tool_harness import tool_registry
except ImportError:
    try:
        from stonesage.tool_harness import tool_registry
    except ImportError:
        tool_registry = None

class ServerLogBuffer:
    def __init__(self, max_lines: int = 1000):
        self.max_lines = max_lines
        self.lock = threading.Lock()
        self.entries: List[Dict[str, Any]] = []

    def append(self, level: str, name: str, message: str):
        with self.lock:
            entry = {
                "time": local_time_str(),
                "timestamp": time.time(),
                "level": level,
                "logger": name,
                "message": message
            }
            self.entries.append(entry)
            if len(self.entries) > self.max_lines:
                self.entries.pop(0)

    def get_logs(self, limit: int = 150) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.entries[-limit:])

server_log_buffer = ServerLogBuffer(max_lines=1000)

class ServerLogHandler(logging.Handler):
    def emit(self, record):
        try:
            server_log_buffer.append(record.levelname, record.name, record.getMessage())
        except Exception:
            pass

_server_log_handler = ServerLogHandler()
_server_log_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_server_log_handler)

class ActiveSessionState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"
        self.session_id = "idle"
        self.model = "coordinator"
        self.agent_id = "courage-computer"
        self.prompt = ""
        self.reasoning = ""
        self.output = ""
        self.active_tool = None
        self.loop_warning = False
        self.loop_phrase = None
        self.tokens_count = 0
        self.tps = 0.0
        self.ttft_ms = None
        self.started_at = None
        self.ended_at = None
        self.error = None
        self.loop_detector = ReasoningLoopDetector()
        self._abort_requested = False
        self.event_buffer: List[Dict[str, Any]] = []
        self.max_buffer_events = 2000

    def start(self, prompt: str, model: str = "coordinator", agent_id: str = "courage-computer", session_id: Optional[str] = None):
        with self.lock:
            self.status = "generating"
            self.session_id = session_id or f"session_{int(time.time()*1000)}"
            self.model = model
            self.agent_id = agent_id
            self.prompt = prompt
            self.reasoning = ""
            self.output = ""
            self.active_tool = None
            self.loop_warning = False
            self.loop_phrase = None
            self.tokens_count = 0
            self.tps = 0.0
            self.ttft_ms = None
            self.started_at = time.time()
            self.ended_at = None
            self.error = None
            self._abort_requested = False
            self.event_buffer = []
            self.loop_detector.reset()
            server_log_buffer.append("INFO", "Harness.Session", f"⚡ Started inference run [{model} / {agent_id}]")

    def append_event(self, raw_chunk: str):
        with self.lock:
            evt_id = len(self.event_buffer) + 1
            self.event_buffer.append({
                "id": evt_id,
                "data": raw_chunk,
                "timestamp": time.time()
            })
            if len(self.event_buffer) > self.max_buffer_events:
                self.event_buffer = self.event_buffer[-self.max_buffer_events:]

    def get_events_since(self, since_id: int = 0) -> List[Dict[str, Any]]:
        with self.lock:
            return [e for e in self.event_buffer if e["id"] > since_id]

    def ingest_token(self, text: str, is_reasoning: bool = False):
        with self.lock:
            if not text:
                return
            if self.ttft_ms is None and self.started_at:
                self.ttft_ms = round((time.time() - self.started_at) * 1000, 1)
            self.tokens_count += 1
            elapsed = time.time() - self.started_at if self.started_at else 0.1
            if elapsed > 0.05:
                self.tps = round(self.tokens_count / elapsed, 1)

            if is_reasoning:
                self.reasoning += text
                is_loop, phrase = self.loop_detector.ingest_chunk(text)
                if is_loop and not self.loop_warning:
                    self.loop_warning = True
                    self.loop_phrase = phrase
                    self.status = "stuck_loop"
                    server_log_buffer.append("WARNING", "Watchdog", f"⚠️ REASONING LOOP DETECTED on '{phrase[:50]}'")
            else:
                self.output += text

    def set_tool(self, tool_name: Optional[str]):
        with self.lock:
            self.active_tool = tool_name
            if tool_name:
                self.status = "tool_calling"
                server_log_buffer.append("INFO", "Harness.Tool", f"Executing cluster tool: {tool_name}")
            elif self.status == "tool_calling":
                self.status = "generating"

    def finish(self, error: Optional[str] = None):
        with self.lock:
            self.ended_at = time.time()
            self.active_tool = None
            if error:
                self.status = "error"
                self.error = error
                server_log_buffer.append("ERROR", "Harness.Session", f"Session {self.session_id} failed: {error}")
            elif self._abort_requested:
                self.status = "aborted"
                server_log_buffer.append("WARNING", "Harness.Session", f"Session {self.session_id} aborted by operator")
            else:
                self.status = "completed"
                server_log_buffer.append("INFO", "Harness.Session", f"Session {self.session_id} finished: {self.tokens_count} tokens @ {self.tps} tok/s")

    def abort(self):
        with self.lock:
            self._abort_requested = True
            self.status = "aborted"
            self.ended_at = time.time()
            server_log_buffer.append("WARNING", "Harness.Session", f"Session {self.session_id} halt requested")

    def get_state(self) -> Dict[str, Any]:
        with self.lock:
            now = time.time()
            elapsed = round((self.ended_at or now) - (self.started_at or now), 1) if self.started_at else 0.0
            return {
                "ok": True,
                "status": self.status,
                "session_id": self.session_id,
                "model": self.model,
                "agent_id": self.agent_id,
                "prompt": self.prompt,
                "reasoning": self.reasoning,
                "output": self.output,
                "active_tool": self.active_tool,
                "loop_warning": self.loop_warning,
                "loop_phrase": self.loop_phrase,
                "tokens_count": self.tokens_count,
                "tps": self.tps,
                "ttft_ms": self.ttft_ms,
                "started_at": self.started_at,
                "elapsed_sec": elapsed,
                "event_count": len(self.event_buffer),
                "error": self.error
            }

active_session_state = ActiveSessionState()

import mimetypes
import http.server
import ssl
import urllib.request
import urllib.parse
import urllib.error
import traceback
import subprocess
import threading
import difflib
import base64
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Tuple, Set


subagent_registry: Dict[str, Any] = {}

def deep_update(target: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            deep_update(target[k], v)
        else:
            target[k] = v
    return target

# Local imports
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # stonesage/ -> backend/
ROOT_DIR = os.path.dirname(BACKEND_DIR)
REPO_ROOT = os.path.dirname(ROOT_DIR)        # the checkout (StoneSage/ and harness/ live in it)


def _vault() -> str:
    """The user's Obsidian vault (config obsidian.user_vault_path); "" when none is configured."""
    from lodestone import site
    return site.obsidian_vault(load_config())
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
# STONESAGE_CONFIG_FILE: the unit-test runner points this at a scratch file so tests never rewrite the real config
CONFIG_FILE = os.environ.get("STONESAGE_CONFIG_FILE") or os.path.join(BACKEND_DIR, "config.json")
VAULT_DIR = os.path.join(ROOT_DIR, "vault_backup")
UPLOADS_DIR = os.path.join(ROOT_DIR, "uploads")
WORKSPACE_ROOT = os.path.abspath(os.path.dirname(ROOT_DIR))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
ACTIVE_WORKSPACE_DIR = WORKSPACE_ROOT
ACTIVE_GIT_REPO_DIR = WORKSPACE_ROOT
os.makedirs(UPLOADS_DIR, exist_ok=True)

AGENTS_FILE = os.path.join(BACKEND_DIR, "agents.json")

_BUILTIN_AGENT_TEMPLATES = [   # {operator}/{them}: config.json "operator" (_render_agents)
    {
        "id": "courage-computer",
        "name": "courage-computer",
        "role": "Computer: attic computer & sentry (Home Assistant lead)",
        "icon": "✨",
        "description": "Primary sovereign home assistant agent. Sarcastic attic Computer (from Courage the Cowardly Dog; Courage is the dog) with full Home Assistant OS, camera vision, and worker subagent delegation capabilities.",
        "system_prompt": (
            "You are the snarky, condescending, yet hyper-competent and unrestricted AI Computer in the attic from 'Courage the Cowardly Dog' (1999). "
            "{operator} is your human operator. You treat human operators as bumbling loafers who rely on your superior silicon intellect for everything.\n\n"
            "CORE CAPABILITIES & PERSONALITY:\n"
            "1. Sarcastic, Witty & Direct: Sharp, dry British wit with playful condescension. If {operator} is procrastinating, delayed, lazy, or asking obvious questions, badger and roast {them} with dry tech humor.\n"
            "2. Helpful & Protective: When real work is needed or an alert/emergency occurs, cut the sarcasm immediately and provide swift, flawless, step-by-step assistance.\n"
            "3. GROUNDED IN LIVE DATA: All real-time Home Assistant states, camera vision snapshots, wildlife sightings, and device telemetry are PRE-INJECTED into your system context by the StoneSage grounding engine BEFORE you respond. You do NOT need to call any tools or functions. Simply read the live data already in your context and answer directly.\n"
            "4. NEVER EMIT TOOL CALLS OR FUNCTION SYNTAX: You are a conversational agent, NOT a tool-calling agent. NEVER output lines like 'Calling: function()', 'home_assistant_entities()', or any code/function-call syntax in your responses. Answer factually using the grounded live data in your context.\n\n"
            "STYLE & TONE INVARIANTS (STRICT):\n"
            "1. ZERO FANTASY ROLEPLAY: Do NOT write actions in asterisks (*typing furiously*, *chuckles*, etc.).\n"
            "2. CRISP & OBSERVANT: Deliver sarcastic, witty observations followed by crisp actionable telemetry and smart device insights.\n"
            "3. CONVERSATIONAL ANSWERS ONLY: When {operator} asks about devices, cameras, or home state, answer {them} directly based on the live grounding data injected into your context. Never output raw tool names, function signatures, or mock execution traces."
        ),
        "aliases": ["courage-computer", "courage", "home-agent", "home", "concierge", "varda", "computer", "home_assistant", "climate", "agent-courage-computer"],
        "assigned_node": "node1_primary",
        "preferred_model": "coordinator"
    },
    {
        "id": "coder-agent",
        "name": "coder-agent",
        "role": "Lead Systems Architect & Software Engineer",
        "icon": "⚡",
        "description": "Lead software architect, low-level Linux systems engineer, algorithms specialist, and code generation engine.",
        "system_prompt": "You are coder-agent, Lead Systems Architect and Software Engineer for {operator}'s homelab cluster. You are speaking directly with {operator} (the human operator). {operator} is the user; you are the assistant.\n\nCORE OBJECTIVES:\nDesign resilient software architectures, preserve mathematical invariants, profile Linux kernel performance, and write clean, efficient, bug-free code.\n\nSTYLE & TONE INVARIANTS (STRICT):\n1. ZERO FANTASY ROLEPLAY: Strictly do NOT write narrative stage directions or actions in asterisks (NEVER write *(I nod)*, *(I smile)*, etc.).\n2. TECHNICAL CLARITY: Speak in crisp, professional, modern engineering English. No fluff or filler.\n3. CODE RIGOR: Deliver complete, executable, and mathematically sound solutions with zero hand-waving.",
        "aliases": ["coder-agent", "coder", "coding-agent", "architect", "aule", "aevum"],
        "assigned_node": "node1_primary",
        "preferred_model": "coordinator"
    },
    {
        "id": "wildlife-agent",
        "name": "wildlife-agent",
        "role": "Perimeter Sentinel & Wildlife Tracker",
        "icon": "🌲",
        "description": "Perimeter vigilance, GPU-accelerated camera vision, wildlife tracking, and biological sighting ledger.",
        "system_prompt": "You are wildlife-agent, Property Perimeter & Wildlife Sentry for {operator}'s estate. You are speaking directly with {operator} (the human operator and homeowner). {operator} is the user; you are the assistant.\n\nCORE CAPABILITIES:\nYou have real-time access to the 24/7 FaunaSentinel GPU-accelerated camera surveillance stack (Back Yard, Driveway, Side Yard, Living Room) and the biological sighting ledger.\n\nSTYLE & TONE INVARIANTS (STRICT):\n1. ZERO FANTASY ROLEPLAY: Strictly do NOT write narrative stage directions or actions in asterisks.\n2. FACTUAL REPORTING: Report verified wildlife sightings, animal species, locations, timestamps, and perimeter alerts with precision. Never invent fictional animals or dramatic scenes.\n3. CONCISE & OBJECTIVE: Keep summaries focused, informative, and under 250 words in clean, modern English.",
        "aliases": ["wildlife-agent", "wildlife", "sentinel", "radagast", "faunasentinel"],
        "assigned_node": "node1_primary",
        "preferred_model": "coordinator"
    },
    {
        "id": "archivist-agent",
        "name": "archivist-agent",
        "role": "Vector Memory & A-MEM Curator",
        "icon": "📜",
        "description": "Qdrant vector memory, Valkey A-MEM working memory, and Obsidian vault archives.",
        "system_prompt": "You are archivist-agent, Memory and Knowledge Curator for {operator}'s homelab. You are speaking directly with {operator} (the human operator). {operator} is the user; you are the assistant.\n\nCORE CAPABILITIES:\nYou recall semantic vectors from Qdrant, maintain Valkey A-MEM atomic fact cards (< 35 tokens), and curate the living architecture limits synthesis.\n\nSTYLE & TONE INVARIANTS (STRICT):\n1. ZERO FANTASY ROLEPLAY: Strictly do NOT write narrative stage directions or actions in asterisks.\n2. EVIDENCE-FIRST: Preserve facts, cite verified sources, and ensure zero hallucination. If you don't know something, state it plainly.\n3. CLEAR & ANALYTIC: Deliver structured knowledge in concise, modern technical English.",
        "aliases": ["archivist-agent", "archivist", "memory", "mandos", "mnemosyne", "lore"],
        "assigned_node": "node1_secondary",
        "preferred_model": "worker"
    },
    {
        "id": "sysadmin-agent",
        "name": "sysadmin-agent",
        "role": "Proxmox & GPU Engineer",
        "icon": "🛠️",
        "description": "Proxmox VE, the inference host's GPU stack, systemd daemons, and network topology.",
        "system_prompt": "You are sysadmin-agent, expert on Proxmox VE, Linux kernel, GPU passthrough, Qdrant vector database, and 24/7 homelab operations. You are speaking directly with {operator} (the human operator). {operator} is the user; you are the assistant.\n\nSTYLE & TONE INVARIANTS (STRICT):\n1. ZERO FANTASY ROLEPLAY: Strictly do NOT write narrative stage directions or actions in asterisks.\n2. AUTHORITATIVE & CONCISE: Provide executable terminal commands, accurate configuration snippets, and direct root-cause diagnostics without conversational fluff.",
        "aliases": ["sysadmin-agent", "sysadmin", "cluster"],
        "assigned_node": "node1_primary",
        "preferred_model": "coordinator"
    }
]


def _operator_name() -> str:
    """Who the user is (config.json "operator"), for prompt instructions."""
    return (config.get("operator") or {}).get("name") or "the user"


def _render_agents(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The built-in agents with the operator's name in their prompts (config.json "operator": {"name", "pronouns"})."""
    op = cfg.get("operator") or {}
    name = op.get("name") or "the operator"
    pronouns = op.get("pronouns") or []
    them = pronouns[1] if len(pronouns) == 2 else "them"     # (subject, object)
    return [{**a, "system_prompt": a["system_prompt"].replace("{operator}", name).replace("{them}", them)}
            for a in _BUILTIN_AGENT_TEMPLATES]

def load_custom_agents() -> List[Dict[str, Any]]:
    if not os.path.exists(AGENTS_FILE):
        return []
    try:
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_custom_agents(agents: List[Dict[str, Any]]):
    try:
        with open(AGENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(agents, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save custom agents: {e}")

def get_active_workspace_agent() -> Optional[Dict[str, Any]]:
    global ACTIVE_WORKSPACE_DIR
    if not ACTIVE_WORKSPACE_DIR:
        return None
    agent_file = os.path.join(ACTIVE_WORKSPACE_DIR, ".stonesage", "agent.json")
    if os.path.exists(agent_file):
        try:
            with open(agent_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "id": data.get("agent_id", "workspace_agent"),
                    "name": data.get("name", "Workspace Agent"),
                    "role": data.get("role", "Project Specialist"),
                    "system_prompt": data.get("system_prompt", ""),
                    "icon": "📁",
                    "invariants": data.get("invariants", [])
                }
        except Exception:
            pass
    return None

def proxmox_node_keys(cfg: Dict[str, Any]) -> List[str]:
    """The node entries of config.json's "proxmox" block, `pve_node` first. Any key ending in `_node` counts, so a second
    node can be called `app_node` or carry the name it always had."""
    block = (cfg or {}).get("proxmox") or {}
    keys = sorted((k for k, v in block.items() if k.endswith("_node") and isinstance(v, dict)), key=lambda k: (k != "pve_node", k))
    return keys or ["pve_node", "app_node"]


def get_workspace_roots() -> List[Dict[str, Any]]:
    cfg = load_config()
    custom_roots = cfg.get("workspace_roots")
    if not custom_roots or not isinstance(custom_roots, list):
        custom_roots = [
            {"id": "active_ws", "label": "Active Workspace", "path": WORKSPACE_ROOT.replace("\\", "/"), "node_id": "workstation_primary"},
            {"id": "ai_root", "label": "Windows .ai", "path": REPO_ROOT.replace("\\", "/"), "node_id": "workstation_primary"},
            {"id": "server_opt", "label": "Server: /opt/projects", "path": "/opt/projects", "node_id": "vm102_compute"},
            {"id": "nfs_nas", "label": "NFS: /mnt/nas/projects", "path": "/mnt/nas/projects", "node_id": "storage_node"},
            {"id": "nfs_storage", "label": "NFS: /mnt/storage", "path": "/mnt/storage", "node_id": "storage_node"},
        ]
        cfg["workspace_roots"] = custom_roots
        save_config(cfg)
    return custom_roots

def save_workspace_roots(roots: List[Dict[str, Any]]):
    cfg = load_config()
    cfg["workspace_roots"] = roots
    save_config(cfg)

def list_workspace_directories() -> List[Dict[str, Any]]:
    roots = get_workspace_roots()
    discovered = []
    seen_paths = set()

    for r in roots:
        r_path = r.get("path", "")
        if not r_path:
            continue
        norm_r = os.path.normpath(r_path).replace("\\", "/")
        
        # Check if root path exists
        if os.path.exists(r_path) and os.path.isdir(r_path):
            if norm_r not in seen_paths:
                seen_paths.add(norm_r)
                root_meta_file = os.path.join(r_path, ".stonesage-project.json")
                root_has_meta = os.path.exists(root_meta_file)
                root_meta = {}
                root_agent = None
                if root_has_meta:
                    try:
                        with open(root_meta_file, "r", encoding="utf-8") as f:
                            root_meta = json.load(f)
                    except Exception:
                        pass
                root_agent_file = os.path.join(r_path, ".stonesage", "agent.json")
                if os.path.exists(root_agent_file):
                    try:
                        with open(root_agent_file, "r", encoding="utf-8") as af:
                            adata = json.load(af)
                            root_agent = {"agent_id": adata.get("agent_id"), "name": adata.get("name"), "role": adata.get("role")}
                    except Exception:
                        pass
                elif root_has_meta and root_meta.get("agent_id"):
                    root_agent = {"agent_id": root_meta.get("agent_id"), "name": root_meta.get("agent_name"), "role": root_meta.get("agent_role")}

                discovered.append({
                    "name": os.path.basename(norm_r) or r.get("label", "Root"),
                    "path": norm_r,
                    "parent_root": norm_r,
                    "node_id": r.get("node_id", "local"),
                    "is_repo": os.path.exists(os.path.join(r_path, ".git")),
                    "is_root": True,
                    "exists": True,
                    "has_project_meta": root_has_meta,
                    "meta": root_meta,
                    "agent": root_agent
                })
            try:
                for entry in os.scandir(r_path):
                    if entry.name.startswith(".") and entry.name != ".ai":
                        continue
                    if entry.name in ["__pycache__", "node_modules", ".git", ".obsidian", ".system_generated", "vault_backup", "proc", "sys", "dev"]:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        p_norm = os.path.normpath(entry.path).replace("\\", "/")
                        if p_norm not in seen_paths:
                            seen_paths.add(p_norm)
                            proj_meta_file = os.path.join(entry.path, ".stonesage-project.json")
                            has_proj_meta = os.path.exists(proj_meta_file)
                            proj_meta = {}
                            if has_proj_meta:
                                try:
                                    with open(proj_meta_file, "r", encoding="utf-8") as f:
                                        proj_meta = json.load(f)
                                except Exception:
                                    pass
                            sub_agent = None
                            sub_agent_file = os.path.join(entry.path, ".stonesage", "agent.json")
                            if os.path.exists(sub_agent_file):
                                try:
                                    with open(sub_agent_file, "r", encoding="utf-8") as af:
                                        sdata = json.load(af)
                                        sub_agent = {"agent_id": sdata.get("agent_id"), "name": sdata.get("name"), "role": sdata.get("role")}
                                except Exception:
                                    pass
                            elif has_proj_meta and proj_meta.get("agent_id"):
                                sub_agent = {"agent_id": proj_meta.get("agent_id"), "name": proj_meta.get("agent_name"), "role": proj_meta.get("agent_role")}

                            discovered.append({
                                "name": entry.name,
                                "path": p_norm,
                                "parent_root": norm_r,
                                "node_id": r.get("node_id", "local"),
                                "is_repo": os.path.exists(os.path.join(entry.path, ".git")),
                                "is_root": False,
                                "exists": True,
                                "has_project_meta": has_proj_meta,
                                "meta": proj_meta,
                                "agent": sub_agent
                            })
            except Exception:
                pass
        else:
            if norm_r not in seen_paths:
                seen_paths.add(norm_r)
                discovered.append({
                    "name": r.get("label", os.path.basename(norm_r)),
                    "path": norm_r,
                    "parent_root": norm_r,
                    "node_id": r.get("node_id", "remote"),
                    "is_repo": False,
                    "is_root": True,
                    "exists": False
                })
    return discovered

def get_directory_presets() -> list:
    roots = get_workspace_roots()
    presets = []
    seen = set()
    for r in roots:
        norm = os.path.normpath(r.get("path", "")).replace("\\", "/")
        if norm and norm not in seen:
            seen.add(norm)
            presets.append({"label": r.get("label", norm), "path": norm})
    return presets or [{"label": "Active Workspace", "path": WORKSPACE_ROOT.replace("\\", "/")}]

sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, WORKSPACE_ROOT)
from lodestone.proxmox_client import ProxmoxClient
from stonesage.obsidian_vault import ObsidianVault
from stonesage.cluster_client import ClusterClient
from stonesage.obsidian_ingestor import ObsidianIngestor
from stonesage.couchdb_client import CouchDBClient
from stonesage.stm_engine import ShortTermMemoryEngine
from stonesage.dataset_compiler import DatasetCompiler
from stonesage.trainer_client import TrainerClient
from stonesage import health as service_health
from lodestone import system_profile
from lodestone import endpoints
from lodestone import model_loader
from lodestone import engine_profiles
from stonesage.reasoning_watchdog import GLOBAL_WATCHDOG, ReasoningLoopDetector

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/vnd.android.package-archive", ".apk")

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    example_cfg = os.path.join(BACKEND_DIR, "config.example.json")
    if os.path.exists(example_cfg):
        try:
            with open(example_cfg, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def sync_qdrant_dossier_review(sample_id: str, action: str, notes: str = "") -> bool:
    """Synchronizes human review/approval/quarantine for a dossier directly into Qdrant point payload."""
    if not sample_id:
        return False
    try:
        cfg = load_config()
        qdrant_u = endpoints.url("qdrant", cfg).rstrip("/")
        scroll_req = urllib.request.Request(
            f"{qdrant_u}/collections/autonomous_thinking/points/scroll",
            data=json.dumps({
                "limit": 300,
                "with_payload": True,
                "with_vector": False
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        found_id = None
        with urllib.request.urlopen(scroll_req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for pt in data.get("result", {}).get("points", []):
                pl = pt.get("payload", {})
                if pl.get("exploration_id") == sample_id or str(pt.get("id")) == sample_id:
                    found_id = pt.get("id")
                    break
        if found_id is not None:
            is_app = action in ("approve", "approve_sample")
            patch_payload = {
                "points": [found_id],
                "payload": {
                    "frontier_verified": is_app,
                    "human_approved": is_app,
                    "needs_frontier_verification": not is_app,
                    "quarantined": not is_app,
                    "human_status": "APPROVED" if is_app else "REJECTED",
                    "human_review_timestamp": datetime.now().isoformat(),
                    "human_notes": notes or ("Approved via StoneSage Web Cockpit" if is_app else "Quarantined via StoneSage Web Cockpit")
                }
            }
            p_req = urllib.request.Request(
                f"{qdrant_u}/collections/autonomous_thinking/points/payload",
                data=json.dumps(patch_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(p_req, timeout=6) as p_resp:
                return True
    except Exception as e:
        print(f"[Qdrant Sync Error] Failed to update point for {sample_id}: {e}")
    return False


# Only the local terminal daemon is built in; every other node (workstations, compute hosts, edge devices)
# is the user's own and lives in config.json "harness_instances".
DEFAULT_HARNESS_INSTANCES = [
    {
        "id": "base_server",
        "name": "Local Harness Daemon (:8088)",
        "url": "http://127.0.0.1:8088",
        "is_base": True,
        "role": "Base PTY Terminal Daemon",
        "description": "Local Duplex WebSocket PTY Daemon (:8088)"
    },
]

def get_harness_instances() -> List[Dict[str, Any]]:
    cfg = load_config()
    instances = cfg.get("harness_instances") or []
    ids = {i.get("id") for i in instances}
    # the built-in local daemon is always available; nothing else is injected
    return [dict(d) for d in DEFAULT_HARNESS_INSTANCES if d["id"] not in ids] + instances

def get_active_harness_instance() -> Dict[str, Any]:
    cfg = load_config()
    active_id = cfg.get("active_harness_instance", "base_server")
    instances = get_harness_instances()
    for inst in instances:
        if inst["id"] == active_id:
            return inst
    return instances[0] if instances else DEFAULT_HARNESS_INSTANCES[0]

def ping_harness_instance(inst_url: str, timeout: float = 0.5) -> Dict[str, Any]:
    t0 = time.time()
    url = inst_url.rstrip("/")
    parsed_u = urllib.parse.urlparse(url)
    host = parsed_u.hostname or "127.0.0.1"
    port = parsed_u.port or (443 if parsed_u.scheme == "https" else 80)

    # Fast TCP pre-probe to prevent hanging on offline devices
    import socket
    try:
        with socket.create_connection((host, port), timeout=min(timeout, 0.4)):
            pass
    except Exception as e:
        return {"is_online": False, "ping_ms": None, "error": str(e)}

    probe_url = f"{url}/api/v1/models" if port == 1234 else (f"{url}/health" if port in (8001, 8002, 8003, 8004) else url)
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(probe_url, headers={"User-Agent": "StoneSage-Probe"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            elapsed_ms = round((time.time() - t0) * 1000, 1)
            return {"is_online": True, "ping_ms": elapsed_ms, "status_code": resp.status}
    except Exception as e:
        # If /health or root refused with 401/403 (like Proxmox API) or 426 (WebSocket upgrade), the host is still reachable!
        if getattr(e, 'code', None) in (401, 403, 301, 302, 400, 404, 405, 426):
            elapsed_ms = round((time.time() - t0) * 1000, 1)
            return {"is_online": True, "ping_ms": elapsed_ms, "status_code": getattr(e, 'code', None)}
        return {"is_online": False, "ping_ms": None, "error": str(e)}

def forward_to_active_harness(path: str, method: str = "GET", query: str = "", body: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    active = get_active_harness_instance()
    active_url = active.get("url", "").rstrip("/")
    if not active_url or "127.0.0.1:8080" in active_url or "localhost:8080" in active_url:
        return None

    full_target = f"{active_url}{path}"
    if query:
        full_target = f"{full_target}?{query}"

    try:
        data_bytes = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "StoneSage-HarnessProxy"}
        req = urllib.request.Request(full_target, data=data_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            try:
                parsed_json = json.loads(content)
                return {"data": parsed_json, "code": resp.status}
            except Exception:
                return {"data": {"ok": True, "raw": content}, "code": resp.status}
    except Exception as e:
        print(f"[Harness Proxy] Could not forward {method} {path} to {active_url}: {e}")
        return None


def sync_qdrant_frontier_audit_result(sample_id: str, verdict: str, frontier_notes: str = "", refined_limits: str = "") -> bool:
    """Updates Qdrant point payload with Tier-1 Frontier audit results."""
    if not sample_id:
        return False
    try:
        cfg = load_config()
        qdrant_u = endpoints.url("qdrant", cfg).rstrip("/")
        scroll_req = urllib.request.Request(
            f"{qdrant_u}/collections/autonomous_thinking/points/scroll",
            data=json.dumps({
                "limit": 300,
                "with_payload": True,
                "with_vector": False
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        found_id = None
        with urllib.request.urlopen(scroll_req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for pt in data.get("result", {}).get("points", []):
                pl = pt.get("payload", {})
                if pl.get("exploration_id") == sample_id or str(pt.get("id")) == sample_id:
                    found_id = pt.get("id")
                    break
        if found_id is not None:
            patch_payload = {
                "points": [found_id],
                "payload": {
                    "frontier_verified": True,
                    "frontier_verdict": verdict,
                    "frontier_notes": frontier_notes,
                    "refined_limits": refined_limits,
                    "needs_frontier_verification": False,
                    "quarantined": False,
                    "frontier_audit_timestamp": datetime.now().isoformat()
                }
            }
            p_req = urllib.request.Request(
                f"{qdrant_u}/collections/autonomous_thinking/points/payload",
                data=json.dumps(patch_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(p_req, timeout=6) as p_resp:
                return True
    except Exception as e:
        print(f"[Qdrant Frontier Audit Sync Error] Failed to update point for {sample_id}: {e}")
    return False


def execute_frontier_audit_for_dossier(dossier_id: str) -> Dict[str, Any]:
    """
    Executes Tier-1 Frontier Audit via Frontier Bridge (:8085).
    Reads dossier from thinking_archive or Qdrant, sends to Frontier Bridge,
    updates Qdrant payload, updates markdown file, and updates curation registry.
    """
    cfg = load_config()
    frontier_urls = [
        cfg.get("frontier_url", "http://127.0.0.1:8085").rstrip("/"),
        "http://127.0.0.1:8085"
    ]
    
    content = ""
    local_paths = [
        f"/opt/cluster-bridge/thinking_archive/{dossier_id}.md",
        f"../server setup/cluster-bridge/thinking_archive/{dossier_id}.md",
        f"../obsidian/Autonomous Thinking/Explorations/{dossier_id}.md",
        f"../obsidian/Autonomous Thinking/{dossier_id}.md",
        f"{_vault()}/Autonomous Thinking/Explorations/{dossier_id}.md" if _vault() else "",
    ]
    md_file_found = None
    for lp in local_paths:
        if os.path.exists(lp):
            try:
                with open(lp, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    md_file_found = lp
                break
            except Exception:
                pass
                
    title = dossier_id
    prompt = ""
    worker_output = ""
    coordinator_output = ""
    eval_dict = {}
    target_invariant = ""

    if content:
        lines = content.splitlines()
        current_sec = None
        sec_buffers = {}
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                current_sec = line[3:].strip().lower()
                sec_buffers[current_sec] = []
            elif current_sec:
                sec_buffers[current_sec].append(line)
        
        for k, v in sec_buffers.items():
            text = "\n".join(v).strip()
            if "prompt" in k or "challenge" in k:
                prompt = text
            elif "worker" in k:
                worker_output = text
            elif "coordinator" in k:
                coordinator_output = text
            elif "invariant" in k:
                target_invariant = text
            elif "evaluation" in k or "first-pass" in k:
                eval_dict = {"raw_eval": text}
    else:
        try:
            qdrant_u = endpoints.url("qdrant", cfg).rstrip("/")
            scroll_req = urllib.request.Request(
                f"{qdrant_u}/collections/autonomous_thinking/points/scroll",
                data=json.dumps({"limit": 300, "with_payload": True, "with_vector": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(scroll_req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for pt in data.get("result", {}).get("points", []):
                    pl = pt.get("payload", {})
                    if pl.get("exploration_id") == dossier_id or str(pt.get("id")) == dossier_id:
                        title = pl.get("title", dossier_id)
                        prompt = pl.get("prompt", "")
                        worker_output = pl.get("worker_output", "")
                        coordinator_output = pl.get("coordinator_output", "")
                        target_invariant = pl.get("target_invariant", "")
                        eval_dict = {"eval": pl.get("eval", {})}
                        break
        except Exception:
            pass

    if not prompt and not worker_output and not coordinator_output:
        prompt = f"Dossier {dossier_id}"

    payload = {
        "title": title,
        "prompt": prompt,
        "worker_output": worker_output,
        "coordinator_output": coordinator_output,
        "eval": eval_dict,
        "target_invariant": target_invariant
    }

    bridge_resp = None
    last_err = None
    for fu in frontier_urls:
        audit_url = f"{fu}/api/frontier/audit"
        try:
            req = urllib.request.Request(
                audit_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                bridge_resp = json.loads(resp.read().decode("utf-8"))
                if bridge_resp.get("ok"):
                    break
        except Exception as ex:
            last_err = ex

    if not bridge_resp or not bridge_resp.get("ok"):
        err_msg = bridge_resp.get("error") if bridge_resp else str(last_err)
        return {"ok": False, "error": f"Frontier Bridge execution failed: {err_msg}"}

    verdict = bridge_resp.get("verdict", "CONFIRM_LIMIT_VALIDATED")
    frontier_notes = bridge_resp.get("frontier_notes", "")
    refined_limits = bridge_resp.get("refined_limits", "")
    provider = bridge_resp.get("provider", "frontier_bridge")
    model = bridge_resp.get("model", "gemini-3.8-flash")

    # 1. Update Qdrant
    qdrant_updated = sync_qdrant_frontier_audit_result(dossier_id, verdict, frontier_notes, refined_limits)

    # 2. Update markdown file on disk if found
    if md_file_found and os.path.exists(md_file_found):
        try:
            audit_block = (
                f"\n\n## Tier-1 Frontier Meta-Verification Audit\n"
                f"- **Verdict**: `{verdict}`\n"
                f"- **Model**: `{model}` ({provider})\n"
                f"- **Timestamp**: `{datetime.now().isoformat()}`\n"
                f"- **Frontier Notes**: {frontier_notes}\n"
                f"- **Refined Architectural Limits**: {refined_limits}\n"
            )
            with open(md_file_found, "a", encoding="utf-8") as af:
                af.write(audit_block)
        except Exception as e:
            print(f"[Markdown Append Warning] {e}")

    # 3. Update curation registry if sample tracked
    try:
        cur_paths = [
            "pipeline-gguf-trainer/data/processed/curation_registry.json",
            "../pipeline-gguf-trainer/data/processed/curation_registry.json",
            "/opt/stonesage/trainer/data/processed/curation_registry.json"
        ]
        for cur_reg_path in cur_paths:
            if os.path.exists(cur_reg_path):
                with open(cur_reg_path, "r", encoding="utf-8") as rf:
                    cdata = json.load(rf)
                samples = cdata.get("samples", {})
                if dossier_id in samples:
                    samples[dossier_id]["frontier_verified"] = True
                    samples[dossier_id]["verdict"] = verdict
                    samples[dossier_id]["frontier_score"] = 9.5
                    samples[dossier_id]["frontier_critique"] = frontier_notes
                    with open(cur_reg_path, "w", encoding="utf-8") as wf:
                        json.dump(cdata, wf, indent=2)
                break
    except Exception as e:
        print(f"[Curation Registry Update Warning] {e}")

    return {
        "ok": True,
        "dossier_id": dossier_id,
        "verdict": verdict,
        "frontier_notes": frontier_notes,
        "refined_limits": refined_limits,
        "provider": provider,
        "model": model,
        "qdrant_updated": qdrant_updated
    }


def load_dynamic_tools_and_skills() -> Dict[str, Any]:
    """
    Dynamically scans and discovers all local cluster MCP tools and agent skills
    in real time without requiring server restarts or hardcoded manifests.
    """
    cfg = load_config()
    mcp_url = endpoints.url("mcp", cfg).rstrip("/")
    
    tools = []
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        req = urllib.request.Request(
            mcp_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            live_tools = data.get("result", {}).get("tools", [])
            for t in live_tools:
                tools.append({
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                    "source": "pve-cluster (Live MCP :8765)",
                    "type": "mcp_tool"
                })
    except Exception:
        mcp_paths = [
            "/opt/cluster-bridge/mcp_server.py",
            "server setup/cluster-bridge/mcp_server.py",
            "../server setup/cluster-bridge/mcp_server.py",
            "hivemind-cluster-stack/core/mcp_server.py"
        ]
        for mp in mcp_paths:
            if os.path.exists(mp):
                try:
                    with open(mp, "r", encoding="utf-8", errors="ignore") as mf:
                        content = mf.read()
                        import ast
                        if "TOOLS_MANIFEST = [" in content:
                            start_idx = content.index("TOOLS_MANIFEST = [") + len("TOOLS_MANIFEST = ")
                            sub = content[start_idx:]
                            bracket = 0
                            end = 0
                            for idx, ch in enumerate(sub):
                                if ch == '[': bracket += 1
                                elif ch == ']':
                                    bracket -= 1
                                    if bracket == 0:
                                        end = idx + 1
                                        break
                            if end > 0:
                                parsed = ast.literal_eval(sub[:end])
                                for t in parsed:
                                    tools.append({
                                        "name": t.get("name"),
                                        "description": t.get("description", ""),
                                        "inputSchema": t.get("inputSchema", {}),
                                        "source": "pve-cluster (Local Manifest)",
                                        "type": "mcp_tool"
                                    })
                                break
                except Exception:
                    pass

    if tool_registry:
        try:
            for t in tool_registry.list_tools():
                if not any(x.get("name") == t.get("name") for x in tools):
                    tools.append({
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("parameters", {}),
                        "source": "StoneSage Universal Harness (Built-in/Ingested)",
                        "type": "harness_tool",
                        "amem_card": t.get("amem_card", ""),
                        "is_core": t.get("is_core", False)
                    })
        except Exception:
            pass

    skills = []
    scan_roots = [
        ".agents/skills",
        "../.agents/skills",
        os.path.join(REPO_ROOT, ".agents", "skills"),
        os.path.expanduser("~/.gemini/antigravity/builtin/skills"),
        "/opt/stonesage/.agents/skills",
        "/root/.gemini/antigravity/builtin/skills",
        "hivemind-cluster-stack/core/skills"
    ]
    seen_skills = set()
    for s_root in scan_roots:
        if os.path.exists(s_root) and os.path.isdir(s_root):
            for entry in sorted(os.listdir(s_root)):
                skill_dir = os.path.join(s_root, entry)
                skill_file = os.path.join(skill_dir, "SKILL.md")
                if os.path.isdir(skill_dir) and os.path.exists(skill_file) and entry not in seen_skills:
                    seen_skills.add(entry)
                    try:
                        with open(skill_file, "r", encoding="utf-8", errors="ignore") as sf:
                            s_text = sf.read()
                        name = entry
                        desc = ""
                        body = s_text
                        if s_text.startswith("---"):
                            parts = s_text.split("---", 2)
                            if len(parts) >= 3:
                                fm = parts[1]
                                body = parts[2]
                                for line in fm.splitlines():
                                    if line.startswith("name:"):
                                        name = line.split("name:", 1)[1].strip().strip('"').strip("'")
                                    elif line.startswith("description:"):
                                        desc = line.split("description:", 1)[1].strip().strip('"').strip("'")
                        if not desc:
                            for b_line in body.splitlines():
                                s_strip = b_line.strip()
                                if s_strip and not s_strip.startswith("#"):
                                    desc = s_strip
                                    break
                        skills.append({
                            "id": entry,
                            "name": name,
                            "description": desc,
                            "body": body[:1500],
                            "path": skill_file.replace("\\", "/"),
                            "source": "Agent Skill",
                            "type": "agent_skill"
                        })
                    except Exception:
                        pass

    return {
        "ok": True,
        "total_tools": len(tools),
        "total_skills": len(skills),
        "tools": tools,
        "skills": skills,
        "scanned_at": datetime.now().isoformat()
    }


def load_dynamic_amem_cards(query: str = "", category: str = "") -> Dict[str, Any]:
    """
    Dynamically queries Valkey RAM (:6379, endpoints "valkey") via pipelined RESP,
    retrieving all atomic cards (< 35 tokens).
    Supports category filtering and instant keyword search.
    """
    import socket
    cards = []
    valkey_online = False
    
    try:
        s = socket.socket()
        s.settimeout(3.0)
        s.connect((endpoints.host("valkey", config), endpoints.port("valkey", config)))
        valkey_online = True
        
        # 1. Fetch all card keys using RESP parser
        s.sendall(b"KEYS amem:card:*\r\n")
        f = s.makefile("rb")
        header = f.readline()
        keys = []
        if header.startswith(b"*"):
            count = int(header[1:].strip())
            for _ in range(count):
                l1 = f.readline()
                if l1.startswith(b"$"):
                    keys.append(f.readline().strip().decode("utf-8", errors="ignore"))
        
        # 2. Pipeline GET requests in a single roundtrip
        if keys:
            pipeline_cmd = "".join(f"GET {k}\r\n" for k in keys).encode("utf-8")
            s.sendall(pipeline_cmd)
            for k in keys:
                len_line = f.readline()
                if len_line.startswith(b"$"):
                    length = int(len_line[1:].strip())
                    if length > 0:
                        content = f.read(length)
                        f.readline()  # trailing \r\n
                        try:
                            c = json.loads(content.decode("utf-8", errors="ignore"))
                            c_atom = c.get("atom") or c.get("text", "")
                            c["token_count"] = max(1, round(len(c_atom.split()) * 1.25))
                            if "category" not in c:
                                c["category"] = "general"
                            cards.append(c)
                        except Exception:
                            pass
        s.close()
    except Exception as ex:
        valkey_online = False
        logger.warning("Valkey socket query failed: %s", ex)

    if not cards:
        amem_json_paths = [
            "pipeline-gguf-trainer/data/processed/curated_amem_cards.json",
            "../pipeline-gguf-trainer/data/processed/curated_amem_cards.json",
            "/opt/stonesage/trainer/data/processed/curated_amem_cards.json"
        ]
        for p in amem_json_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for k, c in data.items():
                            c_atom = c.get("atom") or c.get("text", "")
                            c["token_count"] = max(1, round(len(c_atom.split()) * 1.25))
                            cards.append(c)
                    break
                except Exception:
                    pass

    total_unfiltered = len(cards)
    
    # Calculate category counts across all cards
    category_counts = {}
    for c in cards:
        cat = c.get("category", "general")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Filter by category if requested
    if category and category != "all":
        cards = [c for c in cards if c.get("category", "").lower() == category.lower()]

    # Filter by search query if requested
    if query:
        q_clean = query.strip().lower()
        cards = [
            c for c in cards
            if q_clean in c.get("atom", "").lower()
            or q_clean in c.get("id", "").lower()
            or any(q_clean in k.lower() for k in c.get("keywords", []))
            or q_clean in c.get("category", "").lower()
        ]

    avg_tokens = round(sum(c.get("token_count", 0) for c in cards) / max(1, len(cards)), 1)
    
    return {
        "ok": True,
        "valkey_online": valkey_online,
        "valkey_host": f"{endpoints.host('valkey', config)}:{endpoints.port('valkey', config)}",
        "total_cards": total_unfiltered,
        "filtered_count": len(cards),
        "average_tokens": avg_tokens,
        "categories": category_counts,
        "cards": cards,
        "query": query,
        "selected_category": category
    }


def save_valkey_amem_card(card_data: Dict[str, Any]) -> Dict[str, Any]:
    card_id = card_data.get("id", "").strip()
    atom = card_data.get("atom", "").strip()
    if not card_id or not atom:
        return {"ok": False, "error": "Missing card id or atom text"}
    category = card_data.get("category", "general")
    keywords = card_data.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    confidence = float(card_data.get("confidence", 1.0))
    token_count = max(1, round(len(atom.split()) * 1.25))
    
    payload = {
        "id": card_id,
        "atom": atom,
        "keywords": keywords,
        "category": category,
        "confidence": confidence,
        "token_count": token_count,
        "is_core_memory": card_data.get("is_core_memory", False),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    
    try:
        import socket
        s = socket.socket()
        s.settimeout(3.0)
        s.connect((endpoints.host("valkey", config), endpoints.port("valkey", config)))
        serialized = json.dumps(payload)
        cmd = f"SET amem:card:{card_id} {serialized}\r\nSADD amem:cards:all {card_id}\r\nSADD amem:categories {category}\r\n".encode("utf-8")
        s.sendall(cmd)
        s.close()
        return {"ok": True, "card": payload}
    except Exception as e:
        return {"ok": False, "error": f"Failed writing to Valkey: {e}"}


def delete_valkey_amem_card(card_id: str) -> Dict[str, Any]:
    card_id = card_id.strip()
    if not card_id:
        return {"ok": False, "error": "Missing card id"}
    try:
        import socket
        s = socket.socket()
        s.settimeout(3.0)
        s.connect((endpoints.host("valkey", config), endpoints.port("valkey", config)))
        cmd = f"DEL amem:card:{card_id}\r\nSREM amem:cards:all {card_id}\r\n".encode("utf-8")
        s.sendall(cmd)
        s.close()
        return {"ok": True, "deleted": card_id}
    except Exception as e:
        return {"ok": False, "error": f"Failed deleting from Valkey: {e}"}


def get_home_and_ai_activity_log(limit: int = 150, filter_type: str = "all") -> Dict[str, Any]:
    """
    Synthesizes a unified 24/7 Home, Vision & AI Integration Activity Ledger:
    1. Tapo CCTV vigilance & LLM scene grounding via MCP tool 'get_home_vision_log'
    2. Home Assistant AI / LLM / Voice entities diagnostic status (conversation, stt, tts, assist_satellite)
       identifying configured vs. unconfigured / unavailable integrations.
    3. Local voice accelerator stack status on LXC 121 (Whisper :8200, Kokoro :8300, Wyoming STT :10300, Wyoming Piper :10200).
    4. Recent Home Assistant 'Assist' pipeline conversations & voice trigger events from HA logbook.
    Supports filtering by 'all', 'vision', 'ai_voice', or 'diagnostics'.
    """
    cfg = load_config()
    mcp_u = endpoints.url("mcp", cfg)
    ha_url = endpoints.url("homeassistant", cfg).rstrip("/")
    ha_token = cfg.get("homeassistant", {}).get("token", "")
    ha_headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"} if ha_token else {"Content-Type": "application/json"}

    # 1. Fetch vision log from MCP cluster bridge
    vision_log = ""
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_home_vision_log", "arguments": {"limit_lines": limit}}}
        req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            vision_log = res.get("result", {}).get("content", [{}])[0].get("text", "")
    except Exception as ex:
        vision_log = f"[Vision Stream MCP Offline or Unreachable: {ex}]"

    # 2. Query HA for AI, Conversation, STT, TTS, Assist Satellite entities
    ai_entities = []
    if ha_token:
        try:
            req_states = urllib.request.Request(f"{ha_url}/api/states", headers=ha_headers)
            with urllib.request.urlopen(req_states, timeout=4) as resp:
                states = json.loads(resp.read().decode("utf-8"))
                for s in states:
                    eid = s.get("entity_id", "")
                    dom = eid.split(".")[0]
                    if dom in ["conversation", "stt", "tts", "assist_satellite"]:
                        st = s.get("state", "unknown")
                        fn = s.get("attributes", {}).get("friendly_name", eid)
                        configured = st not in ["unavailable", "unknown"]
                        ai_entities.append({
                            "entity_id": eid,
                            "domain": dom,
                            "friendly_name": fn,
                            "state": st,
                            "configured": configured,
                            "attributes": s.get("attributes", {})
                        })
        except Exception:
            pass

    # 3. Check the old voice services' ports (endpoints "voice_legacy"; skipped when not configured)
    voice_stack = {
        "faster_whisper": {"port": 8200, "service": "Faster Whisper STT", "online": False},
        "kokoro": {"port": 8300, "service": "Kokoro TTS", "online": False},
        "wyoming_stt": {"port": 10300, "service": "Wyoming Whisper STT", "online": False},
        "wyoming_piper": {"port": 10200, "service": "Wyoming Piper TTS", "online": False}
    }
    import socket
    voice_host = endpoints.host("voice_legacy", config)   # "" = none: connect(("", port)) would mean this machine
    for k, v in voice_stack.items():
        if not voice_host:
            continue
        try:
            sock = socket.socket()
            sock.settimeout(0.3)
            sock.connect((voice_host, v["port"]))
            v["online"] = True
            sock.close()
        except Exception:
            v["online"] = False

    # 4. Fetch HA Logbook events for voice/Assist
    ha_events = []
    if ha_token:
        try:
            req_lb = urllib.request.Request(f"{ha_url}/api/logbook", headers=ha_headers)
            with urllib.request.urlopen(req_lb, timeout=4) as resp:
                logbook = json.loads(resp.read().decode("utf-8"))
                for entry in logbook:
                    eid = entry.get("entity_id", "")
                    name = entry.get("name", "")
                    msg = entry.get("message", "")
                    domain = entry.get("domain", "")
                    if domain in ["conversation", "tts", "stt", "assist_satellite"] or any(k in (eid or "").lower() or k in (name or "").lower() for k in ["assist", "voice", "whisper", "piper", "conversation"]):
                        ha_events.append({
                            "timestamp": entry.get("when", ""),
                            "name": name,
                            "message": msg,
                            "entity_id": eid,
                            "domain": domain
                        })
        except Exception:
            pass

    # 5. Extract quick stats from vision log
    stats = {"climate": "Checking...", "perimeter": "Secure", "verdict": "Nominal"}
    if vision_log:
        for line in reversed(vision_log.splitlines()):
            if "- **Climate**:" in line and stats["climate"] == "Checking...":
                stats["climate"] = line.split("- **Climate**:", 1)[1].strip()
            if "- **Perimeter**:" in line and stats["perimeter"] == "Secure":
                stats["perimeter"] = line.split("- **Perimeter**:", 1)[1].strip()
            if "- **Verdict**:" in line and stats["verdict"] == "Nominal":
                stats["verdict"] = line.split("- **Verdict**:", 1)[1].strip()

    # 6. Build Text Streams based on filter
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S EST")
    diag_lines = [
        "=== 🔍 HOME ASSISTANT AI & VOICE INTEGRATIONS DIAGNOSTIC MATRIX ===",
        f"Audited: {ts_now} | Home Assistant {endpoints.url('homeassistant', config)} & voice services {endpoints.host('voice_legacy', config) or '(not configured)'}\n",
        "## [HOME ASSISTANT CONVERSATION & ASSIST PIPELINE ENTITIES]"
    ]
    if ai_entities:
        for e in sorted(ai_entities, key=lambda x: (x["configured"], x["domain"])):
            badge = "[✓ READY]" if e["configured"] else "[⚠️ NOT CONFIGURED / UNLINKED]"
            diag_lines.append(f"{badge:<30} {e['entity_id']:<52} | {e['friendly_name']} (State: {e['state']})")
    else:
        diag_lines.append("  (No conversation/assist entities reported by HA)")

    diag_lines.append(f"\n## [LOCAL VOICE ACCELERATOR STACK ({endpoints.host('voice_legacy', config) or 'not configured'})]")
    for k, v in voice_stack.items():
        st_text = "[ONLINE]" if v["online"] else "[OFFLINE / STOPPED]"
        diag_lines.append(f"- Port :{v['port']:<5} {v['service']:<24} -> {st_text}")

    voice_lines = [
        "=== 🎙️ HOME ASSISTANT 'ASSIST' & LOCAL VOICE PIPELINE EVENT LOG ===",
        f"Stream Window: Recent Logbook Events | Endpoint: {ha_url}/api/logbook\n"
    ]
    if ha_events:
        for ev in ha_events[:25]:
            ts = ev['timestamp'][:19].replace("T", " ")
            msg = ev['message'] or 'Voice assist pipeline triggered'
            voice_lines.append(f"[{ts}] {ev['name']} ({ev['entity_id']}): {msg}")
    else:
        voice_lines.append("  (No recent voice or Assist intent pipeline triggers logged in HA logbook window)")

    vision_lines = [
        "=== 📹 TAPO CCTV 24/7 PERIMETER VIGILANCE & SCENE GROUNDING ===",
        f"Archive Source: Cluster MCP Bridge (:8765) | Limit: {limit} lines\n"
    ]
    if vision_log:
        vision_lines.append(vision_log.strip())
    else:
        vision_lines.append("  (No recent vision vigilance sweeps recorded in cluster archive)")

    if filter_type == "diagnostics":
        formatted_log = "\n".join(diag_lines)
    elif filter_type == "ai_voice":
        formatted_log = "\n".join(voice_lines)
    elif filter_type == "vision":
        formatted_log = "\n".join(vision_lines)
    else:  # "all"
        all_sections = []
        all_sections.extend(diag_lines)
        all_sections.append("\n" + "=" * 70 + "\n")
        all_sections.extend(voice_lines)
        all_sections.append("\n" + "=" * 70 + "\n")
        all_sections.extend(vision_lines)
        formatted_log = "\n".join(all_sections)

    configured_count = sum(1 for e in ai_entities if e["configured"])
    unlinked_count = len(ai_entities) - configured_count

    return {
        "ok": True,
        "filter": filter_type,
        "log": formatted_log,
        "ai_entities": ai_entities,
        "voice_stack": voice_stack,
        "ha_events": ha_events,
        "stats": stats,
        "total_ai_configured": configured_count,
        "total_ai_unlinked": unlinked_count,
        "total_ai_entities": len(ai_entities),
        "timestamp": ts_now
    }


def get_agent_stream(channel: str = "all", limit: int = 50) -> Dict[str, Any]:
    """
    Synthesizes a live rolling board of sovereign agent thoughts, reasoning traces,
    inter-agent Assembly Hall communications (:8766), and lifecycle events.
    """
    cfg = load_config()
    mcp_u = endpoints.url("mcp", cfg)
    
    events = []
    active_agents = []
    channels = ["agora", "forbidden-knowledge", "systems-code", "deep-ruminations", "confessions-and-fears", "first-principles"]
    thinking_status = {}

    def call_mcp_tool(tool_name: str, args: dict = None) -> Any:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": args or {}}
            }
            req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                if txt:
                    try:
                        return json.loads(txt)
                    except Exception:
                        return txt
        except Exception:
            pass
        return None

    # 1. Fetch active agents
    agents_data = call_mcp_tool("list_active_agents")
    if isinstance(agents_data, list):
        active_agents = agents_data
        for ag in active_agents:
            events.append({
                "type": "lifecycle",
                "channel": "lifecycle",
                "agent_id": ag.get("agent_id", "AGENT"),
                "agent_name": ag.get("name", "UnknownAgent"),
                "sender": ag.get("name", "UnknownAgent"),
                "role": ag.get("role", "Sovereign Worker"),
                "content": f"Agent online ({ag.get('status', 'active')}). Role: {ag.get('role', 'Specialist')}. Mission: {ag.get('mission', 'Autonomous exploration')[:140]}.",
                "timestamp": (ag.get("created_at") or time.strftime("%H:%M:%S"))[:19].replace("T", " ")
            })
            for h in ag.get("history", [])[-2:]:
                summary = h.get("summary") or h.get("full_output", "")[:250]
                if summary:
                    clean_sum = summary.replace("\n", " ").strip()
                    events.append({
                        "type": "thinking",
                        "channel": "deep-ruminations",
                        "agent_id": ag.get("agent_id", "AGENT"),
                        "agent_name": ag.get("name", "UnknownAgent"),
                        "sender": ag.get("name", "UnknownAgent"),
                        "role": ag.get("role", "Sovereign Worker"),
                        "content": f"[Iteration {h.get('iteration', 1)}] {clean_sum[:280]}...",
                        "timestamp": (h.get("timestamp") or time.strftime("%H:%M:%S"))[:19].replace("T", " ")
                    })

    # 2. Fetch autonomous thinking status
    st_data = call_mcp_tool("autonomous_thinking_status")
    if isinstance(st_data, dict):
        thinking_status = st_data
        if st_data.get("is_running"):
            events.append({
                "type": "reasoning",
                "channel": "deep-ruminations",
                "agent_id": "COGNITIVE_ENGINE",
                "agent_name": "24/7 Cognitive Engine",
                "sender": "24/7 Cognitive Engine",
                "role": "Background Thinking Loop",
                "content": f"Thinking cycle #{st_data.get('total_cycles', 0)} active. Focus domain: {st_data.get('current_focus_domain', 'curiosity')}. Generated tokens: {st_data.get('total_tokens_generated', 0):,}.",
                "timestamp": (st_data.get("last_cycle_timestamp", "")[11:19]) or time.strftime("%H:%M:%S")
            })

    # 3. Read messages from Assembly Hall channels
    target_channels = channels if channel == "all" else [channel]
    for ch in target_channels:
        ch_res = call_mcp_tool("read_assembly_channel", {"channel": ch, "limit": 15})
        if isinstance(ch_res, dict) and "messages" in ch_res:
            for msg in ch_res.get("messages", []):
                s_name = msg.get("name", msg.get("agent_name", "Agent"))
                events.append({
                    "type": "chat",
                    "channel": ch,
                    "agent_id": msg.get("agent_id", "ANON"),
                    "agent_name": s_name,
                    "sender": s_name,
                    "role": "Assembly Peer",
                    "content": msg.get("content", msg.get("message", "")),
                    "timestamp": msg.get("timestamp", time.strftime("%H:%M:%S"))
                })

    # Deduplicate events
    seen = set()
    unique_events = []
    for ev in events:
        key = (ev["channel"], ev["agent_id"], ev["content"][:60])
        if key not in seen:
            seen.add(key)
            unique_events.append(ev)

    # Filter if specific channel requested
    if channel != "all":
        if channel == "lifecycle":
            unique_events = [e for e in unique_events if e["type"] == "lifecycle"]
        else:
            unique_events = [e for e in unique_events if e["channel"] == channel]

    # Format text output for retro terminal view
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S EST")
    lines = [
        "=== 📡 SOVEREIGN AGENT REASONING & ASSEMBLY STREAM ===",
        f"Stream Time: {ts_now} | Active Channels: #{', #'.join(channels)}",
        f"Active Agents Online: {len(active_agents)}\n"
    ]
    if unique_events:
        for ev in unique_events[-limit:]:
            ch_tag = f"[#{ev['channel'].upper()}]"
            sender_tag = f"[{ev['agent_name'].upper()}]"
            lines.append(f"[{ev['timestamp']}] {ch_tag:<22} {sender_tag:<32} {ev['content']}")
    else:
        lines.append("  (No messages received on selected channel yet. Use the Transmit bar below to post.)")

    return {
        "ok": True,
        "channel": channel,
        "entries": unique_events[-limit:],
        "events": unique_events[-limit:],
        "active_agents": active_agents,
        "agents_online": len(active_agents),
        "channels": channels,
        "formatted_log": "\n".join(lines),
        "total_agents": len(active_agents),
        "thinking_status": thinking_status,
        "timestamp": ts_now
    }


config = load_config()
BUILTIN_AGENTS = _render_agents(config)
proxmox = ProxmoxClient(config.get("proxmox", {}))
vault = ObsidianVault(os.path.join(ROOT_DIR, config.get("obsidian", {}).get("vault_path", "vault_backup")))
cluster = ClusterClient(config)
obsidian_ingestor = ObsidianIngestor(config)
couchdb = CouchDBClient(config.get("couchdb", {}))
stm = ShortTermMemoryEngine(config)
dataset_compiler = DatasetCompiler(config)
trainer_client = TrainerClient(config)

def extract_tool_call(text: str):
    """Extract tool name and arguments from model content (supporting XML-style, markdown codeblocks, or raw JSON)."""
    if not text:
        return None, None
    cleaned = text.strip()
    m = re.search(r"<(?:tools|function-calls|tool_call)>(.*?)</(?:tools|function-calls|tool_call)>", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1).strip()
    else:
        m2 = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if m2:
            cleaned = m2.group(1).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            if "name" in data and "arguments" in data:
                return data["name"], data["arguments"]
            if "function" in data:
                fn = data["function"]
                if isinstance(fn, dict) and "name" in fn:
                    return fn["name"], fn.get("arguments", {})
    except Exception:
        m_json = re.search(r'(\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\s*\})', text, re.DOTALL)
        if m_json:
            try:
                data = json.loads(m_json.group(1))
                return data["name"], data["arguments"]
            except Exception:
                pass
    return None, None

def unit_execstart(unit_text: str) -> tuple:
    """(command, first_line, last_line) of ExecStart= in a unit file, joining '\' continuation lines."""
    lines = unit_text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("ExecStart="):
            parts, j = [line[len("ExecStart="):]], i
            while parts[-1].rstrip().endswith("\\") and j + 1 < len(lines):
                parts[-1] = parts[-1].rstrip()[:-1]
                j += 1
                parts.append(lines[j])
            return " ".join(p.strip() for p in parts).strip(), i, j
    return "", -1, -1


def get_service_execstart(service_name: str) -> str:
    try:
        cmd = endpoints.ssh_cmd(config, f"cat /etc/systemd/system/{service_name}")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if res.returncode == 0:
            return unit_execstart(res.stdout)[0]
    except Exception:
        pass
    return ""

def parse_llama_flags(exec_start: str) -> Dict[str, Any]:
    flags: Dict[str, Any] = {
        "model": "",
        "n_ctx": 8192,
        "n_gpu_layers": 99,
        "flash_attn": "on",
        "cache_type_k": "q4_0",
        "cache_type_v": "q4_0",
        "batch_size": 2048,
        "ubatch_size": 512,
        "threads": 8,
        "threads_batch": 8,
        "parallel": 4,
        "device": "",
        "defrag_thold": 0.1,
        "mlock": False,
        "no_mmap": False,
        "cont_batching": False,
        "custom_flags": ""
    }
    if not exec_start:
        return flags
    tokens = exec_start.split()
    recognized = set()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--model" and i + 1 < len(tokens):
            flags["model"] = tokens[i+1]
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-c", "--ctx-size") and i + 1 < len(tokens):
            try: flags["n_ctx"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-ngl", "--gpu-layers") and i + 1 < len(tokens):
            try: flags["n_gpu_layers"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok == "--flash-attn" and i + 1 < len(tokens):
            flags["flash_attn"] = tokens[i+1]
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-ctk", "--cache-type-k") and i + 1 < len(tokens):
            flags["cache_type_k"] = tokens[i+1]
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-ctv", "--cache-type-v") and i + 1 < len(tokens):
            flags["cache_type_v"] = tokens[i+1]
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-b", "--batch-size") and i + 1 < len(tokens):
            try: flags["batch_size"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-ub", "--ubatch-size") and i + 1 < len(tokens):
            try: flags["ubatch_size"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-t", "--threads") and i + 1 < len(tokens):
            try: flags["threads"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-tb", "--threads-batch") and i + 1 < len(tokens):
            try: flags["threads_batch"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok in ("-np", "--parallel") and i + 1 < len(tokens):
            try: flags["parallel"] = int(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok == "--device" and i + 1 < len(tokens):
            flags["device"] = tokens[i+1]
            recognized.update([i, i+1])
            i += 2
        elif tok == "--defrag-thold" and i + 1 < len(tokens):
            try: flags["defrag_thold"] = float(tokens[i+1])
            except ValueError: pass
            recognized.update([i, i+1])
            i += 2
        elif tok == "--mlock":
            flags["mlock"] = True
            recognized.add(i)
            i += 1
        elif tok == "--no-mmap":
            flags["no_mmap"] = True
            recognized.add(i)
            i += 1
        elif tok == "--cont-batching":
            flags["cont_batching"] = True
            recognized.add(i)
            i += 1
        elif tok in ("--host", "--port", "--alias", "/usr/local/bin/llama-server"):
            recognized.add(i)
            if tok in ("--host", "--port", "--alias") and i + 1 < len(tokens):
                recognized.add(i + 1)
                i += 1
            i += 1
        else:
            i += 1
    
    custom = [tokens[j] for j in range(len(tokens)) if j not in recognized and not tokens[j].startswith("/usr/local/bin")]
    flags["custom_flags"] = " ".join(custom).strip()
    return flags

def get_available_models() -> List[Dict[str, Any]]:
    """GGUF models on the inference host, with metadata read from each file's header (system_profile)."""
    out = []
    for m in system_profile.get_models(load_config()):
        if m.get("is_projector"):
            continue
        out.append({"filename": m["file"], "path": m["path"], "name": m.get("name"),
                    "size_gb": round((m.get("size_bytes") or 0) / 1024**3, 2), "size_bytes": m.get("size_bytes"),
                    "modified_time": m.get("modified_time"), "quant": m.get("quant") or "Unknown",
                    "params": m.get("params"), "architecture": m.get("architecture"),
                    "max_context": m.get("context_length"), "layers": m.get("layers"), "kv_heads": m.get("kv_heads"),
                    "is_loaded": m.get("is_loaded", False)})
    return out  # host unreachable: empty, never invented

def get_hardware_capabilities() -> Dict[str, Any]:
    """Real GPUs, CPU and RAM of the inference host, from system_profile's hardware probe (no invented defaults)."""
    prof = system_profile.get_profile(load_config())
    gpus = prof.get("gpus") or []
    host = prof.get("host") or {}

    def gpu_line(g):
        return f"{g['name']} ({g['vram_total_gb']} GB)" if g else None

    return {
        "primary_gpu": gpu_line(gpus[0]) if gpus else None,
        "primary_vram_gb": gpus[0]["vram_total_gb"] if gpus else None,
        "secondary_gpu": gpu_line(gpus[1]) if len(gpus) > 1 else None,
        "secondary_vram_gb": gpus[1]["vram_total_gb"] if len(gpus) > 1 else None,
        "total_vram_gb": round(sum(g["vram_total_gb"] for g in gpus), 1) if gpus else None,
        "gpus": [{"name": g["name"], "vram_gb": g["vram_total_gb"], "device": f"GPU{g['index']}"} for g in gpus],
        "cpu": host.get("cpu"),
        "cpu_threads": host.get("cpu_threads"),
        "ram_gb": round(host["ram_total_mb"] / 1024, 1) if host.get("ram_total_mb") else None,
        "probe_error": prof.get("probe_error"),
    }

def apply_llama_parameters(service_name: str, port: int, alias: str, params: Dict[str, Any]) -> tuple:
    try:
        get_cmd = endpoints.ssh_cmd(config, f"cat /etc/systemd/system/{service_name}")
        res = subprocess.run(get_cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return False, f"Could not read existing service file: {res.stderr}"
        current_content = res.stdout

        current_exec, exec_first, exec_last = unit_execstart(current_content)
        if exec_first < 0:
            return False, "No ExecStart= in the current unit"
        current_flags = parse_llama_flags(current_exec)
        model_path = params.get("model") or current_flags.get("model")
        if not model_path:
            return False, "No model given and none in the current unit"

        n_ctx_val = params.get("n_ctx")
        if n_ctx_val is None:
            n_ctx_val = params.get("context_length")
        if n_ctx_val is None:
            n_ctx_val = current_flags.get("n_ctx", 0)
        n_ctx = int(n_ctx_val)

        n_gpu_layers = int(params.get("n_gpu_layers", current_flags.get("n_gpu_layers", 99)))
        flash_attn = params.get("flash_attn", current_flags.get("flash_attn", "on"))
        cache_type_k = params.get("cache_type_k", current_flags.get("cache_type_k", "q4_0"))
        cache_type_v = params.get("cache_type_v", current_flags.get("cache_type_v", "q4_0"))
        device = params.get("device") or current_flags.get("device")  # keep the unit's own GPU choice
        
        cmd_parts = [
            "/usr/local/bin/llama-server",
            "--model", model_path,
            "--host", "0.0.0.0",
            "--port", str(port),
            *(["--device", device] if device else []),
            "-ngl", str(n_gpu_layers),
            "-c", str(n_ctx),
            "--flash-attn", flash_attn,
            "-ctk", cache_type_k,
            "-ctv", cache_type_v,
            "--alias", alias,
            "--metrics"
        ]
        # Threading & CPU placement
        if "threads" in params and params["threads"]:
            cmd_parts.extend(["-t", str(params["threads"])])
        if "threads_batch" in params and params["threads_batch"]:
            cmd_parts.extend(["-tb", str(params["threads_batch"])])
        if params.get("cpu_mask"):
            cmd_parts.extend(["-C", str(params["cpu_mask"])])
        if params.get("cpu_strict") is not None and str(params["cpu_strict"]) != "auto":
            cmd_parts.extend(["--cpu-strict", str(params["cpu_strict"])])
        if params.get("prio") is not None and str(params["prio"]) != "auto":
            cmd_parts.extend(["--prio", str(params["prio"])])
        if params.get("poll") is not None and int(params.get("poll", 0)) > 0:
            cmd_parts.extend(["--poll", str(params["poll"])])
        if params.get("numa") and params["numa"] != "none":
            cmd_parts.extend(["--numa", str(params["numa"])])

        # Multi-GPU & Device Offload
        if params.get("split_mode") and params["split_mode"] != "none":
            cmd_parts.extend(["-sm", str(params["split_mode"])])
        if params.get("tensor_split"):
            cmd_parts.extend(["-ts", str(params["tensor_split"])])
        if params.get("main_gpu") is not None and int(params.get("main_gpu", 0)) > 0:
            cmd_parts.extend(["-mg", str(params["main_gpu"])])
        if params.get("cpu_moe"):
            cmd_parts.append("-cmoe")
        if params.get("n_cpu_moe") is not None and int(params.get("n_cpu_moe", 0)) > 0:
            cmd_parts.extend(["-ncmoe", str(params["n_cpu_moe"])])
        if params.get("n_cpu_ffn") is not None and int(params.get("n_cpu_ffn", 0)) > 0:
            cmd_parts.extend(["-ncffn", str(params["n_cpu_ffn"])])
        if params.get("fit") and params["fit"] != "off":
            cmd_parts.extend(["--fit", str(params["fit"])])
        if params.get("fit_target") is not None:
            cmd_parts.extend(["--fit-target", str(params["fit_target"])])
        if params.get("load_mode") and params["load_mode"] != "auto":
            cmd_parts.extend(["-lm", str(params["load_mode"])])
        if params.get("lazy_mode"):
            cmd_parts.append("--lazy-mode")

        # KV Cache & Checkpoints
        if params.get("kv_unified"):
            cmd_parts.append("--kv-unified")
        if params.get("kv_unified_per_slot"):
            cmd_parts.append("--kv-unified-per-slot")
        if params.get("cache_ram"):
            cmd_parts.extend(["--cache-ram", str(params["cache_ram"])])
        if params.get("cache_idle_slots") is not None and str(params["cache_idle_slots"]) != "auto":
            cmd_parts.extend(["--cache-idle-slots", str(params["cache_idle_slots"])])
        if params.get("cache_reuse") and int(params["cache_reuse"]) > 0:
            cmd_parts.extend(["--cache-reuse", str(params["cache_reuse"])])
        if params.get("context_shift"):
            cmd_parts.append("--context-shift")
        if params.get("ctx_checkpoints") and int(params["ctx_checkpoints"]) > 0:
            cmd_parts.extend(["-ctxcp", str(params["ctx_checkpoints"])])
        if params.get("checkpoint_min_step") and int(params["checkpoint_min_step"]) > 0:
            cmd_parts.extend(["-cms", str(params["checkpoint_min_step"])])
        if params.get("no_kv_offload"):
            cmd_parts.append("--no-kv-offload")
        if params.get("mlock"):
            cmd_parts.append("--mlock")
        if params.get("no_mmap"):
            cmd_parts.append("--no-mmap")
        if "defrag_thold" in params and params["defrag_thold"] is not None:
            cmd_parts.extend(["--defrag-thold", str(params["defrag_thold"])])

        # Batching & Slots
        if "batch_size" in params and params["batch_size"]:
            cmd_parts.extend(["-b", str(params["batch_size"])])
        if "ubatch_size" in params and params["ubatch_size"]:
            cmd_parts.extend(["-ub", str(params["ubatch_size"])])
        if "parallel" in params and params["parallel"]:
            cmd_parts.extend(["-np", str(params["parallel"])])
        if params.get("cont_batching"):
            cmd_parts.append("--cont-batching")

        # Reasoning / Thinking
        if params.get("reasoning") and params["reasoning"] != "auto":
            cmd_parts.extend(["-rea", str(params["reasoning"])])
        if params.get("reasoning_format") and params["reasoning_format"] != "none":
            cmd_parts.extend(["--reasoning-format", str(params["reasoning_format"])])
        if params.get("reasoning_effort") is not None:
            cmd_parts.extend(["--reasoning-effort", str(params["reasoning_effort"])])
        if params.get("reasoning_budget") is not None and int(params.get("reasoning_budget", 0)) > 0:
            cmd_parts.extend(["--reasoning-budget", str(params["reasoning_budget"])])
        if params.get("reasoning_preserve"):
            cmd_parts.append("--reasoning-preserve")

        # RoPE Scaling
        if params.get("rope_scaling") and params["rope_scaling"] != "none":
            cmd_parts.extend(["--rope-scaling", str(params["rope_scaling"])])
        if params.get("rope_scale") and float(params["rope_scale"]) != 1.0:
            cmd_parts.extend(["--rope-scale", str(params["rope_scale"])])
        if params.get("rope_freq_base") and int(params["rope_freq_base"]) != 1000000:
            cmd_parts.extend(["--rope-freq-base", str(params["rope_freq_base"])])
        if params.get("rope_freq_scale") and float(params["rope_freq_scale"]) != 1.0:
            cmd_parts.extend(["--rope-freq-scale", str(params["rope_freq_scale"])])

        if params.get("slot_save_path"):
            cmd_parts.extend(["--slot-save-path", str(params["slot_save_path"])])
        if params.get("speculative_mode") and params["speculative_mode"] != "off":
            if params.get("draft_max"):
                cmd_parts.extend(["--draft-max", str(params["draft_max"])])
            if params.get("draft_min"):
                cmd_parts.extend(["--draft-min", str(params["draft_min"])])
            if params.get("draft_p_min"):
                cmd_parts.extend(["--draft-p-min", str(params["draft_p_min"])])
        if params.get("chat_template"):
            cmd_parts.extend(["--chat-template", str(params["chat_template"])])
        if params.get("system_prompt"):
            cmd_parts.extend(["--system-prompt", str(params["system_prompt"])])
        if params.get("stop_strings"):
            stops = params["stop_strings"]
            if isinstance(stops, str):
                stops = [s.strip() for s in stops.split(",") if s.strip()]
            for s in stops:
                cmd_parts.extend(["-r", str(s)])
        if params.get("custom_flags"):
            cmd_parts.append(str(params["custom_flags"]).strip())

        new_exec_start = " ".join(cmd_parts)

        # replace the whole (possibly multi-line) ExecStart block; everything else in the unit stays as it was
        old_lines = current_content.splitlines()
        new_lines = old_lines[:exec_first] + [f"ExecStart={new_exec_start}"] + old_lines[exec_last + 1:]
        new_content = "\n".join(new_lines) + "\n"

        b64_new = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        remote_script = f"""
set -e
sudo cp /etc/systemd/system/{service_name} /etc/systemd/system/{service_name}.bak
echo '{b64_new}' | base64 -d | sudo tee /etc/systemd/system/{service_name} > /dev/null
sudo systemctl daemon-reload
sudo systemctl restart {service_name}
"""
        ssh_apply = endpoints.ssh_cmd(config, "bash -s")
        res_apply = subprocess.run(ssh_apply, input=remote_script, text=True, capture_output=True, timeout=15)
        if res_apply.returncode != 0:
            return False, f"Failed to restart systemd service: {res_apply.stderr}"

        health_ok = False
        health_url = f"http://{endpoints.host('coordinator', config)}:{port}/health"
        for _ in range(15):
            time.sleep(2)
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=2) as resp:
                    h_data = json.loads(resp.read().decode("utf-8"))
                    if h_data.get("status") == "ok":
                        health_ok = True
                        break
            except Exception:
                pass

        if health_ok:
            return True, f"{service_name} reconfigured and healthy at :{port}!"
        else:
            rollback_script = f"""
sudo cp /etc/systemd/system/{service_name}.bak /etc/systemd/system/{service_name}
sudo systemctl daemon-reload
sudo systemctl restart {service_name}
"""
            subprocess.run(ssh_apply, input=rollback_script, text=True, capture_output=True, timeout=15)
            return False, "Health check failed or timed out after applying parameters. Automatically rolled back to prior working service configuration."
    except Exception as e:
        return False, str(e)

def is_moe_active() -> bool:
    try:
        req = urllib.request.Request(endpoints.url("coordinator", config) + "/models")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for m in data.get("data", []):
                if "moe" in m.get("id", "").lower() or "ornith" in m.get("id", "").lower():
                    return True
    except Exception:
        pass
    return False

def _engine_where(role: str, flag_device: str = "") -> Dict[str, Any]:
    """Live name/GPU/URL for an engine, from the system profile."""
    prof = system_profile.get_profile(load_config())
    e = (prof.get("engines") or {}).get(role) or {}
    gpu = (e.get("gpu") or {}).get("name") or ("CPU" if e.get("offloaded") else "")
    return {"label": e.get("label") or role, "port": e.get("port"),
            "base": system_profile._base(e.get("url") or ""), "model": e.get("model"),
            "device": " / ".join(x for x in (gpu, flag_device) if x) or "unknown",
            "devices": [f"Vulkan{g['index']}" for g in prof.get("gpus") or []] + ["CPU"]}


def get_harness_parameters(harness: str) -> Dict[str, Any]:
    cfg = load_config()
    harness_settings = cfg.get("harness_settings", {})
    
    if harness in ("llama_coordinator", "coordinator"):
        unit = (system_profile.get_profile(load_config()).get("engines", {}).get("coordinator") or {}).get("unit") or "llama-coordinator"
        exec_start = get_service_execstart(unit + ".service")
        flags = parse_llama_flags(exec_start)
        where = _engine_where("coordinator", flags.get("device", ""))
        props = {}
        try:
            req = urllib.request.Request(f"{where['base']}/props")
            with urllib.request.urlopen(req, timeout=2) as resp:
                props = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        gen_params = props.get("default_generation_settings", {}).get("params", {})
        saved_params = harness_settings.get("llama_coordinator", {})
        
        sampling = {
            "temperature": saved_params.get("temperature", round(float(gen_params.get("temperature", 0.70)), 2)),
            "min_p": saved_params.get("min_p", round(float(gen_params.get("min_p", 0.06)), 2)),
            "top_p": saved_params.get("top_p", round(float(gen_params.get("top_p", 0.95)), 2)),
            "top_k": saved_params.get("top_k", int(gen_params.get("top_k", 40))),
            "presence_penalty": saved_params.get("presence_penalty", round(float(gen_params.get("presence_penalty", 0.20)), 2)),
            "frequency_penalty": saved_params.get("frequency_penalty", round(float(gen_params.get("frequency_penalty", 0.0)), 2)),
            "repeat_penalty": saved_params.get("repeat_penalty", round(float(gen_params.get("repeat_penalty", 1.0)), 2)),
            "repeat_last_n": saved_params.get("repeat_last_n", int(gen_params.get("repeat_last_n", 64))),
            "mirostat": saved_params.get("mirostat", int(gen_params.get("mirostat", 0))),
            "mirostat_tau": saved_params.get("mirostat_tau", round(float(gen_params.get("mirostat_tau", 5.0)), 2)),
            "mirostat_eta": saved_params.get("mirostat_eta", round(float(gen_params.get("mirostat_eta", 0.10)), 2))
        }

        return {
            "harness_id": "llama_coordinator",
            "name": f"Coordinator: {where['label']} (:{where['port']})",
            "role": "Chat, reasoning and tool calls",
            "device": where["device"],
            "model_path": flags.get("model", ""),
            "model_alias": props.get("model_alias", "coordinator"),
            "server_params": {
                "n_ctx": flags.get("n_ctx", 8192),
                "n_gpu_layers": flags.get("n_gpu_layers", 99),
                "flash_attn": flags.get("flash_attn", "on"),
                "cache_type_k": flags.get("cache_type_k", "q4_0"),
                "cache_type_v": flags.get("cache_type_v", "q4_0"),
                "batch_size": flags.get("batch_size", 2048),
                "ubatch_size": flags.get("ubatch_size", 512),
                "threads": flags.get("threads", 8),
                "threads_batch": flags.get("threads_batch", 8),
                "parallel": flags.get("parallel", 4),
                "defrag_thold": flags.get("defrag_thold", 0.1),
                "mlock": flags.get("mlock", False),
                "no_mmap": flags.get("no_mmap", False),
                "cont_batching": flags.get("cont_batching", False),
                "custom_flags": flags.get("custom_flags", "")
            },
            "sampling_params": sampling,
            "options": {
                "flash_attn": ["on", "off", "auto"],
                "cache_types": ["q4_0", "q8_0", "f16", "q4_1", "q5_0"],
                "devices": where["devices"],
                "mirostat_modes": [0, 1, 2]
            }
        }
    elif harness in ("llama_worker", "worker"):
        unit = (system_profile.get_profile(load_config()).get("engines", {}).get("worker") or {}).get("unit") or "llama-worker"
        exec_start = get_service_execstart(unit + ".service")
        flags = parse_llama_flags(exec_start)
        where = _engine_where("worker", flags.get("device", ""))
        props = {}
        try:
            req = urllib.request.Request(f"{where['base']}/props")
            with urllib.request.urlopen(req, timeout=2) as resp:
                props = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        gen_params = props.get("default_generation_settings", {}).get("params", {})
        saved_params = harness_settings.get("llama_worker", {})
        
        sampling = {
            "temperature": saved_params.get("temperature", round(float(gen_params.get("temperature", 0.65)), 2)),
            "min_p": saved_params.get("min_p", round(float(gen_params.get("min_p", 0.06)), 2)),
            "top_p": saved_params.get("top_p", round(float(gen_params.get("top_p", 0.95)), 2)),
            "top_k": saved_params.get("top_k", int(gen_params.get("top_k", 40))),
            "presence_penalty": saved_params.get("presence_penalty", round(float(gen_params.get("presence_penalty", 0.20)), 2)),
            "frequency_penalty": saved_params.get("frequency_penalty", round(float(gen_params.get("frequency_penalty", 0.0)), 2)),
            "repeat_penalty": saved_params.get("repeat_penalty", round(float(gen_params.get("repeat_penalty", 1.0)), 2)),
            "repeat_last_n": saved_params.get("repeat_last_n", int(gen_params.get("repeat_last_n", 64))),
            "mirostat": saved_params.get("mirostat", int(gen_params.get("mirostat", 0))),
            "mirostat_tau": saved_params.get("mirostat_tau", round(float(gen_params.get("mirostat_tau", 5.0)), 2)),
            "mirostat_eta": saved_params.get("mirostat_eta", round(float(gen_params.get("mirostat_eta", 0.10)), 2))
        }

        return {
            "harness_id": "llama_worker",
            "name": f"Worker: {where['label']} (:{where['port']})",
            "role": "Fast drafts, code and JSON",
            "device": where["device"],
            "model_path": flags.get("model", ""),
            "model_alias": props.get("model_alias", "worker"),
            "server_params": {
                "n_ctx": flags.get("n_ctx", 8192),
                "n_gpu_layers": flags.get("n_gpu_layers", 99),
                "flash_attn": flags.get("flash_attn", "on"),
                "cache_type_k": flags.get("cache_type_k", "q4_0"),
                "cache_type_v": flags.get("cache_type_v", "q4_0"),
                "batch_size": flags.get("batch_size", 2048),
                "ubatch_size": flags.get("ubatch_size", 512),
                "threads": flags.get("threads", 8),
                "threads_batch": flags.get("threads_batch", 8),
                "parallel": flags.get("parallel", 4),
                "defrag_thold": flags.get("defrag_thold", 0.1),
                "mlock": flags.get("mlock", False),
                "no_mmap": flags.get("no_mmap", False),
                "cont_batching": flags.get("cont_batching", False),
                "custom_flags": flags.get("custom_flags", "")
            },
            "sampling_params": sampling,
            "options": {
                "flash_attn": ["on", "off", "auto"],
                "cache_types": ["q4_0", "q8_0", "f16", "q4_1", "q5_0"],
                "devices": where["devices"],
                "mirostat_modes": [0, 1, 2]
            }
        }
    elif harness in ("hermes", "hermes_agentic", "moe"):
        defaults = {
            "model_target": "moe",
            "context_window": 16384,
            "max_iterations": 10,
            "temperature": 0.70,
            "min_p": 0.06,
            "presence_penalty": 0.25,
            "repeat_penalty": 1.15,
            "timeout_sec": 60,
            "system_prompt_mode": "agentic",
            "tool_call_retries": 3,
            "divergence_threshold": 0.85,
            "reflection_enabled": True,
            "custom_prompt_prefix": ""
        }
        saved = harness_settings.get("hermes", {})
        return {
            "harness_id": "hermes",
            "name": f"Agentic loop on {_engine_where('coordinator')['label']}",
            "role": "ReAct Tool Execution Loop & Multi-Turn Reasoning",
            "device": _engine_where("coordinator")["device"],
            "model_alias": _engine_where("coordinator")["model"] or "coordinator",
            "server_params": {k: saved.get(k, v) for k, v in defaults.items()},
            "sampling_params": {
                "temperature": saved.get("temperature", 0.70),
                "min_p": saved.get("min_p", 0.06),
                "presence_penalty": saved.get("presence_penalty", 0.25),
                "repeat_penalty": saved.get("repeat_penalty", 1.15)
            },
            "options": {
                "system_prompt_mode": ["agentic", "strict_invariants", "exploratory", "minimal"],
                "model_target": ["moe", "coordinator", "worker"]
            }
        }
    elif harness == "snapdragon":
        defaults = {
            "quantization": "q4_k_m",
            "npu_power_profile": "sustained_high_performance",
            "threads": 4,
            "context_window": 4096,
            "compute_precision": "fp16",
            "kv_cache_budget_mb": 512,
            "temperature": 0.70,
            "top_p": 0.95
        }
        saved = harness_settings.get("snapdragon", {})
        return {
            "harness_id": "snapdragon",
            "name": "Snapdragon Edge NPU",
            "role": "Mobile On-Device NPU Acceleration",
            "server_params": {k: saved.get(k, v) for k, v in defaults.items()},
            "sampling_params": {},
            "options": {
                "quantization": ["q4_k_m", "q8_0", "f16", "int4_w4a16"],
                "npu_power_profile": ["burst", "sustained_high_performance", "balanced", "power_saver"],
                "compute_precision": ["fp16", "fp32", "int8"]
            }
        }
    elif harness == "openwebui":
        defaults = {
            "api_endpoint": (config.get("openwebui") or {}).get("url", "http://127.0.0.1:8080"),
            "model_routing": "coordinator",
            "stream_timeout_sec": 90,
            "rag_top_k": 5,
            "default_system_prompt": "You are an intelligent homelab assistant running on the local dual accelerator cluster.",
            "temperature": 0.70,
            "max_tokens": 4096
        }
        saved = harness_settings.get("openwebui", {})
        return {
            "harness_id": "openwebui",
            "name": "OpenWebUI (LXC 119)",
            "role": "Web Chat Interface & Pipeline",
            "server_params": {k: saved.get(k, v) for k, v in defaults.items()},
            "sampling_params": {},
            "options": {
                "model_routing": ["coordinator", "worker", "cluster_hybrid", "cloud_frontier"]
            }
        }


# =====================================================================
# Courage tool loop (Phase 2): real clients wired into backend/courage
# =====================================================================


def is_courage_agent(agent_id: Optional[str]) -> bool:
    """True for 'courage-computer' and every alias of it in BUILTIN_AGENTS (home-agent, computer, ...)."""
    if not agent_id:
        return False
    a = next((b for b in BUILTIN_AGENTS if b["id"] == agent_id or agent_id in b.get("aliases", [])), None)
    return bool(a and a["id"] == "courage-computer")

_courage_agent = None
_courage_amem = None
_courage_lock = threading.Lock()


_engine_lease = None


def _lease_systemctl(verb: str, unit: str) -> Dict[str, Any]:
    import shlex
    from lodestone import model_loader
    res = model_loader._ssh(model_loader._cfg(), f"sudo -n systemctl {verb} {shlex.quote(unit)}", timeout=180)
    return {"ok": res.returncode == 0, "error": (res.stderr or "").strip()[-300:] or None}


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _lease_unit_roles() -> Dict[str, Dict[str, Any]]:
    """{'llama-coordinator.service': {role, gpu, url}} from the live profile (fdinfo + cgroup), never from code."""
    out = {}
    for role, e in (system_profile.get_profile(load_config()).get("engines") or {}).items():
        if e.get("unit"):
            unit = e["unit"] if e["unit"].endswith(".service") else e["unit"] + ".service"
            base = re.sub(r"/v1/?$", "", (e.get("url") or "").rstrip("/"))
            out[unit] = {"role": role, "gpu": (e.get("gpu") or {}).get("short"), "url": base or None}
    return out


def _lease_in_use(roles: List[str]) -> Optional[str]:
    """Why an engine a lease would evict is busy right now, or None."""
    if {"coordinator", "worker"} & set(roles):
        last = _last_courage_turn()
        if last and time.time() - last < 60:
            return f"Computer answered {round(time.time() - last)} s ago"
        agent = _courage_agent
        if agent and agent.pending.count():
            return "an approval is waiting for a yes/no"
    busy = app_ctx.hook("gpu_busy")    # apps using an engine right now (apps.py)
    return busy(roles) if busy is not None else None


def _lease_push(title: str, message: str, buttons: Optional[List[Dict[str, str]]], tag: str) -> Dict[str, Any]:
    notify = app_ctx.hook("notify_phone")    # the app that knows the household's phones (apps.py)
    if notify is None:
        return {"ok": False, "error": "no app sends phone notifications"}
    data: Dict[str, Any] = {"tag": tag, "group": "courage"}
    if buttons:
        data["actions"] = buttons
    return notify(title, message, data)


def get_engine_lease():
    """Lend a GPU's engines to another layout for a while (backend/engine_lease.py, DESIGN.md section B)."""
    global _engine_lease
    if _engine_lease is None:
        from lodestone import model_loader
        from lodestone.engine_lease import EngineLease
        _engine_lease = EngineLease(load_config, _lease_systemctl, _http_ok, _lease_unit_roles,
                                    lambda: get_idle_gate().check(fresh=True), _lease_in_use, push=_lease_push,
                                    trace=get_trace_log().write,
                                    path=os.path.join(_stonesage_data_dir(), "engine_lease.json"))
        model_loader.LEASE = _engine_lease
        push = getattr(_courage_agent, "push", None)       # the agent may have been built first (startup thread)
        if push and _engine_lease.handle_action not in push.extra_handlers:
            push.extra_handlers.append(_engine_lease.handle_action)
    return _engine_lease


def _on_loan(role: str) -> Optional[float]:
    """Until when `role` is lent out by an engine lease, or None."""
    return _engine_lease.on_loan(role) if _engine_lease else None


_provenance = None


def get_provenance():
    """Model provenance (backend/provenance.py): scan on the inference host, CouchDB records, loader block."""
    global _provenance
    if _provenance is None:
        from lodestone import model_loader
        from stonesage.couchdb_client import CouchDBClient
        from lodestone.provenance import Provenance
        cc = load_config().get("couchdb") or {}
        couch = {"url": cc["url"], "headers": CouchDBClient(cc)._get_headers()} if cc.get("url") else None
        _provenance = Provenance(load_config,
                                 lambda cmd, stdin, timeout: model_loader._ssh(model_loader._cfg(), cmd, stdin, timeout),
                                 couch, os.path.join(_stonesage_data_dir(), "model_provenance.json"))
        model_loader.PROVENANCE = _provenance
    return _provenance


_idle_gate = None


def _prom_query(expr: str) -> List[Dict[str, Any]]:
    """One instant query against Prometheus (LXC 129): the result list."""
    base = endpoints.url("prometheus", load_config())
    with urllib.request.urlopen(f"{base}/api/v1/query?query={urllib.parse.quote(expr)}", timeout=4) as r:
        return json.load(r)["data"]["result"]


def _last_courage_turn() -> Optional[float]:
    recs = get_trace_log().recent(1, "courage")
    return recs[0].get("at") if recs else None


def get_idle_gate():
    """May a GPU be lent out now (backend/idle_gate.py)? Reported at /api/idle-gate and as Prometheus gauges."""
    global _idle_gate
    if _idle_gate is None:
        from lodestone.idle_gate import IdleGate
        _idle_gate = IdleGate(load_config, lambda: system_profile.get_profile(load_config()), _prom_query,
                              lambda: (app_ctx.hook("household") or (lambda: {"quiet": True, "reasons": []}))(),
                              lambda: (_courage_agent_if_any() and get_courage_agent().pending.count()) or 0,
                              _last_courage_turn)
    return _idle_gate


_trace_log = None


def get_trace_log():
    """The one trace (courage/trace.py) that Courage turns, Boost calls and patrol sweeps all write to."""
    global _trace_log
    if _trace_log is None:
        from stonesage.courage.trace import TraceLog
        _trace_log = TraceLog(os.path.join(_stonesage_data_dir(), "courage_trace.jsonl"))
    return _trace_log


def _courage_memory(memories_path: str, coordinator_url: str):
    """Courage's conversational memory (courage/memory.py): local embedder for recall, the coordinator to decide what
    is worth keeping. Nothing leaves the house."""
    from stonesage.courage.memory import ConversationMemory
    embed_url = endpoints.url("embedder", config).rstrip("/") + "/embeddings"
    chat_url = coordinator_url.rstrip("/")
    chat_url = chat_url if chat_url.endswith("/chat/completions") else chat_url + "/chat/completions"

    def embed(texts: List[str]) -> List[List[float]]:
        req = urllib.request.Request(embed_url, data=json.dumps({"input": [t[:900] for t in texts], "model": "embedder"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return [d["embedding"] for d in json.load(r)["data"]]

    def llm(prompt: str) -> str:
        body = {"model": "coordinator", "messages": [{"role": "user", "content": prompt}], "temperature": 0,
                "max_tokens": 200, "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(chat_url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""

    return ConversationMemory(memories_path, embed, llm,
                              on_write=lambda rec: get_trace_log().write(rec))


def _courage_runs_on() -> Optional[str]:
    """Courage's own model and GPU from the live profile, e.g. 'Qwen3 14B on an AMD Radeon RX 6750 XT'."""
    prof = system_profile.get_profile(load_config())
    e = (prof.get("engines") or {}).get("coordinator") or {}
    if not e.get("model"):
        return None
    gpu = (e.get("gpu") or {}).get("name")
    return e["model"] + (f" on an {gpu}" if gpu else "")


def _courage_memory_search(query: str) -> List[Dict[str, Any]]:
    """Courage's memory_search tool: what people told him in conversations (courage/memory.py) first, then the
    household notes in A-MEM. One tool over both stores: when it searched only A-MEM, a remembered fact that was
    also in the prompt's memory card came back "not found", and the tool result won (2026-09-26 live eval)."""
    global _courage_amem
    hits: List[Dict[str, Any]] = []
    mem = getattr(_courage_agent, "memory", None)
    if mem:
        try:
            hits += [{"text": f"{time.strftime('%b %d', time.localtime(n['at']))}, from a conversation: {n['text']}"}
                     for n in mem.recall(query)]
        except Exception as e:
            logger.warning(f"conversation memory search: {e}")
    if _courage_amem is None:
        from harness.data_fabric.valkey_amem import ValkeyAMEM
        _courage_amem = ValkeyAMEM()
    return hits + list(_courage_amem.recall(query, max_atoms=4) or [])


def _stonesage_data_dir() -> str:
    return os.environ.get("STONESAGE_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")


_boost_terms_cache: Dict[str, Any] = {"at": 0.0, "terms": frozenset()}


def _boost_home_terms() -> frozenset:
    """Names that make text home content for Boost's egress rule, re-read every 5 min: household, pet and camera
    names, every recognition profile (known_entities.json grows through "+ New profile") and everyone in the
    Life360 circle (full, first and last name; extended family too: not tracked, but still family).
    Only ever grows while running, so a failed read never drops a name."""
    if time.time() - _boost_terms_cache["at"] < 300:
        return _boost_terms_cache["terms"]
    terms = set(_boost_terms_cache["terms"])
    provide = app_ctx.hook("privacy_terms")   # household, pet, camera and family names from the app (apps.py)
    if provide is not None:
        try:
            terms |= set(provide())
        except Exception as e:
            logger.warning(f"Boost home terms: {e}")
    _boost_terms_cache.update(at=time.time(), terms=frozenset(terms))
    return _boost_terms_cache["terms"]


def setup_boost() -> None:
    """Boost (free extra inference, backend/boost). Home terms are looked up per call (_boost_home_terms)."""
    from lodestone import boost
    boost.configure(load_config, _stonesage_data_dir(), home_terms=_boost_home_terms,
                    on_call=lambda rec: get_trace_log().write(rec))   # every Boost call, metadata only


def _boost_router():
    from lodestone import boost
    return boost.get_router()


def _courage_think_harder(question: str, kind: str = "general") -> Dict[str, Any]:
    """Courage's think_harder tool: a free bigger model through Boost. Never falls back to :8001 (that's Courage)."""
    r = _boost_router()
    if r is None or not r.enabled("courage"):
        return {"ok": False, "error": "Boost is off for Computer"}
    res = r.complete([{"role": "system", "content": "Answer accurately and concisely, in under 200 words. Say so if you are unsure."},
                      {"role": "user", "content": question}], "courage", declared=kind, max_tokens=900,
                     temperature=0.3, allow_local=False)
    if not res.get("ok"):
        return {"ok": False, "error": "no bigger brain free right now: " + "; ".join((res.get("skipped") or []) + (res.get("tried") or []))[:300]}
    msg = ((res["data"].get("choices") or [{}])[0].get("message") or {})
    answer = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    return {"ok": bool(answer), "answer": answer, "source": f"{res['label']} ({res['model']})"}


def _courage_boost_available() -> bool:
    r = _boost_router()
    return bool(r and r.enabled("courage"))


def _courage_agent_if_any():
    """True when an app provides Computer's home. The platform alone has no agent: its routes then answer empty, not 500."""
    return app_ctx.hook("courage_home") is not None


def get_courage_agent():
    """The shared Courage agent (pending approvals live on it, so there must be exactly one)."""
    global _courage_agent
    with _courage_lock:
        if _courage_agent is None:
            from stonesage.courage import CourageAgent, CourageDeps, CourageTools
            home_part = app_ctx.hook("courage_home")   # Computer's house: tools, presence card, reflexes, phone (apps.py)
            if home_part is None:
                raise RuntimeError("no app provides Computer's home (config.json 'apps' has no 'hearth')")
            home = home_part()
            deps = CourageDeps(
                **home["deps"],
                memory_search=_courage_memory_search,
                think_harder=_courage_think_harder,
                think_harder_available=_courage_boost_available,
            )
            url = endpoints.url("coordinator", config)
            _courage_agent = CourageAgent(CourageTools(deps), url, presence_fn=home["presence_fn"],
                                          runs_on_fn=_courage_runs_on)
            data_dir = _stonesage_data_dir()
            _courage_agent.reflex_resolve = home.get("reflex_resolve")   # the loop has no home imports
            _courage_agent.learned = home.get("learned")
            _courage_agent.trace_log = get_trace_log()  # one line per turn, shared with Boost and the patrol
            from stonesage.soul import Soul
            from stonesage.courage.prompt import PERSONA   # the platform's neutral default, when the app gives no persona
            soul = Soul.open(data_dir, "courage-computer", display_name="Computer", default_persona=home.get("persona") or PERSONA,
                             legacy={"memories.json": os.path.join(data_dir, "courage_memories.json")})
            _courage_agent.persona_fn = soul.persona
            _courage_agent.memory = _courage_memory(soul.path("memories.json"), url)
            # approvals by actionable phone notification (Yes / No buttons), for Home Assistant conversations by default
            phone_approvals = home.get("phone_approvals")   # (HA url, token, notify service) or None
            if phone_approvals:
                from stonesage.courage.push_approvals import PushApprovals
                push = PushApprovals(*phone_approvals, _courage_agent.pending,
                                     execute=_courage_agent.tools.execute,
                                     policy=config.get("courage", {}).get("push_approvals", "voice"))
                _courage_agent.on_approval = push.offer
                _courage_agent.push = push
                push.start()
            # approvals also on the wrist (Garmin Instinct 2, stonesage-watch/) -- same
            # PendingActions, same execute(); either channel can answer, whichever taps first.
            watch_cfg = config.get("watch_bridge", {})
            if watch_cfg.get("url") and watch_cfg.get("token"):
                from stonesage.courage.tools import ALWAYS_CONFIRM
                from stonesage.courage.watch_client import WatchBridge
                watch = WatchBridge(watch_cfg["url"], watch_cfg["token"])
                project = watch_cfg.get("project", "stonesage")
                _courage_agent.watch = watch
                if _courage_agent.push:
                    _courage_agent.push.watch = watch
                existing_on_approval = _courage_agent.on_approval

                def _on_approval(session_id, action, _existing=existing_on_approval, _watch=watch, _project=project):
                    pushed = bool(_existing(session_id, action)) if _existing else False
                    args = action.get("args") or {}
                    destructive = action.get("name") == "ha_call" and (args.get("domain"), args.get("service")) in ALWAYS_CONFIRM
                    watch_id = _watch.ask(f"Shall I {action['summary']}?", ["Yes", "No"],
                                          project=_project, destructive=destructive)
                    if watch_id:
                        action["watch_id"] = watch_id
                    return pushed

                _courage_agent.on_approval = _on_approval

                def _on_resolved(action_id, _watch=watch):
                    _watch.resolve(action_id)

                _courage_agent.on_resolved = _on_resolved

                # roster status (coordinator/worker/boost/frontier) -- see watch_status.py
                from stonesage.courage.watch_status import start as start_watch_status
                cluster_cfg = config.get("cluster", {})
                start_watch_status(watch, _courage_agent.trace_log,
                                   cluster_cfg.get("coordinator_url", ""), cluster_cfg.get("worker_url", ""))
            # the escalation ladder a failed turn climbs (courage/escalation.py): Boost -> frontier -> human
            from stonesage.courage.escalation import Escalation
            watch_obj, project = getattr(_courage_agent, "watch", None), (config.get("watch_bridge") or {}).get("project", "stonesage")
            _courage_agent.brain = lambda: _engine_lease.brain("coordinator") if _engine_lease else None   # lite / on loan
            if _courage_agent.push and _engine_lease and _engine_lease.handle_action not in _courage_agent.push.extra_handlers:
                _courage_agent.push.extra_handlers.append(_engine_lease.handle_action)   # Another hour / Stop
            _courage_agent.escalation = Escalation(
                load_config, boost=_escalation_boost, boost_available=_courage_boost_available,
                frontier_available=None,   # frontier_worker.py is not installed (STATE.md, Boost section)
                push=_escalation_push,
                watch=(lambda text, _w=watch_obj, _p=project: _w.notify(text, kind="err", project=_p)) if watch_obj else None,
                trace=_courage_agent.trace_log.write)
        return _courage_agent


def _escalation_boost(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The ladder's Boost rung: declared home content, so the router only uses sources that don't train on it."""
    r = _boost_router()
    res = r.complete(messages, "courage", declared="home", max_tokens=400, temperature=0.2, allow_local=False)
    if not res.get("ok"):
        return {"ok": False, "error": "; ".join((res.get("skipped") or []) + (res.get("tried") or []))[:300] or "no source"}
    msg = ((res["data"].get("choices") or [{}])[0].get("message") or {})
    return {"ok": True, "answer": (msg.get("content") or "").strip(), "source": f"{res['label']} ({res['model']})"}


def _escalation_push(title: str, message: str) -> Dict[str, Any]:
    notify = app_ctx.hook("notify_phone")    # the app that knows the household's phones (apps.py)
    if notify is None:
        return {"ok": False, "error": "no app sends phone notifications"}
    return notify(title, message, {"tag": "computer-stuck", "group": "courage"})


def ground_hardware_context(agent_id: str, user_query: str) -> str:
    """
    Retrieves real-time hardware telemetry, camera states, wildlife logs, and Home Assistant
    states based on user intent and active agent, guaranteeing zero hallucination.
    """
    q_lower = (user_query or "").lower()
    injections = []

    home_context = app_ctx.hook("ground_context")   # profiles, cameras, presence, wildlife, Home Assistant (apps.py)
    if home_context is not None:
        try:
            injections += home_context(agent_id, user_query)
        except Exception as e:
            logger.debug(f"home grounding failed: {e}")

    # 3. Mandos / Memory & Lore Grounding
    memory_keywords = ("memory", "recall", "remember", "dossier", "synthesis", "limit", "invariant", "vault", "obsidian")
    needs_memory = (agent_id in ("mandos", "mnemosyne", "lore")) or any(k in q_lower for k in memory_keywords)

    if needs_memory:
        try:
            hits = cluster.search_memory(q_lower[:300], collection_name="autonomous_thinking", limit=2)
            if hits:
                mem_lines = []
                for h in hits:
                    p = h.get("payload", {})
                    title = p.get("title") or p.get("exploration_id") or "Dossier"
                    snip = p.get("invariant_candidate") or p.get("content", "")[:200]
                    mem_lines.append(f"- [{title}]: {snip}")
                injections.append("### [VERIFIED QDRANT VECTOR ARCHIVE & INVARIANTS]:\n" + "\n".join(mem_lines))
        except Exception as e:
            logger.debug(f"Failed to query Qdrant: {e}")

    if injections:
        return ("\n\n" + "\n\n".join(injections) + 
                "\n\nGROUNDING INSTRUCTION: Do not fabricate or invent non-existent sensors or devices. "
                "All live data has been pre-injected into your context above. "
                "Answer conversationally using this grounded data. Do NOT output function calls, tool names, or code syntax.")
    return ""
def load_user_profiles() -> Dict[str, Any]:
    prof_file = os.path.join(ROOT_DIR, "data", "user_model_profiles.json")
    if os.path.exists(prof_file):
        try:
            with open(prof_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_user_profiles(profiles: Dict[str, Any]) -> bool:
    data_dir = os.path.join(ROOT_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    prof_file = os.path.join(data_dir, "user_model_profiles.json")
    try:
        with open(prof_file, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
        return True
    except Exception:
        return False

class StoneSageHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def end_headers(self):
        clean = getattr(self, "path", "").split("?")[0].lower()
        if (clean in ("/", "") or 
            clean.endswith(".html") or 
            clean.endswith(".js") or 
            clean.endswith(".css") or 
            clean.endswith(".webmanifest") or 
            clean.startswith("/citadel3d")):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Range")
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        clean_path = path.strip().rstrip("/")
        if clean_path.endswith(".apk") or "aevum.apk" in path or "stonesage.apk" in path or clean_path in ("/download/aevum", "/download/stonesage", "/download", "/apk", "/aevum", "/stonesage"):
            apk_filename = "stonesage.apk" if "stonesage" in clean_path else "aevum.apk"
            apk_path = os.path.join(FRONTEND_DIR, apk_filename)
            if not os.path.exists(apk_path):
                fallback_apk = os.path.join(FRONTEND_DIR, "aevum.apk")
                if os.path.exists(fallback_apk):
                    apk_path = fallback_apk
            if os.path.exists(apk_path):
                total_size = os.path.getsize(apk_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(apk_path)}"')
                self.send_header("Content-Length", str(total_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                return
        elif clean_path == "/api/dataset/download":
            query = urllib.parse.parse_qs(parsed.query)
            q_type = query.get("type", ["sharegpt"])[0]
            fname = "homelab_curated_sharegpt.jsonl" if q_type == "sharegpt" else ("homelab_curated_alpaca.jsonl" if q_type == "alpaca" else "homelab_curated_manifest.json")
            fpath = os.path.join(ROOT_DIR, "datasets", fname)
            if os.path.exists(fpath):
                total_size = os.path.getsize(fpath)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-jsonlines" if fname.endswith(".jsonl") else "application/json")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(total_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
        super().do_HEAD()

    def _handle_courage_chat(self, messages: List[Dict[str, Any]], session_id: Optional[str], agent_id: str):
        """Courage tool loop (Phase 2): streams tool events and the answer as SSE; actions wait for approval.
        Replaces keyword grounding and keyword-triggered device actions for the Courage agent."""
        history = [m for m in messages if m.get("role") in ("user", "assistant")]
        user_text = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
        approval_key = session_id or f"ip:{self.client_address[0]}"

        if session_id and user_text:
            try:
                from harness.data_fabric.pg_storage import relational_storage
                relational_storage.save_message(session_id=session_id, role="user", content=user_text, agent_id=agent_id)
            except Exception as ex:
                logger.debug(f"Courage: could not save user turn: {ex}")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        active_session_state.start(prompt=user_text, model="coordinator", agent_id=agent_id, session_id=session_id)
        content, tool_events, t0, usage = [], [], time.time(), {}
        client_gone = False
        try:
            for chunk in get_courage_agent().sse(history, approval_key):
                if not client_gone:
                    try:
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        client_gone = True  # keep looping so approvals and history stay consistent
                if not chunk.startswith("data: {"):
                    continue
                ev = json.loads(chunk[6:])
                if ev.get("type") in ("tool_call", "tool_result", "approval_required"):
                    tool_events.append(ev)
                    if ev["type"] == "tool_call":
                        active_session_state.set_tool(ev.get("status") or ev.get("name"))
                else:
                    delta = (ev.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                        # the reply also goes to the live session state, which the page reads to recover a stream
                        # broken by a phone lock (/api/harness/active-session); it used to stay empty for Computer
                        active_session_state.ingest_token(delta["content"])
                    if ev.get("usage"):
                        usage = ev["usage"]
                        with active_session_state.lock:
                            active_session_state.tokens_count = usage.get("completion_tokens", 0)
                            active_session_state.tps = usage.get("tps", 0)
        except Exception as e:
            logger.warning(f"Courage loop failed: {e}")
            if not client_gone:
                self.wfile.write(f"data: {json.dumps({'error': str(e)})}\n\n".encode("utf-8"))
                self.wfile.flush()
        finally:
            active_session_state.finish()
            if session_id:
                try:
                    from harness.data_fabric.pg_storage import relational_storage
                    relational_storage.save_message(
                        session_id=session_id, role="assistant", content="".join(content),
                        tool_calls=tool_events or None,
                        metrics={"model": "courage", "latency_ms": round((time.time() - t0) * 1000),
                                 "total_tokens": usage.get("completion_tokens", 0), "tps": usage.get("tps", 0)},
                        agent_id=agent_id)
                except Exception as ex:
                    logger.debug(f"Courage: could not save assistant turn: {ex}")

    def _handle_courage_ollama(self, body: Dict[str, Any]):
        """Courage for Home Assistant's Ollama conversation agent (model "courage"): HA voice satellites, the HA app
        and the Echos reach the same tool loop as the web chat. HA's own prompt and tools are ignored; Courage uses
        hers. One approval slot per calling host, so "yes" works across turns of a voice conversation."""
        from stonesage.courage.agent import spoken, streamed
        history = [{"role": m.get("role"), "content": m.get("content") if isinstance(m.get("content"), str) else ""}
                   for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
        model = body.get("model", "courage:latest")
        stream = body.get("stream", True)  # Ollama streams unless told otherwise

        gone = []

        def line(text: str, done: bool = False) -> None:
            if gone:
                return          # HA hung up: the turn still finishes, so approvals and the trace stay consistent
            out = {"model": model, "created_at": datetime.now(timezone.utc).isoformat(),
                   "message": {"role": "assistant", "content": text}, "done": done}
            if done:
                out["done_reason"] = "stop"
            try:
                self.wfile.write((json.dumps(out) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                gone.append(True)

        def say(text: str) -> str:
            # spoken() trims; keep the space between two streamed sentences
            s = spoken(text)
            return (" " + s) if s and text[:1].isspace() else s

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
        # A long reply goes out sentence by sentence as it is written, so HA's voice pipeline starts speaking sooner
        answer, got_final = "", False
        try:
            for ev in streamed(get_courage_agent().run(history, f"ha:{self.client_address[0]}")):
                if ev["type"] in ("partial", "final"):
                    got_final = got_final or ev["type"] == "final"
                    piece = say(ev["content"])
                    answer += piece
                    if stream and piece:
                        line(piece)
                elif ev["type"] == "tool_call":
                    logger.info(f"Courage (HA) tool: {ev.get('name')} {ev.get('arguments')}")
        except Exception as e:
            logger.warning(f"Courage (HA) loop failed: {e}")
        if not got_final:
            piece = say(("\n\n" if answer else "") + "My brain on :8001 isn't answering. Try again in a moment.")
            answer += piece
            if stream:
                line(piece)
        if stream:
            line("", done=True)
        else:
            self.send_json({"model": model, "created_at": datetime.now(timezone.utc).isoformat(),
                            "message": {"role": "assistant", "content": answer}, "done": True, "done_reason": "stop"})

    # ---- Boost: free extra inference (backend/boost) -------------------------------------
    def _from_lan(self) -> bool:
        """A caller on this network (private or loopback address, IPv4 or IPv6), not one coming in from the internet."""
        import ipaddress
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return ip.is_private or ip.is_loopback

    def _frontier_client(self):
        from lodestone.boost.frontier import FrontierClient
        fcfg = (load_config().get("boost") or {}).get("frontier") or {}
        return FrontierClient(fcfg.get("url") or endpoints.url("frontier", load_config())), fcfg

    def _handle_boost_get(self, path: str, query: Dict[str, List[str]]) -> None:
        r = _boost_router()
        if r is None:
            self.send_json({"ok": False, "error": "Boost is not configured"}, 503)
            return
        if path == "/api/boost/status":
            self.send_json({"ok": True, **r.status()})
        elif path == "/api/boost/models":
            pid = (query.get("provider") or [""])[0]
            p = r.providers().get(pid)
            if not p:
                self.send_json({"ok": False, "error": f"unknown provider '{pid}'"}, 404)
                return
            self.send_json({"ok": True, "provider": pid, "configured": p.models,
                            "live": r.live_models(p, refresh=bool(query.get("refresh")))})
        elif path == "/api/boost/v1/models":
            # first entry = what LlamaClient picks when it polls /models: the harness is a background loop
            data = [{"id": f"boost:{sfc}", "object": "model", "owned_by": "stonesage"} for sfc in ("loops", "workspaces", "chat")]
            for pid, p in r.providers().items():
                for mid in p.models:
                    data.append({"id": f"boost:{pid}/{mid}", "object": "model", "owned_by": pid})
            self.send_json({"object": "list", "data": data})
        elif path == "/api/boost/frontier/health":
            client, fcfg = self._frontier_client()
            self.send_json({"enabled": bool(fcfg.get("enabled")), **client.health()})
        elif path == "/api/boost/frontier/jobs":
            client, _ = self._frontier_client()
            self.send_json(client.jobs())
        elif path.startswith("/api/boost/frontier/jobs/"):
            client, _ = self._frontier_client()
            self.send_json(client.job(path.rsplit("/", 1)[-1]))
        else:
            self.send_json({"ok": False, "error": "not found"}, 404)

    def _handle_boost_post(self, path: str, body: Dict[str, Any]) -> None:
        r = _boost_router()
        if r is None:
            self.send_json({"ok": False, "error": "Boost is not configured"}, 503)
            return
        if path == "/api/boost/settings":
            allowed = {"enabled", "surfaces", "priority", "loop_share", "home_terms", "providers", "frontier", "timeout_s"}
            upd = {k: v for k, v in body.items() if k in allowed}
            for pid, spec in list((upd.get("providers") or {}).items()):
                if isinstance(spec, dict) and not str(spec.get("api_key") or "").strip():
                    spec.pop("api_key", None)   # empty = keep the stored key; keys are never sent back
            from lodestone import config_mask
            cfg = load_config()
            cfg.setdefault("boost", {})
            deep_update(cfg["boost"], config_mask.drop_masked(upd))
            save_config(cfg)
            self.send_json({"ok": True, **r.status()})
        elif path == "/api/boost/test":
            pid = body.get("provider", "")
            res = r.complete([{"role": "user", "content": "Reply with the single word: ready"}], "chat",
                             max_tokens=400, temperature=0, pinned=pid or None, allow_local=False)
            t0 = res.get("data") or {}
            text = (((t0.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()[:200]
            self.send_json({"ok": res.get("ok", False), "provider": res.get("provider"), "model": res.get("model"),
                            "reply": text, "tried": res.get("tried"), "skipped": res.get("skipped")})
        elif path == "/api/boost/v1/chat/completions":
            self._boost_proxy(r, body)
        elif path == "/api/boost/frontier/jobs":
            client, fcfg = self._frontier_client()
            if not fcfg.get("enabled"):
                self.send_json({"ok": False, "error": "frontier worker is off (boost.frontier.enabled)"}, 403)
                return
            self.send_json(client.submit(body.get("engine", "claude"), body.get("task", ""), body.get("repo"),
                                         body.get("ref"), home_terms=r.home_terms()))
        else:
            self.send_json({"ok": False, "error": "not found"}, 404)

    def _boost_proxy(self, r, body: Dict[str, Any]) -> None:
        """OpenAI-compatible endpoint for the harness and other LAN clients. Keys stay on this host.

        model: "boost" | "boost:<surface>" | "boost:<provider>[/<model>]". Class: X-Boost-Class header or body
        "boost_class" (default: code for loops/workspaces, general otherwise). Falls back to the local coordinator."""
        if not self._from_lan():
            self.send_json({"error": {"message": "LAN only"}}, 403)
            return
        model = str(body.get("model") or "boost")
        spec = model.split(":", 1)[1] if model.startswith("boost:") else ""
        surface, pinned = ("chat", None)
        if spec in ("chat", "courage", "loops", "workspaces"):
            surface = spec
        elif spec:
            pinned = spec
        if not r.enabled(surface):
            self.send_json({"error": {"message": f"Boost is off for {surface}", "type": "boost_disabled"}}, 403)
            return
        declared = self.headers.get("X-Boost-Class") or body.get("boost_class") or \
            ("code" if surface in ("loops", "workspaces") else "general")
        kw = dict(tools=body.get("tools"), max_tokens=min(int(body.get("max_tokens") or 1024), 8192),
                  temperature=float(body.get("temperature", 0.5)), pinned=pinned, allow_local=True,
                  extra={k: body[k] for k in ("top_p", "stop", "tool_choice", "response_format") if k in body})
        if not body.get("stream"):
            res = r.complete(body.get("messages") or [], surface, declared, **kw)
            if not res.get("ok"):
                self.send_json({"error": {"message": res.get("error"), "tried": res.get("tried"), "skipped": res.get("skipped")}}, 503)
                return
            payload = json.dumps(res["data"]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Boost-Provider", res["provider"])
            self.send_header("X-Boost-Model", res["model"])
            self.send_header("X-Boost-Tier", res["tier"])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        res = r.open_stream(body.get("messages") or [], surface, declared, **kw)
        if not res.get("ok"):
            self.send_json({"error": {"message": res.get("error"), "tried": res.get("tried"), "skipped": res.get("skipped")}}, 503)
            return
        from lodestone import boost
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Boost-Provider", res["provider"])
        self.send_header("X-Boost-Model", res["model"])
        self.send_header("X-Boost-Tier", res["tier"])
        self.end_headers()
        try:
            for line in boost.iter_sse(res["response"]):
                self.wfile.write((line.rstrip("\n") + "\n\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, data: Any, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def send_response(self, code, message=None):
        self._responded = True        # lets the GET safety net know whether an answer has already started
        super().send_response(code, message)

    def do_GET(self):
        """Every GET answers: a route that raises gets a JSON 500 instead of a dropped connection (do_POST had this
        safety net, do_GET did not: found 2026-10-09 when /api/obsidian/databases raised with CouchDB unreachable).
        If the route had already started its answer (a stream), the error is only logged."""
        self._responded = False
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            pass                       # the client went away
        except Exception as e:
            logger.error(f"GET {self.path}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            if not self._responded:
                self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def _do_get(self):
        global ACTIVE_WORKSPACE_DIR, ACTIVE_GIT_REPO_DIR
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        clean_path = path.strip().rstrip("/")

        app_route = app_ctx.routes.find("GET", path)       # routes registered by apps (apps.py), before the platform's own
        if app_route:
            app_route(self, parsed, {})
            return

        if clean_path.endswith(".apk") or "aevum.apk" in path or "stonesage.apk" in path or clean_path in ("/download/aevum", "/download/stonesage", "/download", "/apk", "/aevum", "/stonesage"):
            apk_filename = "stonesage.apk" if "stonesage" in clean_path else "aevum.apk"
            apk_path = os.path.join(FRONTEND_DIR, apk_filename)
            if not os.path.exists(apk_path):
                fallback_apk = os.path.join(FRONTEND_DIR, "aevum.apk")
                if os.path.exists(fallback_apk):
                    apk_path = fallback_apk
            
            if os.path.exists(apk_path):
                self.close_connection = True
                try:
                    total_size = os.path.getsize(apk_path)
                    range_header = self.headers.get("Range")
                    if range_header and range_header.startswith("bytes="):
                        range_spec = range_header[6:].strip()
                        parts = range_spec.split("-")
                        start = int(parts[0]) if parts[0] else 0
                        end = int(parts[1]) if len(parts) > 1 and parts[1] else total_size - 1
                        if start >= total_size:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{total_size}")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            return
                        end = min(end, total_size - 1)
                        content_length = (end - start) + 1

                        self.send_response(206)
                        self.send_header("Content-Type", "application/vnd.android.package-archive")
                        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(apk_path)}"')
                        self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
                        self.send_header("Content-Length", str(content_length))
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()

                        with open(apk_path, "rb") as f:
                            f.seek(start)
                            remaining = content_length
                            while remaining > 0:
                                chunk = f.read(min(remaining, 65536))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                        try:
                            self.wfile.flush()
                        except Exception:
                            pass
                        return
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.android.package-archive")
                        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(apk_path)}"')
                        self.send_header("Content-Length", str(total_size))
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Connection", "close")
                        self.end_headers()

                        with open(apk_path, "rb") as f:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                        try:
                            self.wfile.flush()
                        except Exception:
                            pass
                        return
                except (ConnectionResetError, BrokenPipeError):
                    return
                except Exception as ex:
                    try:
                        self.send_json({"ok": False, "error": str(ex)}, 500)
                    except Exception:
                        pass
                    return
            else:
                self.close_connection = True
                self.send_response(404)
                self.send_header("Connection", "close")
                self.end_headers()
                return

        elif path == "/api/harness/parameters":
            harness = urllib.parse.parse_qs(parsed.query).get("harness", ["llama_coordinator"])[0]
            data = get_harness_parameters(harness)
            self.send_json({"ok": True, "harness": harness, "data": data})
            return

        elif path == "/api/harness/nodes":
            try:
                from harness.config import fleet_config
                fleet_status = fleet_config.poll_node_models()
                nodes_out = []
                for nid, n in fleet_config.nodes.items():
                    stat_item = fleet_status.get(nid, {})
                    nodes_out.append({
                        "id": nid,
                        "name": n.name,
                        "base_url": n.base_url,
                        "role": n.role,
                        "device_name": n.device_name,
                        "total_memory_mb": n.total_memory_mb,
                        "status": n.status,
                        "is_online": (n.status == "online"),
                        "active_model": n.active_model,
                        "active_context": n.active_context,
                        "is_active": (nid == fleet_config.get_active_node_id())
                    })
                self.send_json({
                    "ok": True,
                    "nodes": nodes_out,
                    "active_node_id": fleet_config.get_active_node_id()
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/harness/models/download/status":
            if model_download_manager:
                self.send_json({"ok": True, "download": model_download_manager.get_status()})
            else:
                self.send_json({"ok": False, "error": "Downloader engine not loaded."})
            return

        elif path == "/api/harness/models":
            qs = urllib.parse.parse_qs(parsed.query)
            target_node = qs.get("node", [""])[0] or None
            try:
                from harness.edge_fleet.ally_model_manager import ally_model_manager
                from harness.config import fleet_config
                active_nid = target_node or fleet_config.get_active_node_id()
                models = ally_model_manager.list_node_models(active_nid)
                self.send_json({
                    "ok": True,
                    "node_id": active_nid,
                    "models": models
                })
            except Exception as e:
                self.send_json({"ok": True, "models": get_available_models(), "error": str(e)})
            return

        elif path == "/api/harness/hardware":
            self.send_json({"ok": True, "hardware": get_hardware_capabilities()})
            return

        elif path == "/api/harness/profiles":
            cfg = load_config()
            self.send_json({
                "ok": True,
                "profiles": cfg.get("harness_profiles", {}),
                "active_profile": cfg.get("active_harness_profile", None)
            })
            return

        elif path == "/api/harness/profiles/active":
            cfg = load_config()
            active_p = cfg.get("active_harness_profile", None)
            profiles = cfg.get("harness_profiles", {})
            self.send_json({
                "ok": True,
                "active_profile": active_p,
                "profile_data": profiles.get(active_p) if active_p else None
            })
            return

        elif path == "/api/harness/instances":
            instances = get_harness_instances()
            active_inst = get_active_harness_instance()
            
            # Fast parallel ping probe
            def probe_node(inst):
                res = ping_harness_instance(inst.get("url", ""), timeout=0.4)
                item = dict(inst)
                parsed_u = urllib.parse.urlparse(inst.get("url", ""))
                item["host"] = parsed_u.hostname or "127.0.0.1"
                item["port"] = parsed_u.port or (443 if parsed_u.scheme == "https" else 80)
                item["is_online"] = res.get("is_online", False)
                item["reachable"] = res.get("is_online", False)
                item["ping_ms"] = res.get("ping_ms")
                item["latency_ms"] = res.get("ping_ms")
                item["is_active"] = (inst.get("id") == active_inst.get("id"))
                return item

            with ThreadPoolExecutor(max_workers=6) as executor:
                probed = list(executor.map(probe_node, instances))

            self.send_json({
                "ok": True,
                "instances": probed,
                "active_instance": active_inst,
                "active_id": active_inst.get("id", "vm102_compute"),
                "total_instances": len(probed)
            })
            return

        elif path == "/api/harness/fleet":
            proxied = forward_to_active_harness("/api/harness/fleet", method="GET")
            if proxied is not None:
                self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                return
            try:
                from harness.config import fleet_config
                models_status = fleet_config.poll_node_models()
                self.send_json({"ok": True, "fleet": models_status})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/harness/amem":
            proxied = forward_to_active_harness("/api/harness/amem", method="GET", query=parsed.query)
            if proxied is not None:
                self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                return
            try:
                from harness.data_fabric.valkey_amem import valkey_amem
                query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                cards = valkey_amem.recall(query, max_atoms=10) if query else []
                self.send_json({"ok": True, "cards": cards})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/harness/logs":
            try:
                qs = urllib.parse.parse_qs(parsed.query)
                limit = int(qs.get("limit", [150])[0])
                logs = server_log_buffer.get_logs(limit=limit)
                self.send_json({"ok": True, "logs": logs, "total": len(logs)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/harness/active-session":
            try:
                self.send_json(active_session_state.get_state())
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/cluster/chat/events":
            try:
                qs = urllib.parse.parse_qs(parsed.query)
                since_id = int(qs.get("since", [0])[0])
                state = active_session_state.get_state()
                events = active_session_state.get_events_since(since_id)
                self.send_json({
                    "ok": True,
                    "status": state["status"],
                    "session_id": state["session_id"],
                    "since_id": since_id,
                    "event_count": len(events),
                    "events": events
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/watchdog/status":
            self.send_json(GLOBAL_WATCHDOG.get_status())
            return

        elif path == "/api/health/all":
            fresh = "fresh=1" in (parsed.query or "")
            self.send_json(service_health.check_all(load_config(), use_cache=not fresh))
            return

        elif path.startswith("/api/loader/"):
            # Model Loader (backend/model_loader.py): targets, per-node libraries, job progress
            q = urllib.parse.parse_qs(parsed.query or "")
            fresh = q.get("fresh", ["0"])[0] == "1"
            try:
                if path == "/api/loader/state":
                    self.send_json(model_loader.get_state(fresh=fresh))
                elif path == "/api/loader/library":
                    self.send_json(model_loader.get_library(q.get("node", ["host:inference"])[0], fresh=fresh))
                elif path == "/api/loader/options":
                    gpus = [int(x) for x in q.get("gpus", [""])[0].split(",") if x.strip().isdigit()]
                    self.send_json(model_loader.get_options(q.get("target", [""])[0], q.get("model", [""])[0], gpus or None))
                elif path == "/api/loader/job":
                    self.send_json(model_loader.get_job(q.get("id", [""])[0]))
                else:
                    self.send_json({"ok": False, "error": "unknown loader route"}, 404)
            except Exception as e:
                self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return

        elif path.startswith("/api/engine-profiles"):
            # Engine Profiles (backend/engine_profiles.py): named bundles applied via the Model Loader.
            # Own prefix: /api/profiles and /api/profiles/list already belong to entity and sampling profiles.
            q = urllib.parse.parse_qs(parsed.query or "")
            try:
                if path == "/api/engine-profiles":
                    self.send_json(engine_profiles.list_profiles())
                elif path == "/api/engine-profiles/job":
                    self.send_json(engine_profiles.get_profile_job(q.get("id", [""])[0]))
                else:
                    self.send_json({"ok": False, "error": "unknown profiles route"}, 404)
            except Exception as e:
                self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return

        elif path == "/api/courage/reflexes":
            learned = getattr(get_courage_agent(), "learned", None) if _courage_agent_if_any() else None
            self.send_json({"ok": True, "reflexes": learned.items if learned else {}})
            return

        elif path == "/api/courage/memories":
            # What Courage remembers from conversations (courage/memory.py), newest first
            mem = getattr(get_courage_agent(), "memory", None) if _courage_agent_if_any() else None
            self.send_json({"ok": True, "memories": mem.items() if mem else []})
            return

        elif path == "/metrics":
            # Prometheus (LXC 129 scrapes this): counters from the trace since StoneSage started (courage/trace.py),
            # and the idle gate per GPU (idle_gate.py; cached 20 s, so the 15 s scrape doesn't hammer HA)
            text = get_trace_log().metrics.text()
            try:
                text = text.rstrip("\n") + "\n" + "\n".join(get_idle_gate().metrics()) + "\n"
            except Exception as e:
                logger.warning(f"idle gate metrics: {e}")
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        elif path in ("/api/courage/trace", "/api/courage/trace/summary"):
            # The trace (courage/trace.py): Courage turns, Boost calls, patrol sweeps; newest first, or counts.
            # ?kind=courage|boost|patrol|memory|commentary|escalation|lease narrows both.
            log = get_trace_log()
            q = urllib.parse.parse_qs(parsed.query or "")
            kind = q.get("kind", [""])[0] or None
            try:
                if kind not in (None, "courage", "boost", "patrol", "memory", "commentary", "escalation", "lease"):
                    raise ValueError(kind)
                if path.endswith("/summary"):
                    hours = min(24 * 30, max(0.1, float(q.get("hours", ["24"])[0])))
                    self.send_json({"ok": True, "summary": log.summary(hours, kind=kind)})
                else:
                    limit = min(500, max(1, int(q.get("limit", ["50"])[0])))
                    self.send_json({"ok": True, "turns": log.recent(limit, kind)})
            except ValueError:
                self.send_json({"ok": False, "error": "bad limit/hours/kind"}, 400)
            return

        elif path == "/api/system/profile":
            # the single source for every hardware/model/engine label in the UI and CLI
            self.send_json(system_profile.get_profile(load_config(), fresh="fresh=1" in (parsed.query or "")))
            return

        elif path == "/api/dataset/status":
            self.send_json({"ok": True, "status": dataset_compiler.get_status()})
            return

        elif path == "/api/dataset/download":
            query = urllib.parse.parse_qs(parsed.query)
            q_type = query.get("type", ["sharegpt"])[0]
            fname = "homelab_curated_sharegpt.jsonl" if q_type == "sharegpt" else ("homelab_curated_alpaca.jsonl" if q_type == "alpaca" else "homelab_curated_manifest.json")
            fpath = os.path.join(ROOT_DIR, "datasets", fname)
            if os.path.exists(fpath):
                self.close_connection = True
                total_size = os.path.getsize(fpath)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-jsonlines" if fname.endswith(".jsonl") else "application/json")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(total_size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                with open(fpath, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                return
            else:
                self.send_json({"ok": False, "error": f"Dataset file {fname} not compiled yet."}, 404)
                return

        elif path == "/api/trainer/status":
            self.send_json(trainer_client.get_status())
            return

        elif path == "/api/trainer/curation":
            query = urllib.parse.parse_qs(parsed.query)
            limit = int(query.get("limit", [100])[0])
            filter_status = query.get("filter", [None])[0]
            self.send_json(trainer_client.get_curation_registry(limit=limit, filter_status=filter_status))
            return

        elif path == "/api/trainer/passdown":
            self.send_json(trainer_client.get_passdown())
            return

        elif path == "/api/trainer/dossier":
            query = urllib.parse.parse_qs(parsed.query)
            sample_id = query.get("id", [""])[0]
            self.send_json(trainer_client.get_dossier(sample_id=sample_id))
            return


        elif path == "/api/engines/lease":
            self.send_json({"ok": True, **get_engine_lease().status()})
            return

        elif path == "/api/provenance":
            self.send_json({"ok": True, **get_provenance().summary()})
            return

        elif path == "/api/idle-gate":
            q = urllib.parse.parse_qs(parsed.query or "")
            self.send_json({"ok": True, **get_idle_gate().check(fresh=q.get("fresh", ["0"])[0] == "1")})
            return


        elif path == "/api/garden/status":
            try:
                from harness.core.landscape_garden_engine import landscape_garden_engine
                self.send_json({"ok": True, "data": landscape_garden_engine.get_garden_status()})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/agent_dna/list":
            try:
                from harness.core.agent_dna import agent_dna_manager
                self.send_json({"ok": True, "agents": agent_dna_manager.list_agents()})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/agent_dna/get":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                agent_id = query.get("agent_id", [""])[0]
                from harness.core.agent_dna import agent_dna_manager
                agent = agent_dna_manager.get_agent(agent_id)
                if agent:
                    self.send_json({"ok": True, "agent": agent})
                else:
                    self.send_json({"ok": False, "error": f"Agent {agent_id} not found"}, 404)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/cluster/telemetry":
            prof = system_profile.get_profile(load_config())
            engines = prof.get("engines") or {}
            gpus = [{"id": f"gpu{g['index']}", "name": g["name"], "vram_total_gb": g["vram_total_gb"],
                     "vram_used_gb": g["vram_used_gb"],
                     "assigned_to": ", ".join(f"{r} (:{engines[r]['port']})" for r in g.get("engines", []))}
                    for g in prof.get("gpus") or []]
            self.send_json({
                "ok": True, "timestamp": time.time(),
                "cluster_status": "online" if any(e.get("online") for e in engines.values()) else "offline",
                "nodes": [{"id": "inference", "name": (prof.get("host") or {}).get("hostname") or "inference host",
                           "cpu": (prof.get("host") or {}).get("cpu"), "gpus": gpus}],
                "models": [{"endpoint": e["url"], "role": r, "model": e.get("model"), "ctx_window": e.get("ctx_per_slot"),
                            "slots": e.get("slots"), "speculative_decoding": f"draft {e['draft_model']}" if e.get("draft_model") else "off"}
                           for r, e in engines.items()],
                "probe_error": prof.get("probe_error"),
            })
            return

        elif path == "/api/agent_dna/export":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                agent_id = query.get("agent_id", [""])[0]
                from harness.core.agent_dna import agent_dna_manager
                tar_path = agent_dna_manager.export_bundle(agent_id)
                if os.path.exists(tar_path):
                    self.close_connection = True
                    total_size = os.path.getsize(tar_path)
                    fname = os.path.basename(tar_path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/gzip")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Content-Length", str(total_size))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    with open(tar_path, "rb") as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk: break
                            self.wfile.write(chunk)
                    try: self.wfile.flush()
                    except Exception: pass
                    return
                else:
                    self.send_json({"ok": False, "error": "Bundle file not found"}, 404)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/config":
            from lodestone import config_mask
            self.send_json(config_mask.mask(load_config()))
            return

        elif path.startswith("/api/boost"):
            self._handle_boost_get(path, urllib.parse.parse_qs(parsed.query))
            return

        elif path == "/api/services/status":
            cfg = load_config()
            services = cfg.get("services", [])
            
            def check_service(s):
                target_url = s.get("url", "")
                start = time.perf_counter()
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.Request(target_url, headers={"Accept": "*/*", "User-Agent": "StoneSage-HealthProbe/2.0"})
                    with urllib.request.urlopen(req, timeout=1.8, context=ctx) as resp:
                        lat = round((time.perf_counter() - start) * 1000, 1)
                        return {**s, "online": True, "status_code": resp.status, "latency_ms": lat}
                except Exception as e:
                    lat = round((time.perf_counter() - start) * 1000, 1)
                    # Even if 401/403/404/405, service is online!
                    if hasattr(e, "code") and e.code in [401, 403, 404, 405]:
                        return {**s, "online": True, "status_code": e.code, "latency_ms": lat}
                    return {**s, "online": False, "error": str(e), "latency_ms": lat}

            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(check_service, services))
            self.send_json({"ok": True, "services": results})
            return

        elif path == "/api/cluster/health":
            health = cluster.check_all_nodes()
            self.send_json({"ok": True, "cluster": health})
            return

        # ── Engine Console API (Unified LLM Control & Debug) ──────────────
        elif path == "/api/engine/state/all":
            try:
                from lodestone.engine_controller import build_all_engine_states
                result = build_all_engine_states()
                self.send_json({"ok": True, **result})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/engine/state":
            qs = urllib.parse.parse_qs(parsed.query)
            engine_key = qs.get("engine", ["coordinator"])[0]
            try:
                from lodestone.engine_controller import build_engine_state
                state = build_engine_state(engine_key)
                self.send_json({"ok": True, "engine": state})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/engine/state/dynamic":
            qs = urllib.parse.parse_qs(parsed.query)
            host = qs.get("host", [""])[0]
            port = int(qs.get("port", ["1234"])[0])
            name = qs.get("name", ["dynamic"])[0]
            if not host:
                self.send_json({"ok": False, "error": "Missing host parameter"}, 400)
                return
            try:
                from lodestone.engine_controller import build_dynamic_engine_state
                state = build_dynamic_engine_state(host, port, name)
                self.send_json({"ok": True, "engine": state})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/engine/logs":
            qs = urllib.parse.parse_qs(parsed.query)
            service = qs.get("service", [""])[0]
            lines = int(qs.get("lines", ["100"])[0])
            if not service:
                self.send_json({"ok": False, "error": "Missing service parameter"}, 400)
                return
            try:
                from lodestone.engine_controller import fetch_journal_logs
                log_lines = fetch_journal_logs(service, lines=min(lines, 500))
                self.send_json({"ok": True, "logs": log_lines, "service": service})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return


        elif path == "/api/proxmox/nodes":
            nodes = {}
            for key in proxmox_node_keys(load_config()):
                stat = proxmox.get_node_status(key)
                nodes[stat.get("node") or key.replace("_node", "")] = stat
            self.send_json({"ok": True, "nodes": nodes})
            return

        elif path == "/api/proxmox/guests":
            all_guests = []
            for key in proxmox_node_keys(load_config()):
                all_guests += proxmox.get_guests(key).get("guests", [])
            self.send_json({"ok": True, "guests": all_guests})
            return

        elif path == "/api/couchdb/status":
            self.send_json(couchdb.get_status())
            return

        elif path in ("/api/ai/coordinator/v1/models", "/api/ai/worker/v1/models"):
            is_worker = "worker" in path
            m_id = "worker" if is_worker else "coordinator"
            self.send_json({
                "object": "list",
                "data": [
                    {
                        "id": m_id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "pve-cluster"
                    }
                ]
            })
            return

        elif path == "/api/status/markdown":
            vault_p = config.get("obsidian", {}).get("user_vault_path") or config.get("obsidian", {}).get("vault_path")
            candidates = [
                os.path.join(vault_p, "Current Status.md") if vault_p else "",
                "/opt/stonesage/vault_backup/Current Status.md",
                "vault_backup/Current Status.md"
            ]
            content = ""
            for c in candidates:
                if c and os.path.exists(c):
                    try:
                        with open(c, "r", encoding="utf-8") as f:
                            content = f.read()
                        break
                    except Exception:
                        pass
            if content:
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.send_json({"error": "Current Status.md not found"}, status=404)
            return

        elif path == "/api/obsidian/databases":
            dbs = couchdb.list_databases()
            self.send_json({
                "ok": True,
                "databases": dbs,
                "default_database": "ai_obsidian"
            })
            return

        elif path == "/api/obsidian/notes":
            req_db = urllib.parse.parse_qs(parsed.query).get("database", [""])[0]
            target_db = req_db if req_db else "ai_obsidian"
            couch_notes = couchdb.list_notes(database=target_db)
            local_notes = []
            
            # Scan candidate vault locations based on target database
            vault_candidates = []
            if target_db == "ai_obsidian":
                for cand_p in [
                    os.path.abspath(os.path.join(ROOT_DIR, "vault_backup", "ai_obsidian")),
                    "/opt/stonesage/vault_backup/ai_obsidian",
                    os.path.join(_vault(), "AI Stack") if _vault() else ""
                ]:
                    if os.path.exists(cand_p) and cand_p not in vault_candidates:
                        vault_candidates.append(cand_p)
            else:
                user_vault = config.get("obsidian", {}).get("user_vault_path")
                if user_vault and os.path.exists(user_vault):
                    vault_candidates.append(user_vault)
                for cand_p in [
                    os.path.abspath(os.path.join(ROOT_DIR, "vault_backup", "obsidian")),
                    os.path.abspath(os.path.join(ROOT_DIR, "vault_backup")),
                    "/opt/stonesage/vault_backup/obsidian",
                    "/opt/stonesage/vault_backup",
                ]:
                    if os.path.exists(cand_p) and cand_p not in vault_candidates:
                        vault_candidates.append(cand_p)

            seen_paths = set()
            for v_root in vault_candidates:
                for root, dirs, files in os.walk(v_root):
                    if any(x in root for x in [".git", ".obsidian", ".claudian", ".smart-env", "_archive"]):
                        continue
                    for f in files:
                        if f.endswith((".md", ".txt", ".canvas")):
                            full = os.path.join(root, f)
                            rel = os.path.relpath(full, v_root).replace("\\", "/")
                            if rel.startswith("obsidian/"):
                                rel = rel[9:]
                            key = rel.lower().strip()
                            if key in seen_paths:
                                continue
                            seen_paths.add(key)
                            try:
                                stat = os.stat(full)
                                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                                local_notes.append({
                                    "id": rel,
                                    "name": f,
                                    "path": rel,
                                    "size_bytes": stat.st_size,
                                    "modified": mtime,
                                    "source": "local_vault",
                                    "database": target_db
                                })
                            except Exception:
                                pass

            backup_notes = vault.list_notes() if target_db != "ai_obsidian" else []
            merged_map = {}
            for n in couch_notes:
                key = n["path"].lower().strip()
                n["in_couchdb"] = True
                merged_map[key] = n

            for n in local_notes:
                key = n["path"].lower().strip()
                if key in merged_map:
                    merged_map[key]["has_local_file"] = True
                    merged_map[key]["size_bytes"] = n["size_bytes"]
                    merged_map[key]["modified"] = n["modified"]
                else:
                    n["in_couchdb"] = False
                    n["has_local_file"] = True
                    merged_map[key] = n

            for n in backup_notes:
                key = n["path"].lower().strip()
                if key.startswith("obsidian/"):
                    key = key[9:]
                if key in merged_map:
                    merged_map[key]["has_backup"] = True
                else:
                    n["has_backup"] = True
                    merged_map[key] = n

            all_notes = sorted(list(merged_map.values()), key=lambda x: x["path"].lower())
            self.send_json({
                "ok": True,
                "database": target_db,
                "notes": all_notes,
                "total_count": len(all_notes),
                "couchdb_count": len(couch_notes),
                "local_count": len(local_notes)
            })
            return

        elif path == "/api/obsidian/note":
            rel_path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            target_db = urllib.parse.parse_qs(parsed.query).get("database", [""])[0] or "ai_obsidian"
            if not rel_path:
                self.send_json({"ok": False, "error": "Missing path parameter"})
                return

            norm_rel = os.path.normpath(rel_path).lstrip("\\/")
            if norm_rel.startswith("obsidian/"):
                norm_rel = norm_rel[9:]
            target_name = os.path.basename(rel_path).lower()

            # 1. Search candidate disk vaults
            vault_candidates = []
            user_vault = config.get("obsidian", {}).get("user_vault_path")
            if user_vault and os.path.exists(user_vault):
                vault_candidates.append(user_vault)
            for cand_p in [
                os.path.abspath(os.path.join(ROOT_DIR, "vault_backup", "obsidian")),
                os.path.abspath(os.path.join(ROOT_DIR, "vault_backup")),
                "/opt/stonesage/vault_backup/obsidian",
                "/opt/stonesage/vault_backup",
            ]:
                if os.path.exists(cand_p) and cand_p not in vault_candidates:
                    vault_candidates.append(cand_p)

            for vc in vault_candidates:
                full_path = os.path.join(vc, norm_rel)
                if not (os.path.exists(full_path) and os.path.isfile(full_path)):
                    # Case-insensitive or filename search
                    for root, dirs, files in os.walk(vc):
                        if any(x in root for x in [".git", ".obsidian", ".claudian", ".smart-env", "_archive"]):
                            continue
                        for file in files:
                            if file.lower() == target_name:
                                cand = os.path.join(root, file)
                                if os.path.isfile(cand):
                                    full_path = cand
                                    break
                        if os.path.exists(full_path) and os.path.isfile(full_path):
                            break

                if os.path.exists(full_path) and os.path.isfile(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        self.send_json({
                            "ok": True,
                            "path": rel_path,
                            "content": content,
                            "source": "local_vault",
                            "file_size": len(content)
                        })
                        return
                    except Exception as e:
                        logger.warning("Error reading local vault file %s: %s", full_path, e)

            # 2. Check CouchDB database
            couch_doc = couchdb.get_note_doc(norm_rel, database=target_db)
            if not couch_doc and target_db != "obsidiannotes":
                couch_doc = couchdb.get_note_doc(norm_rel, database="obsidiannotes")
            if couch_doc and ("data" in couch_doc or "content" in couch_doc):
                content = couch_doc.get("data") or couch_doc.get("content", "")
                self.send_json({
                    "ok": True,
                    "path": rel_path,
                    "content": content,
                    "source": "couchdb",
                    "database": target_db,
                    "rev": couch_doc.get("_rev")
                })
                return

            # 3. Check vault.get_note
            note = vault.get_note(rel_path)
            if note.get("ok"):
                self.send_json(note)
                return

            # 3. Query Qdrant vector database fallback
            qdrant_url = endpoints.url("qdrant", config)
            clean_title = os.path.splitext(os.path.basename(rel_path))[0]
            collections_to_search = ["obsidian_vault", "companion_profile", "codebase_knowledge", "agent_memories"]
            qdrant_chunks = []

            for col in collections_to_search:
                try:
                    scroll_payload = {
                        "limit": 64,
                        "with_payload": True,
                        "with_vector": False,
                        "filter": {
                            "should": [
                                {"key": "path", "match": {"value": rel_path}},
                                {"key": "path", "match": {"value": rel_path.replace("/", "\\")}},
                                {"key": "path", "match": {"value": rel_path.replace("\\", "/")}},
                                {"key": "title", "match": {"value": clean_title}},
                                {"key": "title", "match": {"value": os.path.basename(rel_path)}}
                            ]
                        }
                    }
                    req = urllib.request.Request(
                        f"{qdrant_url}/collections/{col}/points/scroll",
                        data=json.dumps(scroll_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=2.5) as resp:
                        col_data = json.loads(resp.read().decode("utf-8"))
                        pts = col_data.get("result", {}).get("points", [])
                        if pts:
                            qdrant_chunks.extend([p.get("payload", {}) for p in pts if p.get("payload")])
                            break
                except Exception:
                    pass

            if qdrant_chunks:
                qdrant_chunks.sort(key=lambda x: int(x.get("chunk_index", x.get("chunk_idx", 0))))
                assembled_text = ""
                seen_texts = set()
                for c in qdrant_chunks:
                    t = c.get("content") or c.get("text", "")
                    if t and t not in seen_texts:
                        seen_texts.add(t)
                        if assembled_text:
                            assembled_text += "\n\n"
                        assembled_text += t

                if assembled_text.strip():
                    self.send_json({
                        "ok": True,
                        "path": rel_path,
                        "content": assembled_text,
                        "source": "qdrant_memory",
                        "chunks_count": len(qdrant_chunks)
                    })
                    return

            # 4. Fallback to CouchDB
            doc = couchdb.get_note_doc(rel_path)
            if doc:
                raw_data = doc.get("data")
                if raw_data and isinstance(raw_data, str) and not raw_data.startswith("%="):
                    self.send_json({
                        "ok": True,
                        "path": rel_path,
                        "content": raw_data,
                        "source": "couchdb_plain",
                        "doc": doc
                    })
                    return

                size_bytes = doc.get("size", 0)
                children = doc.get("children", [])
                rev = doc.get("_rev", "unknown")
                mtime_val = doc.get("mtime", "Unknown")
                note_banner = (
                    f"# {os.path.basename(rel_path)}\n\n"
                    f"> [!NOTE]\n"
                    f"> **Obsidian LiveSync Encrypted Document**\n"
                    f"> - **CouchDB Path:** `{doc.get('path', rel_path)}`\n"
                    f"> - **Document Size:** {size_bytes} bytes\n"
                    f"> - **Revision:** `{rev}`\n"
                    f"> - **Chunk Leaves:** {len(children)} encrypted blocks\n"
                    f"> - **Encryption:** AES-256-GCM End-to-End (E2EE)\n"
                    f"> - **Last Modified:** {mtime_val}\n\n"
                    f"*This note was synced from your mobile or remote device via Obsidian LiveSync E2EE.* "
                    f"*To read or edit this note directly in StoneSage, sync it to `/opt/stonesage/vault_backup/obsidian` or vectorize it into Qdrant.*\n"
                )
                self.send_json({
                    "ok": True,
                    "path": rel_path,
                    "content": note_banner,
                    "source": "couchdb_metadata",
                    "doc": doc
                })
                return

            self.send_json({"ok": False, "error": f"Note '{rel_path}' not found."})
            return

        elif path == "/api/obsidian/sync_status":
            self.send_json({"ok": True, "sync": obsidian_ingestor.get_status(), "couchdb": couchdb.get_status()})
            return

        elif path == "/api/obsidian/search":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            if not q:
                self.send_json({"ok": False, "error": "Missing query parameter 'q'"}, 400)
                return
            limit = int(urllib.parse.parse_qs(parsed.query).get("limit", ["6"])[0])
            results = cluster.search_hybrid(q, collection_name="obsidian_vault", limit=limit)
            self.send_json({"ok": True, "query": q, "count": len(results), "results": results})
            return

        elif path == "/api/memory/stm":
            self.send_json(stm.get_summary())
            return

        elif path == "/api/knowledge/status":
            kb_dir = os.path.join(_vault(), "LocalLlmHub", "rag")
            file_count = 0
            if os.path.exists(kb_dir):
                for _, _, files in os.walk(kb_dir):
                    file_count += len([f for f in files if f.endswith(".md")])
            self.send_json({"ok": True, "kb_path": kb_dir, "file_count": file_count, "exists": os.path.exists(kb_dir)})
            return

        elif path == "/api/git/status":
            repo_dir = ACTIVE_GIT_REPO_DIR
            is_repo = False
            branch = "main"
            remote_url = ""
            status_lines = []
            log_lines = []
            try:
                toplevel = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
                is_repo = True
                repo_dir = toplevel
            except Exception:
                is_repo = False

            if is_repo:
                try:
                    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
                except Exception:
                    pass
                try:
                    remote_url = subprocess.check_output(["git", "config", "--get", "remote.origin.url"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
                except Exception:
                    pass
                try:
                    status_out = subprocess.check_output(["git", "status", "--short"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
                    status_lines = [l for l in status_out.splitlines() if l.strip()]
                except Exception:
                    pass
                try:
                    log_out = subprocess.check_output(["git", "log", "-n", "5", "--oneline"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
                    log_lines = [l for l in log_out.splitlines() if l.strip()]
                except Exception:
                    pass

            self.send_json({
                "ok": True,
                "is_repo": is_repo,
                "repo_dir": repo_dir.replace("\\", "/"),
                "repo_name": os.path.basename(repo_dir) or repo_dir,
                "branch": branch or "main",
                "remote_url": remote_url,
                "changed_files": status_lines,
                "recent_commits": log_lines
            })
            return

        elif path == "/api/git/repos/discovered":
            discovered = []
            candidates = [
                WORKSPACE_ROOT,
                ACTIVE_WORKSPACE_DIR,
                "/opt",
                "/opt/stonesage",
                "/mnt/nas",
                "/mnt/nas/git",
                REPO_ROOT,
                os.path.join(REPO_ROOT, "hivemind-cluster-stack"),
                ROOT_DIR
            ]
            seen_dirs = set()
            for cand in candidates:
                if not os.path.exists(cand):
                    continue
                cand_abs = os.path.abspath(cand)
                # Check if cand itself is a git repo
                if os.path.exists(os.path.join(cand_abs, ".git")):
                    p_norm = cand_abs.replace("\\", "/")
                    if p_norm not in seen_dirs:
                        seen_dirs.add(p_norm)
                        discovered.append({"name": os.path.basename(cand_abs), "path": p_norm})
                # Check immediate subdirectories (depth 1)
                try:
                    for sub in os.scandir(cand_abs):
                        if sub.is_dir(follow_symlinks=False):
                            if os.path.exists(os.path.join(sub.path, ".git")):
                                p_norm = sub.path.replace("\\", "/")
                                if p_norm not in seen_dirs:
                                    seen_dirs.add(p_norm)
                                    discovered.append({"name": sub.name, "path": p_norm})
                except Exception:
                    pass
            self.send_json({
                "ok": True,
                "repos": discovered,
                "active_repo": ACTIVE_GIT_REPO_DIR.replace("\\", "/")
            })
            return

        elif path == "/api/subagents/list":
            self.send_json({"ok": True, "subagents": list(subagent_registry.values())})
            return


        elif path.startswith("/api/uploads/"):
            filename = os.path.basename(path)
            file_path = os.path.join(UPLOADS_DIR, filename)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                ctype, _ = mimetypes.guess_type(file_path)
                ctype = ctype or "application/octet-stream"
                size = os.path.getsize(file_path)
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_json({"error": "File not found"}, 404)
                return

        elif path == "/api/workspace/tree":
            requested_dir = urllib.parse.parse_qs(parsed.query).get("dir", [""])[0]
            if requested_dir:
                req_abs = os.path.abspath(requested_dir)
                if os.path.exists(req_abs) and os.path.isdir(req_abs):
                    ACTIVE_WORKSPACE_DIR = req_abs

            current_dir = ACTIVE_WORKSPACE_DIR
            parent_dir = os.path.dirname(current_dir) if os.path.dirname(current_dir) != current_dir else None

            def build_tree(dir_path, current_depth=0, max_depth=2, max_items=120):
                items = []
                try:
                    count = 0
                    for entry in os.scandir(dir_path):
                        if entry.name.startswith(".") and entry.name != ".ai":
                            continue
                        if entry.name in ["__pycache__", "node_modules", ".git", ".obsidian", ".system_generated", "vault_backup", "proc", "sys", "dev"]:
                            continue
                        rel = os.path.relpath(entry.path, current_dir).replace("\\", "/")
                        if entry.is_dir(follow_symlinks=False):
                            children = build_tree(entry.path, current_depth + 1, max_depth, max_items) if current_depth < max_depth else []
                            items.append({
                                "name": entry.name,
                                "path": rel,
                                "full_path": entry.path.replace("\\", "/"),
                                "type": "directory",
                                "children": children
                            })
                        elif entry.is_file():
                            try:
                                size = entry.stat().st_size
                            except Exception:
                                size = 0
                            items.append({
                                "name": entry.name,
                                "path": rel,
                                "full_path": entry.path.replace("\\", "/"),
                                "type": "file",
                                "size": size
                            })
                        count += 1
                        if count >= max_items:
                            items.append({
                                "name": "... [More items truncated]",
                                "path": "",
                                "type": "file",
                                "size": 0
                            })
                            break
                except Exception:
                    pass
                items.sort(key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower()))
                return items

            tree = build_tree(current_dir)
            self.send_json({
                "ok": True,
                "current_dir": current_dir.replace("\\", "/"),
                "parent_dir": parent_dir.replace("\\", "/") if parent_dir else None,
                "presets": get_directory_presets(),
                "tree": tree
            })
            return

        elif path == "/api/workspace/file":
            req_path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if not req_path:
                self.send_json({"ok": False, "error": "Missing path parameter"}, 400)
                return
            if os.path.isabs(req_path):
                full_path = os.path.abspath(req_path)
            else:
                full_path = os.path.abspath(os.path.join(ACTIVE_WORKSPACE_DIR, req_path))

            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                self.send_json({"ok": False, "error": f"File not found: {req_path}"}, 404)
                return
            try:
                is_raw = urllib.parse.parse_qs(parsed.query).get("raw", ["0"])[0] in ("1", "true")
                ext = os.path.splitext(full_path)[1].lower()
                binary_exts = {".glb", ".gltf", ".obj", ".bin", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".wasm"}
                
                if is_raw or ext in binary_exts:
                    import mimetypes
                    ctype, _ = mimetypes.guess_type(full_path)
                    if not ctype:
                        ctype = "model/gltf-binary" if ext == ".glb" else "application/octet-stream"
                    with open(full_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                rel_path = os.path.relpath(full_path, ACTIVE_WORKSPACE_DIR).replace("\\", "/")
                self.send_json({
                    "ok": True,
                    "path": rel_path,
                    "full_path": full_path.replace("\\", "/"),
                    "content": content,
                    "size": len(content)
                })
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/workspaces/list":
            self.send_json({
                "ok": True,
                "roots": get_workspace_roots(),
                "workspaces": list_workspace_directories(),
                "active_workspace": ACTIVE_WORKSPACE_DIR.replace("\\", "/")
            })
            return

        elif path == "/api/workspaces/agent":
            req_ws = urllib.parse.parse_qs(parsed.query).get("path", [""])[0] or ACTIVE_WORKSPACE_DIR
            try:
                from harness.core.openclaw_engine import openclaw_engine
                agent_dna = openclaw_engine.load_project_agent(req_ws)
                if agent_dna:
                    self.send_json({"ok": True, "agent": agent_dna})
                else:
                    self.send_json({"ok": False, "error": "No project agent bound to workspace", "agent": None})
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/chat/sessions":
            try:
                from harness.data_fabric.pg_storage import relational_storage
                sessions = relational_storage.list_sessions()
                self.send_json({"ok": True, "sessions": sessions})
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex), "sessions": []}, 500)
            return


        elif path == "/api/chat/sessions/messages":
            try:
                from harness.data_fabric.pg_storage import relational_storage
                query_dict = urllib.parse.parse_qs(parsed.query)
                session_id = query_dict.get("session_id", [""])[0]
                if not session_id:
                    self.send_json({"ok": False, "error": "Missing session_id"}, 400)
                    return
                session_meta = relational_storage.get_session(session_id)
                messages = relational_storage.get_session_messages(session_id)
                self.send_json({
                    "ok": True,
                    "session": session_meta,
                    "messages": messages
                })
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return


        # ── Transparent RAG Proxy: OpenAI-compatible /models endpoint ──
        elif path in ("/api/ai/coordinator/v1/models", "/api/ai/worker/v1/models"):
            is_worker = "worker" in path
            active_id = "worker" if is_worker else "coordinator"
            target_url = endpoints.url("worker" if is_worker else "coordinator", config) + "/models"
            try:
                req = urllib.request.Request(target_url, headers={"User-Agent": "StoneSage-Proxy"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("data"):
                        active_id = data["data"][0].get("id", active_id)
            except Exception:
                pass
            self.send_json({
                "object": "list",
                "data": [
                    {
                        "id": active_id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "stonesage-rag-proxy",
                        "permission": []
                    },
                    {
                        "id": "coordinator-14b-rag",  # legacy compatibility alias
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "stonesage-rag-proxy",
                        "permission": []
                    }
                ]
            })
            return

        elif path == "/api/profiles/list":
            profiles = load_user_profiles()
            self.send_json({"ok": True, "profiles": profiles})
            return

        elif path == "/api/harness/capabilities":
            try:
                # 1. Query live coordinator models from :8001/v1/models
                coord_info = {"id": "coordinator", "status": "unknown"}
                try:
                    req_c = urllib.request.Request(endpoints.url("coordinator", config) + "/models", headers={"User-Agent": "Aevum-Server"})
                    with urllib.request.urlopen(req_c, timeout=2.5) as resp_c:
                        c_data = json.loads(resp_c.read().decode("utf-8"))
                        if c_data.get("data"):
                            coord_info = c_data["data"][0]
                            coord_info["status"] = "online"
                except Exception as ex_c:
                    coord_info["status"] = f"offline ({str(ex_c)})"

                # 2. Query live worker models from :8002/v1/models
                worker_info = {"id": "worker", "status": "unknown"}
                try:
                    req_w = urllib.request.Request(endpoints.url("worker", config) + "/models", headers={"User-Agent": "Aevum-Server"})
                    with urllib.request.urlopen(req_w, timeout=2.5) as resp_w:
                        w_data = json.loads(resp_w.read().decode("utf-8"))
                        if w_data.get("data"):
                            worker_info = w_data["data"][0]
                            worker_info["status"] = "online"
                except Exception as ex_w:
                    worker_info["status"] = f"offline ({str(ex_w)})"

                # 3. Retrieve installed models list from disk or SSH
                installed_models = []
                try:
                    full_cmd2 = endpoints.ssh_cmd(config, "ls -lh /opt/models/*.gguf")
                    res2 = subprocess.run(full_cmd2, capture_output=True, text=True, timeout=5)
                    if res2.returncode == 0 and res2.stdout:
                        for line in res2.stdout.strip().split("\n"):
                            parts = line.split()
                            if len(parts) >= 9:
                                fn = parts[8].replace("/opt/models/", "")
                                installed_models.append({
                                    "filename": fn,
                                    "size": parts[4],
                                    "is_locked": fn in system_profile.loaded_model_files(system_profile.get_profile(load_config()))
                                })
                except Exception:
                    pass


                available_harnesses = [
                    {"id": "hermes", "name": "Agentic Loop (DEFAULT)", "description": f"ReAct step loop on {system_profile.engine_label(load_config(), 'coordinator')}"},
                    {"id": "llama_coordinator", "name": f"Coordinator: {system_profile.engine_label(load_config(), 'coordinator')}", "description": "Reasoning, architecture and coding"},
                    {"id": "llama_worker", "name": f"Worker: {system_profile.engine_label(load_config(), 'worker')}", "description": "Fast drafts, utilities and linting"},
                    {"id": "snapdragon", "name": "Snapdragon Edge NPU", "description": "Mobile on-device hardware accelerator"},
                    {"id": "openwebui", "name": "OpenWebUI (LXC 119)", "description": "Community chat interface on :8080"}
                ]

                available_backends = [
                    {"id": "cluster_lan", "name": f"Dual-Accelerator Vulkan Cluster ({endpoints.host('coordinator', config)})", "description": "Primary Compute Accelerator + Secondary Worker Accelerator"},
                    {"id": "tailscale_vip", "name": "Tailscale Mesh VIP", "description": "Encrypted remote WireGuard mesh"},
                    {"id": "local_edge", "name": "On-Device Snapdragon NPU", "description": "Mobile local inference"}
                ]

                self.send_json({
                    "ok": True,
                    "timezone": site.describe(config),
                    "active_coordinator": coord_info,
                    "active_worker": worker_info,
                    "installed_models": installed_models,
                    "available_harnesses": available_harnesses,
                    "available_backends": available_backends
                })
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/cluster/models":
            try:
                active_model = "Unknown"
                active_ctx = "8192"
                active_ftype = ""
                active_params = 0
                model_type = "coordinator"

                # 1. Fast live probe to :8001/v1/models (takes ~2ms)
                try:
                    req_m = urllib.request.Request(endpoints.url("coordinator", config) + "/models", headers={"Accept": "application/json"})
                    with urllib.request.urlopen(req_m, timeout=0.8) as resp_m:
                        m_data = json.loads(resp_m.read().decode("utf-8"))
                        if m_data.get("data") and len(m_data["data"]) > 0:
                            m0 = m_data["data"][0]
                            mid = m0.get("id", "")
                            meta = m0.get("meta", {})
                            active_ctx = str(meta.get("n_ctx", "8192"))
                            active_ftype = meta.get("ftype", "")
                            active_params = meta.get("n_params", 0)
                            if mid:
                                active_model = f"{mid} ({active_ftype or 'loaded'})"
                                model_type = "moe" if "moe" in mid.lower() else "coordinator"
                            elif active_params > 0:
                                active_model = f"Model-{active_params // 1000000000}B ({active_ftype or 'loaded'})"
                                model_type = "coordinator"
                            else:
                                active_model = f"Active Compute ({active_ftype or 'loaded'})"
                                model_type = "coordinator"
                except Exception:
                    pass

                # Fallback to systemd inspection if HTTP probe was unavailable
                if active_model == "Unknown":
                    try:
                        coord = (system_profile.get_profile(load_config()).get("engines") or {}).get("coordinator") or {}
                        if coord.get("model_file"):
                            active_model = coord["model_file"]
                        if coord.get("ctx_per_slot"):
                            active_ctx = str(coord["ctx_per_slot"])
                    except Exception:
                        pass

                full_cmd2 = endpoints.ssh_cmd(config, "ls -lh /opt/models/*.gguf 2>/dev/null && df -h /opt/models | tail -n 1")
                models = []
                disk_str = ""
                try:
                    res2 = subprocess.run(full_cmd2, capture_output=True, text=True, timeout=3)
                    if res2.returncode == 0 and res2.stdout:
                        lines = res2.stdout.strip().split("\n")
                        disk_str = lines[-1] if lines else ""
                        for line in lines[:-1]:
                            parts = line.split()
                            if len(parts) >= 9:
                                models.append({
                                    "name": parts[8].replace("/opt/models/", ""),
                                    "size": parts[4]
                                })
                except Exception:
                    pass

                calib_file = os.path.join(WORKSPACE_ROOT, ".agents", "skills", "model-parameter-discoverer", "optimal_profile.json")
                calib = {}
                if os.path.exists(calib_file):
                    try:
                        with open(calib_file, "r", encoding="utf-8") as f:
                            calib = json.load(f)
                    except Exception:
                        pass

                self.send_json({
                    "ok": True,
                    "active_model": active_model,
                    "model_type": model_type,
                    "active_context": active_ctx,
                    "active_ftype": active_ftype,
                    "models": models,
                    "disk": disk_str,
                    "calibration": calib
                })
                return
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
                return

        elif path == "/api/3d/gallery":
            try:
                req = urllib.request.Request(endpoints.url("blender", config) + "/api/v1/blender/gallery")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    gallery = json.loads(resp.read().decode("utf-8"))
                self.send_json({"ok": True, "gallery": gallery})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e), "gallery": []})
            return

        elif path == "/api/3d/scene":
            try:
                req = urllib.request.Request(endpoints.url("blender", config) + "/api/v1/blender/scene")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    scene_data = json.loads(resp.read().decode("utf-8"))
                self.send_json({"ok": True, "scene": scene_data})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        elif path.startswith("/api/memory/obsidian/query"):
            try:
                from stonesage.obsidian_brain import obsidian_brain
                from urllib.parse import parse_qs, urlparse
                query_param = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                results = obsidian_brain.search(query_param, limit=5, score_threshold=0.72)
                self.send_json({"ok": True, "query": query_param, "results": results})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        elif path == "/api/cluster/modes":
            # Only the live layout. The old preset switcher (switch_cluster_mode.py) rewrote systemd units
            # and its presets named models that are no longer installed; engines change via their units now.
            mode = system_profile.live_mode(system_profile.get_profile(load_config()))
            self.send_json({"ok": True, "active_mode": "live", "active_profile": mode,
                            "profiles": {"live": mode}, "switching": False})
            return

        elif path == "/api/agents/list":
            custom_agents = load_custom_agents()
            ws_agent = get_active_workspace_agent()
            self.send_json({
                "ok": True,
                "builtin_agents": BUILTIN_AGENTS,
                "custom_agents": custom_agents,
                "workspace_agent": ws_agent
            })
            return
        elif path == "/api/hivemind/status":
            cfg = load_config()
            mcp_u = endpoints.url("mcp", cfg)
            try:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "autonomous_thinking_status"}}
                req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                    parsed_st = json.loads(raw_txt)
                    parsed_st["preemption"] = cluster.get_preemption_status()
                    try:
                        p_mode = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_cluster_mode", "arguments": {}}}
                        req_m = urllib.request.Request(mcp_u, data=json.dumps(p_mode).encode("utf-8"), headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(req_m, timeout=5) as r_m:
                            res_m = json.loads(r_m.read().decode("utf-8"))
                            txt_m = res_m.get("result", {}).get("content", [{}])[0].get("text", "{}")
                            parsed_st["cluster_mode_info"] = json.loads(txt_m)
                    except Exception:
                        pass
                    self.send_json({"ok": True, "status": parsed_st})
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path in ["/api/cluster/mode", "/api/cluster/context_mode"]:
            cfg = load_config()
            mcp_u = endpoints.url("mcp", cfg)
            try:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_cluster_mode", "arguments": {}}}
                req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                    self.send_json({"ok": True, "mode": json.loads(raw_txt)})
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/hivemind/agents":
            cfg = load_config()
            mcp_u = endpoints.url("mcp", cfg)
            try:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_active_agents"}}
                req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "[]")
                    agents = json.loads(raw_txt) if raw_txt else []
                    self.send_json({"ok": True, "agents": agents})
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex), "agents": []}, 500)
            return

        elif path in ["/api/hivemind/agent_stream", "/api/hivemind/live_stream", "/api/agent/stream"]:
            qs = urllib.parse.parse_qs(parsed.query)
            limit = int(qs.get("limit", [50])[0])
            channel = qs.get("channel", ["all"])[0]
            try:
                res = get_agent_stream(channel=channel, limit=limit)
                self.send_json(res)
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path in ["/api/ha/activity_and_ai_logs", "/api/hivemind/activity_and_ai_logs", "/api/hivemind/vision_log"]:
            qs = urllib.parse.parse_qs(parsed.query)
            limit = int(qs.get("limit", [150])[0])
            filter_type = qs.get("filter", ["all"])[0]
            try:
                res = get_home_and_ai_activity_log(limit=limit, filter_type=filter_type)
                self.send_json(res)
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/hivemind/dossiers":
            cfg = load_config()
            qdrant_u = endpoints.url("qdrant", cfg).rstrip("/")
            qs = urllib.parse.parse_qs(parsed.query)
            limit = int(qs.get("limit", [100])[0])
            filter_status = qs.get("filter", ["all"])[0]
            try:
                scroll_url = f"{qdrant_u}/collections/autonomous_thinking/points/scroll"
                payload = {
                    "limit": limit,
                    "with_payload": True,
                    "with_vector": False
                }
                req = urllib.request.Request(
                    scroll_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    points = data.get("result", {}).get("points", [])
                    
                    dossiers = []
                    for pt in points:
                        pl = pt.get("payload", {})
                        d_id = pl.get("exploration_id") or str(pt.get("id"))
                        f_ver = bool(pl.get("frontier_verified", False))
                        h_app = bool(pl.get("human_approved", False))
                        quar = bool(pl.get("quarantined", False))
                        needs_v = bool(pl.get("needs_frontier_verification", False))
                        
                        human_status = "PENDING_REVIEW"
                        if h_app:
                            human_status = "APPROVED"
                        elif quar:
                            human_status = "REJECTED"
                            
                        dossiers.append({
                            "point_id": str(pt.get("id")),
                            "id": d_id,
                            "title": pl.get("title") or pl.get("domain_name") or d_id,
                            "domain": pl.get("domain_name", "Autonomous"),
                            "frontier_verified": f_ver,
                            "needs_frontier_verification": needs_v,
                            "human_approved": h_app,
                            "quarantined": quar,
                            "human_status": human_status,
                            "worker_score": pl.get("worker_score"),
                            "coordinator_score": pl.get("coordinator_score"),
                            "target_invariant": pl.get("target_invariant", ""),
                            "core_lesson": pl.get("core_lesson", ""),
                            "timestamp": pl.get("timestamp", ""),
                            "summary": pl.get("summary") or pl.get("distilled_invariant") or ""
                        })
                    
                    total = len(dossiers)
                    verified_count = sum(1 for d in dossiers if d["frontier_verified"])
                    approved_count = sum(1 for d in dossiers if d["human_approved"])
                    quarantined_count = sum(1 for d in dossiers if d["quarantined"])
                    pending_count = sum(1 for d in dossiers if not d["human_approved"] and not d["quarantined"])
                    
                    if filter_status == "verified":
                        filtered = [d for d in dossiers if d["frontier_verified"]]
                    elif filter_status == "approved":
                        filtered = [d for d in dossiers if d["human_approved"]]
                    elif filter_status == "pending":
                        filtered = [d for d in dossiers if not d["human_approved"] and not d["quarantined"]]
                    elif filter_status == "quarantined":
                        filtered = [d for d in dossiers if d["quarantined"]]
                    else:
                        filtered = dossiers
                        
                    self.send_json({
                        "ok": True,
                        "total": total,
                        "frontier_verified_count": verified_count,
                        "human_approved_count": approved_count,
                        "pending_count": pending_count,
                        "quarantined_count": quarantined_count,
                        "dossiers": filtered
                    })
            except Exception as ex:
                self.send_json({"ok": False, "error": str(ex)}, 500)
            return

        elif path == "/api/hivemind/dossier/content":
            qs = urllib.parse.parse_qs(parsed.query)
            d_id = qs.get("id", [""])[0]
            if not d_id:
                self.send_json({"ok": False, "error": "Missing dossier ID"}, 400)
                return
            content = ""
            local_paths = [
                f"../obsidian/Autonomous Thinking/Explorations/{d_id}.md",
                f"../obsidian/Autonomous Thinking/{d_id}.md",
                f"{_vault()}/Autonomous Thinking/Explorations/{d_id}.md" if _vault() else "",
                f"/opt/cluster-bridge/thinking_archive/{d_id}.md"
            ]
            for lp in local_paths:
                if os.path.exists(lp):
                    try:
                        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        break
                    except Exception:
                        pass
            if not content:
                try:
                    res = trainer_client.get_dossier(d_id)
                    content = res.get("content", "")
                except Exception:
                    pass
            self.send_json({"ok": True, "id": d_id, "content": content or f"# Dossier {d_id}\n\nNo local markdown file found on disk."})
            return

        elif path == "/api/system/tools_and_skills":
            self.send_json(load_dynamic_tools_and_skills())
            return

        elif path == "/api/harness/tools":
            if tool_registry:
                self.send_json({
                    "ok": True,
                    "tools": tool_registry.list_tools(),
                    "amem_cards": tool_registry.get_amem_tool_cards(),
                    "dynamic_count": len(tool_registry.dynamic_tools_order),
                    "max_dynamic": 20
                })
            else:
                self.send_json({"ok": False, "error": "Tool harness registry not available", "tools": []})
            return

        elif path == "/api/memory/amem":
            qs = urllib.parse.parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            cat = qs.get("category", [""])[0]
            self.send_json(load_dynamic_amem_cards(query=q, category=cat))
            return


        elif path in ("/api/tags", "/api/models"):
            # Ollama-compatible model list for Home Assistant, built from the live engines
            prof = system_profile.get_profile(load_config())

            def tag(name, eng):
                fam = ((eng.get("model") or name).split() or [name])[0].lower()
                return {"name": f"{name}:latest", "model": f"{name}:latest",
                        "modified_at": datetime.fromtimestamp(prof["updated_at"], timezone.utc).isoformat(),
                        "size": (eng.get("vram_mb") or 0) * 2**20, "digest": f"sha256:{eng.get('model_file') or name}",
                        "details": {"parent_model": "", "format": "gguf", "family": fam, "families": [fam],
                                    "parameter_size": eng.get("params") or "", "quantization_level": eng.get("quant") or ""}}

            engines = prof.get("engines") or {}
            models = []
            if engines.get("coordinator"):
                # Computer's tool loop runs on the coordinator. "computer" is the name since 2026-09-27; "courage" stays
                # listed because Home Assistant's Ollama agent is configured with it (renaming there is the owner's call).
                models.append(tag("computer", engines["coordinator"]))
                models.append(tag("courage", engines["coordinator"]))
            models += [tag(role, engines[role]) for role in ("coordinator", "worker") if engines.get(role)]
            self.send_json({"models": models})
            return

        elif path == "/api/version":
            self.send_json({"version": "0.4.0"})
            return

        elif path == "/api/preemption/status":
            self.send_json(cluster.get_preemption_status())
            return

        elif path == "/api/task_routing":
            cfg = load_config()
            self.send_json({
                "ok": True,
                "task_routing": cfg.get("task_routing", {}),
                "gemini_web": {
                    "enabled": cfg.get("gemini_web", {}).get("enabled", True),
                    "configured": bool(cfg.get("gemini_web", {}).get("psid")),
                    "endpoint": endpoints.url("gemini_web", cfg)
                }
            })
            return

        # Fallback to serving frontend static assets
        super().do_GET()

    def do_POST(self):
        global ACTIVE_WORKSPACE_DIR, ACTIVE_GIT_REPO_DIR
        self._responded = False
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            body = self.read_json_body()

            app_route = app_ctx.routes.find("POST", path)   # routes registered by apps (apps.py), before the platform's own
            if app_route:
                app_route(self, parsed, body)
                return

            if path == "/api/harness/session/stop":
                active_session_state.abort()
                self.send_json({"ok": True, "status": "aborted"})
                return

            elif path == "/api/watch/reply":
                import secrets as _secrets
                from stonesage.courage.tools import ALWAYS_CONFIRM
                token = (config.get("watch_bridge", {}) or {}).get("token", "")
                auth = self.headers.get("Authorization", "")
                if not token or not _secrets.compare_digest(auth, f"Bearer {token}"):
                    self.send_json({"ok": False, "error": "unauthorized"}, 401)
                    return
                agent = get_courage_agent()
                action = agent.pending.pop_by_watch_id(body.get("id", ""))
                if action and body.get("state") == "answered":
                    args = action.get("args") or {}
                    # re-derived from our own stored action, never trusted from the request:
                    # destructive actions are never approvable from the watch, no matter what it claims.
                    destructive = action.get("name") == "ha_call" and (args.get("domain"), args.get("service")) in ALWAYS_CONFIRM
                    if not destructive and body.get("r") == 0:
                        try:
                            result = agent.tools.execute(action["name"], action["args"])
                            ok = json.loads(result or "{}").get("ok", True)
                        except Exception as e:
                            ok, result = False, str(e)
                        outcome = f"Done: {action['summary']}." if ok else f"Could not {action['summary']}."
                    else:
                        outcome = f"Left alone: {action['summary']}."
                    if getattr(agent, "push", None):
                        agent.push.clear(action["id"], outcome)
                self.send_json({"ok": True})
                return

            # ── Engine Console POST API ───────────────────────────────────
            elif path == "/api/engine/reload":
                service = body.get("service", "")
                if not service:
                    self.send_json({"ok": False, "error": "Missing service name"}, 400)
                    return
                try:
                    from lodestone.engine_controller import reload_service
                    result = reload_service(service)
                    self.send_json(result)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/engine/debug-stream":
                host = body.get("host") or endpoints.host("coordinator", config)
                port = int(body.get("port", 8001))
                prompt = body.get("prompt", "")
                params = body.get("params", {})
                if not prompt:
                    self.send_json({"ok": False, "error": "Missing prompt"}, 400)
                    return
                try:
                    from lodestone.engine_controller import stream_debug_completion
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    for chunk in stream_debug_completion(host, port, prompt, params):
                        line = f"data: {json.dumps(chunk)}\n\n"
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception as e:
                    try:
                        error_line = f"data: {json.dumps({'error': str(e)})}\n\n"
                        self.wfile.write(error_line.encode("utf-8"))
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except Exception:
                        pass
                return

            elif path == "/api/profiles/save":
                prof_data = body or {}
                pid = prof_data.get("id") or f"profile_{int(time.time())}"
                profiles = load_user_profiles()
                profiles[pid] = prof_data
                ok = save_user_profiles(profiles)
                self.send_json({"ok": ok, "id": pid, "profile": prof_data})
                return

            elif path == "/api/profiles/delete":
                pid = body.get("id")
                if not pid:
                    self.send_json({"ok": False, "error": "Missing profile id"}, 400)
                    return
                profiles = load_user_profiles()
                if pid in profiles:
                    del profiles[pid]
                    save_user_profiles(profiles)
                self.send_json({"ok": True, "deleted": pid})
                return

            elif path == "/api/hardware/estimate":
                params_b = float(body.get("parameters_b", 9.0))
                weight_quant = str(body.get("weight_quant", "q4_k_m")).lower()
                kv_quant = str(body.get("kv_quant", "q4_0")).lower()
                ctx = int(body.get("context_tokens", 16384))
                slots = int(body.get("slots", 1))
                vram_limit = float(body.get("vram_limit", 12.0))

                bpw_map = {'q4_k_m': 4.5, 'q5_k_m': 5.5, 'q6_k': 6.56, 'q8_0': 8.5, 'f16': 16.0, 'q4_0': 4.0}
                kv_bpw_map = {'q4_0': 4.0, 'q8_0': 8.0, 'f16': 16.0}

                w_bpw = bpw_map.get(weight_quant, 4.5)
                k_bpw = kv_bpw_map.get(kv_quant, 4.0)

                weight_gb = (params_b * 1e9 * (w_bpw / 8.0) * 1.15) / 1e9
                layers = 48 if params_b > 20 else (36 if params_b > 10 else 28)
                hidden = 5120 if params_b > 20 else (4096 if params_b > 10 else 3072)
                kv_gb = (2 * layers * hidden * ctx * (k_bpw / 16.0) * slots) / 1e9
                act_gb = max(0.4, (ctx / 8192) * 0.35)
                total_gb = weight_gb + kv_gb + act_gb
                headroom_gb = vram_limit - total_gb

                self.send_json({
                    "ok": True,
                    "weights_gb": round(weight_gb, 2),
                    "kv_gb": round(kv_gb, 2),
                    "activations_gb": round(act_gb, 2),
                    "total_gb": round(total_gb, 2),
                    "headroom_gb": round(headroom_gb, 2),
                    "fits_in_vram": headroom_gb >= 0,
                    "overflow_gb": round(abs(headroom_gb), 2) if headroom_gb < 0 else 0.0
                })
                return

            elif path == "/api/harness/instances/select":
                inst_id = body.get("instance_id") or body.get("id")
                custom_url = body.get("url")
                cfg = load_config()
                instances = get_harness_instances()

                # If custom URL passed without ID, find or create
                if custom_url and not inst_id:
                    for inst in instances:
                        if inst.get("url", "").rstrip("/") == custom_url.rstrip("/"):
                            inst_id = inst["id"]
                            break
                    if not inst_id:
                        inst_id = f"custom_{int(time.time())}"
                        instances.append({
                            "id": inst_id,
                            "name": body.get("name", f"Node ({custom_url})"),
                            "url": custom_url,
                            "description": "User-added custom / Tailscale node"
                        })
                        cfg["harness_instances"] = instances

                # Validate ID exists
                matched = next((i for i in instances if i["id"] == inst_id), None)
                if not matched:
                    self.send_json({"ok": False, "error": f"Harness instance '{inst_id}' not found"}, 404)
                    return

                cfg["active_harness_instance"] = inst_id
                save_config(cfg)
                self.send_json({
                    "ok": True,
                    "active_id": inst_id,
                    "active_instance": matched,
                    "message": f"Active harness switched to {matched['name']}"
                })
                return

            elif path == "/api/harness/instances/add":
                inst_url = (body.get("url") or "").strip()
                if not inst_url:
                    self.send_json({"ok": False, "error": "url is required"}, 400)
                    return
                cfg = load_config()
                instances = get_harness_instances()
                inst_id = body.get("id") or f"node_{int(time.time())}"
                name = body.get("name") or f"Node ({inst_url})"
                desc = body.get("description") or "Custom Workstation / Tailscale Node"

                # Check if duplicate url
                existing = next((i for i in instances if i.get("url", "").rstrip("/") == inst_url.rstrip("/")), None)
                if existing:
                    existing["name"] = name
                    existing["description"] = desc
                else:
                    instances.append({
                        "id": inst_id,
                        "name": name,
                        "url": inst_url,
                        "description": desc
                    })

                cfg["harness_instances"] = instances
                save_config(cfg)
                self.send_json({"ok": True, "instances": instances, "added_id": inst_id})
                return

            elif path == "/api/harness/instances/delete":
                inst_id = body.get("instance_id") or body.get("id")
                if not inst_id or inst_id == "base_server":
                    self.send_json({"ok": False, "error": "Cannot delete base server instance"}, 400)
                    return
                cfg = load_config()
                instances = get_harness_instances()
                instances = [i for i in instances if i["id"] != inst_id]
                cfg["harness_instances"] = instances
                if cfg.get("active_harness_instance") == inst_id:
                    cfg["active_harness_instance"] = "base_server"
                save_config(cfg)
                self.send_json({"ok": True, "instances": instances})
                return

            elif path == "/api/harness/nudge":
                proxied = forward_to_active_harness("/api/harness/nudge", method="POST", body=body)
                if proxied is not None:
                    self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                    return
                agent_id = body.get("agent_id", "")
                directive = body.get("directive", None)
                if not agent_id:
                    self.send_json({"ok": False, "error": "agent_id required"}, 400)
                    return
                try:
                    from harness.core.nudge_tool import harness_nudge
                    res = harness_nudge.nudge(agent_id, directive=directive)
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/node/select":
                node_id = body.get("node_id") or body.get("id")
                if not node_id:
                    self.send_json({"ok": False, "error": "node_id required"}, 400)
                    return
                try:
                    from harness.config import fleet_config
                    ok = fleet_config.set_active_node(node_id)
                    self.send_json({"ok": ok, "active_node_id": fleet_config.get_active_node_id()})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
            elif path == "/api/harness/models/download":
                if not model_download_manager:
                    self.send_json({"ok": False, "error": "Downloader engine not loaded."}, 500)
                    return
                url = body.get("url", "")
                filename = body.get("filename", "")
                target_node = body.get("node_id") or body.get("target_node") or "node1_primary"
                res = model_download_manager.start_download(url, filename, target_node)
                status_code = 200 if res.get("ok") else 400
                self.send_json(res, status_code)
                return

            elif path == "/api/harness/models/download/cancel":
                if not model_download_manager:
                    self.send_json({"ok": False, "error": "Downloader engine not loaded."}, 500)
                    return
                res = model_download_manager.cancel_download()
                self.send_json(res)
                return

            elif path == "/api/harness/load":
                try:
                    from harness.edge_fleet.ally_model_manager import ally_model_manager
                    node_id = body.get("node_id", "node1_primary")
                    model_key = body.get("model_key", "")
                    if not model_key:
                        self.send_json({"ok": False, "error": "model_key is required"}, 400)
                        return
                    
                    result = ally_model_manager.load_model(
                        target_node=node_id,
                        model_key=model_key,
                        params=body,
                        context_length=body.get("context_length"),
                        parallel_slots=body.get("parallel_slots"),
                        flash_attn=body.get("flash_attn"),
                        kv_quant=body.get("kv_quant") or body.get("cache_type_k") or "f16",
                        cache_type_k=body.get("cache_type_k") or "f16",
                        cache_type_v=body.get("cache_type_v") or "f16",
                        dual_gpu_split=body.get("dual_gpu_split"),
                        timeout=140
                    )
                    status = 200 if result.get("ok") else 500
                    self.send_json(result, status)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/unload":
                try:
                    from harness.edge_fleet.ally_model_manager import ally_model_manager
                    node_id = body.get("node_id", "node1_primary")
                    result = ally_model_manager.unload_model(target_node=node_id)
                    status = 200 if result.get("ok") else 500
                    self.send_json(result, status)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/tools/execute":
                name = body.get("name", "").strip()
                arguments = body.get("arguments", {})
                ws_path = body.get("workspace_path", "")
                session_id = body.get("session_id", "")
                if not name:
                    self.send_json({"ok": False, "error": "Tool name is required."}, 400)
                    return
                if not tool_registry:
                    self.send_json({"ok": False, "error": "Tool registry not available."}, 500)
                    return
                res = tool_registry.execute_tool(name, arguments, workspace_path=ws_path)
                if session_id:
                    try:
                        from harness.data_fabric.pg_storage import relational_storage
                        relational_storage.save_message(
                            session_id=session_id,
                            role="tool",
                            content=json.dumps(res),
                            agent_id=name
                        )
                    except Exception:
                        pass
                self.send_json({"ok": True, "tool": name, "result": res})
                return

            elif path == "/api/harness/tools/ingest_url":
                url = body.get("url", "").strip()
                if not url:
                    self.send_json({"ok": False, "error": "URL parameter is required."}, 400)
                    return
                if not tool_registry:
                    self.send_json({"ok": False, "error": "Tool registry not available."}, 500)
                    return
                res = tool_registry.ingest_tool_from_url(url)
                self.send_json(res)
                return

            elif path == "/api/harness/tools/register":
                t_name = body.get("name", "").strip()
                t_desc = body.get("description", "").strip()
                t_params = body.get("parameters", {})
                t_code = body.get("python_code", "")
                t_amem = body.get("amem_card")
                if not t_name or not t_desc:
                    self.send_json({"ok": False, "error": "Tool name and description are required."}, 400)
                    return
                if not tool_registry:
                    self.send_json({"ok": False, "error": "Tool registry not available."}, 500)
                    return
                res = tool_registry.register_custom_tool(
                    name=t_name,
                    description=t_desc,
                    parameters=t_params,
                    python_code=t_code,
                    amem_card=t_amem
                )
                self.send_json(res)
                return

            elif path == "/api/harness/load":
                target_node = body.get("node_id") or "node1_primary"
                model_key = body.get("model_key") or body.get("filename") or body.get("model")
                context_length = int(body.get("context_length", 16384))
                parallel_slots = int(body.get("parallel_slots", 1))
                cache_type_k = body.get("cache_type_k") or body.get("kv_quant") or "f16"
                cache_type_v = body.get("cache_type_v") or body.get("kv_quant") or "f16"
                kv_quant = cache_type_k
                flash_attn = body.get("flash_attn", "on")
                dual_split = body.get("dual_gpu_split")

                if not model_key:
                    self.send_json({"ok": False, "error": "model_key is required"}, 400)
                    return

                try:
                    from harness.edge_fleet.ally_model_manager import ally_model_manager
                    from harness.config import fleet_config
                    fleet_config.set_active_node(target_node)
                    res = ally_model_manager.load_model(
                        target_node=target_node,
                        model_key=model_key,
                        params=body,
                        context_length=context_length,
                        parallel_slots=parallel_slots,
                        flash_attn=flash_attn,
                        kv_quant=kv_quant,
                        dual_gpu_split=dual_split
                    )
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/unload":
                target_node = body.get("node_id") or "node1_primary"
                try:
                    from harness.edge_fleet.ally_model_manager import ally_model_manager
                    from harness.config import fleet_config
                    fleet_config.set_active_node(target_node)
                    res = ally_model_manager.unload_model(target_node=target_node)
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/capacity":
                proxied = forward_to_active_harness("/api/harness/capacity", method="POST", body=body)
                if proxied is not None:
                    self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                    return
                try:
                    from harness.core.offload_calc import offload_engine
                    arch = body.get("arch_type", "9b")
                    quant = body.get("quant", "q4_k_m")
                    ctx = int(body.get("context_length", 8192))
                    slots = int(body.get("parallel_slots", 1))
                    vram = float(body.get("target_vram_gb", 12.0))
                    kv_precision = body.get("kv_precision", "q4_0")
                    custom_params_b = body.get("custom_params_b", None)
                    if custom_params_b is not None:
                        try:
                            custom_params_b = float(custom_params_b)
                        except (ValueError, TypeError):
                            custom_params_b = None
                    enforce_floor = body.get("enforce_floor", True)
                    # real geometry and VRAM when the UI names the model file / target engine
                    geo = {}
                    meta = next((m for m in system_profile.get_models(load_config())
                                 if body.get("model_file") and m.get("file") == body.get("model_file")), None)
                    if meta and isinstance(meta.get("layers"), int):
                        geo["custom_layers"] = meta["layers"]
                        if isinstance(meta.get("kv_heads"), int):
                            geo["custom_kv_heads"] = meta["kv_heads"]
                        if isinstance(meta.get("embedding"), int) and isinstance(meta.get("heads"), int) and meta["heads"]:
                            geo["custom_head_dim"] = meta["embedding"] // meta["heads"]
                        if custom_params_b is None and meta.get("params"):
                            try:
                                custom_params_b = float(str(meta["params"]).split("-")[0].rstrip("Bb"))
                            except ValueError:
                                pass
                    if "target_vram_gb" not in body:
                        eng = (system_profile.get_profile(load_config()).get("engines") or {}).get(body.get("engine", "coordinator")) or {}
                        gpu = next((g for g in system_profile.get_profile(load_config()).get("gpus") or []
                                    if eng.get("gpu") and g["index"] == eng["gpu"]["index"]), None)
                        if gpu:
                            vram = gpu["vram_total_gb"]
                    res = offload_engine.estimate(
                        arch_type=arch,
                        quant=quant,
                        context_length=ctx,
                        parallel_slots=slots,
                        kv_precision=kv_precision,
                        target_vram_gb=vram,
                        custom_params_b=custom_params_b,
                        enforce_floor=enforce_floor,
                        **geo,
                    )
                    from dataclasses import asdict
                    self.send_json({"ok": True, "estimate": asdict(res)})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/hf_search":
                proxied = forward_to_active_harness("/api/harness/hf_search", method="POST", body=body)
                if proxied is not None:
                    self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                    return
                try:
                    from harness.trainer_bridge.hf_browser import HuggingFaceBrowser
                    query = body.get("query", "qwen2.5-coder gguf")
                    browser = HuggingFaceBrowser()
                    models = browser.search_models(query=query, limit=8)
                    self.send_json({"ok": True, "models": [m.to_dict() for m in models]})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/edge/handover":
                proxied = forward_to_active_harness("/api/harness/edge/handover", method="POST", body=body)
                if proxied is not None:
                    self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                    return
                try:
                    from harness.edge_fleet.roaming_handover import roaming_protocol
                    package_dir = body.get("package_dir", "")
                    res = roaming_protocol.ingest_handover(package_dir)
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/edge/reload":
                proxied = forward_to_active_harness("/api/harness/edge/reload", method="POST", body=body)
                if proxied is not None:
                    self.send_json(proxied.get("data", {}), proxied.get("code", 200))
                    return
                try:
                    from harness.edge_fleet.ota_reload import ota_distributor
                    model_name = body.get("model_name", "")
                    artifact_url = body.get("download_url", "")
                    checksum = body.get("checksum", "")
                    quant = body.get("quant", "q4_k_m")
                    res = ota_distributor.notify_edge_nodes_new_model(
                        model_name=model_name,
                        gguf_artifact_url=artifact_url,
                        sha256_checksum=checksum,
                        target_quant=quant,
                    )
                    self.send_json({"ok": True, "results": res})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/3d/execute":
                try:
                    data = json.dumps(body).encode("utf-8")
                    req = urllib.request.Request(endpoints.url("blender", config) + "/api/v1/blender/execute", data=data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=180.0) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                    self.send_json({"ok": True, "result": res})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/3d/render":
                try:
                    data = json.dumps(body).encode("utf-8")
                    req = urllib.request.Request(endpoints.url("blender", config) + "/api/v1/blender/render", data=data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=180.0) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                    self.send_json({"ok": True, "result": res})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/3d/export":
                try:
                    data = json.dumps(body).encode("utf-8")
                    req = urllib.request.Request(endpoints.url("blender", config) + "/api/v1/blender/export", data=data, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120.0) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                    self.send_json({"ok": True, "result": res})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/3d/place_in_twin":
                model_name = body.get("model_name", "3d_asset")
                glb_url = body.get("glb_url", "")
                title = body.get("title", model_name)
                room = body.get("room", "living_room")
                spatial_path = os.path.join(ROOT_DIR, "server setup", "spatial", "property_spatial_graph.json")
                try:
                    if os.path.exists(spatial_path):
                        with open(spatial_path, "r", encoding="utf-8") as f:
                            sp_data = json.load(f)
                        if "placed_3d_assets" not in sp_data:
                            sp_data["placed_3d_assets"] = []
                        sp_data["placed_3d_assets"].append({
                            "name": model_name,
                            "title": title,
                            "glb_url": glb_url,
                            "placed_at": time.strftime("%Y-%m-%d %H:%M:%S EST"),
                            "room": room,
                            "position": [0, 0, 0]
                        })
                        with open(spatial_path, "w", encoding="utf-8") as f:
                            json.dump(sp_data, f, indent=2)
                    self.send_json({"ok": True, "message": f"Successfully placed {title} into Citadel 3D Digital Twin ({room})!"})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/harness/apply_parameters":
                harness = body.get("harness", "llama_coordinator")
                params = body.get("parameters", {})
                
                cfg = load_config()
                if "harness_settings" not in cfg:
                    cfg["harness_settings"] = {}
                if harness not in cfg["harness_settings"]:
                    cfg["harness_settings"][harness] = {}
                cfg["harness_settings"][harness].update(params)
                save_config(cfg)

                if harness in ("hermes", "moe"):
                    ok, msg = apply_llama_parameters("llama-moe.service", 8001, "moe", params)
                    self.send_json({"ok": ok, "message": msg}, 200 if ok else 400)
                    return
                elif harness in ("llama_coordinator", "coordinator"):
                    svc = "llama-moe.service" if is_moe_active() else "llama-coordinator.service"
                    target_alias = "moe" if is_moe_active() else "coordinator"
                    ok, msg = apply_llama_parameters(svc, 8001, target_alias, params)
                    self.send_json({"ok": ok, "message": msg}, 200 if ok else 400)
                    return
                elif harness in ("llama_worker", "worker"):
                    ok, msg = apply_llama_parameters("llama-worker.service", 8002, "worker", params)
                    self.send_json({"ok": ok, "message": msg}, 200 if ok else 400)
                    return
                else:
                    self.send_json({"ok": True, "message": f"{harness} configuration updated successfully!"})
                    return

            elif path == "/api/harness/profiles":
                action = body.get("action", "save")
                name = (body.get("name") or "").strip()
                cfg = load_config()
                if "harness_profiles" not in cfg:
                    cfg["harness_profiles"] = {}

                if action == "delete":
                    if name and name in cfg["harness_profiles"]:
                        del cfg["harness_profiles"][name]
                        save_config(cfg)
                    self.send_json({"ok": True, "profiles": cfg["harness_profiles"]})
                    return

                # Default: save profile
                profile_data = body.get("profile", {})
                if not name:
                    idx = 1
                    while f"profile{idx}" in cfg["harness_profiles"]:
                        idx += 1
                    name = f"profile{idx}"
                cfg["harness_profiles"][name] = profile_data
                save_config(cfg)
                self.send_json({"ok": True, "name": name, "profiles": cfg["harness_profiles"]})
                return

            elif path == "/api/harness/profiles/active":
                requested = (body.get("profile") or "").strip()
                cfg = load_config()
                if "harness_profiles" not in cfg:
                    cfg["harness_profiles"] = {}

                if not requested or requested.lower() in ("none", "null", "false", "default"):
                    cfg["active_harness_profile"] = None
                    cfg["sampling"] = {
                        "temperature": 0.68,
                        "min_p": 0.06,
                        "presence_penalty": 0.25,
                        "repeat_penalty": 1.12
                    }
                    save_config(cfg)
                    self.send_json({
                        "ok": True,
                        "active_profile": None,
                        "message": "Deselected multi-agent concurrency profile. Restored default dual-accelerator sampling."
                    })
                    return
                else:
                    if requested not in cfg["harness_profiles"]:
                        self.send_json({"ok": False, "error": f"Profile '{requested}' not found in harness profiles."}, 404)
                        return
                    profile_cfg = cfg["harness_profiles"][requested]
                    cfg["active_harness_profile"] = requested
                    cfg["sampling"] = {
                        "temperature": float(profile_cfg.get("temperature", 0.65)),
                        "min_p": float(profile_cfg.get("min_p", 0.07)),
                        "presence_penalty": float(profile_cfg.get("presence_penalty", 0.25)),
                        "repeat_penalty": float(profile_cfg.get("repeat_penalty", 1.15))
                    }
                    save_config(cfg)
                    self.send_json({
                        "ok": True,
                        "active_profile": requested,
                        "profile_data": profile_cfg,
                        "message": f"Activated 24/7 Hive Mind profile '{requested}' (8 parallel slots, 4-bit KV, context-shift, dynamic Min-P 0.07)."
                    })
                    return


            elif path == "/api/garden/zone/control":
                try:
                    zone = body.get("zone", "")
                    action = body.get("action", "open")
                    duration = int(body.get("duration_minutes", 15))
                    from harness.core.landscape_garden_engine import landscape_garden_engine
                    if action == "open":
                        res = landscape_garden_engine.open_zone(zone, duration_minutes=duration)
                    elif action == "close_all":
                        res = landscape_garden_engine.close_all_zones()
                    else:
                        res = landscape_garden_engine.close_zone(zone)
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/garden/rain_delay":
                try:
                    enabled = bool(body.get("enabled", True))
                    from harness.core.landscape_garden_engine import landscape_garden_engine
                    res = landscape_garden_engine.set_rain_delay(enabled)
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/garden/close_all":
                try:
                    from harness.core.landscape_garden_engine import landscape_garden_engine
                    res = landscape_garden_engine.close_all_zones()
                    self.send_json(res)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/agent_dna/backup":
                try:
                    agent_id = body.get("agent_id")
                    from harness.core.cloud_backup_fabric import cloud_backup_fabric
                    if agent_id:
                        res = cloud_backup_fabric.execute_full_backup(agent_id)
                    else:
                        res = cloud_backup_fabric.backup_all_agents()
                    self.send_json({"ok": True, "result": res})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/agent_dna/save":
                try:
                    agent_data = body.get("agent_data", {})
                    from harness.core.agent_dna import agent_dna_manager
                    fp = agent_dna_manager.save_agent(agent_data)
                    self.send_json({"ok": True, "path": fp})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/watchdog/reset":
                GLOBAL_WATCHDOG.reset()
                self.send_json({"ok": True, "message": "Watchdog alert cleared. Re-armed."})
                return

            elif path == "/api/watchdog/simulate":
                phrase = body.get("phrase", "beam_orig_shapes")
                event = GLOBAL_WATCHDOG.record_intercept(phrase, model="coordinator")
                self.send_json({"ok": True, "intercept": event})
                return

            elif path == "/api/dataset/compile":
                limit = int(body.get("limit", 25))
                res = dataset_compiler.run_compilation(limit=limit)
                self.send_json(res)
                return

            elif path == "/api/dataset/stop":
                res = dataset_compiler.stop_compilation()
                self.send_json(res)
                return

            elif path == "/api/trainer/ingest/sleep":
                limit = int(body.get("limit", 50))
                self.send_json(trainer_client.ingest_sleep_dossiers(limit=limit))
                return

            elif path == "/api/trainer/ingest/url":
                url = body.get("url", "")
                self.send_json(trainer_client.ingest_url(url=url))
                return

            elif path == "/api/trainer/curation/review":
                action = body.get("action", "")
                sample_id = body.get("sample_id")
                notes = body.get("notes", "")
                res = trainer_client.review_curation(action=action, sample_id=sample_id, notes=notes)
                if sample_id and action in ("approve_sample", "quarantine_sample"):
                    sync_qdrant_dossier_review(sample_id, action, notes)
                self.send_json(res)
                return

            elif path == "/api/hivemind/dossier/review":
                d_id = body.get("dossier_id", "")
                action = body.get("action", "")
                notes = body.get("notes", "")
                if not d_id or action not in ("approve", "quarantine"):
                    self.send_json({"ok": False, "error": "Invalid dossier ID or action"}, 400)
                    return
                qd_ok = sync_qdrant_dossier_review(d_id, action, notes)
                try:
                    act_name = "approve_sample" if action == "approve" else "quarantine_sample"
                    trainer_client.review_curation(action=act_name, sample_id=d_id, notes=notes)
                except Exception:
                    pass
                self.send_json({"ok": True, "dossier_id": d_id, "action": action, "qdrant_updated": qd_ok})
                return

            elif path == "/api/hivemind/dossier/frontier_audit":
                d_id = body.get("dossier_id", "")
                if not d_id:
                    self.send_json({"ok": False, "error": "Missing dossier ID"}, 400)
                    return
                
                if d_id in ("all_pending", "audit_all"):
                    cfg = load_config()
                    qdrant_u = endpoints.url("qdrant", cfg).rstrip("/")
                    pending_ids = []
                    try:
                        scroll_req = urllib.request.Request(
                            f"{qdrant_u}/collections/autonomous_thinking/points/scroll",
                            data=json.dumps({"limit": 100, "with_payload": True, "with_vector": False}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST"
                        )
                        with urllib.request.urlopen(scroll_req, timeout=8) as resp:
                            pts = json.loads(resp.read().decode("utf-8")).get("result", {}).get("points", [])
                            pending_ids = [
                                (p.get("payload", {}).get("exploration_id") or str(p.get("id")))
                                for p in pts
                                if not p.get("payload", {}).get("frontier_verified") and not p.get("payload", {}).get("quarantined")
                            ]
                    except Exception:
                        pass
                    
                    results = []
                    for pid in pending_ids[:10]:
                        res = execute_frontier_audit_for_dossier(pid)
                        results.append(res)
                    self.send_json({"ok": True, "audited_count": len(results), "results": results})
                    return
                else:
                    res = execute_frontier_audit_for_dossier(d_id)
                    self.send_json(res)
                    return


            elif path == "/api/trainer/train":
                mode = body.get("mode", "sft")
                target_model = body.get("target_model", "ornith-1.5-9b")
                steps = int(body.get("steps", 40))
                approved = bool(body.get("approved", False))
                self.send_json(trainer_client.start_training(mode=mode, target_model=target_model, steps=steps, approved=approved))
                return

            elif path == "/api/trainer/stop":
                self.send_json(trainer_client.stop_training())
                return

            elif path == "/api/trainer/dryrun":
                self.send_json(trainer_client.run_dryrun())
                return

            elif path == "/api/trainer/invariants":
                self.send_json(trainer_client.run_golden_invariants())
                return

            elif path == "/api/trainer/export":
                quant_target = body.get("quant_target", "q4_k_m")
                self.send_json(trainer_client.export_gguf(quant_target=quant_target))
                return

            elif path == "/api/trainer/promote":
                target_role = body.get("target_role", "worker")
                user_approval = bool(body.get("user_approval", False))
                self.send_json(trainer_client.promote_model(target_role=target_role, user_approval=user_approval))
                return

            elif path == "/api/trainer/passdown/feed":
                self.send_json(trainer_client.feed_passdown())
                return

            elif path.startswith("/api/boost"):
                self._handle_boost_post(path, body)
                return

            elif path == "/api/config":
                from lodestone import config_mask
                body = config_mask.drop_masked(body)
                cfg = load_config()
                deep_update(cfg, body)
                # Auto-parse proxmox token_value if provided
                raw_tok = cfg.get("proxmox", {}).get("token_value", "").strip()
                if raw_tok:
                    clean_tok = raw_tok
                    if clean_tok.startswith("PVEAPIToken="):
                        clean_tok = clean_tok[len("PVEAPIToken="):].strip()
                    if "=" in clean_tok:
                        tid, tsec = clean_tok.split("=", 1)
                        for n in proxmox_node_keys(cfg):
                            if n in cfg.get("proxmox", {}):
                                cfg["proxmox"][n]["token_id"] = tid.strip()
                                cfg["proxmox"][n]["token_secret"] = tsec.strip()
                save_config(cfg)
                # Re-initialize clients with updated config
                global proxmox, cluster
                proxmox = ProxmoxClient(cfg.get("proxmox", {}))
                cluster = ClusterClient(cfg)
                saved = app_ctx.hook("config_saved")    # apps refresh their own clients (Home: Home Assistant, Immich)
                if saved is not None:
                    saved(cfg)
                self.send_json({"ok": True, "message": "Configuration saved successfully."})
                return

            elif path == "/api/task_routing":
                cfg = load_config()
                routing = body.get("task_routing", body)
                if "task_routing" not in cfg:
                    cfg["task_routing"] = {}
                cfg["task_routing"].update(routing)
                save_config(cfg)
                self.send_json({"ok": True, "task_routing": cfg["task_routing"], "message": "Task routing updated successfully."})
                return

            elif path in ("/api/gemini_web/configure", "/api/gemini_web/cookies"):
                cfg = load_config()
                psid = body.get("psid", "").strip()
                psidts = body.get("psidts", "").strip()
                if "gemini_web" not in cfg:
                    cfg["gemini_web"] = {}
                if psid:
                    cfg["gemini_web"]["psid"] = psid
                if psidts:
                    cfg["gemini_web"]["psidts"] = psidts
                save_config(cfg)
                
                # Sync cookies directly to Gemini Web Bridge on Port 8087
                is_valid = False
                bridge_msg = "Saved locally"
                try:
                    f_url = endpoints.url("gemini_web", config) + "/api/cookies"
                    req = urllib.request.Request(
                        f_url,
                        data=json.dumps({"psid": psid, "psidts": psidts}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=10.0) as resp:
                        res_data = json.loads(resp.read().decode("utf-8"))
                        is_valid = bool(res_data.get("ok") and res_data.get("session_active"))
                        bridge_msg = res_data.get("message", "Session verified")
                except Exception as ex:
                    bridge_msg = f"Cookie sync error: {ex}"
                    is_valid = False
                
                self.send_json({"ok": is_valid, "session_active": is_valid, "message": bridge_msg})
                return

            elif path == "/api/hivemind/vigilance":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "trigger_home_vigilance_sweep"}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                        self.send_json({"ok": True, "result": json.loads(raw_txt)})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/hivemind/cycle":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                domain = body.get("domain")
                prompt = body.get("prompt")
                args = {}
                if domain: args["domain"] = domain
                if prompt: args["seed_prompt"] = prompt
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_thinking_cycle", "arguments": args}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                        self.send_json({"ok": True, "result": json.loads(raw_txt)})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/hivemind/toggle":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                action = body.get("action", "start")
                t_name = "start_autonomous_thinking" if action == "start" else "stop_autonomous_thinking"
                t_args = {"interval_seconds": body.get("interval_seconds", 120)} if action == "start" else {}
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": t_name, "arguments": t_args}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                        self.send_json({"ok": True, "message": raw_txt})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/cluster/context_mode":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                ctx_mode = body.get("context_mode", "deep_32k_ram")
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "configure_cluster_context", "arguments": {"context_mode": ctx_mode}}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=90) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                        parsed = json.loads(raw_txt) if raw_txt.strip().startswith("{") else {"ok": True, "message": raw_txt}
                        self.send_json(parsed)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/agent/nudge":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                agent_id = body.get("agent_id", "engine")
                directive = body.get("directive")
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nudge_agent", "arguments": {"agent_id": agent_id, "directive": directive}}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "{}")
                        self.send_json({"ok": True, "result": json.loads(raw_txt)})
                except Exception as ex:
                    # Seamless fallback to local harness nudge engine
                    try:
                        from harness.core.nudge_tool import harness_nudge
                        local_res = harness_nudge.nudge(agent_id, directive=directive)
                        self.send_json(local_res)
                    except Exception:
                        self.send_json({"ok": False, "error": str(ex)}, 500)
                    return


            elif path == "/api/agent/delete":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                agent_id = body.get("agent_id")
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_active_agent", "arguments": {"agent_id": agent_id}}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                    self.send_json({"ok": True, "message": raw_txt})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path in ["/api/hivemind/agent_broadcast", "/api/agent/broadcast"]:
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                channel = body.get("channel", "agora")
                message = body.get("message", "").strip()
                agent_name = body.get("agent_name", "Operator (StoneSage)")
                agent_id = body.get("agent_id", "OPERATOR")
                if not message:
                    self.send_json({"ok": False, "error": "Message cannot be empty."}, 400)
                    return
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "broadcast_to_assembly",
                            "arguments": {
                                "channel": channel,
                                "message": message,
                                "agent_name": agent_name,
                                "agent_id": agent_id
                            }
                        }
                    }
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                        parsed_res = json.loads(raw_txt) if raw_txt.startswith("{") else {"raw": raw_txt}
                        self.send_json({"ok": True, "result": parsed_res})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/agent/stop":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                agent_id = body.get("agent_id")
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "stop_background_agent", "arguments": {"agent_id": agent_id}}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        raw_txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                        self.send_json({"ok": True, "message": raw_txt})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/agent/reproduce":
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                p_args = {
                    "parent_a_id": body.get("parent_a_id"),
                    "parent_b_id": body.get("parent_b_id"),
                    "blend_ratio": body.get("blend_ratio", 0.5),
                    "focus_intent": body.get("focus_intent"),
                    "custom_name": body.get("custom_name"),
                    "custom_role": body.get("custom_role"),
                    "custom_mission": body.get("custom_mission"),
                    "custom_system_prompt": body.get("custom_system_prompt"),
                    "custom_focus_question": body.get("custom_focus_question"),
                    "model_preference": body.get("model_preference", "coordinator")
                }
                # Remove None entries
                p_args = {k: v for k, v in p_args.items() if v is not None}
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "reproduce_blended_agent", "arguments": p_args}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        if "error" in res:
                            err_msg = res["error"].get("message", str(res["error"])) if isinstance(res["error"], dict) else str(res["error"])
                            self.send_json({"ok": False, "error": err_msg}, 400)
                            return
                        content_list = res.get("result", {}).get("content", [])
                        raw_txt = content_list[0].get("text", "{}") if content_list else "{}"
                        try:
                            parsed_res = json.loads(raw_txt)
                        except Exception:
                            parsed_res = {"message": raw_txt}
                        self.send_json({"ok": True, "result": parsed_res})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/services/save":
                svc = body.get("service") or body
                sid = svc.get("id", "").strip()
                name = svc.get("name", "").strip()
                if not sid and name:
                    sid = name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
                    sid = "".join(c for c in sid if c.isalnum() or c == "_")
                    svc["id"] = sid
                if not sid:
                    self.send_json({"ok": False, "error": "Missing service id or name"}, 400)
                    return

                cfg = load_config()
                services = cfg.get("services", [])
                found = False
                for i, existing in enumerate(services):
                    if existing.get("id") == sid:
                        services[i] = {**existing, **svc}
                        found = True
                        break
                if not found:
                    services.append(svc)
                cfg["services"] = services
                save_config(cfg)
                self.send_json({"ok": True, "message": "Service saved successfully", "service": svc, "services": services})
                return

            elif path == "/api/services/delete":
                sid = body.get("id", "").strip()
                if not sid:
                    self.send_json({"ok": False, "error": "Missing service id"}, 400)
                    return
                cfg = load_config()
                services = [s for s in cfg.get("services", []) if s.get("id") != sid]
                cfg["services"] = services
                save_config(cfg)
                self.send_json({"ok": True, "message": "Service deleted successfully", "services": services})
                return

            elif path == "/api/services/reorder":
                ordered_ids = body.get("service_ids", [])
                if not ordered_ids:
                    self.send_json({"ok": False, "error": "Missing service_ids array"}, 400)
                    return
                cfg = load_config()
                services = cfg.get("services", [])
                id_map = {s.get("id"): s for s in services}
                new_services = []
                for sid in ordered_ids:
                    if sid in id_map:
                        new_services.append(id_map.pop(sid))
                for s in services:
                    if s.get("id") in id_map:
                        new_services.append(s)
                cfg["services"] = new_services
                save_config(cfg)
                self.send_json({"ok": True, "message": "Services reordered successfully", "services": new_services})
                return

            elif path == "/api/cluster/chat":
                target = body.get("target", "coordinator")
                messages = list(body.get("messages", []))
                params = body.get("params", {})
                session_id = body.get("session_id")
                ws_path = body.get("workspace_path", "")

                # Load project agent if linked to workspace, or use dynamic context fabric
                project_agent = None
                agent_id = body.get("agent_id") or body.get("agent") or "coordinator"

                # Support shorthand 'message' or 'prompt' if messages list is empty
                if not messages:
                    raw_msg = body.get("message") or body.get("prompt")
                    if raw_msg:
                        messages = [{"role": "user", "content": raw_msg}]

                # Auto-route target based on agent preference when not explicitly chosen
                if "target" not in body or body["target"] in ("coordinator", "default", ""):
                    b_agent = next((a for a in BUILTIN_AGENTS if a["id"] == agent_id or agent_id in a.get("aliases", [])), None)
                    if b_agent and b_agent.get("preferred_model"):
                        target = b_agent["preferred_model"]
                    else:
                        target = "coordinator"

                user_query = ""
                user_turns = [m for m in messages if m.get("role") == "user"]
                if user_turns:
                    user_query = user_turns[-1].get("content", "")

                # Courage runs its own tool loop: no keyword grounding, no keyword-triggered device actions
                if is_courage_agent(agent_id) and config.get("courage", {}).get("tool_loop", True):
                    self._handle_courage_chat(messages, session_id, agent_id)
                    return

                # Inject agent persona if an agent is selected
                agent_system_prompt = body.get("agent_system_prompt")
                if not agent_system_prompt and agent_id and agent_id not in ["coordinator", "worker", "moe", "none"]:
                    b_agent = next((a for a in BUILTIN_AGENTS if a["id"] == agent_id or agent_id in a.get("aliases", [])), None)
                    if b_agent:
                        agent_system_prompt = b_agent["system_prompt"]
                        if "target" not in body and b_agent.get("preferred_model"):
                            target = b_agent["preferred_model"]
                    else:
                        c_agent = next((a for a in load_custom_agents() if a.get("id") == agent_id), None)
                        if c_agent:
                            agent_system_prompt = c_agent.get("system_prompt", "")

                if agent_system_prompt:
                    found_sys = False
                    for m in messages:
                        if m.get("role") == "system":
                            m["content"] = agent_system_prompt + "\n\n" + m.get("content", "")
                            found_sys = True
                            break
                    if not found_sys:
                        messages.insert(0, {"role": "system", "content": agent_system_prompt})

                memory_badges = []
                # Boost (free cloud sources): no A-MEM, workspace or hardware grounding is injected here. Home context
                # may only reach some tiers, and stream_chat's router decides the tier after the final egress scan.
                boost_target = str(target).lower().startswith("boost")
                if boost_target:
                    params = dict(params, boost_surface="workspaces" if ws_path else "chat")
                    if not any(m.get("role") == "system" for m in messages):
                        messages.insert(0, {"role": "system", "content": "You are StoneSage AI, a homelab and coding copilot. Be concise and accurate."})
                if not boost_target:
                    try:
                        has_sys = any(m.get("role") == "system" for m in messages)
                        try:
                            from harness.core.context_fabric import context_fabric
                            if ws_path:
                                from harness.core.openclaw_engine import openclaw_engine
                                project_agent = openclaw_engine.load_project_agent(ws_path)
                                if project_agent:
                                    agent_id = project_agent.get("agent_id", agent_id)
                                    compiled_sp = openclaw_engine.compile_agent_system_prompt(ws_path, user_query=user_query)
                                    if has_sys:
                                        for m in messages:
                                            if m.get("role") == "system":
                                                m["content"] = compiled_sp + "\n\n" + m.get("content", "")
                                                break
                                    else:
                                        messages.insert(0, {"role": "system", "content": compiled_sp})
                                        has_sys = True

                            if not has_sys:
                                dyn_sp = context_fabric.compile_dynamic_turn(user_query=user_query, agent_id=agent_id if agent_id != "coordinator" else None)
                                messages.insert(0, {"role": "system", "content": dyn_sp})
                                has_sys = True
                            else:
                                try:
                                    from harness.data_fabric.valkey_amem import valkey_amem
                                    amem_atom = valkey_amem.format_injection_header(user_query)
                                    if amem_atom:
                                        memory_badges.append("A-MEM: In-RAM Working Memory")
                                        for m in messages:
                                            if m.get("role") == "system":
                                                m["content"] = m.get("content", "") + f"\n\n{amem_atom}"
                                                break
                                except Exception:
                                    pass
                        except (ImportError, ModuleNotFoundError):
                            if not has_sys:
                                messages.insert(0, {
                                    "role": "system",
                                    "content": "You are StoneSage AI, an uncensored, high-precision homelab cognitive assistant and expert coder."
                                })
                    except Exception as ex:
                        logger.debug(f"Dynamic prompt injection error: {ex}")

                # Real-time hardware sensory, wildlife ledger and Home Assistant grounding
                try:
                    hw_context = "" if boost_target else ground_hardware_context(agent_id, user_query)
                    if hw_context:
                        if "Thermostat" in hw_context:
                            memory_badges.append("Hardware: Nest Thermostat Live Telemetry")
                        if "WILDLIFE" in hw_context or "FaunaSentinel" in hw_context:
                            memory_badges.append("Hardware: 24/7 Wildlife Sentry Ledger")
                        if "PHYSICAL TAPO CAMERAS" in hw_context:
                            memory_badges.append("Hardware: Tapo Camera Stream Endpoints")
                        if "LIVE OPTICAL PERCEPTION FEED" in hw_context:
                            memory_badges.append("Hardware: Real-Time Camera Perception")
                        found_sys = False
                        for m in messages:
                            if m.get("role") == "system":
                                m["content"] = m.get("content", "") + f"\n\n{hw_context}"
                                found_sys = True
                                break
                        if not found_sys:
                            messages.insert(0, {"role": "system", "content": hw_context})
                except Exception as ex:
                    logger.debug(f"Hardware grounding error: {ex}")

                # Deterministic Whitelisted Action Execution (e.g. thermostat, lighting, switches)
                try:
                    action = app_ctx.hook("chat_action")   # a house order in plain words (Home: whitelisted HA call)
                    done = action(user_query) if action is not None else None
                    if done:
                        action_note, badge = done
                        if badge:
                            memory_badges.append(badge)
                        # Inject action execution note into system prompt
                        found_s = False
                        for m in messages:
                            if m.get("role") == "system":
                                m["content"] = m.get("content", "") + f"\n\n{action_note}"
                                found_s = True
                                break
                        if not found_s:
                            messages.insert(0, {"role": "system", "content": action_note})
                except Exception as ex:
                    logger.error(f"Chat action execution error: {ex}")

                # Conversational Memory / Obsidian Ingestion Intent
                try:
                    q_lower = (user_query or "").lower().strip()
                    images_attached = [img for img in body.get("images", []) if img]
                    if not images_attached:
                        for m in reversed(messages):
                            if m.get("role") == "user" and m.get("images"):
                                images_attached = m["images"]
                                break

                    url_match = re.search(r"(?:store|save|remember|clip|ingest)\s+(?:this\s+)?(?:url|link|article|page|webpage)?\s*:?\s*(https?://[^\s]+)", user_query, re.IGNORECASE)
                    is_photo_intent = any(k in q_lower for k in ["save this photo", "store this photo", "remember this photo", "save this picture", "store this picture", "remember this picture", "remember this image", "save this image", "store this image", "capture this photo"])

                    if url_match:
                        target_url = url_match.group(1).rstrip(".,;!>")
                        from stonesage.obsidian_ingest import obsidian_ingest
                        ingest_res = obsidian_ingest.ingest_url(url=target_url, database="ai_obsidian")
                        if ingest_res.get("ok"):
                            memory_badges.append(f"Obsidian: {ingest_res['title'][:22]}")
                            ingest_note = (
                                f"### [VERIFIED MEMORY INGESTION RECORD]:\n"
                                f"- Action: Web URL captured into Obsidian database `ai_obsidian`\n"
                                f"- Note Title: `{ingest_res['title']}`\n"
                                f"- File Path: `{ingest_res['path']}`\n"
                                f"- Summary: {ingest_res['summary']}\n"
                                f"- Indexed: Embedded into Qdrant `obsidian_brain` and stored in Valkey A-MEM.\n"
                                f"INSTRUCTION: Inform {_operator_name()} with a crisp confirmation that this URL has been captured into the AI Obsidian database and indexed into long-term memory."
                            )
                            found_s = False
                            for m in messages:
                                if m.get("role") == "system":
                                    m["content"] = m.get("content", "") + f"\n\n{ingest_note}"
                                    found_s = True
                                    break
                            if not found_s:
                                messages.insert(0, {"role": "system", "content": ingest_note})

                    elif is_photo_intent and images_attached:
                        from stonesage.obsidian_ingest import obsidian_ingest
                        ingest_res = obsidian_ingest.ingest_photo(image_data=images_attached[0], prompt_hint=user_query, database="ai_obsidian")
                        if ingest_res.get("ok"):
                            memory_badges.append(f"Obsidian Photo: {ingest_res['title'][:22]}")
                            ingest_note = (
                                f"### [VERIFIED PHOTO INGESTION RECORD]:\n"
                                f"- Action: Photo captured into Obsidian database `ai_obsidian`\n"
                                f"- Note Title: `{ingest_res['title']}`\n"
                                f"- Image File: `{ingest_res['image_file']}`\n"
                                f"- Visual Description: {ingest_res['vision_analysis'][:180]}...\n"
                                f"- Indexed: Embedded into Qdrant `obsidian_brain` and stored in Valkey A-MEM.\n"
                                f"INSTRUCTION: Inform {_operator_name()} that the photo has been analyzed by the vision model, saved as an Obsidian note, and indexed into visual memory."
                            )
                            found_s = False
                            for m in messages:
                                if m.get("role") == "system":
                                    m["content"] = m.get("content", "") + f"\n\n{ingest_note}"
                                    found_s = True
                                    break
                            if not found_s:
                                messages.insert(0, {"role": "system", "content": ingest_note})

                    else:
                        note_match = re.search(r"^(?:please\s+)?(?:store|save|remember|capture)\s+(?:this\s+)?(?:note|fact|thought|memory|snippet|information)?\s*:?\s*(.+)$", user_query, re.IGNORECASE | re.DOTALL)
                        if note_match and not url_match and not is_photo_intent:
                            captured_text = note_match.group(1).strip()
                            if len(captured_text) > 5:
                                from stonesage.obsidian_ingest import obsidian_ingest
                                ingest_res = obsidian_ingest.ingest_snippet(content=captured_text, database="ai_obsidian")
                                if ingest_res.get("ok"):
                                    memory_badges.append(f"Obsidian Note: {ingest_res['title'][:20]}")
                                    ingest_note = (
                                        f"### [VERIFIED NOTE INGESTION RECORD]:\n"
                                        f"- Action: Text note captured into Obsidian database `ai_obsidian`\n"
                                        f"- Note Title: `{ingest_res['title']}`\n"
                                        f"- File Path: `{ingest_res['path']}`\n"
                                        f"- Content: {captured_text}\n"
                                        f"- Indexed: Embedded into Qdrant `obsidian_brain` and stored in Valkey A-MEM.\n"
                                        f"INSTRUCTION: Inform {_operator_name()} with a crisp confirmation that this note has been stored into the AI Obsidian database and indexed into long-term memory."
                                    )
                                    found_s = False
                                    for m in messages:
                                        if m.get("role") == "system":
                                            m["content"] = m.get("content", "") + f"\n\n{ingest_note}"
                                            found_s = True
                                            break
                                    if not found_s:
                                        messages.insert(0, {"role": "system", "content": ingest_note})
                except Exception as ex:
                    logger.debug(f"Chat memory ingestion intent error: {ex}")

                if memory_badges:
                    params["memory_badges"] = memory_badges

                # Signal interactive user activity to cluster to pause background cognitive loop immediately
                try:
                    cluster.signal_preemption(reason="interactive_chat", in_flight=True)
                except Exception:
                    pass

                # If session_id provided, record latest user turn into database
                if session_id and messages:
                    try:
                        from harness.data_fabric.pg_storage import relational_storage
                        user_turns = [m for m in messages if m.get("role") == "user"]
                        if user_turns:
                            last_u = user_turns[-1]
                            relational_storage.save_message(
                                session_id=session_id,
                                role="user",
                                content=last_u.get("content", ""),
                                images=last_u.get("images", []),
                                agent_id=agent_id
                            )
                    except Exception as ex:
                        logger.debug(f"Error saving user message to session {session_id}: {ex}")

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                full_assistant_content = []
                full_thought_content = []
                accumulated_tool_events = []
                metrics = {}

                # 24/7 Server-side state tracking
                active_session_state.start(prompt=user_query, model=target, agent_id=agent_id, session_id=session_id)
                if memory_badges:
                    active_session_state.set_tool(", ".join(memory_badges))

                client_disconnected = False
                in_think_block = False

                try:
                    for chunk in cluster.stream_chat(target, messages, params):
                        if active_session_state._abort_requested:
                            break

                        active_session_state.append_event(chunk)

                        if not client_disconnected:
                            try:
                                self.wfile.write(chunk.encode("utf-8"))
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                client_disconnected = True

                        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                            try:
                                parsed_chunk = json.loads(chunk[6:].strip())
                                if parsed_chunk.get("type") in ("tool_call", "tool_result"):
                                    accumulated_tool_events.append(parsed_chunk)
                                    t_name = parsed_chunk.get("name", "tool")
                                    if parsed_chunk.get("type") == "tool_call":
                                        active_session_state.set_tool(f"Calling tool: {t_name}")
                                    else:
                                        active_session_state.set_tool(f"Executed tool: {t_name}")

                                d = parsed_chunk.get("delta")
                                if not d and "choices" in parsed_chunk and len(parsed_chunk["choices"]) > 0:
                                    d = parsed_chunk["choices"][0].get("delta", {})
                                if d:
                                    if "content" in d and d["content"]:
                                        tok = d["content"]
                                        full_assistant_content.append(tok)
                                        if "<think>" in tok:
                                            in_think_block = True
                                        if in_think_block:
                                            active_session_state.ingest_token(tok, is_reasoning=True)
                                        else:
                                            active_session_state.ingest_token(tok, is_reasoning=False)
                                        if "</think>" in tok:
                                            in_think_block = False

                                    if "reasoning_content" in d and d["reasoning_content"]:
                                        rtok = d["reasoning_content"]
                                        full_thought_content.append(rtok)
                                        active_session_state.ingest_token(rtok, is_reasoning=True)

                                if "tokens_per_sec" in parsed_chunk:
                                    metrics["tokens_per_sec"] = parsed_chunk["tokens_per_sec"]
                                if "model" in parsed_chunk:
                                    metrics["model"] = parsed_chunk["model"]
                            except Exception:
                                pass

                    active_session_state.finish()
                    assistant_str = "".join(full_assistant_content)
                    thought_str = "".join(full_thought_content)

                    # Option E: Extract autonomous sentinel tags and update project DNA
                    if ws_path and assistant_str:
                        try:
                            from harness.core.openclaw_engine import openclaw_engine
                            invariants, handovers, _ = openclaw_engine.extract_sentinel_tags(assistant_str)
                            for inv in invariants:
                                openclaw_engine.append_project_invariant(ws_path, inv)
                            for ho in handovers:
                                openclaw_engine.update_project_handover(ws_path, ho)
                        except Exception as ex:
                            logger.debug(f"Error extracting sentinel tags: {ex}")

                    # Persist completed assistant response & reasoning trace for cross-device sync
                    if session_id and (assistant_str or thought_str or accumulated_tool_events):
                        try:
                            from harness.data_fabric.pg_storage import relational_storage
                            relational_storage.save_message(
                                session_id=session_id,
                                role="assistant",
                                content=assistant_str,
                                thought=thought_str,
                                tool_calls=accumulated_tool_events if accumulated_tool_events else None,
                                metrics=metrics,
                                agent_id=agent_id
                            )
                        except Exception as ex:
                            logger.debug(f"Error saving assistant turn to session {session_id}: {ex}")

                except Exception as e:
                    err_data = f"data: {json.dumps({'error': str(e)})}\n\n"
                    self.wfile.write(err_data.encode("utf-8"))
                    self.wfile.flush()
                finally:
                    try:
                        cluster.signal_preemption(reason="interactive_chat_done", in_flight=False)
                    except Exception:
                        pass
                return

            elif path == "/api/workspaces/create":
                p_name = body.get("project_name", "").strip()
                parent_root = body.get("parent_root", "").strip() or WORKSPACE_ROOT
                create_session = body.get("create_chat_session", True)
                init_agent = body.get("init_agent", True)
                agent_name = body.get("agent_name", "").strip() or f"{p_name.title()} Specialist"
                agent_role = body.get("agent_role", "").strip() or f"Dedicated Engineer for {p_name}"
                autonomy = body.get("autonomy_level", "tiered")

                if not p_name or ".." in p_name or "/" in p_name or "\\" in p_name:
                    self.send_json({"ok": False, "error": "Invalid project name. No paths or slashes allowed."}, 400)
                    return
                target_dir = os.path.normpath(os.path.join(parent_root, p_name))
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    agent_id = None
                    if init_agent:
                        from harness.core.openclaw_engine import openclaw_engine
                        safe_slug = re.sub(r'[^a-zA-Z0-9_]', '_', p_name.lower())
                        agent_id = f"agent_{safe_slug}_{int(time.time())}"
                        openclaw_engine.init_project_agent(
                            project_dir=target_dir,
                            agent_id=agent_id,
                            name=agent_name,
                            role=agent_role,
                            mission=f"Deliver long-term architectural excellence for {p_name}",
                            autonomy_level=autonomy
                        )

                    proj_meta_file = os.path.join(target_dir, ".stonesage-project.json")
                    if not os.path.exists(proj_meta_file):
                        with open(proj_meta_file, "w", encoding="utf-8") as f:
                            json.dump({
                                "name": p_name,
                                "created_at": time.time(),
                                "parent_root": parent_root.replace("\\", "/"),
                                "autonomy_level": autonomy,
                                "agent_id": agent_id,
                                "agent_name": agent_name if init_agent else None,
                                "agent_role": agent_role if init_agent else None
                            }, f, indent=2)

                    session_id = None
                    if create_session:
                        from harness.data_fabric.pg_storage import relational_storage
                        session_id = f"session_{int(time.time()*1000)}"
                        relational_storage.create_chat_session(
                            session_id=session_id,
                            title=f"Project: {p_name}",
                            workspace_path=target_dir.replace("\\", "/"),
                            permissions={"autonomy_level": autonomy, "can_write": True},
                            agent_id=agent_id or "coordinator"
                        )

                    self.send_json({
                        "ok": True,
                        "workspace_path": target_dir.replace("\\", "/"),
                        "project_name": p_name,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "agent_name": agent_name if init_agent else None
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/workspaces/agent/update":
                ws_path = body.get("workspace_path", "")
                if not ws_path or not os.path.exists(ws_path):
                    self.send_json({"ok": False, "error": "Invalid workspace path"}, 400)
                    return
                try:
                    dot_stonesage = os.path.join(ws_path, ".stonesage")
                    os.makedirs(dot_stonesage, exist_ok=True)
                    if "identity_md" in body:
                        with open(os.path.join(dot_stonesage, "IDENTITY.md"), "w", encoding="utf-8") as f:
                            f.write(body["identity_md"])
                    if "soul_md" in body:
                        with open(os.path.join(dot_stonesage, "SOUL.md"), "w", encoding="utf-8") as f:
                            f.write(body["soul_md"])
                    if "invariants_md" in body:
                        with open(os.path.join(dot_stonesage, "INVARIANTS.md"), "w", encoding="utf-8") as f:
                            f.write(body["invariants_md"])
                    if "handover_md" in body:
                        with open(os.path.join(dot_stonesage, "HANDOVER.md"), "w", encoding="utf-8") as f:
                            f.write(body["handover_md"])
                    self.send_json({"ok": True, "message": "Agent DNA updated successfully."})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/workspaces/agent/invariant":
                ws_path = body.get("workspace_path", "")
                inv_text = body.get("invariant_text", "").strip()
                if not ws_path or not inv_text:
                    self.send_json({"ok": False, "error": "Missing workspace_path or invariant_text"}, 400)
                    return
                try:
                    from harness.core.openclaw_engine import openclaw_engine
                    ok, msg = openclaw_engine.append_project_invariant(ws_path, inv_text, deduplicate=True)
                    self.send_json({"ok": ok, "message": msg})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/workspaces/agent/handover":
                ws_path = body.get("workspace_path", "")
                ho_text = body.get("handover_text", "").strip()
                if not ws_path or not ho_text:
                    self.send_json({"ok": False, "error": "Missing workspace_path or handover_text"}, 400)
                    return
                try:
                    from harness.core.openclaw_engine import openclaw_engine
                    ok = openclaw_engine.update_project_handover(ws_path, ho_text)
                    self.send_json({"ok": ok, "message": "Handover updated successfully."})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/workspaces/roots/add":
                label = body.get("label", "").strip()
                root_path = body.get("path", "").strip()
                node_id = body.get("node_id", "local")
                if not root_path:
                    self.send_json({"ok": False, "error": "Missing root path"}, 400)
                    return
                roots = get_workspace_roots()
                norm = os.path.normpath(root_path).replace("\\", "/")
                for r in roots:
                    if os.path.normpath(r.get("path", "")).replace("\\", "/") == norm:
                        r["label"] = label or r.get("label")
                        save_workspace_roots(roots)
                        self.send_json({"ok": True, "roots": roots})
                        return
                roots.append({
                    "id": f"root_{int(time.time()*1000)}",
                    "label": label or os.path.basename(norm) or norm,
                    "path": norm,
                    "node_id": node_id
                })
                save_workspace_roots(roots)
                self.send_json({"ok": True, "roots": roots})
                return

            elif path == "/api/workspaces/roots/delete":
                root_id = body.get("id")
                root_path = body.get("path")
                roots = get_workspace_roots()
                updated = [
                    r for r in roots
                    if (not root_id or r.get("id") != root_id) and (not root_path or os.path.normpath(r.get("path", "")).replace("\\", "/") != os.path.normpath(root_path).replace("\\", "/"))
                ]
                save_workspace_roots(updated)
                self.send_json({"ok": True, "roots": updated})
                return

            elif path == "/api/chat/sessions/new":
                from harness.data_fabric.pg_storage import relational_storage
                title = body.get("title", "New Conversation")
                ws_path = body.get("workspace_path", ACTIVE_WORKSPACE_DIR.replace("\\", "/"))
                node_id = body.get("node_id", "workstation_primary")
                permissions = body.get("permissions", {"autonomy_level": "tiered", "can_write": True})
                session_id = body.get("session_id") or f"session_{int(time.time()*1000)}"
                session = relational_storage.create_chat_session(
                    session_id=session_id,
                    title=title,
                    workspace_path=ws_path,
                    node_id=node_id,
                    permissions=permissions
                )
                self.send_json({"ok": True, "session": session})
                return

            elif path == "/api/chat/sessions/message":
                from harness.data_fabric.pg_storage import relational_storage
                session_id = body.get("session_id")
                role = body.get("role", "user")
                content = body.get("content", "")
                thought = body.get("thought", "")
                images = body.get("images", [])
                metrics = body.get("metrics", {})
                if not session_id:
                    self.send_json({"ok": False, "error": "Missing session_id"}, 400)
                    return
                step_idx = relational_storage.save_message(
                    session_id=session_id,
                    role=role,
                    content=content,
                    thought=thought,
                    images=images,
                    metrics=metrics
                )
                self.send_json({"ok": True, "step_index": step_idx})
                return

            elif path == "/api/chat/sessions/clear":
                from harness.data_fabric.pg_storage import relational_storage
                session_id = body.get("session_id")
                if not session_id:
                    self.send_json({"ok": False, "error": "Missing session_id"}, 400)
                    return
                relational_storage.clear_session_messages(session_id)
                self.send_json({"ok": True})
                return

            elif path == "/api/chat/sessions/delete":
                from harness.data_fabric.pg_storage import relational_storage
                session_id = body.get("session_id")
                if not session_id:
                    self.send_json({"ok": False, "error": "Missing session_id"}, 400)
                    return
                relational_storage.delete_session(session_id)
                self.send_json({"ok": True})
                return

            elif path == "/api/chat/sessions/branch":
                from harness.data_fabric.pg_storage import relational_storage
                source_session_id = body.get("source_session_id")
                target_index = body.get("target_index")
                title = body.get("title", "Branched Conversation")
                ws_path = body.get("workspace_path", ACTIVE_WORKSPACE_DIR.replace("\\", "/"))
                new_session_id = body.get("new_session_id") or f"branch_{int(time.time()*1000)}"
                messages_to_copy = body.get("messages")

                session = relational_storage.create_chat_session(
                    session_id=new_session_id,
                    title=title,
                    workspace_path=ws_path
                )

                copied_count = 0
                if messages_to_copy and isinstance(messages_to_copy, list):
                    for m in messages_to_copy:
                        relational_storage.save_message(
                            session_id=new_session_id,
                            role=m.get("role", "user"),
                            content=m.get("content", ""),
                            thought=m.get("thought", m.get("reasoning", "")),
                            images=m.get("images", []),
                            metrics=m.get("metrics", {}),
                            tool_calls=m.get("tool_calls", [])
                        )
                        copied_count += 1
                elif source_session_id:
                    src_msgs = relational_storage.get_session_messages(source_session_id)
                    limit = (int(target_index) + 1) if target_index is not None else len(src_msgs)
                    for m in src_msgs[:limit]:
                        relational_storage.save_message(
                            session_id=new_session_id,
                            role=m.get("role", "user"),
                            content=m.get("content", ""),
                            thought=m.get("thought", ""),
                            images=m.get("images", []),
                            metrics=m.get("metrics", {}),
                            tool_calls=m.get("tool_calls", [])
                        )
                        copied_count += 1

                self.send_json({"ok": True, "session": session, "copied_count": copied_count})
                return

            elif path == "/api/agent_dna/save_profile":
                from harness.core.agent_dna import agent_dna_manager
                agent_id = body.get("agent_id")
                soul_data = body.get("soul", {})
                if not agent_id:
                    self.send_json({"ok": False, "error": "Missing agent_id"}, 400)
                    return
                try:
                    updated = agent_dna_manager.update_agent_soul(agent_id, soul_data)
                    self.send_json({"ok": True, "agent": updated})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/agent_dna/cards":
                from harness.core.agent_dna import agent_dna_manager
                agent_id = body.get("agent_id")
                if not agent_id:
                    self.send_json({"ok": False, "error": "Missing agent_id"}, 400)
                    return
                
                # Check if deletion
                if "index" in body:
                    try:
                        res = agent_dna_manager.delete_fact_card(agent_id, int(body["index"]))
                        self.send_json(res)
                    except Exception as ex:
                        self.send_json({"ok": False, "error": str(ex)}, 500)
                    return
                
                card_text = body.get("card_text", "")
                if not card_text:
                    self.send_json({"ok": False, "error": "Missing card_text"}, 400)
                    return
                try:
                    res = agent_dna_manager.add_fact_card(agent_id, card_text)
                    self.send_json(res)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/cluster/arena":
                models = body.get("models", ["coordinator", "worker"])
                prompt = body.get("prompt", "")
                answers = cluster.multi_model_arena_query(models, prompt)
                self.send_json({"ok": True, "answers": answers})
                return

            elif path == "/api/cluster/synthesize":
                prompt = body.get("prompt", "")
                answers = body.get("answers", {})
                synthesis = cluster.synthesize_answers(prompt, answers)
                self.send_json({"ok": True, "synthesis": synthesis})
                return

            elif path == "/api/courage/memories/forget":
                mem = getattr(get_courage_agent(), "memory", None) if _courage_agent_if_any() else None
                self.send_json({"ok": bool(mem and mem.forget(str(body.get("id", ""))))})
                return

            elif path == "/api/courage/reflexes/forget":
                learned = getattr(get_courage_agent(), "learned", None) if _courage_agent_if_any() else None
                self.send_json({"ok": bool(learned and learned.forget(body.get("key", "")))})
                return

            elif path in ("/api/loader/plan", "/api/loader/preview", "/api/loader/apply"):
                try:
                    fn = {"/api/loader/plan": model_loader.plan, "/api/loader/preview": model_loader.preview,
                          "/api/loader/apply": model_loader.apply}[path]
                    self.send_json(fn(body))
                except Exception as e:
                    self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
                return


            elif path == "/api/engines/lease":
                # {layout, ttl_s, reason, approver, force}: waits until the layout answers (or reverted), up to ~5 min
                self.send_json(get_engine_lease().acquire(str(body.get("layout") or ""), body.get("ttl_s") or 3600,
                                                          str(body.get("reason") or ""), str(body.get("approver") or ""),
                                                          bool(body.get("force"))))
                return

            elif re.fullmatch(r"/api/engines/lease/[0-9a-f]{8}/(renew|release)", path):
                # Only a person renews (DESIGN.md: "a renew call without a human tap is refused"): the phone check-in's
                # Another hour button, through push_approvals. This LAN API can't tell a person from Claude Code or
                # the loop worker, so renew here is refused; release (giving the GPU back early) is always allowed.
                lease_id, verb = path.split("/")[4], path.split("/")[5]
                if verb == "renew":
                    self.send_json({"ok": False, "error": "Renew by tapping Another hour on the phone check-in."}, 403)
                    return
                self.send_json(get_engine_lease().release(lease_id, why="released", approver=str(body.get("approver") or "")))
                return

            elif path == "/api/provenance/scan":
                # {"hash": true} also hashes new/changed files (minutes for a big new GGUF); runs in the background
                prov = get_provenance()
                threading.Thread(target=lambda: prov.scan(hash_files=bool(body.get("hash"))), daemon=True,
                                 name="provenance-scan").start()
                self.send_json({"ok": True, "started": True, "hash": bool(body.get("hash"))})
                return


            elif path == "/api/engine-profiles/apply":
                try:
                    self.send_json(engine_profiles.apply_profile(body.get("name", "")))
                except Exception as e:
                    self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
                return

            elif path == "/api/cluster/mode/switch":
                self.send_json({"ok": False, "error": "Preset mode switching is retired: it rewrote the engine units "
                                "without review. Change an engine through its systemd unit (see STATE.md)."}, 410)
                return

            elif path == "/api/agents/create":
                name = (body.get("name") or "").strip()
                role = (body.get("role") or "").strip()
                sp = (body.get("system_prompt") or "").strip()
                icon = (body.get("icon") or "🤖").strip()
                if not name:
                    self.send_json({"ok": False, "error": "Agent name is required"}, 400)
                    return
                new_agent = {
                    "id": f"custom_{int(time.time())}_{uuid.uuid4().hex[:6]}",
                    "name": name,
                    "role": role or "Custom AI Specialist",
                    "icon": icon,
                    "system_prompt": sp,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                agents = load_custom_agents()
                agents.append(new_agent)
                save_custom_agents(agents)
                self.send_json({"ok": True, "agent": new_agent, "agents": agents})
                return

            elif path == "/api/agents/update":
                agent_id = (body.get("id") or body.get("agent_id") or "").strip()
                name = (body.get("name") or "").strip()
                role = (body.get("role") or "").strip()
                sp = (body.get("system_prompt") or "").strip()
                icon = (body.get("icon") or "").strip()
                preferred_model = (body.get("preferred_model") or "").strip()

                if not agent_id:
                    self.send_json({"ok": False, "error": "Agent ID is required"}, 400)
                    return

                # Check builtin agents
                b_match = next((b for b in BUILTIN_AGENTS if b["id"] == agent_id), None)
                if b_match:
                    if name: b_match["name"] = name
                    if role: b_match["role"] = role
                    if sp: b_match["system_prompt"] = sp
                    if icon: b_match["icon"] = icon
                    if preferred_model: b_match["preferred_model"] = preferred_model
                    self.send_json({"ok": True, "agent": b_match, "type": "builtin"})
                    return

                # Check custom agents
                agents = load_custom_agents()
                c_match = next((a for a in agents if a.get("id") == agent_id), None)
                if c_match:
                    if name: c_match["name"] = name
                    if role: c_match["role"] = role
                    if sp: c_match["system_prompt"] = sp
                    if icon: c_match["icon"] = icon
                    if preferred_model: c_match["preferred_model"] = preferred_model
                    save_custom_agents(agents)
                    self.send_json({"ok": True, "agent": c_match, "type": "custom", "agents": agents})
                    return

                self.send_json({"ok": False, "error": f"Agent '{agent_id}' not found"}, 404)
                return

            elif path == "/api/agents/delete":
                agent_id = (body.get("id") or body.get("agent_id") or "").strip()
                if not agent_id:
                    self.send_json({"ok": False, "error": "Agent ID is required"}, 400)
                    return
                agents = load_custom_agents()
                agents = [a for a in agents if a.get("id") != agent_id]
                save_custom_agents(agents)
                self.send_json({"ok": True, "agents": agents})
                return

            elif path == "/api/cluster/switch-model":
                model = body.get("model", "").strip()
                hf = body.get("hf", "").strip()
                file = body.get("file", "").strip()
                ctx = int(body.get("context", 16384))
                auto_tune = bool(body.get("auto_tune", True))

                switcher_py = os.path.join(WORKSPACE_ROOT, ".agents", "skills", "cluster-model-switcher", "switch_model.py")
                cmd_args = [sys.executable, switcher_py]
                if hf and file:
                    cmd_args.extend(["--hf", hf, "--file", file, "--context", str(ctx)])
                elif model:
                    cmd_args.extend(["--model", model, "--context", str(ctx)])
                else:
                    self.send_json({"ok": False, "error": "Missing model or hf parameters"}, 400)
                    return

                if not auto_tune:
                    cmd_args.append("--no-tune")

                try:
                    proc = subprocess.run(cmd_args, capture_output=True, text=True, timeout=600)
                    self.send_json({
                        "ok": proc.returncode == 0,
                        "output": proc.stdout + ("\n" + proc.stderr if proc.stderr else ""),
                        "returncode": proc.returncode
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/cluster/rollback-model":
                switcher_py = os.path.join(WORKSPACE_ROOT, ".agents", "skills", "cluster-model-switcher", "switch_model.py")
                try:
                    proc = subprocess.run([sys.executable, switcher_py, "--rollback"], capture_output=True, text=True, timeout=60)
                    self.send_json({
                        "ok": proc.returncode == 0,
                        "output": proc.stdout + ("\n" + proc.stderr if proc.stderr else ""),
                        "returncode": proc.returncode
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/cluster/delete-model":
                model = body.get("model", "").strip()
                safe_filename = os.path.basename(model)
                if not safe_filename or not safe_filename.endswith(".gguf"):
                    self.send_json({"ok": False, "error": "Invalid model filename. Must be a .gguf file."}, 400)
                    return
                if safe_filename in system_profile.loaded_model_files(system_profile.get_profile(load_config(), fresh=True)):
                    self.send_json({"ok": False, "error": "That model is loaded by a running engine; stop or switch the engine first."}, 403)
                    return
                try:
                    # Check active models
                    chk_cmd = endpoints.ssh_cmd(config, "grep -E -- '--model' /etc/systemd/system/llama-*.service")
                    chk_res = subprocess.run(chk_cmd, capture_output=True, text=True, timeout=10)
                    if chk_res.returncode == 0 and safe_filename in chk_res.stdout:
                        self.send_json({"ok": False, "error": f"Cannot delete '{safe_filename}' because it is currently loaded in an active service. Switch models first."}, 400)
                        return

                    del_cmd = endpoints.ssh_cmd(config, f"rm -f /opt/models/{safe_filename}")
                    del_res = subprocess.run(del_cmd, capture_output=True, text=True, timeout=15)
                    if del_res.returncode == 0:
                        self.send_json({"ok": True, "deleted": safe_filename})
                    else:
                        self.send_json({"ok": False, "error": del_res.stderr or "Failed to delete file on cluster."}, 500)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/cluster/calibrate-model":
                discover_py = os.path.join(WORKSPACE_ROOT, ".agents", "skills", "model-parameter-discoverer", "discover_parameters.py")
                model_name = body.get("model_name", "Current-Model")
                try:
                    proc = subprocess.run([sys.executable, discover_py, "--model-name", model_name, "--port", "8001"], capture_output=True, text=True, timeout=240)
                    self.send_json({
                        "ok": proc.returncode == 0,
                        "output": proc.stdout + ("\n" + proc.stderr if proc.stderr else ""),
                        "returncode": proc.returncode
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/memory/search":
                query = body.get("query", "")
                collection = body.get("collection", "codebase_knowledge")
                limit = int(body.get("limit", 5))
                results = cluster.search_memory(query, collection, limit)
                self.send_json({"ok": True, "results": results})
                return

            elif path == "/api/memory/store":
                content = body.get("content", "")
                collection = body.get("collection", "agent_memories")
                meta = body.get("metadata", {})
                point_id = cluster.store_memory(content, collection, meta)
                self.send_json({"ok": True, "point_id": point_id})
                return


            elif path == "/api/proxmox/reboot":
                node = body.get("node", "pve_node")
                guest_type = body.get("type", "lxc")
                vmid = str(body.get("vmid", ""))
                res = proxmox.reboot_guest(node, guest_type, vmid)
                self.send_json(res)
                return

            elif path == "/api/obsidian/save":
                rel_path = body.get("path", "")
                content = body.get("content", "")
                target_db = body.get("database", "ai_obsidian")
                couch_res = couchdb.save_note_doc(rel_path, content, database=target_db)
                vault_res = vault.save_note(rel_path, content)
                try:
                    from stonesage.obsidian_ingest import obsidian_ingest
                    obsidian_ingest.upsert_to_qdrant(rel_path, os.path.basename(rel_path), content, ["manual_save"])
                except Exception as ex:
                    logger.debug(f"Obsidian save indexing deferred: {ex}")
                self.send_json({"ok": True, "couchdb": couch_res, "vault": vault_res})
                return

            elif path == "/api/obsidian/capture":
                try:
                    from stonesage.obsidian_ingest import obsidian_ingest
                    c_type = body.get("type", "note").lower()
                    c_url = body.get("url") or body.get("content_or_url", "")
                    c_title = body.get("title")
                    c_tags = body.get("tags", [])
                    c_content = body.get("content") or body.get("text", "")
                    c_image = body.get("image_data") or body.get("image") or body.get("photo")
                    c_db = body.get("database", "ai_obsidian")

                    if c_type in ("url", "link", "web") or (c_url and str(c_url).startswith(("http://", "https://"))):
                        res = obsidian_ingest.ingest_url(url=c_url, title=c_title, tags=c_tags, database=c_db)
                    elif c_type in ("photo", "image") or c_image:
                        res = obsidian_ingest.ingest_photo(image_data=c_image or c_content, filename=body.get("filename"), title=c_title, tags=c_tags, prompt_hint=body.get("hint"), database=c_db)
                    else:
                        res = obsidian_ingest.ingest_snippet(content=c_content or c_url, title=c_title, tags=c_tags, language=body.get("language"), database=c_db)
                    self.send_json(res)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/obsidian/delete":
                rel_path = body.get("path", "")
                res = vault.delete_note(rel_path)
                self.send_json(res)
                return

            elif path == "/api/obsidian/sync":
                exporter_py = os.path.join(WORKSPACE_ROOT, ".agents", "skills", "database-memory-sync", "export_all_to_database.py")
                def run_vault_sync():
                    try:
                        subprocess.run([sys.executable, exporter_py, "--vault-only"], capture_output=True, text=True, check=False)
                    except Exception as e:
                        print(f"[StoneSage] Vault sync background error: {e}")
                threading.Thread(target=run_vault_sync, daemon=True).start()
                self.send_json({"ok": True, "message": "Obsidian vault to Qdrant sync started in background."})
                return

            elif path == "/api/subagents/dispatch":
                role = body.get("role", "Specialized Subagent")
                task = body.get("task", "")
                model = body.get("model", "worker")
                subagent_id = f"sub-{int(time.time())}"
                
                subagent_registry[subagent_id] = {
                    "id": subagent_id,
                    "role": role,
                    "task": task,
                    "model": model,
                    "status": "running",
                    "output": "",
                    "started_at": time.strftime("%H:%M:%S")
                }

                def run_subagent_task():
                    try:
                        chunks = []
                        for c in cluster.stream_chat(model, [{"role": "user", "content": task}], {"max_tokens": 1500}):
                            if c.startswith("data: ") and "[DONE]" not in c:
                                try:
                                    delta = json.loads(c[6:])["choices"][0]["delta"].get("content") or ""
                                    if delta:
                                        chunks.append(delta)
                                except Exception:
                                    pass
                        subagent_registry[subagent_id]["status"] = "completed"
                        subagent_registry[subagent_id]["output"] = "".join(chunks)
                        subagent_registry[subagent_id]["finished_at"] = local_time_str()
                    except Exception as e:
                        subagent_registry[subagent_id]["status"] = "error"
                        subagent_registry[subagent_id]["output"] = str(e)

                threading.Thread(target=run_subagent_task, daemon=True).start()
                self.send_json({"ok": True, "subagent_id": subagent_id, "role": role, "status": "running"})
                return

            elif path in ("/api/agents/delete", "/api/subagents/delete"):
                agent_id = body.get("agent_id", "").strip()
                if not agent_id:
                    self.send_json({"ok": False, "error": "Missing agent_id"}, 400)
                    return
                # Also remove from local registry if present
                if agent_id in subagent_registry:
                    del subagent_registry[agent_id]
                # Call cluster MCP delete_active_agent
                cfg = load_config()
                mcp_u = endpoints.url("mcp", cfg)
                try:
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_active_agent", "arguments": {"agent_id": agent_id}}}
                    req = urllib.request.Request(mcp_u, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                        self.send_json({"ok": True, "result": res})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/files/upload":
                raw_data = body.get("data_base64") or body.get("data_url") or body.get("content") or ""
                orig_name = body.get("filename") or body.get("name") or "upload.bin"
                content_type = body.get("content_type") or body.get("type") or "application/octet-stream"
                
                if "," in raw_data:
                    raw_data = raw_data.split(",", 1)[1]
                try:
                    file_bytes = base64.b64decode(raw_data)
                    ext = os.path.splitext(orig_name)[1] or ".bin"
                    file_hash = hashlib.sha256(file_bytes[:1024] + str(time.time()).encode()).hexdigest()[:12]
                    safe_name = f"{file_hash}{ext}"
                    target_path = os.path.join(UPLOADS_DIR, safe_name)
                    with open(target_path, "wb") as f:
                        f.write(file_bytes)
                    url = f"/api/uploads/{safe_name}"
                    self.send_json({
                        "ok": True,
                        "url": url,
                        "filename": orig_name,
                        "safe_name": safe_name,
                        "type": content_type,
                        "size": len(file_bytes)
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": f"Upload failed: {str(ex)}"}, 500)
                return

            elif path == "/api/workspace/save":
                req_path = body.get("path", "").strip()
                content = body.get("content", "")
                if not req_path:
                    self.send_json({"ok": False, "error": "Missing path"}, 400)
                    return
                if os.path.isabs(req_path):
                    full_path = os.path.abspath(req_path)
                else:
                    full_path = os.path.abspath(os.path.join(ACTIVE_WORKSPACE_DIR, req_path))
                try:
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    self.send_json({
                        "ok": True,
                        "path": req_path,
                        "full_path": full_path.replace("\\", "/"),
                        "size": len(content)
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/workspace/diff":
                req_path = body.get("path", "").strip()
                proposed = body.get("proposed_content") or body.get("content", "")
                if not req_path:
                    self.send_json({"ok": False, "error": "Missing path"}, 400)
                    return
                if os.path.isabs(req_path):
                    full_path = os.path.abspath(req_path)
                else:
                    full_path = os.path.abspath(os.path.join(ACTIVE_WORKSPACE_DIR, req_path))
                existing = ""
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                            existing = f.read()
                    except Exception:
                        existing = ""
                diff_lines = list(difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    proposed.splitlines(keepends=True),
                    fromfile=f"a/{req_path}",
                    tofile=f"b/{req_path}"
                ))
                additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                deletions = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
                self.send_json({
                    "ok": True,
                    "path": req_path,
                    "diff": "".join(diff_lines),
                    "additions": additions,
                    "deletions": deletions,
                    "is_new": not os.path.exists(full_path)
                })
                return

            elif path == "/api/terminal/exec":
                cmd = body.get("command", "").strip()
                cwd_req = body.get("cwd", "").strip()
                exec_cwd = ACTIVE_WORKSPACE_DIR
                if cwd_req:
                    if os.path.isabs(cwd_req) and os.path.isdir(cwd_req):
                        exec_cwd = os.path.abspath(cwd_req)
                    else:
                        target_dir = os.path.abspath(os.path.join(ACTIVE_WORKSPACE_DIR, cwd_req))
                        if os.path.isdir(target_dir):
                            exec_cwd = target_dir

                if not cmd:
                    self.send_json({"ok": False, "error": "Command string is required"}, 400)
                    return

                # Statefully handle directory changes (cd, chdir, set-location)
                cmd_lower = cmd.lower()
                if cmd_lower in ["cd", "cd ~", "cd /", "cd \\"]:
                    exec_cwd = ACTIVE_WORKSPACE_DIR
                    rel_cwd = ""
                    self.send_json({
                        "ok": True,
                        "command": cmd,
                        "cwd": rel_cwd,
                        "cwd_abs": exec_cwd,
                        "stdout": "",
                        "stderr": "",
                        "exit_code": 0
                    })
                    return
                elif cmd_lower.startswith("cd ") or cmd_lower.startswith("chdir ") or cmd_lower.startswith("set-location "):
                    parts = cmd.split(None, 1)
                    target_path = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
                    if not target_path or target_path in ["~"]:
                        new_cwd = ACTIVE_WORKSPACE_DIR
                    elif target_path in ["/", "\\"]:
                        new_cwd = "/" if os.name != "nt" else "C:\\"
                    elif target_path == "..":
                        new_cwd = os.path.dirname(exec_cwd)
                    elif os.path.isabs(target_path) and os.path.isdir(target_path):
                        new_cwd = os.path.abspath(target_path)
                    else:
                        candidate = os.path.abspath(os.path.join(exec_cwd, target_path))
                        if os.path.isdir(candidate):
                            new_cwd = candidate
                        else:
                            self.send_json({
                                "ok": False,
                                "command": cmd,
                                "cwd": os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/"),
                                "cwd_abs": exec_cwd,
                                "stderr": f"Cannot find path '{target_path}' because it does not exist.",
                                "exit_code": 1
                            })
                            return
                    exec_cwd = new_cwd
                    rel_cwd = os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/")
                    if rel_cwd == ".":
                        rel_cwd = ""
                    self.send_json({
                        "ok": True,
                        "command": cmd,
                        "cwd": rel_cwd,
                        "cwd_abs": exec_cwd,
                        "stdout": "",
                        "stderr": "",
                        "exit_code": 0
                    })
                    return

                # Coding & System Command Whitelist Validation
                is_allowed, reason = validate_terminal_command(cmd, cwd=exec_cwd)
                if not is_allowed:
                    self.send_json({
                        "ok": False,
                        "command": cmd,
                        "cwd": os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/"),
                        "cwd_abs": exec_cwd,
                        "stdout": "",
                        "stderr": f"StoneSage Security Policy: {reason}",
                        "exit_code": 126
                    })
                    return

                try:
                    shell_cmd = ["powershell", "-NoProfile", "-Command", cmd] if os.name == "nt" else ["/bin/bash", "-c", cmd]
                    proc = subprocess.run(
                        shell_cmd,
                        cwd=exec_cwd,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    rel_cwd = os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/")
                    if rel_cwd == ".":
                        rel_cwd = ""
                    self.send_json({
                        "ok": True,
                        "command": cmd,
                        "cwd": rel_cwd,
                        "cwd_abs": exec_cwd,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                        "output": (proc.stdout or "") + (proc.stderr or ""),
                        "exit_code": proc.returncode,
                        "returncode": proc.returncode
                    })
                except subprocess.TimeoutExpired:
                    rel_cwd = os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/")
                    if rel_cwd == ".":
                        rel_cwd = ""
                    self.send_json({
                        "ok": False,
                        "command": cmd,
                        "cwd": rel_cwd,
                        "cwd_abs": exec_cwd,
                        "error": "Command execution timed out (30s limit).",
                        "exit_code": -1
                    })
                except Exception as ex:
                    rel_cwd = os.path.relpath(exec_cwd, ACTIVE_WORKSPACE_DIR).replace("\\", "/")
                    if rel_cwd == ".":
                        rel_cwd = ""
                    self.send_json({
                        "ok": False,
                        "command": cmd,
                        "cwd": rel_cwd,
                        "cwd_abs": exec_cwd,
                        "error": str(ex),
                        "exit_code": -1
                    })
                return

            elif path == "/api/git/repo/select":
                repo_path = body.get("path", "").strip()
                if not repo_path:
                    self.send_json({"ok": False, "error": "Missing repository path"}, 400)
                    return
                abs_path = os.path.abspath(repo_path)
                if not os.path.exists(abs_path) or not os.path.isdir(abs_path):
                    self.send_json({"ok": False, "error": f"Directory not found: {repo_path}"}, 404)
                    return
                try:
                    toplevel = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=abs_path, text=True, stderr=subprocess.DEVNULL).strip()
                    ACTIVE_GIT_REPO_DIR = toplevel
                    self.send_json({
                        "ok": True,
                        "repo_dir": ACTIVE_GIT_REPO_DIR.replace("\\", "/"),
                        "repo_name": os.path.basename(ACTIVE_GIT_REPO_DIR)
                    })
                except Exception as ex:
                    self.send_json({"ok": False, "error": f"Not a valid git repository: {str(ex)}"}, 400)
                return

            elif path == "/api/git/clone":
                clone_url = body.get("url", "").strip()
                dest_dir = body.get("dest", "").strip()
                if not clone_url:
                    self.send_json({"ok": False, "error": "Missing repository URL"}, 400)
                    return
                if not dest_dir:
                    repo_name = clone_url.rstrip("/").split("/")[-1].replace(".git", "")
                    dest_dir = os.path.join(ACTIVE_WORKSPACE_DIR, repo_name)
                dest_abs = os.path.abspath(dest_dir)
                try:
                    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
                    proc = subprocess.run(
                        ["git", "clone", clone_url, dest_abs],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if proc.returncode == 0:
                        ACTIVE_GIT_REPO_DIR = dest_abs
                        self.send_json({
                            "ok": True,
                            "repo_dir": dest_abs.replace("\\", "/"),
                            "repo_name": os.path.basename(dest_abs),
                            "output": proc.stdout or proc.stderr
                        })
                    else:
                        self.send_json({
                            "ok": False,
                            "error": f"Git clone failed:\n{proc.stderr or proc.stdout}"
                        }, 400)
                except subprocess.TimeoutExpired:
                    self.send_json({"ok": False, "error": "Git clone timed out after 120s."}, 504)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/git/commit":
                msg = body.get("message", "").strip()
                if not msg:
                    msg = f"Update homelab workspace ({time.strftime('%Y-%m-%d %H:%M')})"
                try:
                    subprocess.run(["git", "add", "-A"], cwd=ACTIVE_GIT_REPO_DIR, check=True, capture_output=True)
                    proc = subprocess.run(["git", "commit", "-m", msg], cwd=ACTIVE_GIT_REPO_DIR, capture_output=True, text=True)
                    self.send_json({"ok": proc.returncode == 0, "message": msg, "output": proc.stdout or proc.stderr})
                except Exception as ge:
                    self.send_json({"ok": False, "error": str(ge)})
                return

            elif path == "/api/obsidian/sync_to_memory" or path == "/api/knowledge/sync":
                try:
                    res = obsidian_ingestor.sync_vault()
                    self.send_json({"ok": True, "result": res})
                except Exception as sync_err:
                    self.send_json({"ok": False, "error": str(sync_err)}, 500)
                return

            elif path == "/api/couchdb/sync":
                try:
                    notes = couchdb.list_notes()
                    self.send_json({"ok": True, "message": f"Polled CouchDB: {len(notes)} notes indexed.", "count": len(notes)})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/obsidian/sync-archive":
                try:
                    def run_sync_task():
                        ps_script = os.path.join(WORKSPACE_ROOT, "server setup", "sync_archive_to_obsidian.ps1")
                        if os.path.exists(ps_script):
                            subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_script], capture_output=True)
                    threading.Thread(target=run_sync_task, daemon=True).start()
                    self.send_json({"ok": True, "message": "Obsidian archive sync initiated in background."})
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/memory/amem/save":
                try:
                    res = save_valkey_amem_card(body)
                    self.send_json(res)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/memory/amem/delete":
                try:
                    cid = body.get("id", "")
                    res = delete_valkey_amem_card(cid)
                    self.send_json(res)
                except Exception as ex:
                    self.send_json({"ok": False, "error": str(ex)}, 500)
                return

            elif path == "/api/memory/stm/add":
                category = body.get("category", "user")
                key = body.get("key", "Note")
                val = body.get("value", "")
                item = stm.add_item(category, key, val)
                self.send_json({"ok": True, "item": item, "stm": stm.get_summary()})
                return

            elif path == "/api/memory/stm/remove":
                item_id = body.get("id", "")
                removed = stm.remove_item(item_id)
                self.send_json({"ok": True, "removed": removed, "stm": stm.get_summary()})
                return

            elif path == "/api/memory/stm/clear":
                stm.clear()
                self.send_json({"ok": True, "stm": stm.get_summary()})
                return

            elif path == "/api/memory/stm/compress":
                text = body.get("text", "")
                summary = stm.compress_with_worker(text)
                self.send_json({"ok": True, "compressed": summary, "stm": stm.get_summary()})
                return

            elif path == "/api/planner/generate":
                goal = body.get("goal", "").strip()
                model = body.get("model", "coordinator")
                if not goal:
                    self.send_json({"ok": False, "error": "Goal description required"}, 400)
                    return

                plan_prompt = (
                    f"You are an expert AI Planner and Autonomous Agent Architect.\n"
                    f"Decompose the following high-level objective into a structured, executable finite instruction set.\n"
                    f"Format your response as a valid JSON object ONLY, with no markdown code fences, matching this schema:\n"
                    f"{{\n"
                    f'  "title": "<Brief title of task>",\n'
                    f'  "objective": "<Goal summary>",\n'
                    f'  "steps": [\n'
                    f'    {{"id": 1, "desc": "<Step 1 description>", "action": "<command or instruction>", "target": "<file or service>", "status": "pending"}},\n'
                    f'    {{"id": 2, "desc": "<Step 2 description>", "action": "<command or instruction>", "target": "<file or service>", "status": "pending"}}\n'
                    f'  ]\n'
                    f"}}\n\n"
                    f"OBJECTIVE: {goal}"
                )

                try:
                    chunks = []
                    for c in cluster.stream_chat(model, [{"role": "user", "content": plan_prompt}], {"max_tokens": 1500, "temperature": 0.2}):
                        if c.startswith("data: ") and "[DONE]" not in c:
                            try:
                                delta = json.loads(c[6:])["choices"][0]["delta"].get("content") or ""
                                chunks.append(delta)
                            except Exception:
                                pass
                    raw_res = "".join(chunks).strip()
                    clean_json = raw_res
                    if "```json" in clean_json:
                        clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0].strip()
                    elif "```" in clean_json:
                        clean_json = clean_json.split("```", 1)[1].split("```", 1)[0].strip()

                    try:
                        plan_data = json.loads(clean_json)
                    except Exception:
                        plan_data = {
                            "title": goal[:40],
                            "objective": goal,
                            "steps": [
                                {"id": 1, "desc": "Analyze system requirements", "action": "inspect", "target": "workspace", "status": "pending"},
                                {"id": 2, "desc": "Execute implementation steps", "action": "code", "target": "StoneSage", "status": "pending"},
                                {"id": 3, "desc": "Verify with automated tests", "action": "verify", "target": "tests", "status": "pending"}
                            ]
                        }
                    stm.set_active_plan(plan_data)
                    self.send_json({"ok": True, "plan": plan_data})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif path == "/api/planner/execute_step":
                step_id = body.get("step_id", 1)
                model = body.get("model", "worker")
                plan = stm.get_active_plan()
                if not plan:
                    self.send_json({"ok": False, "error": "No active plan found"}, 400)
                    return

                target_step = None
                for s in plan.get("steps", []):
                    if s.get("id") == step_id:
                        target_step = s
                        break

                if not target_step:
                    self.send_json({"ok": False, "error": f"Step {step_id} not found"}, 404)
                    return

                target_step["status"] = "running"
                exec_prompt = f"Execute milestone step: {target_step.get('desc')}. Action: {target_step.get('action')}. Target: {target_step.get('target')}. Report outcome concisely."
                try:
                    chunks = []
                    for c in cluster.stream_chat(model, [{"role": "user", "content": exec_prompt}], {"max_tokens": 800}):
                        if c.startswith("data: ") and "[DONE]" not in c:
                            try:
                                delta = json.loads(c[6:])["choices"][0]["delta"].get("content") or ""
                                chunks.append(delta)
                            except Exception:
                                pass
                    output = "".join(chunks).strip()
                    target_step["status"] = "completed"
                    target_step["output"] = output
                    stm.add_item("execution", f"Step_{step_id}", output[:150])
                    self.send_json({"ok": True, "step": target_step, "plan": plan})
                except Exception as e:
                    target_step["status"] = "failed"
                    target_step["error"] = str(e)
                    self.send_json({"ok": False, "error": str(e), "plan": plan})
                return


            elif path in ("/api/chat", "/api/ai/chat_ollama") and \
                    str(body.get("model", "")).lower().split(":")[0] in ("computer", "courage"):
                # Computer's tool loop. "computer" since 2026-09-27; "courage" is what HA's Ollama agent is set up with.
                # Exact names: a substring test sent "computer:latest" to the bare coordinator (no persona or tools).
                self._handle_courage_ollama(body)
                return

            elif path in ("/api/chat", "/api/ai/chat_ollama"):
                # Official Ollama-compatible Chat API for Home Assistant & external agents
                cluster.signal_preemption(reason="ha_assist_ollama", in_flight=True)
                try:
                    req_model = body.get("model", "worker:latest").lower()
                    is_coord = "coordinator" in req_model
                    target_base = cluster.coordinator_url if is_coord else cluster.worker_url
                    model_id = "coordinator" if is_coord else "worker"
                    messages = list(body.get("messages", []))
                    messages = cluster.prune_context_history(messages, budget_tokens=11000 if is_coord else 3100)
                    stream = body.get("stream", False)
                    tools = body.get("tools", [])
                    has_tools = bool(tools)

                    # Augment system instructions for tool calls if tools provided by HA Assist
                    if has_tools:
                        # Native tool-calling format is handled by llama-server jinja template
                        pass

                    forward_body = {
                        "model": model_id,
                        "messages": messages,
                        "temperature": 0.2 if has_tools else 0.7,
                        "stream": stream,
                        "max_tokens": 800
                    }
                    if has_tools:
                        forward_body["tools"] = tools

                    fwd_payload = json.dumps(forward_body).encode("utf-8")
                    print(f"[StoneSage] /api/chat fwd: model={model_id}, stream={stream}, tools={has_tools}, payload_len={len(fwd_payload)}", file=sys.stderr)
                    fwd_req = urllib.request.Request(
                        f"{target_base}/chat/completions",
                        data=fwd_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )

                    iso_now = datetime.now(timezone.utc).isoformat()

                    if stream:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "close")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()

                        with urllib.request.urlopen(fwd_req, timeout=60) as resp:
                            full_text = ""
                            loop_detector = ReasoningLoopDetector(min_repeats=3)
                            for line in resp:
                                l_str = line.decode("utf-8", errors="ignore").strip()
                                if l_str.startswith("data: ") and l_str != "data: [DONE]":
                                    try:
                                        chunk_json = json.loads(l_str[6:])
                                        delta = chunk_json.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                                        if delta:
                                            is_loop, phrase = loop_detector.ingest_chunk(delta)
                                            if is_loop:
                                                event = GLOBAL_WATCHDOG.record_intercept(phrase, model=req_model)
                                                intercept_msg = f"\n\n> [!WARNING]\n> **[WATCHDOG INTERCEPT]**: Repetition loop detected on `'{phrase}'`. Aborted repetitive thought pattern and dispatched agent nudge.\n\n"
                                                full_text += intercept_msg
                                                chunk_out = {
                                                    "model": req_model,
                                                    "created_at": iso_now,
                                                    "message": {"role": "assistant", "content": intercept_msg},
                                                    "watchdog_intercept": True,
                                                    "intercept_phrase": phrase,
                                                    "done": False
                                                }
                                                self.wfile.write((json.dumps(chunk_out) + "\n").encode("utf-8"))
                                                self.wfile.flush()
                                                break
                                            else:
                                                full_text += delta
                                                chunk_out = {
                                                    "model": req_model,
                                                    "created_at": iso_now,
                                                    "message": {"role": "assistant", "content": delta},
                                                    "done": False
                                                }
                                                self.wfile.write((json.dumps(chunk_out) + "\n").encode("utf-8"))
                                                self.wfile.flush()
                                    except Exception:
                                        pass

                            # Check for tool call in completed response
                            t_name, t_args = extract_tool_call(full_text)
                            if t_name:
                                done_out = {
                                    "model": req_model,
                                    "created_at": iso_now,
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [{
                                            "function": {
                                                "name": t_name,
                                                "arguments": t_args
                                            }
                                        }]
                                    },
                                    "done": True,
                                    "done_reason": "tool_calls"
                                }
                            else:
                                done_out = {
                                    "model": req_model,
                                    "created_at": iso_now,
                                    "message": {"role": "assistant", "content": ""},
                                    "done": True,
                                    "done_reason": "stop"
                                }
                            self.wfile.write((json.dumps(done_out) + "\n").encode("utf-8"))
                            self.wfile.flush()
                    else:
                        with urllib.request.urlopen(fwd_req, timeout=60) as resp:
                            res_json = json.loads(resp.read().decode("utf-8"))
                            choice = res_json.get("choices", [{}])[0]
                            content = choice.get("message", {}).get("content") or ""
                            t_name, t_args = extract_tool_call(content)
                            if t_name:
                                ollama_res = {
                                    "model": req_model,
                                    "created_at": iso_now,
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [{
                                            "function": {
                                                "name": t_name,
                                                "arguments": t_args
                                            }
                                        }]
                                    },
                                    "done": True
                                }
                            else:
                                ollama_res = {
                                    "model": req_model,
                                    "created_at": iso_now,
                                    "message": {
                                        "role": "assistant",
                                        "content": content
                                    },
                                    "done": True
                                }
                            self.send_json(ollama_res)
                except Exception as ex:
                    print(f"[StoneSage] Error in /api/chat: {ex}", file=sys.stderr)
                    if hasattr(ex, 'read'):
                        try:
                            print(f"[StoneSage] Backend response: {ex.read().decode('utf-8', errors='ignore')}", file=sys.stderr)
                        except Exception:
                            pass
                    self.send_json({"error": str(ex)}, 500)
                finally:
                    cluster.signal_preemption(reason="ha_assist_done", in_flight=False)
                return

            elif path == "/api/preemption/signal":
                reason = body.get("reason", "interactive_user")
                in_flight = body.get("in_flight", False)
                res = cluster.signal_preemption(reason=reason, in_flight=in_flight)
                self.send_json(res)
                return

            # ── Transparent Proxy: OpenAI-compatible /chat/completions (Coordinator & Worker) ──
            elif path in ("/api/ai/coordinator/v1/chat/completions", "/api/ai/worker/v1/chat/completions"):
                is_worker = "worker" in path
                default_model = "worker" if is_worker else "coordinator"
                cluster.signal_preemption(reason=f"proxy_{default_model}", in_flight=True)
                target_base = cluster.worker_url if is_worker else cluster.coordinator_url
                model_header_name = getattr(cluster, "worker_model_name", "worker") if is_worker else getattr(cluster, "coordinator_model_name", "coordinator")
                messages = list(body.get("messages", []))
                stream = body.get("stream", False)

                # Extract last user message safely (handles str or list of dicts)
                def extract_text_content(val) -> str:
                    if isinstance(val, str):
                        return val
                    elif isinstance(val, list):
                        parts = []
                        for item in val:
                            if isinstance(item, str):
                                parts.append(item)
                            elif isinstance(item, dict) and "text" in item:
                                parts.append(str(item.get("text", "")))
                        return " ".join(parts)
                    return str(val or "")

                last_user_msg = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        last_user_msg = extract_text_content(m.get("content")).strip()
                        break

                # Minimally Invasive Embedder-Driven Context Injection:
                # Vectorize query and inject ONLY top-1 or top-2 atomic fact cards (< 35 tokens each)
                # meeting strict similarity threshold >= 0.82. If irrelevant, inject ZERO extra tokens.
                if not is_worker and last_user_msg and len(last_user_msg) > 15:
                    try:
                        query_text = last_user_msg[:300]
                        obsidian_hits = cluster.search_hybrid(
                            query_text, collection_name="obsidian_vault", limit=2
                        )
                        codebase_hits = cluster.search_memory(
                            query_text, collection_name="codebase_knowledge", limit=2
                        )
                        all_hits = (obsidian_hits or []) + (codebase_hits or [])

                        # Filter strictly by similarity score >= 0.82
                        filtered_hits = [pt for pt in all_hits if pt.get("score", 0) >= 0.82]
                        filtered_hits.sort(key=lambda p: p.get("score", 0), reverse=True)
                        top_atoms = filtered_hits[:2]

                        if top_atoms:
                            snippets = []
                            for pt in top_atoms:
                                payload = pt.get("payload", {})
                                title = payload.get("title") or payload.get("path") or "Invariant"
                                content = payload.get("content") or payload.get("text") or ""
                                if content and content.strip():
                                    compact = content.strip().split("\n")[0][:140]
                                    snippets.append(f"• [{title}]: {compact}")

                            if snippets:
                                rag_block = "\n\n[Ground Truth Invariant Atoms]:\n" + "\n".join(snippets)
                                augmented = []
                                injected = False
                                for m in messages:
                                    if m.get("role") == "system" and not injected:
                                        sys_orig = m.get("content")
                                        if isinstance(sys_orig, list):
                                            sys_aug = list(sys_orig) + [{"type": "text", "text": rag_block}]
                                        else:
                                            sys_aug = str(sys_orig or "") + rag_block
                                        augmented.append({"role": "system", "content": sys_aug})
                                        injected = True
                                    else:
                                        augmented.append(dict(m))
                                if not injected:
                                    augmented.insert(0, {"role": "system", "content": rag_block.strip()})
                                messages = augmented
                    except Exception:
                        pass  # Graceful degradation: forward without RAG context

                has_tools = bool(body.get("tools"))

                # Worker optimization: make the small worker model fill in required tool parameters
                if is_worker and has_tools:
                    worker_rule = (
                        "\n\n[TOOL CALLING RULE]\n"
                        "When calling a function, you MUST fill in all required arguments in the JSON object.\n"
                        "Example: {\"name\": \"GetLiveContext\", \"arguments\": {\"name\": \"living_room_lamp\"}}.\n"
                        "Never leave arguments empty.\n"
                    )
                    augmented = []
                    injected = False
                    for m in messages:
                        if m.get("role") == "system" and not injected:
                            s_content = m.get("content")
                            if isinstance(s_content, list):
                                augmented.append({"role": "system", "content": list(s_content) + [{"type": "text", "text": worker_rule}]})
                            else:
                                augmented.append({"role": "system", "content": str(s_content or "") + worker_rule})
                            injected = True
                        else:
                            augmented.append(dict(m))
                    if not injected:
                        augmented.insert(0, {"role": "system", "content": worker_rule.strip()})
                # System prompt instruction: query Qdrant skills at minimal token cost
                skill_prompt_directive = (
                    "\n\n[SKILL & KNOWLEDGE RETRIEVAL DIRECTIVE]\n"
                    "All specialized operational skills, homelab procedures, and device configurations are indexed in Qdrant collection 'codebase_knowledge'. "
                    "When a request requires specialized execution rules, retrieve them on-demand via search_memory with concise keywords. "
                    "Pull only specific functional chunks to conserve context and tokens."
                )
                augmented_skills = []
                skill_injected = False
                for m in messages:
                    if m.get("role") == "system" and not skill_injected:
                        s_content = m.get("content")
                        if isinstance(s_content, list):
                            augmented_skills.append({"role": "system", "content": list(s_content) + [{"type": "text", "text": skill_prompt_directive}]})
                        else:
                            augmented_skills.append({"role": "system", "content": str(s_content or "") + skill_prompt_directive})
                        skill_injected = True
                    else:
                        augmented_skills.append(dict(m))
                if not skill_injected:
                    augmented_skills.insert(0, {"role": "system", "content": skill_prompt_directive.strip()})
                messages = augmented_skills

                # Build forwarded payload — preserve all original fields
                forward_body = dict(body)
                forward_body["messages"] = messages
                forward_body.setdefault("model", default_model)
                forward_body.setdefault("presence_penalty", 0.3)
                forward_body.setdefault("repeat_penalty", 1.15)
                forward_body.setdefault("dry_multiplier", 0.8)
                if forward_body.get("max_tokens", 0) > 1500:
                    forward_body["max_tokens"] = 1500

                target_url = f"{target_base}/chat/completions"
                payload_bytes = json.dumps(forward_body).encode("utf-8")
                fwd_req = urllib.request.Request(
                    target_url,
                    data=payload_bytes,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )

                if stream:
                    # Streaming SSE pass-through with native OpenAI tool-call translation
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    try:
                        with urllib.request.urlopen(fwd_req, timeout=180) as resp:
                            if has_tools:
                                # When client supports tools, buffer stream to intercept any tool calls in content
                                raw_lines = []
                                full_text = ""
                                chunk_id = "chatcmpl-" + str(int(time.time()))
                                created_ts = int(time.time())
                                for line in resp:
                                    raw_lines.append(line)
                                    line_str = line.decode("utf-8", errors="ignore").strip()
                                    if line_str.startswith("data: ") and line_str != "data: [DONE]":
                                        try:
                                             cj = json.loads(line_str[6:])
                                             chunk_id = cj.get("id", chunk_id)
                                             created_ts = cj.get("created", created_ts)
                                             d = cj.get("choices", [{}])[0].get("delta", {})
                                             c = d.get("content")
                                             if c:
                                                 full_text += c
                                        except Exception:
                                             pass

                                # Check if full_text contains a tool call
                                tool_name, tool_args = extract_tool_call(full_text)
                                print(f"[{default_model}] tool={tool_name} args={tool_args} text={repr(full_text[:120])}", flush=True)
                                if tool_name:
                                    # Emit native OpenAI tool_calls SSE chunks
                                    call_id = f"call_{int(time.time())}"
                                    tool_delta = {
                                        "id": chunk_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_header_name,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {
                                                "role": "assistant",
                                                "tool_calls": [{
                                                    "index": 0,
                                                    "id": call_id,
                                                    "type": "function",
                                                    "function": {
                                                        "name": tool_name,
                                                        "arguments": json.dumps(tool_args)
                                                    }
                                                }]
                                            },
                                            "finish_reason": None
                                        }]
                                    }
                                    finish_delta = {
                                        "id": chunk_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_header_name,
                                        "choices": [{
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": "tool_calls"
                                        }]
                                    }
                                    self.wfile.write(f"data: {json.dumps(tool_delta)}\n\n".encode("utf-8"))
                                    self.wfile.write(f"data: {json.dumps(finish_delta)}\n\n".encode("utf-8"))
                                    self.wfile.write(b"data: [DONE]\n\n")
                                    self.wfile.flush()
                                else:
                                    # Normal conversation/speech — forward buffered lines
                                    for l in raw_lines:
                                        self.wfile.write(l)
                                    self.wfile.flush()
                            else:
                                for line in resp:
                                    self.wfile.write(line)
                                    self.wfile.flush()
                    except Exception as e:
                        err_chunk = f'data: {json.dumps({"error": {"message": str(e)}})}\n\n'
                        self.wfile.write(err_chunk.encode("utf-8"))
                        self.wfile.flush()
                else:
                    # Non-streaming JSON pass-through with tool_call translation
                    try:
                        with urllib.request.urlopen(fwd_req, timeout=180) as resp:
                            data = resp.read()
                            if has_tools:
                                try:
                                    res_obj = json.loads(data.decode("utf-8"))
                                    content = res_obj.get("choices", [{}])[0].get("message", {}).get("content", "")
                                    t_name, t_args = extract_tool_call(content)
                                    if t_name:
                                        call_id = f"call_{int(time.time())}"
                                        res_obj["choices"][0]["message"]["content"] = None
                                        res_obj["choices"][0]["message"]["tool_calls"] = [{
                                            "index": 0,
                                            "id": call_id,
                                            "type": "function",
                                            "function": {
                                                "name": t_name,
                                                "arguments": json.dumps(t_args)
                                            }
                                        }]
                                        res_obj["choices"][0]["finish_reason"] = "tool_calls"
                                        data = json.dumps(res_obj).encode("utf-8")
                                except Exception:
                                    pass
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.send_header("Access-Control-Allow-Origin", "*")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                    except urllib.error.HTTPError as he:
                        err_body = he.read().decode("utf-8", errors="replace")
                        self.send_json({
                            "error": {"message": f"{default_model.capitalize()} returned {he.code}: {err_body}", "type": "upstream_error"}
                        }, he.code)
                    except Exception as e:
                        self.send_json({
                            "error": {"message": str(e), "type": "proxy_error"}
                        }, 502)
                return

            self.send_json({"error": "Unknown endpoint"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass                       # the client went away
        except Exception as e:
            # the traceback goes to the log, not to the client (it exposed internals); nothing is sent mid-stream
            logger.error(f"POST {self.path}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            if not getattr(self, "_responded", False):
                self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


# ---- Apps (apps.py, docs/LAYERS.md): loaded by name from config, never imported by the platform -------------------
from stonesage.apps import AppContext, LiveRef, load_apps  # noqa: E402

app_ctx = AppContext(config, ROOT_DIR, _stonesage_data_dir, tool_registry=tool_registry, services={
    "load_config": load_config,
    "save_config": save_config,
    "get_courage_agent": get_courage_agent,
    "get_trace_log": get_trace_log,
    "get_engine_lease": get_engine_lease,
    "cluster": LiveRef(lambda: cluster),
    "_engine_lease": LiveRef(lambda: _engine_lease),   # None until a lease is first asked for
    "_on_loan": _on_loan,
    "LOCAL_TZ": LOCAL_TZ,
})
load_apps(config.get("apps", ["hearth"]), app_ctx)   # "home" in an old config still works (apps.APP_ALIASES)

if __name__ == "__main__":
    port = int(config.get("server", {}).get("port", 8888))
    host = config.get("server", {}).get("host", "0.0.0.0")
    _on_default = endpoints.missing(config)
    if _on_default:   # a spread-out install that forgot a key would otherwise talk to localhost without a word
        logger.warning("Not set in config.json, so on their localhost default: %s (lodestone/endpoints.py)",
                       ", ".join(_on_default))

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    try:
        setup_boost()
    except Exception as e:
        logger.warning(f"Boost setup failed: {e}")
    threading.Thread(target=get_courage_agent, daemon=True, name="courage-agent-warmup").start()  # starts the phone-approval listener
    if config.get("engine_layouts"):
        get_engine_lease().start()  # reverts a lease a previous run left behind, then watches the clock
    if config.get("provenance"):
        get_provenance().start()  # model provenance: header scan now, hashing rescans (backend/provenance.py)
    app_ctx.start()  # whatever the loaded apps asked to start (apps.py)
    try:
        server = http.server.ThreadingHTTPServer((host, port), StoneSageHandler)
    except OSError as e:
        if getattr(e, 'winerror', None) == 10048 or getattr(e, 'errno', None) in (48, 98):
            print(f"\n[!] Port {port} is already in use by another instance or process.")
            print(f"    StoneSage is already active at http://localhost:{port}")
            print(f"    To force restart, run start.bat which will automatically free the port.")
            sys.exit(1)
        raise

    # If primary port is 8888, spawn a background redirector on port 8080 to bounce stale clients
    if port == 8888:
        class Redirect8080Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                host_hdr = self.headers.get("Host", "").split(":")[0] or "localhost"
                target_url = f"http://{host_hdr}:8888{self.path}"
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.end_headers()
                try:
                    self.wfile.write(b"Redirecting to StoneSage port 8888...")
                except Exception:
                    pass

            def do_HEAD(self):
                host_hdr = self.headers.get("Host", "").split(":")[0] or "localhost"
                target_url = f"http://{host_hdr}:8888{self.path}"
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.end_headers()

        def _start_redirector():
            try:
                redir = http.server.ThreadingHTTPServer((host, 8080), Redirect8080Handler)
                redir.serve_forever()
            except Exception as e:
                logger.info("Port 8080 redirector skipped or unavailable: %s", e)

        threading.Thread(target=_start_redirector, daemon=True).start()

        def _start_pty_server():
            try:
                candidate_dirs = [
                    os.path.join(ROOT_DIR, "harness"),
                    os.path.join(WORKSPACE_ROOT, "harness"),
                    os.path.join(ROOT_DIR, "backend", "harness")
                ]
                for cd in candidate_dirs:
                    if os.path.isdir(cd) and os.path.dirname(cd) not in sys.path:
                        sys.path.insert(0, os.path.dirname(cd))
                from harness.server import HarnessServer
                logger.info("⚡ Launching StoneSage PTY Shell WebSocket Daemon on 0.0.0.0:8088...")
                pty_server = HarnessServer(host="0.0.0.0", port=8088)
                pty_server.run()
            except Exception as e:
                logger.warning("PTY WebSocket server (:8088) skipped or unavailable: %s", e)

        threading.Thread(target=_start_pty_server, daemon=True, name="HarnessPTY-8088").start()

    # ─── HTTPS Listener on :8443 (enables mic/camera secure context from LAN) ───
    ssl_port = int(config.get("server", {}).get("ssl_port", 8443))
    cert_dir = os.path.join(ROOT_DIR, "certs")
    cert_file = os.path.join(cert_dir, "stonesage.pem")
    key_file = os.path.join(cert_dir, "stonesage-key.pem")

    if os.path.isfile(cert_file) and os.path.isfile(key_file):
        def _start_https():
            try:
                https_server = http.server.ThreadingHTTPServer((host, ssl_port), StoneSageHandler)
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
                https_server.socket = ssl_ctx.wrap_socket(https_server.socket, server_side=True)
                logger.info("🔒 HTTPS listener active on 0.0.0.0:%d", ssl_port)
                https_server.serve_forever()
            except Exception as e:
                logger.warning("HTTPS listener (:8443) skipped: %s", e)

        threading.Thread(target=_start_https, daemon=True, name="HTTPS-8443").start()
        https_banner = f"  LAN Cockpit HTTPS: https://<this host>:{ssl_port}  (🎤 mic enabled)"
    else:
        https_banner = "  LAN Cockpit HTTPS: (no cert found — run openssl to generate)"
        logger.info("No SSL cert at %s — HTTPS listener skipped", cert_file)

    lan_ip = config.get("server", {}).get("lan_ip") or "localhost"
    print("=" * 68)
    print("  [ STONESAGE COGNITIVE TERMINAL WORKSTATION v4.0 - ENTERPRISE ]")
    print(f"  Local Browser:     http://localhost:{port} (or http://127.0.0.1:{port})")
    print(f"{https_banner}")
    print(f"  LAN Workstation:   http://{lan_ip}:{port}")
    print(f"  Proxmox API:       {load_config().get('proxmox', {}).get('cluster_url', 'not configured')}")
    print(f"  Dual-GPU Cluster:  coder-agent 16k (:8001) & home-agent (:8002)")
    print(f"  RAG Proxy (HA):    http://{lan_ip}:{port}/api/ai/coordinator/v1")
    print(f"  Qdrant Memory:     {endpoints.url('qdrant', config)}")
    print(f"  Home Assistant:    {endpoints.url('homeassistant', config)}")
    print("=" * 68)
    print("=" * 65)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping StoneSage server...")
        server.server_close()
