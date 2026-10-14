"""Backups of this install: what a person would miss if the machine died, in one archive, and the way back.
(docs/LAYERS.md, "M5: Backups, upgrades and the rest of the review")

    python -m stonesage.backup create [--out DIR] [--no-push]
    python -m stonesage.backup push [--dest NAME] [FILE]
    python -m stonesage.backup list [--dir DIR]
    python -m stonesage.backup restore FILE [--yes] [--dry-run]

The server also takes one every day (config `backup`: `enabled` true, `dir` = <data dir>/backups, `keep` 14, `hour` 3 local time),
and one before it migrates an old config (config_migrate.py). `GET /api/backup/status` and `POST /api/backup/run` (behind the
login) show and trigger it. There is deliberately NO download route: the archive holds `config.json`, i.e. the login key and every
token. Copying it off the machine (another disk, another host) is the operator's job; docs/UPGRADING.md says how, and config
`backup.destinations` can do it after every backup: a mounted folder (NAS, USB disk, a synced cloud folder) or an rclone remote
(backup_dest.py). With none configured nothing of that runs.

What goes in is the list RULES below: first match wins, every entry has a reason. Anything in the data folder that no rule names is
included up to 5 MB a file (a state file somebody added later is not forgotten); bigger unnamed files are skipped and listed in the
manifest. The archive is `manifest.json` (format version, time, versions, sha256 and size per file, which files hold secrets, what
was skipped and why) followed by the files under `config/`, `certs/` and `data/`. It is written 0600 (owner only) where the OS has
modes, atomically (a `.partial` file is renamed when complete), from a private snapshot so a file that changes while it is read
cannot make the checksum lie, and SQLite databases are copied through SQLite's own backup, not file-copied under a writer.

Restore checks the format (a newer one is refused), every checksum, and every name (plain relative paths only; no `..`, absolute
path, drive, link or device: the rules in archive.py that soul imports use too) before touching anything. Every file it would
replace is first moved aside to `<path>.pre-restore-<time>`; nothing is ever deleted, and files in the install that the archive does
not have are left alone. It refuses while the server runs unless `--yes`.
"""

import argparse
import fnmatch
import hashlib
import io
import json
import logging
import os
import pathlib
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import stonesage
from stonesage import archive, auth, backup_dest, config_migrate, paths, soul

logger = logging.getLogger("StoneSage.backup")

FORMAT_VERSION = 1                      # the archive layout (manifest "format"); a restore refuses a higher number
KIND = "stonesage-backup"
NAME_PREFIX, NAME_SUFFIX = backup_dest.NAME_PREFIX, backup_dest.NAME_SUFFIX
DEFAULT_MAX_FILE = 5 * 1024 * 1024      # per file, for anything no rule names
MAX_TOTAL_BYTES = 1024 * 1024 * 1024    # all files of one backup
MAX_FILES = 20000
RESTORE_MAX_FILE = 600 * 1024 * 1024    # a restore accepts what a backup could write (a SQLite database can be big)
DEFAULTS = {"enabled": True, "keep": 14, "hour": 3}
HARNESS_ROOT = "harness-data"           # the harness's data folder, when it is not the server's (see Locations.harness_dir)


# ---- what goes in ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    root: str                 # "config" (config.json, agents.json), "certs" (the TLS certificate folder) or "data" (the data folder)
    pattern: str              # path under the root, '/'-separated; '*' globs one segment, '**' any number; a folder name covers everything beneath
    action: str               # "include" | "exclude"
    reason: str               # why: the list is the documentation
    secret: bool = False      # the file holds secrets (flagged in the manifest)
    max_bytes: int = 0        # include only: a file over this is skipped (0 = DEFAULT_MAX_FILE)
    sqlite: bool = False      # include only: copy through SQLite's backup, not as bytes


RULES: Tuple[Rule, ...] = (
    # --- config: the two files that are not in the data folder
    Rule("config", "config.json", "include", "every setting, the login key and every token: without it a restore is a blank install", secret=True),
    Rule("config", "agents.json", "include", "agents a person defined in the cockpit"),
    # --- certs: the HTTPS listener's certificate and key
    Rule("certs", "*.pem", "include", "the HTTPS certificate and key the phones were set to trust (regenerable, but then every device must trust it again)", secret=True),
    Rule("certs", "**", "exclude", "only the .pem files of the certificate folder"),
    # --- data: never in a backup
    Rule("data", "backups", "exclude", "this is where backups are written; a backup of backups grows without end"),
    Rule("data", "**/__pycache__", "exclude", "compiled bytecode, rebuilt by Python"),
    Rule("data", "**/*.pyc", "exclude", "compiled bytecode, rebuilt by Python"),
    Rule("data", "**/*.tmp", "exclude", "a file somebody was still writing"),
    Rule("data", "**/.import-*", "exclude", "a half-finished soul import"),
    Rule("data", "**/*.pre-restore-*", "exclude", "files a restore moved aside; they are the safety copy, not state"),
    Rule("data", "**/*.pre-merge-*", "exclude", "files a harness-folder merge moved aside; the safety copy, not state"),
    Rule("data", "**/*.bak-*", "exclude", "older copies that tools set aside (an overwritten soul); the current file is what matters"),
    Rule("data", "**/*.db-wal", "exclude", "SQLite side file; the database is copied whole through SQLite's own backup"),
    Rule("data", "**/*.db-shm", "exclude", "SQLite side file; see above"),
    Rule("data", "tts_cache", "exclude", "spoken lines kept for speed; regenerated by the voice server"),
    Rule("data", "voice_samples", "exclude", "recordings of a person's voice: large and personal, not state"),
    Rule("data", "sighting_boxes.json", "exclude", "cache of where the vision model found a subject in a picture; recomputed on demand"),
    Rule("data", "engine_lease.json", "exclude", "transient: a restored lease would try to hand back engines of the machine it was taken on"),
    Rule("data", "boost_quota.json", "exclude", "today's usage counters of the free cloud sources; stale counters only mislead"),
    Rule("data", "autonomous_dossiers", "exclude", "output of the shelved thinking loop: large, regenerable, not used by anything shipped"),
    Rule("data", "training", "exclude", "datasets of the shelved trainer: large and rebuilt from their sources"),
    Rule("data", "dataset_compiler_state.json", "exclude", "state of the shelved trainer's dataset compiler"),
    Rule("data", "test_scratch", "exclude", "scratch space of tests"),
    # --- data: named, with why a person would miss them
    Rule("data", "souls", "include", "the agents: persona, memories and what they have learned (plain files)"),
    Rule("data", "courage_memories.json", "include", "Computer's notes about the people it talks to (older location; souls/ holds the current copy)"),
    Rule("data", "courage_reflexes.json", "include", "phrasings of orders Computer learned to carry out without the model"),
    Rule("data", "custom_tools.json", "include", "tools a person added through the cockpit"),
    Rule("data", "commentary_state.json", "include", "which remarks Computer already made (its daily budget)"),
    Rule("data", "patrol_state.json", "include", "when each camera was last swept"),
    Rule("data", "patrol_corrections.jsonl", "include", "what a person corrected about camera sightings: training signal you cannot recreate"),
    Rule("data", "model_provenance.json", "include", "local copy of the model catalogue with typed notes"),
    Rule("data", "user_model_profiles.json", "include", "model sampling profiles a person saved"),
    Rule("data", "profiles", "include", "the household's profile cards (people and pets)"),
    Rule("data", "agent_dna", "include", "agent DNA bundles edited in the cockpit"),
    Rule("data", "handovers", "include", "workspace handover notes", max_bytes=2 * 1024 * 1024),
    Rule("data", "irrigation_log.json", "include", "watering history"),
    Rule("data", "garden_log.json", "include", "watering history under its older name"),
    Rule("data", "edge_nodes.json", "include", "the edge machines this install knows"),
    Rule("data", "courage_trace.jsonl*", "include", "the decision trace of Computer; a file over 8 MB is skipped (it rotates at 5 MB)",
         max_bytes=8 * 1024 * 1024),
    Rule("data", "*.db", "include", "chat sessions and agent trajectories (SQLite)", sqlite=True, max_bytes=MAX_TOTAL_BYTES),
    # --- data: everything else
    Rule("data", "**", "include", "anything else a feature wrote here is assumed to be state a person would miss; over 5 MB a file it is skipped and listed"),
)


def _match_segments(pat: List[str], parts: List[str]) -> bool:
    if not pat:
        return True                                   # the pattern is used up: it named a folder (or the file itself)
    if pat[0] == "**":
        return any(_match_segments(pat[1:], parts[i:]) for i in range(len(parts) + 1))
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], pat[0]) and _match_segments(pat[1:], parts[1:])


def matches(pattern: str, rel: str) -> bool:
    """Does the pattern match the path `rel` (or a folder above it)? Segment-wise: `*` stays inside one segment."""
    return _match_segments(pattern.split("/"), rel.split("/"))


def rule_for(root: str, rel: str) -> Optional[Rule]:
    """The first rule of the root that matches (None = no rule, nothing is taken). The harness's own data folder follows the data
    rules (same kinds of files: harness.db, profile cards, agent DNA, the irrigation log)."""
    root = "data" if root == HARNESS_ROOT else root
    for r in RULES:
        if r.root == root and matches(r.pattern, rel):
            return r
    return None


# ---- where things are -------------------------------------------------------------------------------------------------
@dataclass
class Locations:
    config_file: str
    data_dir: str
    backend_dir: str                 # agents.json lives here
    root_dir: str                    # certs/ lives here
    backup_dir: str = ""
    harness_dir: str = ""            # the harness's data folder when it differs from data_dir ("" = same folder, or no harness)

    @classmethod
    def default(cls, config_file: Optional[str] = None, data_dir: Optional[str] = None) -> "Locations":
        """The same places the server uses: STONESAGE_CONFIG_FILE / STONESAGE_DATA_DIR (the tests and Docker set them), else
        the folders next to the code."""
        backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = os.path.abspath(data_dir or soul.default_data_dir())
        harness = "" if data_dir else _harness_data_dir()      # an explicit --data-dir means: that folder, nothing guessed
        return cls(config_file=os.path.abspath(config_file or auth.default_config_file()), data_dir=data,
                   backend_dir=backend, root_dir=os.path.dirname(backend),
                   harness_dir=harness if harness and os.path.isdir(harness)
                   and os.path.normcase(harness) != os.path.normcase(data) else "")

    def target(self, arc_name: str) -> str:
        """Where an archive member goes on disk, or ValueError when its name is not one this install restores to."""
        top, _, rel = arc_name.partition("/")
        if top == "config":
            if rel == "config.json":
                return self.config_file
            if rel == "agents.json":
                return os.path.join(self.backend_dir, "agents.json")
            raise ValueError(f"'{arc_name}' is not a config file this install knows")
        if top == "certs" and rel and "/" not in rel:
            return paths.safe_join(os.path.join(self.root_dir, "certs"), rel)
        if top == "data" and rel:
            return paths.safe_join(self.data_dir, rel)
        if top == HARNESS_ROOT and rel:      # an install where both share one folder (Docker) takes them into the data folder
            return paths.safe_join(self.harness_dir or self.data_dir, rel)
        raise ValueError(f"'{arc_name}' is not a place this install restores to")


def _harness_data_dir() -> str:
    """The harness's OLD data folder (`data` next to the harness package) that an install from before 2026-10-12 may still use
    (harness/core/datadir.py), or "" when there can be none (STONESAGE_DATA_DIR set: the harness uses the data folder). Located
    without importing the harness (the platform layer does not depend on it)."""
    if os.environ.get("STONESAGE_DATA_DIR"):
        return ""                     # the harness uses the data folder itself: one folder
    try:
        import importlib.util
        spec = importlib.util.find_spec("harness")
    except (ImportError, ValueError):
        return ""
    locs = list(spec.submodule_search_locations or []) if spec else []
    return os.path.abspath(os.path.join(os.path.dirname(locs[0]), "data")) if locs else ""


def backup_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """config `backup` with the defaults filled in and nonsense replaced by the default."""
    sec = (config or {}).get("backup")
    sec = sec if isinstance(sec, dict) else {}
    keep, hour = sec.get("keep"), sec.get("hour")
    return {"enabled": sec.get("enabled") is not False,
            "dir": str(sec.get("dir") or "").strip(),
            "keep": keep if isinstance(keep, int) and not isinstance(keep, bool) and keep >= 1 else DEFAULTS["keep"],
            "hour": hour if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23 else DEFAULTS["hour"]}


def backup_dir_for(loc: Locations, config: Optional[Dict[str, Any]]) -> str:
    return os.path.abspath(backup_settings(config)["dir"] or loc.backup_dir or os.path.join(loc.data_dir, "backups"))


# ---- creating --------------------------------------------------------------------------------------------------------
@dataclass
class Candidate:
    arc: str
    src: str
    rule: Rule


def _walk(root: str, base: str) -> Iterator[Tuple[str, str, bool]]:
    """(relative path, absolute path, is_link) of every file under base. Folders a rule excludes are not entered; links are
    reported (so they can be listed as skipped), never followed."""
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            r = rule_for(root, rel)
            if os.path.islink(os.path.join(dirpath, d)):
                yield rel, os.path.join(dirpath, d), True
            elif not (r and r.action == "exclude"):
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            yield (f"{rel_dir}/{f}" if rel_dir else f), p, os.path.islink(p)


def plan(loc: Locations) -> Tuple[List[Candidate], List[Dict[str, str]]]:
    """(files to back up, skipped: [{'path','reason'}]). Nothing is read here but directory listings."""
    cands: List[Candidate] = []
    skipped: List[Dict[str, str]] = []

    def consider(root: str, rel: str, src: str, is_link: bool) -> None:
        r = rule_for(root, rel)
        arc = f"{root}/{rel}"
        if r is None or r.action == "exclude":
            return
        if not archive.plain_path(arc):
            skipped.append({"path": arc, "reason": "the file name has characters a restore would refuse (a plain name is letters, digits, . _ -)"})
        elif is_link:
            skipped.append({"path": arc, "reason": "a link (links are never followed or stored)"})
        elif os.path.isfile(src):
            cands.append(Candidate(arc, src, r))

    for name in ("config.json", "agents.json"):
        src = loc.config_file if name == "config.json" else os.path.join(loc.backend_dir, name)
        if os.path.lexists(src):
            consider("config", name, src, os.path.islink(src))
    certs = os.path.join(loc.root_dir, "certs")
    if os.path.isdir(certs):
        for rel, src, link in _walk("certs", certs):
            consider("certs", rel, src, link)
    if os.path.isdir(loc.data_dir):
        for rel, src, link in _walk("data", loc.data_dir):
            consider("data", rel, src, link)
    if loc.harness_dir and os.path.isdir(loc.harness_dir):
        for rel, src, link in _walk(HARNESS_ROOT, loc.harness_dir):
            consider(HARNESS_ROOT, rel, src, link)
    return cands, skipped


def _sha256_file(path: str) -> Tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _snapshot_sqlite(src: str, dst: str) -> None:
    """A consistent copy of a SQLite database even while something writes to it."""
    s = sqlite3.connect(pathlib.Path(src).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


@dataclass
class BackupResult:
    path: str
    size: int
    files: int
    skipped: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    created: float = 0.0
    pushes: List[Dict[str, Any]] = field(default_factory=list)      # one row per off-box destination tried (empty when none)


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _private_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def create(loc: Locations, config: Optional[Dict[str, Any]] = None, out_dir: Optional[str] = None, keep: Optional[int] = None,
           now: Optional[float] = None, reason: str = "manual", push: Any = False) -> BackupResult:
    """Write one backup archive. Returns where it went; raises OSError when it could not be written at all.
    A single unreadable file is a recorded error in the result, not a failed backup. `push` copies it to the configured off-box
    destinations afterwards (True = now, in this thread; "background" = in a thread of its own, for callers that must not wait);
    a destination's failure is recorded in the result and never raises."""
    now = time.time() if now is None else now
    config = config if config is not None else _read_json(loc.config_file)
    out = os.path.abspath(out_dir or backup_dir_for(loc, config))
    if keep is None and out_dir is None:                  # old ones are pruned only in the managed folder, never in one the operator chose
        keep = backup_settings(config)["keep"]
    _private_dir(out)
    cands, skipped = plan(loc)
    errors: List[str] = []
    entries: Dict[str, Dict[str, Any]] = {}
    stage = tempfile.mkdtemp(prefix=".stage-", dir=out)             # a private snapshot; mkdtemp is 0700
    partial = ""
    try:
        total = 0
        for i, c in enumerate(sorted(cands, key=lambda c: c.arc)):
            cap = c.rule.max_bytes or DEFAULT_MAX_FILE
            snap = os.path.join(stage, f"{i}.bin")
            try:
                if c.rule.sqlite:
                    _snapshot_sqlite(c.src, snap)
                else:
                    size = os.path.getsize(c.src)
                    if size > cap:
                        skipped.append({"path": c.arc, "reason": f"over {cap // (1024 * 1024)} MB (the limit for this file)"})
                        continue
                    shutil.copyfile(c.src, snap)
                digest, size = _sha256_file(snap)
                if size > cap and not c.rule.sqlite:                     # grew while it was copied
                    skipped.append({"path": c.arc, "reason": f"over {cap // (1024 * 1024)} MB (the limit for this file)"})
                    os.unlink(snap)
                    continue
                if total + size > MAX_TOTAL_BYTES or len(entries) >= MAX_FILES:
                    skipped.append({"path": c.arc, "reason": "the backup is at its size limit"})
                    os.unlink(snap)
                    continue
            except (OSError, sqlite3.Error) as e:
                errors.append(f"{c.arc}: {type(e).__name__}")
                continue
            total += size
            entries[c.arc] = {"sha256": digest, "size": size, "secret": c.rule.secret, "_snap": snap,
                              "_mtime": int(_safe_mtime(c.src))}
        manifest = {
            "kind": KIND, "format": FORMAT_VERSION, "created": round(now, 3),
            "created_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": reason, "stonesage_version": stonesage.__version__, "api_version": stonesage.API_VERSION,
            "config_version": (config or {}).get("config_version"), "config_version_latest": config_migrate.LATEST,
            "files": {a: {k: v for k, v in e.items() if not k.startswith("_")} for a, e in sorted(entries.items())},
            "secret_files": sorted(a for a, e in entries.items() if e["secret"]),
            "skipped": skipped, "errors": errors,
        }
        name = f"{NAME_PREFIX}{_stamp(now)}{NAME_SUFFIX}"
        final, n = os.path.join(out, name), 1
        while os.path.exists(final):
            n += 1
            final = os.path.join(out, f"{NAME_PREFIX}{_stamp(now)}-{n}{NAME_SUFFIX}")
        partial = final + ".partial"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)       # owner only from the first byte
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tf:
            body = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            _add_bytes(tf, "manifest.json", body, now, 0o600)
            for arc, e in sorted(entries.items()):
                info = tarfile.TarInfo(arc)
                info.size, info.mtime, info.mode = e["size"], e["_mtime"], 0o600 if e["secret"] else 0o644
                with open(e["_snap"], "rb") as f:
                    tf.addfile(info, f)
        try:
            os.chmod(partial, 0o600)
        except OSError:
            pass
        os.replace(partial, final)
        partial = ""
        size = os.path.getsize(final)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass
    if keep:
        _rotate(out, keep, final)
    logger.info("backup written: %s (%d files, %d KB%s)", final, len(entries), max(1, size // 1024),
                f", {len(skipped)} skipped" if skipped else "")
    for s in skipped:
        logger.info("backup skipped %s: %s", s["path"], s["reason"])
    result = BackupResult(final, size, len(entries), skipped, errors, now)
    if push and backup_dest.has_work(config):
        if push == "background":
            threading.Thread(target=_push_quietly, args=(final, loc, config), daemon=True, name="backup-push").start()
        else:
            result.pushes = _push_quietly(final, loc, config)
    return result


def push_to_destinations(archive_path: str, loc: Locations, config: Optional[Dict[str, Any]], only: str = "",
                         runner: Optional[backup_dest.Runner] = None, which: Optional[Callable[[str], Optional[str]]] = None,
                         clock: Callable[[], float] = time.time) -> List[Dict[str, Any]]:
    """Copy one existing archive to the off-box destinations (all enabled ones, or just `only`). Each row says ok or why not;
    ValueError when `only` is unknown or the file is not named like a backup."""
    state = os.path.join(backup_dir_for(loc, config), backup_dest.STATE_NAME)
    return backup_dest.Pusher(state, runner, which, clock).push(archive_path, config, only)


def _push_quietly(archive_path: str, loc: Locations, config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return push_to_destinations(archive_path, loc, config)
    except Exception as e:                       # the local backup is made and stays; this is only the extra copy
        logger.error("backup copy to the off-box destinations failed: %s: %s", type(e).__name__, e)
        return [{"name": "(all)", "ok": False, "error": f"{type(e).__name__}: {e}"}]


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return time.time()


def _add_bytes(tf: tarfile.TarFile, name: str, data: bytes, mtime: float, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(mtime), mode
    tf.addfile(info, io.BytesIO(data))


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_backups(folder: str) -> List[str]:
    """Backup archives in the folder, oldest first (only names this module writes)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    ok = [n for n in names if n.startswith(NAME_PREFIX) and n.endswith(NAME_SUFFIX)]
    return [os.path.join(folder, n) for n in sorted(ok)]


def _rotate(folder: str, keep: int, newest: str) -> None:
    """Keep the newest `keep` backups; older ones are removed (this is the retention the operator configured, and the only
    place a backup is ever deleted)."""
    found = list_backups(folder)
    for old in found[:-keep] if keep < len(found) else []:
        if os.path.abspath(old) != os.path.abspath(newest):
            try:
                os.unlink(old)
                logger.info("old backup removed (keep=%d): %s", keep, os.path.basename(old))
            except OSError as e:
                logger.warning("could not remove old backup %s: %s", os.path.basename(old), e)


def read_manifest(archive_path: str) -> Dict[str, Any]:
    """The manifest of a backup (the first member), or ValueError saying why this is not one."""
    try:
        with tarfile.open(archive_path, mode="r:gz") as tf:
            m = tf.next()
            if m is None or m.name != "manifest.json" or not m.isreg() or m.size > 8 * 1024 * 1024:
                raise ValueError("not a StoneSage backup (no manifest first)")
            manifest = json.loads(archive.read_member(tf, m, ValueError).decode("utf-8-sig"))
    except (tarfile.TarError, OSError, EOFError, UnicodeError) as e:
        raise ValueError(f"not a readable backup ({type(e).__name__})")
    except json.JSONDecodeError:
        raise ValueError("the manifest is not valid JSON")
    if not isinstance(manifest, dict) or manifest.get("kind") != KIND:
        raise ValueError("not a StoneSage backup")
    return manifest


# ---- restoring -------------------------------------------------------------------------------------------------------
class RestoreError(ValueError):
    """A restore that was refused; nothing on disk was changed (the message says why and is safe to show)."""


@dataclass
class RestoreReport:
    replaced: List[str] = field(default_factory=list)       # files that existed and were moved aside first
    created: List[str] = field(default_factory=list)        # files that did not exist
    moved_aside: List[str] = field(default_factory=list)    # where the replaced ones went
    skipped: List[str] = field(default_factory=list)        # members not restored, with why
    applied: bool = False


def server_running(config: Optional[Dict[str, Any]], port: Optional[int] = None) -> bool:
    """Is a StoneSage server answering on this machine (a TCP connect to its port on localhost)?"""
    if port is None:
        try:
            port = int(((config or {}).get("server") or {}).get("port", 8888))
        except (TypeError, ValueError):
            port = 8888
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _unique_aside(path: str, stamp: str) -> str:
    aside, n = f"{path}.pre-restore-{stamp}", 1
    while os.path.lexists(aside):
        n += 1
        aside = f"{path}.pre-restore-{stamp}-{n}"
    return aside


def restore(archive_path: str, loc: Locations, config: Optional[Dict[str, Any]] = None, yes: bool = False, dry_run: bool = False,
            running: Optional[Callable[[], bool]] = None, now: Optional[float] = None, prefer: str = "data") -> RestoreReport:
    """Put a backup back. Raises RestoreError, having changed nothing, when the archive is not acceptable or the server runs
    and `yes` was not given. With dry_run only the plan is returned. `prefer` ("data" | "harness") picks which copy goes in
    when a backup of a two-folder install is restored into a one-folder install and both folders had the file."""
    if prefer not in ("data", "harness"):
        raise RestoreError("--prefer must be data or harness")
    now = time.time() if now is None else now
    stage = tempfile.mkdtemp(prefix=".restore-")
    try:
        files = _stage_archive(archive_path, stage)
        try:
            with open(files.pop("manifest.json"), encoding="utf-8-sig") as f:
                manifest = json.load(f)
        except ValueError:
            raise RestoreError("manifest.json is not valid JSON")
        _check_manifest(manifest, files)
        targets, claimed = {}, {}
        report = RestoreReport()
        order = sorted(files, key=lambda a: (a.startswith(HARNESS_ROOT + "/") != (prefer == "harness"), a))
        for arc in order:                    # the preferred folder's copy claims a shared place first
            try:
                dest = loc.target(arc)
            except ValueError as e:
                raise RestoreError(str(e))
            key = os.path.normcase(os.path.abspath(dest))
            if key in claimed:
                report.skipped.append(f"{arc}: same place as {claimed[key]} in this install (one shared data folder); that one is restored")
                continue
            claimed[key] = arc
            targets[arc] = dest
        for arc, dest in targets.items():
            if os.path.lexists(dest):
                if os.path.islink(dest) or not os.path.isfile(dest):
                    raise RestoreError(f"'{arc}' would replace something that is not a plain file ({dest}); move it away first")
                report.replaced.append(dest)
            else:
                report.created.append(dest)
        if dry_run:
            return report
        is_up = running if running is not None else (lambda: server_running(config))
        if not yes and is_up():
            raise RestoreError("the StoneSage server is running: stop it first, or pass --yes to restore underneath it. "
                               f"{len(report.replaced)} file(s) would be replaced (each moved aside first), "
                               f"{len(report.created)} created.")
        _apply(files, targets, manifest, report, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
        report.applied = True
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _stage_archive(archive_path: str, stage: str) -> Dict[str, str]:
    """Read every member into the staging folder under checked names; {archive name: staged path}."""
    rules = archive.MemberRules(RestoreError, archive.plain_path, RESTORE_MAX_FILE, MAX_TOTAL_BYTES + 16 * 1024 * 1024, MAX_FILES + 1,
                                name_hint="plain relative paths only: no '..', no absolute path or drive, no hidden names")
    staged: Dict[str, str] = {}
    try:
        with tarfile.open(archive_path, mode="r:gz") as tf:
            for i, m in enumerate(tf):
                name = rules.check(m)
                dst = os.path.join(stage, f"{i}.bin")
                src = tf.extractfile(m)
                written = 0
                with open(dst, "wb") as out:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        out.write(chunk)
                        written += len(chunk)
                if written != m.size:
                    raise RestoreError(f"'{name}' is cut short")
                staged[name] = dst
    except RestoreError:
        raise
    except (tarfile.TarError, OSError, EOFError, UnicodeError) as e:
        raise RestoreError(f"not a readable backup ({type(e).__name__})")
    if "manifest.json" not in staged:
        raise RestoreError("the archive has no manifest.json: it is not a StoneSage backup")
    return staged


def _check_manifest(manifest: Any, files: Dict[str, str]) -> None:
    if not isinstance(manifest, dict) or manifest.get("kind") != KIND:
        raise RestoreError("not a StoneSage backup (the manifest is not one)")
    fmt = manifest.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise RestoreError("the manifest has no format number")
    if fmt > FORMAT_VERSION:
        raise RestoreError(f"this backup has format {fmt}, made by a newer StoneSage; this version reads up to format "
                           f"{FORMAT_VERSION}. Upgrade StoneSage first (docs/UPGRADING.md).")
    if fmt < 1:
        raise RestoreError(f"unknown backup format {fmt}")
    cv = manifest.get("config_version")
    if isinstance(cv, int) and not isinstance(cv, bool) and cv > config_migrate.LATEST:
        raise RestoreError(f"the settings in this backup are version {cv}, newer than this StoneSage understands "
                           f"({config_migrate.LATEST}). Upgrade StoneSage first (docs/UPGRADING.md).")
    listed = manifest.get("files")
    if not isinstance(listed, dict) or set(listed) != set(files):
        raise RestoreError("the file list in the manifest does not match the archive")
    for arc, meta in listed.items():
        digest, size = _sha256_file(files[arc])
        if not isinstance(meta, dict) or meta.get("sha256") != digest or meta.get("size") != size:
            raise RestoreError(f"checksum mismatch for '{arc}': the backup is damaged")


def _apply(files: Dict[str, str], targets: Dict[str, str], manifest: Dict[str, Any], report: RestoreReport, stamp: str) -> None:
    """Stage every new file next to its destination first (a failure here changes nothing), then for each: move the old one
    aside and put the new one in place. A failure part-way puts the moved-aside files back."""
    prepared: List[Tuple[str, str, bool]] = []                 # (dest, temp copy next to dest, secret)
    done: List[Tuple[str, Optional[str], List[Tuple[str, str]]]] = []      # (dest, aside or None, sidecars moved)
    try:
        for arc, src in files.items():
            if arc not in targets:                            # skipped: another member restores to the same place
                continue
            dest = targets[arc]
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".restore-tmp"
            secret = bool((manifest["files"].get(arc) or {}).get("secret"))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
                shutil.copyfileobj(f, out)
            if not secret:
                try:
                    os.chmod(tmp, 0o644)
                except OSError:
                    pass
            prepared.append((dest, tmp, secret))
        for dest, tmp, _secret in prepared:
            aside: Optional[str] = None
            sidecars: List[Tuple[str, str]] = []
            if os.path.lexists(dest):
                aside = _unique_aside(dest, stamp)
                os.replace(dest, aside)
            if dest.endswith(".db"):                              # a leftover write-ahead log would be applied to the restored database
                for suffix in ("-wal", "-shm"):
                    if os.path.lexists(dest + suffix):
                        side = _unique_aside(dest + suffix, stamp)
                        os.replace(dest + suffix, side)
                        sidecars.append((dest + suffix, side))
            done.append((dest, aside, sidecars))
            os.replace(tmp, dest)
            if aside:
                report.moved_aside.append(aside)
    except BaseException:
        for dest, aside, sidecars in reversed(done):             # put back what was moved aside
            try:
                if aside:
                    os.replace(aside, dest)
                elif os.path.exists(dest):
                    os.unlink(dest)
                for orig, side in sidecars:
                    os.replace(side, orig)
            except OSError:
                logger.error("restore: could not put %s back after a failure; the safety copy is next to it (.pre-restore-*)",
                             os.path.basename(dest))
        raise
    finally:
        for _dest, tmp, _s in prepared:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---- the daily service -------------------------------------------------------------------------------------------------
class BackupService:
    """The daily backup and what the status route shows. `start()` runs the thread (the server does that at start-up; the
    CLI and tests call create/run_now directly)."""

    CATCH_UP_DELAY_S = 300           # after a start, a missed backup is made 5 minutes in, not during boot

    def __init__(self, loc: Locations, config_fn: Callable[[], Dict[str, Any]], clock: Callable[[], float] = time.time,
                 tz_fn: Optional[Callable[[], Any]] = None, dest_runner: Optional[backup_dest.Runner] = None,
                 dest_which: Optional[Callable[[str], Optional[str]]] = None):
        self.loc, self.config_fn, self.clock = loc, config_fn, clock
        self._dest_runner, self._dest_which = dest_runner, dest_which      # test seams: a fake rclone
        self._tz_fn = tz_fn
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error = ""
        self.last_attempt = 0.0
        self.last_result: Optional[BackupResult] = None

    def settings(self) -> Dict[str, Any]:
        return backup_settings(self.config_fn())

    def folder(self) -> str:
        return backup_dir_for(self.loc, self.config_fn())

    def _tz(self):
        if self._tz_fn:
            return self._tz_fn()
        from lodestone import site
        return site.local_tz(self.config_fn())

    def next_run(self, after: Optional[float] = None) -> Optional[float]:
        """The next time of day `hour` in the install's zone, strictly after `after` (None when automatic backups are off)."""
        s = self.settings()
        if not s["enabled"]:
            return None
        after = self.clock() if after is None else after
        tz = self._tz()
        local = datetime.fromtimestamp(after, tz)
        target = local.replace(hour=s["hour"], minute=0, second=0, microsecond=0)
        if target <= local:
            tomorrow = local.date() + timedelta(days=1)
            target = datetime.combine(tomorrow, dtime(s["hour"], 0), tzinfo=tz)
        return target.timestamp()

    def _push(self, path: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return push_to_destinations(path, self.loc, cfg, runner=self._dest_runner, which=self._dest_which, clock=self.clock)
        except Exception as e:
            logger.error("backup copy to the off-box destinations failed: %s: %s", type(e).__name__, e)
            return [{"name": "(all)", "ok": False, "error": f"{type(e).__name__}: {e}"}]

    def run_now(self, reason: str = "manual", background_push: bool = False) -> Dict[str, Any]:
        """Take a backup now, then copy it to the off-box destinations if there are any (in this thread, or with
        `background_push` in one of its own so a request does not wait). Never raises: the result says ok or why not (the status
        shows the last error); a destination that fails never makes the backup fail."""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "a backup is already running"}
        try:
            self.last_attempt = self.clock()
            try:
                res = create(self.loc, self.config_fn(), now=self.last_attempt, reason=reason)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error("backup failed: %s", self.last_error)
                return {"ok": False, "error": self.last_error}
            self.last_result = res
            self.last_error = "; ".join(res.errors[:5]) if res.errors else ""
            out = {"ok": True, "file": os.path.basename(res.path), "size": res.size, "files": res.files,
                   "skipped": len(res.skipped), "errors": res.errors}
            cfg = self.config_fn()
            if backup_dest.has_work(cfg):
                if background_push:
                    threading.Thread(target=self._push, args=(res.path, cfg), daemon=True, name="backup-push").start()
                    out["pushing"] = True
                else:
                    out["destinations"] = self._push(res.path, cfg)
            return out
        finally:
            self._lock.release()

    def status(self) -> Dict[str, Any]:
        s = self.settings()
        found = list_backups(self.folder())
        last: Dict[str, Any] = {}
        if found:
            p = found[-1]
            try:
                last = {"file": os.path.basename(p), "size": os.path.getsize(p), "time": round(os.path.getmtime(p), 3)}
                man = read_manifest(p)
                last.update(files=len(man.get("files") or {}), skipped=len(man.get("skipped") or []),
                            errors=list(man.get("errors") or []), stonesage_version=man.get("stonesage_version"))
            except (OSError, ValueError):
                last["error"] = "the newest backup cannot be read"
        nxt = self.next_run()
        return {"ok": True, "enabled": s["enabled"], "dir": self.folder(), "keep": s["keep"], "hour": s["hour"],
                "count": len(found), "last": last or None, "last_error": self.last_error or None,
                "last_attempt": self.last_attempt or None, "next_run": round(nxt, 3) if nxt else None,
                "running": self._lock.locked(), "pushing": backup_dest.is_pushing(),
                "destinations": backup_dest.Pusher(os.path.join(self.folder(), backup_dest.STATE_NAME)).status(self.config_fn()),
                "note": "Backups hold config.json (the login key and every token). Copy them off this machine yourself; there is no download."}

    # -- the thread
    def start(self) -> Optional[threading.Thread]:
        if not self.settings()["enabled"]:
            logger.info("automatic backups are off (config backup.enabled is false)")
            return None
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="backup-daily")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _newest_time(self) -> float:
        found = list_backups(self.folder())
        try:
            return os.path.getmtime(found[-1]) if found else 0.0
        except OSError:
            return 0.0

    def _loop(self) -> None:
        wait = self.CATCH_UP_DELAY_S if self.clock() - self._newest_time() > 25 * 3600 else None
        if wait is not None and not self._stop.wait(wait):
            self.run_now("catch-up after start")
        while not self._stop.is_set():
            nxt = self.next_run()
            if nxt is None:
                if self._stop.wait(3600):
                    return
                continue
            delay = max(1.0, nxt - self.clock())
            if self._stop.wait(min(delay, 3600)):
                return
            if self.clock() >= nxt - 1:
                self.run_now("daily")


# ---- one data folder --------------------------------------------------------------------------------------------------
# A native install from before 2026-10-12 has two data folders: the server's, and the harness's old <checkout>/data (harness.db,
# agent DNA, profile cards, the irrigation log). merge-folders copies the old folder's files into the data folder, asks which copy
# to keep when both have a file and they differ, and only then renames the old folder to `<name>.merged-<time>` (kept, never
# deleted). Until that rename the harness keeps reading the old folder (harness/core/datadir.py), so a merge that stops part-way
# changes nothing anybody reads.
PREFER = ("newer", "data", "harness")
MERGE_SKIP = ("**/__pycache__", "**/*.pyc", "**/*.tmp", "**/*.db-wal", "**/*.db-shm", "backups")


@dataclass
class Duplicate:
    rel: str
    data_path: str                    # the copy in the data folder
    harness_path: str                 # the copy in the old harness folder
    choice: str = ""                  # "data" | "harness"

    def info(self, which: str) -> Tuple[int, float]:
        p = self.data_path if which == "data" else self.harness_path
        return os.path.getsize(p), os.path.getmtime(p)

    def newer(self) -> str:
        return "harness" if self.info("harness")[1] > self.info("data")[1] else "data"


@dataclass
class MergeReport:
    copied: List[str] = field(default_factory=list)          # only in the old folder: now in the data folder too
    identical: List[str] = field(default_factory=list)       # same bytes in both: nothing to do
    duplicates: List[Duplicate] = field(default_factory=list)
    moved_aside: List[str] = field(default_factory=list)     # data-folder copies replaced by the harness copy
    backup: str = ""
    old_folder_now: str = ""
    applied: bool = False


def _same_file(a: str, b: str) -> bool:
    return os.path.getsize(a) == os.path.getsize(b) and _sha256_file(a)[0] == _sha256_file(b)[0]


def merge_plan(loc: Locations) -> MergeReport:
    """What merge-folders would do. Reads directory listings and compares file contents; changes nothing."""
    rep = MergeReport()
    if not loc.harness_dir or not os.path.isdir(loc.harness_dir):
        return rep
    for dirpath, dirnames, filenames in os.walk(loc.harness_dir, followlinks=False):
        rel_dir = os.path.relpath(dirpath, loc.harness_dir).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in sorted(dirnames) if not os.path.islink(os.path.join(dirpath, d))
                       and not any(matches(p, f"{rel_dir}/{d}" if rel_dir else d) for p in MERGE_SKIP)]
        for f in sorted(filenames):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            src = os.path.join(dirpath, f)
            if os.path.islink(src) or any(matches(p, rel) for p in MERGE_SKIP):
                continue
            dest = paths.safe_join(loc.data_dir, rel)
            if not os.path.exists(dest):
                rep.copied.append(rel)
            elif _same_file(src, dest):
                rep.identical.append(rel)
            else:
                rep.duplicates.append(Duplicate(rel, dest, src))
    return rep


def _copy_into(src: str, dest: str) -> None:
    """Copy one file into place atomically; a SQLite database through SQLite's backup (it takes its -wal along)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".merge-tmp"
    if src.endswith(".db"):
        _snapshot_sqlite(src, tmp)
        shutil.copymode(src, tmp)
    else:
        shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def merge_folders(loc: Locations, config: Optional[Dict[str, Any]] = None, prefer: str = "",
                  ask: Optional[Callable[[Duplicate], str]] = None, dry_run: bool = False,
                  running: Optional[Callable[[], bool]] = None, now: Optional[float] = None) -> MergeReport:
    """Make the data folder the only one. Each duplicate that differs is settled by `prefer` (newer | data | harness), else by
    `ask(duplicate)` -> "data" | "harness", else the merge is refused, listing them. A backup of both folders is taken first."""
    if prefer and prefer not in PREFER:
        raise RestoreError(f"--prefer must be one of {', '.join(PREFER)}")
    rep = merge_plan(loc)
    if not loc.harness_dir or not os.path.isdir(loc.harness_dir):
        raise RestoreError("there is one data folder already: nothing to merge")
    for d in rep.duplicates:
        if prefer:
            d.choice = d.newer() if prefer == "newer" else prefer
        elif ask:
            d.choice = ask(d)
        if d.choice not in ("data", "harness"):
            names = ", ".join(x.rel for x in rep.duplicates)
            raise RestoreError(f"these files are in both folders and differ: {names}. Run it in a terminal to choose each one, "
                               "or pass --prefer newer|data|harness")
    if dry_run:
        return rep
    is_up = running if running is not None else (lambda: server_running(config))
    if is_up():
        raise RestoreError("the StoneSage server is running: stop it first (harness.db is open while it runs)")
    rep.backup = create(loc, config, reason="before-merge", push=True).path
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    for rel in rep.copied:
        _copy_into(os.path.join(loc.harness_dir, *rel.split("/")), paths.safe_join(loc.data_dir, rel))
    for d in rep.duplicates:
        if d.choice == "harness":
            aside = _unique_aside(d.data_path, stamp).replace(".pre-restore-", ".pre-merge-")
            os.replace(d.data_path, aside)
            rep.moved_aside.append(aside)
            _copy_into(d.harness_path, d.data_path)
    rep.old_folder_now = f"{loc.harness_dir.rstrip(os.sep)}.merged-{stamp}"
    os.replace(loc.harness_dir, rep.old_folder_now)        # last: until now the harness still read the old folder
    rep.applied = True
    logger.info("data folders merged into %s (%d copied, %d identical, %d duplicates settled); the old folder is now %s",
                loc.data_dir, len(rep.copied), len(rep.identical), len(rep.duplicates), rep.old_folder_now)
    return rep


def _print_pushes(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        if r["ok"]:
            print(f"  copied to '{r['name']}'" + (f" ({r['warning']})" if r.get("warning") else ""))
        else:
            print(f"  NOT copied to '{r['name']}': {r['error']}")


def _ask_in_terminal(d: Duplicate) -> str:
    print(f"\n{d.rel} is in both folders and they differ:")
    newer = d.newer()
    for n, which, path in ((1, "data", d.data_path), (2, "harness", d.harness_path)):
        size, mtime = d.info(which)
        tag = "  (newer)" if which == newer else ""
        print(f"  [{n}] {path}\n      {size} bytes, changed {datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}{tag}")
    while True:
        ans = input("Keep which one? [1/2] ").strip()
        if ans in ("1", "2"):
            return "data" if ans == "1" else "harness"


# ---- command line -----------------------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stonesage.backup", description="Back up and restore this StoneSage install.")
    ap.add_argument("--config", default=None, help="config.json (default: $STONESAGE_CONFIG_FILE, else backend/config.json)")
    ap.add_argument("--data-dir", default=None, help="data folder (default: $STONESAGE_DATA_DIR, else StoneSage/data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="write a backup now")
    c.add_argument("--out", default=None, help="folder for the archive (default: config backup.dir, else <data dir>/backups)")
    c.add_argument("--no-push", action="store_true", help="do not copy it to the off-box destinations (config backup.destinations)")
    pu = sub.add_parser("push", help="copy an existing backup to the off-box destinations (retry; default: the newest backup)")
    pu.add_argument("file", nargs="?", default=None)
    pu.add_argument("--dest", default="", help="only this destination (its name in config backup.destinations)")
    ls = sub.add_parser("list", help="list the backups in the folder")
    ls.add_argument("--dir", default=None)
    r = sub.add_parser("restore", help="put a backup back (every replaced file is moved aside first)")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="restore even though the server is running")
    r.add_argument("--dry-run", action="store_true", help="only print what would be replaced")
    r.add_argument("--prefer", choices=("data", "harness"), default="data",
                   help="a backup of a two-folder install restored into one folder: which copy of a file both had goes in")
    m = sub.add_parser("merge-folders", help="move the harness's old data folder into the data folder (one folder for everything)")
    m.add_argument("--prefer", choices=PREFER, default="",
                   help="settle every file both folders have by the newer copy, the data folder's or the harness folder's "
                        "(default: ask for each one; required when not run in a terminal)")
    m.add_argument("--dry-run", action="store_true", help="only print what would happen")
    args = ap.parse_args(argv)
    loc = Locations.default(args.config, args.data_dir)
    config = _read_json(loc.config_file)
    try:
        if args.cmd == "create":
            res = create(loc, config, out_dir=args.out, push=not args.no_push)
            print(f"wrote {res.path} ({res.files} files, {res.size // 1024} KB)")
            print("This archive contains config.json: the login key and every token. Keep it private and copy it off this machine.")
            for s in res.skipped:
                print(f"  skipped {s['path']}: {s['reason']}")
            for e in res.errors:
                print(f"  error {e}")
            _print_pushes(res.pushes)
            return 1 if res.errors or any(not p["ok"] for p in res.pushes) else 0
        if args.cmd == "push":
            target = args.file
            if not target:
                found = list_backups(backup_dir_for(loc, config))
                if not found:
                    raise ValueError(f"no backups in {backup_dir_for(loc, config)}; pass the file to copy")
                target = found[-1]
            if not os.path.isfile(target):
                raise ValueError(f"{target} is not a file")
            read_manifest(target)                                    # ValueError unless it really is a StoneSage backup
            if not backup_dest.load_destinations(config):
                raise ValueError("no destinations are configured (config backup.destinations)")
            rows = push_to_destinations(target, loc, config, only=args.dest)
            print(f"copying {os.path.basename(target)}")
            _print_pushes(rows)
            return 0 if rows and all(r["ok"] for r in rows) else 1
        if args.cmd == "list":
            folder = args.dir or backup_dir_for(loc, config)
            found = list_backups(folder)
            if not found:
                print(f"no backups in {folder}")
            for p in found:
                try:
                    m = read_manifest(p)
                    flag = " (holds secrets)" if m.get("secret_files") else ""
                    print(f"{os.path.basename(p)}\t{m.get('created_utc', '?')}\t{os.path.getsize(p) // 1024} KB\t"
                          f"{len(m.get('files') or {})} files\tv{m.get('stonesage_version', '?')}{flag}")
                except ValueError as e:
                    print(f"{os.path.basename(p)}\tunreadable: {e}")
            return 0
        if args.cmd == "merge-folders":
            ask = _ask_in_terminal if (not args.prefer and sys.stdin.isatty()) else None
            mr = merge_folders(loc, config, prefer=args.prefer, ask=ask, dry_run=args.dry_run)
            pre = "would " if args.dry_run else ""
            print(f"{pre}copy {len(mr.copied)} file(s) from {loc.harness_dir} into {loc.data_dir}; {len(mr.identical)} already identical")
            for d in mr.duplicates:
                print(f"  {d.rel}: {pre}keep the {d.choice} folder's copy")
            for a in mr.moved_aside:
                print(f"  replaced copy kept as {a}")
            if not args.dry_run:
                print(f"backup taken first: {mr.backup}")
                print(f"the old folder is now {mr.old_folder_now} (nothing deleted). Start the server again.")
            return 0
        rep = restore(args.file, loc, config, yes=args.yes, dry_run=args.dry_run, prefer=args.prefer)
        verb = "would replace" if args.dry_run else "replaced"
        for p in rep.replaced:
            print(f"  {verb}: {p}" + ("" if args.dry_run else "  (the old one is kept next to it as .pre-restore-*)"))
        for p in rep.created:
            print(f"  {'would create' if args.dry_run else 'created'}: {p}")
        for s in rep.skipped:
            print(f"  skipped: {s}")
        print("dry run: nothing was changed" if args.dry_run else "restored. Start the server again.")
        return 0
    except RestoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
