"""
Unit tests for Multi-Signal Dynamic Presence Engine.
Verifies activity tracking, idle threshold evaluation, HA phone polling, and manual overrides.
"""

import time
import unittest
from unittest.mock import patch, MagicMock
from harness.core.presence_engine import (
    PresenceEngine,
    STATE_INTERACTIVE,
    STATE_IDLE_RUMINATION,
    STATE_MANUAL_OVERRIDE,
)


class TestPresenceEngine(unittest.TestCase):
    def setUp(self):
        # Create fresh engine instance with a short 60s idle threshold for testing
        self.engine = PresenceEngine(idle_threshold_seconds=60)

    def test_initial_state_is_interactive(self):
        """Newly created engine starts in interactive priority."""
        eval_res = self.engine.evaluate_presence()
        self.assertEqual(eval_res["state"], STATE_INTERACTIVE)
        self.assertTrue(eval_res["is_interactive"])

    def test_record_activity_resets_idle_timer(self):
        """Recording activity updates last_workstation_activity to current time."""
        past_time = time.time() - 100.0
        self.engine.last_workstation_activity = past_time
        self.assertFalse(self.engine.is_interactive_priority())

        # Record activity
        self.engine.record_activity(source="cli", details="user prompt")
        self.assertTrue(self.engine.is_interactive_priority())
        eval_res = self.engine.evaluate_presence()
        self.assertEqual(eval_res["state"], STATE_INTERACTIVE)

    def test_idle_transition_after_threshold(self):
        """Engine transitions to SOVEREIGN_IDLE_RUMINATION when idle_seconds >= threshold."""
        self.engine.last_workstation_activity = time.time() - 75.0  # 75s > 60s
        eval_res = self.engine.evaluate_presence()
        self.assertEqual(eval_res["state"], STATE_IDLE_RUMINATION)
        self.assertFalse(eval_res["is_interactive"])
        self.assertIn("Quiet workstation", eval_res["reason"])

    def test_manual_override(self):
        """Manual override bypasses activity timer and HA presence."""
        # Force idle even if active
        self.engine.set_manual_override(STATE_IDLE_RUMINATION)
        eval_res = self.engine.evaluate_presence()
        self.assertEqual(eval_res["state"], STATE_IDLE_RUMINATION)
        self.assertFalse(eval_res["is_interactive"])

        # Force interactive even if quiet
        self.engine.last_workstation_activity = time.time() - 500.0
        self.engine.set_manual_override(STATE_INTERACTIVE)
        eval_res = self.engine.evaluate_presence()
        self.assertEqual(eval_res["state"], STATE_INTERACTIVE)
        self.assertTrue(eval_res["is_interactive"])

        # Clear override
        self.engine.set_manual_override(None)
        self.assertFalse(self.engine.is_interactive_priority())  # Since past time is 500s ago

    @patch("harness.connectors.hass_connector.HassConnector.get_state")
    def test_poll_phone_presence(self, mock_get_state):
        """Verifies phone presence is parsed properly from Home Assistant (the tracker comes from config)."""
        from harness.config import fleet_config
        self.engine._last_phone_poll = 0.0
        with patch.dict(fleet_config.stonesage_cfg, {"presence_engine": {}}):
            self.assertEqual(self.engine.poll_phone_presence(), "unknown")      # no tracker configured
            mock_get_state.assert_not_called()

        with patch.dict(fleet_config.stonesage_cfg, {"presence_engine": {"phone_tracker": "device_tracker.alex_s_phone"}}):
            mock_get_state.return_value = {"entity_id": "device_tracker.alex_s_phone", "state": "home"}
            self.engine._last_phone_poll = 0.0
            self.assertEqual(self.engine.poll_phone_presence(), "home")
            mock_get_state.assert_called_with("device_tracker.alex_s_phone")

            mock_get_state.return_value = {"entity_id": "device_tracker.alex_s_phone", "state": "not_home"}
            self.engine._last_phone_poll = 0.0
            self.assertEqual(self.engine.poll_phone_presence(), "not_home")


if __name__ == "__main__":
    unittest.main()
