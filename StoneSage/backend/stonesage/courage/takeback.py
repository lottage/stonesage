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

Per card (2026-10-14, "vision during garden time", docs/garden/DESIGN.md): a trigger names the resource it needs, and takes back only the
card holding it. The coordinator (the RX 6750 XT lease, taken back as above) and vision (the RX 6600 lease; the vision server) each have a
cooldown of their own. A person-started camera request (camera_look, camera_scan -- `courage.takeback.vision_tools`) while the garden holds
the vision card is a HOT-LOAD: say "One moment.", stop the garden session (the worker saves the draft, reason "takeback: <why>"), release the
6600 lease, wait for the vision server to answer (measured 5.5 s from restart to /health; bound `vision_wait_s`, 60), and carry on with
the SAME tool call. The same entry point serves the PTZ "Sweep now" button (`hot_load("vision", why="sweep")`). If the garden holds the
6750 instead, a camera question does not disturb it: the spare brain just runs the camera tool (vision is on the other card). After a
hot-load the garden may not borrow the vision card for `vision_cooldown_min` (10).

Layers: this is the platform. It imports nothing from an app. The garden plugs in with register_yielder(fn(why, cooldown_s)).

Config (config.json `courage.takeback`): enabled (true), tools (list, replaces the default: tools the SPARE brain must not run; default
think_harder), vision_tools (camera_look, camera_scan: tools that need the vision card), wait_s (90), cooldown_min (60) -- the coordinator's
-- and vision_wait_s (60), vision_cooldown_min (10) -- vision's --, poll_s (1).
Trace kind `takeback`: card/resource (coordinator | vision), why (tool | step_cap | llm_error | empty_answer | on_loan | cockpit | sweep ...),
tool, source, outcome (ok | takeback_failed),
coordinator_ms (until it answered), user_wait_ms (the whole request, None for a cockpit takeback), lease, layout.
Prometheus: stonesage_takeback_total{card,why,outcome}, stonesage_takeback_user_wait_seconds (histogram, coordinator),
stonesage_hotload_wait_seconds{card="vision"} (histogram), stonesage_takeback_cooldown_seconds{card}.
"""

import inspect
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "tools": ["think_harder"],
    "vision_tools": ["camera_look", "camera_scan"],
    "wait_s": 90,
    "cooldown_min": 60,
    "vision_wait_s": 60,
    "vision_cooldown_min": 10,
    "poll_s": 1,
}
COORDINATOR, VISION = "coordinator", "vision"
RESOURCES = (COORDINATOR, VISION)
FILLER = "One moment."           # a line of agent.CANNED_LINES: its audio is already cached for the speakers
STILL_COMING_BACK = "That one needs the full brain, and it is still on its way back from a loan. Ask me again in a minute."
BUCKETS = (5, 10, 20, 30, 45, 60, 90, 120)
WHYS = ("tool", "step_cap", "llm_error", "empty_answer", "on_loan", "cockpit", "sweep")


def _call_yielder(fn: Callable[..., Any], reason: str, cooldown_s: float, resource: str) -> Any:
    """Yielders written before the cards were told apart take (why, cooldown_s); newer ones also take `resource`."""
    try:
        params = inspect.signature(fn).parameters
        takes = "resource" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        takes = False
    return fn(reason, cooldown_s, resource=resource) if takes else fn(reason, cooldown_s)


class Takeback:
    def __init__(self, cfg_fn: Callable[[], Dict[str, Any]], lease_fn: Callable[[], Any],
                 coordinator_ready: Callable[[], bool], trace: Optional[Callable[[Dict[str, Any]], None]] = None,
                 push: Optional[Callable[[str, str], Any]] = None, path: Optional[str] = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 vision_ready: Optional[Callable[[], bool]] = None, on_vision_back: Optional[Callable[[], Any]] = None):
        """lease_fn() -> the EngineLease (or None before it exists); coordinator_ready() -> the real coordinator answers /health;
        vision_ready() -> the vision server answers /health and no lease holds it; on_vision_back() -> called once after a hot-load
        gave the vision card back (the vision queue starts reading what waited)."""
        self.cfg_fn, self.lease_fn, self.coordinator_ready = cfg_fn, lease_fn, coordinator_ready
        self.vision_ready, self.on_vision_back = vision_ready, on_vision_back
        self.trace, self.push, self.path, self.clock, self.sleep = trace, push, path, clock, sleep
        self.yielders: List[Callable[..., Any]] = []
        self.last: Optional[Dict[str, Any]] = None
        self.cooldowns: Dict[str, float] = {COORDINATOR: 0.0, VISION: 0.0}
        self.counts: Dict[tuple, int] = {}
        self.waits: List[float] = []                   # user waits (s), coordinator takebacks, for the histogram
        self.hot_waits: List[float] = []               # vision hot-load waits (s)
        self._lock = threading.Lock()
        self._current: Optional[Dict[str, Any]] = None
        self._cockpit_starting = False
        self._taken_id: Optional[str] = None             # the lease we released: its revert may still be running (stand-in stopping)
        self._load()

    @property
    def cooldown_until(self) -> float:                   # the coordinator's (kept for older callers and the saved file)
        return self.cooldowns[COORDINATOR]

    @cooldown_until.setter
    def cooldown_until(self, value: float) -> None:
        self.cooldowns[COORDINATOR] = value

    # ------------------------------------------------------------------ config / state ----
    def cfg(self) -> Dict[str, Any]:
        raw = ((self.cfg_fn() or {}).get("courage") or {}).get("takeback")
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
        for k, default in (("wait_s", 90), ("cooldown_min", 60), ("vision_wait_s", 60), ("vision_cooldown_min", 10), ("poll_s", 1)):
            try:
                out[k] = max(0.0, float(out[k]))
            except (TypeError, ValueError):
                out[k] = float(default)
        out["poll_s"] = max(0.05, out["poll_s"])
        for k in ("tools", "vision_tools"):
            if not isinstance(out[k], list):
                out[k] = list(DEFAULTS[k])
        return out

    def enabled(self) -> bool:
        return bool(self.cfg()["enabled"])

    def wants_full(self, tool: str) -> bool:
        """Is `tool` one the spare brain must not run (it needs the full Computer)?"""
        return bool(tool) and tool in [str(t) for t in self.cfg()["tools"]]

    def wants_vision(self, tool: str) -> bool:
        """Does `tool` need the vision card AND is that card out with the garden right now (so a hot-load is due)?"""
        return bool(tool) and tool in [str(t) for t in self.cfg()["vision_tools"]] and self.yielding_lease(VISION) is not None

    def register_yielder(self, fn: Callable[..., Any]) -> None:
        """An app that uses the borrowed GPU registers how to stop: fn(why, cooldown_s[, resource]) stops its work now (it may be called
        from any thread and must return quickly; the lease is released by the takeback itself)."""
        if fn not in self.yielders:
            self.yielders.append(fn)

    def cooldown_left(self, resource: str = COORDINATOR) -> float:
        """Seconds until a layout that evicts `resource` may be borrowed again (0 = now)."""
        return max(0.0, self.cooldowns.get(resource, 0.0) - self.clock())

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.cooldowns[COORDINATOR] = float(d.get("cooldown_until") or 0.0)
            self.cooldowns[VISION] = float((d.get("cooldowns") or {}).get(VISION) or 0.0)
            self.last = d.get("last") if isinstance(d.get("last"), dict) else None
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"cooldown_until": self.cooldowns[COORDINATOR], "cooldowns": dict(self.cooldowns), "last": self.last}, f)
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

    def yielding_lease(self, resource: str = COORDINATOR) -> Optional[Dict[str, Any]]:
        """The held background lease that has `resource` lent out, or None (no lease, a person's lease, a lease on the other card)."""
        if not self.enabled() or self.reverting():
            return None
        lease = self.lease_fn()
        fn = getattr(lease, "yielding", None)
        try:
            return fn(resource) if fn else None
        except Exception:
            return None

    def _ready(self, resource: str) -> bool:
        try:
            if resource == VISION:
                return bool(self.vision_ready()) if self.vision_ready else False
            return bool(self.coordinator_ready())
        except Exception:
            return False

    # ------------------------------------------------------------------ the turn ----
    def run(self, agent: Any, history: List[Dict[str, Any]], session_id: str) -> Iterator[Dict[str, Any]]:
        """agent.run() with a takeback: the events of the turn, as agent._turn would give them."""
        if self._current is None and self.reverting():
            # a takeback has finished but the lease is still winding down: the real coordinator when it answers, else the spare one
            ready = self._ready(COORDINATOR)
            yield from agent._turn(history, session_id, full=ready)
            return
        if self._current is None and self.yielding_lease(COORDINATOR) is None:
            yield from agent._turn(history, session_id)
            return
        t_user = self.clock()
        why, source, tool, lite_final = None, "", None, None
        joining = self._current is not None
        starting = (self.yielding_lease(COORDINATOR) or {}).get("state") == "starting"
        if not joining and starting:
            # The lease is still starting: the coordinator has not been evicted yet (the stand-in comes up first), so there is no
            # spare brain worth probing. Cancel the start before it evicts anything (cheapest: nothing to revert).
            why, tool = "on_loan", None
        elif not joining:
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
        if joining:
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
    def take_back(self, why: str, tool: Optional[str] = None, source: str = "request", resource: str = COORDINATOR) -> Dict[str, Any]:
        """Stop the borrower, release the lease, wait (bounded) for `resource` to answer. One at a time: a second caller waits for the
        first one's result. Returns {ok, coordinator_ms, error?, lease, layout, resource}."""
        with self._lock:
            cur = self._current
            owner = cur is None
            if owner:
                cur = self._current = {"done": threading.Event(), "result": None}
        if not owner:
            cur["done"].wait(max(self.cfg()["wait_s"], self.cfg()["vision_wait_s"]) + 30)
            return cur["result"] or {"ok": False, "error": "the takeback did not finish", "coordinator_ms": None}
        res: Dict[str, Any] = {"ok": False, "error": "takeback crashed", "coordinator_ms": None}
        try:
            res = self._do(why, resource)
        finally:
            cur["result"] = res
            with self._lock:
                self._current = None
            cur["done"].set()
        return res

    def _do(self, why: str, resource: str = COORDINATOR) -> Dict[str, Any]:
        cfg = self.cfg()
        t0 = self.clock()
        lease_obj = self.lease_fn()
        lease = self.yielding_lease(resource) or {}
        prefix = "vision_" if resource == VISION else ""
        wait_s, cooldown_s = cfg[prefix + "wait_s"], cfg[prefix + "cooldown_min"] * 60
        # every card the lease holds comes back with it, so every one of them cools down
        cards = [r for r in RESOURCES if r in (lease.get("evicted_roles") or [])] or [resource]
        for card in cards:
            pre = "vision_" if card == VISION else ""
            self.cooldowns[card] = max(self.cooldowns.get(card, 0.0), t0 + cfg[pre + "cooldown_min"] * 60)   # from the first moment
        reason = "takeback: " + why
        stopped = []
        for fn in list(self.yielders):
            try:
                stopped.append(_call_yielder(fn, reason, cooldown_s, resource))
            except Exception as e:                            # a borrower that cannot stop must not keep the GPU from Computer
                stopped.append({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:100])})
        if lease and lease_obj is not None:
            self._taken_id = lease.get("id")
            releaser = threading.Thread(target=self._release, args=(lease_obj, lease.get("id"), reason), name="takeback-release",
                                        daemon=True)
            releaser.start()
            if lease.get("state") == "starting":
                # A start being cancelled puts back what it began (stand-in, maybe an eviction): the coordinator that answers
                # now may be about to be stopped, so wait for the cancel to finish before trusting /health.
                releaser.join(wait_s)
        deadline = t0 + wait_s
        ready = False
        while True:
            ready = self._ready(resource)
            if ready or self.clock() >= deadline:
                break
            self.sleep(cfg["poll_s"])
        ms = round((self.clock() - t0) * 1000)
        res = {"ok": ready, "coordinator_ms": ms if ready else None, "waited_ms": ms, "lease": lease.get("id"),
               "layout": lease.get("layout"), "stopped": stopped, "resource": resource}
        if not ready:
            res["error"] = "the %s did not answer within %d s" % ("vision server" if resource == VISION else "coordinator", wait_s)
        return res

    @staticmethod
    def _release(lease_obj: Any, lease_id: Optional[str], reason: str) -> None:
        try:
            lease_obj.release(lease_id, why=reason, approver="takeback")
        except Exception:
            pass

    # ------------------------------------------------------------------ hot-load: a person needs the vision card ----
    def hot_load(self, why: str = "tool", tool: Optional[str] = None, source: str = "request", resource: str = VISION) -> Dict[str, Any]:
        """A person-started request needs `resource` (the vision server) and the garden may hold it: stop the garden session, release the
        lease, wait (bounded by vision_wait_s) for the engine, and say when it is there. Does nothing, and costs nothing, when no
        background lease holds the card (the answer is ok, noop). The caller says "One moment." first when it can."""
        if resource != VISION:
            return self.take_back(why, tool=tool, source=source, resource=resource)
        if not self.enabled():
            return {"ok": False, "noop": True, "error": "takeback is switched off"}
        t_user = self.clock()
        if self._current is None and self.reverting():
            deadline = t_user + self.cfg()["vision_wait_s"]       # an earlier takeback is still handing the card back: wait for it
            while not self._ready(VISION) and self.clock() < deadline:
                self.sleep(self.cfg()["poll_s"])
            return {"ok": self._ready(VISION), "noop": True, "resource": VISION}
        if self._current is None and self.yielding_lease(VISION) is None:
            return {"ok": True, "noop": True, "resource": VISION}
        res = self.take_back(why, tool=tool, source=source, resource=VISION)
        ok = bool(res.get("ok"))
        self._record(res, why, tool, source, ok, user_wait_ms=round((self.clock() - t_user) * 1000), resource=VISION)
        if ok and self.on_vision_back:
            try:
                self.on_vision_back()
            except Exception:
                pass
        return res

    # ------------------------------------------------------------------ the page woke it ----
    def on_cockpit_activity(self, source: str = "") -> None:
        """A beat from stonesage/activity.py (any thread, on every beat, so the fast path is two attribute reads): if a borrowed
        coordinator is out and a person is now at the cockpit, give it back."""
        if self._current is not None or self._cockpit_starting or not self.enabled():
            return
        if self.yielding_lease(COORDINATOR) is None:
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
                user_wait_ms: Optional[int], t0: Optional[float] = None, resource: str = COORDINATOR) -> None:
        outcome = "ok" if ok else "takeback_failed"
        rec = {"kind": "takeback", "at": round(t0 if t0 is not None else self.clock(), 3), "why": why, "source": source,
               "card": resource, "resource": resource, "outcome": outcome, "coordinator_ms": res.get("coordinator_ms"),
               "waited_ms": res.get("waited_ms"), "user_wait_ms": user_wait_ms, "lease": res.get("lease"), "layout": res.get("layout"),
               "triggers": [] if ok else ["takeback_failed"]}
        if tool:
            rec["tool"] = tool
        if res.get("error"):
            rec["error"] = str(res["error"])[:200]
        key = (resource, why if why in WHYS else "other", outcome)
        self.counts[key] = self.counts.get(key, 0) + 1
        if user_wait_ms is not None:
            target = self.hot_waits if resource == VISION else self.waits
            target.append(user_wait_ms / 1000.0)
            del target[:-500]
        self.last = rec
        self._save()
        if self.trace:
            try:
                self.trace(rec)
            except Exception:
                pass
        if not ok and self.push:
            what = "the camera engine" if resource == VISION else "the full brain"
            tail = ("Camera looks fall back to Frigate until it is back." if resource == VISION
                    else "The spare brain is answering until it is back.")
            try:
                self.push("Computer: %s is slow to come back" % what, "A %s takeback: %s. %s" % (why, rec.get("error") or "no answer", tail))
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        return {"enabled": self.enabled(), "last": self.last, "cooldown_until": self.cooldown_until if self.cooldown_left() else None,
                "cooldown_left_s": round(self.cooldown_left()), "in_progress": self._current is not None,
                "cooldowns_left_s": {r: round(self.cooldown_left(r)) for r in RESOURCES},
                "tools": list(self.cfg()["tools"]), "vision_tools": list(self.cfg()["vision_tools"]),
                "wait_s": self.cfg()["wait_s"], "vision_wait_s": self.cfg()["vision_wait_s"]}

    @staticmethod
    def _histogram(name: str, help_text: str, waits: List[float], label: str = "") -> List[str]:
        lab = label + "," if label else ""
        tail = "{%s}" % label if label else ""
        lines = ["# HELP %s %s" % (name, help_text), "# TYPE %s histogram" % name]
        for b in BUCKETS:
            lines.append('%s_bucket{%sle="%d"} %d' % (name, lab, b, sum(1 for w in waits if w <= b)))
        lines += ['%s_bucket{%sle="+Inf"} %d' % (name, lab, len(waits)),
                  "%s_sum%s %.3f" % (name, tail, sum(waits)),
                  "%s_count%s %d" % (name, tail, len(waits))]
        return lines

    def metrics(self) -> List[str]:
        lines = ["# HELP stonesage_takeback_total Takebacks of a borrowed card, by card, why and outcome (ok | takeback_failed).",
                 "# TYPE stonesage_takeback_total counter"]
        for (card, why, outcome), n in sorted(self.counts.items()):
            lines.append('stonesage_takeback_total{card="%s",why="%s",outcome="%s"} %d' % (card, why, outcome, n))
        lines += self._histogram("stonesage_takeback_user_wait_seconds",
                                 "How long a request waited because of a takeback (the whole request).", self.waits)
        lines += self._histogram("stonesage_hotload_wait_seconds",
                                 "How long a person-started camera request waited for the garden to hand the vision card back.",
                                 self.hot_waits, 'card="vision"')
        lines += ["# HELP stonesage_takeback_cooldown_seconds Seconds until a garden layout that evicts the card may be borrowed again.",
                  "# TYPE stonesage_takeback_cooldown_seconds gauge"]
        for r in RESOURCES:
            lines.append('stonesage_takeback_cooldown_seconds{card="%s"} %d' % (r, round(self.cooldown_left(r))))
        return lines
