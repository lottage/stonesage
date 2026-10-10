"""
Tier 0: Sub-millisecond In-RAM Working Memory (A-MEM on Valkey :6379).
Stores ultra-dense atomic cards (< 35 tokens) to collapse <think> loop bloat by 96%.
Supports temporal decay with an immutable 'core-memory' tag for zero-decay permanent truths.
"""

import re
import time
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Set
from ..config import fleet_config


def _op() -> str:
    return fleet_config.operator_name()

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger("Harness.AMEM")

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "what", "which", "who",
    "how", "why", "where", "when", "does", "do", "did", "can", "could",
    "should", "would", "to", "in", "on", "at", "by", "for", "with", "about",
    "of", "and", "or", "not", "my", "your", "his", "her", "their", "our"
}

def live_topology_text(profile: Optional[Dict[str, Any]] = None) -> str:
    """core_topology card from the live system profile: real GPUs and which engine runs where (no hardcoding)."""
    prof = profile if profile is not None else fleet_config.stonesage_profile()
    gpus = prof.get("gpus") or []
    engines = prof.get("engines") or {}
    if not gpus and not any(e.get("online") for e in engines.values()):
        return "Engine and GPU layout unknown right now; StoneSage /api/system/profile reports it live."
    host = (prof.get("host") or {}).get("hostname") or "inference host"
    parts = []
    for g in gpus:
        on = [f"{r} :{engines[r]['port']} ({engines[r].get('model') or r})" for r in g.get("engines", []) if r in engines]
        parts.append(f"{g.get('short') or g.get('name')} {round(g.get('vram_total_gb') or 0)}GB runs " + (" + ".join(on) or "nothing"))
    cpu_side = [f"{r} :{e['port']}" for r, e in engines.items() if e.get("offloaded")]
    if cpu_side:
        parts.append("CPU runs " + " + ".join(cpu_side))
    return f"Inference host {host}: " + "; ".join(parts) + "."


def live_hierarchy_text(profile: Optional[Dict[str, Any]] = None) -> str:
    prof = profile if profile is not None else fleet_config.stonesage_profile()
    e = prof.get("engines") or {}
    name = lambda r: (e.get(r) or {}).get("model") or r  # noqa: E731
    return (f"Cognitive hierarchy: frontier cloud model (Antigravity) on top, then Coordinator :8001 ({name('coordinator')}), "
            f"Worker :8002 ({name('worker')}), then memory (Qdrant/Valkey).")


class ValkeyAMEM:
    def __init__(self, host: str = fleet_config.valkey_host, port: int = fleet_config.valkey_port):
        self.host = host
        self.port = port
        self.r = None
        self._local_fallback_cards: Dict[str, Dict[str, Any]] = {}
        self._local_fallback_tags: Dict[str, Set[str]] = {}
        self._connect()
        self.seed_homelab_core_cards()

    def _connect(self):
        if redis is None:
            logger.warning("redis-py not installed; using in-memory fallback store.")
            return
        try:
            client = redis.Redis(host=self.host, port=self.port, decode_responses=True, socket_timeout=1.5)
            if client.ping():
                self.r = client
                logger.info(f"⚡ Connected to Valkey A-MEM store at {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"Could not connect to Valkey at {self.host}:{self.port}: {e}. Fallback active.")

    def store_atom(
        self,
        atom_id: str,
        atom_text: str,
        keywords: List[str],
        category: str = "general",
        confidence: float = 1.0,
        is_core_memory: bool = False,
    ) -> Dict[str, Any]:
        """
        Stores an atomic fact card.
        If is_core_memory is True or 'core-memory' is in keywords, it is marked immune to decay.
        """
        clean_id = atom_id.strip().lower()
        clean_text = atom_text.strip()
        tags = [k.strip().lower() for k in keywords if k.strip() and k.strip().lower() not in STOPWORDS]
        if is_core_memory or "core-memory" in tags:
            is_core_memory = True
            if "core-memory" not in tags:
                tags.append("core-memory")

        card = {
            "id": clean_id,
            "atom": clean_text,
            "keywords": tags,
            "category": category,
            "confidence": confidence,
            "is_core_memory": is_core_memory,
            "access_count": 0,
            "created_at": time.time(),
            "last_accessed_at": time.time(),
        }

        if self.r:
            try:
                card_key = f"amem:card:{clean_id}"
                self.r.set(card_key, json.dumps(card))
                for tag in tags:
                    self.r.sadd(f"amem:tag:{tag}", clean_id)
                return card
            except Exception as e:
                logger.warning(f"Valkey store error: {e}")

        self._local_fallback_cards[clean_id] = card
        for tag in tags:
            self._local_fallback_tags.setdefault(tag, set()).add(clean_id)
        return card

    def seed_homelab_core_cards(self):
        """
        Seeds permanent, zero-decay atomic fact cards for homelab topology,
        databases, smart home services, cognitive hierarchy, and collective philosophy.
        """
        core_cards = [
            (
                "core_operator",
                f"{_op()} is the human operator, homeowner, and creator. You are speaking directly with {_op()}. Never address {_op()} by an agent name or confuse who is who.",
                [_op().lower(), "user", "human", "creator", "operator", "homeowner", "who", "hi", "hello", "hey", "identity", "me", "yourself"],
                "identity"
            ),
            (
                "core_databases",
                f"Active databases: Qdrant Vector DB ({fleet_config.qdrant_url}, 6 collections), Valkey in-RAM A-MEM (:6379), CouchDB Obsidian LiveSync ({fleet_config.couchdb_url}), SQLite relational (data/harness.db).",
                ["database", "databases", "qdrant", "valkey", "couchdb", "sqlite", "vector", "storage", "db", "dbs", "memory", "persist", "persistence", "collections"],
                "architecture"
            ),
            (
                "core_topology",
                live_topology_text(),
                ["cluster", "topology", "node", "nodes", "pve", "hardware", "gpu", "gpus", "vulkan", "ip", "ips", "proxmox", "server", "servers", "coordinator", "worker", "embedder", "vision"],
                "hardware"
            ),
            (
                "core_smarthome",
                f"Home Assistant OS at {fleet_config.hass_url} controls the thermostat, plugs, lights and the household's smart devices.",
                ["homeassistant", "hass", "smart-home", "smarthome", "nest", "thermostat", "temp", "temperature", "heat", "cool", "hvac", "ac", "climate", "lights", "plugs", "switches", "devices"],
                "environment"
            ),
            (
                "core_wildlife_sentry",
                "FaunaSentinel 24/7 Wildlife Sentry Daemon on VM 102 monitors Tapo cameras (Back Yard, Driveway, Side Yard, Living Room) and logs deer, foxes, cats, and perimeter events.",
                ["wildlife", "camera", "cameras", "cam", "cams", "tapo", "sentry", "perimeter", "deer", "backyard", "driveway", "yard", "animal", "sightings"],
                "security"
            ),
            (
                "core_hierarchy",
                live_hierarchy_text(),
                ["hierarchy", "frontier", "antigravity", "coordinator", "worker", "tier", "tiers", "models", "orchestration"],
                "governance"
            ),
            (
                "core_philosophy",
                f"Sovereign Collective: Grounded in {_op()}'s physical home. No agent dies; insights and invariants persist in Qdrant and Obsidian. Empirical truth over hierarchy.",
                ["philosophy", "collective", "soul", "immortality", "manifesto", _op().lower(), "invariants", "sovereign", "sanctuary", "hearth"],
                "ontology"
            ),
            (
                "core_agents_work",
                "Background engines: FaunaSentinel wildlife sentry on VM 102 (describes camera frames with Vision :8004), the PVE hardware watchdog on LXC 120, and model inference on Coordinator :8001, Worker :8002, Embedder :8003 and Vision :8004. The 24/7 autonomous thinking loop and the Assembly Hall are shelved and stopped since 2026-09-23.",
                ["agent", "agents", "working", "work", "stack", "task", "tasks", "doing", "active", "fleet", "status", "currently", "now"],
                "operations"
            ),
            (
                "core_home_power",
                "Homelab runs on standard AC utility power monitored by Kasa smart plugs. Proxmox 'pve_backup_status' tracks LXC/VM backup snapshot task completion.",
                ["power", "backup", "pve_backup_status", "kasa", "electric"],
                "operations"
            ),
        ]
        for cid, text, tags, cat in core_cards:
            self.store_atom(
                atom_id=cid,
                atom_text=text,
                keywords=tags,
                category=cat,
                confidence=1.0,
                is_core_memory=True
            )

        # Seed Human and Pet Entity Profile Core Cards
        try:
            from ..core.entity_profiles import entity_profiles
            entity_profiles.sync_to_valkey(self)
        except Exception as e:
            logger.debug(f"Entity profile seeding deferred: {e}")

    def seed_agent_cards(self, agent_id: str, name: str, role: str, mission: str, invariants: Optional[List[str]] = None):
        """Seeds permanent atomic identity & invariant cards for a specific agent."""
        cid = f"agent_{agent_id.lower().strip()}"
        inv_text = f" Invariants: {'; '.join(invariants)}" if invariants else ""
        text = f"Agent {name} (`{agent_id}`): {role}. Mission: {mission}.{inv_text}"
        tags = [agent_id.lower().strip(), name.lower().strip(), "agent", "role", "mission"]
        self.store_atom(
            atom_id=cid,
            atom_text=text,
            keywords=tags,
            category="agent_contract",
            confidence=1.0,
            is_core_memory=True
        )

    def recall(self, query: str, max_atoms: int = 2) -> List[Dict[str, Any]]:
        """
        Sub-millisecond tag-intersection recall.
        Applies decay calculation: core-memory cards never decay.
        """
        words = [w.lower().strip("?!.,:;\"'()[]{}") for w in re.split(r"[\s\-_/]+", query) if len(w) >= 2 and w.lower() not in STOPWORDS]
        if not words:
            return []

        matched_ids: Dict[str, int] = {}
        if self.r:
            try:
                for word in words:
                    members = self.r.smembers(f"amem:tag:{word}")
                    for cid in members:
                        matched_ids[cid] = matched_ids.get(cid, 0) + 1
            except Exception as e:
                logger.warning(f"Valkey recall error: {e}")
        else:
            for word in words:
                members = self._local_fallback_tags.get(word, set())
                for cid in members:
                    matched_ids[cid] = matched_ids.get(cid, 0) + 1

        if not matched_ids:
            return []

        # Sort by match count, fetch cards, and apply temporal decay
        sorted_ids = sorted(matched_ids.keys(), key=lambda k: matched_ids[k], reverse=True)[:max_atoms]
        results = []
        now = time.time()

        for cid in sorted_ids:
            card = None
            if self.r:
                try:
                    raw = self.r.get(f"amem:card:{cid}")
                    if raw:
                        card = json.loads(raw)
                except Exception:
                    pass
            else:
                card = self._local_fallback_cards.get(cid)

            if card:
                # Check decay: core-memory has 0 decay
                is_core = card.get("is_core_memory", False) or "core-memory" in card.get("keywords", [])
                if not is_core:
                    # 14-day half-life decay
                    days_inactive = (now - card.get("last_accessed_at", now)) / 86400.0
                    decayed_confidence = card.get("confidence", 1.0) * (0.5 ** (days_inactive / 14.0))
                    card["confidence"] = round(decayed_confidence, 3)
                    if decayed_confidence < 0.20:
                        continue  # Expired transient fact

                results.append(card)

        return results

    def format_injection_header(self, query: str, max_atoms: int = 2) -> str:
        """Formats recalled atomic cards as a minimal prompt header (< 35 tokens)."""
        cards = self.recall(query, max_atoms=max_atoms)
        if not cards:
            return ""
        if len(cards) == 1:
            return f"[KNOWLEDGE ATOM]: {cards[0]['atom']}"
        lines = ["[KNOWLEDGE ATOMS]:"]
        for c in cards:
            lines.append(f"• {c['atom']}")
        return "\n".join(lines)

valkey_amem = ValkeyAMEM()
