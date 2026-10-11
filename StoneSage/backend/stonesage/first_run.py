#!/usr/bin/env python3
"""First-run setup: writes config.json for a new install (docs/LAYERS.md M2: everything about the home lives in config).

    python first_run.py                       ask for what is missing, write config.json (a backup of any old one first)
    python first_run.py --all                 ask every question again (current answers are the defaults)
    python first_run.py --answers a.json      no questions: take the answers from a file (unattended install)
    python first_run.py --check               write nothing: list what is still on a default

Answers are flat keys ("operator.name", "household.people", ...). Lists are typed comma-separated; pets as
"Name:species, Name:species". The Home Assistant token is read without echo and stored only in config.json, which
never leaves the machine (gitignored; GET /api/config masks it).
"""

import argparse
import getpass
import json
import os
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # stonesage/ -> backend/
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from lodestone import endpoints  # noqa: E402

CONFIG_FILE = os.environ.get("STONESAGE_CONFIG_FILE") or os.path.join(BACKEND_DIR, "config.json")


def _list(text: str) -> List[str]:
    return [x.strip() for x in str(text).split(",") if x.strip()]


def _pets(text: str) -> Dict[str, str]:
    out = {}
    for item in _list(text):
        name, _, species = item.partition(":")
        if name.strip():
            out[name.strip()] = (species.strip() or "pet").lower()
    return out


def _pronouns(text: str) -> List[str]:
    p = [x.strip().lower() for x in str(text).replace("/", ",").split(",") if x.strip()]
    return p if len(p) == 2 else ["they", "them"]


# (key, question, default, parse, secret). Defaults are for one box running everything.
QUESTIONS: List[Tuple[str, str, Any, Callable[[str], Any], bool]] = [
    ("operator.name", "Your name (what the assistant calls you)", "", str.strip, False),
    ("operator.pronouns", "Your pronouns, subject/object", "they/them", _pronouns, False),
    ("server.timezone", "Time zone (IANA name, e.g. Europe/London; blank = this machine's)", "", str.strip, False),
    ("cluster.coordinator_url", "Main model server (OpenAI-compatible, ends in /v1)", "http://127.0.0.1:8001/v1", str.strip, False),
    ("cluster.worker_url", "Fast helper model server", "http://127.0.0.1:8002/v1", str.strip, False),
    ("cluster.embedder_url", "Embedding server", "http://127.0.0.1:8003/v1", str.strip, False),
    ("cluster.vision_url", "Vision model server", "http://127.0.0.1:8004/v1", str.strip, False),
    ("cluster.ssh_user", "User for SSH to the model host (blank = the current user)", "", str.strip, False),
    ("household.people", "People who live there, comma-separated (the first approves actions)", "", _list, False),
    ("household.owner_pronouns", "That first person's pronouns, subject/object", "they/them", _pronouns, False),
    ("household.pets", "Pets as Name:species, comma-separated", "", _pets, False),
    ("homeassistant.url", "Home Assistant address", "http://homeassistant.local:8123", str.strip, False),
    ("homeassistant.token", "Home Assistant long-lived access token", "", str.strip, True),
]


def get(cfg: Dict[str, Any], key: str) -> Any:
    node: Any = cfg
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def put(cfg: Dict[str, Any], key: str, value: Any) -> None:
    *parents, last = key.split(".")
    node = cfg
    for part in parents:
        node = node.setdefault(part, {})
    node[last] = value


def build_config(answers: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """existing + answers (parsed, empty answers dropped); the household's owner pronouns follow the operator's when the
    operator is the household's first person and none were given. Pure: no files, no prompts."""
    cfg = json.loads(json.dumps(existing or {}))
    parse = {k: p for k, _, _, p, _ in QUESTIONS}
    for key, raw in answers.items():
        value = parse[key](raw) if key in parse and isinstance(raw, str) else raw
        if value in ("", [], {}, None):
            continue
        put(cfg, key, value)
    people = get(cfg, "household.people") or []
    if people and get(cfg, "operator.name") == people[0] and "household.owner_pronouns" not in answers:
        if get(cfg, "operator.pronouns"):
            put(cfg, "household.owner_pronouns", get(cfg, "operator.pronouns"))
    return cfg


def missing_questions(cfg: Dict[str, Any]) -> List[str]:
    return [k for k, *_ in QUESTIONS if get(cfg, k) in (None, "", [], {})]


def load(path: str = CONFIG_FILE) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def backup_file(path: str) -> Optional[str]:
    """Copy an existing file to <path>.bak-<time> (the setup wizard shares this). Returns the copy's path, None if no file."""
    if not os.path.exists(path):
        return None
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copyfile(path, backup)
    try:
        os.chmod(backup, 0o600)          # the copy holds the same tokens as the file
    except OSError:
        pass
    return backup


def write(cfg: Dict[str, Any], path: str = CONFIG_FILE) -> Optional[str]:
    """Write config.json; the old one is kept as config.json.bak-<time>. Returns the backup's path (None: no old file)."""
    backup = backup_file(path)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # owner-only from the first byte
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return backup


def ask(cfg: Dict[str, Any], keys: List[str], read: Callable[[str], str] = input,
        secret: Callable[[str], str] = getpass.getpass) -> Dict[str, Any]:
    answers = {}
    for key, question, default, _, is_secret in QUESTIONS:
        if key not in keys:
            continue
        current = get(cfg, key)
        shown = "/".join(current) if key.endswith("pronouns") and isinstance(current, list) else \
            ", ".join(f"{n}:{s}" for n, s in current.items()) if isinstance(current, dict) else \
            ", ".join(current) if isinstance(current, list) else current
        if is_secret:
            raw = secret(f"{question}{' (Enter keeps the current one)' if current else ''}: ")
        else:
            d = shown or default
            raw = read(f"{question}{f' [{d}]' if d else ''}: ").strip() or d
        if raw:
            answers[key] = raw
    return answers


def check(cfg: Dict[str, Any]) -> List[str]:
    lines = [f"still on a default (not set in config.json): {', '.join(endpoints.missing(cfg)) or 'nothing'}"]
    optional = endpoints.missing(cfg, optional=True)
    if optional:
        lines.append(f"optional, not set (fine unless you use them): {', '.join(optional)}")
    gaps = missing_questions(cfg)
    if gaps:
        lines.append(f"unanswered: {', '.join(gaps)}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="StoneSage first-run setup: writes config.json")
    ap.add_argument("--all", action="store_true", help="ask every question again")
    ap.add_argument("--answers", help="JSON file of answers (no questions asked)")
    ap.add_argument("--check", action="store_true", help="write nothing; list what is still on a default")
    ap.add_argument("--config", default=CONFIG_FILE, help="config.json to read and write")
    args = ap.parse_args(argv)

    cfg = load(args.config)
    if args.check:
        print("\n".join(check(cfg)))
        return 0
    if args.answers:
        with open(args.answers, encoding="utf-8") as f:
            answers = json.load(f)
    else:
        keys = [k for k, *_ in QUESTIONS] if args.all else missing_questions(cfg)
        if not keys:
            print("Nothing missing. Run with --all to change answers.")
            print("\n".join(check(cfg)))
            return 0
        answers = ask(cfg, keys)
    new = build_config(answers, cfg)
    backup = write(new, args.config)
    print(f"wrote {args.config}" + (f" (previous kept as {backup})" if backup else ""))
    print("\n".join(check(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
