"""
Multi-Signal Dynamic Presence & Activity Engine.
Fuses Home Assistant phone presence (config presence_engine.phone_tracker, a device_tracker entity),
developer workstation heartbeats (CLI/web turns from the workstation),
and smart plug telemetry to dynamically govern background GPU duty cycles.
"""

import time
import logging
from typing import Dict, Any, Optional
from ..connectors.hass_connector import hass_connector
from ..config import fleet_config

logger = logging.getLogger("Harness.PresenceEngine")

STATE_INTERACTIVE = "INTERACTIVE_PRIORITY"
STATE_IDLE_RUMINATION = "SOVEREIGN_IDLE_RUMINATION"
STATE_MANUAL_OVERRIDE = "MANUAL_OVERRIDE"

DEFAULT_IDLE_THRESHOLD_SECONDS = 45 * 60  # 45 minutes


class PresenceEngine:
    def __init__(self, idle_threshold_seconds: int = DEFAULT_IDLE_THRESHOLD_SECONDS):
        self.idle_threshold_seconds = idle_threshold_seconds
        self.last_workstation_activity: float = time.time()
        self.manual_override_state: Optional[str] = None
        self._cached_phone_state: str = "unknown"
        self._last_phone_poll: float = 0.0
        self._phone_poll_interval: float = 60.0  # Poll HA at most once every minute

    def record_activity(self, source: str = "cli", details: Optional[str] = None) -> None:
        """Records a user-driven interaction (keystroke, prompt, API request)."""
        self.last_workstation_activity = time.time()
        logger.debug(f"Recorded activity from source '{source}': {details or 'active'}")

    def set_manual_override(self, override_state: Optional[str]) -> None:
        """Sets or clears a manual presence override ('INTERACTIVE_PRIORITY' or 'SOVEREIGN_IDLE_RUMINATION')."""
        self.manual_override_state = override_state
        logger.info(f"Manual presence override set to: {override_state}")

    def poll_phone_presence(self) -> str:
        """Polls Home Assistant for the household phone's tracker."""
        now = time.time()
        if now - self._last_phone_poll < self._phone_poll_interval:
            return self._cached_phone_state

        self._last_phone_poll = now
        tracker = (fleet_config.stonesage_cfg.get("presence_engine") or {}).get("phone_tracker", "")
        if not tracker:                 # no phone configured: presence from the phone is simply unknown
            self._cached_phone_state = "unknown"
            return self._cached_phone_state
        try:
            state_data = hass_connector.get_state(tracker)
            st = state_data.get("state", "unknown")
            if st in ("home", "not_home"):
                self._cached_phone_state = st
            else:
                self._cached_phone_state = "unknown"
        except Exception as e:
            logger.warning(f"Could not poll {tracker}: {e}")
            self._cached_phone_state = "unknown"

        return self._cached_phone_state

    def evaluate_presence(self) -> Dict[str, Any]:
        """
        Evaluates current multi-signal presence state:
        1. Manual override (if set)
        2. Workstation recency (active if interaction within threshold)
        3. Home Assistant phone presence
        """
        now = time.time()
        idle_seconds = max(0.0, now - self.last_workstation_activity)
        idle_minutes = round(idle_seconds / 60.0, 1)

        # 1. Manual Override check
        if self.manual_override_state:
            return {
                "state": self.manual_override_state,
                "is_interactive": self.manual_override_state == STATE_INTERACTIVE,
                "reason": f"Manual operator override set to {self.manual_override_state}",
                "workstation_idle_minutes": idle_minutes,
                "phone_presence": self._cached_phone_state,
            }

        phone_state = self.poll_phone_presence()

        # 2. If workstation was active recently (< 45 min), stay interactive
        if idle_seconds < self.idle_threshold_seconds:
            reason = f"Workstation active {idle_minutes}m ago (< {self.idle_threshold_seconds // 60}m threshold)"
            return {
                "state": STATE_INTERACTIVE,
                "is_interactive": True,
                "reason": reason,
                "workstation_idle_minutes": idle_minutes,
                "phone_presence": phone_state,
            }

        # 3. Workstation has been quiet > 45 minutes
        reason = f"Quiet workstation for {idle_minutes}m (threshold {self.idle_threshold_seconds // 60}m); Phone: {phone_state}"
        return {
            "state": STATE_IDLE_RUMINATION,
            "is_interactive": False,
            "reason": reason,
            "workstation_idle_minutes": idle_minutes,
            "phone_presence": phone_state,
        }

    def is_interactive_priority(self) -> bool:
        """Quick boolean helper to determine if GPUs should yield to operator."""
        return self.evaluate_presence()["is_interactive"]


# Global instance
presence_engine = PresenceEngine()
