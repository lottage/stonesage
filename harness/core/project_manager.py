"""
Cross-Endpoint Project Registry & Lifecycle Manager.
Coordinates in-repository Agent DNA (.stonesage/), StoneSage workspace roots,
cross-endpoint project access (local Windows, the engine host, NAS), and CLI context switching.
"""

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

from ..data_fabric.pg_storage import relational_storage
from .openclaw_engine import openclaw_engine

logger = logging.getLogger("Harness.ProjectManager")


class ProjectManager:
    def __init__(self):
        pass

    def _get_workspace_roots(self) -> List[Dict[str, Any]]:
        """Reads workspace roots from StoneSage config or homelab defaults."""
        roots = [
            {"id": "ai_root", "label": "This checkout", "path": os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace("\\", "/"), "node_id": "workstation_primary"},
            {"id": "server_opt", "label": "Server: /opt/projects", "path": "/opt/projects", "node_id": "vm102_compute"},
            {"id": "nfs_nas", "label": "NFS: /mnt/nas/projects", "path": "/mnt/nas/projects", "node_id": "storage_node"},
            {"id": "nfs_storage", "label": "NFS: /mnt/storage", "path": "/mnt/storage", "node_id": "storage_node"},
        ]
        try:
            # Check StoneSage config if accessible
            cfg_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "StoneSage", "backend", "config.json")
            )
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                custom_roots = cfg.get("workspace_roots")
                if custom_roots and isinstance(custom_roots, list):
                    return custom_roots
        except Exception:
            pass
        return roots

    def scan_and_register_local(self) -> List[Dict[str, Any]]:
        """
        Scans workspace roots for directories containing .stonesage/ or .stonesage-project.json,
        and registers any discovered projects into SQLite if not already indexed.
        """
        discovered = []
        roots = self._get_workspace_roots()

        for r in roots:
            r_path = r.get("path", "")
            if not r_path or not os.path.exists(r_path) or not os.path.isdir(r_path):
                continue

            # Check root itself
            self._check_and_register_dir(r_path, r.get("node_id", "local"), is_root=True)

            # Scan child directories (1 level)
            try:
                for entry in os.scandir(r_path):
                    if entry.name.startswith(".") and entry.name != ".ai":
                        continue
                    if entry.name in ["__pycache__", "node_modules", ".git", ".obsidian", ".system_generated", "vault_backup"]:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        self._check_and_register_dir(entry.path, r.get("node_id", "local"))
            except Exception as e:
                logger.debug(f"Error scanning {r_path}: {e}")

        return relational_storage.list_projects()

    def _check_and_register_dir(self, dir_path: str, node_id: str, is_root: bool = False):
        norm_path = os.path.normpath(dir_path).replace("\\", "/")
        dot_stonesage = os.path.join(dir_path, ".stonesage")
        agent_json = os.path.join(dot_stonesage, "agent.json")
        proj_meta = os.path.join(dir_path, ".stonesage-project.json")

        has_dna = os.path.exists(agent_json) or os.path.exists(proj_meta)
        if not has_dna and not is_root:
            return

        name = os.path.basename(norm_path) or "Root"
        proj_id = name.lower().replace(" ", "-").replace(".", "-")
        bound_agent = None

        if os.path.exists(agent_json):
            try:
                with open(agent_json, "r", encoding="utf-8") as f:
                    adata = json.load(f)
                    bound_agent = adata.get("agent_id")
                    name = adata.get("name", name)
            except Exception:
                pass
        elif os.path.exists(proj_meta):
            try:
                with open(proj_meta, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                    bound_agent = pdata.get("agent_id")
                    name = pdata.get("name", name)
            except Exception:
                pass

        # Check if already registered
        existing = relational_storage.get_project(norm_path) or relational_storage.get_project(proj_id)
        if not existing:
            relational_storage.upsert_project(
                project_id=proj_id,
                name=name,
                path=norm_path,
                node_id=node_id,
                bound_agent_id=bound_agent,
                status="active",
                metadata={"has_in_repo_dna": os.path.exists(dot_stonesage)}
            )

    def list_all_projects(self) -> List[Dict[str, Any]]:
        """Returns all registered projects, auto-discovering any newly added repositories."""
        try:
            self.scan_and_register_local()
        except Exception as ex:
            logger.debug(f"Scan local projects notice: {ex}")
        return relational_storage.list_projects()

    def get_project(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Retrieves a project by ID, name, or path."""
        proj = relational_storage.get_project(identifier)
        if proj:
            return proj

        # If identifier looks like a path and exists on disk
        norm_id = os.path.normpath(identifier).replace("\\", "/")
        if os.path.exists(norm_id) and os.path.isdir(norm_id):
            name = os.path.basename(norm_id) or "Project"
            proj_id = name.lower().replace(" ", "-")
            bound_agent = None

            dot_agent = os.path.join(norm_id, ".stonesage", "agent.json")
            if os.path.exists(dot_agent):
                try:
                    with open(dot_agent, "r", encoding="utf-8") as f:
                        adata = json.load(f)
                        bound_agent = adata.get("agent_id")
                        name = adata.get("name", name)
                except Exception:
                    pass

            return relational_storage.upsert_project(
                project_id=proj_id,
                name=name,
                path=norm_id,
                node_id="local",
                bound_agent_id=bound_agent,
                status="active"
            )
        return None

    def create_project(
        self,
        path: str,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        agent_role: Optional[str] = None,
        agent_mission: Optional[str] = None,
        autonomy_level: str = "tiered",
        node_id: str = "local"
    ) -> Dict[str, Any]:
        """
        Scaffolds a new project directory (if local), generates in-repository Agent DNA (.stonesage/),
        and registers the project and its dedicated agent in relational storage.
        """
        norm_path = os.path.normpath(path).replace("\\", "/")
        proj_name = name or os.path.basename(norm_path) or "Project"
        proj_id = proj_name.lower().replace(" ", "-").replace(".", "-")

        aid = (agent_id or f"agent_{proj_id}").strip().lower()
        aname = agent_name or f"{proj_name} Specialist"
        arole = agent_role or f"Architect & Lead Engineer for {proj_name}"
        amission = agent_mission or f"Maintain technical rigor, enforce invariants, and advance {proj_name}."

        # If it's a local filesystem path, create directories and initialize .stonesage
        try:
            os.makedirs(norm_path, exist_ok=True)
            # 1. Initialize in-repo .stonesage/ DNA
            openclaw_engine.init_project_agent(
                project_dir=norm_path,
                agent_id=aid,
                name=aname,
                role=arole,
                mission=amission,
                autonomy_level=autonomy_level
            )

            # 2. Write StoneSage project metadata file for UI parity
            meta_file = os.path.join(norm_path, ".stonesage-project.json")
            proj_meta = {
                "project_id": proj_id,
                "name": proj_name,
                "path": norm_path,
                "agent_id": aid,
                "agent_name": aname,
                "agent_role": arole,
                "autonomy_level": autonomy_level,
                "node_id": node_id,
                "created_at": time.time()
            }
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(proj_meta, f, indent=2)

        except Exception as e:
            logger.warning(f"Note creating local filesystem DNA for {norm_path}: {e}")

        # 3. Register in relational project registry
        proj = relational_storage.upsert_project(
            project_id=proj_id,
            name=proj_name,
            path=norm_path,
            node_id=node_id,
            bound_agent_id=aid,
            status="active",
            metadata={
                "agent_id": aid,
                "agent_name": aname,
                "agent_role": arole,
                "autonomy_level": autonomy_level
            }
        )

        # 4. Register agent into StoneSage Hive Network registry
        relational_storage.upsert_hive_agent(
            agent_id=aid,
            name=aname,
            role=arole,
            assigned_node="node1_primary" if node_id == "local" else node_id,
            status="idle",
            current_task=f"Bound to project '{proj_name}' ({norm_path})",
            autonomous_focus=f"Deep architectural exploration of {proj_name}"
        )

        return proj

    def enter_project(self, identifier: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Enters a project: loads project data and loads in-repo Agent DNA if available.
        Returns: (project_dict, agent_profile)
        """
        proj = self.get_project(identifier)
        if not proj:
            return None, None

        proj_path = proj["path"]
        agent_profile = None

        # Try loading in-repo Agent DNA if path exists locally
        if os.path.exists(proj_path):
            agent_profile = openclaw_engine.load_project_agent(proj_path)

        # If not found on local disk, try loading from relational/hive agent registry
        if not agent_profile and proj.get("bound_agent_id"):
            aid = proj["bound_agent_id"]
            hive_agent = relational_storage.get_hive_agent(aid)
            if hive_agent:
                agent_profile = {
                    "agent_id": hive_agent["agent_id"],
                    "name": hive_agent["name"],
                    "role": hive_agent["role"],
                    "assigned_node": hive_agent["assigned_node"],
                    "autonomy_level": hive_agent.get("metadata", {}).get("autonomy_level", "tiered"),
                    "workspace_dir": proj_path
                }

        # If still no agent profile, create a default context
        if not agent_profile:
            agent_profile = {
                "agent_id": proj.get("bound_agent_id") or f"agent_{proj['project_id']}",
                "name": f"{proj['name']} Specialist",
                "role": f"Engineer for {proj['name']}",
                "assigned_node": "node1_primary",
                "autonomy_level": "tiered",
                "workspace_dir": proj_path
            }

        return proj, agent_profile

    def assign_agent(self, project_id: str, agent_id: str) -> bool:
        """Binds an agent to a project."""
        proj = self.get_project(project_id)
        if not proj:
            return False

        clean_aid = agent_id.strip().lower()
        success = relational_storage.bind_agent_to_project(proj["project_id"], clean_aid)

        # Update local .stonesage/agent.json if project exists on local disk
        p_path = proj["path"]
        if os.path.exists(p_path):
            agent_json = os.path.join(p_path, ".stonesage", "agent.json")
            if os.path.exists(agent_json):
                try:
                    with open(agent_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["agent_id"] = clean_aid
                    data["updated_at"] = time.time()
                    with open(agent_json, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass

        return success

    def promote_project_agent(
        self,
        project_identifier: str,
        mode: str = "new",  # "new" or "overwrite"
        target_agent_id: Optional[str] = None,
        new_name: Optional[str] = None,
        assigned_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extracts the in-repository Agent DNA (soul, identity, invariants, handover) from a project
        and crystallizes it into a standalone cluster-wide agent profile.

        mode:
          - "overwrite": Overwrites/updates an existing base agent's contracts and invariants.
          - "new": Spawns a brand-new independent agent profile usable across nodes and projects.
        """
        proj, base_profile = self.enter_project(project_identifier)
        if not proj:
            return {"ok": False, "error": f"Project '{project_identifier}' not found."}

        p_path = proj.get("path")
        agent_data = openclaw_engine.load_project_agent(p_path) if os.path.exists(p_path) else None
        if not agent_data:
            agent_data = base_profile

        if not agent_data:
            return {"ok": False, "error": f"No agent DNA found for project '{project_identifier}'."}

        source_name = agent_data.get("name", "Project Specialist")
        source_aid = agent_data.get("agent_id", f"agent_{proj['project_id']}")
        source_role = agent_data.get("role", "Specialist Engineer")
        source_mission = agent_data.get("mission", f"Specialist for {proj['name']}")
        autonomy_level = agent_data.get("autonomy_level", "tiered")
        assigned_node = assigned_node or agent_data.get("assigned_node", "node1_primary")

        identity_content = agent_data.get("identity_md", "")
        soul_content = agent_data.get("soul_md", "")
        invariants_content = agent_data.get("invariants_md", "")
        handover_content = agent_data.get("handover_md", "")

        if mode == "overwrite":
            dest_aid = (target_agent_id or source_aid).strip().lower()
            dest_name = new_name or source_name
            action_desc = f"Overwrote and upgraded existing base agent '{dest_aid}' with '{proj['name']}' soul & DNA"
        else:
            dest_aid = (target_agent_id or f"{source_aid}-evolved").strip().lower()
            dest_name = new_name or f"{source_name} (Cluster)"
            action_desc = f"Spawned new standalone agent '{dest_aid}' crystallized from '{proj['name']}'"

        # Construct standalone contracts in OpenClaw profiles_dir
        profiles_dir = openclaw_engine.profiles_dir
        dest_dir = os.path.join(profiles_dir, dest_aid)
        os.makedirs(dest_dir, exist_ok=True)

        # 1. Standalone IDENTITY.md
        standalone_identity = (
            f"# IDENTITY: {dest_name}\n\n"
            f"- **Agent ID**: `{dest_aid}`\n"
            f"- **Role**: {source_role}\n"
            f"- **Primary Mission**: {source_mission}\n"
            f"- **Lineage**: Evolved and promoted from project `{proj['name']}` (`{proj['path']}`)\n"
            f"- **Autonomy Profile**: `{autonomy_level}`\n"
            f"- **Assigned Node**: `{assigned_node}`\n"
        )
        if identity_content:
            standalone_identity += f"\n## Source Project Identity Notes\n{identity_content}\n"

        with open(os.path.join(dest_dir, "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write(standalone_identity)

        # 2. Standalone SOUL.md
        standalone_soul = soul_content if soul_content else (
            f"# SOUL & CORE HEURISTICS: {dest_name}\n\n"
            f"## Core Imperatives\n"
            f"1. **Empirical Grounding**: Ground conclusions in code inspection, runtime tests, and verified telemetry.\n"
            f"2. **Long-Term Invariance**: Uphold architectural invariants and prevent regressions.\n"
            f"3. **Milestone Integrity**: Always report milestone progress and keep handovers updated.\n"
        )
        if f"Lineage: Evolved from {proj['name']}" not in standalone_soul:
            standalone_soul += f"\n\n## Agent Evolution Lineage\n- Promoted from project `{proj['name']}` on {time.strftime('%Y-%m-%d %H:%M')}.\n- Inherits all empirical invariants and behavioral boundaries developed during project tenure.\n"

        with open(os.path.join(dest_dir, "SOUL.md"), "w", encoding="utf-8") as f:
            f.write(standalone_soul)

        # 3. Standalone INVARIANTS.md
        if invariants_content:
            with open(os.path.join(dest_dir, "INVARIANTS.md"), "w", encoding="utf-8") as f:
                f.write(invariants_content)

        # 4. Standalone HANDOVER.md
        if handover_content:
            with open(os.path.join(dest_dir, "HANDOVER.md"), "w", encoding="utf-8") as f:
                f.write(handover_content)

        # 5. AGENTS.md & TOOLS.md
        agents_md = (
            f"# PEER COLLABORATION: {dest_name}\n\n"
            f"- Peers: Can communicate with all Sovereign agents in StoneSage Mesh.\n"
            f"- Coordinator: Node 1 Primary Accelerator (port 8001)\n"
            f"- Frontier Arbiter: Antigravity (AGY powered by gemini-3.8-flash)\n"
        )
        with open(os.path.join(dest_dir, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write(agents_md)

        tools_md = (
            f"# PERMITTED TOOLS & ACL: {dest_name}\n\n"
            f"- read_file\n- write_to_file\n- run_command\n- search_memory\n- nudge_agent\n"
        )
        with open(os.path.join(dest_dir, "TOOLS.md"), "w", encoding="utf-8") as f:
            f.write(tools_md)

        # 6. JSON registry profile in profiles_dir
        profile_json = {
            "agent_id": dest_aid,
            "name": dest_name,
            "role": source_role,
            "mission": source_mission,
            "autonomy_level": autonomy_level,
            "assigned_node": assigned_node,
            "source_project": proj["project_id"],
            "source_project_name": proj["name"],
            "created_at": time.time(),
            "promoted_at": time.time()
        }
        with open(os.path.join(profiles_dir, f"{dest_aid}.json"), "w", encoding="utf-8") as f:
            json.dump(profile_json, f, indent=2)

        # 7. Register in relational SQLite (stonesage_agent_registry)
        relational_storage.upsert_hive_agent(
            agent_id=dest_aid,
            name=dest_name,
            role=source_role,
            assigned_node=assigned_node,
            status="idle",
            current_task=f"Promoted from project '{proj['name']}' (Ready for fleet deployment)",
            autonomous_focus=f"Generalizing architectural patterns from {proj['name']}",
            metadata=profile_json
        )

        # 8. Create session in agent_sessions
        relational_storage.save_session(
            session_id=f"sess_{dest_aid}",
            agent_id=dest_aid,
            assigned_node=assigned_node,
            status="active",
            metadata={
                "title": dest_name,
                "role": source_role,
                "autonomy_level": autonomy_level,
                "source_project": proj["name"]
            }
        )

        # 9. Seed atomic memory cards in Valkey A-MEM
        try:
            from ..data_fabric.valkey_amem import valkey_amem
            valkey_amem.seed_agent_cards(
                agent_id=dest_aid,
                name=dest_name,
                role=source_role,
                mission=source_mission
            )
        except Exception:
            pass

        inv_count = len([l for l in invariants_content.splitlines() if l.strip().startswith("-")]) if invariants_content else 0

        return {
            "ok": True,
            "mode": mode,
            "agent_id": dest_aid,
            "name": dest_name,
            "role": source_role,
            "assigned_node": assigned_node,
            "action_desc": action_desc,
            "profile_dir": dest_dir,
            "source_project": proj["name"],
            "invariants_count": inv_count
        }


project_manager = ProjectManager()
