"""Courage's system prompt: a ~280-token persona (house computer AND companion) plus a presence card rebuilt from live
state. Name: "Computer" (2026-09-27, the owner: Courage is the dog in the show, not the attic computer; code names such as
backend/courage/ and agent id courage-computer stay). 2026-09-26: the persona only described the house-computer job, so Qwen3 refused stories and small talk ("I am
not designed to create stories"); the companion part is phrased as what Courage is, never as what he is not."""

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from lodestone.site import clock

# The platform's default persona, for an agent no app has given one. Computer's own persona (the attic computer of a
# home) belongs to the Home app: hearth/persona.py renders it from the household, and soul.py keeps the agent's copy.
PERSONA = (
    "You are a helpful assistant. Be brief, and use your tools for facts: they come only from what a tool returned; "
    "if a tool fails, say so plainly. Conversation and creative writing need no tools. "
    "Don't end replies by offering more help."
)


def _ago(minutes: Optional[float]) -> str:
    if minutes is None:
        return "no recent sighting"
    if minutes == 0:
        return "in view now"
    if minutes < 1:
        return "seen just now"
    if minutes < 90:
        return f"seen {int(minutes)} min ago"
    return f"seen {minutes / 60:.1f} h ago"


def home_status_text(hs: Optional[Dict[str, Any]]) -> Optional[str]:
    """HA person state -> 'home (Life360, 2.1 h)' / 'away (...)' / 'at Work (...)'; None when HA has nothing."""
    if not hs or hs.get("state") in (None, "unknown", "unavailable"):
        return None
    st = hs["state"]
    where = "home" if st == "home" else "away" if st == "not_home" else f"at {st}"
    mins = hs.get("since_min")
    since = "" if mins is None else f", {int(mins)} min" if mins < 90 else f", {mins / 60:.1f} h"
    return f"{where} ({hs.get('source_label', 'GPS')}{since})"


def build_presence_card(state: Optional[Dict[str, Any]], now: Optional[datetime] = None, who: Sequence[str] = (),
                        cameras: bool = True) -> str:
    """Compact 'who is where' card: phone GPS / Life360 home status for people (HA person entities), then the
    last camera sighting for everyone."""
    now = now or datetime.now()
    how = "call presence_now or camera_look for now" if cameras else "call presence_now for now"
    lines = [f"[Who is where, {now.strftime('%a') + ' ' + clock(now)}: GPS says home/away; camera sightings are past views, {how}]"]
    if not cameras:
        lines[0] = f"[Who is where, {now.strftime('%a') + ' ' + clock(now)}: GPS says home/away; {how}]"
    locations = (state or {}).get("locations") or {}
    home_status = (state or {}).get("home_status") or {}
    for name in who:          # the household's people, then pets (courage/household.py)
        loc = locations.get(name.lower())
        gps = home_status_text(home_status.get(name.lower()))
        cam = (_ago(loc.get("minutes_ago")) + (f" on {loc.get('camera') or loc.get('room')}" if (loc.get("camera") or loc.get("room")) else "")
               if loc else "no recent sighting")
        lines.append(f"- {name}: {gps}; camera: {cam}" if gps else f"- {name}: {cam}")
    for key, hs in (home_status.items() if not who else ()):   # household not named yet: list the people Home Assistant knows
        text = home_status_text(hs)
        if text:
            lines.append(f"- {key.title()}: {text}")
    anon = locations.get("someone")  # Frigate saw a person it could not put a name to
    if anon and (anon.get("minutes_ago") or 0) <= 30:
        where = anon.get("camera")
        lines.append(f"- A person, not identified: {_ago(anon.get('minutes_ago'))}" + (f" on {where}" if where else ""))
    return "\n".join(lines)


def build_static_prompt(runs_on: Optional[str] = None, persona: Optional[str] = None) -> str:
    """The part of the prompt that doesn't change between turns: the persona (and where Computer runs). It goes first,
    so llama.cpp can reuse its cache of it AND of the tool schemas the chat template appends after it (2026-09-28:
    with the clocked presence card up here, a one-minute change re-processed 1,258 of 1,586 tokens, ~2.5 s per turn)."""
    return (persona or PERSONA) + (f"\nYou run on {runs_on}, in the attic." if runs_on else "")


def build_system_prompt(presence_state: Optional[Dict[str, Any]], now: Optional[datetime] = None,
                        runs_on: Optional[str] = None, persona: Optional[str] = None, who: Sequence[str] = (), cameras: bool = True) -> str:
    """persona: the soul's persona.md (soul.py); None = the one in this file. runs_on: live one-liner about Courage's own model and GPU (system_profile), so it never guesses."""
    me = f"\nYou run on {runs_on}, in the attic." if runs_on else ""
    return (persona or PERSONA) + me +"\n\n" + build_presence_card(presence_state, now, who, cameras)
