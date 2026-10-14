"""
Cockpit activity (2026-10-13): is a person using the StoneSage page right now?

Background work (the garden's sessions) must never sit on Computer's GPU while someone is at the cockpit. Two things count as use:

  - a heartbeat: the page (frontend/js/activity.js) posts /api/activity/heartbeat on real input (key, tap, pointer, scroll) and when the
    tab becomes visible again, at most once a minute;
  - any request signed in with the page's login cookie that CHANGES something (POST, PUT, PATCH, DELETE), except the paths in
    `cockpit_activity.ignore`. Reads never count: the page polls (health, profile, presence, the garden board...) all day while a tab
    is merely open, and a bearer key (Home Assistant, the Alexa relay, scripts, Claude Code) is not a person at the cockpit.

For `cockpit_activity.window_s` seconds (default 600) after the last beat, `reason()` says "someone is using StoneSage (N s ago)". The idle
gate adds it to its shared reasons, so the garden does not start and a running garden window ends. `listeners` are called on every beat
(the takeback, stonesage/courage/takeback.py, takes back a coordinator the garden has borrowed); they must be quick and never raise.

Config (config.json `cockpit_activity`): enabled (default true), window_s (600), ignore (paths that never count).
"""

import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from . import auth

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "window_s": 600,
    # the login, the camera stats the page posts by itself, and the heartbeat route (handled on its own)
    "ignore": ["/api/auth/", "/api/cameras/webrtc-report", "/api/activity/"],
}
CHANGING = ("POST", "PUT", "PATCH", "DELETE")
HEARTBEAT = "/api/activity/heartbeat"


def from_cockpit(h: Any) -> bool:
    """True when the request is signed in as the owner by the page's cookie (no Authorization header at all)."""
    headers = getattr(h, "headers", None)
    if headers is None or getattr(h, "principal", None) != auth.OWNER:
        return False
    return not headers.get("Authorization") and bool(auth.cookie_value(headers.get("Cookie", "")))


class CockpitActivity:
    def __init__(self, cfg_fn: Callable[[], Dict[str, Any]], clock: Callable[[], float] = time.time):
        self.cfg_fn, self.clock = cfg_fn, clock
        self.last: Optional[float] = None
        self.last_source = ""
        self.beats = 0
        self.listeners: List[Callable[[str], Any]] = []
        self._lock = threading.Lock()

    def cfg(self) -> Dict[str, Any]:
        raw = (self.cfg_fn() or {}).get("cockpit_activity")
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if k in DEFAULTS})
        try:
            out["window_s"] = max(0.0, float(out["window_s"]))
        except (TypeError, ValueError):
            out["window_s"] = float(DEFAULTS["window_s"])
        return out

    # ---- what counts ----
    def counts(self, method: str, path: str) -> bool:
        """Would a signed-in cookie request with this method and path be a person using the page?"""
        if str(method).upper() not in CHANGING:
            return False
        return not any(path.startswith(p) for p in self.cfg()["ignore"] if p)

    def observe(self, h: Any, method: str) -> bool:
        """Called by the server's gate for every authenticated request. True when it counted as use."""
        if not self.cfg()["enabled"] or not from_cockpit(h):
            return False
        path = urllib.parse.urlparse(getattr(h, "path", "") or "").path
        if not self.counts(method, path):
            return False
        self.beat("request")
        return True

    # ---- the signal ----
    def beat(self, source: str = "heartbeat") -> None:
        if not self.cfg()["enabled"]:
            return
        with self._lock:
            self.last, self.last_source = self.clock(), source
            self.beats += 1
        for fn in list(self.listeners):
            try:
                fn(source)
            except Exception:
                pass

    def seconds_since(self) -> Optional[float]:
        return None if self.last is None else max(0.0, self.clock() - self.last)

    def active(self) -> bool:
        cfg = self.cfg()
        ago = self.seconds_since()
        return bool(cfg["enabled"] and ago is not None and ago < cfg["window_s"])

    def reason(self) -> Optional[str]:
        """The idle gate's shared reason, or None when nobody has used the page within the window."""
        if not self.active():
            return None
        return "someone is using StoneSage (%d s ago)" % round(self.seconds_since() or 0)

    def status(self) -> Dict[str, Any]:
        cfg = self.cfg()
        ago = self.seconds_since()
        return {"ok": True, "enabled": bool(cfg["enabled"]), "active": self.active(), "window_s": cfg["window_s"],
                "seconds_since": None if ago is None else round(ago, 1), "last_source": self.last_source, "beats": self.beats}

    def metrics(self) -> List[str]:
        return ["# HELP stonesage_cockpit_active 1 while a person has used the StoneSage page within the activity window.",
                "# TYPE stonesage_cockpit_active gauge", "stonesage_cockpit_active %d" % (1 if self.active() else 0)]


def install(ctx: Any, activity: CockpitActivity) -> None:
    """POST /api/activity/heartbeat (the page's beat) and GET /api/activity/status, on the platform's route table."""
    def heartbeat(h, parsed, body) -> None:
        if auth.require_principal(h, ctx.config):
            return
        activity.beat("heartbeat")
        auth.reply(h, 200, {"ok": True, "active": activity.active()})

    def status(h, parsed, body) -> None:
        if auth.require_principal(h, ctx.config):
            return
        auth.reply(h, 200, activity.status())

    ctx.routes.add("POST", HEARTBEAT, heartbeat, owner="stonesage")
    ctx.routes.add("GET", "/api/activity/status", status, owner="stonesage")
