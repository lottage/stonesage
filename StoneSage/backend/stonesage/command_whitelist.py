"""Shell-command safety for the platform's tools: allowed binaries, safe systemctl actions, dangerous patterns, and
workspace scoping. The Home Assistant limits that used to live here are in hearth/ha_policy.py (docs/LAYERS.md)."""

import os
import re
import shlex
from typing import Dict, Any, List, Tuple, Optional

# ==============================================================================
# 2. CODING & TERMINAL COMMAND WHITELIST
# ==============================================================================

# Allowed command base names / binaries
ALLOWED_BINARIES = {
    # Compilers, Runtimes & Interpreters
    "python", "python3", "py", "node", "npm", "npx", "pnpm", "yarn",
    "cargo", "rustc", "go", "tsc", "deno", "bun",
    # Testing, Linting & Formatting
    "pytest", "unittest", "ruff", "black", "flake8", "eslint", "prettier", "mypy", "bandit",
    # Version Control
    "git",
    # Package Management
    "pip", "pip3", "uv",
    # Filesystem & Inspection
    "ls", "dir", "cat", "head", "tail", "grep", "find", "findstr",
    "wc", "pwd", "echo", "diff", "mkdir", "tree", "type", "touch", "cp", "mv",
    # Network, Diagnostics & Homelab Services
    "curl", "wget", "jq", "ping", "systemctl", "journalctl", "uptime", "free", "df", "ps"
}

# Subcommands explicitly allowed for systemctl (read-only + safe service restarts)
ALLOWED_SYSTEMCTL_ACTIONS = {
    "status", "is-active", "is-enabled", "restart"
}
ALLOWED_SYSTEMCTL_SERVICES = {
    "stonesage", "stonesage.service",
    "cluster-mcp", "cluster-mcp.service",
    "wildlife-sentry", "wildlife-sentry.service"
}

# Hard blacklist of dangerous shell commands / destructive patterns
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]*\s+[\/\\]",                 # rm -rf / or rm -rf \
    r"\brm\s+-[rf]*\s+\*",                     # rm -rf *
    r"\brmdir\s+\/s\s+\/q\s+[c-zC-Z]:\\",       # Windows rmdir /s /q C:\
    r"\bmkfs\b",                               # Format partition
    r"\bfdisk\b",                              # Partition tool
    r"\bdd\s+if=",                             # Raw block overwrite
    r"\bshutdown\b",                           # Node shutdown
    r"\binit\s+0\b",                           # Shutdown
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",   # Fork bomb
    r"\bchmod\s+-[rR]*\s+777\s+[\/\\]",        # chmod -R 777 /
    r"\bchown\s+-[rR]*\s+[\/\\]",              # chown -R /
    r"\bdrop\s+database\b",                    # Database destruction
    r"\btruncate\s+table\b",                   # Table destruction
]


def validate_terminal_command(command_str: str, cwd: str = "", allowed_roots: Optional[List[str]] = None) -> Tuple[bool, str]:
    """
    Validates a shell/terminal command against coding binaries, safety bounds,
    and filesystem scope.
    Returns: (is_allowed, reason)
    """
    cmd = (command_str or "").strip()
    if not cmd:
        return False, "Command string cannot be empty."

    # 1. Dangerous Pattern Blacklist Check
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False, f"Command contains forbidden destructive pattern matching '{pattern}'."

    # 2. Extract base binary
    # Handle PowerShell command chains or pipe splits safely
    # Extract first token
    parts = cmd.split(None, 1)
    base_token = parts[0].strip().strip('"').strip("'").lower()
    
    # Strip path prefix (e.g. /usr/bin/git -> git, C:\Python313\python.exe -> python)
    base_name = os.path.basename(base_token).lower()
    if base_name.endswith(".exe"):
        base_name = base_name[:-4]

    # Handle directory navigation builtins
    if base_name in ("cd", "chdir", "set-location"):
        return True, "Directory navigation permitted."

    # 3. Whitelist check on binary
    if base_name not in ALLOWED_BINARIES:
        return False, f"Binary '{base_name}' is not in the StoneSage coding and diagnostics whitelist."

    # 4. Systemctl scope check
    if base_name == "systemctl":
        cmd_tokens = cmd.split()[1:]
        if not cmd_tokens:
            return False, "systemctl requires a subcommand (e.g. status, is-active)."
        subcmd = cmd_tokens[0].lower()
        if subcmd not in ALLOWED_SYSTEMCTL_ACTIONS:
            return False, f"systemctl action '{subcmd}' is not permitted. Allowed: {ALLOWED_SYSTEMCTL_ACTIONS}"
        if subcmd == "restart":
            # Must be one of the permitted cluster services
            target_svc = cmd_tokens[1].lower() if len(cmd_tokens) > 1 else ""
            if target_svc not in ALLOWED_SYSTEMCTL_SERVICES:
                return False, f"Restarting service '{target_svc}' is not permitted. Allowed: {ALLOWED_SYSTEMCTL_SERVICES}"

    # 5. Working Directory Scoping Check (if allowed_roots supplied)
    if allowed_roots and cwd:
        abs_cwd = os.path.abspath(cwd).lower()
        matched = any(abs_cwd.startswith(os.path.abspath(r).lower()) for r in allowed_roots)
        if not matched:
            return False, f"Execution path '{cwd}' is outside authorized workspace roots."

    return True, "Command approved by coding and development whitelist."
