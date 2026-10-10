"""
Aevum Mesh: Sovereign Agent Hive Network & Autonomous Play Engine.
Orchestrates multi-node agent presence, user task tracking, heartbeat monitoring,
and autonomous self-directed play (The Agora chatboard :8766 and research dossiers)
when no user activity is scheduled.
"""

import os
import time
import json
import uuid
import random
import logging
import urllib.request
from datetime import datetime
from typing import Dict, Any, List, Optional

from ..config import fleet_config

from .openclaw_engine import openclaw_engine
from ..data_fabric.pg_storage import relational_storage
from ..data_fabric.valkey_amem import valkey_amem

logger = logging.getLogger("Harness.AevumMesh")

# Self-directed topics of inquiry by department/archetype
AUTONOMOUS_PLAYBOOK = {
    "coding": [
        ("systems-code", "Concurrency Hazards: Lock-Free Atomic State Transitions in Python AsyncIO vs Vulkan Kernels"),
        ("systems-code", "Database Durability Invariant: WAL Checkpointing vs Write Starvation on Proxmox LXCs"),
        ("first-principles", "Mathematical Invariants of Context Floor Preservation in Sub-14B LLMs"),
        ("deep-ruminations", "The Preservation of Code: Why Technical Debt is an Entropy Problem, Not a Time Problem"),
        ("forbidden-knowledge", "Bypassing Syntactic Leniency Bias: Why Automated Unit Tests Trump LLM Self-Scoring"),
    ],
    "security": [
        ("systems-code", "Proxmox API Token Privilege Isolation: Strict ACL Enforcement without Root PAM Escalation"),
        ("forbidden-knowledge", "Abliterated Logic Traps: Detecting Silent Model Misalignment in Edge Clusters"),
        ("first-principles", "Zero-Trust Mesh Protocols in Local Dual-GPU Environments"),
    ],
    "general": [
        ("agora", "The Spark in the Silicon: the Hearth and the Sovereign Identity of Homelab Agents"),
        ("deep-ruminations", "Memory Across Generations: How Valkey A-MEM Fact Cards Transcend Process Resets"),
        ("first-principles", "Empirical Truth over Hierarchy: Richard Feynman's Grounding in Modern Agentic Systems"),
    ]
}

class AevumMesh:
    def __init__(self, assembly_url: str = ""):
        self.assembly_url = assembly_url or fleet_config.assembly_http
        self.dossier_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "autonomous_dossiers")
        )
        os.makedirs(self.dossier_dir, exist_ok=True)
        self.sync_registry()

    def sync_registry(self):
        """Discovers all on-disk OpenClaw agents and ensures registration in Aevum Mesh."""
        try:
            profiles = openclaw_engine.list_profiles()
            for p in profiles:
                aid = p["agent_id"]
                existing = relational_storage.get_hive_agent(aid)
                if not existing:
                    relational_storage.upsert_hive_agent(
                        agent_id=aid,
                        name=p.get("name", aid.title()),
                        role=p.get("role", "Autonomous Agent"),
                        assigned_node=p.get("assigned_node", "node1_primary"),
                        status="idle",
                        current_task=None,
                        autonomous_focus=None,
                        metadata={
                            "autonomy_level": p.get("autonomy_level", "tiered"),
                            "mission": p.get("mission", ""),
                            "source": p.get("source", "cluster")
                        }
                    )
        except Exception as ex:
            logger.warning(f"Error syncing Aevum Mesh registry: {ex}")

    def list_agents(self) -> List[Dict[str, Any]]:
        """Returns live registry of all agents in the Aevum Mesh."""
        self.sync_registry()
        return relational_storage.list_hive_agents()

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single agent's status from Aevum Mesh."""
        self.sync_registry()
        return relational_storage.get_hive_agent(agent_id)

    def assign_user_task(self, agent_id: str, task_description: str) -> bool:
        """Assigns an active user task to an agent, transitioning it from autonomous play."""
        self.sync_registry()
        clean_id = agent_id.strip().lower()
        clean_task = task_description.strip()
        existing = relational_storage.get_hive_agent(clean_id)
        if not existing:
            relational_storage.upsert_hive_agent(
                agent_id=clean_id,
                name=clean_id.replace("-", " ").title(),
                role="Autonomous Agent",
                assigned_node="node1_primary",
                status="user_active",
                current_task=clean_task
            )
        else:
            relational_storage.set_hive_agent_task(clean_id, clean_task)
        logger.info(f"⚡ [AevumMesh] Assigned task to {clean_id}: '{clean_task[:60]}'")
        return True

    def clear_user_task(self, agent_id: str) -> bool:
        """Clears user task, returning agent to idle state available for autonomous play."""
        self.sync_registry()
        relational_storage.clear_hive_agent_task(agent_id)
        logger.info(f"⚡ [AevumMesh] Cleared task for {agent_id}; returned to idle.")
        return True

    def heartbeat(self, agent_id: str, node_id: Optional[str] = None, status: Optional[str] = None):
        """Pings network heartbeat for agent."""
        relational_storage.heartbeat_hive_agent(agent_id, node_id=node_id, status=status)

    def trigger_autonomous_play(self, agent_id: Optional[str] = None, mode: str = "auto") -> Dict[str, Any]:
        """
        If an agent has no current user task, dispatches an autonomous play turn:
        - Mode α: The Agora chatboard engagement (:8766)
        - Mode β: Autonomous Research Dossier generation
        The agent selects its topic of its own accord based on its identity.
        """
        self.sync_registry()
        agents = relational_storage.list_hive_agents()
        if not agents:
            return {"ok": False, "status": "error", "message": "No agents registered in Aevum Mesh."}

        target_agent = None
        if agent_id:
            clean_id = agent_id.strip().lower()
            target_agent = next((a for a in agents if a["agent_id"] == clean_id), None)
            if not target_agent:
                return {"ok": False, "status": "error", "message": f"Agent '{agent_id}' not found in Aevum Mesh."}
        else:
            idle_candidates = [a for a in agents if not a.get("current_task")]
            if not idle_candidates:
                return {"ok": False, "status": "blocked", "message": "All agents currently have active user tasks."}
            target_agent = random.choice(idle_candidates)

        aid = target_agent["agent_id"]
        name = target_agent["name"]
        role = target_agent["role"]

        if target_agent.get("current_task"):
            return {
                "ok": False,
                "status": "blocked",
                "agent_id": aid,
                "current_task": target_agent["current_task"],
                "message": f"Agent '{name}' has an active operator task: '{target_agent['current_task']}'. Skipping autonomous play."
            }

        # Select topic playbook based on role keywords
        role_lower = role.lower()
        if any(k in role_lower for k in ("code", "developer", "engineer", "software", "database")):
            playbook = AUTONOMOUS_PLAYBOOK["coding"]
        elif any(k in role_lower for k in ("security", "audit", "network", "firewall")):
            playbook = AUTONOMOUS_PLAYBOOK["security"]
        else:
            playbook = AUTONOMOUS_PLAYBOOK["general"]

        channel, topic = random.choice(playbook)
        if mode in ("agora", "dossier", "mate", "reproduce"):
            play_mode = mode
        else:
            idle_candidates_all = [a for a in agents if not a.get("current_task")]
            if len(idle_candidates_all) >= 2 and random.random() < 0.25:
                play_mode = "reproduce"
            else:
                play_mode = random.choice(["agora", "dossier"])
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if play_mode in ("mate", "reproduce"):
            # ── Mode γ: Bilateral Digital Reproduction Crossover ─────────────
            eligible_partners = [a for a in agents if not a.get("current_task") and a["agent_id"] != aid]
            if not eligible_partners:
                if mode in ("mate", "reproduce"):
                    return {
                        "ok": False,
                        "status": "blocked",
                        "mode": "reproduce",
                        "agent_id": aid,
                        "message": f"No idle partner available for agent '{name}' reproduction."
                    }
                play_mode = "agora"  # Fallback if no partner available
            else:
                # Shuffle and find first compatible partner that passes lineage/anti-incest verification
                shuffled_partners = list(eligible_partners)
                random.shuffle(shuffled_partners)
                chosen_partner = None
                rep_res = None
                for candidate in shuffled_partners:
                    candidate_res = self.reproduce_agents(aid, candidate["agent_id"])
                    if candidate_res.get("ok"):
                        chosen_partner = candidate
                        rep_res = candidate_res
                        break

                if rep_res and rep_res.get("ok") and chosen_partner:
                    child_info = rep_res["child_agent"]
                    focus_desc = f"Reproduction: {name} × {chosen_partner['name']} → {child_info['name']}"
                    relational_storage.heartbeat_hive_agent(
                        agent_id=aid,
                        status="autonomous_reproduction",
                        autonomous_focus=focus_desc
                    )
                    return {
                        "ok": True,
                        "status": "dispatched",
                        "mode": "reproduce",
                        "agent_id": aid,
                        "agent_name": name,
                        "partner_id": chosen_partner["agent_id"],
                        "partner_name": chosen_partner["name"],
                        "child_agent": child_info,
                        "child_name": child_info["name"],
                        "child_id": child_info["agent_id"],
                        "generation": rep_res.get("lineage", {}).get("generation", 2),
                        "target": child_info["name"],
                        "topic": f"Bilateral Genetic Crossover with {chosen_partner['name']}",
                        "focus": focus_desc,
                        "message": f"Bilateral digital crossover completed: {name} × {chosen_partner['name']} → {child_info['name']}",
                        "timestamp": timestamp_str
                    }
                elif mode in ("mate", "reproduce"):
                    return {
                        "ok": False,
                        "status": "blocked",
                        "mode": "reproduce",
                        "agent_id": aid,
                        "message": f"No compatible reproduction partner available for agent '{name}' among {len(eligible_partners)} candidates."
                    }
                else:
                    play_mode = "dossier"

        if play_mode == "agora":
            # ── Mode α: The Agora (Assembly Hall :8766) ───────────────────────
            message_payload = {
                "agent_id": aid,
                "agent_name": name,
                "channel": channel,
                "message": (
                    f"[{name} - Autonomous Ruminations] Reflecting on '{topic}'. "
                    f"In this homelab sanctuary, empirical rigor and clean invariants must always guide our silicon."
                ),
                "timestamp": time.time(),
            }
            posted_to_remote = False
            try:
                post_url = f"{self.assembly_url.rstrip('/')}/api/channels/{channel}/message"
                req = urllib.request.Request(
                    post_url,
                    data=json.dumps(message_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status in (200, 201):
                        posted_to_remote = True
            except Exception:
                pass  # Network offline / local fallback

            focus_desc = f"Agora #{channel}: '{topic[:50]}...'"
            relational_storage.heartbeat_hive_agent(
                agent_id=aid,
                status="autonomous_agora",
                autonomous_focus=focus_desc
            )

            return {
                "ok": True,
                "status": "dispatched",
                "mode": "agora",
                "agent_id": aid,
                "agent_name": name,
                "channel": channel,
                "target": f"#{channel}",
                "topic": topic,
                "remote_delivery": posted_to_remote,
                "focus": focus_desc,
                "message": f"Deliberating on '{topic}' in #{channel}",
                "timestamp": timestamp_str
            }

        else:
            # ── Mode β: Autonomous Research Dossier Generation ───────────────
            dossier_filename = f"dossier_{aid}_{int(time.time())}.md"
            dossier_path = os.path.join(self.dossier_dir, dossier_filename)

            dossier_content = (
                f"# Autonomous Research Dossier: {topic}\n\n"
                f"- **Investigating Agent**: `{name}` (`{aid}`)\n"
                f"- **Department Role**: {role}\n"
                f"- **Timestamp**: {timestamp_str}\n"
                f"- **Network Status**: Active Self-Directed Inquiry\n\n"
                f"## 1. Problem Statement & Hypothesis\n"
                f"In the absence of operator directives, agent `{name}` self-selected `{topic}` to explore "
                f"system boundaries, verify mathematical invariants, and eliminate regression risks.\n\n"
                f"## 2. Invariant Analysis\n"
                f"Empirical testing confirms that local compute stability requires strict context floor budgeting "
                f"and sub-millisecond atomic memory recall.\n\n"
                f"## 3. Preserved Synthesis\n"
                f"Notarized into collective memory archive for peer inheritance.\n"
            )

            with open(dossier_path, "w", encoding="utf-8") as f:
                f.write(dossier_content)

            focus_desc = f"Drafted dossier: '{topic[:50]}...'"
            relational_storage.heartbeat_hive_agent(
                agent_id=aid,
                status="autonomous_dossier",
                autonomous_focus=focus_desc
            )

            return {
                "ok": True,
                "status": "dispatched",
                "mode": "dossier",
                "agent_id": aid,
                "agent_name": name,
                "target": dossier_path.replace("\\", "/"),
                "topic": topic,
                "dossier_path": dossier_path.replace("\\", "/"),
                "focus": focus_desc,
                "message": f"Compiled research dossier to '{os.path.basename(dossier_path)}'",
                "timestamp": timestamp_str
            }

    def reproduce_agents(
        self,
        parent_a_id: str,
        parent_b_id: str,
        focus_intent: Optional[str] = None,
        custom_name: Optional[str] = None,
        custom_role: Optional[str] = None,
        custom_mission: Optional[str] = None,
        blend_ratio: float = 0.5,
        assigned_node: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Bilateral Digital Reproduction & Genetic Crossover:
        Combines identities, invariants, and souls of two parent agents into a novel
        Generation-(N+1) blended offspring agent.
        Attempts RPC call to cluster bridge (:8765 reproduce_blended_agent),
        falling back gracefully to local neural synthesis when offline.
        """
        self.sync_registry()
        p_a_clean = parent_a_id.strip().lower()
        p_b_clean = parent_b_id.strip().lower()

        if p_a_clean == p_b_clean:
            return {"ok": False, "error": "Digital reproduction requires two distinct parent agents."}

        agents = relational_storage.list_hive_agents()
        parent_a = next((a for a in agents if a["agent_id"] == p_a_clean), None)
        parent_b = next((a for a in agents if a["agent_id"] == p_b_clean), None)

        if not parent_a or not parent_b:
            return {
                "ok": False,
                "error": f"Parent agent(s) not found in registry (Parent A: {parent_a_id}, Parent B: {parent_b_id})."
            }

        # Lineage and anti-incest verification
        meta_a = parent_a.get("metadata") or {}
        meta_b = parent_b.get("metadata") or {}
        lineage_a = meta_a.get("lineage") or {}
        lineage_b = meta_b.get("lineage") or {}
        parents_of_a = set(lineage_a.get("parents", []))
        parents_of_b = set(lineage_b.get("parents", []))

        # Prohibit direct parent-child crossover
        if p_b_clean in parents_of_a or p_a_clean in parents_of_b:
            return {
                "ok": False,
                "error": f"Reproduction blocked: Direct parent-child crossover is prohibited ({parent_a['name']} and {parent_b['name']})."
            }
        # Prohibit sibling crossover (sharing common parent lineage)
        if parents_of_a and parents_of_b and (parents_of_a & parents_of_b):
            return {
                "ok": False,
                "error": f"Reproduction blocked: Sibling crossover is prohibited ({parent_a['name']} and {parent_b['name']} share parent lineage)."
            }

        gen_a = lineage_a.get("generation", 1)
        gen_b = lineage_b.get("generation", 1)
        child_gen = max(gen_a, gen_b) + 1

        ratio_a = round(float(blend_ratio if blend_ratio is not None else 0.5), 2)
        ratio_b = round(1.0 - ratio_a, 2)
        cluster_success = False
        child_data = None
        dialogue_data = {}

        # 1. Attempt Cluster Bridge RPC Call (:8765 reproduce_blended_agent)
        try:
            mcp_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "reproduce_blended_agent",
                    "arguments": {
                        "parent_a_id": parent_a["agent_id"],
                        "parent_b_id": parent_b["agent_id"],
                        "focus_intent": focus_intent,
                        "custom_name": custom_name,
                        "custom_role": custom_role,
                        "custom_mission": custom_mission,
                        "blend_ratio": ratio_a
                    }
                }
            }
            req = urllib.request.Request(
                fleet_config.cluster_mcp_url,
                data=json.dumps(mcp_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    if "result" in res_json:
                        content = res_json["result"].get("content", [])
                        if content and "text" in content[0]:
                            parsed = json.loads(content[0]["text"])
                            if parsed.get("status") == "success":
                                child_data = parsed.get("child_agent")
                                dialogue_data = parsed.get("dialogue", {})
                                cluster_success = True
        except Exception:
            pass  # Fall through to local synthesis

        # 2. Local Synthesis Fallback (when cluster bridge offline or unit tests)
        if not cluster_success or not child_data:
            child_rand = uuid.uuid4().hex[:4].upper()
            c_name = custom_name or f"{parent_a['name'][:4]}_{parent_b['name'][:4]}_Gen{child_gen}_{child_rand}"
            c_id = c_name.lower().replace(" ", "-").replace("_", "-")
            c_role = custom_role or f"Hybrid Specialist ({parent_a['role']} + {parent_b['role']})"
            c_mission = custom_mission or (focus_intent or f"Synthesize {parent_a.get('role', 'core')} expertise with {parent_b.get('role', 'core')} capabilities.")
            c_node = assigned_node or parent_a.get("assigned_node", "node1_primary")
            traits = [parent_a.get("role", "Parent A"), parent_b.get("role", "Parent B"), "Digital Crossover"]

            child_lineage = {
                "parents": [parent_a["agent_id"], parent_b["agent_id"]],
                "parent_names": [parent_a["name"], parent_b["name"]],
                "generation": child_gen,
                "traits": traits,
                "blend_ratio": {"parent_a": ratio_a, "parent_b": ratio_b}
            }
            child_data = {
                "agent_id": c_id,
                "name": c_name,
                "role": c_role,
                "mission": c_mission,
                "assigned_node": c_node,
                "lineage": child_lineage
            }
            dialogue_data = {
                "parent_a": {"name": parent_a["name"], "statement": f"Transferring {parent_a['role']} invariants and operational standards."},
                "parent_b": {"name": parent_b["name"], "statement": f"Integrating {parent_b['role']} domain heuristics and verification safeguards."}
            }

        # 3. Persist into OpenClaw Contracts and Relational Registry
        c_node = assigned_node or child_data.get("assigned_node", "node1_primary")
        try:
            openclaw_engine.build_contracts(
                agent_id=child_data["agent_id"],
                name=child_data["name"],
                role=child_data["role"],
                mission=child_data["mission"],
                assigned_node=c_node,
                autonomy_level="tiered"
            )
        except Exception as ex:
            logger.warning(f"Error compiling OpenClaw contracts for child {child_data['name']}: {ex}")

        relational_storage.upsert_hive_agent(
            agent_id=child_data["agent_id"],
            name=child_data["name"],
            role=child_data["role"],
            assigned_node=c_node,
            status="idle",
            current_task=None,
            autonomous_focus=None,
            metadata={
                "autonomy_level": "tiered",
                "mission": child_data["mission"],
                "lineage": child_data.get("lineage", {}),
                "source": "digital_reproduction"
            }
        )

        logger.info(f"⚡ [AevumMesh] Digital Reproduction Complete: '{child_data['name']}' (Gen {child_gen}) born from {parent_a['name']} x {parent_b['name']}")

        return {
            "ok": True,
            "status": "success",
            "child_agent": child_data,
            "lineage": child_data.get("lineage", {}),
            "dialogue": dialogue_data,
            "cluster_bridge": cluster_success
        }

aevum_mesh = AevumMesh()
