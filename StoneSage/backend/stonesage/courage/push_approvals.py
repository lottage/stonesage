"""
Approve Courage's actions from the phone: an actionable Home Assistant notification with Yes / No buttons.

offer() sends the push when an action needs approval (policy: config.json courage.push_approvals =
"voice" (default: conversations that came through Home Assistant), "always" or "never").
A background listener subscribes to HA's `mobile_app_notification_action` events over the websocket API;
a tap on Yes runs the parked action (the same PendingActions entry a spoken or typed "yes" would run),
No drops it. The notification is then replaced (same tag) with the outcome.
"""

import asyncio
import json
import logging
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("StoneSage.Courage.Push")

YES, NO = "COURAGE_YES_", "COURAGE_NO_"


class PushApprovals:
    def __init__(self, ha_url: str, token: str, phone_service: str, pending, execute: Callable[[str, Dict[str, Any]], str],
                 policy: str = "voice", post: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        self.ha_url = ha_url.rstrip("/")
        self.token = token
        self.service = phone_service          # e.g. mobile_app_alex_s_phone
        self.pending = pending                # courage.agent.PendingActions
        self.execute = execute                # (tool name, args) -> result JSON string
        self.policy = policy
        self._post = post or self._ha_post
        self._thread: Optional[threading.Thread] = None
        self.last_event: Optional[Dict[str, Any]] = None
        self.watch = None  # set from server.py when the watch bridge is configured
        self.extra_handlers: list = []  # (action string) -> outcome text | None, for buttons that aren't approvals

    # -------------------------------------------------------------- sending ----
    def _ha_post(self, path: str, body: Dict[str, Any]) -> Any:
        req = urllib.request.Request(f"{self.ha_url}{path}", json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status

    def _notify(self, message: str, action_id: str, buttons: bool) -> None:
        data: Dict[str, Any] = {"tag": f"courage-{action_id}", "group": "courage"}
        if buttons:
            data["actions"] = [{"action": f"{YES}{action_id}", "title": "Yes"}, {"action": f"{NO}{action_id}", "title": "No"}]
        self._post(f"/api/services/notify/{self.service}", {"title": "Computer", "message": message, "data": data})

    def wants_push(self, session_id: str) -> bool:
        if self.policy == "always":
            return True
        if self.policy == "never":
            return False
        return session_id.startswith(("ha:", "alexa:"))     # spoken conversations; an Echo session can end before the yes

    def offer(self, session_id: str, action: Dict[str, Any]) -> bool:
        """Push the approval request if the policy says so. Returns True when a push was sent."""
        if not self.wants_push(session_id):
            return False
        try:
            self._notify(f"Shall I {action['summary']}?", action["id"], buttons=True)
            return True
        except Exception as e:
            logger.warning(f"approval push failed: {e}")
            return False

    def clear(self, action_id: str, outcome: str) -> None:
        """Update the phone notification with an outcome -- used when another channel (the watch) answered first."""
        try:
            self._notify(outcome, action_id, buttons=False)
        except Exception as e:
            logger.warning(f"outcome push failed: {e}")

    # ------------------------------------------------------------- receiving ----
    def handle_action(self, action_str: str) -> Optional[str]:
        """Apply a tapped button ('COURAGE_YES_<id>' / 'COURAGE_NO_<id>'). Returns the outcome text, or None if not ours.
        Other buttons go to `extra_handlers` (e.g. the engine lease's Another hour / Stop check-in)."""
        if not action_str.startswith((YES, NO)):
            for handler in self.extra_handlers:
                try:
                    out = handler(action_str)
                except Exception as e:
                    out = f"failed: {e}"
                if out is not None:
                    return out
            return None
        approve = action_str.startswith(YES)
        action_id = action_str[len(YES if approve else NO):]
        found = self.pending.pop_by_id(action_id)
        if not found:
            outcome = "That request expired or was already answered."
        elif not approve:
            outcome = f"Left alone: {found['summary']}."
        else:
            try:
                result = json.loads(self.execute(found["name"], found["args"]) or "{}")
                ok = result.get("ok", True)
            except Exception as e:
                ok, result = False, {"error": str(e)}
            outcome = f"Done: {found['summary']}." if ok else f"Could not {found['summary']}: {result.get('error', 'failed')}"
        try:
            self._notify(outcome, action_id, buttons=False)
        except Exception as e:
            logger.warning(f"outcome push failed: {e}")
        if self.watch and found and found.get("watch_id"):
            self.watch.resolve(found["watch_id"])  # clears the watch's card too -- different id space than ours
        return outcome

    async def _listen(self) -> None:
        import websockets  # available on the StoneSage host (websockets 10.x)
        url = self.ha_url.replace("http", "ws", 1) + "/api/websocket"
        backoff = 5
        while True:
            try:
                async with websockets.connect(url, max_size=None, ping_interval=30) as ws:
                    await ws.recv()
                    await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    if json.loads(await ws.recv()).get("type") != "auth_ok":
                        raise RuntimeError("Home Assistant rejected the token")
                    await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}))
                    backoff = 5
                    logger.info("listening for approval taps from the phone")
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "event":
                            continue
                        data = (msg.get("event") or {}).get("data") or {}
                        self.last_event = {"at": time.time(), "action": data.get("action")}
                        outcome = await asyncio.get_running_loop().run_in_executor(None, self.handle_action, data.get("action") or "")
                        if outcome:
                            logger.info(f"phone approval: {data.get('action')} -> {outcome}")
            except Exception as e:
                logger.warning(f"HA event listener: {e}; retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self._listen()), name="courage-push-approvals", daemon=True)
        self._thread.start()
