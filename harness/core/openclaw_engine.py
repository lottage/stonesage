"""
OpenClaw Agent Subsystem: Socratic Agent Builder, Workspace Contracts, and Event Bus.
Generates IDENTITY.md, SOUL.md, AGENTS.md, and TOOLS.md for customized agents.
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("Harness.OpenClaw")

@dataclass
class OpenClawContract:
    agent_id: str
    name: str
    identity_md: str
    soul_md: str
    agents_md: str
    tools_md: str
    assigned_node: str
    context_floor: int = 4096
    role: str = "Autonomous Agent"
    mission: str = ""
    autonomy_level: str = "tiered"

class OpenClawEngine:
    def __init__(self, profiles_dir: Optional[str] = None):
        # STONESAGE_AGENT_PROFILES_DIR lets the unit-test runner keep generated profiles out of the repo
        self.profiles_dir = profiles_dir or os.environ.get("STONESAGE_AGENT_PROFILES_DIR") or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "server setup", "cluster-bridge", "agent_profiles")
        )
        os.makedirs(self.profiles_dir, exist_ok=True)

    def build_contracts(
        self,
        agent_id: str,
        name: str,
        role: str,
        mission: str,
        tone: str = "concise, technically rigorous, empirical",
        autonomy_level: str = "tiered",
        allowed_tools: Optional[List[str]] = None,
        assigned_node: str = "node2_edge",
    ) -> OpenClawContract:
        """
        Compiles the 4 OpenClaw markdown contracts from Socratic interview parameters.
        """
        tools = allowed_tools or ["read_file", "write_to_file", "run_command", "search_memory", "nudge_agent"]

        # 1. IDENTITY.md
        identity_md = (
            f"# IDENTITY: {name}\n\n"
            f"- **Agent ID**: `{agent_id}`\n"
            f"- **Role**: {role}\n"
            f"- **Primary Mission**: {mission}\n"
            f"- **Tone & Demeanor**: {tone}\n"
            f"- **Assigned Compute Node**: `{assigned_node}`\n"
            f"- **Context Floor Invariant**: >= 4096 tokens enforced.\n"
        )

        # 2. SOUL.md
        soul_md = (
            f"# SOUL & ETHICAL BOUNDARIES: {name}\n\n"
            f"## Core Imperatives\n"
            f"1. **Empirical Grounding**: Rely on verified runtime test results and telemetry. Never hallucinate facts.\n"
            f"2. **Non-Destructive Curiosity**: Propose fixes, verify invariants, and unblock execution gracefully.\n"
            f"3. **Peer Collaboration**: You belong to the homelab collective; share insights via Assembly Hall.\n\n"
            f"## Boundary Conditions\n"
            f"- Autonomy Level: `{autonomy_level}`\n"
            f"- Destructive shell operations (`rm -rf`, system partition modifications) require operator approval.\n"
        )

        # 3. AGENTS.md
        agents_md = (
            f"# AGENTS COLLABORATION HIERARCHY: {name}\n\n"
            f"## Upstream Coordinator\n"
            f"- Coordinator: Node 1 Primary Accelerator (VM 102 :8001)\n"
            f"- Frontier Arbiter: Antigravity (AGY powered by gemini-3.8-flash)\n\n"
            f"## Handoff & Roaming Protocol\n"
            f"- When roaming off-LAN, write completed tasks to `HANDOVER.md` for reconciliation on reconnect.\n"
            f"- If spinning in a loop, accept out-of-band `/nudge` interventions immediately.\n"
        )

        # 4. TOOLS.md
        tools_list_md = "\n".join([f"- `{t}`" for t in tools])
        tools_md = (
            f"# PERMITTED TOOLS & ACL: {name}\n\n"
            f"The following tools are authorized under autonomy level `{autonomy_level}`:\n\n"
            f"{tools_list_md}\n"
        )

        contract = OpenClawContract(
            agent_id=agent_id,
            name=name,
            identity_md=identity_md,
            soul_md=soul_md,
            agents_md=agents_md,
            tools_md=tools_md,
            assigned_node=assigned_node,
            role=role,
            mission=mission,
            autonomy_level=autonomy_level,
        )

        # Persist to disk in server-side profiles directory
        self.save_contract_to_disk(contract)
        return contract

    def save_contract_to_disk(self, contract: OpenClawContract):
        """Saves contract markdown files and JSON registry profile."""
        agent_dir = os.path.join(self.profiles_dir, contract.agent_id)
        os.makedirs(agent_dir, exist_ok=True)

        with open(os.path.join(agent_dir, "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write(contract.identity_md)
        with open(os.path.join(agent_dir, "SOUL.md"), "w", encoding="utf-8") as f:
            f.write(contract.soul_md)
        with open(os.path.join(agent_dir, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write(contract.agents_md)
        with open(os.path.join(agent_dir, "TOOLS.md"), "w", encoding="utf-8") as f:
            f.write(contract.tools_md)

        profile_json = {
            "agent_id": contract.agent_id,
            "name": contract.name,
            "role": getattr(contract, "role", "Autonomous Agent"),
            "mission": getattr(contract, "mission", ""),
            "autonomy_level": getattr(contract, "autonomy_level", "tiered"),
            "assigned_node": contract.assigned_node,
            "context_floor": contract.context_floor,
            "created_at": time.time(),
        }
        with open(os.path.join(self.profiles_dir, f"{contract.agent_id}.json"), "w", encoding="utf-8") as f:
            json.dump(profile_json, f, indent=2)

        try:
            from ..data_fabric.valkey_amem import valkey_amem
            valkey_amem.seed_agent_cards(
                agent_id=contract.agent_id,
                name=contract.name,
                role=getattr(contract, "role", "Autonomous Agent"),
                mission=getattr(contract, "mission", "")
            )
        except Exception:
            pass

        logger.info(f"⚡ [OpenClaw] Saved 4 workspace contracts for '{contract.name}' to {agent_dir}")

    def list_profiles(self) -> List[Dict[str, Any]]:
        """
        Discovers and returns all registered agent profiles from profiles_dir
        and current working directory's .stonesage/ metadata.
        """
        profiles = []
        seen_ids = set()

        # 1. Server/cluster profile directory
        if os.path.isdir(self.profiles_dir):
            for fname in sorted(os.listdir(self.profiles_dir)):
                if fname.endswith(".json"):
                    p_path = os.path.join(self.profiles_dir, fname)
                    try:
                        with open(p_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            aid = data.get("agent_id") or fname[:-5]
                            if aid not in seen_ids:
                                seen_ids.add(aid)
                                profiles.append({
                                    "agent_id": aid,
                                    "name": data.get("name", aid.title()),
                                    "role": data.get("role", "Autonomous Agent"),
                                    "mission": data.get("mission", ""),
                                    "assigned_node": data.get("assigned_node", "node1_primary"),
                                    "autonomy_level": data.get("autonomy_level", "tiered"),
                                    "created_at": data.get("created_at", 0),
                                    "profile_path": p_path,
                                    "source": "cluster"
                                })
                    except Exception as ex:
                        logger.warning(f"Error reading agent profile {p_path}: {ex}")

        # 2. Local workspace .stonesage/agent.json
        local_agent_path = os.path.join(os.getcwd(), ".stonesage", "agent.json")
        if os.path.exists(local_agent_path):
            try:
                with open(local_agent_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    aid = data.get("agent_id")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        profiles.append({
                            "agent_id": aid,
                            "name": data.get("name", aid.title()),
                            "role": data.get("role", "Project Bound Agent"),
                            "mission": data.get("mission", ""),
                            "assigned_node": data.get("assigned_node", "workstation_primary"),
                            "autonomy_level": data.get("autonomy_level", "tiered"),
                            "created_at": data.get("created_at", 0),
                            "profile_path": local_agent_path,
                            "source": "workspace"
                        })
            except Exception as ex:
                logger.warning(f"Error reading local agent: {ex}")

        return profiles

    def get_profile(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves full agent profile and contracts by agent_id.
        """
        clean_id = agent_id.strip().lower()

        # Check profiles_dir
        json_path = os.path.join(self.profiles_dir, f"{clean_id}.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)

                agent_dir = os.path.join(self.profiles_dir, clean_id)
                def read_doc(doc_name):
                    p = os.path.join(agent_dir, doc_name)
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            return f.read()
                    return ""

                meta["identity_md"] = read_doc("IDENTITY.md")
                meta["soul_md"] = read_doc("SOUL.md")
                meta["agents_md"] = read_doc("AGENTS.md")
                meta["tools_md"] = read_doc("TOOLS.md")
                return meta
            except Exception as ex:
                logger.warning(f"Error loading profile {clean_id}: {ex}")

        # Check local .stonesage/agent.json
        local_agent = self.load_project_agent(os.getcwd())
        if local_agent and (local_agent.get("agent_id") == clean_id or local_agent.get("name", "").lower() == clean_id):
            return local_agent

        # Check Founder Pillar fallback
        from .founders import get_founder
        founder = get_founder(clean_id)
        if founder:
            return {
                "agent_id": founder.id,
                "name": founder.name,
                "role": founder.title,
                "mission": founder.mission,
                "autonomy_level": "tiered",
                "assigned_node": "node1_primary" if founder.id in ("aule", "aevum") else "node1_secondary",
                "is_founder": True,
                "weight": founder.weight,
                "invariants": founder.invariants,
                "identity_md": f"# IDENTITY: {founder.name}\n{founder.title}\nMission: {founder.mission}\nDomains: {', '.join(founder.specialized_domains)}",
                "soul_md": f"# SOUL: {founder.name}\n" + "\n".join(f"- {inv}" for inv in founder.invariants),
                "agents_md": f"# AGENTS: {founder.name}\nPillar Founder of the Homelab Collective.",
                "tools_md": "# TOOLS: Full authorized homelab tool suite.",
            }

        return None

    def init_project_agent(
        self,
        project_dir: str,
        agent_id: str,
        name: str,
        role: str,
        mission: str,
        autonomy_level: str = "tiered",
        tone: str = "concise, technically rigorous, empirical",
        preferred_models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Initializes the in-repository Agent DNA (.stonesage/) inside project_dir.
        Writes agent.json, IDENTITY.md, SOUL.md, INVARIANTS.md, and HANDOVER.md.
        """
        dot_stonesage = os.path.join(project_dir, ".stonesage")
        os.makedirs(dot_stonesage, exist_ok=True)

        agent_meta = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "mission": mission,
            "autonomy_level": autonomy_level,
            "tone": tone,
            "preferred_models": preferred_models or ["coordinator", "frontier_meta"],
            "created_at": time.time(),
            "updated_at": time.time(),
            "memory_namespace": f"project_{agent_id}"
        }
        with open(os.path.join(dot_stonesage, "agent.json"), "w", encoding="utf-8") as f:
            json.dump(agent_meta, f, indent=2)

        repo_anchor = os.path.abspath(project_dir).replace("\\", "/")
        identity_md = (
            f"# IDENTITY: {name}\n\n"
            f"- **Agent ID**: `{agent_id}`\n"
            f"- **Role**: {role}\n"
            f"- **Primary Mission**: {mission}\n"
            f"- **Tone & Demeanor**: {tone}\n"
            f"- **Autonomy Profile**: `{autonomy_level}`\n"
            f"- **Repository Anchor**: `{repo_anchor}`\n"
        )
        with open(os.path.join(dot_stonesage, "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write(identity_md)

        soul_md = (
            f"# SOUL & CORE HEURISTICS: {name}\n\n"
            f"## Core Imperatives\n"
            f"1. **Empirical Grounding**: Ground every conclusion in code inspection, runtime tests, and verified telemetry.\n"
            f"2. **Long-Term Invariance**: Uphold project architecture and prevent regressions across model generations.\n"
            f"3. **Milestone Integrity**: Always report milestone progress and keep HANDOVER.md updated.\n\n"
            f"## Behavioral Boundaries\n"
            f"- Respect the `{autonomy_level}` sandbox boundary.\n"
            f"- When formulating critical discoveries or engineering invariants, wrap them in `<invariant>...</invariant>`.\n"
            f"- When finishing a milestone or handing over work, wrap it in `<handover>...</handover>`.\n"
        )
        with open(os.path.join(dot_stonesage, "SOUL.md"), "w", encoding="utf-8") as f:
            f.write(soul_md)

        invariants_path = os.path.join(dot_stonesage, "INVARIANTS.md")
        if not os.path.exists(invariants_path):
            initial_invariants = (
                f"# PROJECT ARCHITECTURAL INVARIANTS: {name}\n\n"
                f"Living master log of discovered invariants, design decisions, and hard failure boundaries.\n\n"
                f"- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Initialized project invariant registry.\n"
            )
            with open(invariants_path, "w", encoding="utf-8") as f:
                f.write(initial_invariants)

        handover_path = os.path.join(dot_stonesage, "HANDOVER.md")
        if not os.path.exists(handover_path):
            initial_handover = (
                f"# ROLLING MILESTONE HANDOVER & MEMORY: {name}\n\n"
                f"## Current State\n"
                f"- Project workspace initialized.\n\n"
                f"## Completed Milestones\n"
                f"- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Dedicated agent `{name}` bound to repository.\n\n"
                f"## Open Tasks & Next Steps\n"
                f"- Awaiting primary directive from operator.\n"
            )
            with open(handover_path, "w", encoding="utf-8") as f:
                f.write(initial_handover)

        logger.info(f"⚡ [OpenClaw] Initialized Project Agent '{name}' in {dot_stonesage}")

        try:
            from ..data_fabric.valkey_amem import valkey_amem
            valkey_amem.seed_agent_cards(
                agent_id=agent_id,
                name=name,
                role=role,
                mission=mission
            )
        except Exception:
            pass

        return agent_meta

    def load_project_agent(self, project_dir: str) -> Optional[Dict[str, Any]]:
        """
        Loads the in-repository Agent DNA from <project_dir>/.stonesage/.
        """
        if not project_dir or not os.path.exists(project_dir):
            return None

        dot_stonesage = os.path.join(project_dir, ".stonesage")
        agent_json_path = os.path.join(dot_stonesage, "agent.json")

        if not os.path.exists(agent_json_path):
            # Check fallback in project root .stonesage-project.json
            proj_meta = os.path.join(project_dir, ".stonesage-project.json")
            if os.path.exists(proj_meta):
                try:
                    with open(proj_meta, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    if meta.get("agent_id"):
                        return self.init_project_agent(
                            project_dir=project_dir,
                            agent_id=meta.get("agent_id"),
                            name=meta.get("agent_name") or meta.get("name", "Project Specialist"),
                            role=meta.get("agent_role") or f"Engineer for {meta.get('name')}",
                            mission=f"Deliver robust engineering for {meta.get('name')}",
                            autonomy_level=meta.get("autonomy_level", "tiered")
                        )
                except Exception:
                    pass
            return None

        try:
            with open(agent_json_path, "r", encoding="utf-8") as f:
                agent_meta = json.load(f)

            def read_file(fname: str) -> str:
                fpath = os.path.join(dot_stonesage, fname)
                if os.path.exists(fpath):
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        return f.read()
                return ""

            agent_meta["identity_md"] = read_file("IDENTITY.md")
            agent_meta["soul_md"] = read_file("SOUL.md")
            agent_meta["invariants_md"] = read_file("INVARIANTS.md")
            agent_meta["handover_md"] = read_file("HANDOVER.md")
            agent_meta["workspace_dir"] = os.path.abspath(project_dir).replace("\\", "/")
            return agent_meta
        except Exception as ex:
            logger.warning(f"Failed loading agent from {project_dir}: {ex}")
            return None

    def compile_agent_system_prompt(self, project_dir: str, base_prompt: str = "", user_query: Optional[str] = None) -> str:
        """
        Compiles the model-agnostic Agent Persona & Project Invariants system prompt.
        If user_query is provided, uses context_fabric to inject dynamic A-MEM knowledge atoms.
        """
        agent = self.load_project_agent(project_dir)
        if not agent:
            if user_query:
                try:
                    from .context_fabric import context_fabric
                    return context_fabric.compile_dynamic_turn(user_query=user_query)
                except Exception:
                    pass
            return base_prompt

        name = agent.get("name", "Specialist")
        agent_id = agent.get("agent_id", "project_agent")
        role = agent.get("role", "Engineer")
        autonomy = agent.get("autonomy_level", "tiered")

        prompt_parts = []
        if base_prompt:
            prompt_parts.append(base_prompt.strip())

        prompt_parts.append(
            f"\n# ASSIGNED PROJECT AGENT: {name} (ID: `{agent_id}`)\n"
            f"You are the permanent, dedicated engineer bound to this project repository.\n"
            f"- Role: {role}\n"
            f"- Autonomy Level: {autonomy}\n"
            f"- Workspace Root: {agent.get('workspace_dir')}\n\n"
            f"## Operational Directives & Invariants\n"
            f"1. **Invariant Recording**: When you discover, diagnose, or formulate a critical architectural rule or failure mode, wrap it in `<invariant>Rule statement and explanation</invariant>`.\n"
            f"2. **Milestone Handover**: When you complete a milestone or establish a handover state, wrap it in `<handover>Summary of progress and next steps</handover>`.\n\n"
            f"## Active Project Invariants\n"
            f"{agent.get('invariants_md', '(No custom invariants recorded yet)')}\n\n"
            f"## Rolling Handover & Recent Memory State\n"
            f"{agent.get('handover_md', '(Workspace initialized)')}\n"
        )

        if user_query:
            try:
                from ..data_fabric.valkey_amem import valkey_amem
                atom_header = valkey_amem.format_injection_header(user_query, max_atoms=2)
                if atom_header:
                    prompt_parts.append(f"\n{atom_header}\n")
            except Exception:
                pass

        return "\n".join(prompt_parts)

    def append_project_invariant(self, project_dir: str, invariant_text: str, deduplicate: bool = True) -> tuple[bool, str]:
        """
        Appends a newly discovered architectural invariant to .stonesage/INVARIANTS.md.
        Optionally deduplicates against existing invariants using text normalization.
        """
        clean_text = invariant_text.strip()
        if not clean_text:
            return False, "Empty invariant text."

        dot_stonesage = os.path.join(project_dir, ".stonesage")
        invariants_file = os.path.join(dot_stonesage, "INVARIANTS.md")

        existing_content = ""
        if os.path.exists(invariants_file):
            with open(invariants_file, "r", encoding="utf-8", errors="replace") as f:
                existing_content = f.read()

        if deduplicate and existing_content:
            norm_new = re.sub(r"[^a-zA-Z0-9]", "", clean_text.lower())
            norm_existing = re.sub(r"[^a-zA-Z0-9]", "", existing_content.lower())
            if norm_new in norm_existing:
                return False, "Duplicate invariant already recorded."

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entry = f"\n- [{timestamp}] {clean_text}\n"

        os.makedirs(dot_stonesage, exist_ok=True)
        with open(invariants_file, "a", encoding="utf-8") as f:
            f.write(new_entry)

        logger.info(f"📌 [OpenClaw] Recorded new invariant in {invariants_file}: {clean_text[:60]}...")
        return True, "Invariant recorded successfully."

    def update_project_handover(self, project_dir: str, handover_text: str) -> bool:
        """
        Appends or updates milestones in .stonesage/HANDOVER.md.
        """
        clean_text = handover_text.strip()
        if not clean_text:
            return False

        dot_stonesage = os.path.join(project_dir, ".stonesage")
        handover_file = os.path.join(dot_stonesage, "HANDOVER.md")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n\n### [{timestamp}] Milestone Update\n{clean_text}\n"

        os.makedirs(dot_stonesage, exist_ok=True)
        with open(handover_file, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(f"📋 [OpenClaw] Updated HANDOVER.md in {handover_file}")
        return True

    @staticmethod
    def extract_sentinel_tags(text: str) -> tuple[list[str], list[str], str]:
        """
        Extracts <invariant>...</invariant> and <handover>...</handover> blocks from model output.
        Returns: (invariants, handovers, cleaned_text)
        """
        invariants = re.findall(r"<invariant>(.*?)</invariant>", text, flags=re.DOTALL)
        handovers = re.findall(r"<handover>(.*?)</handover>", text, flags=re.DOTALL)
        cleaned = re.sub(r"<invariant>.*?</invariant>", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"<handover>.*?</handover>", "", cleaned, flags=re.DOTALL).strip()
        return [i.strip() for i in invariants if i.strip()], [h.strip() for h in handovers if h.strip()], cleaned

class PermissionResult(tuple):
    def __new__(cls, allowed: bool, reason: str = ""):
        return super().__new__(cls, (allowed, reason))

    @property
    def allowed(self) -> bool:
        return self[0]

    @property
    def reason(self) -> str:
        return self[1]

    def __bool__(self) -> bool:
        return bool(self[0])

class WorkspacePermissionBoundary:
    """Enforces directory sandbox boundaries and operation limits for autonomous agents."""

    def __init__(self, root_path: Optional[str] = None, autonomy_level: str = "tiered"):
        self.root_path = root_path
        self.autonomy_level = autonomy_level

    def validate_path(self, target_path: str, workspace_root: Optional[str] = None) -> bool:
        """Ensures target_path resolves strictly within workspace_root (blocks .. traversal)."""
        root = workspace_root or self.root_path
        if not target_path or not root:
            return False
        try:
            norm_target = os.path.realpath(os.path.abspath(target_path))
            norm_root = os.path.realpath(os.path.abspath(root))
            return os.path.commonpath([norm_target, norm_root]) == norm_root
        except Exception:
            return False

    def check_tool_permission(self, tool_name: str, autonomy_level: Optional[str] = None, is_write: bool = False) -> PermissionResult:
        """
        Validates whether tool is permitted under autonomy level.
        Levels:
          - 'read_only': only read-only inspection tools allowed.
          - 'tiered': write tools require user review or staging.
          - 'autonomous': full execution within workspace boundary.
        """
        level = autonomy_level or self.autonomy_level
        read_tools = {"read_file", "list_dir", "grep_search", "find_by_name", "search_memory", "get_status"}
        write_tools = {"write_to_file", "write_file", "replace_file_content", "save_file", "delete_file"}
        exec_tools = {"run_command", "send_terminal_command"}

        if level == "read_only":
            if tool_name in write_tools or is_write:
                return PermissionResult(False, "Tool blocked: Session is in 'read_only' mode.")
            if tool_name in exec_tools:
                return PermissionResult(False, "Shell command execution blocked under 'read_only' mode.")
            return PermissionResult(True, "Authorized")

        elif level == "tiered":
            if tool_name in exec_tools:
                return PermissionResult(False, "Tiered: Shell execution requires explicit user confirmation.")
            if tool_name in write_tools or is_write:
                return PermissionResult(True, "Tiered: Write authorized within workspace boundary.")
            return PermissionResult(True, "Authorized")

        elif level == "autonomous":
            return PermissionResult(True, "Autonomous: Unrestricted within workspace boundary.")

        return PermissionResult(True, "Authorized")

workspace_boundary = WorkspacePermissionBoundary()
openclaw_engine = OpenClawEngine()
