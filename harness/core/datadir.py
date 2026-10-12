"""Where the harness keeps its state files: the same data folder as the server, so one folder holds everything (backups,
Docker volumes, moving a machine).

The data folder is STONESAGE_DATA_DIR (the Docker image sets /data, a volume), else the server's own default
(StoneSage/data next to the backend: stonesage/soul.py default_data_dir), else, for a harness running without the server
next to it, <checkout>/data. Until 2026-10-12 the harness always used <checkout>/data when STONESAGE_DATA_DIR was unset, which
on a native install is NOT the server's folder: two data folders. An existing install is not disturbed: a file that is not in
the data folder yet while the old place has it keeps being used there, until `python -m stonesage.backup merge-folders`
moves the old folder's files over (asking which copy to keep when both have one)."""

import importlib.util
import os

LEGACY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))


def _server_data_dir() -> str:
    """The server's default data folder, found without importing the server (the harness does not depend on it)."""
    try:
        spec = importlib.util.find_spec("stonesage")
    except (ImportError, ValueError):
        return ""
    locs = list(spec.submodule_search_locations or []) if spec else []
    return os.path.abspath(os.path.join(locs[0], "..", "..", "data")) if locs else ""


def data_dir() -> str:
    env = os.environ.get("STONESAGE_DATA_DIR")
    return os.path.abspath(env) if env else (_server_data_dir() or LEGACY_DIR)


def resolve(*parts: str) -> str:
    """The path of a state file (or folder) under the data folder, or in the legacy place for one an older install keeps there.
    With STONESAGE_DATA_DIR set (Docker), the data folder wins and the legacy place is only a fallback, as before. Without it (a
    native install), the legacy folder is what the harness always used, so a file it has still wins there: nothing changes which
    copy is read until merge-folders moves the folder over (and renames the old one away). New files go to the data folder."""
    wanted = os.path.join(data_dir(), *parts)
    legacy = os.path.join(LEGACY_DIR, *parts)
    if wanted == legacy:
        return wanted
    if os.environ.get("STONESAGE_DATA_DIR"):
        return legacy if not os.path.exists(wanted) and os.path.exists(legacy) else wanted
    return legacy if os.path.exists(legacy) else wanted
