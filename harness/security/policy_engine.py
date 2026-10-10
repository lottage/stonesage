"""
Granular Policy Engine & ACL Validator.
Evaluates agent actions, file access boundaries, and shell command patterns against the active security profile.
"""

import re
import os
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from .profiles import SecurityProfile, TIERED_PROFILE, STRICT_PROFILE, AUTONOMOUS_PROFILE

logger = logging.getLogger("Harness.PolicyEngine")

@dataclass
class ActionVerdict:
    allowed: bool
    requires_confirmation: bool
    reason: str
    risk_level: str  # "safe", "moderate", "dangerous", "blocked"

class PolicyEngine:
    READ_ONLY_TOOLS = {
        "read_file", "view_file", "list_dir", "grep_search",
        "find_by_name", "search_memory", "read_url_content"
    }

    WRITE_TOOLS = {
        "write_to_file", "replace_file_content", "store_memory"
    }

    COMMAND_TOOLS = {
        "run_command", "home_assistant_call"
    }

    def __init__(self, default_profile: Optional[SecurityProfile] = None):
        self.profile = default_profile or TIERED_PROFILE
        self.custom_whitelisted_paths: List[str] = ["./scratch", "./tests", "/tmp"]

    def set_profile(self, profile_name: str) -> bool:
        """Switches active security profile."""
        p_name = profile_name.strip().lower()
        if p_name == "strict":
            self.profile = STRICT_PROFILE
            return True
        elif p_name in ("tiered", "balanced"):
            self.profile = TIERED_PROFILE
            return True
        elif p_name in ("autonomous", "hands-free"):
            self.profile = AUTONOMOUS_PROFILE
            return True
        return False

    def evaluate_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> ActionVerdict:
        """
        Determines whether a tool call can proceed autonomously, requires confirmation, or is blocked.
        """
        # 1. Path Boundary Checks
        target_path = arguments.get("TargetFile") or arguments.get("path") or arguments.get("AbsolutePath")
        if target_path:
            clean_path = str(target_path).replace("\\", "/")
            for prot in self.profile.protected_paths:
                if clean_path.startswith(prot.replace("\\", "/")):
                    return ActionVerdict(
                        allowed=False,
                        requires_confirmation=False,
                        reason=f"Access to protected system path '{prot}' is strictly blocked.",
                        risk_level="blocked",
                    )

        # 2. Command Regex Checks
        cmd_str = arguments.get("CommandLine") or arguments.get("command")
        if cmd_str:
            for pattern in self.profile.blacklisted_commands_regex:
                if re.search(pattern, str(cmd_str), re.IGNORECASE):
                    if self.profile.name == "Strict":
                        return ActionVerdict(
                            allowed=True,
                            requires_confirmation=True,
                            reason=f"Strict profile: Command '{cmd_str[:40]}...' requires explicit confirmation.",
                            risk_level="moderate",
                        )
                    return ActionVerdict(
                        allowed=False,
                        requires_confirmation=False,
                        reason=f"Command matches forbidden pattern: '{pattern}'.",
                        risk_level="blocked",
                    )

        # 3. Read-Only Actions
        if tool_name in self.READ_ONLY_TOOLS:
            if self.profile.auto_approve_read_only:
                return ActionVerdict(allowed=True, requires_confirmation=False, reason="Read-only action auto-approved.", risk_level="safe")
            return ActionVerdict(allowed=True, requires_confirmation=True, reason="Read-only action requires approval under Strict profile.", risk_level="safe")

        # 4. Write Actions
        if tool_name in self.WRITE_TOOLS:
            if self.profile.auto_approve_file_writes:
                return ActionVerdict(allowed=True, requires_confirmation=False, reason="File mutation auto-approved under Autonomous profile.", risk_level="moderate")
            return ActionVerdict(allowed=True, requires_confirmation=True, reason="File mutation requires operator approval.", risk_level="moderate")

        # 5. Shell Command Execution
        if tool_name in self.COMMAND_TOOLS:
            if self.profile.auto_approve_shell_commands:
                return ActionVerdict(allowed=True, requires_confirmation=False, reason="Command auto-approved under Autonomous profile.", risk_level="moderate")
            return ActionVerdict(allowed=True, requires_confirmation=True, reason="Shell execution requires operator approval.", risk_level="dangerous")

        # Fallback default
        return ActionVerdict(
            allowed=True,
            requires_confirmation=not self.profile.auto_approve_read_only,
            reason=f"Tool '{tool_name}' evaluated against '{self.profile.name}' profile.",
            risk_level="moderate",
        )

policy_engine = PolicyEngine()
