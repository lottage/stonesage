"""
Multi-Tier Sovereign Agent Backup & Disaster Recovery Fabric.
Secures Sovereign Agent DNA (.agent.dna) across three resilient tiers:
- Tier 1: Local NAS / Redundant Storage (/mnt/nas, local vault)
- Tier 2: CouchDB / Obsidian Document Sync (the CouchDB host :5984 & Obsidian Vault)
- Tier 3: Off-site Cloud Object Storage (Cloudflare R2 / AWS S3 / MinIO API)

Ensures users can carry forward their sovereign agents forever into the AGI future,
completely decoupled from model weights, servers, or hardware topologies.
"""

import os
import sys
import time
import json
import shutil
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from ..config import fleet_config
from .agent_dna import agent_dna_manager

logger = logging.getLogger("Harness.CloudBackupFabric")

# Backup directories
LOCAL_BACKUP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "backups", "agent_dna"))
OBSIDIAN_AGENT_DIR = os.path.join(fleet_config.obsidian_vault(), "Sovereign Agents") if fleet_config.obsidian_vault() else ""

# Cloud S3/R2 Configuration
S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "")
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "sovereign-agent-backups")
S3_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
S3_SECRET = os.environ.get("S3_SECRET_ACCESS_KEY", "")

class CloudBackupFabric:
    def __init__(self, local_backup_dir: str = LOCAL_BACKUP_DIR):
        self.local_dir = local_backup_dir
        os.makedirs(self.local_dir, exist_ok=True)
        if os.path.exists(os.path.dirname(OBSIDIAN_AGENT_DIR)):
            os.makedirs(OBSIDIAN_AGENT_DIR, exist_ok=True)

    def backup_to_local_tier(self, agent_id: str, bundle_path: str) -> Dict[str, Any]:
        """Tier 1: Copies .agent.dna bundle to local archive directory."""
        fn = os.path.basename(bundle_path)
        dest = os.path.join(self.local_dir, fn)
        try:
            shutil.copy2(bundle_path, dest)
            size_kb = round(os.path.getsize(dest) / 1024, 2)
            return {"ok": True, "tier": "local_tier", "path": dest, "size_kb": size_kb}
        except Exception as e:
            logger.error(f"Local backup failed: {e}")
            return {"ok": False, "tier": "local_tier", "error": str(e)}

    def backup_to_obsidian_tier(self, agent_id: str) -> Dict[str, Any]:
        """Tier 2: Generates a human-readable dossier in the Obsidian vault."""
        agent = agent_dna_manager.get_agent(agent_id)
        if not agent:
            return {"ok": False, "tier": "obsidian_tier", "error": "Agent not found"}

        manifest = agent.get("manifest", {})
        soul = agent.get("soul", {})
        amem = agent.get("amem_facts", [])
        goals = agent.get("autonomy_goals", [])

        md_content = (
            f"# Sovereign Agent DNA: {manifest.get('name', agent_id)}\n\n"
            f"- **Agent ID**: `{manifest.get('agent_id')}`\n"
            f"- **Created At**: {manifest.get('created_at')}\n"
            f"- **Updated At**: {manifest.get('updated_at')}\n"
            f"- **SHA-256 Fingerprint**: `{manifest.get('sha256', 'unknown')}`\n"
            f"- **Archetype**: `{soul.get('archetype')}`\n"
            f"- **Preferred Voice**: `{soul.get('voice')}` (@ {soul.get('speech_speed', 1.0)}x)\n\n"
            "## Invariants & Core Directives\n"
        )
        for inv in soul.get("invariants", []):
            md_content += f"- {inv}\n"

        md_content += "\n## Working Memory (A-MEM Fact Cards)\n"
        for fact in amem:
            md_content += f"- {fact}\n"

        md_content += "\n## Autonomy Goals & Rumination Scope\n"
        for g in goals:
            md_content += f"- {g}\n"

        target_file = os.path.join(OBSIDIAN_AGENT_DIR, f"{agent_id}.md")
        try:
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(md_content)
            return {"ok": True, "tier": "obsidian_tier", "path": target_file}
        except Exception as e:
            logger.warning(f"Obsidian sync failed: {e}")
            return {"ok": False, "tier": "obsidian_tier", "error": str(e)}

    def backup_to_cloud_tier(self, agent_id: str, bundle_path: str) -> Dict[str, Any]:
        """
        Tier 3: Uploads .agent.dna bundle to Cloudflare R2 / AWS S3 if credentials configured,
        or flags as staged for manual export / API download.
        """
        if not (S3_ENDPOINT and S3_KEY_ID and S3_SECRET):
            return {
                "ok": True,
                "tier": "cloud_tier",
                "status": "staged_for_export",
                "message": "Cloud S3/R2 credentials not set. DNA bundle staged in local archive for 1-click web download."
            }

        try:
            import boto3
            s3 = boto3.client(
                "s3",
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_KEY_ID,
                aws_secret_access_key=S3_SECRET
            )
            key = f"agent-dna/{os.path.basename(bundle_path)}"
            s3.upload_file(bundle_path, S3_BUCKET, key)
            return {"ok": True, "tier": "cloud_tier", "bucket": S3_BUCKET, "key": key}
        except Exception as e:
            logger.error(f"S3/R2 upload failed: {e}")
            return {"ok": False, "tier": "cloud_tier", "error": str(e)}

    def execute_full_backup(self, agent_id: str) -> Dict[str, Any]:
        """Executes backup across all 3 tiers for a given agent."""
        try:
            bundle_path = agent_dna_manager.export_bundle(agent_id)
        except Exception as e:
            return {"ok": False, "error": f"Failed to export bundle: {e}"}

        tier1 = self.backup_to_local_tier(agent_id, bundle_path)
        tier2 = self.backup_to_obsidian_tier(agent_id)
        tier3 = self.backup_to_cloud_tier(agent_id, bundle_path)

        return {
            "ok": True,
            "agent_id": agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tiers": {
                "tier1_local": tier1,
                "tier2_obsidian": tier2,
                "tier3_cloud": tier3
            }
        }

    def backup_all_agents(self) -> List[Dict[str, Any]]:
        """Backs up every registered Sovereign Agent across all tiers."""
        agents = agent_dna_manager.list_agents()
        results = []
        for a in agents:
            aid = a.get("agent_id")
            if aid:
                results.append(self.execute_full_backup(aid))
        return results

cloud_backup_fabric = CloudBackupFabric()
