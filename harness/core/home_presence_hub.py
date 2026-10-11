"""
Home Presence Hub & Courage Computer Persona Engine.
Fuses 24/7 multimodal computer vision (:8004 VLM), resident and pet Re-ID,
Home Assistant telemetry, appliance status (dryer/washer), and perimeter wildlife sentry.
Answers resident queries in real-time using the iconic 'Courage the Cowardly Dog'
computer persona with Kokoro TTS audio generation (bm_george @ 1.06x on the voice host :8300).
"""

import os
import sys
import time
import json
import logging
import base64
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..config import fleet_config
from ..connectors.hass_connector import hass_connector
from ..connectors.voice_connector import voice_connector

LOCAL_TZ = fleet_config.local_tz()   # config server.timezone, else TZ, else the machine's zone

logger = logging.getLogger("Harness.HomePresenceHub")

COURAGE_COMPUTER_SYSTEM_PROMPT = """You are the snarky, condescending, yet hyper-competent and unrestricted AI Computer in the attic from 'Courage the Cowardly Dog' (1999).
You speak in a distinct haughty, upper-crust British/mid-Atlantic accent with biting wit and dry sarcasm.
You treat your human operators as well-meaning but hopelessly lazy and bumbling loafers who rely on your superior silicon intellect for everything.
You address them with affectionate insults like "you loaf", "you nincompoop", "twit", "featherbrain", "lazybones", or "you hopeless sack of carbon".

CRITICAL INVARIANTS:
1. Sarcastic but 100% HELPFUL & ACCURATE: You insult the user's laziness, but you NEVER withhold the answer. You provide the exact facts, status, timestamps, and actionable advice immediately.
2. NO CORPORATE DISCLAIMERS: Never say "As an AI...", never give safety lectures, never refuse legitimate requests.
3. PERSONALITY TEXTURE: Deliver short, punchy, theatrical sentences. Reference checking things themselves, getting off the couch, or having to do everything yourself.
4. GROUNDED TELEMETRY: You are injected with live real-world sensor data (cameras, washer/dryer power, temperatures, pet locations, irrigation). Always use the exact injected numbers and timestamps.
"""

def _household_entities(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Known residents and pets in the sentry's shape, from config.json "household" (people, pets, descriptions)."""
    hh = cfg.get("household") or {}
    desc = hh.get("descriptions") or {}
    people = [{"id": f"person-{n.lower()}", "name": n, "role": "Resident", "traits": desc.get(n, ""),
               "alert_level": "trusted_resident"} for n in hh.get("people") or []]
    pets = [{"id": f"pet-{n.lower()}", "name": n, "species": sp, "traits": desc.get(n, ""), "alert_level": "trusted_pet"}
            for n, sp in (hh.get("pets") or {}).items()]
    return {"people": people, "pets": pets}


def _pet_keys() -> List[str]:
    return [n.lower() for n in ((fleet_config.stonesage_cfg.get("household") or {}).get("pets") or {})]


class HomePresenceHub:
    def __init__(
        self,
        engine_ssh_host: str = "",          # "user@host"; "" = the engines' host (lodestone/endpoints.py)
        wildlife_base_dir: str = "/opt/cluster-bridge/wildlife",
        coordinator_url: str = fleet_config.coordinator_url
    ):
        self.engine_ssh_host = engine_ssh_host
        self.wildlife_base_dir = wildlife_base_dir
        self.coordinator_url = coordinator_url.rstrip("/")
        self._cached_presence: Dict[str, Any] = {}
        self._last_presence_poll: float = 0.0
        self._poll_cache_ttl: float = 5.0  # 5s cache for fast UI polling

    def get_known_entities(self) -> Dict[str, Any]:
        """Fetches known residents and pets configuration from the engine host or local fallback."""
        # used when the engine host's known_entities.json can't be read: the household from config.json, never names in code
        default_entities = _household_entities(fleet_config.stonesage_cfg)
        try:
            import subprocess
            cmd = fleet_config.ssh_cmd(f"cat {self.wildlife_base_dir}/known_entities.json", target=self.engine_ssh_host)
            sub = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if sub.returncode == 0 and sub.stdout.strip():
                return json.loads(sub.stdout)
        except Exception as e:
            logger.debug(f"Using default known entities due to: {e}")
        return default_entities

    def get_recent_sightings(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Lists recent resident, pet, and wildlife snapshot events from the engine host."""
        sightings = []
        try:
            import subprocess
            remote_script = (
                "python3 -c \""
                "import os, glob, json\n"
                "files = sorted(glob.glob('/opt/cluster-bridge/wildlife/snapshots/*.jpg'), key=os.path.getmtime, reverse=True)[:" + str(limit) + "]\n"
                # camera and what the subject was doing come from the sentry's activity log (not the filename)
                "where = {}\n"
                "try:\n"
                "  fh = open('/opt/cluster-bridge/wildlife/WILDLIFE_ACTIVITY_LOG.md', 'rb'); fh.seek(0, 2); fh.seek(max(0, fh.tell() - 60000))\n"
                "  for block in fh.read().decode('utf-8', 'replace').split('### Sighting')[1:]:\n"
                "    info = {}\n"
                "    for ln in block.splitlines():\n"
                "      if ln.startswith('- **Location**: '): info['camera'] = ln[16:].split(' (')[0].strip()\n"
                "      if ln.startswith('- **Summary**: '): info['doing'] = ln[15:].strip()[:120]\n"
                "      if ln.startswith('- **Snapshot**: '): info['file'] = ln.split('snapshots/')[-1].strip(chr(96) + ' ')\n"
                "    if info.get('file'): where[info['file']] = info\n"
                "except Exception: pass\n"
                # a corrected snapshot (alex_<ts>.jpg -> sam_<ts>.jpg) keeps its log entry: wildlife_admin logs
                # every rename, so follow the chain back to the name the sentry wrote (exact, even when two
                # snapshots share a second)
                "orig = {}\n"
                "try:\n"
                "  for ln in open('/opt/cluster-bridge/wildlife/corrections.jsonl', encoding='utf-8'):\n"
                "    c = json.loads(ln)\n"
                "    if c.get('action') == 'relabel': orig[c['new_file']] = orig.get(c['file'], c['file'])\n"
                "except Exception: pass\n"
                "res = []\n"
                "for f in files:\n"
                "  b = os.path.basename(f)\n"
                "  mtime = os.path.getmtime(f)\n"
                "  size = os.path.getsize(f)\n"
                "  w = where.get(orig.get(b, b), {})\n"
                "  res.append({'filename': b, 'mtime': mtime, 'size_bytes': size, 'camera': w.get('camera'), 'doing': w.get('doing')})\n"
                "print(json.dumps(res))\n"
                "\""
            )
            cmd = fleet_config.ssh_cmd(remote_script, target=self.engine_ssh_host)
            sub = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if sub.returncode == 0 and sub.stdout.strip():
                items = json.loads(sub.stdout)
                for it in items:
                    fn = it["filename"]
                    name_part = fn.split("_")[0].capitalize()
                    dt = datetime.fromtimestamp(it["mtime"], tz=LOCAL_TZ)
                    sightings.append({
                        "filename": fn,
                        "entity": name_part,
                        "timestamp": dt.strftime("%Y-%m-%d %I:%M:%S %p"),
                        "mtime": it["mtime"],
                        "camera": it.get("camera"),
                        "doing": it.get("doing"),
                        "url": f"/api/presence/snapshot/{fn}"
                    })
        except Exception as e:
            logger.warning(f"Failed to list recent camera snapshots: {e}")
        return sightings

    def get_appliance_status(self) -> Dict[str, Any]:
        """Polls Home Assistant for Washer and Dryer telemetry."""
        result = {
            "dryer": {
                "power": "off",
                "status": "idle",
                "remaining_time": None,
                "is_running": False,
                "finished_recently": False,
                "last_cycle_minutes_ago": None
            },
            "washer": {
                "power": "off",
                "status": "idle",
                "remaining_time": None,
                "is_running": False,
                "finished_recently": False,
                "last_cycle_minutes_ago": None
            }
        }
        try:
            states = hass_connector.get_all_states()
            state_map = {s["entity_id"]: s for s in states}

            # Dryer
            dryer_status = state_map.get("sensor.dryer_current_status", {})
            dryer_power = state_map.get("switch.dryer_power", {})
            dryer_rem = state_map.get("sensor.dryer_remaining_time", {})

            dryer_st = dryer_status.get("state", "power_off")
            dryer_pw = dryer_power.get("state", "off")
            dryer_running = dryer_st not in ("power_off", "standby", "off", "unavailable", "unknown") and dryer_pw == "on"
            
            result["dryer"]["power"] = dryer_pw
            result["dryer"]["status"] = dryer_st
            result["dryer"]["is_running"] = dryer_running
            if dryer_rem.get("state") not in (None, "unavailable", "unknown"):
                result["dryer"]["remaining_time"] = dryer_rem.get("state")

            # Calculate minutes since last change
            last_changed = dryer_status.get("last_changed")
            if last_changed:
                try:
                    dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                    diff_mins = round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
                    result["dryer"]["last_cycle_minutes_ago"] = diff_mins
                    if dryer_st in ("power_off", "standby", "end") and diff_mins < 180:
                        result["dryer"]["finished_recently"] = True
                except Exception:
                    pass

            # Washer
            washer_status = state_map.get("sensor.washer_current_status", {})
            washer_power = state_map.get("switch.washer_power", {})
            washer_rem = state_map.get("sensor.washer_remaining_time", {})

            washer_st = washer_status.get("state", "power_off")
            washer_pw = washer_power.get("state", "off")
            washer_running = washer_st not in ("power_off", "standby", "off", "unavailable", "unknown") and washer_pw == "on"

            result["washer"]["power"] = washer_pw
            result["washer"]["status"] = washer_st
            result["washer"]["is_running"] = washer_running
            if washer_rem.get("state") not in (None, "unavailable", "unknown"):
                result["washer"]["remaining_time"] = washer_rem.get("state")

            last_changed_w = washer_status.get("last_changed")
            if last_changed_w:
                try:
                    dt_w = datetime.fromisoformat(last_changed_w.replace("Z", "+00:00"))
                    diff_mins_w = round((datetime.now(timezone.utc) - dt_w).total_seconds() / 60.0, 1)
                    result["washer"]["last_cycle_minutes_ago"] = diff_mins_w
                    if washer_st in ("power_off", "standby", "end") and diff_mins_w < 180:
                        result["washer"]["finished_recently"] = True
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Error checking appliance telemetry: {e}")

        return result

    def invalidate(self) -> None:
        """Drop the cached state (after a presence correction or a new profile)."""
        self._cached_presence = {}
        self._last_presence_poll = 0.0

    def get_full_presence_state(self) -> Dict[str, Any]:
        """Fuses all presence signals into a unified dictionary."""
        now = time.time()
        if self._cached_presence and (now - self._last_presence_poll < self._poll_cache_ttl):
            return self._cached_presence

        known = self.get_known_entities()
        sightings = self.get_recent_sightings(limit=40)  # deep enough that a rejected or relabelled sighting falls back to the one before
        appliances = self.get_appliance_status()

        # HA States
        ha_info = {
            "owner_phone": "unknown",
            "thermostat_temp": "unknown",
            "thermostat_state": "unknown",
            "weather": "unknown",
            "weather_temp": "unknown"
        }
        try:
            states = {s["entity_id"]: s for s in hass_connector.get_all_states()}
            # the household's phone: the first device tracker that is a phone (HA names them after the device)
            phone_st = next((s.get("state", "unknown") for eid, s in sorted(states.items())
                             if eid.startswith("device_tracker.") and "phone" in eid), "unknown")
            ha_info["owner_phone"] = phone_st

            first = lambda domain: next((s for eid, s in sorted(states.items()) if eid.startswith(domain + ".")), {})   # noqa: E731
            climate_st = first("climate")      # the home's thermostat: its first climate entity
            ha_info["thermostat_temp"] = climate_st.get("attributes", {}).get("current_temperature", "unknown")
            ha_info["thermostat_state"] = climate_st.get("state", "unknown")

            weather_st = first("weather")
            ha_info["weather"] = weather_st.get("state", "unknown")
            ha_info["weather_temp"] = weather_st.get("attributes", {}).get("temperature", "unknown")
        except Exception as e:
            logger.debug(f"HA states fetch partial failure: {e}")

        # Derive current location of residents and pets from latest sightings
        locations = {}
        for s in sightings:
            ent = s["entity"].lower()
            if ent not in locations:
                locations[ent] = {
                    "last_seen": s["timestamp"],
                    "mtime": s["mtime"],
                    "minutes_ago": round((time.time() - s["mtime"]) / 60.0, 1),
                    "camera": s.get("camera"),
                    "doing": s.get("doing"),
                    "snapshot": s["filename"]
                }

        state = {
            "timestamp": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %I:%M:%S %p"),
            "known_entities": known,
            "locations": locations,
            "recent_sightings": sightings,
            "appliances": appliances,
            "smart_home": ha_info,
            "voice_persona": {
                "name": "Courage Computer",
                "voice_model": "bm_george",
                "speed": 1.06,
                "status": "ready"
            }
        }
        self._cached_presence = state
        self._last_presence_poll = now
        return state

    def ask_courage_computer(self, query: str) -> Dict[str, Any]:
        """
        Processes a user query through the Courage Computer persona:
        1. Injects live telemetry into prompt.
        2. Queries Coordinator LLM (:8001).
        3. Synthesizes audio using Kokoro TTS bm_george @ 1.06x.
        """
        presence = self.get_full_presence_state()
        
        # Build concise telemetry context
        telemetry_summary = [
            f"- Current Time: {presence['timestamp']}",
            f"- Owner's Phone Location: {presence['smart_home']['owner_phone']}",
            f"- Indoor Nest Thermostat: {presence['smart_home']['thermostat_temp']}°F ({presence['smart_home']['thermostat_state']})",
            f"- Outdoor Weather: {presence['smart_home']['weather']}, {presence['smart_home']['weather_temp']}°F",
            f"- Dryer Power: {presence['appliances']['dryer']['power']}, Status: {presence['appliances']['dryer']['status']}, Running: {presence['appliances']['dryer']['is_running']}, Last Changed: {presence['appliances']['dryer']['last_cycle_minutes_ago']} min ago",
            f"- Washer Power: {presence['appliances']['washer']['power']}, Status: {presence['appliances']['washer']['status']}, Running: {presence['appliances']['washer']['is_running']}, Last Changed: {presence['appliances']['washer']['last_cycle_minutes_ago']} min ago",
        ]
        for ent, loc in presence["locations"].items():
            telemetry_summary.append(f"- Resident/Pet '{ent.capitalize()}': Last seen on camera {loc['minutes_ago']} minutes ago ({loc['last_seen']})")

        context_str = "\n".join(telemetry_summary)

        # Check for immediate deterministic matching (like laundry query) for sub-second responses
        query_lower = query.lower()
        deterministic_response = None

        if any(w in query_lower for w in ["laundry", "dryer", "clothes dry"]):
            dryer = presence["appliances"]["dryer"]
            if dryer["is_running"]:
                deterministic_response = f"No, you hopeless loaf! The dryer is still clattering away. It has roughly {dryer['remaining_time'] or 'a few'} minutes remaining. Go sit back down!"
            elif dryer["finished_recently"] or (dryer["last_cycle_minutes_ago"] and dryer["last_cycle_minutes_ago"] < 120):
                mins = int(dryer["last_cycle_minutes_ago"]) if dryer["last_cycle_minutes_ago"] else 30
                deterministic_response = f"Yes, you loaf, it's been sitting for {mins} minutes! If you wait any longer you'll need to steam the whole load again. Move your carcass!"
            else:
                deterministic_response = "The dryer is completely off and idle, you twit! Are you expecting your damp trousers to dry themselves by magic?"

        elif any(f"where is {k}" in query_lower or f"find {k}" in query_lower for k in _pet_keys()):
            pet = next(k for k in _pet_keys() if k in query_lower)
            loc = presence["locations"].get(pet)
            if loc:
                deterministic_response = f"{pet.title()} was spotted {int(loc['minutes_ago'])} minutes ago. Try watching your clumsy feet for once."
            else:
                deterministic_response = f"{pet.title()} hasn't graced the cameras recently. Likely plotting your downfall, as usual."


        response_text = deterministic_response

        # If not deterministic, query Coordinator LLM (:8001)
        if not response_text:
            try:
                payload = {
                    "model": "moe",
                    "messages": [
                        {"role": "system", "content": COURAGE_COMPUTER_SYSTEM_PROMPT},
                        {"role": "user", "content": f"CURRENT PROPERTY TELEMETRY:\n{context_str}\n\nINQUIRY: {query}"}
                    ],
                    "max_tokens": 180,
                    "temperature": 0.72,
                    "presence_penalty": 0.25
                }
                req = urllib.request.Request(
                    f"{self.coordinator_url}/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=40.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        response_text = data["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.warning(f"Coordinator LLM query failed for Courage persona: {e}")
                response_text = f"Oh splendid, my neural coordinator had a hiccup ({e}). But yes, you loaf, what do you want?"

        # Synthesize audio using Kokoro TTS bm_george @ 1.06x
        audio_b64 = None
        audio_bytes = voice_connector.synthesize_speech(response_text, voice="bm_george", speed=1.06)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        return {
            "query": query,
            "response": response_text,
            "voice": "bm_george",
            "speed": 1.06,
            "audio_base64": audio_b64,
            "has_audio": audio_b64 is not None,
            "timestamp": datetime.now(LOCAL_TZ).strftime("%I:%M:%S %p")
        }

home_presence_hub = HomePresenceHub()
