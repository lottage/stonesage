"""Settings upgrades: config.json carries `config_version`, and a list of small steps brings an older file up to date.
(docs/LAYERS.md, "M5: Backups, upgrades and the rest of the review"; operator-facing text in docs/UPGRADING.md)

    python -m stonesage.config_migrate [--config FILE] [--dry-run]     # show or do the upgrade by hand

On start the server calls `upgrade_file`: a config with a `config_version` below LATEST (no number = 0) is backed up first (the
caller passes the backup function; a failed backup means nothing is changed), brought up step by step, and written atomically
with owner-only permissions. A config NEWER than this code understands is refused (`ConfigTooNew`): running old code on settings
a newer version rewrote could quietly misread them, and then save the misreading. Each step is a small pure function (it gets
the settings and returns new settings plus notes; its input is never changed), and only ever renames or moves a setting
whose old name the code still reads as a fallback; nothing is deleted that has a value the new place does not already hold.

Add a step: write the function, append it to MIGRATIONS (never reorder or edit a released one), and add a test.
"""

import argparse
import copy
import functools
import json
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from stonesage import auth

logger = logging.getLogger("StoneSage.config")

Notes = List[str]
Step = Callable[[Dict[str, Any]], Tuple[Dict[str, Any], Notes]]


class ConfigTooNew(RuntimeError):
    """config.json was written by a newer StoneSage than this one."""


class ConfigError(ValueError):
    """config.json cannot be understood (not JSON, or `config_version` is not a whole number)."""


def _step(fn: Callable[[Dict[str, Any], Notes], None]) -> Step:
    """Turn `fn(copy, notes)`, which edits its own copy, into a step that never touches the settings it was given."""
    @functools.wraps(fn)
    def run(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Notes]:
        new, notes = copy.deepcopy(cfg), []
        fn(new, notes)
        return new, notes
    return run


def _move_key(cfg: Dict[str, Any], old: Tuple[str, ...], new: Tuple[str, ...], notes: Notes) -> None:
    """Move the value at path `old` to path `new` when `new` is not set yet; an `old` that is set while `new` also is stays where
    it is (the code reads `new` first, and nothing is thrown away). An emptied parent section is removed."""
    def get(path):
        node: Any = cfg
        for k in path:
            if not isinstance(node, dict) or k not in node:
                return False, None
            node = node[k]
        return True, node

    has_old, value = get(old)
    if not has_old:
        return
    has_new, _ = get(new)
    label = ".".join(old) + " -> " + ".".join(new)
    if has_new:
        notes.append(f"{label}: both are set, left alone (the new one is used)")
        return
    parent: Any = cfg
    for k in new[:-1]:
        parent = parent.setdefault(k, {})
        if not isinstance(parent, dict):
            notes.append(f"{label}: {'.'.join(new[:-1])} is not a section, left alone")
            return
    parent[new[-1]] = value
    holder: Any = cfg
    for k in old[:-1]:
        holder = holder[k]
    del holder[old[-1]]
    if len(old) > 1 and not cfg.get(old[0]):
        cfg.pop(old[0], None)
    notes.append(f"{label}: moved")


@_step
def apps_home_to_hearth(cfg: Dict[str, Any], notes: Notes) -> None:
    """`apps: ["home"]` -> `["hearth"]`: the Home app's package was renamed; the old name still loads through an alias."""
    apps = cfg.get("apps")
    if isinstance(apps, list) and "home" in apps:
        out: List[Any] = []
        for a in apps:
            a = "hearth" if a == "home" else a
            if a not in out:
                out.append(a)
        cfg["apps"] = out
        notes.append('apps: "home" renamed to "hearth"')


@_step
def garden_to_irrigation(cfg: Dict[str, Any], notes: Notes) -> None:
    """`garden` -> `irrigation`: the watering block was renamed (garden now means a long-term agent idea). Read-time alias before."""
    _move_key(cfg, ("garden",), ("irrigation",), notes)


@_step
def hass_to_homeassistant(cfg: Dict[str, Any], notes: Notes) -> None:
    """`hass` -> `homeassistant`: the Home Assistant section had two names; `homeassistant` is the one the setup wizard writes."""
    _move_key(cfg, ("hass",), ("homeassistant",), notes)


@_step
def valkey_host_to_url(cfg: Dict[str, Any], notes: Notes) -> None:
    """`cluster.valkey_host` -> `cluster.valkey_url` (a bare host is accepted in the url key: it borrows scheme and port)."""
    _move_key(cfg, ("cluster", "valkey_host"), ("cluster", "valkey_url"), notes)


@_step
def voice_host_to_legacy_host(cfg: Dict[str, Any], notes: Notes) -> None:
    """`voice.host` -> `voice.legacy_host`: it always meant the old speech server; the new name says so."""
    _move_key(cfg, ("voice", "host"), ("voice", "legacy_host"), notes)


# Released steps never change and never move: a config's number says how many of these it has been through.
MIGRATIONS: List[Tuple[str, Step]] = [
    ("apps-home-to-hearth", apps_home_to_hearth),
    ("garden-to-irrigation", garden_to_irrigation),
    ("hass-to-homeassistant", hass_to_homeassistant),
    ("valkey-host-to-url", valkey_host_to_url),
    ("voice-host-to-legacy-host", voice_host_to_legacy_host),
]
LATEST = len(MIGRATIONS)


def version_of(cfg: Dict[str, Any]) -> int:
    """The config's `config_version` (0 when absent). ConfigError when it is not a whole number >= 0."""
    v = cfg.get("config_version", 0)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ConfigError(f"config_version must be a whole number (found {v!r})")
    return v


def check_not_newer(cfg: Dict[str, Any]) -> None:
    v = version_of(cfg)
    if v > LATEST:
        raise ConfigTooNew(
            f"config.json has config_version {v}, but this StoneSage only understands up to {LATEST}. It was written by a newer "
            f"version. Upgrade StoneSage (docs/UPGRADING.md), or restore an older config from a backup. Nothing was changed.")


def migrate(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, Notes]]]:
    """(new settings, [(step name, notes)] for the steps that ran). Pure: `cfg` is not changed. Raises ConfigTooNew."""
    check_not_newer(cfg)
    current = version_of(cfg)
    new, ran = copy.deepcopy(cfg), []
    for number, (name, step) in enumerate(MIGRATIONS, start=1):
        if number <= current:
            continue
        new, notes = step(new)
        ran.append((name, notes))
    new["config_version"] = LATEST
    return new, ran


def read_config(path: str) -> Optional[Dict[str, Any]]:
    """The config file's settings; None when there is no file. ConfigError when it is not a JSON object."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path} cannot be read as JSON ({type(e).__name__})") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} is not a JSON object")
    return data


def upgrade_file(path: str, backup: Optional[Callable[[], Any]] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Bring the file at `path` up to LATEST. Returns {'status', 'steps', 'from', 'to'} where status is
    'no-config' | 'current' | 'upgraded' | 'would-upgrade' | 'backup-failed'. `backup` is called before the first change; if it
    raises, nothing is changed. Raises ConfigTooNew / ConfigError."""
    cfg = read_config(path)
    if cfg is None:
        return {"status": "no-config", "steps": [], "from": None, "to": None}
    check_not_newer(cfg)
    current = version_of(cfg)
    if current >= LATEST:
        return {"status": "current", "steps": [], "from": current, "to": current}
    new, ran = migrate(cfg)
    steps = [{"step": n, "notes": notes} for n, notes in ran]
    if dry_run:
        return {"status": "would-upgrade", "steps": steps, "from": current, "to": LATEST}
    if backup is not None:
        try:
            backup()
        except Exception as e:
            logger.error("settings upgrade %d -> %d NOT done: the backup before it failed (%s: %s). "
                         "The old settings keep working; fix the backup (disk space? permissions?) and restart.",
                         current, LATEST, type(e).__name__, e)
            return {"status": "backup-failed", "steps": [], "from": current, "to": current, "error": f"{type(e).__name__}: {e}"}
    auth.write_private_json(path, new)
    for n, notes in ran:
        logger.info("settings upgrade step %s: %s", n, "; ".join(notes) if notes else "nothing to change")
    logger.info("settings upgraded from version %d to %d", current, LATEST)
    return {"status": "upgraded", "steps": steps, "from": current, "to": LATEST}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stonesage.config_migrate", description="Upgrade config.json to the current settings version.")
    ap.add_argument("--config", default=None, help="config.json (default: $STONESAGE_CONFIG_FILE, else backend/config.json)")
    ap.add_argument("--dry-run", action="store_true", help="show the steps that would run, change nothing")
    ap.add_argument("--no-backup", action="store_true", help="skip the backup first (not recommended)")
    args = ap.parse_args(argv)
    path = args.config or auth.default_config_file()
    backup = None
    if not args.no_backup and not args.dry_run:
        from stonesage import backup as backup_mod
        loc = backup_mod.Locations.default(path)
        backup = lambda: backup_mod.create(loc, backup_mod._read_json(path), reason="before settings upgrade")
    try:
        report = upgrade_file(path, backup=backup, dry_run=args.dry_run)
    except (ConfigTooNew, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{path}: {report['status']}" + (f" (version {report['from']} -> {report['to']})" if report["from"] is not None else ""))
    for s in report["steps"]:
        print(f"  {s['step']}: " + ("; ".join(s["notes"]) if s["notes"] else "nothing to change"))
    return 1 if report["status"] == "backup-failed" else 0


if __name__ == "__main__":
    sys.exit(main())
