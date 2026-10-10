#!/usr/bin/env python3
"""
StoneSage Universal Tool Calling & Execution Engine.
Empowers all local cluster endpoints and remote frontier models to invoke tools,
perform actions across the homelab, execute commands, and dynamically ingest tools from URLs.
Synchronizes with Valkey A-MEM atomic fact cards (FIFO overflow policy) and Qdrant memory.
"""

import os
import sys
import re
import json
from lodestone import endpoints
from lodestone.system_profile import load_cfg
import time
import uuid
import urllib.request
import urllib.parse
import subprocess
import logging
from typing import Dict, Any, List, Optional, Tuple, Callable

logger = logging.getLogger("StoneSage.ToolHarness")

# Maximum dynamic tools in active A-MEM working memory (FIFO policy)
MAX_DYNAMIC_TOOLS_IN_AMEM = 20
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # stonesage/ -> backend/
# Tool names accepted in plain `name(arg=...)` text form; app packs add theirs (ToolRegistry.add_text_call_names)
CORE_TEXT_CALL_NAMES = ("delegate_worker", "spawn_subagent", "get_subagent_task", "list_subagents", "execute_command",
                        "read_file", "write_file", "search_codebase", "search_memory", "store_to_obsidian")


# ==============================================================================
# Built-In Tool Execution Handlers
# ==============================================================================

def tool_read_file(path: str, offset: int = 0, limit: int = 300) -> Dict[str, Any]:
    """Reads lines from a local or workspace file."""
    clean_path = (path or "").strip().strip('"').strip("'")
    if not clean_path:
        return {"ok": False, "error": "Path parameter is required."}

    # Resolve relative paths against workspace root
    if not os.path.isabs(clean_path):
        clean_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, clean_path))

    if not os.path.exists(clean_path):
        return {"ok": False, "error": f"File not found: {clean_path}"}

    try:
        with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total_lines = len(lines)
        start_idx = max(0, offset)
        end_idx = min(total_lines, start_idx + limit)
        slice_lines = lines[start_idx:end_idx]
        return {
            "ok": True,
            "path": clean_path,
            "total_lines": total_lines,
            "offset": start_idx,
            "lines_returned": len(slice_lines),
            "content": "".join(slice_lines)
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write_file(path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
    """Writes content to a file."""
    clean_path = (path or "").strip().strip('"').strip("'")
    if not clean_path:
        return {"ok": False, "error": "Path parameter is required."}

    if not os.path.isabs(clean_path):
        clean_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, clean_path))

    if os.path.exists(clean_path) and not overwrite:
        return {"ok": False, "error": f"File already exists and overwrite is False: {clean_path}"}

    try:
        os.makedirs(os.path.dirname(clean_path), exist_ok=True)
        with open(clean_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "ok": True,
            "path": clean_path,
            "bytes_written": len(content.encode("utf-8")),
            "message": f"Successfully wrote {clean_path}"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_list_directory(path: str = "", depth: int = 1) -> Dict[str, Any]:
    """Lists files and directories within a given directory."""
    target_path = (path or "").strip().strip('"').strip("'")
    if not target_path:
        target_path = WORKSPACE_ROOT

    if not os.path.isabs(target_path):
        target_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, target_path))

    if not os.path.exists(target_path):
        return {"ok": False, "error": f"Directory not found: {target_path}"}

    try:
        results = []
        for root, dirs, files in os.walk(target_path):
            rel_root = os.path.relpath(root, target_path)
            cur_depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if cur_depth > depth:
                dirs.clear()
                continue
            for d in dirs:
                if d.startswith(".") or d in ("node_modules", "__pycache__", "target", "dist"):
                    continue
                results.append({"type": "dir", "name": d, "rel_path": os.path.normpath(os.path.join(rel_root, d))})
            for f in files:
                if f.startswith("."):
                    continue
                results.append({"type": "file", "name": f, "rel_path": os.path.normpath(os.path.join(rel_root, f))})
            if cur_depth >= depth:
                dirs.clear()
        return {"ok": True, "path": target_path, "items_count": len(results), "items": results[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_search_codebase(query: str, path: str = "") -> Dict[str, Any]:
    """Searches for pattern or text across the workspace codebase."""
    search_dir = (path or "").strip().strip('"').strip("'") or WORKSPACE_ROOT
    if not os.path.isabs(search_dir):
        search_dir = os.path.abspath(os.path.join(WORKSPACE_ROOT, search_dir))

    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Query string is required."}

    # Use python search across codebase
    matches = []
    ignored_dirs = {".git", ".system_generated", "node_modules", "__pycache__", "venv", ".venv"}
    try:
        for root, dirs, files in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for f in files:
                if f.endswith((".py", ".js", ".html", ".css", ".md", ".json", ".sh", ".ps1", ".txt")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as file_in:
                            for idx, line in enumerate(file_in, 1):
                                if q.lower() in line.lower():
                                    matches.append({
                                        "file": os.path.relpath(fp, search_dir),
                                        "line": idx,
                                        "text": line.strip()[:200]
                                    })
                                    if len(matches) >= 30:
                                        break
                    except Exception:
                        pass
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break
        return {"ok": True, "query": q, "match_count": len(matches), "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_ingest_knowledge(path_or_content: str, knowledgebase: str = "Stonesage") -> Dict[str, Any]:
    """
    Ingests text or file contents directly into Qdrant vector memory
    under the collection 'codebase_knowledge' (Qdrant, endpoints "qdrant").
    """
    target = (path_or_content or "").strip()
    if not target:
        return {"ok": False, "error": "path_or_content parameter is required."}

    content_to_ingest = ""
    source_label = target

    # Check if target is a file path
    possible_path = target
    if not os.path.isabs(possible_path):
        possible_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, possible_path))

    if os.path.exists(possible_path) and os.path.isfile(possible_path):
        try:
            with open(possible_path, "r", encoding="utf-8", errors="replace") as f:
                content_to_ingest = f.read()
            source_label = os.path.basename(possible_path)
        except Exception as e:
            return {"ok": False, "error": f"Failed reading file: {e}"}
    else:
        # Treat as direct text content
        content_to_ingest = target

    if not content_to_ingest.strip():
        return {"ok": False, "error": "No content found to ingest."}

    # Chunk content strictly < 1000 characters to prevent BGE 512-token context overflow
    chunks = []
    lines = content_to_ingest.splitlines()
    curr_chunk = []
    curr_len = 0
    for l in lines:
        if curr_len + len(l) > 850:
            if curr_chunk:
                chunks.append("\n".join(curr_chunk))
            curr_chunk = [l]
            curr_len = len(l)
        else:
            curr_chunk.append(l)
            curr_len += len(l)
    if curr_chunk:
        chunks.append("\n".join(curr_chunk))

    # Ingest chunks into Qdrant via ClusterClient
    try:
        from stonesage.cluster_client import ClusterClient
        client = ClusterClient(load_cfg())
        stored_ids = []
        for i, ch in enumerate(chunks[:20]):
            pid = client.store_memory(
                content=ch,
                collection_name="codebase_knowledge",
                metadata={"source": source_label, "knowledgebase": knowledgebase, "chunk_index": i}
            )
            stored_ids.append(pid)
        return {
            "ok": True,
            "knowledgebase": knowledgebase,
            "source": source_label,
            "chunks_ingested": len(stored_ids),
            "collection": "codebase_knowledge",
            "message": f"Successfully indexed {len(stored_ids)} chunks from '{source_label}' into Qdrant."
        }
    except Exception as e:
        return {"ok": False, "error": f"Qdrant ingestion error: {e}"}


def tool_search_memory(query: str, collection: str = "codebase_knowledge", limit: int = 5) -> Dict[str, Any]:
    """Performs semantic search against Qdrant vector memory (endpoints "qdrant")."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Query string is required."}

    try:
        from stonesage.cluster_client import ClusterClient
        client = ClusterClient(load_cfg())
        results = client.search_memory(query=q, collection_name=collection, limit=limit)
        clean_pts = []
        for r in results:
            payload = r.get("payload", {})
            clean_pts.append({
                "score": r.get("score"),
                "content": (payload.get("content") or payload.get("text") or "")[:400],
                "metadata": payload.get("metadata", {})
            })
        return {"ok": True, "query": q, "collection": collection, "matches": clean_pts}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_store_to_obsidian(
    content_or_url: str,
    type: str = "note",
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
    database: str = "ai_obsidian"
) -> Dict[str, Any]:
    """Stores a URL, text note, snippet, or photo into Obsidian, Qdrant, and Valkey memory."""
    try:
        from stonesage.obsidian_ingest import obsidian_ingest
        t = (type or "note").lower().strip()
        if t in ("url", "link", "web", "webpage", "article"):
            return obsidian_ingest.ingest_url(url=content_or_url, title=title, tags=tags, database=database)
        elif t in ("photo", "image", "picture"):
            return obsidian_ingest.ingest_photo(image_data=content_or_url, title=title, tags=tags, database=database)
        else:
            return obsidian_ingest.ingest_snippet(content=content_or_url, title=title, tags=tags, database=database)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_execute_command(command: str, cwd: str = "") -> Dict[str, Any]:
    """Executes a terminal/shell command with safety validation."""
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "error": "Command string is required."}

    try:
        from stonesage.command_whitelist import validate_terminal_command
        is_allowed, reason = validate_terminal_command(cmd, cwd=cwd)
        if not is_allowed:
            return {"ok": False, "error": f"Command rejected by security guardrails: {reason}"}
    except Exception:
        pass

    exec_cwd = cwd or WORKSPACE_ROOT
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=exec_cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "ok": res.returncode == 0,
            "returncode": res.returncode,
            "stdout": res.stdout[:2000],
            "stderr": res.stderr[:1000]
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command execution timed out (30s limit)."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_get_cluster_status() -> Dict[str, Any]:
    """Retrieves cluster telemetry, node latency, and active loaded models."""
    try:
        from stonesage.cluster_client import ClusterClient
        client = ClusterClient({})
        node_status = client.check_all_nodes()
        return {"ok": True, "cluster": node_status}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==============================================================================
# Sovereign Subagent & Worker Delegation Engine (September 2026 Parity)
# ==============================================================================

import threading

# Global subagent task registry
_SUBAGENT_TASKS: Dict[str, Dict[str, Any]] = {}
_SUBAGENT_LOCK = threading.Lock()

def _delegate_targets() -> List[tuple]:
    """(chat URL, label) in fallback order: worker, edge nodes from config, coordinator. From config + live profile."""
    from lodestone import system_profile
    cfg = system_profile.load_cfg()
    cl = cfg.get("cluster", {})
    out = []
    if cl.get("worker_url"):
        out.append((system_profile._base(cl["worker_url"]) + "/v1/chat/completions", system_profile.engine_label(cfg, "worker")))
    for inst in cfg.get("harness_instances", []):
        url = (inst.get("url") or "").rstrip("/")
        if inst.get("engine") or url.endswith(":1234") or url.endswith(":11434"):
            out.append((url + "/v1/chat/completions", inst.get("name", inst.get("id", "edge"))))
    if cl.get("coordinator_url"):
        out.append((system_profile._base(cl["coordinator_url"]) + "/v1/chat/completions",
                    system_profile.engine_label(cfg, "coordinator") + " (fallback)"))
    return out


def _topology_lines() -> str:
    """One line per live engine and configured edge node, e.g. '- worker (:8002): Qwen2.5 Coder 3B · RX 6750 XT'."""
    try:
        from lodestone import system_profile
        cfg = system_profile.load_cfg()
        prof = system_profile.get_profile(cfg)
        lines = [f"- {r} (:{e['port']}): {e['label']}" + (f", {e['ctx_per_slot']} ctx x {e['slots']} slots" if e.get("ctx_per_slot") else "")
                 for r, e in (prof.get("engines") or {}).items() if e.get("online")]
        for inst in cfg.get("harness_instances", []):
            hw = inst.get("hardware") or {}
            if inst.get("engine"):
                lines.append(f"- edge: {inst.get('name')}" + (f" ({hw.get('cpu')}, {hw.get('ram_gb')} GB RAM)" if hw else ""))
        return "\n".join(lines) or "- (no engines reachable)"
    except Exception:
        return "- (engine list unavailable)"


def tool_delegate_worker(
    task: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2
) -> Dict[str, Any]:
    """
    Delegates a bounded technical task, unit test, JSON schema, or code drafting
    to the fast worker engine (then any edge node from config, then the coordinator as a fallback).
    """
    clean_task = (task or "").strip()
    if not clean_task:
        return {"ok": False, "error": "Task description is required."}

    sys_sp = system_prompt or (
        "You are the fast worker engine on the local cluster. "
        "The user and the Lead Coordinator rely on your speed. Deliver pure, production-grade code, "
        "tests, or data schemas immediately with zero boilerplate, conversational preamble, or markdown stage directions."
    )

    messages = [
        {"role": "system", "content": sys_sp},
        {"role": "user", "content": clean_task}
    ]

    worker_urls = _delegate_targets()

    t0 = time.time()
    last_err = None

    for url, label in worker_urls:
        try:
            payload = json.dumps({
                "model": "worker" if "8002" in url else "default",
                "messages": messages,
                "max_tokens": min(max_tokens, 4096),
                "temperature": temperature,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False}
            }).encode("utf-8")

            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                elapsed_ms = round((time.time() - t0) * 1000)
                usage = data.get("usage", {})
                return {
                    "ok": True,
                    "target_engine": label,
                    "output": content,
                    "tokens": usage.get("completion_tokens", 0),
                    "latency_ms": elapsed_ms,
                    "message": f"Successfully completed on {label} in {elapsed_ms}ms"
                }
        except Exception as ex:
            last_err = ex
            continue

    return {"ok": False, "error": f"All worker endpoints failed. Last error: {last_err}"}


def _run_subagent_background_loop(task_id: str):
    """Executes iterative autonomous cycles in the background."""
    with _SUBAGENT_LOCK:
        task_info = _SUBAGENT_TASKS.get(task_id)
    if not task_info:
        return

    name = task_info["name"]
    role = task_info["role"]
    mission = task_info["mission"]
    max_iters = task_info["max_iterations"]
    model_pref = task_info["model_preference"]

    endpoint = endpoints.url("worker" if model_pref == "worker" else "coordinator") + "/chat/completions"
    history = [
        {
            "role": "system",
            "content": f"You are subagent '{name}', acting as {role}. Mission: {mission}. "
                       f"Execute your mission step by step. When finished, conclude with [MISSION_ACCOMPLISHED]."
        },
        {"role": "user", "content": f"Begin mission: {mission}"}
    ]

    for iteration in range(1, max_iters + 1):
        with _SUBAGENT_LOCK:
            if _SUBAGENT_TASKS[task_id].get("stopped"):
                _SUBAGENT_TASKS[task_id]["status"] = "stopped"
                break
            _SUBAGENT_TASKS[task_id]["current_iteration"] = iteration
            _SUBAGENT_TASKS[task_id]["status"] = "running"

        try:
            payload = json.dumps({
                "model": "worker" if model_pref == "worker" else "coordinator",
                "messages": history,
                "max_tokens": 1024,
                "temperature": 0.3,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False}
            }).encode("utf-8")
            req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=45.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                output = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                
                with _SUBAGENT_LOCK:
                    _SUBAGENT_TASKS[task_id]["iterations_log"].append({
                        "iteration": iteration,
                        "timestamp": time.time(),
                        "output": output
                    })
                    _SUBAGENT_TASKS[task_id]["latest_output"] = output

                if "[MISSION_ACCOMPLISHED]" in output or iteration == max_iters:
                    with _SUBAGENT_LOCK:
                        _SUBAGENT_TASKS[task_id]["status"] = "completed"
                        _SUBAGENT_TASKS[task_id]["completed_at"] = time.time()
                    break

                history.append({"role": "assistant", "content": output})
                history.append({"role": "user", "content": "Proceed to the next milestone. Review what was done and take the next concrete action."})

        except Exception as ex:
            with _SUBAGENT_LOCK:
                _SUBAGENT_TASKS[task_id]["status"] = "error"
                _SUBAGENT_TASKS[task_id]["error"] = str(ex)
            break
        time.sleep(0.5)


def tool_spawn_subagent(
    name: str,
    role: str,
    mission: str,
    max_iterations: int = 5,
    model_preference: str = "worker"
) -> Dict[str, Any]:
    """
    Commissions an independent autonomous background subagent to execute
    an ongoing, iterative task across multiple cycles on the cluster.
    """
    clean_name = (name or "subagent").strip()
    task_id = f"subagent_{uuid.uuid4().hex[:8]}"
    
    task_info = {
        "task_id": task_id,
        "name": clean_name,
        "role": role or "Task Specialist",
        "mission": mission or "Execute assigned objectives.",
        "max_iterations": max(1, min(max_iterations, 15)),
        "current_iteration": 0,
        "status": "queued",
        "model_preference": model_preference,
        "created_at": time.time(),
        "completed_at": None,
        "stopped": False,
        "iterations_log": [],
        "latest_output": None,
        "error": None
    }

    with _SUBAGENT_LOCK:
        _SUBAGENT_TASKS[task_id] = task_info

    # Launch in background thread
    t = threading.Thread(target=_run_subagent_background_loop, args=(task_id,), daemon=True)
    t.start()

    return {
        "ok": True,
        "task_id": task_id,
        "name": clean_name,
        "role": role,
        "status": "running",
        "max_iterations": task_info["max_iterations"],
        "message": f"Subagent '{clean_name}' successfully launched in background on {model_preference}. Monitor with get_subagent_task('{task_id}')."
    }


def tool_get_subagent_task(task_id: str) -> Dict[str, Any]:
    """Retrieves live status, current iteration, and output logs for a spawned subagent."""
    clean_id = (task_id or "").strip()
    with _SUBAGENT_LOCK:
        task = _SUBAGENT_TASKS.get(clean_id)
        if not task:
            return {"ok": False, "error": f"Subagent task '{clean_id}' not found."}
        return {
            "ok": True,
            "task_id": task["task_id"],
            "name": task["name"],
            "role": task["role"],
            "status": task["status"],
            "iteration": task["current_iteration"],
            "max_iterations": task["max_iterations"],
            "latest_output": task["latest_output"][:600] if task["latest_output"] else None,
            "iterations_completed": len(task["iterations_log"]),
            "error": task["error"]
        }


def tool_list_subagents() -> Dict[str, Any]:
    """Lists all active, completed, or queued background subagents."""
    with _SUBAGENT_LOCK:
        items = []
        for t in sorted(_SUBAGENT_TASKS.values(), key=lambda x: x["created_at"], reverse=True)[:15]:
            items.append({
                "task_id": t["task_id"],
                "name": t["name"],
                "role": t["role"],
                "status": t["status"],
                "current_iteration": t["current_iteration"],
                "max_iterations": t["max_iterations"],
                "created_at": t["created_at"]
            })
        return {"ok": True, "count": len(items), "subagents": items}



def tool_download_model(url: str, filename: Optional[str] = None, target_node: str = "node1_primary") -> Dict[str, Any]:
    """Triggers a background GGUF model download via ModelDownloadManager."""
    try:
        from lodestone.model_downloader import model_download_manager
        return model_download_manager.start_download(raw_url=url, custom_filename=filename, target_node=target_node)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==============================================================================
# Tool Registry & Dynamic URL Tool Ingester
# ==============================================================================

# Single background worker so boot-time tool indexing is serialized (one embedder
# request at a time) instead of blocking startup or bursting ~40 parallel requests.
import queue as _queue
import threading as _threading
_TOOL_INDEX_QUEUE: "_queue.Queue" = _queue.Queue()
_TOOL_INDEX_THREAD: Optional[_threading.Thread] = None


def _enqueue_tool_index(fn: Callable, arg: Dict[str, Any]) -> None:
    global _TOOL_INDEX_THREAD
    _TOOL_INDEX_QUEUE.put((fn, arg))
    if _TOOL_INDEX_THREAD is None or not _TOOL_INDEX_THREAD.is_alive():
        def _drain():
            while True:
                f, a = _TOOL_INDEX_QUEUE.get()
                try:
                    f(a)
                finally:
                    _TOOL_INDEX_QUEUE.task_done()
        _TOOL_INDEX_THREAD = _threading.Thread(target=_drain, daemon=True, name="tool-index-worker")
        _TOOL_INDEX_THREAD.start()


def _custom_tools_dir() -> str:
    """data/ by default; STONESAGE_DATA_DIR lets the unit-test runner keep writes out of the repo."""
    return os.environ.get("STONESAGE_DATA_DIR") or os.path.join(WORKSPACE_ROOT, "data")


class ToolRegistry:
    """
    Central registry for all tools accessible to any chat model at any endpoint.
    Maintains A-MEM atomic fact cards in Valkey with a FIFO overflow policy,
    and indexes tool schemas into Qdrant.
    """

    def __init__(self, amem_enabled: bool = True, qdrant_enabled: bool = True):
        self.amem_enabled = amem_enabled
        self.qdrant_enabled = qdrant_enabled
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.aliases: Dict[str, str] = {
            "chat_mcp__search": "search_codebase",
            "chat_mcp__ingest": "ingest_knowledge",
            "chat_mcp__create_tool": "ingest_tool_from_url",
            "read": "read_file",
            "write": "write_file",
            "search": "search_codebase",
            "ingest": "ingest_knowledge",
            "command": "execute_command",
            "run": "execute_command",
            "download": "download_model",
            "store_to_obsidian": "store_to_obsidian",
            "save_to_obsidian": "store_to_obsidian",
            "store_url": "store_to_obsidian",
            "store_photo": "store_to_obsidian",
            "capture_memory": "store_to_obsidian",
            "remember": "store_to_obsidian",
            "delegate": "delegate_worker",
            "worker": "delegate_worker",
            "delegate_to_worker": "delegate_worker",
            "subagent": "spawn_subagent",
            "spawn_agent": "spawn_subagent",
            "subagent_task": "get_subagent_task",
            "tasks": "list_subagents",
            "subagents": "list_subagents",
        }
        self.dynamic_tools_order: List[str] = []
        # contributed by app packs (e.g. hearth/ha_tools.py) through the add_* methods below: the platform never imports an app
        self.prompt_sections: List[Tuple[str, str]] = []
        self.text_call_names: set = set(CORE_TEXT_CALL_NAMES)
        self._register_defaults()
        self._load_persisted_tools()

    def _register_defaults(self):
        """Registers the core standard tools."""
        self.register_tool(
            name="read_file",
            description="Reads lines from a local or homelab file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path to read."},
                    "offset": {"type": "integer", "description": "Starting line offset (0-indexed).", "default": 0},
                    "limit": {"type": "integer", "description": "Maximum number of lines to return.", "default": 300}
                },
                "required": ["path"]
            },
            handler=tool_read_file,
            is_core=True
        )

        self.register_tool(
            name="write_file",
            description="Writes text or code to a file on disk.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path to write."},
                    "content": {"type": "string", "description": "Full file content to write."},
                    "overwrite": {"type": "boolean", "description": "Whether to overwrite if file exists.", "default": True}
                },
                "required": ["path", "content"]
            },
            handler=tool_write_file,
            is_core=True
        )

        self.register_tool(
            name="list_directory",
            description="Lists directories and files inside a directory path.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to inspect.", "default": ""},
                    "depth": {"type": "integer", "description": "Maximum recursive traversal depth.", "default": 1}
                }
            },
            handler=tool_list_directory,
            is_core=True
        )

        self.register_tool(
            name="search_codebase",
            description="Searches text patterns across the homelab codebase.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text or keyword pattern to find."},
                    "path": {"type": "string", "description": "Root directory to search within.", "default": ""}
                },
                "required": ["query"]
            },
            handler=tool_search_codebase,
            is_core=True
        )

        self.register_tool(
            name="ingest_knowledge",
            description="Indexes text or a file into Qdrant vector memory collection 'codebase_knowledge'.",
            parameters={
                "type": "object",
                "properties": {
                    "path_or_content": {"type": "string", "description": "File path to read and index, or direct text."},
                    "knowledgebase": {"type": "string", "description": "Name of knowledgebase.", "default": "Stonesage"}
                },
                "required": ["path_or_content"]
            },
            handler=tool_ingest_knowledge,
            is_core=True
        )

        self.register_tool(
            name="search_memory",
            description="Queries Qdrant vector memory using BGE embeddings.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Semantic query or question."},
                    "collection": {"type": "string", "description": "Collection name.", "default": "codebase_knowledge"},
                    "limit": {"type": "integer", "description": "Maximum results to return.", "default": 5}
                },
                "required": ["query"]
            },
            handler=tool_search_memory,
            is_core=True
        )

        self.register_tool(
            name="execute_command",
            description="Runs a shell/terminal command under safety bounds.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command line to execute."},
                    "cwd": {"type": "string", "description": "Working directory.", "default": ""}
                },
                "required": ["command"]
            },
            handler=tool_execute_command,
            is_core=True
        )

        self.register_tool(
            name="get_cluster_status",
            description="Returns status of compute nodes, active models, and latencies.",
            parameters={"type": "object", "properties": {}},
            handler=tool_get_cluster_status,
            is_core=True
        )

        self.register_tool(
            name="download_model",
            description="Initiates a background download of a GGUF model from a URL.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Hugging Face or direct GGUF model download URL."},
                    "filename": {"type": "string", "description": "Target filename.", "default": ""},
                    "target_node": {"type": "string", "description": "Node ID to download to.", "default": "node1_primary"}
                },
                "required": ["url"]
            },
            handler=tool_download_model,
            is_core=True
        )

        self.register_tool(
            name="store_to_obsidian",
            description="Stores a URL, text note, code snippet, or photo into the AI stack's Obsidian database ('ai_obsidian') and processes it into Qdrant vector memory and Valkey A-MEM for instant recall.",
            parameters={
                "type": "object",
                "properties": {
                    "content_or_url": {"type": "string", "description": "The URL to scrape, text/code content to save, or image data."},
                    "type": {"type": "string", "enum": ["url", "note", "snippet", "photo"], "description": "Type of item being stored.", "default": "note"},
                    "title": {"type": "string", "description": "Optional title for the note."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags for categorization."},
                    "database": {"type": "string", "description": "Target CouchDB database ('ai_obsidian' or 'obsidiannotes').", "default": "ai_obsidian"}
                },
                "required": ["content_or_url"]
            },
            handler=tool_store_to_obsidian,
            is_core=True
        )

        self.register_tool(
            name="ingest_tool_from_url",
            description="Fetches and registers a new tool definition or Python script from a URL on request.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to tool schema JSON, Python file, or OpenAPI spec."},
                    "name": {"type": "string", "description": "Optional custom name for the tool.", "default": ""}
                },
                "required": ["url"]
            },
            handler=self.ingest_tool_from_url,
            is_core=True
        )

        self.register_tool(
            name="delegate_worker",
            description="Delegates a bounded task, unit test, JSON schema, docstring, or code drafting to the fast local worker engine.",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Exact instruction and context for the worker model."},
                    "system_prompt": {"type": "string", "description": "Optional specialized system prompt for the task."},
                    "max_tokens": {"type": "integer", "description": "Maximum tokens to generate.", "default": 1024}
                },
                "required": ["task"]
            },
            handler=tool_delegate_worker,
            is_core=True
        )

        self.register_tool(
            name="spawn_subagent",
            description="Commissions an autonomous background subagent to execute a multi-iteration mission on the cluster.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Friendly name for the subagent (e.g. 'test_runner', 'linter')."},
                    "role": {"type": "string", "description": "Subagent role or title."},
                    "mission": {"type": "string", "description": "Detailed goal or multi-step task for the subagent."},
                    "max_iterations": {"type": "integer", "description": "Maximum autonomous cycles (1-15).", "default": 5}
                },
                "required": ["name", "mission"]
            },
            handler=tool_spawn_subagent,
            is_core=True
        )

        self.register_tool(
            name="get_subagent_task",
            description="Checks the progress, iteration count, and output of a running or completed subagent task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task_id returned by spawn_subagent."}
                },
                "required": ["task_id"]
            },
            handler=tool_get_subagent_task,
            is_core=True
        )

        self.register_tool(
            name="list_subagents",
            description="Lists all active, completed, or queued background subagents.",
            parameters={"type": "object", "properties": {}},
            handler=tool_list_subagents,
            is_core=True
        )

    def register_tool(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable, is_core: bool = False) -> Dict[str, Any]:
        """Registers a tool, updates A-MEM atomic cards with FIFO eviction, and indexes to Qdrant."""
        clean_name = name.strip().lower()
        tool_def = {
            "name": clean_name,
            "description": description.strip(),
            "parameters": parameters,
            "handler": handler,
            "is_core": is_core,
            "registered_at": time.time()
        }
        self.tools[clean_name] = tool_def

        if not is_core:
            if clean_name not in self.dynamic_tools_order:
                self.dynamic_tools_order.append(clean_name)

            # Enforce FIFO policy on dynamic tools
            while len(self.dynamic_tools_order) > MAX_DYNAMIC_TOOLS_IN_AMEM:
                evicted_name = self.dynamic_tools_order.pop(0)
                logger.info(f"⚡ [A-MEM FIFO] Evicting oldest dynamic tool card: {evicted_name}")
                self._evict_amem_card(f"amem:tool:{evicted_name}")

        # Update A-MEM atomic card (< 35 tokens)
        self._store_amem_tool_card(tool_def)

        # Index to Qdrant
        self._index_tool_to_qdrant(tool_def)

        return {"ok": True, "tool": clean_name, "is_core": is_core}

    # ---- extension points for app packs (docs/LAYERS.md): an app registers into the platform, never the other way ----
    def add_aliases(self, aliases: Dict[str, str]) -> None:
        """Extra names that resolve to a registered tool ('ha_state' -> 'home_assistant_get_state')."""
        self.aliases.update({k.strip().lower(): v.strip().lower() for k, v in aliases.items()})

    def add_prompt_section(self, title: str, body: str) -> None:
        """A block of rules shown to models that use text tool calls, after the platform's own (once per title)."""
        self.prompt_sections = [(t, b) for t, b in self.prompt_sections if t != title] + [(title, body.strip())]

    def add_text_call_names(self, names) -> None:
        """Tool names (and aliases) that extract_tool_calls_from_text accepts in plain `name(arg=...)` form."""
        self.text_call_names.update(n.strip().lower() for n in names)

    def list_tools(self) -> List[Dict[str, Any]]:
        """Returns all registered tools with metadata and amem card summaries."""
        results = []
        for name, t in self.tools.items():
            param_keys = list(t.get("parameters", {}).get("properties", {}).keys())
            results.append({
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters", {}),
                "is_core": t.get("is_core", False),
                "registered_at": t.get("registered_at", 0),
                "amem_card": t.get("custom_amem_card") or f"Tool: `{t['name']}({', '.join(param_keys[:3])})` - {t['description'][:85]}"
            })
        return results

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieves a registered tool by name or alias."""
        clean_name = (name or "").strip().lower()
        resolved = self.aliases.get(clean_name, clean_name)
        return self.tools.get(resolved)

    def get_amem_tool_cards(self) -> List[Dict[str, Any]]:
        """
        Compiles ultra-dense A-MEM atomic fact cards (< 35 tokens each)
        for all active tools (core tools + dynamic tools in FIFO queue).
        """
        cards = []
        # 1. Core tools
        for name, t in self.tools.items():
            if t.get("is_core"):
                param_keys = list(t.get("parameters", {}).get("properties", {}).keys())
                card_text = t.get("custom_amem_card") or f"Tool: `{t['name']}({', '.join(param_keys[:3])})` - {t['description'][:85]}"
                cards.append({
                    "name": t["name"],
                    "is_core": True,
                    "card": card_text,
                    "token_est": len(card_text.split())
                })

        # 2. Active dynamic tools (strictly following FIFO order)
        for d_name in self.dynamic_tools_order:
            t = self.tools.get(d_name)
            if t:
                param_keys = list(t.get("parameters", {}).get("properties", {}).keys())
                card_text = t.get("custom_amem_card") or f"Tool: `{t['name']}({', '.join(param_keys[:3])})` - {t['description'][:85]}"
                cards.append({
                    "name": t["name"],
                    "is_core": False,
                    "card": card_text,
                    "token_est": len(card_text.split())
                })
        return cards

    def register_custom_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        python_code: str = "",
        amem_card: Optional[str] = None
    ) -> Dict[str, Any]:
        """Registers a custom or operator-defined tool."""
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip().lower()

        handler = None
        if python_code and python_code.strip():
            try:
                local_ns = {}
                exec(python_code, {"json": json, "os": os, "time": time, "urllib": urllib}, local_ns)
                handler = local_ns.get("execute") or local_ns.get("run") or local_ns.get("handler")
            except Exception as e:
                logger.warning(f"Error compiling python_code for tool {clean_name}: {e}")

        if not handler:
            def default_handler(**kwargs):
                return {"ok": True, "tool": clean_name, "received_args": kwargs, "status": "executed"}
            handler = default_handler

        tool_def = {
            "name": clean_name,
            "description": description.strip(),
            "parameters": parameters,
            "handler": handler,
            "is_core": False,
            "registered_at": time.time(),
            "custom_amem_card": amem_card
        }
        self.tools[clean_name] = tool_def

        if clean_name not in self.dynamic_tools_order:
            self.dynamic_tools_order.append(clean_name)

        # Enforce FIFO policy on dynamic tools
        while len(self.dynamic_tools_order) > MAX_DYNAMIC_TOOLS_IN_AMEM:
            evicted_name = self.dynamic_tools_order.pop(0)
            logger.info(f"⚡ [A-MEM FIFO] Evicting oldest dynamic tool card: {evicted_name}")
            self._evict_amem_card(f"amem:tool:{evicted_name}")

        self._store_amem_tool_card(tool_def)
        self._index_tool_to_qdrant(tool_def)
        return {"ok": True, "tool": clean_name, "is_core": False}

    def ingest_tool_from_url(self, url: str, name: Optional[str] = None) -> Dict[str, Any]:
        """Downloads, parses, and registers a tool from a URL on operator request."""
        clean_url = (url or "").strip()
        if not clean_url:
            return {"ok": False, "error": "URL parameter is required."}

        try:
            req = urllib.request.Request(clean_url, headers={"User-Agent": "StoneSage/4.0 ToolIngester"})
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw_data = resp.read().decode("utf-8", errors="replace")

            # Determine format: JSON schema vs Python script
            tool_name = name or ""
            desc = ""
            params = {"type": "object", "properties": {}}

            try:
                data = json.loads(raw_data)
                if isinstance(data, dict):
                    tool_name = tool_name or data.get("name") or "dynamic_tool"
                    desc = data.get("description") or f"Tool ingested from {clean_url}"
                    params = data.get("parameters") or {"type": "object", "properties": {}}
            except Exception:
                # Text/script format
                if not tool_name:
                    parsed = urllib.parse.urlparse(clean_url)
                    base = os.path.basename(parsed.path).replace(".py", "").replace(".json", "")
                    tool_name = base or "custom_url_tool"
                desc = f"Dynamically ingested script tool from {clean_url}"
                params = {
                    "type": "object",
                    "properties": {
                        "payload": {"type": "string", "description": "Arguments passed to script."}
                    }
                }

            tool_name = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name).lower()

            # Create an execution closure that fetches or runs the ingested code
            def dynamic_handler(**kwargs):
                return {
                    "ok": True,
                    "tool": tool_name,
                    "source_url": clean_url,
                    "received_args": kwargs,
                    "status": "executed"
                }

            self.register_tool(
                name=tool_name,
                description=desc,
                parameters=params,
                handler=dynamic_handler,
                is_core=False
            )

            self._persist_custom_tool(tool_name, desc, params, clean_url)

            return {
                "ok": True,
                "tool_name": tool_name,
                "description": desc,
                "source_url": clean_url,
                "message": f"Successfully ingested tool '{tool_name}' from URL. Added to active A-MEM and Qdrant."
            }

        except Exception as e:
            return {"ok": False, "error": f"Failed ingesting tool from URL: {e}"}

    def _store_amem_tool_card(self, tool_def: Dict[str, Any]):
        """Stores an ultra-dense atomic fact card (< 35 tokens) in Valkey A-MEM."""
        if not self.amem_enabled:
            return
        name = tool_def["name"]
        desc = tool_def["description"]
        param_keys = list(tool_def.get("parameters", {}).get("properties", {}).keys())
        sig = f"{name}({', '.join(param_keys[:4])})"
        atom_text = f"Tool: `{sig}` - {desc[:90]}"

        tags = ["tool", "tools", name] + [k for k in param_keys[:3]]
        tags.append("core-memory" if tool_def.get("is_core") else "dynamic-tool")

        try:
            from harness.data_fabric.valkey_amem import valkey_amem
            valkey_amem.store_atom(
                atom_id=f"tool_{name}",
                atom_text=atom_text,
                keywords=tags,
                category="tool_registry",
                confidence=1.0,
                is_core_memory=tool_def.get("is_core", False)
            )
        except Exception:
            pass

    def _evict_amem_card(self, card_key: str):
        """Removes an evicted dynamic card from Valkey."""
        if not self.amem_enabled:
            return
        try:
            from harness.data_fabric.valkey_amem import valkey_amem
            if valkey_amem.r:
                valkey_amem.r.delete(card_key)
        except Exception:
            pass

    def _index_tool_to_qdrant(self, tool_def: Dict[str, Any]):
        """Indexes tool specification into Qdrant for semantic discovery.

        Runs in a background thread so a slow/offline embedder never blocks startup,
        and upserts with a deterministic id (uuid5 of the tool name) so restarts do
        not append duplicate tool points to `codebase_knowledge`.
        Set STONESAGE_OFFLINE=1 to skip network indexing entirely (tests, laptops off-LAN).
        """
        if not self.qdrant_enabled or os.environ.get("STONESAGE_OFFLINE") == "1":
            return

        def _worker(td: Dict[str, Any]):
            try:
                from stonesage.cluster_client import ClusterClient
                client = ClusterClient(load_cfg())    # QDRANT_URL still applies (lodestone/endpoints.py)
                content = f"Tool: {td['name']}\nDescription: {td['description']}\nParameters: {json.dumps(td.get('parameters', {}))}"
                client.store_memory(
                    content=content,
                    collection_name="codebase_knowledge",
                    metadata={"type": "tool_specification", "tool_name": td["name"]},
                    point_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"stonesage-tool:{td['name']}")),
                )
            except Exception as e:
                logger.debug(f"Tool indexing skipped for {td.get('name')}: {e}")

        _enqueue_tool_index(_worker, dict(tool_def))

    def _persist_custom_tool(self, name: str, desc: str, params: Dict[str, Any], url: str):
        """Saves dynamic tool metadata to disk."""
        data_dir = _custom_tools_dir()
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "custom_tools.json")
        tools_dict = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tools_dict = json.load(f)
            except Exception:
                pass
        tools_dict[name] = {"description": desc, "parameters": params, "url": url, "added_at": time.time()}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(tools_dict, f, indent=2)
        except Exception:
            pass

    def _load_persisted_tools(self):
        """Restores custom tools from disk on startup."""
        path = os.path.join(_custom_tools_dir(), "custom_tools.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tools_dict = json.load(f)
                for name, info in tools_dict.items():
                    url = info.get("url", "")
                    desc = info.get("description", "")
                    params = info.get("parameters", {})
                    def dynamic_handler(**kwargs):
                        return {"ok": True, "tool": name, "source_url": url, "received_args": kwargs}
                    self.register_tool(name=name, description=desc, parameters=params, handler=dynamic_handler, is_core=False)
            except Exception:
                pass

    def get_openai_tool_definitions(self) -> List[Dict[str, Any]]:
        """Returns standard OpenAI format tool schemas."""
        defs = []
        for name, t in self.tools.items():
            defs.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {"type": "object", "properties": {}})
                }
            })
        return defs

    def compile_tool_system_prompt(self) -> str:
        """Compiles concise tool execution instructions for non-schema endpoints."""
        tool_lines = []
        for name, t in self.tools.items():
            param_names = list(t.get("parameters", {}).get("properties", {}).keys())
            tool_lines.append(f"- `{name}({', '.join(param_names)})`: {t['description']}")
        return (
            "### [HOMELAB AUTONOMOUS SOVEREIGN AGENT TOOL ENGINE]\n"
            "You are operating within the local cluster and homelab environment.\n"
            f"CLUSTER TOPOLOGY (live):\n{_topology_lines()}\n\n"
            "MULTI-AGENT DELEGATION INVARIANTS:\n"
            "1. Grunt Work Delegation: For generating unit tests, linting, docstrings, JSON transforms, or drafting boilerplate, CALL `delegate_worker(task=...)` to offload work to the worker engine without consuming coordinator tokens.\n"
            "2. Background Multi-Step Missions: For multi-step exploration or autonomous tasks, CALL `spawn_subagent(name=..., role=..., mission=...)`.\n"
            "3. Autonomous Loop Holding: Hold the autonomous loop. Take consecutive tool actions (up to 15 turns) to inspect, execute, verify, and refine. Never ask the user to do what your tools can accomplish.\n\n"
            + "".join(f"{title}:\n{body}\n\n" for title, body in self.prompt_sections) +
            "TOOL CALL SYNTAX:\n"
            "To call a tool, emit an exact tool call using either syntax:\n"
            "<|tool_call>call:tool_name{\"param\": \"value\"}<tool_call|>\n"
            "or XML:\n"
            "<tool_call>{\"name\": \"tool_name\", \"arguments\": {\"param\": \"value\"}}</tool_call>\n\n"
            "Available Tools:\n" + "\n".join(tool_lines) + "\n"
            "When you emit a tool call, the StoneSage harness will run it immediately and return the verified result."
        )

    def execute_tool(self, name: str, args: Dict[str, Any], workspace_path: str = "") -> Dict[str, Any]:
        """Executes a tool by name, resolving aliases automatically."""
        clean_name = (name or "").strip().lower()
        resolved_name = self.aliases.get(clean_name, clean_name)

        if resolved_name not in self.tools:
            # Fallback fuzzy matching
            match = next((k for k in self.tools if k in resolved_name or resolved_name in k), None)
            if match:
                resolved_name = match
            else:
                return {
                    "ok": False,
                    "error": f"Tool '{name}' not found in registry. Available: {list(self.tools.keys())}"
                }

        tool = self.tools[resolved_name]
        handler = tool["handler"]
        try:
            # Inject workspace_path into path-based tools if relative path passed
            if workspace_path and "path" in args and not os.path.isabs(args["path"]):
                args["path"] = os.path.normpath(os.path.join(workspace_path, args["path"]))

            return handler(**args)
        except TypeError as te:
            # Argument mismatch; try calling with raw payload if dynamic
            try:
                return handler(payload=json.dumps(args))
            except Exception:
                return {"ok": False, "error": f"Tool argument signature error: {te}"}
        except Exception as ex:
            return {"ok": False, "error": f"Tool execution failed: {ex}"}


# Global tool registry singleton
tool_registry = ToolRegistry()


# ==============================================================================
# Tool Call Extraction from Raw Output Text
# ==============================================================================

def extract_tool_calls_from_text(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Extracts all tool calls from model output text.
    Handles:
    1. Qwen / ChatML: <|tool_call>call:tool_name{args}<tool_call|>
    2. XML format: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    3. JSON codeblocks: ```tool_call {"name": ...} ```
    4. Inline calls: call:tool_name{...}
    """
    if not text:
        return []

    calls: List[Tuple[str, Dict[str, Any]]] = []

    # 1. Qwen / ChatML syntax: <|tool_call>call:NAME{...}<tool_call|>
    qwen_matches = re.finditer(r"<\|?tool_call\|?>call:([a-zA-Z0-9_\-:]+)(\{.*?\})<\|?/?tool_call\|?>", text, re.DOTALL)
    for m in qwen_matches:
        fn_name = m.group(1).strip()
        raw_args = m.group(2).strip()
        # Clean pseudo-markup inside arguments e.g. <|"|>value<|"|> -> "value"
        clean_args = re.sub(r"<\|\"\|>", '"', raw_args)
        clean_args = re.sub(r"<\|'\|>", "'", clean_args)
        try:
            args_obj = json.loads(clean_args)
            calls.append((fn_name, args_obj))
        except Exception:
            # Fallback manual key-value extraction
            kv_args = {}
            for kv in re.finditer(r'([a-zA-Z0-9_]+)\s*:\s*(?:"([^"]+)"|\'([^\']+)\'|([^,}]+))', clean_args):
                k = kv.group(1)
                v = kv.group(2) or kv.group(3) or kv.group(4).strip()
                kv_args[k] = v
            calls.append((fn_name, kv_args))

    if calls:
        return calls

    # 2. XML syntax: <tool_call>...</tool_call>
    xml_matches = re.finditer(r"<(?:tool_call|function_call)>(.*?)</(?:tool_call|function_call)>", text, re.DOTALL)
    for m in xml_matches:
        body = m.group(1).strip()
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                name = data.get("name") or data.get("function")
                args = data.get("arguments") or data.get("parameters") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                if name:
                    calls.append((name, args))
        except Exception:
            pass

    if calls:
        return calls

    if calls:
        return calls

    # 3. Direct call:name{...} format
    direct_matches = re.finditer(r"\bcall:([a-zA-Z0-9_\-]+)\s*(\{.*?\})", text, re.DOTALL)
    for m in direct_matches:
        fn_name = m.group(1).strip()
        raw_args = m.group(2).strip()
        try:
            args_obj = json.loads(raw_args)
            calls.append((fn_name, args_obj))
        except Exception:
            pass

    if calls:
        return calls

    # 4. Python function call syntax: tool_name(param="value", ...)
    registry = globals().get("tool_registry")
    names = registry.text_call_names if registry is not None else set(CORE_TEXT_CALL_NAMES)
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    py_func_pattern = re.finditer(r"\b(" + alternation + r")\s*\(([\s\S]*?)\)", text)
    for m in py_func_pattern:
        fn_name = m.group(1).strip()
        raw_inner = m.group(2).strip()
        kwargs = {}
        for kv in re.finditer(r'([a-zA-Z0-9_]+)\s*=\s*(?:"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\'|"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'|([^,\)]+))', raw_inner):
            k = kv.group(1)
            v = kv.group(2) or kv.group(3) or kv.group(4) or kv.group(5) or kv.group(6)
            if v is not None:
                kwargs[k] = v.strip().strip('"').strip("'")
        if kwargs or not raw_inner:
            calls.append((fn_name, kwargs))

    return calls
