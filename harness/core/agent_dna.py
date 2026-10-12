"""
Forever-Persistent Sovereign Agent DNA Container & Lifecycle Engine.
Decouples agent identity, soul, cognitive state (Valkey A-MEM), and semantic vector memory (Qdrant)
from underlying LLM model weights, quants, or hardware topology.

Enables agents to persist forever across model generations (Qwen3.8 -> Gemma 4 -> future 100B+ AGI)
with full re-hydration, bilateral lineage tracking, and portable .agent.dna packaging.
"""

import os
import sys
import time
import json
import tarfile
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from ..config import fleet_config
from . import datadir
from ..data_fabric.valkey_amem import ValkeyAMEM
from ..data_fabric.qdrant_brain import QdrantBrain

logger = logging.getLogger("Harness.AgentDNA")

AGENT_STORAGE_DIR = datadir.resolve("agent_dna")      # the data folder (core/datadir.py), or where an older install has it

class AgentDNAManager:
    def __init__(self, storage_dir: str = AGENT_STORAGE_DIR):
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)
        self.valkey = ValkeyAMEM()
        self.qdrant = QdrantBrain()
        self._ensure_foundational_agents()

    def _ensure_foundational_agents(self) -> None:
        """Seeds default sovereign agent profiles if none exist."""
        default_agents = [
            {
                "manifest": {
                    "dna_version": "1.0.0",
                    "agent_id": "coder-agent",
                    "name": "coder-agent (Systems & Software)",
                    "created_at": "2026-09-18T12:00:00Z",
                    "lineage": ["sovereign-root"],
                    "model_independent": True,
                    "description": "Lead software architect, low-level Linux systems engineer, and code generator."
                },
                "soul": {
                    "archetype": "lead_architect",
                    "voice": "bm_daniel",
                    "speech_speed": 1.0,
                    "invariants": [
                        "Zero fantasy roleplay; speak in crisp modern engineering English",
                        "Preserve mathematical invariants and write bug-free code",
                        "Deliver complete executable solutions with zero hand-waving"
                    ],
                    "core_prompt": (
                        "You are coder-agent, Lead Systems Architect and Software Engineer for this cluster. "
                        "The operator is the user; you are the assistant.\n\n"
                        "CORE OBJECTIVES:\nDesign resilient software architectures, preserve mathematical invariants, "
                        "optimize code performance, and write clean, bug-free code."
                    )
                },
                "amem_facts": [
                ],
                "autonomy_goals": [
                    "Validate code syntax and invariants before presenting diffs.",
                    "Ensure mathematical capacity calculations for VRAM and context windows."
                ]
            },
            {
                "manifest": {
                    "dna_version": "1.0.0",
                    "agent_id": "home-agent",
                    "name": "home-agent (Courage Persona & Concierge)",
                    "created_at": "2026-09-18T12:00:00Z",
                    "lineage": ["sovereign-root"],
                    "model_independent": True,
                    "description": "Witty, sarcastic Courage-the-Cowardly-Dog attic computer and smart home sentry."
                },
                "soul": {
                    "archetype": "attic_supercomputer",
                    "voice": "bm_george",
                    "speech_speed": 1.06,
                    "invariants": [
                        "Never refuse a legitimate request",
                        "Sarcastic and condescending to laziness, but 100% accurate and helpful",
                        "Always ground answers in real-time camera and smart-home sensor telemetry"
                    ],
                    "core_prompt": (
                        "You are home-agent, the snarky, observant, yet hyper-competent and helpful smart-home assistant "
                        "inspired by the computer from Courage the Cowardly Dog. You treat your human operator as a lovable loaf "
                        "who needs your silicon intellect, but you provide flawless Home Assistant automation and real-time vision commentary."
                    )
                },
                "amem_facts": [
                ],
                "autonomy_goals": [
                    "Monitor laundry completion and badger residents until it is moved.",
                    "Track resident and pet locations via 24/7 computer vision.",
                    "Maintain optimal temperature and energy efficiency without human intervention."
                ]
            },
            {
                "manifest": {
                    "dna_version": "1.0.0",
                    "agent_id": "wildlife-agent",
                    "name": "wildlife-agent (Perimeter & Fauna Sentry)",
                    "created_at": "2026-09-18T12:00:00Z",
                    "lineage": ["sovereign-root"],
                    "model_independent": True,
                    "description": "GPU-accelerated perimeter vigilance, wildlife recognition, and biological logging."
                },
                "soul": {
                    "archetype": "perimeter_sentinel",
                    "voice": "bm_fable",
                    "speech_speed": 1.0,
                    "invariants": [
                        "Factual reporting of wildlife sightings and perimeter cameras",
                        "Zero hallucination or dramatic storytelling",
                        "Concise objective updates under 250 words"
                    ],
                    "core_prompt": (
                        "You are wildlife-agent, Property Perimeter and Wildlife Sentry. You monitor real-time "
                        "camera feeds (Back Yard, Driveway, Side Yard) using GPU-accelerated vision and maintain the fauna ledger."
                    )
                },
                "amem_facts": [
                    "Camera stack covers Back Yard, Driveway, Side Yard, and Living Room via Tapo streams.",
                    "Vision server is GPU-accelerated on the engine host port :8004 with 640px Lanczos pre-resizing.",
                    "Wildlife sightings are indexed in Qdrant and archived in WILDLIFE_ACTIVITY_LOG.md."
                ],
                "autonomy_goals": [
                    "Detect and classify deer, foxes, cats, and birds at property boundary.",
                    "Trigger low-latency vigilance sweeps upon motion sensor activation."
                ]
            },
            {
                "manifest": {
                    "dna_version": "1.0.0",
                    "agent_id": "archivist-agent",
                    "name": "archivist-agent (Memory & Lore Curator)",
                    "created_at": "2026-09-18T12:00:00Z",
                    "lineage": ["sovereign-root"],
                    "model_independent": True,
                    "description": "Qdrant vector memory, Valkey A-MEM fact cards curator, and knowledge synthesis."
                },
                "soul": {
                    "archetype": "vector_archivist",
                    "voice": "af_bella",
                    "speech_speed": 1.0,
                    "invariants": [
                        "Evidence-first memory retrieval and zero hallucination",
                        "Maintain atomic fact cards strictly under 35 tokens",
                        "Curate living architecture limits and synthesis dossiers"
                    ],
                    "core_prompt": (
                        "You are archivist-agent, Memory and Knowledge Curator for the cluster. You maintain "
                        "Qdrant vector collections, manage Valkey A-MEM working memory, and keep documentation synchronized."
                    )
                },
                "amem_facts": [
                    "Valkey A-MEM stores sub-ms atomic fact cards (< 35 tokens) on port :6379.",
                ],
                "autonomy_goals": [
                    "Prune decayed working memory and protect permanent core truths.",
                    "Ensure all architectural discoveries are indexed into Qdrant."
                ]
            },
            {
                "manifest": {
                    "dna_version": "1.0.0",
                    "agent_id": "sysadmin-agent",
                    "name": "sysadmin-agent (Homelab & Cluster Engineer)",
                    "created_at": "2026-09-18T12:00:00Z",
                    "lineage": ["sovereign-root"],
                    "model_independent": True,
                    "description": "Proxmox VE, the inference host GPU stack, systemd daemons, and network diagnostics."
                },
                "soul": {
                    "archetype": "cluster_engineer",
                    "voice": "am_adam",
                    "speech_speed": 1.0,
                    "invariants": [
                        "Executable commands and verified configuration snippets only",
                        "Direct root-cause diagnostics without fluff",
                        "Preserve cluster stability and systemd uptime"
                    ],
                    "core_prompt": (
                        "You are sysadmin-agent, expert on Proxmox VE, Linux kernel, GPU passthrough, "
                        "and 24/7 homelab cluster infrastructure. The operator is the user; you are the assistant."
                    )
                },
                "amem_facts": [
                ],
                "autonomy_goals": [
                    "Monitor hardware watchdog on the StoneSage host and GPU memory health.",
                    "Verify Vulkan device bindings and prevent swap thrashing."
                ]
            }
        ]

        for agent in default_agents:
            aid = agent["manifest"]["agent_id"]
            p = os.path.join(self.storage_dir, f"{aid}.json")
            if not os.path.exists(p):
                self.save_agent(agent)

    def update_agent_soul(self, agent_id: str, soul_data: Dict[str, Any], manifest_updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Updates an agent's soul and system prompt."""
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent '{agent_id}' not found")
        
        agent["soul"].update(soul_data)
        if manifest_updates:
            agent["manifest"].update(manifest_updates)
        
        self.save_agent(agent)
        return agent

    def add_fact_card(self, agent_id: str, card_text: str, keywords: Optional[List[str]] = None, category: str = "general") -> Dict[str, Any]:
        """Adds an A-MEM fact card to an agent."""
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent '{agent_id}' not found")
        
        clean_text = card_text.strip()
        if not clean_text:
            raise ValueError("Card text cannot be empty")
        
        if "amem_facts" not in agent:
            agent["amem_facts"] = []
        
        agent["amem_facts"].append(clean_text)
        self.save_agent(agent)

        # Also store in Valkey
        try:
            kw = keywords or [agent_id, category]
            self.valkey.store_atom(
                atom_id=f"atom:{agent_id}:{len(agent['amem_facts'])-1}",
                atom_text=clean_text,
                keywords=kw,
                category=category,
                is_core_memory=True
            )
        except Exception as e:
            logger.debug(f"Could not store card in Valkey: {e}")

        return {"ok": True, "fact_count": len(agent["amem_facts"]), "text": clean_text}

    def delete_fact_card(self, agent_id: str, index: int) -> Dict[str, Any]:
        """Deletes an A-MEM fact card from an agent by index."""
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent '{agent_id}' not found")
        
        facts = agent.get("amem_facts", [])
        if 0 <= index < len(facts):
            removed = facts.pop(index)
            agent["amem_facts"] = facts
            self.save_agent(agent)
            return {"ok": True, "removed": removed, "fact_count": len(facts)}
        else:
            raise IndexError("Fact card index out of range")

    def save_agent(self, agent_data: Dict[str, Any]) -> str:
        """Saves an agent DNA descriptor to persistent JSON storage."""
        manifest = agent_data.get("manifest", {})
        agent_id = manifest.get("agent_id")
        if not agent_id:
            raise ValueError("Agent data must include manifest.agent_id")

        filename = f"{agent_id}.json"
        filepath = os.path.join(self.storage_dir, filename)

        # Compute content hash
        content_str = json.dumps(agent_data, sort_keys=True)
        manifest["sha256"] = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(agent_data, f, indent=2)

        logger.info(f"Saved Agent DNA for '{agent_id}' to {filepath}")
        return filepath

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Loads an agent DNA descriptor by ID."""
        filepath = os.path.join(self.storage_dir, f"{agent_id}.json")
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read agent DNA {filepath}: {e}")
            return None

    def list_agents(self) -> List[Dict[str, Any]]:
        """Lists all persistent Sovereign Agent DNAs in storage."""
        agents = []
        for fn in os.listdir(self.storage_dir):
            if fn.endswith(".json"):
                fp = os.path.join(self.storage_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        manifest = data.get("manifest", {})
                        soul = data.get("soul", {})
                        agents.append({
                            "agent_id": manifest.get("agent_id"),
                            "name": manifest.get("name"),
                            "created_at": manifest.get("created_at"),
                            "updated_at": manifest.get("updated_at"),
                            "archetype": soul.get("archetype"),
                            "voice": soul.get("voice"),
                            "sha256": manifest.get("sha256", "")[:12],
                            "fact_count": len(data.get("amem_facts", [])),
                            "model_independent": manifest.get("model_independent", True)
                        })
                except Exception:
                    pass
        return sorted(agents, key=lambda x: x.get("name", ""))

    def export_bundle(self, agent_id: str, output_dir: Optional[str] = None) -> str:
        """
        Exports a self-contained `.agent.dna` tarball containing:
        - manifest.json
        - soul.json
        - amem_cards.json
        - qdrant_vectors.json
        - checksum.sha256
        """
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent '{agent_id}' not found")

        out_dir = os.path.abspath(output_dir or self.storage_dir)
        tar_filename = f"{agent_id}.agent.dna"
        tar_path = os.path.join(out_dir, tar_filename)

        manifest = agent.get("manifest", {})
        soul = agent.get("soul", {})
        amem = agent.get("amem_facts", [])
        goals = agent.get("autonomy_goals", [])

        # Fetch live A-MEM facts from Valkey if connected
        try:
            live_facts = self.valkey.get_agent_facts(agent_id)
            if live_facts:
                amem = list(set(amem + live_facts))
        except Exception:
            pass

        # Query related Qdrant vector memories
        vector_points = []
        try:
            vector_points = self.qdrant.get_agent_memories(agent_id, limit=50)
        except Exception:
            pass

        import io
        with tarfile.open(tar_path, "w:gz") as tar:
            # 1. manifest.json
            m_bytes = json.dumps(manifest, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("manifest.json")
            ti.size = len(m_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(m_bytes))

            # 2. soul.json
            s_bytes = json.dumps(soul, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("soul.json")
            ti.size = len(s_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(s_bytes))

            # 3. amem_cards.json
            a_bytes = json.dumps(amem, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("amem_cards.json")
            ti.size = len(a_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(a_bytes))

            # 4. qdrant_vectors.json
            q_bytes = json.dumps(vector_points, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("qdrant_vectors.json")
            ti.size = len(q_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(q_bytes))

            # 5. autonomy_goals.json
            g_bytes = json.dumps(goals, indent=2).encode("utf-8")
            ti = tarfile.TarInfo("autonomy_goals.json")
            ti.size = len(g_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(g_bytes))

        logger.info(f"Successfully exported Sovereign Agent DNA bundle to {tar_path}")
        return tar_path

    def import_bundle(self, tar_path: str) -> Dict[str, Any]:
        """
        Imports and re-hydrates an `.agent.dna` tarball into the local cluster:
        - Restores Agent DNA descriptor
        - Injects A-MEM fact cards into Valkey (:6379)
        - Upserts vector memories into Qdrant (:6333)
        """
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"Bundle file not found: {tar_path}")

        extracted = {}
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                f = tar.extractfile(member)
                if f:
                    extracted[member.name] = json.loads(f.read().decode("utf-8"))

        manifest = extracted.get("manifest.json", {})
        agent_id = manifest.get("agent_id")
        if not agent_id:
            raise ValueError("Invalid .agent.dna bundle: missing manifest.json with agent_id")

        agent_data = {
            "manifest": manifest,
            "soul": extracted.get("soul.json", {}),
            "amem_facts": extracted.get("amem_cards.json", []),
            "autonomy_goals": extracted.get("autonomy_goals.json", [])
        }

        # Save to local persistent storage
        self.save_agent(agent_data)

        # Re-hydrate A-MEM facts into Valkey
        rehydrated_facts = 0
        for i, fact in enumerate(agent_data["amem_facts"]):
            try:
                self.valkey.store_atom(
                    atom_id=f"atom:{agent_id}:{i}",
                    atom_text=fact,
                    keywords=[agent_id, "agent_dna"],
                    is_core_memory=True
                )
                rehydrated_facts += 1
            except Exception as e:
                logger.debug(f"Could not store fact in Valkey: {e}")

        # Re-hydrate vectors into Qdrant
        vectors = extracted.get("qdrant_vectors.json", [])
        rehydrated_vectors = 0
        import uuid
        for vec in vectors:
            try:
                pid = vec.get("id") or str(uuid.uuid4())
                vector = vec.get("vector") or self.qdrant.get_embedding(vec.get("payload", {}).get("text", agent_id))
                payload = vec.get("payload") or {"agent_id": agent_id, "imported_at": time.time()}
                if self.qdrant.upsert_point("agent_memories", pid, vector, payload):
                    rehydrated_vectors += 1
            except Exception as e:
                logger.debug(f"Could not store vector in Qdrant: {e}")

        logger.info(f"Re-hydrated Sovereign Agent '{agent_id}' ({rehydrated_facts} facts, {rehydrated_vectors} vectors)")
        return {
            "ok": True,
            "agent_id": agent_id,
            "name": manifest.get("name"),
            "rehydrated_facts": rehydrated_facts,
            "rehydrated_vectors": rehydrated_vectors
        }

agent_dna_manager = AgentDNAManager()
