"""
Tier 4: Human-First Obsidian Gateway.
Strictly isolates the Obsidian Vault from AI runtime clutter.
Enforces the 3-File Human Coordination Invariant:
  1. AI/CLUSTER_FLEET_STATUS.md
  2. AI/ACTIVE_PROJECTS_DIGEST.md
  3. AI/FRONTIER_VERIFIED_INSIGHTS.md
All raw agent contracts, session logs, and dossiers stay in server-side databases.
"""

import os
import time
import logging
from typing import Dict, Any, List, Optional

from ..config import fleet_config

logger = logging.getLogger("Harness.ObsidianGateway")

class ObsidianGateway:
    ALLOWED_HUMAN_FILES = {
        "CLUSTER_FLEET_STATUS.md",
        "ACTIVE_PROJECTS_DIGEST.md",
        "FRONTIER_VERIFIED_INSIGHTS.md",
    }

    def __init__(self, vault_root: Optional[str] = None):
        self.vault_root = vault_root or fleet_config.obsidian_vault()    # config / OBSIDIAN_VAULT(_PATH); "" = none
        self.ai_folder = os.path.join(self.vault_root, "AI")
        try:
            if os.path.exists(self.vault_root):
                os.makedirs(self.ai_folder, exist_ok=True)
        except Exception:
            pass

    def update_cluster_status(self, fleet_status: List[Dict[str, Any]]):
        """Updates the living AI/CLUSTER_FLEET_STATUS.md note for the human operator."""
        lines = [
            "# 🛰️ Homelab AI Cluster Fleet Status",
            "",
            f"> **Last Updated**: {time.strftime('%Y-%m-%d %H:%M:%S EST')}  ",
            f"> **Frontier Arbiter**: Antigravity (AGY / gemini-3.8-flash)  ",
            "",
            "## Active Compute Nodes",
            "| Node | Role | Model Loaded | Active Slots | Roaming Status |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for n in fleet_status:
            roam = "⚡ Roaming Edge" if n.get("is_roaming") else "🟢 Fixed Host"
            lines.append(
                f"| **{n['name']}** | `{n['role']}` | `{n['active_model']}` | "
                f"{n['occupied_slots']}/{n['total_slots']} | {roam} |"
            )
        self._write_allowed_file("CLUSTER_FLEET_STATUS.md", "\n".join(lines))

    def update_projects_digest(self, active_missions: List[Dict[str, Any]]):
        """Updates AI/ACTIVE_PROJECTS_DIGEST.md with concise summaries of running tasks."""
        lines = [
            "# 📋 Active AI Projects & Agent Missions",
            "",
            f"> **Last Updated**: {time.strftime('%Y-%m-%d %H:%M:%S EST')}  ",
            "",
            "## Ongoing Agent Tasks",
        ]
        if not active_missions:
            lines.append("*All agents currently idle in sleep consolidation.*")
        else:
            for m in active_missions:
                lines.append(f"### ⚡ {m.get('title', 'Mission')}")
                lines.append(f"- **Assigned Agent**: `{m.get('agent_id')}`")
                lines.append(f"- **Compute Node**: `{m.get('node')}`")
                lines.append(f"- **Progress**: {m.get('summary', 'In progress...')}\n")
        self._write_allowed_file("ACTIVE_PROJECTS_DIGEST.md", "\n".join(lines))

    def append_verified_insight(self, title: str, formal_invariant: str, source_node: str):
        """Appends an insight that has passed Tier-1 Frontier verification to AI/FRONTIER_VERIFIED_INSIGHTS.md."""
        entry = (
            f"\n### 💎 [{time.strftime('%Y-%m-%d')}] {title}\n"
            f"- **Verified Invariant**: {formal_invariant}\n"
            f"- **Origin**: `{source_node}` | **Audited By**: Antigravity (Tier-1 Frontier)\n"
        )
        file_path = os.path.join(self.ai_folder, "FRONTIER_VERIFIED_INSIGHTS.md")
        try:
            if not os.path.exists(file_path):
                header = "# 💎 Frontier-Verified Cognitive Invariants\n\nCurated architectural discoveries validated by Tier-1 Frontier audit.\n"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(header)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info(f"Appended verified insight '{title}' to Obsidian.")
        except Exception as e:
            logger.warning(f"Could not append to Obsidian verified insights: {e}")

    def write_arbitrary_file(self, filename: str, content: str) -> Dict[str, Any]:
        """Strictly blocks non-whitelisted files from being written to the vault."""
        base_name = os.path.basename(filename)
        if base_name not in self.ALLOWED_HUMAN_FILES:
            logger.error(f"🛑 [Obsidian Security] Blocked attempt to write non-curated file '{filename}' to vault!")
            return {"ok": False, "error": f"Obsidian Vault is strictly restricted to the 3 human coordination files, rejected: {filename}"}
        self._write_allowed_file(base_name, content)
        return {"ok": True, "path": os.path.join(self.ai_folder, base_name)}

    def update_cluster_fleet_status(self, status_dict: Dict[str, Any]) -> Dict[str, Any]:
        lines = [
            "# 🛰️ Homelab AI Cluster Fleet Status",
            "",
            f"> **Last Updated**: {time.strftime('%Y-%m-%d %H:%M:%S EST')}  ",
            f"> **Frontier Arbiter**: Antigravity (AGY / gemini-3.8-flash)  ",
            "",
            "## Fleet Telemetry",
        ]
        for k, v in status_dict.items():
            lines.append(f"- **{k}**: `{v}`")
        self._write_allowed_file("CLUSTER_FLEET_STATUS.md", "\n".join(lines))
        return {"ok": True}

    def update_active_projects_digest(self, missions: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.update_projects_digest(missions)
        return {"ok": True}

    def append_frontier_insight(self, insight_title: str, body: str, invariant_id: str) -> Dict[str, Any]:
        self.append_verified_insight(title=insight_title, formal_invariant=f"[{invariant_id}] {body}", source_node="Cluster Coordinator")
        return {"ok": True}

    def _write_allowed_file(self, filename: str, content: str):
        """Guards against writing any unauthorized files to the Obsidian vault."""
        if filename not in self.ALLOWED_HUMAN_FILES:
            logger.error(f"🛑 [Obsidian Security] Blocked attempt to write non-curated file '{filename}' to vault!")
            return
        os.makedirs(self.ai_folder, exist_ok=True)
        target = os.path.join(self.ai_folder, filename)
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"Updated human coordination note 'AI/{filename}' in Obsidian.")
        except Exception as e:
            logger.warning(f"Could not update Obsidian note 'AI/{filename}': {e}")

obsidian_gateway = ObsidianGateway()

