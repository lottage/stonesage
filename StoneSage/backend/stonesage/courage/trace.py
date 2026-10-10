"""
A record of every Courage turn, Boost call and patrol sweep, for debugging ("why did Courage do that?") and, later,
metrics and escalation (plan Phase 6). One JSON line each in data/courage_trace.jsonl, with "kind":

  courage  written by TurnTrace below (fields listed next)
  boost    boost/router.py _report: surface, declared/effective class, kinds of home content found (never the words:
           Boost traffic can hold home text, so no message content is kept), provider, model, ms, tokens, sources
           that failed first; triggers all_failed, fell_through, local_fallback
  memory   courage/memory.py learn: what a conversation turn taught Courage (stored notes, refreshed, nothing, error)
  patrol   patrol.py _trace: camera, scheduled/manual, frames and pans, why it stopped, where the camera went back to,
           vision calls; triggers no_frames, frame_error, vision_error, return_failed, interrupted
  lease    engine_lease.py: acquire, refused (why), checkin, renew (approver, surface), release, expire, revert_failed
  escalation  courage/escalation.py: a failed turn's climb (reason, each rung's outcome, who answered, the local text
           and the higher answer), or a tool failing repeatedly (tool_failing)
  commentary  commentary.py: an unprompted remark (arrival / cooking / cleaning): room, who, facts, outcome spoken |
           logged | held (why) | failed, the remark, timings; triggers llm_error, empty_answer, speak_error

A Courage turn:

  at, session, user (first 300 chars), path, outcome, ms, llm {calls, ms, tokens}, steps [...], final (300 chars),
  triggers [...]

path     how the turn was handled: reflex | learned | approval_yes | approval_no | loop
outcome  answered | asked_approval | step_cap | llm_error | error | abandoned (client went away mid-turn)
steps    each model call ({"llm": ms, "tokens", "calls": n}) and tool call ({"tool", "args", "ok", "ms", "error"})
triggers the objective escalation signals of the stack-limits review, RECORDED ONLY (nothing acts on them yet):
         tool_error, invalid_args, repeated_call (same tool + arguments twice in a turn), nudged (the model described
         a tool call instead of making it), step_cap, llm_error, empty_answer

The file rotates at 5 MB (one old copy kept). Nothing here leaves LXC 120.
"""

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_BYTES = 5 * 1024 * 1024
TEXT = 300
ARG_TEXT = 200


def _short(value: Any, limit: int) -> Any:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def _tool_ok(result: str) -> Tuple[bool, Optional[str]]:
    """(ok, error) from a tool's JSON result; text results count as ok."""
    if not isinstance(result, str) or not result.startswith("{"):
        return True, None
    try:
        d = json.loads(result)
    except ValueError:
        return True, None
    if d.get("ok") is False or d.get("error"):
        return False, _short(d.get("error") or "failed", TEXT)
    return True, None


class TurnTrace:
    """Built up by the agent during one turn; `record()` is what gets written."""

    def __init__(self, session: str, user: str, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.t0 = clock()
        self.session, self.user = session, user
        self.path = "loop"
        self.outcome: Optional[str] = None
        self.steps: List[Dict[str, Any]] = []
        self.triggers: List[str] = []
        self.tokens = 0
        self.llm_ms = 0.0
        self.llm_calls = 0
        self.predicted_ms = 0.0  # pure decode time (llama.cpp timings.predicted_ms), excludes prompt eval
        self.final = ""
        self._seen_calls: set = set()
        self._tool_started: Optional[float] = None
        self.recalled: List[Dict[str, Any]] = []   # memory notes put into the prompt: [{id, score}]
        self.context: List[Dict[str, Any]] = []    # the turn's messages (never written): the escalation ladder reads them
        self.escalated: Optional[Dict[str, Any]] = None  # {outcome, by} when this turn climbed the ladder
        self.brain = "full"                         # "lite" when a lease's stand-in answered (engine_lease.py)

    # ---- what the agent reports ------------------------------------------------
    def trigger(self, name: str) -> None:
        if name not in self.triggers:
            self.triggers.append(name)

    def llm(self, ms: float, tokens: int, calls: int, predicted_ms: float = 0.0) -> None:
        self.llm_calls += 1
        self.llm_ms += ms
        self.tokens += int(tokens or 0)
        self.predicted_ms += predicted_ms or 0.0
        self.steps.append({"llm": round(ms), "tokens": int(tokens or 0), "calls": calls})

    def tool_started(self) -> None:
        self._tool_started = self.clock()

    def tool(self, name: str, args: Dict[str, Any], result: str) -> None:
        ms = (self.clock() - self._tool_started) * 1000 if self._tool_started else None
        self._tool_started = None
        ok, err = _tool_ok(result)
        step = {"tool": name, "args": _short(args, ARG_TEXT), "ok": ok}
        if ms is not None:
            step["ms"] = round(ms)
        if err:
            step["error"] = err
            self.trigger("tool_error")
        self.steps.append(step)
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in self._seen_calls:
            self.trigger("repeated_call")
        self._seen_calls.add(key)

    def invalid(self, name: str, args: Dict[str, Any], error: str) -> None:
        self.steps.append({"tool": name, "args": _short(args, ARG_TEXT), "ok": False, "error": _short(error, TEXT),
                           "refused": True})
        self.trigger("invalid_args")

    def end(self, outcome: str, final: str = "") -> None:
        if self.outcome is None:              # the first reason wins
            self.outcome = outcome
            self.final = final or ""
            if outcome in ("step_cap", "llm_error"):
                self.trigger(outcome)

    # ---- output ------------------------------------------------------------------
    def record(self) -> Dict[str, Any]:
        return {"kind": "courage", "at": round(self.t0, 3), "session": self.session, "user": _short(self.user or "", TEXT),
                "path": self.path, "outcome": self.outcome or "error", "ms": round((self.clock() - self.t0) * 1000),
                "llm": {"calls": self.llm_calls, "ms": round(self.llm_ms), "tokens": self.tokens,
                        "predicted_ms": round(self.predicted_ms)},
                "steps": self.steps, "final": _short(self.final, TEXT), "triggers": self.triggers,
                **({"recalled": self.recalled} if self.recalled else {}),
                **({"escalated": self.escalated} if self.escalated else {}),
                **({"brain": self.brain} if self.brain != "full" else {})}


def _label(v: Any) -> str:
    """A Prometheus label value: escaped, and short (a label is an index, not a log line)."""
    return str(v if v is not None else "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:80]


class TraceMetrics:
    """Counters over every record written since StoneSage started, in Prometheus text format (GET /metrics on
    StoneSage, scraped by Prometheus on LXC 129). They reset on a restart; Prometheus' rate() allows for that."""

    def __init__(self):
        self._lock = threading.Lock()
        self.records: Dict[tuple, int] = {}      # (kind, outcome)
        self.triggers: Dict[tuple, int] = {}     # (kind, trigger)
        self.seconds: Dict[str, List[float]] = {}  # kind -> [sum, count]
        self.boost: Dict[tuple, int] = {}        # (provider, class, outcome)
        self.frames: Dict[str, int] = {}         # patrol camera -> frames taken
        self.tools: Dict[tuple, int] = {}        # (tool, ok)

    def add(self, rec: Dict[str, Any]) -> None:
        kind = rec.get("kind", "courage")
        with self._lock:
            k = (kind, rec.get("outcome"))
            self.records[k] = self.records.get(k, 0) + 1
            for t in rec.get("triggers") or []:
                self.triggers[(kind, t)] = self.triggers.get((kind, t), 0) + 1
            if isinstance(rec.get("ms"), (int, float)):
                acc = self.seconds.setdefault(kind, [0.0, 0])
                acc[0] += rec["ms"] / 1000
                acc[1] += 1
            if kind == "boost":
                b = (rec.get("provider") or "none", rec.get("class"), rec.get("outcome"))
                self.boost[b] = self.boost.get(b, 0) + 1
            if kind == "patrol":
                self.frames[rec.get("camera")] = self.frames.get(rec.get("camera"), 0) + int(rec.get("frames") or 0)
            for st in rec.get("steps") or []:
                if "tool" in st:
                    t = (st["tool"], "true" if st.get("ok") else "false")
                    self.tools[t] = self.tools.get(t, 0) + 1

    def text(self) -> str:
        out: List[str] = []

        def family(name: str, help_: str, kind: str, rows) -> None:
            out.append(f"# HELP {name} {help_}")
            out.append(f"# TYPE {name} {kind}")
            for labels, value in rows:
                lab = ",".join(f'{k}="{_label(v)}"' for k, v in labels)
                out.append(f"{name}{{{lab}}} {value}")

        with self._lock:
            family("stonesage_trace_records_total", "Trace records (Courage turns, Boost calls, patrol sweeps).", "counter",
                   [((("kind", k), ("outcome", o)), n) for (k, o), n in sorted(self.records.items(), key=str)])
            family("stonesage_trace_triggers_total", "Escalation triggers recorded (nothing acts on them yet).", "counter",
                   [((("kind", k), ("trigger", t)), n) for (k, t), n in sorted(self.triggers.items(), key=str)])
            family("stonesage_trace_duration_seconds", "Time per record: a Courage turn, a Boost call, a sweep.", "summary", [])
            for k, (total, count) in sorted(self.seconds.items()):
                out.append(f'stonesage_trace_duration_seconds_sum{{kind="{_label(k)}"}} {round(total, 3)}')
                out.append(f'stonesage_trace_duration_seconds_count{{kind="{_label(k)}"}} {count}')
            family("stonesage_boost_calls_total", "Boost calls by answering source, content class and outcome.", "counter",
                   [((("provider", p), ("class", c), ("outcome", o)), n) for (p, c, o), n in sorted(self.boost.items(), key=str)])
            family("stonesage_patrol_frames_total", "Frames taken by patrol sweeps.", "counter",
                   [((("camera", c),), n) for c, n in sorted(self.frames.items(), key=str)])
            family("stonesage_courage_tool_calls_total", "Courage tool calls by tool and whether they succeeded.", "counter",
                   [((("tool", t), ("ok", ok)), n) for (t, ok), n in sorted(self.tools.items(), key=str)])
        return "\n".join(out) + "\n"


class TraceLog:
    """Append-only JSONL with size rotation; reads for the StoneSage trace view; counters for Prometheus."""

    def __init__(self, path: str, max_bytes: int = MAX_BYTES):
        self.path, self.max_bytes = path, max_bytes
        self._lock = threading.Lock()
        self.metrics = TraceMetrics()

    def write(self, rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        try:
            self.metrics.add(rec)
        except Exception:
            pass  # counters must never break a turn either
        with self._lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) + len(line) > self.max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass  # tracing must never break a turn

    def recent(self, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Newest first; `kind` keeps only courage / boost / patrol records (records from before kinds = courage)."""
        out: List[Dict[str, Any]] = []
        for p in (self.path, self.path + ".1"):
            try:
                with open(p, encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for ln in reversed(lines):
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if kind and rec.get("kind", "courage") != kind:
                    continue
                out.append(rec)
                if len(out) >= limit:
                    return out
        return out

    def summary(self, hours: float = 24, now: Optional[float] = None, kind: Optional[str] = None) -> Dict[str, Any]:
        """Counts over the last `hours`: records, outcomes, paths, triggers, latency p50/p95, tools used and failing,
        records per kind, Boost providers and patrol cameras."""
        since = (now or time.time()) - hours * 3600
        recs = [r for r in self.recent(100000, kind) if r.get("at", 0) >= since]
        out: Dict[str, Any] = {"hours": hours, "turns": len(recs), "outcomes": {}, "paths": {}, "triggers": {},
                               "tools": {}, "tool_errors": {}, "kinds": {}, "providers": {}, "cameras": {}}
        def bump(table: str, key: Any) -> None:
            if key is not None:
                out[table][key] = out[table].get(key, 0) + 1

        for r in recs:
            bump("kinds", r.get("kind", "courage"))
            bump("outcomes", r.get("outcome"))
            bump("paths", r.get("path"))
            bump("providers", r.get("provider") if r.get("kind") == "boost" else None)
            bump("cameras", r.get("camera") if r.get("kind") == "patrol" else None)
            for t in r.get("triggers", []):
                out["triggers"][t] = out["triggers"].get(t, 0) + 1
            for s in r.get("steps", []):
                if "tool" in s:
                    out["tools"][s["tool"]] = out["tools"].get(s["tool"], 0) + 1
                    if not s.get("ok"):
                        out["tool_errors"][s["tool"]] = out["tool_errors"].get(s["tool"], 0) + 1
        ms = sorted(r.get("ms", 0) for r in recs)
        out["ms_p50"] = ms[len(ms) // 2] if ms else None
        out["ms_p95"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None
        return out
