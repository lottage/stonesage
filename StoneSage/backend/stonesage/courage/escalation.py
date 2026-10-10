"""
Escalation ladder (Phase 6, 2026-09-28): what happens when a Computer turn fails, on OBJECTIVE triggers only.

A turn has failed when the loop hit its step cap, the local brain (:8001) did not answer, or the answer came back empty.
A tool error Computer reported honestly ("the camera didn't respond") is a correct answer, not a failure, and never
escalates by itself. Rungs, in order, each only when it is available:

  boost     a bigger free model through the Boost router (surface "courage", never the local coordinator: that already
            failed). Tool-free: it gets the question and the attempt history (the tool calls and what they returned) and
            answers from that alone, or says what is missing. The router's egress scan decides who may see it (home text
            only to no_training sources, never pictures). Its answer replaces the failure text, marked as such.
            2026-09-28: Boost is off and has no keys, so this rung is skipped until the owner adds them.
  frontier  the owner's Claude Code / Gemini CLI through frontier_worker.py: not installed yet, so reported "not installed".
  human     a phone push (HA notify) and a watch "err" event with the question, why it failed and what was tried.
            Policy like phone approvals (config courage.escalation.human: voice = turns that came through Home
            Assistant (default) | always | never), at most one per human_every_min and human_daily a day, and the same
            question once. The answer then says the details went to the phone.

Separately, a tool that errors tool_alert_errors times within an hour, across turns, is a broken integration rather
than a hard question: the human rung hears about it once per tool_alert_every_h.

Every escalation is one trace record, kind "escalation": the failed turn (session, at), why, each rung's outcome and
ms, and when a higher rung answered, the local text next to that answer (the "verdict" pair that correction capture
needs later).
"""

import re
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

FAILED_OUTCOMES = ("step_cap", "llm_error")
DEFAULTS = {"enabled": True, "human": "voice", "human_every_min": 30, "human_daily": 6,
            "tool_alert_errors": 3, "tool_alert_every_h": 6}
REASONS = {"step_cap": "went round in circles and hit the step limit", "llm_error": "the local brain on :8001 didn't answer",
           "empty_answer": "came back with an empty answer"}
BOOST_SYSTEM = (
    "You are helping Computer, a home assistant, that got stuck on a request. You have no tools. Below are the request "
    "and everything Computer's tools returned. Answer the request in one to three plain sentences using only that "
    "information. If it does not contain what is needed, say briefly what is missing; never guess facts about the home.")


def failed(outcome: Optional[str], triggers: List[str]) -> Optional[str]:
    """Why this turn failed (a REASONS key), or None when it did not."""
    if outcome in FAILED_OUTCOMES:
        return outcome
    return "empty_answer" if "empty_answer" in triggers else None


def attempt_history(messages: List[Dict[str, Any]], limit: int = 2400) -> str:
    """The tool calls this turn made and what they returned, as short text (no system prompt, no earlier turns)."""
    start = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    lines = []
    for m in messages[start + 1:]:
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            lines.append(f"called {fn.get('name')}({fn.get('arguments') or ''})"[:300])
        if m.get("role") == "tool":
            lines.append(f"  -> {str(m.get('content') or '')[:500]}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit] + "…"


def tried_summary(steps: List[Dict[str, Any]], limit: int = 3) -> str:
    """'presence_now failed: timeout; camera_look ok' from a TurnTrace's steps."""
    tools = [s for s in steps if "tool" in s]
    parts = [f"{s['tool']} " + (f"failed: {s.get('error')}" if not s.get("ok") else "ok") for s in tools[-limit:]]
    return "; ".join(parts) or "no tools ran"


class Escalation:
    def __init__(self, cfg_fn: Callable[[], Dict[str, Any]],
                 boost: Optional[Callable[[List[Dict[str, str]]], Dict[str, Any]]] = None,
                 boost_available: Optional[Callable[[], bool]] = None,
                 frontier_available: Optional[Callable[[], bool]] = None,
                 push: Optional[Callable[[str, str], Any]] = None, watch: Optional[Callable[[str], Any]] = None,
                 trace: Optional[Callable[[Dict[str, Any]], None]] = None, clock: Callable[[], float] = time.time):
        """boost(messages) -> {ok, answer, source | error}; push(title, message) -> phone; watch(text) -> watch err."""
        self.cfg_fn, self.boost, self.boost_available = cfg_fn, boost, boost_available
        self.frontier_available, self.push, self.watch, self.trace, self.clock = frontier_available, push, watch, trace, clock
        self._human_sent: Deque[float] = deque(maxlen=100)
        self._asked: Dict[str, float] = {}                   # normalised question -> when a human was told
        self._tool_errors: Dict[str, Deque[float]] = {}
        self._tool_alerted: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _cfg(self) -> Dict[str, Any]:
        return {**DEFAULTS, **((self.cfg_fn().get("courage") or {}).get("escalation") or {})}

    # ------------------------------------------------------------------ rungs ----
    def _boost(self, question: str, history: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
        if not self.boost or not (self.boost_available and self.boost_available()):
            return "unavailable", None, {"why": "Boost is off for Computer (or has no keys)"}
        t0 = time.time()
        msgs = [{"role": "system", "content": BOOST_SYSTEM},
                {"role": "user", "content": f"Request: {question}\n\nWhat Computer's tools returned:\n{history or '(nothing: no tool ran)'}"}]
        try:
            res = self.boost(msgs) or {}
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        info = {"ms": round((time.time() - t0) * 1000)}
        if res.get("ok") and (res.get("answer") or "").strip():
            info["source"] = res.get("source")
            return "answered", res["answer"].strip(), info
        info["error"] = str(res.get("error") or "no answer")[:200]
        return "failed", None, info

    def _human_allowed(self, cfg: Dict[str, Any], session: str, key: Optional[str]) -> Optional[str]:
        """None when a push may go now, else why not."""
        policy = cfg["human"]
        if policy == "never":
            return "policy never"
        if policy == "voice" and not session.startswith("ha:"):
            return "not a voice turn (policy voice)"
        now = self.clock()
        if key and now - self._asked.get(key, -1e12) < 86400:
            return "already told about this question today"
        if self._human_sent and now - self._human_sent[-1] < float(cfg["human_every_min"]) * 60:
            return "rate limit"
        if sum(1 for t in self._human_sent if now - t < 86400) >= int(cfg["human_daily"]):
            return "daily limit"
        return None

    def _tell_human(self, title: str, message: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.push:
            try:
                res = self.push(title, message) or {}
                out["phone"] = bool(res.get("ok", True))
            except Exception as e:
                out["phone"], out["phone_error"] = False, str(e)[:120]
        if self.watch:
            try:
                self.watch(f"{title}: {message}"[:180])
                out["watch"] = True
            except Exception:
                out["watch"] = False
        return out

    # --------------------------------------------------------------- the ladder ----
    def handle(self, session: str, question: str, local_text: str, reason: str, steps: List[Dict[str, Any]],
               messages: List[Dict[str, Any]], turn_at: float) -> Dict[str, Any]:
        """Climb for one failed turn. Returns {"content": what to tell the user, "record": the trace record}."""
        cfg = self._cfg()
        rec: Dict[str, Any] = {"kind": "escalation", "at": round(self.clock(), 3), "session": session,
                               "turn_at": turn_at, "reason": reason, "user": question[:300], "local": local_text[:300],
                               "rungs": [], "triggers": [reason]}
        content, rec["outcome"] = local_text, "unresolved"
        state, answer, info = self._boost(question, attempt_history(messages))      # no lock: up to 30 s of network
        rec["rungs"].append({"rung": "boost", "outcome": state, **info})
        if answer:
            rec.update(outcome="answered", answered_by="boost", answer=answer[:600])
            self._write(rec)
            return {"content": f"{answer} (I got stuck, so a bigger brain answered from what I'd found.)", "record": rec}
        installed = bool(self.frontier_available and self.frontier_available())
        rec["rungs"].append({"rung": "frontier", "outcome": "not_wired" if installed else "unavailable",
                             "why": "no question-answering job in the frontier worker yet" if installed
                             else "frontier worker not installed"})
        key = re.sub(r"\W+", " ", question.lower()).strip()[:120]
        with self._lock:                          # two failing turns at once must not both pass the rate limit
            why_not = self._human_allowed(cfg, session, key)
            if not why_not:
                self._asked[key] = self.clock()
                self._human_sent.append(self.clock())
        if why_not:
            rec["rungs"].append({"rung": "human", "outcome": "skipped", "why": why_not})
        else:
            msg = f"“{question[:120]}”: {REASONS.get(reason, reason)}. Tried: {tried_summary(steps)}."
            told = self._tell_human("Computer got stuck", msg)
            rec["rungs"].append({"rung": "human", "outcome": "told", **told})
            rec.update(outcome="handed_to_human", answered_by="human")
            if told.get("phone"):
                content = f"{local_text} I've sent the details to the phone."
        self._write(rec)
        return {"content": content, "record": rec}

    def tool_errors(self, steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Count this turn's tool errors; a tool failing tool_alert_errors times in an hour is told to a human
        (policy "never" silences it too) once per tool_alert_every_h. Returns the trace record when an alert went out."""
        cfg, now = self._cfg(), self.clock()
        failing = [(s["tool"], s.get("error") or "") for s in steps if "tool" in s and not s.get("ok") and not s.get("refused")]
        for tool, err in failing:
            q = self._tool_errors.setdefault(tool, deque(maxlen=50))
            q.append(now)
            recent = [t for t in q if now - t <= 3600]
            if len(recent) < int(cfg["tool_alert_errors"]):
                continue
            if now - self._tool_alerted.get(tool, -1e12) < float(cfg["tool_alert_every_h"]) * 3600 or cfg["human"] == "never":
                continue
            self._tool_alerted[tool] = now
            self._human_sent.append(now)
            told = self._tell_human("Computer: a tool keeps failing",
                                    f"{tool} failed {len(recent)} times in the last hour. Latest: {err[:160]}")
            rec = {"kind": "escalation", "at": round(now, 3), "reason": "tool_failing", "tool": tool,
                   "errors_1h": len(recent), "latest_error": err[:200], "rungs": [{"rung": "human", "outcome": "told", **told}],
                   "outcome": "handed_to_human", "triggers": ["tool_failing"]}
            self._write(rec)
            return rec
        return None

    def _write(self, rec: Dict[str, Any]) -> None:
        if self.trace:
            try:
                self.trace(rec)
            except Exception:
                pass
