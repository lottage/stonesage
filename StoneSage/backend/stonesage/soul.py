"""
A soul: everything that makes an agent that agent, kept in one local folder, independent of whichever model answers.
(Roadmap Phase 11, step 1, 2026-10-03.)

    data/souls/<agent_id>/
        manifest.json    id, display name, format version, created, persona seed hash
        persona.md       who the agent is (the system prompt's first part)
        memories.json    notes about the people it talks to (courage/memory.py writes it)
        (later steps)    commitments.json, threads.json, goals.json, journal.jsonl

The model is a stateless brain: it is handed a slice of the soul each turn and never owns a copy. Plain files, so a soul
can be backed up, versioned or copied to another machine as a folder. Nothing here costs VRAM.

persona.md follows the code until a person edits it: while the file still matches the text this code seeded (hash in
the manifest), a newer persona in code replaces it on open; once edited it is the soul's own and code never overwrites it.

Portable (Phase 11 step 5, 2026-10-11): `Soul.export` / `import_soul` move a soul between machines as one tar.gz (manifest.json
with an `export` block of sha256 sums, plus the folder's top-level files). Import is safe by construction: plain file names
only, no links or devices, size caps, checksums, a known format; an existing soul is moved aside, never deleted. The manifest
may carry `tools` (an allowlist that can only narrow what the platform grants the agent) and `brain` (an endpoint role).
Everything in a soul stays readable with a text editor, and nothing in it is written by a remote model.

    python -m stonesage.soul list | export <id> <file> | import <file> [--as <id>] [--overwrite]
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
from typing import Any, BinaryIO, Dict, List, Optional, Union

from stonesage.archive import MemberRules, read_member

FORMAT_VERSION = 1            # the soul folder's layout (manifest "format")
ARCHIVE_FORMAT = 1            # the export archive's layout (manifest copy "export": {"format": ...})
AGENT_ID = re.compile(r"^[a-z0-9-]{1,64}$")
MAX_FILE_BYTES = 20 * 1024 * 1024       # one file in an imported archive
MAX_TOTAL_BYTES = 50 * 1024 * 1024      # all files together
MAX_FILES = 64
_FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")   # a plain name: no path, no leading dot


class SoulError(ValueError):
    """An archive or id that import/export refuses (the message says why, and is safe to show a caller)."""


class SoulExists(SoulError):
    """The soul being imported already exists and overwrite was not asked for."""

    def __init__(self, message: str, agent_id: str = ""):
        super().__init__(message)
        self.agent_id = agent_id


def check_agent_id(agent_id: Any) -> str:
    if not isinstance(agent_id, str) or not AGENT_ID.match(agent_id):
        raise SoulError("an agent id is 1-64 characters of a-z, 0-9 and '-'")
    return agent_id


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


class Soul:
    def __init__(self, root: str, agent_id: str, display_name: str = "", default_persona: str = "",
                 legacy: Optional[Dict[str, str]] = None, clock=time.time):
        """legacy: {soul file name: absolute path of the file it replaces}; copied in once, the old file left as a backup."""
        self.root, self.id = root, agent_id
        self._default_persona = default_persona
        os.makedirs(root, exist_ok=True)
        self.manifest = self._load_manifest()
        if not self.manifest:
            self.manifest = {"id": agent_id, "name": display_name or agent_id, "format": FORMAT_VERSION,
                             "created": round(clock(), 3)}
        elif display_name and self.manifest.get("name") != display_name:
            self.manifest["name"] = display_name
        for name, old in (legacy or {}).items():
            new = self.path(name)
            if not os.path.exists(new) and os.path.exists(old):
                shutil.copy2(old, new)
        self._seed_persona()
        self._save_manifest()

    @classmethod
    def open(cls, data_dir: str, agent_id: str, **kw) -> "Soul":
        return cls(os.path.join(data_dir, "souls", check_agent_id(agent_id)), agent_id, **kw)

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    # ---- manifest -----------------------------------------------------------------------
    def _load_manifest(self) -> Dict:
        try:
            with open(self.path("manifest.json"), encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_manifest(self) -> None:
        _write(self.path("manifest.json"), json.dumps(self.manifest, indent=2, ensure_ascii=False) + "\n")

    # ---- persona ----------------------------------------------------------------------------
    def _seed_persona(self) -> None:
        if not self._default_persona:
            return
        p = self.path("persona.md")
        seed_hash = _sha(self._default_persona)
        try:
            with open(p, encoding="utf-8-sig") as f:
                current = f.read()
        except OSError:
            current = None
        untouched = current is None or _sha(current) == self.manifest.get("persona_seed")
        if untouched and current != self._default_persona:
            _write(p, self._default_persona)
        if untouched:
            self.manifest["persona_seed"] = seed_hash

    def persona(self) -> str:
        """The persona as the file has it now (re-read each call, so an edit takes effect on the next turn)."""
        try:
            with open(self.path("persona.md"), encoding="utf-8-sig") as f:
                text = f.read().strip()
            if text:
                return text
        except OSError:
            pass
        return self._default_persona

    # ---- export ---------------------------------------------------------------------------
    def export(self, dest: Union[str, BinaryIO]) -> Dict[str, Any]:
        """Write this soul as one tar.gz (a path or a binary file object). Returns the manifest copy that went in."""
        return _write_archive(self.root, dest)


# ---- archives -----------------------------------------------------------------------------------------
def souls_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "souls")


def default_data_dir() -> str:
    """Where StoneSage keeps its data: STONESAGE_DATA_DIR, else StoneSage/data next to the backend (same as server.py)."""
    return os.environ.get("STONESAGE_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")


def _read_manifest(folder: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_archive(root: str, dest: Union[str, BinaryIO]) -> Dict[str, Any]:
    """manifest.json (with an `export` block: format, exported-at, sha256 per file) plus every regular file at the top
    level of the folder, in name order. A soul folder with no manifest.json yet gets one made from its folder name."""
    files: Dict[str, bytes] = {}
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if os.path.islink(full) or not os.path.isfile(full) or name.endswith(".tmp") or not _FILE_NAME.match(name):
            continue
        with open(full, "rb") as f:
            files[name] = f.read()
    manifest = _read_manifest(root) or {"id": os.path.basename(os.path.normpath(root)), "format": FORMAT_VERSION}
    files.pop("manifest.json", None)
    manifest = dict(manifest, export={
        "format": ARCHIVE_FORMAT, "exported_at": round(time.time(), 3),
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}})
    entries = {"manifest.json": (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), **files}
    mtime = int(manifest["export"]["exported_at"])
    target = {"fileobj": dest} if hasattr(dest, "write") else {"name": dest}
    with tarfile.open(mode="w:gz", **target) as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), mtime, 0o644
            tf.addfile(info, io.BytesIO(data))
    return manifest


def export_soul(data_dir: str, agent_id: str, dest: Union[str, BinaryIO]) -> Dict[str, Any]:
    root = os.path.join(souls_dir(data_dir), check_agent_id(agent_id))
    if not os.path.isdir(root):
        raise SoulError(f"no soul '{agent_id}'")
    return _write_archive(root, dest)


def _read_archive(archive: Union[str, BinaryIO]) -> Dict[str, bytes]:
    """Every member of the archive as {name: bytes}, or SoulError. Only plain regular files with plain names get through
    (no paths, no '..', no links, devices or directories), within the size and count caps, each read to its declared size.
    The member rules are the shared ones (stonesage/archive.py); backups use the same."""
    files: Dict[str, bytes] = {}
    rules = MemberRules(SoulError, _FILE_NAME.match, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_FILES)
    source = {"fileobj": archive} if hasattr(archive, "read") else {"name": archive}
    try:
        with tarfile.open(mode="r:gz", **source) as tf:
            for m in tf:
                files[rules.check(m)] = read_member(tf, m, SoulError)
    except SoulError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError, UnicodeError) as e:
        raise SoulError(f"not a readable soul archive ({type(e).__name__})")
    return files


def _check_manifest(manifest: Dict[str, Any], files: Dict[str, bytes]) -> None:
    fmt = manifest.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt != FORMAT_VERSION:
        raise SoulError(f"unknown soul format {fmt!r} (this version reads format {FORMAT_VERSION})")
    meta = manifest.get("export")
    if not isinstance(meta, dict) or meta.get("format") != ARCHIVE_FORMAT or not isinstance(meta.get("files"), dict):
        raise SoulError("not a soul export (the manifest has no export block this version reads)")
    sums = meta["files"]
    if set(sums) != set(files) - {"manifest.json"}:
        raise SoulError("the file list in the manifest does not match the archive")
    for name, want in sums.items():
        if hashlib.sha256(files[name]).hexdigest() != want:
            raise SoulError(f"checksum mismatch for '{name}'")
    tools = manifest.get("tools")
    if tools is not None and not (isinstance(tools, list) and len(tools) <= 32
                                  and all(isinstance(t, str) and re.match(r"^[a-z][a-z0-9_]{0,39}$", t) for t in tools)):
        raise SoulError("manifest 'tools' must be a list of tool names")
    brain = manifest.get("brain")
    if brain is not None and not (isinstance(brain, str) and re.match(r"^[a-z][a-z0-9_-]{0,39}$", brain)):
        raise SoulError("manifest 'brain' must be an endpoint role name")


def import_soul(data_dir: str, archive_path: Union[str, BinaryIO], agent_id: Optional[str] = None,
                overwrite: bool = False) -> Dict[str, Any]:
    """Install an exported soul under data_dir/souls/<id>. `agent_id` renames it. An existing soul is refused unless
    overwrite=True, and then moved to souls/<id>.bak-<timestamp> first (never deleted). Nothing is written to the soul's
    place until the whole archive has passed every check. Returns the stored manifest."""
    files = _read_archive(archive_path)
    if "manifest.json" not in files:
        raise SoulError("the archive has no manifest.json")
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8-sig"))
    except ValueError:
        raise SoulError("manifest.json is not valid JSON")
    if not isinstance(manifest, dict):
        raise SoulError("manifest.json is not an object")
    _check_manifest(manifest, files)
    new_id = check_agent_id(agent_id if agent_id is not None else manifest.get("id"))
    stored = {k: v for k, v in manifest.items() if k != "export"}
    stored.update(id=new_id, imported_at=round(time.time(), 3))
    if new_id != manifest.get("id"):
        stored["renamed_from"] = manifest.get("id")
    base = souls_dir(data_dir)
    dest = os.path.join(base, new_id)
    os.makedirs(base, exist_ok=True)
    if os.path.lexists(dest) and not overwrite:
        raise SoulExists(f"soul '{new_id}' already exists (overwrite to replace it; the old one is kept aside).", new_id)
    stage = tempfile.mkdtemp(prefix=".import-", dir=base)
    try:
        for name, data in files.items():
            if name != "manifest.json":
                with open(os.path.join(stage, name), "wb") as f:
                    f.write(data)
        with open(os.path.join(stage, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
        if os.path.lexists(dest):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            aside, n = f"{dest}.bak-{stamp}", 1
            while os.path.lexists(aside):
                n += 1
                aside = f"{dest}.bak-{stamp}-{n}"
            os.replace(dest, aside)
        os.replace(stage, dest)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return stored


def list_souls(data_dir: str) -> List[Dict[str, Any]]:
    """The manifest of every soul folder, by id (backups `<id>.bak-...` and half-imported folders are not souls)."""
    base = souls_dir(data_dir)
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        folder = os.path.join(base, name)
        if AGENT_ID.match(name) and os.path.isdir(folder) and not os.path.islink(folder):
            manifest = _read_manifest(folder)
            if manifest:
                out.append(dict(manifest, id=name))
    return out


# ---- command line -----------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stonesage.soul", description="List, export and import agent souls.")
    ap.add_argument("--data-dir", default=None, help="StoneSage data folder (default: $STONESAGE_DATA_DIR, else StoneSage/data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the souls here")
    ex = sub.add_parser("export", help="write one soul to a .tar.gz")
    ex.add_argument("id")
    ex.add_argument("file")
    im = sub.add_parser("import", help="install a soul from a .tar.gz")
    im.add_argument("file")
    im.add_argument("--as", dest="as_id", default=None, help="store it under this agent id")
    im.add_argument("--overwrite", action="store_true", help="replace an existing soul (the old one is kept aside)")
    args = ap.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    try:
        if args.cmd == "list":
            for m in list_souls(data_dir):
                print(f"{m['id']}\t{m.get('name', '')}\tformat {m.get('format', '?')}")
        elif args.cmd == "export":
            export_soul(data_dir, args.id, args.file)
            print(f"wrote {args.file}")
        else:
            m = import_soul(data_dir, args.file, agent_id=args.as_id, overwrite=args.overwrite)
            print(f"imported soul '{m['id']}'")
    except SoulError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
