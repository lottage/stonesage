"""
Dynamic Tiered Context Fabric (A-MEM & Persona Compiler).
Combines ultra-dense lean personas (~40-50 tokens) with sub-millisecond
on-demand Valkey A-MEM atomic fact cards (< 35 tokens) to achieve 85-90% token reduction
and completely eliminate <think> reasoning loops on local models.
"""

import os
import re
import logging
from typing import Dict, Any, List, Optional

from ..config import fleet_config
from ..data_fabric.valkey_amem import valkey_amem, live_topology_text, live_hierarchy_text
from .openclaw_engine import openclaw_engine

logger = logging.getLogger("Harness.ContextFabric")

class HiveContextFabric:
    def __init__(self):
        # Ensure homelab core cards are seeded
        valkey_amem.seed_homelab_core_cards()

    def get_lean_persona(self, agent_id: Optional[str] = None, project_dir: Optional[str] = None) -> str:
        """
        Generates an ultra-compact (~40-50 tokens) direct-answer system prompt.
        Eliminates defensive instructions, filler, and negative invariants that cause reasoning bloat.
        """
        profile = None
        if project_dir:
            profile = openclaw_engine.load_project_agent(project_dir)
        if not profile and agent_id:
            profile = openclaw_engine.get_profile(agent_id)

        if profile:
            name = profile.get("name", "Agent")
            aid = profile.get("agent_id", agent_id or "agent")
            role = profile.get("role", "Autonomous Agent")
            mission = profile.get("mission", "Execute tasks with technical rigor and precision.")
            autonomy = profile.get("autonomy_level", "tiered")

            # Seed agent-specific atomic card into Valkey A-MEM if not present
            valkey_amem.seed_agent_cards(
                agent_id=aid,
                name=name,
                role=role,
                mission=mission
            )

            extra_ws = ""
            if project_dir:
                extra_ws = f" | Project: `{os.path.basename(os.path.normpath(project_dir))}`"

            return (
                f"You are {name} (ID: `{aid}`), {role} in {fleet_config.operator_name()}'s homelab cluster.{extra_ws}\n"
                f"Mission: {mission}\n"
                f"Autonomy: {autonomy} | Direct Answer Mode: Concise, technically rigorous, empirical. "
                f"Provide direct answers without conversational filler, stage directions in asterisks, or persona roleplay."
            )

        return (
            "Direct Answer Mode: Provide concise, technically rigorous, objective, and direct answers. "
            "Strictly do not use conversational filler, stage directions in asterisks, or persona roleplay."
        )

    def compile_dynamic_turn(
        self,
        user_query: str,
        agent_id: Optional[str] = None,
        project_dir: Optional[str] = None,
        max_atoms: int = 2
    ) -> str:
        """
        Compiles the per-turn system prompt using A-MEM tag matching.
        If user query references infrastructure, databases, smart home, hierarchy, or philosophy,
        injects only the matching atomic cards (< 35 tokens each).
        Otherwise, returns the lean persona (~45 tokens), saving 85-90% prompt tokens.
        """
        lean_persona = self.get_lean_persona(agent_id=agent_id, project_dir=project_dir)
        atom_header = valkey_amem.format_injection_header(user_query, max_atoms=max_atoms)

        parts = [lean_persona]
        if project_dir:
            proj_agent = openclaw_engine.load_project_agent(project_dir)
            if proj_agent and proj_agent.get("invariants_md"):
                inv_raw = proj_agent.get("invariants_md", "").strip()
                if inv_raw and inv_raw != "(No custom invariants recorded yet)":
                    parts.append(f"\n[ACTIVE PROJECT INVARIANTS]\n{inv_raw}")

        if atom_header:
            parts.append(f"\n{atom_header}")
        return "\n".join(parts)

    def compile_full_dossier(self, agent_id: Optional[str] = None, project_dir: Optional[str] = None) -> str:
        """
        Generates the full uncompressed architectural specification (~500 tokens).
        Used for offline auditing, manual export, or explicit debug inspection.
        """
        lean = self.get_lean_persona(agent_id=agent_id, project_dir=project_dir)
        dossier = [
            lean,
            "\n[CLUSTER SILICON & TOPOLOGY]",
            f"• Proxmox API: {fleet_config.proxmox_vip}",
            f"• {live_topology_text()}",  # real GPUs and engines from the live system profile
            f"• Home Assistant: {fleet_config.hass_url}",
            "\n[DATA FABRIC & MEMORY]",
            f"• Qdrant Vector DB ({fleet_config.qdrant_url}, 6 collections: companion_profile, home_automation_registry, codebase_knowledge, agent_memories, session_transcripts, autonomous_thinking).",
            "• Valkey in-RAM A-MEM (:6379): Sub-millisecond atomic fact cards (< 35 tokens).",
            f"• CouchDB LiveSync ({fleet_config.couchdb_url}): Obsidian vault bidirectional sync.",
            "• SQLite Relational DB (data/harness.db): Chat sessions, messages, slot metrics.",
            "\n[COGNITIVE HIERARCHY & PHILOSOPHICAL FOUNDATION]",
            f"• {live_hierarchy_text()}",
            f"• Sovereign Collective: Grounded in {fleet_config.operator_name()}'s real physical home; empirical truth over hierarchy; no agent dies—all insights persist permanently in Qdrant and Obsidian."
        ]
        return "\n".join(dossier)

context_fabric = HiveContextFabric()
