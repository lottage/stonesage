#!/usr/bin/env python3
"""
Entity Profiles & Grounded Memory Fabric for Humans and Pets.
Maintains structured profiles for the household's people and pets (data/profiles/entity_profiles.json,
private: never in code or the public repo).
Generates Valkey A-MEM atomic fact cards (< 35 tokens) and syncs to Qdrant companion_profile.
Includes extensible polling hooks for Home Assistant calendars and social/web data.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

from harness.config import fleet_config
from harness.core import datadir

logger = logging.getLogger("Harness.EntityProfiles")

PROFILES_FILE = datadir.resolve("profiles", "entity_profiles.json")
DATA_DIR = os.path.dirname(PROFILES_FILE)


class EntityProfileManager:
    def __init__(self, profiles_path: str = PROFILES_FILE):
        self.profiles_path = profiles_path
        self.profiles: Dict[str, Any] = self._load_profiles()
        self.polling_cache: Dict[str, Any] = {}

    def _load_profiles(self) -> Dict[str, Any]:
        if os.path.exists(self.profiles_path):
            try:
                with open(self.profiles_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load {self.profiles_path}: {e}")
        return {"people": [], "pets": []}     # no profiles file: nobody is known (never names in code)

    def save_profiles(self):
        os.makedirs(os.path.dirname(self.profiles_path), exist_ok=True)
        try:
            with open(self.profiles_path, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save {self.profiles_path}: {e}")

    def generate_amem_cards(self) -> List[Dict[str, Any]]:
        """
        Dense (< 35 tokens) core-memory cards for Valkey A-MEM: each profile's own "card" (people, then pets),
        then the household's "extra_cards". The wording lives in the profiles file, written for that household.
        """
        cards = []
        for entity in self.profiles.get("people", []) + self.profiles.get("pets", []):
            c = entity.get("card")
            if c and c.get("atom"):
                cards.append({"id": c.get("id") or f"profile_{entity.get('name', '').lower()}", "atom": c["atom"],
                              "keywords": list(c.get("keywords") or []), "category": c.get("category", "identity"),
                              "is_core_memory": True})
        for c in self.profiles.get("extra_cards", []):
            cards.append({"id": c["id"], "atom": c["atom"], "keywords": list(c.get("keywords") or []),
                          "category": c.get("category", "general"), "is_core_memory": True})
        return cards

    def sync_to_valkey(self, amem_instance=None):
        """Syncs all generated atomic cards to Valkey A-MEM."""
        cards = self.generate_amem_cards()
        if amem_instance:
            for c in cards:
                amem_instance.store_atom(
                    atom_id=c["id"],
                    atom_text=c["atom"],
                    keywords=c["keywords"],
                    category=c["category"],
                    confidence=1.0,
                    is_core_memory=True
                )
            logger.info(f"⚡ Synced {len(cards)} profile cards to Valkey A-MEM.")
        return cards

    def sync_to_qdrant(self, qdrant_url: str = "", embedder_url: str = ""):
        """Syncs full profile dossiers into Qdrant companion_profile collection."""
        qdrant_url, embedder_url = qdrant_url or fleet_config.qdrant_url, embedder_url or fleet_config.embedder_url
        try:
            points = []
            for person in self.profiles.get("people", []):
                content = (
                    f"### Person Profile: {person['name']}\n"
                    f"- Role: {person.get('role')}\n"
                    f"- Relations: {', '.join(person.get('relations', []))}\n"
                    f"- Gender: {person.get('gender')}, Ethnicity: {person.get('ethnicity')}\n"
                    f"- Physical Traits: {json.dumps(person.get('physical_traits', {}))}\n"
                    f"- Invariants: {'; '.join(person.get('invariants', []))}"
                )
                points.append({
                    "id": f"person-{person['name'].lower()}",
                    "content": content,
                    "title": f"Profile: {person['name']}",
                    "category": "people"
                })

            for pet in self.profiles.get("pets", []):
                content = (
                    f"### Pet Profile: {pet['name']} ({pet.get('species')})\n"
                    f"- Species: {pet.get('species').upper()}\n"
                    f"- Breed: {pet.get('breed')}\n"
                    f"- Gender: {pet.get('gender')}\n"
                    f"- Physical Traits: {json.dumps(pet.get('physical_traits', {}))}\n"
                    f"- Invariants: {'; '.join(pet.get('invariants', []))}"
                )
                points.append({
                    "id": f"pet-{pet['name'].lower()}",
                    "content": content,
                    "title": f"Pet Profile: {pet['name']}",
                    "category": "pets"
                })

            import uuid
            # Vectorize via Embedder
            for pt in points:
                req_emb = urllib.request.Request(
                    f"{embedder_url.rstrip('/')}/embeddings",
                    data=json.dumps({"input": pt["content"][:800], "model": "embedder"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req_emb, timeout=10) as resp:
                    emb = json.loads(resp.read().decode("utf-8"))["data"][0]["embedding"]

                point_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, pt["id"]))
                point_body = {
                    "points": [
                        {
                            "id": point_uuid,
                            "vector": emb,
                            "payload": {
                                "title": pt["title"],
                                "content": pt["content"],
                                "category": pt["category"],
                                "entity_id": pt["id"],
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                            }
                        }
                    ]
                }
                req_q = urllib.request.Request(
                    f"{qdrant_url.rstrip('/')}/collections/companion_profile/points",
                    data=json.dumps(point_body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT"
                )
                urllib.request.urlopen(req_q, timeout=10)
            logger.info(f"✔ Synced {len(points)} entity dossiers to Qdrant companion_profile.")
            return True
        except Exception as e:
            logger.warning(f"Failed to sync profiles to Qdrant: {e}")
            return False

    def poll_calendar_events(self, hass_url: str = "", token: str = "") -> Dict[str, Any]:
        """Polls current calendar events from Home Assistant (hass_url "" = the configured one)."""
        hass_url = hass_url or fleet_config.hass_url
        results = {}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for person in self.profiles.get("people", []):
            hook = person.get("polling_hooks", {}).get("ha_calendar", {})
            if hook.get("enabled") and hook.get("entity_id"):
                ent = hook["entity_id"]
                try:
                    url = f"{hass_url.rstrip('/')}/api/calendars/{ent}"
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        events = json.loads(resp.read().decode("utf-8"))
                        results[person["name"]] = events
                except Exception:
                    results[person["name"]] = []
        return results

    def get_grounded_entity_summary(self) -> str:
        """Concise markdown for system prompt injection: each profile's "summary" line plus the household's footer;
        "" when no profile has one."""
        entities = [e.get("summary") for e in self.profiles.get("people", []) + self.profiles.get("pets", [])]
        lines = [ln for ln in entities if ln] + list(self.profiles.get("summary_footer") or [])
        if not lines:
            return ""
        return "\n".join([self.profiles.get("summary_header") or "### [VERIFIED HOUSEHOLD RESIDENTS & PETS GROUND TRUTH]:"] + lines)


# Singleton instance
entity_profiles = EntityProfileManager()
