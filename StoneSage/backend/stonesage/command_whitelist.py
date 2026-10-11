"""Shell-command safety for the platform's tools: allowed binaries, safe systemctl actions, dangerous patterns, and
workspace scoping. The Home Assistant limits that used to live here are in hearth/ha_policy.py (docs/LAYERS.md).

A command is never given to a shell. `parse_command` turns the text into an argv list or refuses it, and `run_command`
executes that list with shell=False, so there is nothing for `;`, `|`, backticks or `$(...)` to act on (they are refused
anyway). Anything that is not clearly allowed is refused: the check fails closed."""

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
    # Diagnostics & Homelab Services (no curl/wget: a download-and-run or a data upload is not a diagnostic)
    "jq", "ping", "systemctl", "journalctl", "uptime", "free", "df", "ps"
}

# Arguments that turn an allowed program into "run this text as code" or "run that other program"
INLINE_CODE_FLAGS = {
    "python": {"-c"}, "python3": {"-c"}, "py": {"-c"},
    "node": {"-e", "--eval", "-p", "--print"}, "deno": {"eval"}, "bun": {"-e", "--eval"},
    "git": {"-c", "--exec-path", "--upload-pack", "--receive-pack", "--config-env", "-C"},
    "find": {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"},
    "npx": {"-c", "--call"}, "npm": {"exec", "x"}, "pnpm": {"exec", "dlx"}, "yarn": {"dlx", "exec"},
}

# What a shell would act on: chaining, pipes, redirection, substitution, subshells, newlines, NUL, and on Windows the
# percent and caret expansions. There is no shell here (argv goes straight to the program), so a command that has any of
# these is refused rather than run with the characters taken literally.
SHELL_META = set(";&|<>`$(){}\n\r\x00") | ({"%", "^"} if os.name == "nt" else set())

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

NAVIGATION = ("cd", "chdir", "set-location")      # builtins: the caller tracks the working directory itself


def parse_command(command_str: str) -> Tuple[bool, str, List[str]]:
    """Command text -> (ok, reason, argv). Refuses shell metacharacters, unbalanced quotes, a program that is not a bare
    name on the allowlist, inline-code flags and anything outside the systemctl rules. The argv is what to execute with
    shell=False."""
    cmd = (command_str or "").strip()
    if not cmd:
        return False, "Command string cannot be empty.", []
    bad = sorted({c for c in cmd if c in SHELL_META})
    if bad:
        shown = ", ".join(repr(c) for c in bad)
        return False, f"Shell operators and substitution are not allowed ({shown}). Run one plain command.", []
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False, f"Command contains forbidden destructive pattern matching '{pattern}'.", []
    try:
        argv = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError as e:
        return False, f"Cannot parse the command ({e}).", []
    if os.name == "nt":      # non-POSIX split keeps the quote marks
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "\"'" else a for a in argv]
    if not argv:
        return False, "Command string cannot be empty.", []
    program = argv[0]
    if "/" in program or "\\" in program:
        return False, "Name the program without a path (it is looked up on PATH).", []
    base = program.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base not in ALLOWED_BINARIES:
        return False, f"Binary '{base}' is not in the StoneSage coding and diagnostics whitelist.", []
    banned = INLINE_CODE_FLAGS.get(base, set())
    for a in argv[1:]:
        low = a.lower()
        if low in banned or any(low.startswith(f + "=") for f in banned if f.startswith("--")) \
                or (base in ("python", "python3", "py") and low.startswith("-c")):
            return False, f"'{a}' runs code or another program from inside '{base}'; not allowed here.", []
    if base == "systemctl":
        tokens = [a for a in argv[1:] if not a.startswith("-")]
        if not tokens:
            return False, "systemctl requires a subcommand (e.g. status, is-active).", []
        subcmd = tokens[0].lower()
        if subcmd not in ALLOWED_SYSTEMCTL_ACTIONS:
            return False, f"systemctl action '{subcmd}' is not permitted. Allowed: {sorted(ALLOWED_SYSTEMCTL_ACTIONS)}", []
        if subcmd == "restart":
            targets = [t.lower() for t in tokens[1:]]
            if len(targets) != 1 or targets[0] not in ALLOWED_SYSTEMCTL_SERVICES:
                return False, f"Restarting {targets or 'nothing'} is not permitted. Allowed: {sorted(ALLOWED_SYSTEMCTL_SERVICES)}", []
    return True, "Command approved by coding and development whitelist.", argv


def validate_terminal_command(command_str: str, cwd: str = "", allowed_roots: Optional[List[str]] = None) -> Tuple[bool, str]:
    """Is this command allowed? -> (is_allowed, reason). `cd` and friends are builtins the caller handles (there is no shell
    to run them). When `allowed_roots` is given the working directory must lie inside one of them (realpath containment)."""
    first = (command_str or "").strip().split(None, 1)
    if first and first[0].lower() in NAVIGATION and not any(c in SHELL_META for c in command_str):
        return True, "Directory navigation permitted."
    ok, reason, argv = parse_command(command_str)
    if not ok:
        return False, reason
    if allowed_roots:
        roots = [os.path.realpath(r) for r in allowed_roots]
        if cwd and not _inside(os.path.realpath(cwd), roots):
            return False, f"Execution path '{cwd}' is outside authorized workspace roots."
        outside = path_args_outside(argv, cwd or os.getcwd(), roots)
        if outside:
            return False, f"'{outside}' is outside the authorized workspace roots."
    return True, reason


def _inside(real: str, roots: List[str]) -> bool:
    return any(real == r or real.startswith(r.rstrip("/\\") + os.sep) for r in roots)


def path_args_outside(argv: List[str], cwd: str, roots: List[str]) -> str:
    """The first argument that names a place (has a path separator, starts with ~ or is '..') outside the roots, or ''.
    Symlinks are followed (realpath), so a link inside the workspace does not lead out of it."""
    for a in argv[1:]:
        candidate = a.split("=", 1)[1] if a.startswith("-") and "=" in a else a
        if candidate.startswith("-") or not candidate:
            continue
        if not (("/" in candidate) or ("\\" in candidate) or candidate.startswith("~") or candidate == ".."
                or os.path.isabs(candidate)):
            continue
        real = os.path.realpath(os.path.join(cwd, os.path.expanduser(candidate)))
        if not _inside(real, roots):
            return a
    return ""


def run_command(command_str: str, cwd: str, timeout: float = 30.0):
    """Validate and run one allowed command with shell=False. Returns the subprocess.CompletedProcess; raises ValueError
    (the reason) when the command is refused, and subprocess.TimeoutExpired / OSError as usual."""
    import subprocess
    ok, reason, argv = parse_command(command_str)
    if not ok:
        raise ValueError(reason)
    return subprocess.run(argv, shell=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)
