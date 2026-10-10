"""
Default Security & Autonomy Profiles.
"""

from typing import Dict, Any, List
from dataclasses import dataclass, field

@dataclass
class SecurityProfile:
    name: str
    description: str
    auto_approve_read_only: bool = True
    auto_approve_file_writes: bool = False
    auto_approve_shell_commands: bool = False
    whitelisted_tools: List[str] = field(default_factory=list)
    blacklisted_commands_regex: List[str] = field(default_factory=list)
    protected_paths: List[str] = field(default_factory=list)

STRICT_PROFILE = SecurityProfile(
    name="Strict",
    description="Full Human-in-the-Loop. Every tool call and command requires explicit human confirmation [Y/n].",
    auto_approve_read_only=False,
    auto_approve_file_writes=False,
    auto_approve_shell_commands=False,
    whitelisted_tools=[],
    blacklisted_commands_regex=[r".*"],  # Everything requires prompt
    protected_paths=["/etc", ".git", "/boot", "C:\\Windows"]
)

TIERED_PROFILE = SecurityProfile(
    name="Tiered",
    description="Balanced Autonomy. Read-only inspections run autonomously; file writes and commands prompt for approval.",
    auto_approve_read_only=True,
    auto_approve_file_writes=False,
    auto_approve_shell_commands=False,
    whitelisted_tools=["read_file", "search_memory", "nudge_agent", "list_dir", "grep_search"],
    blacklisted_commands_regex=[
        r"rm\s+-rf.*",
        r"sudo.*",
        r"mkfs.*",
        r"dd\s+if=.*",
        r":\(\)\{ :\|:& \};:",
        r"chmod\s+-R\s+777.*"
    ],
    protected_paths=["/etc", ".git", "/proc", "/sys", "C:\\Windows"]
)

AUTONOMOUS_PROFILE = SecurityProfile(
    name="Autonomous",
    description="Hands-Free Execution with Invariant Rollback. Tools run autonomously; reverts changes on regression.",
    auto_approve_read_only=True,
    auto_approve_file_writes=True,
    auto_approve_shell_commands=True,
    whitelisted_tools=["*"],
    blacklisted_commands_regex=[
        r"rm\s+-rf\s+/(?!opt|tmp).*",
        r"mkfs.*",
        r":\(\)\{ :\|:& \};:"
    ],
    protected_paths=["/etc", "/boot", "C:\\Windows\\System32"]
)
