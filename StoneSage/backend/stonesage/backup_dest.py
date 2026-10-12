"""Off-box copies of backups: where a finished backup is sent after it is written. (docs/LAYERS.md, "M5 ... Off-box copies")

Optional and off by default. With no `backup.destinations` in the settings nothing in this module runs. Each destination is one entry:

    {"name": "nas",   "type": "folder", "path": "/mnt/nas/stonesage"}                    # any mounted folder
    {"name": "cloud", "type": "rclone", "remote": "secret-drive:StoneSage"}              # an rclone remote you set up with `rclone config`

plus `keep` (copies kept there; default `backup.keep`), `enabled` (default true), `timeout_s` (rclone, default 1800), `rclone_path`
(the program, also allowed once under `backup`), `require_encryption` and `allow_unencrypted` (below).

folder   The path must already be an existing folder: StoneSage never creates it, so a share that is not mounted cannot quietly fill
         the local disk. The archive is written as `<name>.partial` (0600) and renamed when complete. A synced Google Drive /
         Dropbox / OneDrive desktop-app folder counts, as does a NAS mount or a USB disk.
rclone   `rclone copyto -- <file> <remote>/<name>`, then `rclone lsf` / `rclone deletefile` to drop the old ones. rclone speaks
         Google Drive, Dropbox, OneDrive, S3, SFTP, WebDAV and many more and does the OAuth logins itself, so StoneSage never
         holds a cloud password. The program is run with an argument list (no shell) and nothing is read from rclone's config.

The archive holds config.json (the login key and every token), so an unencrypted copy is readable by whoever holds the cloud
account. `require_encryption` therefore defaults to true for rclone: the upload is refused unless the remote's type (from
`rclone listremotes --long`, which prints names and types, never settings) is `crypt`, or the entry says `"allow_unencrypted":
true` (a warning is logged on every run). A folder has no encryption of its own; set `require_encryption` true there to insist
on an rclone crypt remote that points at the folder.

Only files named like a backup (`stonesage-backup-<date>T<time>Z[-n].tar.gz`) are ever pruned at a destination. A failure here
never fails or removes the local backup; each destination's last result is kept in `<backup folder>/destinations-state.json`
(times, file names and a short error; no settings) and shown in `GET /api/backup/status`. There is no download: restoring from a
destination means copying the file back yourself and running `restore` (docs/UPGRADING.md).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("StoneSage.backup")

NAME_PREFIX, NAME_SUFFIX = "stonesage-backup-", ".tar.gz"
_NAME_RE = re.compile(r"^stonesage-backup-(\d{8}T\d{6}Z)(?:-(\d+))?\.tar\.gz$")
STATE_NAME = "destinations-state.json"
TYPES = ("folder", "rclone")
DEFAULT_KEEP = 14
DEFAULT_TIMEOUT_S = 1800.0
MAX_ERROR_CHARS = 450
RCLONE_HELP = "install rclone: https://rclone.org/install/ (then set up a remote with `rclone config`)"
_DEST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
_REMOTE_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_. -]+):(.*)$", re.S)        # "name:path"; 2+ chars so C:\x is a drive, not a remote

_PUSH_LOCK = threading.Lock()          # one push at a time, whoever started it (the daily thread, a request's helper, the CLI)

Runner = Callable[[List[str], float], Tuple[int, str, str]]


class DestinationError(Exception):
    """A destination that could not be written; the message is short and safe to show."""


def is_backup_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def sort_key(name: str) -> Tuple[str, int]:
    """Oldest first: the time stamp, then the same-second counter (the first of a second has none)."""
    m = _NAME_RE.match(name)
    return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)


# ---- settings -------------------------------------------------------------------------------------------------------------
@dataclass
class Destination:
    name: str
    type: str = ""
    enabled: bool = True
    keep: int = DEFAULT_KEEP
    path: str = ""
    remote: str = ""
    rclone: str = "rclone"
    timeout_s: float = DEFAULT_TIMEOUT_S
    require_encryption: bool = False
    allow_unencrypted: bool = False
    error: str = ""                    # why the entry is unusable (it is skipped; the others still run)

    @property
    def target(self) -> str:
        return self.path if self.type == "folder" else self.remote


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _clean_text(v: Any) -> str:
    return v.strip() if isinstance(v, str) and not any(ord(c) < 32 for c in v) else ""


def load_destinations(config: Optional[Dict[str, Any]]) -> List[Destination]:
    """The `backup.destinations` of the settings, validated one by one: a bad entry comes back with `error` set (and is skipped
    when pushing), the rest are unaffected. No destinations (absent, null, empty) = an empty list."""
    sec = (config or {}).get("backup")
    sec = sec if isinstance(sec, dict) else {}
    raw = sec.get("destinations")
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        return [Destination("destinations", error="backup.destinations must be a list of destinations")]
    default_keep = sec.get("keep") if _is_int(sec.get("keep")) and sec.get("keep") >= 1 else DEFAULT_KEEP
    shared_rclone = _clean_text(sec.get("rclone_path")) or "rclone"
    out: List[Destination] = []
    seen = set()
    for i, e in enumerate(raw):
        label = f"destination {i + 1}"
        if not isinstance(e, dict):
            out.append(Destination(label, error="each destination must be an object like {\"name\": ..., \"type\": ...}"))
            continue
        name = e.get("name")
        d = Destination(name if isinstance(name, str) and _DEST_NAME_RE.match(name) else label)
        out.append(d)
        problems: List[str] = []
        if d.name != name:
            problems.append("name must be 1-40 letters, digits, spaces, '.', '_' or '-'")
        elif d.name in seen:
            problems.append("this name is used by another destination")
        seen.add(d.name)
        d.type = e.get("type") if isinstance(e.get("type"), str) else ""
        if d.type not in TYPES:
            problems.append(f"type must be one of: {', '.join(TYPES)}")
        enabled = e.get("enabled", True)
        if not isinstance(enabled, bool):
            problems.append("enabled must be true or false")
        else:
            d.enabled = enabled
        keep = e.get("keep", default_keep)
        if not _is_int(keep) or keep < 1:
            problems.append("keep must be a whole number of 1 or more")
        else:
            d.keep = keep
        timeout = e.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 86400:
            problems.append("timeout_s must be a number of seconds between 1 and 86400")
        else:
            d.timeout_s = float(timeout)
        d.require_encryption = d.type == "rclone"
        for key in ("require_encryption", "allow_unencrypted"):
            if key in e:
                if not isinstance(e[key], bool):
                    problems.append(f"{key} must be true or false")
                else:
                    setattr(d, key, e[key])
        if d.type == "folder":
            p = _clean_text(e.get("path"))
            p = os.path.expanduser(p) if p else p
            if not p:
                problems.append("a folder destination needs \"path\" (a folder that is already there, e.g. a mounted share)")
            elif not os.path.isabs(p):
                problems.append("path must be an absolute path")
            d.path = p
        elif d.type == "rclone":
            r = _clean_text(e.get("remote"))
            m = _REMOTE_RE.match(r)
            if not r:
                problems.append("an rclone destination needs \"remote\" (remote name and folder, e.g. \"mydrive:StoneSage\")")
            elif not m:
                problems.append("remote must look like name:folder, with a remote you set up in `rclone config` "
                                "(connection strings and local paths are not accepted)")
            else:
                d.remote = r
            d.rclone = _clean_text(e.get("rclone_path")) or shared_rclone
        d.error = "; ".join(problems)
    return out


# ---- running things ---------------------------------------------------------------------------------------------------------
def run_program(argv: List[str], timeout: float) -> Tuple[int, str, str]:
    """(exit code, stdout, stderr). An argument list, no shell; nothing is read from stdin so rclone can never wait for a prompt."""
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, shell=False)
    return p.returncode, p.stdout or "", p.stderr or ""


def _short(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[:limit] + "..."


def remote_join(remote: str, name: str) -> str:
    return remote + name if remote.endswith(":") else remote.rstrip("/") + "/" + name


def parse_remote_types(listing: str) -> Dict[str, str]:
    """`rclone listremotes --long` -> {remote name: type}. One line per remote: `name:  type`."""
    out: Dict[str, str] = {}
    for line in listing.splitlines():
        name, sep, rest = line.partition(":")
        if sep and name.strip() and rest.strip():
            out[name.strip()] = rest.strip().split()[0].lower()
    return out


class Pusher:
    """Sends one archive to the destinations and keeps each one's last result. `runner` and `which` are the seams the tests use
    instead of a real rclone."""

    def __init__(self, state_path: str = "", runner: Optional[Runner] = None, which: Optional[Callable[[str], Optional[str]]] = None,
                 clock: Callable[[], float] = time.time):
        self.state_path = state_path
        self.runner = runner or run_program
        self.which = which or shutil.which
        self.clock = clock

    # -- state
    def read_state(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.state_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            recs = data.get("destinations") if isinstance(data, dict) else None
            return {k: v for k, v in recs.items() if isinstance(v, dict)} if isinstance(recs, dict) else {}
        except (OSError, ValueError):
            return {}

    def _record(self, name: str, **fields: Any) -> None:
        if not self.state_path:
            return
        state = self.read_state()
        state.setdefault(name, {}).update(fields)
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".dest-state-", dir=os.path.dirname(self.state_path))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"destinations": state}, f, indent=1, sort_keys=True)
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.warning("backup destinations: could not save the last results (%s)", type(e).__name__)

    def status(self, config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """One row per configured destination for the status route. Only whitelisted fields: never the settings themselves."""
        state = self.read_state()
        rows = []
        for d in load_destinations(config):
            s = state.get(d.name, {}) if not d.error else {}
            rows.append({"name": d.name, "type": d.type or None, "target": d.target or None, "enabled": d.enabled,
                         "keep": d.keep, "require_encryption": d.require_encryption, "allow_unencrypted": d.allow_unencrypted,
                         "settings_error": d.error or None, "last_attempt": s.get("last_attempt"), "last_ok": s.get("last_ok"),
                         "last_file": s.get("last_file"), "last_error": s.get("last_error"), "last_warning": s.get("last_warning")})
        return rows

    # -- pushing
    def push(self, archive_path: str, config: Optional[Dict[str, Any]], only: str = "") -> List[Dict[str, Any]]:
        """Copy the archive to every enabled destination (or just `only`), one after the other. Never raises for a destination's
        failure: each row of the result says ok or why not. ValueError when the file is not a backup name or `only` is unknown."""
        name = os.path.basename(archive_path)
        if not is_backup_name(name):
            raise ValueError(f"'{name}' is not named like a backup (stonesage-backup-<date>T<time>Z.tar.gz)")
        dests = load_destinations(config)
        if only and only not in [d.name for d in dests]:
            raise ValueError(f"no destination named '{only}' (configured: {', '.join(d.name for d in dests) or 'none'})")
        results: List[Dict[str, Any]] = []
        with _PUSH_LOCK:
            for d in dests:
                if only and d.name != only:
                    continue
                if d.error:
                    logger.error("backup destination '%s' skipped: %s", d.name, d.error)
                    results.append({"name": d.name, "ok": False, "error": "settings: " + d.error})
                    continue
                if not d.enabled:
                    if only:
                        results.append({"name": d.name, "ok": False, "error": "this destination is switched off (enabled: false)"})
                    continue
                results.append(self._one(d, archive_path, name))
        return results

    def _one(self, d: Destination, archive_path: str, name: str) -> Dict[str, Any]:
        started = self.clock()
        warning = ""
        try:
            if not os.path.isfile(archive_path):
                raise DestinationError("the backup file is not there")
            warning = self._folder(d, archive_path, name) if d.type == "folder" else self._rclone(d, archive_path, name)
        except DestinationError as e:
            err = _short(str(e))
        except OSError as e:
            err = _short(f"{type(e).__name__}: {e}")
        except Exception as e:                                   # a destination must never take the backup down
            err = _short(f"{type(e).__name__}: {e}")
        else:
            err = ""
        if err:
            logger.error("backup copy to '%s' failed: %s (the local backup is kept)", d.name, err)
            self._record(d.name, last_attempt=round(started, 3), last_error=err)
            return {"name": d.name, "ok": False, "error": err}
        logger.info("backup copied to '%s': %s%s", d.name, name, f" (warning: {warning})" if warning else "")
        self._record(d.name, last_attempt=round(started, 3), last_ok=round(self.clock(), 3), last_file=name, last_error=None,
                     last_warning=warning or None)
        row: Dict[str, Any] = {"name": d.name, "ok": True, "file": name}
        if warning:
            row["warning"] = warning
        return row

    # -- folder
    def _folder(self, d: Destination, archive_path: str, name: str) -> str:
        if d.require_encryption and not d.allow_unencrypted:
            raise DestinationError("refused: require_encryption is on and a plain folder has no encryption. Point an rclone "
                                   "`crypt` remote at the folder instead, or switch require_encryption off for this destination")
        if not os.path.isdir(d.path):
            raise DestinationError(f"{d.path} is not an existing folder (is the share or disk mounted?). StoneSage does not create it")
        same = os.path.normcase(os.path.realpath(d.path)) == os.path.normcase(os.path.realpath(os.path.dirname(archive_path)))
        if same:
            raise DestinationError("this destination is the folder the backups are written to; pick another folder")
        final = os.path.join(d.path, name)
        part = final + ".partial"
        size = os.path.getsize(archive_path)
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as out, open(archive_path, "rb") as src:
                shutil.copyfileobj(src, out, 1024 * 1024)
                out.flush()
                try:
                    os.fsync(out.fileno())
                except OSError:
                    pass                                          # some network mounts refuse; the size check below still runs
            if os.path.getsize(part) != size:
                raise DestinationError("the copy came out a different size than the backup")
            try:
                os.chmod(part, 0o600)
            except OSError:
                pass                                              # a share that has no modes
            os.replace(part, final)
        except BaseException:
            try:
                os.unlink(part)
            except OSError:
                pass
            raise
        return self._prune_folder(d, name)

    def _prune_folder(self, d: Destination, keep_name: str) -> str:
        try:
            found = [n for n in os.listdir(d.path) if is_backup_name(n) and os.path.isfile(os.path.join(d.path, n))
                     and not os.path.islink(os.path.join(d.path, n))]
        except OSError as e:
            return f"copied, but the old copies could not be listed ({type(e).__name__})"
        ordered = sorted(found, key=sort_key)
        failed = 0
        for old in ordered[:-d.keep]:
            if old == keep_name:
                continue
            try:
                os.unlink(os.path.join(d.path, old))
                logger.info("old backup removed from '%s' (keep=%d): %s", d.name, d.keep, old)
            except OSError:
                failed += 1
        return f"copied, but {failed} old copy(ies) could not be removed" if failed else ""

    # -- rclone
    def _rclone(self, d: Destination, archive_path: str, name: str) -> str:
        exe = self.which(d.rclone)
        if not exe:
            raise DestinationError(f"rclone was not found ('{d.rclone}'): {RCLONE_HELP}")
        remote_name = d.remote.partition(":")[0]
        if d.require_encryption and not d.allow_unencrypted:
            kind = self._remote_type(exe, remote_name, d)
            if kind != "crypt":
                raise DestinationError(
                    f"refused: remote '{remote_name}' is type '{kind or 'unknown'}', not crypt, and the backup holds your login key "
                    "and tokens. Make a crypt remote over it (`rclone config`, type crypt) and use that, or set "
                    "\"allow_unencrypted\": true on this destination if you accept that the cloud account can read it")
        if d.allow_unencrypted or not d.require_encryption:
            logger.warning("backup destination '%s': the archive (login key and tokens inside) is sent to '%s' without StoneSage-side "
                           "encryption; use an rclone crypt remote to protect it", d.name, remote_name)
        self._run([exe, "copyto", "--", archive_path, remote_join(d.remote, name)], d, "upload")
        return self._prune_rclone(exe, d, name)

    def _run(self, argv: List[str], d: Destination, what: str) -> str:
        try:
            code, out, err = self.runner(argv, d.timeout_s)
        except subprocess.TimeoutExpired:
            raise DestinationError(f"rclone {what} did not finish within {int(d.timeout_s)} s (timeout_s)")
        except FileNotFoundError:
            raise DestinationError(f"rclone could not be started: {RCLONE_HELP}")
        if code != 0:
            raise DestinationError(f"rclone {what} failed (exit {code}): {_short(err or out, 200) or 'no message'}")
        return out

    def _remote_type(self, exe: str, remote_name: str, d: Destination) -> str:
        try:
            listing = self._run([exe, "listremotes", "--long"], d, "check of the remote's type")
        except DestinationError as e:
            raise DestinationError(f"could not tell what type remote '{remote_name}' is: {e}")
        kinds = parse_remote_types(listing)
        if remote_name not in kinds:
            raise DestinationError(f"rclone has no remote named '{remote_name}' (see `rclone listremotes`; set one up with `rclone config`)")
        return kinds[remote_name]

    def _prune_rclone(self, exe: str, d: Destination, keep_name: str) -> str:
        try:
            listing = self._run([exe, "lsf", "--files-only", "--", d.remote], d, "listing")
            names = sorted({ln.strip() for ln in listing.splitlines() if is_backup_name(ln.strip())}, key=sort_key)
        except DestinationError as e:
            return f"uploaded, but old copies were not pruned: {e}"
        failed = 0
        for old in names[:-d.keep]:
            if old == keep_name:
                continue
            try:
                self._run([exe, "deletefile", "--", remote_join(d.remote, old)], d, "delete of an old copy")
                logger.info("old backup removed from '%s' (keep=%d): %s", d.name, d.keep, old)
            except DestinationError:
                failed += 1
        return f"uploaded, but {failed} old copy(ies) could not be removed" if failed else ""


def is_pushing() -> bool:
    return _PUSH_LOCK.locked()


def has_work(config: Optional[Dict[str, Any]]) -> bool:
    """Is there any destination to try (enabled, or broken enough to be worth a logged line)?"""
    return any(d.enabled or d.error for d in load_destinations(config))
