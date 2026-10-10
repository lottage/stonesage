"""
Polls which compute sources are actively serving requests and pushes a consolidated
roster status to the watch bridge every POLL_S seconds -- the "debounce >= 10s" the
scaffold's own CLAUDE.md asks for turns out to be exactly the poll interval.

coordinator / worker: polled directly against the live llama-server /slots endpoint
(is any slot mid-generation right now?), not inferred from Courage's own call
bookkeeping -- "which node is in use" should reflect the node, not just this one
caller of it.
boost / frontier: no engine endpoint to poll (Boost fans out to cloud providers;
frontier_worker.py isn't installed yet), so these read the trace log's most recent
record of that kind and call it "active" if it happened within RECENT_S seconds.
"""

import json
import logging
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("StoneSage.Courage.WatchStatus")

RECENT_S = 20   # a trace-log record counts as "active" for this long afterward
POLL_S = 15


def _engine_busy(v1_url: str, timeout: float = 3.0) -> Optional[bool]:
    """True/False if the llama-server behind this /v1 URL has a slot mid-generation; None if unreachable."""
    base = v1_url[:-3] if v1_url.endswith("/v1") else v1_url
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/slots", timeout=timeout) as r:
            slots = json.loads(r.read().decode())
        return any(bool(s.get("is_processing")) for s in slots)
    except Exception:
        return None


def _trace_active(trace_log, kind: str, now: float) -> bool:
    try:
        recs = trace_log.recent(1, kind=kind)
    except Exception:
        return False
    return bool(recs) and (now - recs[0].get("at", 0)) <= RECENT_S


def _recent_tps(trace_log, now: float) -> Optional[float]:
    """Pure decode tokens/sec (llama.cpp timings.predicted_ms, excludes prompt eval --
    the same figure the chat UI's own usage.tps shows) of the most recent Courage
    turn, if it happened recently -- None (not 0) once stale, so the face shows
    "--" rather than a frozen number from minutes ago. Using llm.ms (wall clock,
    includes prompt processing) here instead reads 5-10x low versus what the model
    actually reports -- caught comparing the two live during this integration."""
    try:
        recs = trace_log.recent(1, kind="courage")
    except Exception:
        return None
    if not recs or (now - recs[0].get("at", 0)) > RECENT_S:
        return None
    llm = recs[0].get("llm") or {}
    predicted_ms = llm.get("predicted_ms")
    tokens = llm.get("tokens")
    if not predicted_ms or not tokens:
        return None
    return tokens / (predicted_ms / 1000.0)


def _poll_once(watch, trace_log, coordinator_url: str, worker_url: str) -> None:
    now = time.time()
    coord_busy = _engine_busy(coordinator_url)
    work_busy = _engine_busy(worker_url)
    watch.status([
        {"p": "coordinator", "s": "run" if coord_busy else ("err" if coord_busy is None else "idle")},
        {"p": "worker", "s": "run" if work_busy else ("err" if work_busy is None else "idle")},
        {"p": "boost", "s": "run" if _trace_active(trace_log, "boost", now) else "idle"},
        {"p": "frontier", "s": "run" if _trace_active(trace_log, "frontier", now) else "idle"},
    ])
    tps = _recent_tps(trace_log, now)
    if tps is not None:
        watch.metrics(tps=round(tps, 1))


def start(watch, trace_log, coordinator_url: str, worker_url: str) -> None:
    def _loop():
        while True:
            try:
                _poll_once(watch, trace_log, coordinator_url, worker_url)
            except Exception as e:
                logger.warning(f"watch status poll failed: {e}")
            time.sleep(POLL_S)

    threading.Thread(target=_loop, name="courage-watch-status", daemon=True).start()
