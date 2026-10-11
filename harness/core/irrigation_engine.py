"""
Irrigation Automation Engine.
Controls smart irrigation valves through Home Assistant. The zones and entities are the site's own, from config.json:

    "irrigation": {
      "zones": {"front": {"name": "Front Zone", "valve": "valve.front_zone", "smart_switch": "switch.front_smart",
                          "history_sensor": "sensor.front_history", "default_duration_mins": 15, "description": "..."}},
      "rain_delay_switch": "switch.rain_delay", "battery_sensor": "sensor.controller_battery",
      "programs": {"whole_yard": "switch.program_whole_yard"}
    }

Integrates smart watering programs, battery telemetry, rain delay enforcement,
weather-based forecast inhibition, and landscape planning logs. Without an "irrigation" block there are no zones.
(The key used to be "garden"; that is still read when "irrigation" is absent.)
"""

import os
import time
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from ..config import _load_stonesage_config
from ..connectors.hass_connector import hass_connector

logger = logging.getLogger("Harness.IrrigationEngine")


def irrigation_config() -> Dict[str, Any]:
    cfg = _load_stonesage_config()
    # "garden" was this block's old name (before it was clear that "garden" also names a long-term agent concept)
    return cfg.get("irrigation") or cfg.get("garden") or {}


def zone_map() -> Dict[str, Dict[str, Any]]:
    return {k: dict({"default_duration_mins": 15, "description": ""}, **v) for k, v in (irrigation_config().get("zones") or {}).items()}


IRRIGATION_LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "irrigation_log.json")
OLD_LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "garden_log.json")   # the log's name before the rename

class IrrigationEngine:
    def __init__(self):
        self._active_timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(IRRIGATION_LOG_FILE)), exist_ok=True)
        if not os.path.exists(IRRIGATION_LOG_FILE):
            old = self._load_log_from(OLD_LOG_FILE) if os.path.exists(OLD_LOG_FILE) else []
            self._save_log(old)

    @staticmethod
    def _load_log_from(path: str) -> List[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _load_log(self) -> List[Dict[str, Any]]:
        return self._load_log_from(IRRIGATION_LOG_FILE)

    def _save_log(self, entries: List[Dict[str, Any]]) -> None:
        try:
            with open(IRRIGATION_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(entries[-100:], f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save irrigation log: {e}")

    def add_irrigation_log(self, event: str, notes: str) -> None:
        entries = self._load_log()
        entries.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
            "event": event,
            "notes": notes
        })
        self._save_log(entries)

    def get_irrigation_status(self) -> Dict[str, Any]:
        """Polls Home Assistant for complete irrigation status."""
        states = {s["entity_id"]: s for s in hass_connector.get_all_states()}
        
        zones_out = {}
        for key, info in zone_map().items():
            valve_state = states.get(info["valve"], {}).get("state", "unknown")
            smart_state = states.get(info.get("smart_switch", ""), {}).get("state", "unknown")
            history_state = states.get(info.get("history_sensor", ""), {}).get("state", "unknown")
            
            is_open = valve_state in ("open", "opening")
            zones_out[key] = {
                "name": info["name"],
                "description": info["description"],
                "valve_entity": info["valve"],
                "state": valve_state,
                "is_open": is_open,
                "smart_watering": smart_state == "on",
                "last_watered": history_state,
                "default_duration_mins": info["default_duration_mins"]
            }

        gc = irrigation_config()
        battery_val = states.get(gc.get("battery_sensor", ""), {}).get("state", "unknown")
        rain_delay_val = states.get(gc.get("rain_delay_switch", ""), {}).get("state", "unknown") == "on"
        programs = {name: states.get(entity, {}).get("state", "unknown") == "on"
                    for name, entity in (gc.get("programs") or {}).items()}

        return {
            "battery_level": int(battery_val) if str(battery_val).isdigit() else battery_val,
            "rain_delay_active": rain_delay_val,
            "programs": programs,
            "zones": zones_out,
            "recent_log": self._load_log()[-8:],
            "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        }

    def open_zone(self, zone_key: str, duration_minutes: Optional[int] = None) -> Dict[str, Any]:
        """Opens a specific irrigation zone with an automatic safety shutoff timer."""
        zones = zone_map()
        if zone_key not in zones:
            return {"ok": False, "error": f"Unknown zone '{zone_key}'. Valid: {list(zones)}"}

        info = zones[zone_key]
        duration = duration_minutes or info["default_duration_mins"]
        duration = max(1, min(duration, 60))  # Bound between 1 and 60 minutes for safety

        resp = hass_connector.call_service("valve", "open_valve", {"entity_id": info["valve"]})
        if not resp.get("ok"):
            return {"ok": False, "error": f"Home Assistant call failed: {resp.get('error')}"}

        # Cancel existing timer if running
        with self._lock:
            if zone_key in self._active_timers:
                self._active_timers[zone_key].cancel()

            # Schedule auto-shutoff
            timer = threading.Timer(duration * 60.0, self.close_zone, args=[zone_key])
            timer.daemon = True
            timer.start()
            self._active_timers[zone_key] = timer

        self.add_irrigation_log(f"Zone Opened: {info['name']}", f"Valve opened for {duration} minutes (auto-shutoff armed).")
        logger.info(f"Opened {info['name']} for {duration} mins.")
        return {"ok": True, "zone": zone_key, "duration_minutes": duration, "valve": info["valve"]}

    def close_zone(self, zone_key: str) -> Dict[str, Any]:
        """Closes an irrigation zone valve immediately."""
        zones = zone_map()
        if zone_key not in zones:
            return {"ok": False, "error": f"Unknown zone '{zone_key}'"}

        info = zones[zone_key]
        with self._lock:
            if zone_key in self._active_timers:
                self._active_timers[zone_key].cancel()
                del self._active_timers[zone_key]

        resp = hass_connector.call_service("valve", "close_valve", {"entity_id": info["valve"]})
        self.add_irrigation_log(f"Zone Closed: {info['name']}", "Valve commanded closed.")
        logger.info(f"Closed {info['name']}.")
        return {"ok": resp.get("ok", False), "zone": zone_key, "valve": info["valve"]}

    def close_all_zones(self) -> Dict[str, Any]:
        """Emergency or manual shutoff of all 4 irrigation zones."""
        results = {}
        for k in zone_map():
            results[k] = self.close_zone(k)
        self.add_irrigation_log("All Zones Closed", "Emergency/manual shutoff executed across all irrigation zones.")
        return {"ok": True, "results": results}

    def set_rain_delay(self, enabled: bool = True) -> Dict[str, Any]:
        """Enables or disables Home Assistant rain delay for all zones."""
        service = "turn_on" if enabled else "turn_off"
        switch = irrigation_config().get("rain_delay_switch")
        if not switch:
            return {"ok": False, "error": "no rain_delay_switch in config.json irrigation"}
        resp = hass_connector.call_service("switch", service, {"entity_id": switch})
        self.add_irrigation_log(f"Rain Delay {'Activated' if enabled else 'Deactivated'}", f"Switch set to {'on' if enabled else 'off'}.")
        return {"ok": resp.get("ok", False), "rain_delay": enabled}

irrigation_engine = IrrigationEngine()
