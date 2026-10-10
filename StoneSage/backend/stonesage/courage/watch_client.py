"""
Talks to the Garmin watch bridge (stonesage-watch/bridge). Reimplemented from the
scaffold's stonesage_client.py using urllib instead of httpx, matching
push_approvals.py's style so this package doesn't need a new dependency.

Fails soft everywhere: a watch bridge that's down or unreachable must never break
a Courage turn, so every call logs and swallows its own exceptions.
"""

import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("StoneSage.Courage.Watch")


class WatchBridge:
    def __init__(self, base_url: str, token: str, timeout: float = 6.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _post(self, path: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req = urllib.request.Request(
            f"{self.base}{path}", json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def ask(self, text: str, options: List[str], project: Optional[str] = None,
            ttl: int = 900, destructive: bool = False) -> Optional[str]:
        """Destructive asks are forced to ["Deny", "At desk"] by the bridge. Returns the ask id, or None on failure."""
        body: Dict[str, Any] = {"k": "ask", "t": text, "o": options, "x": ttl, "d": destructive}
        if project:
            body["p"] = project
        try:
            resp = self._post("/events", body)
            return (resp or {}).get("id")
        except Exception as e:
            logger.warning(f"watch ask failed: {e}")
            return None

    def resolve(self, ask_id: str) -> None:
        """Mark an ask handled elsewhere (phone, chat, voice) so the watch clears it."""
        if not ask_id:
            return
        try:
            self._post(f"/events/{ask_id}/resolve", {})
        except Exception as e:
            logger.warning(f"watch resolve failed: {e}")

    def notify(self, text: str, kind: str = "done", project: Optional[str] = None) -> None:
        body: Dict[str, Any] = {"k": kind, "t": text}
        if project:
            body["p"] = project
        try:
            self._post("/events", body)
        except Exception as e:
            logger.warning(f"watch notify failed: {e}")

    def status(self, agents: List[Dict[str, Any]]) -> None:
        """agents: [{"p": name, "s": wait|err|run|idle, "t": text, "pr": 0-100}]"""
        try:
            self._post("/events", {"k": "st", "a": agents})
        except Exception as e:
            logger.warning(f"watch status failed: {e}")

    def metrics(self, **values: float) -> None:
        """Merged into the watch face's `use` metrics (see PROTOCOL.md), e.g. metrics(tps=54.4)."""
        try:
            self._post("/events", {"k": "use", "m": values})
        except Exception as e:
            logger.warning(f"watch metrics failed: {e}")
