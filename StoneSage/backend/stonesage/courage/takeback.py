"""
Takeback (2026-10-13): Computer takes the GPU back from background work (the garden) on demand.

Background: the garden may borrow the RX 6750 XT (an engine-lease layout that evicts the coordinator). While it does, Computer answers
from the stand-in on the CPU ("lite": short, every action asks, no cameras worth the name). That is fine for "turn off the lamp"
and not fine for "what's on the driveway camera?". Most house commands reach Computer through Home Assistant's Ollama API, the web
chat and the Alexa relay; all three call CourageAgent.run(), which hands the turn to Takeback.run() when this is set.

When it applies: the held lease has `yield_to_computer` (background work), is starting or active, and has the coordinator lent
out (engine_lease.yielding). Then a request runs on the spare brain first (a "probe": no escalation ladder) and is taken back when

  a. the spare brain fails on the objective triggers (step cap, no answer from the stand-in, empty answer; a tool error it reported
     honestly is an answer and never counts), or on_loan: no stand-in is answering at all;
  b. it picks a tool that needs the full Computer (`courage.takeback.tools`, default camera_look, camera_scan, think_harder),
     decided BEFORE the tool runs;
  c. a person starts using the cockpit (stonesage/activity.py): no request is waiting then, the GPU simply goes back.

A takeback: (1) the registered yielders stop whatever uses the GPU (the garden scheduler stops its session, so the worker saves the draft,
reason "takeback: <why>", and starts its cooldown), (2) the lease is released (the existing revert path starts the coordinator and
checks /health; it runs on its own thread), (3) wait for the coordinator to answer, at most `wait_s` (90), (4) run the SAME request again
on the full brain (`_turn(full=True)`). A stream first gets "One moment." (a 'holding' event: streamed(holding=True) turns it into text;
the Alexa relay and non-streaming callers never see it, and the relay announces a late answer on the Echo as before).
If the coordinator is not back in time the user gets the spare brain's answer, or an honest "still coming back" line when it never answered;
the revert goes on in the background; the trace says `takeback_failed` and the phone is told.
Afterwards the garden does not borrow a coordinator again for `cooldown_min` (60).

Layers: this is the platform. It imports nothing from an app. The garden plugs in with register_yielder(fn(why, cooldown_s)).

Config (config.json `courage.takeback`): enabled (true), tools (list; replaces the default), wait_s (90), cooldown_min (60), poll_s (1).
Trace kind `takeback`: why (tool | step_cap | llm_error | empty_answer | on_loan | cockpit), tool, source, outcome (ok | takeback_failed),
coordinator_ms (until it answered), user_wait_ms (the whole request, None for a cockpit takeback), lease, layout.
Prometheus: stonesage_takeback_total{why,outcome}, stonesage_takeback_user_wait_seconds (histogram).
"""

import json
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "tools": ["camera_look", "camera_scan", "think_harder"],
    "wait_s": 90,
    "cooldown_min": 60,
    "poll_s": 1,
}
FILLER = "One moment."           # a line of agent.CANNED_LINES: its audio is already cached for the speakers
STILL_COMING_BACK = "That one needs the full brain, and it is still on its way back from a loan. Ask me again in a minute."
BUCKETS = (5, 10, 20, 30, 45, 60, 90, 120)
WHYS = ("tool", "step_cap", "llm_error", "empty_answer", "on_loan", "cockpit")


class Takeback:
    def __init__(self, cfg_fn: Callable[[], Dict[str, Any]], lease_fn: Callable[[], Any],
                 coordinator_ready: Callable[[], bool], trace: Optional[Callable[[Dict[str, Any]], None]] = None,
                 push: Optional[Callable[[str, str], Any]] = None, path: Optional[str] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        """lease_fn() -> the EngineLease (or None before it exists); coordinator_ready() -> the real coordinator answers /health."""
        self.cfg_fn, self.lease_fn, self.coordinator_ready = cfg_fn, lease_fn, coordinator_ready
        self.trace, self.push, self.path, self.clock, self.sleep = trace, push, path, clock, sleep
        self.yielders: List[Callable[[str, float], Any]] = []
        self.last: Optional[Dict[str, Any]] = None
        self.cooldown_until = 0.0
        self.counts: Dict[tuple, int] = {}
        self.waits: List[float] = []                   # user waits (s), for the histogram
        self._lock = threading.Lock()
        self._current: Optional[Dict[str, Any]] = None
        self._cockpit_starting = False
        self._taken_id: Optional[str] = None             # the lease we released: its revert may still be running (stand-in stopping)
        self._load()

    # ------------------------------------------------------------------ config / state ----
    def cfg(self) -> Dict[str, Any]:
        raw = ((self.cfg_fn() or {}).get("courage") or {}).get("takeback")
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
        for k, default in (("wait_s", 90), ("cooldown_min", 60), ("poll_s", 1)):
            try:
                out[k] = max(0.0, float(out[k]))
            except (TypeError, ValueError):
                out[k] = float(default)
        out["poll_s"] = max(0.05, out["poll_s"])
        if not isinstance(out["tools"], list):
            out["tools"] = list(DEFAULTS["tools"])
        return out

    def enabled(self) -> bool:
        return bool(self.cfg()["enabled"])

    def wants_full(self, tool: str) -> bool:
        """Is `tool` one the spare brain must not run (it needs the full Computer)?"""
        return bool(tool) and tool in [str(t) for t in self.cfg()["tools"]]

    def register_yielder(self, fn: Callable[[str, float], Any]) -> None:
        """An app that uses the borrowed GPU registers how to stop: fn(why, cooldown_s) stops its work now (it may be called from any
        thread and must return quickly; the lease is released by the takeback itself)."""
        if fn not in self.yielders:
            self.yielders.append(fn)

    def cooldown_left(self) -> float:
        """Seconds until a coordinator-evicting layout may be borrowed again (0 = now)."""
        return max(0.0, self.cooldown_until - self.clock())

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.cooldown_until = float(d.get("cooldown_until") or 0.0)
            self.last = d.get("last") if isinstance(d.get("last"), dict) else None
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"cooldown_until": self.cooldown_until, "last": self.last}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ------------------------------------------------------------------ is there anything to take back ----
    def reverting(self) -> bool:
        """True while the lease we released is still held (the revert is finishing: the coordinator is up or coming up, the stand-in
        goes last). Such a lease is not taken back again."""
        if not self._taken_id:
            return False
        held = getattr(self.lease_fn(), "lease", None)
        if isinstance(held, dict) and held.get("id") == self._taken_id:
            return True
        self._taken_id = None
        return False

    def yielding_lease(self) -> Optional[Dict[str, Any]]:
        if not self.enabled() or self.reverting():
            return None
        lease = self.lease_fn()
        fn = getattr(lease, "yielding", None)
        try:
            return fn("coordinator") if fn else None
        except Exception:
            return None

    # ------------------------------------------------------------------ the turn ----
    def run(self, agent: Any, history: List[Dict[str, Any]], session_id: str) -> Iterator[Dict[str, Any]]:
        """agent.run() with a takeback: the events of the turn, as agent._turn would give them."""
        if self._current is None and self.reverting():
            # a takeback has finished but the lease is still winding down: the real coordinator when it answers, else the spare one
            try:
                ready = bool(self.coordinator_ready())
            except Exception:
                ready = False
            yield from agent._turn(history, session_id, full=ready)
            return
        if self._current is None and self.yielding_lease() is None:
            yield from agent._turn(history, session_id)
            return
        t_user = self.clock()
        why, source, tool, lite_final = None, "", None, None
        if self._current is None:
            for ev in agent._turn(history, session_id, probe=True):
                kind = ev["type"]
                if kind == "takeback_request":
                    why, tool = ev.get("why") or "tool", ev.get("tool")
                elif kind == "final" and ev.get("failed"):
                    why, lite_final = ev["failed"], ev          # held back: the user hears the outcome of the takeback instead
                else:
                    yield ev
            if why is None:
                return
        else:
            why = "cockpit"                                      # a takeback is already under way (the page woke it): join it
        yield {"type": "holding", "content": FILLER}
        res: Dict[str, Any] = {}
        ok = False
        try:
            res = self.take_back(why, tool=tool, source="request")
            ok = bool(res.get("ok"))
            if ok:
                yield from agent._turn(history, session_id, full=True)
            else:
                yield {"type": "final", "content": (lite_final or {}).get("content") or STILL_COMING_BACK}
        finally:
            self._record(res, why, tool, "request", ok, user_wait_ms=round((self.clock() - t_user) * 1000))

    # ------------------------------------------------------------------ the takeback itself ----
    def take_back(self, why: str, tool: Optional[str] = None, source: str = "request") -> Dict[str, Any]:
        """Stop the borrower, release the lease, wait (bounded) for the coordinator. One at a time: a second caller waits for the
        first one's result. Returns {ok, coordinator_ms, error?, lease, layout}."""
        with self._lock:
            cur = self._current
            owner = cur is None
            if owner:
                cur = self._current = {"done": threading.Event(), "result": None}
        if not owner:
            cur["done"].wait(self.cfg()["wait_s"] + 30)
            return cur["result"] or {"ok": False, "error": "the takeback did not finish", "coordinator_ms": None}
        res: Dict[str, Any] = {"ok": False, "error": "takeback crashed", "coordinator_ms": None}
        try:
            res = self._do(why)
        finally:
            cur["result"] = res
            with self._lock:
                self._current = None
            cur["done"].set()
        return res

    def _do(self, why: str) -> Dict[str, Any]:
        cfg = self.cfg()
        t0 = self.clock()
        lease_obj = self.lease_fn()
        lease = self.yielding_lease() or {}
        cooldown_s = cfg["cooldown_min"] * 60
        self.cooldown_until = t0 + cooldown_s                 # from the first moment: the garden must not come straight back
        reason = "takeback: " + why
        stopped = []
        for fn in list(self.yielders):
            try:
                stopped.append(fn(reason, cooldown_s))
            except Exception as e:                            # a borrower that cannot stop must not keep the GPU from Computer
                stopped.append({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:100])})
        if lease and lease_obj is not None:
            self._taken_id = lease.get("id")
            threading.Thread(target=self._release, args=(lease_obj, lease.get("id"), reason), name="takeback-release",
                             daemon=True).start()
        deadline = t0 + cfg["wait_s"]
        ready = False
        while True:
            try:
                ready = bool(self.coordinator_ready())
            except Exception:
                ready = False
            if ready or self.clock() >= deadline:
                break
            self.sleep(cfg["poll_s"])
        ms = round((self.clock() - t0) * 1000)
        res = {"ok": ready, "coordinator_ms": ms if ready else None, "waited_ms": ms, "lease": lease.get("id"),
               "layout": lease.get("layout"), "stopped": stopped}
        if not ready:
            res["error"] = "the coordinator did not answer within %d s" % cfg["wait_s"]
        return res

    @staticmethod
    def _release(lease_obj: Any, lease_id: Optional[str], reason: str) -> None:
        try:
            lease_obj.release(lease_id, why=reason, approver="takeback")
        except Exception:
            pass

    # ------------------------------------------------------------------ the page woke it ----
    def on_cockpit_activity(self, source: str = "") -> None:
        """A beat from stonesage/activity.py (any thread, on every beat, so the fast path is two attribute reads): if a borrowed
        coordinator is out and a person is now at the cockpit, give it back."""
        if self._current is not None or self._cockpit_starting or not self.enabled():
            return
        if self.yielding_lease() is None:
            return
        with self._lock:
            if self._cockpit_starting or self._current is not None:
                return
            self._cockpit_starting = True

        def go() -> None:
            try:
                t0 = self.clock()
                res = self.take_back("cockpit", source="cockpit")
                self._record(res, "cockpit", None, "cockpit", bool(res.get("ok")), user_wait_ms=None, t0=t0)
            finally:
                self._cockpit_starting = False

        threading.Thread(target=go, name="takeback-cockpit", daemon=True).start()

    # ------------------------------------------------------------------ trace, metrics, status ----
    def _record(self, res: Dict[str, Any], why: str, tool: Optional[str], source: str, ok: bool,
                user_wait_ms: Optional[int], t0: Optional[float] = None) -> None:
        outcome = "ok" if ok else "takeback_failed"
        rec = {"kind": "takeback", "at": round(t0 if t0 is not None else self.clock(), 3), "why": why, "source": source,
               "outcome": outcome, "coordinator_ms": res.get("coordinator_ms"), "waited_ms": res.get("waited_ms"),
               "user_wait_ms": user_wait_ms, "lease": res.get("lease"), "layout": res.get("layout"),
               "triggers": [] if ok else ["takeback_failed"]}
        if tool:
            rec["tool"] = tool
        if res.get("error"):
            rec["error"] = str(res["error"])[:200]
        key = (why if why in WHYS else "other", outcome)
        self.counts[key] = self.counts.get(key, 0) + 1
        if user_wait_ms is not None:
            self.waits.append(user_wait_ms / 1000.0)
            del self.waits[:-500]
        self.last = rec
        self._save()
        if self.trace:
            try:
                self.trace(rec)
            except Exception:
                pass
        if not ok and self.push:
            try:
                self.push("Computer: the full brain is slow to come back",
                          "A %s takeback: %s. The spare brain is answering until it is back." % (why, rec.get("error") or "no answer"))
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        return {"enabled": self.enabled(), "last": self.last, "cooldown_until": self.cooldown_until if self.cooldown_left() else None,
                "cooldown_left_s": round(self.cooldown_left()), "in_progress": self._current is not None,
                "tools": list(self.cfg()["tools"]), "wait_s": self.cfg()["wait_s"]}

    def metrics(self) -> List[str]:
        lines = ["# HELP stonesage_takeback_total Takebacks of a borrowed coordinator, by why and outcome (ok | takeback_failed).",
                 "# TYPE stonesage_takeback_total counter"]
        for (why, outcome), n in sorted(self.counts.items()):
            lines.append('stonesage_takeback_total{why="%s",outcome="%s"} %d' % (why, outcome, n))
        lines += ["# HELP stonesage_takeback_user_wait_seconds How long a request waited because of a takeback (the whole request).",
                  "# TYPE stonesage_takeback_user_wait_seconds histogram"]
        for b in BUCKETS:
            lines.append('stonesage_takeback_user_wait_seconds_bucket{le="%d"} %d' % (b, sum(1 for w in self.waits if w <= b)))
        lines += ['stonesage_takeback_user_wait_seconds_bucket{le="+Inf"} %d' % len(self.waits),
                  "stonesage_takeback_user_wait_seconds_sum %.3f" % sum(self.waits),
                  "stonesage_takeback_user_wait_seconds_count %d" % len(self.waits),
                  "# HELP stonesage_takeback_cooldown_seconds Seconds until a coordinator-evicting garden layout may be borrowed again.",
                  "# TYPE stonesage_takeback_cooldown_seconds gauge",
                  "stonesage_takeback_cooldown_seconds %d" % round(self.cooldown_left())]
        return lines
