"""
Courage's curated tool set.

Rules (the owner's decisions, 2026-09-23 plan):
- Reading states and cameras, including PTZ moves, runs freely.
- Other actions (HA service calls, notifications, announcements) run at once when the user ordered them,
  and need approval when Courage inferred them (2026-09-23). Unlock and open-cover always need approval.
- ha_call is limited to an allowlist of domain/service pairs; anything else is refused outright.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .household import Household
from .prompt import home_status_text

READ_DOMAINS = ["light", "switch", "climate", "sensor", "binary_sensor", "media_player", "lock", "cover", "fan", "person"]

# domain -> services Courage may request (always behind approval)
ALLOWED_SERVICES: Dict[str, List[str]] = {
    "light": ["turn_on", "turn_off", "toggle"],
    "switch": ["turn_on", "turn_off", "toggle"],
    "fan": ["turn_on", "turn_off"],
    "climate": ["set_temperature", "set_hvac_mode"],
    "media_player": ["media_pause", "media_play", "volume_set"],
    "scene": ["turn_on"],
    "cover": ["open_cover", "close_cover"],
    "lock": ["lock", "unlock"],
}

# smart plugs feeding the site's own servers: never switched off. These are the generic words; a site adds the names of
# its own machines in config.json "infra_words" (Household.infra_words; hearth/app.py /api/hass/call uses the same list)
INFRA_WORDS = ("server", "nas", "router")


def infra_words(extra=()) -> tuple:
    """The generic infrastructure words plus the site's own (lower-cased, empty entries dropped)."""
    return INFRA_WORDS + tuple(str(w).lower() for w in (extra or ()) if str(w).strip())
# Direct commands run at once; these still ask even when ordered outright (security)
ALWAYS_CONFIRM = {("lock", "unlock"), ("cover", "open_cover")}
# words in the user's message that make an action a direct order, per HA service / tool
DIRECT_SERVICE_WORDS: Dict[str, str] = {
    "turn_on": r"\b(turn|switch|put|flip)\b.*\bon\b|\b(on|up)\s*(please|now)?\s*$|\b(light up|start|activate)\b",
    "turn_off": r"\b(turn|switch|shut|put|flip)\b.*\b(off|down)\b|\b(off|kill)\b",
    "toggle": r"\btoggle\b|\b(turn|switch|flip)\b",
    "set_temperature": r"\b(set|raise|lower|bump|turn|make|increase|decrease|drop)\b|\bdegrees?\b|\d",
    "set_hvac_mode": r"\b(set|switch|turn|put)\b",
    "media_pause": r"\b(pause|stop)\b",
    "media_play": r"\b(play|resume|unpause)\b",
    "volume_set": r"\b(volume|louder|quieter|turn (it )?(up|down)|mute)\b",
    "lock": r"\block\b",
    "close_cover": r"\b(close|shut|lower)\b",
}
DIRECT_TOOL_WORDS: Dict[str, str] = {
    "notify": r"\b(send|notify|text|message|ping|push|remind)\b",
    "speak": r"\b(announce|say|tell|broadcast|shout|yell|call out)\b",
    # "that wasn't Rex, it was Bella": the owner telling him the name is wrong. Computer spotting it himself asks first.
    "correct_sighting": r"\b(wasn'?t|was not|isn'?t|is not|wrong|mistake|mislabel\w*|actually|really|it was|that was|"
                        r"correct(ion)?|relabel|fix|not (him|her|them|me)|mixed up)\b",
}

# A message that ASKS ("Is the kitchen light on?", "what's the thermostat set to") is answered, never acted on. Voice
# transcripts often lack the question mark, so it goes by how the message starts. "Can you turn it off?" and
# "do you mind..." are requests, not questions.
QUESTION_START = re.compile(r"^\s*(is|are|was|were|does|do|did|has|have|had|what|what's|whats|which|who|whose|where|"
                            r"when|why|how|should|any(one|body|thing)?)\b", re.I)
REQUEST_WORDS = re.compile(r"\b(please|do you mind|would you mind|can you|could you|will you|would you|for me|"
                           r"go ahead|i want you to|i need you to)\b", re.I)


def is_question(text: str) -> bool:
    return bool(QUESTION_START.match(text or "")) and not REQUEST_WORDS.search(text or "")


FILLER_WORDS ={"any", "all", "every", "the", "a", "an", "my", "our", "house", "home", "right", "now", "currently", "on", "off"}

RECENT_COMMAND_S = 10.0   # how long a command we sent outweighs Home Assistant's still-lagging state
MAX_RESULT_CHARS = 1500
STALE_MINUTES = 10  # presence_now(who) looks through a camera itself when the last sighting is older than this
MAX_ENTITIES = 25


@dataclass
class CourageDeps:
    """Callables Courage's tools use. Server wires real clients; tests pass fakes."""
    ha_states: Callable[[Optional[str]], Dict[str, Any]]                      # domain -> {"ok", "entities": [...]}
    ha_call: Callable[[str, str, Dict[str, Any]], Dict[str, Any]]            # domain, service, data -> {"ok", ...}
    presence: Callable[[], Dict[str, Any]]                                   # -> presence hub state
    camera_look: Callable[..., Optional[str]]                                # entity, name[, people_only=True] -> description
    camera_scan: Callable[[str, str], Optional[str]]                         # entity, name -> multi-angle report
    memory_search: Callable[[str], List[Dict[str, Any]]] = field(default=lambda q: [])
    notify: Callable[[str, str], Dict[str, Any]] = field(default=lambda msg, target: {"ok": False, "error": "notify not wired"})
    speak: Callable[[str, str], Dict[str, Any]] = field(default=lambda msg, room: {"ok": False, "error": "speak not wired"})
    # who (name on the sighting now), action ("is_really" | "wrong"), name -> the same correction as the Residents & Pets
    # card buttons (server._courage_correct_sighting)
    correct_sighting: Callable[[str, str, str], Dict[str, Any]] = field(
        default=lambda who, action, name: {"ok": False, "error": "corrections not wired"})
    # question, kind -> {"ok", "answer", "source"}; None = Boost off for Courage (the tool is not offered at all)
    think_harder: Optional[Callable[[str, str], Dict[str, Any]]] = None
    think_harder_available: Callable[[], bool] = field(default=lambda: True)  # the Boost toggle, read per turn
    # who and what the home has (cameras, people, pets, phones, speakers): supplied by the Home app; empty = no home
    household: Household = field(default_factory=Household)


def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


def _clip(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " …(truncated)"


class CourageTools:
    def __init__(self, deps: CourageDeps):
        self.deps = deps
        self.clock: Callable[[], float] = time.monotonic          # tests move it
        self._commanded: Dict[str, Tuple[Optional[str], float]] = {}   # entity -> (state we left it in, when)
        hh = deps.household
        cams = list(hh.cameras)
        whom = " and ".join(p for p in ((f"people ({', '.join(hh.people)})" if hh.people else ""),
                                        (f"pets ({hh.pets_text()})" if hh.pets else "")) if p) or "people and pets"
        approver = f"{hh.owner} approves it first."
        if len(hh.phones) == 1:
            notify_text = f"Send a push notification to {hh.phones[0].title()}'s phone (the only phone registered in Home Assistant). {approver}"
        else:
            notify_text = f"Send a push notification to a household phone ({', '.join(hh.phones) or 'none registered'}). {approver}"
        was, really = hh.example_pair()
        self.specs: Dict[str, Dict[str, Any]] = {
            "presence_now": {
                "description": f"Where {whom} are: last camera sighting, when, "
                               "and what they were doing. Pass `who` to find one of them: if their sighting is stale this also looks "
                               "through the camera right now.",
                "parameters": _schema({"who": {"type": "string", "enum": hh.keys}}, []),
                "handler": self._presence_now, "approval": False,
                "status": "Checking who's about…",
            },
            "ha_get_states": {
                "description": "Read current Home Assistant states for one domain, optionally filtered by a word in the name (area or device).",
                "parameters": _schema({"domain": {"type": "string", "enum": READ_DOMAINS},
                                       "name_contains": {"type": "string", "description": "only when the user names a room or device, e.g. 'kitchen', 'tv'; omit for 'any'/'all'"}}, ["domain"]),
                "handler": self._ha_get_states, "approval": False,
                "status": "Reading the house…",
            },
            "camera_look": {
                "description": "Look through one camera right now and describe what is visible (people, pets, vehicles, animals).",
                "parameters": _schema({"camera": {"type": "string", "enum": cams}}, ["camera"]),
                "handler": self._camera_look, "approval": False,
                "status": "Looking at the {camera} camera…",
            },
            "camera_scan": {
                "description": "Pan a PTZ camera through its presets and describe each view. Slower (about 20-40 s); use when one look is not enough.",
                "parameters": _schema({"camera": {"type": "string", "enum": list(hh.ptz_cameras)}}, ["camera"]),
                "handler": self._camera_scan, "approval": False,
                "status": "Sweeping the {camera} camera…",
            },
            "memory_search": {
                "description": "Search long-term memory: what people told you in earlier conversations, and notes about "
                               "the home, family, devices or past events.",
                "parameters": _schema({"query": {"type": "string"}}, ["query"]),
                "handler": self._memory_search, "approval": False,
                "status": "Rummaging through my notes…",
            },
            "ha_call": {
                "description": f"Change a device through Home Assistant (lights, switches, fans, climate, media, scenes, covers, locks). {approver}",
                "parameters": _schema({"domain": {"type": "string", "enum": sorted(ALLOWED_SERVICES)},
                                       "service": {"type": "string", "description": "e.g. turn_on, turn_off, set_temperature"},
                                       "entity_id": {"type": "string"},
                                       "data": {"type": "object", "description": "extra service data, e.g. {\"temperature\": 70}"}},
                                      ["domain", "service", "entity_id"]),
                "handler": self._ha_call, "approval": True,
                "status": "Asking to {service} {entity_id}…",
            },
            "notify": {
                "description": notify_text,
                "parameters": _schema({"message": {"type": "string"}, "target": {"type": "string", "enum": list(hh.phones)}}, ["message"]),
                "handler": self._notify, "approval": True,
                "status": "Asking to send a notification…",
            },
            "speak": {
                "description": f"Announce a short message out loud on the Echo speakers ({hh.speakers_text()}). {approver}",
                "parameters": _schema({"message": {"type": "string"}, "room": {"type": "string", "enum": list(hh.speakers)}}, ["message"]),
                "handler": self._speak, "approval": True,
                "status": "Asking to make an announcement…",
            },
        }
        self.specs["correct_sighting"] = {
            "description": f"Fix who a camera sighting shows, when the name on it is wrong ('that wasn't {was}, it was "
                           f"{really}'). Acts on the newest sighting of `who` (the name shown now; 'someone' for an unidentified "
                           "person). action is_really re-files it under `name` (for a person this also teaches face "
                           "recognition); wrong hides it. The previous sighting then shows.",
            "parameters": _schema({"who": {"type": "string", "description": f"name on the sighting now, e.g. {was.lower()}"},
                                   "action": {"type": "string", "enum": ["is_really", "wrong"]},
                                   "name": {"type": "string", "description": "who it really was (for is_really)"}},
                                  ["who", "action"]),
            "handler": self._correct_sighting, "approval": True,
            "status": "Correcting {who}'s sighting…",
        }
        if deps.think_harder is not None:
            self.specs["think_harder"] = {
                "description": "Ask a much larger model for help with a hard question: reasoning, maths, science, coding or "
                               "general knowledge you are unsure of. Write `question` so it stands alone. Never use it for "
                               "anything you can answer with the house tools (presence, cameras, devices).",
                "parameters": _schema({"question": {"type": "string"},
                                       "kind": {"type": "string", "enum": ["general", "code", "home"],
                                                "description": "home only if the question must include facts about the house"}},
                                      ["question"]),
                "handler": self._think_harder, "approval": False,
                "status": "Borrowing a bigger brain…", "available": deps.think_harder_available,
            }

    # ---- schema / metadata -------------------------------------------------
    def _available(self, name: str) -> bool:
        check = self.specs.get(name, {}).get("available")
        try:
            return check is None or bool(check())
        except Exception:
            return False

    def openai_tools(self) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {"name": n, "description": s["description"], "parameters": s["parameters"]}}
                for n, s in self.specs.items() if self._available(n)]

    def needs_approval(self, name: str) -> bool:
        return bool(self.specs.get(name, {}).get("approval"))

    def status_text(self, name: str, args: Dict[str, Any]) -> str:
        tpl = self.specs.get(name, {}).get("status", f"Running {name}…")
        try:
            return tpl.format(**{k: str(v).replace("_", " ") for k, v in args.items()})
        except (KeyError, IndexError):
            return tpl.split("{")[0].strip() or f"Running {name}…"

    def validate(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        """Return an error string if the call must be refused before approval is even asked."""
        if name not in self.specs or not self._available(name):
            return f"unknown tool '{name}'"
        if name == "ha_call":
            domain, service, eid = args.get("domain", ""), args.get("service", ""), args.get("entity_id", "")
            if service not in ALLOWED_SERVICES.get(domain, []):
                return f"{domain}.{service} is not on Computer's allowlist"
            if not eid.startswith(f"{domain}."):
                return f"entity '{eid}' is not a {domain} entity"
            if domain == "switch" and service in ("turn_off", "toggle") and any(w in eid.lower() for w in infra_words(self.deps.household.infra_words)):
                return f"refused: {eid} powers server infrastructure (would take me down with it)"
            entity, candidates = self.resolve_entity(domain, eid)
            if entity is None and candidates is not None:
                hint = f" Closest: {', '.join(candidates)}." if candidates else f" Call ha_get_states for {domain} to find it."
                return f"no entity '{eid}' in Home Assistant.{hint}"
        if name == "correct_sighting":
            if args.get("action") not in ("is_really", "wrong"):
                return "action must be is_really or wrong"
            if args.get("action") == "is_really" and not (args.get("name") or "").strip():
                return "say who it really was (name)"
            if not (args.get("who") or "").strip():
                return "whose sighting? (who)"
        if name in ("camera_look", "camera_scan") and args.get("camera") not in self.deps.household.cameras:
            return f"unknown camera '{args.get('camera')}'"
        return None

    def resolve_entity(self, domain: str, entity_id: str):
        """(entity, None) if it exists; (None, [close ids]) if not; (None, None) if HA can't be read (don't block)."""
        try:
            res = self.deps.ha_states(domain) or {}
        except Exception:
            return None, None
        if not res.get("ok", True):
            return None, None
        ents = res.get("entities") or []
        for e in ents:
            if e.get("entity_id") == entity_id:
                return e, None
        words = [w for w in entity_id.split(".", 1)[-1].split("_") if len(w) > 2]
        close = [e["entity_id"] for e in ents
                 if any(w in f"{e.get('entity_id', '')} {e.get('friendly_name', '')}".lower() for w in words)]
        return None, close[:6]

    def describe_action(self, name: str, args: Dict[str, Any]) -> str:
        if name == "ha_call":
            entity, _ = self.resolve_entity(args.get("domain", ""), args.get("entity_id", ""))
            label = (entity or {}).get("friendly_name") or args.get("entity_id")
            data = args.get("data") or {}
            if args.get("service") == "set_temperature" and "temperature" in data:
                now = (entity or {}).get("current_temperature") or ((entity or {}).get("attributes") or {}).get("current_temperature")
                return f"set the {label} to {data['temperature']}°" + (f" (it's {now}° now)" if now is not None else "")
            if args.get("service") == "set_hvac_mode" and data.get("hvac_mode"):
                return f"switch the {label} to {data['hvac_mode']}"
            extra = f" ({', '.join(f'{k} {v}' for k, v in args['data'].items())})" if args.get("data") else ""
            return f"{args.get('service', '').replace('_', ' ')} the {label}{extra}"
        if name == "notify":
            return f"notify {args.get('target') or self._default_phone()}: \"{args.get('message', '')}\""
        if name == "speak":
            return f"announce in {args.get('room', 'kitchen')}: \"{args.get('message', '')}\""
        if name == "correct_sighting":
            who = str(args.get("who", "")).capitalize()
            if args.get("action") == "wrong":
                return f"hide {who}'s latest camera sighting as wrong"
            return f"mark {who}'s latest camera sighting as really {str(args.get('name', '')).capitalize()}"
        return f"{name}({json.dumps(args)})"

    def is_direct_command(self, name: str, args: Dict[str, Any], user_text: str) -> bool:
        """True when the user's own message ordered this action, so it runs without asking.

        Courage only asks when it inferred the action ("it's cold in here" -> raise the heat?).
        Unlocking doors and opening covers (garage) always ask.
        """
        text = (user_text or "").lower()
        if is_question(text):
            return False
        if name == "ha_call":
            service = args.get("service", "")
            if (args.get("domain"), service) in ALWAYS_CONFIRM:
                return False
            pattern = DIRECT_SERVICE_WORDS.get(service)
            return bool(pattern and re.search(pattern, text))
        pattern = DIRECT_TOOL_WORDS.get(name)
        return bool(pattern and re.search(pattern, text))

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        """Run a tool (approval must already be settled by the caller). Returns a clipped string for the model."""
        err = self.validate(name, args)
        if err:
            return _clip({"ok": False, "error": err})
        try:
            return _clip(self.specs[name]["handler"](**args))
        except TypeError as e:
            return _clip({"ok": False, "error": f"bad arguments: {e}"})
        except Exception as e:  # a tool failure must never kill the loop
            return _clip({"ok": False, "error": f"{type(e).__name__}: {e}"})

    # ---- handlers ------------------------------------------------------------
    def _camera_key(self, label: Optional[str]) -> str:
        """Sentry location label ('Living', 'Driveway') -> camera key; the household's default camera."""
        return self.deps.household.camera_key(label)

    def _default_phone(self) -> str:
        return (self.deps.household.phones or [""])[0]

    def _presence_now(self, who: str = "") -> Dict[str, Any]:
        state = self.deps.presence() or {}
        locs = state.get("locations") or {}
        seen = {name: {"minutes_ago": v.get("minutes_ago"), "camera": v.get("camera"), "doing": v.get("doing")}
                for name, v in locs.items()}
        home = {n: t for n, t in ((n, home_status_text(hs)) for n, hs in (state.get("home_status") or {}).items()) if t}
        who = (who or "").lower().strip()
        if not who:
            return {"ok": True, "gps": home, "last_seen": seen,
                    "note": "gps = phone/Life360 home status (trust it for home or away); last_seen = past camera views."}
        loc = seen.get(who)
        out: Dict[str, Any] = {"ok": True, "who": who, "last_seen": loc or "no recent camera sighting"}
        if who in home:
            out["gps"] = home[who]
        away = (state.get("home_status") or {}).get(who, {}).get("state") not in (None, "home", "unknown", "unavailable")
        if away:
            out["note"] = "GPS says they are not home, so I did not look through the cameras."
        elif loc is None or (loc.get("minutes_ago") or 0) > STALE_MINUTES:
            cam = self.deps.household.cameras[self._camera_key(loc and loc.get("camera"))]
            out["looked_now"] = {"camera": cam["name"],
                                 "sees": self.deps.camera_look(cam["entity"], cam["name"], people_only=True)
                                 or "camera snapshot or vision failed"}
            out["note"] = ("The sighting was stale, so I looked just now. If they are not in this view, say where they "
                           "were last seen and that you can't see them now.")
        return out

    def _ha_get_states(self, domain: str, name_contains: str = "") -> Dict[str, Any]:
        res = self.deps.ha_states(domain) or {}
        if not res.get("ok", True):
            return {"ok": False, "error": res.get("error", "Home Assistant unavailable")}
        words = [w for w in (name_contains or "").lower().replace("_", " ").split()
                 if w not in FILLER_WORDS and w.rstrip("s") != domain]
        rows = []
        for e in res.get("entities", []):
            name = e.get("friendly_name") or e.get("entity_id")
            row = {"entity_id": e.get("entity_id"), "name": name, "state": e.get("state")}
            attrs = e.get("attributes") or {}
            for key in ("current_temperature", "temperature", "hvac_action", "brightness", "unit_of_measurement"):
                if key in attrs:
                    row[key] = attrs[key]
            rows.append(row)
        out: Dict[str, Any] = {"ok": True}
        if words:
            hits = [r for r in rows if any(w in f"{r['name']} {r['entity_id']}".lower() for w in words)]
            if hits:
                rows = hits
            else:  # a filter that matches nothing must not read as "nothing is on"
                out["note"] = f"no {domain} names matched '{name_contains}'; showing all {domain} entities"
        out.update(count=len(rows), entities=rows[:MAX_ENTITIES])
        return out

    def _camera_look(self, camera: str) -> Dict[str, Any]:
        cam = self.deps.household.cameras[camera]
        desc = self.deps.camera_look(cam["entity"], cam["name"])
        return {"ok": bool(desc), "camera": cam["name"], "description": desc or "camera snapshot or vision failed"}

    def _camera_scan(self, camera: str) -> Dict[str, Any]:
        cam = self.deps.household.cameras[camera]
        report = self.deps.camera_scan(cam["entity"], cam["name"])
        return {"ok": bool(report), "camera": cam["name"], "report": report or "scan failed"}

    def _memory_search(self, query: str) -> Dict[str, Any]:
        hits = self.deps.memory_search(query) or []
        return {"ok": True, "results": [str(h.get("text") or h.get("atom") or h)[:300] for h in hits[:5]]}

    def _ha_call(self, domain: str, service: str, entity_id: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = dict(data or {})
        payload["entity_id"] = entity_id
        res = self.deps.ha_call(domain, service, payload) or {}
        if res.get("ok"):
            # what we just left it in (None = unknown, e.g. toggle); see changed_just_now
            self._commanded[entity_id] = ({"turn_on": "on", "turn_off": "off"}.get(service), self.clock())
        return {"ok": bool(res.get("ok")), "done": f"{domain}.{service} {entity_id}", "error": res.get("error")}

    def changed_just_now(self, entity_id: str, wanted_state: str) -> bool:
        """True when we sent this entity a command in the last RECENT_COMMAND_S that left it somewhere other than
        `wanted_state`. Home Assistant reports a change about a second after the service call returns (measured
        2026-09-29: the state landed 0.6-0.9 s after the command), so an "already off" read in that window is stale:
        the order must go out anyway (a repeated on/off is harmless; a false "already" ignores the user)."""
        last = self._commanded.get(entity_id)
        if not last or self.clock() - last[1] > RECENT_COMMAND_S:
            return False
        return last[0] != wanted_state

    def _correct_sighting(self, who: str, action: str, name: str = "") -> Dict[str, Any]:
        return self.deps.correct_sighting(who, action, name)

    def _notify(self, message: str, target: str = "") -> Dict[str, Any]:
        return self.deps.notify(message, target or self._default_phone())

    def _speak(self, message: str, room: str = "") -> Dict[str, Any]:
        return self.deps.speak(message, room or (self.deps.household.speakers or [""])[0])

    def _think_harder(self, question: str, kind: str = "general") -> Dict[str, Any]:
        return self.deps.think_harder(question, kind if kind in ("general", "code", "home") else "general")
