"""One way to turn a client-supplied path into a file under a folder you chose: realpath containment.

`..`, absolute paths, a drive prefix, a NUL byte and a symlink that points out of the folder all end the same way: the path
is refused (ValueError), never quietly cleaned up and used. Every route that takes a path from the browser goes through
`safe_join` (one root) or `contained_in_any` (several, e.g. the configured workspace roots)."""

import os
from typing import Iterable, Optional


def _inside(real: str, root_real: str) -> bool:
    root_real = root_real.rstrip("/\\") or root_real
    return real == root_real or real.startswith(root_real + os.sep)


def safe_join(root: str, rel: str) -> str:
    """root + rel as a real path inside root. ValueError when rel is absolute, has a drive, a NUL, or resolves (through
    `..` or a symlink) to anywhere else."""
    rel = str(rel or "")
    if "\0" in rel:
        raise ValueError("bad path")
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0] or rel.startswith(("/", "\\")):
        raise ValueError("path must be relative to the folder")
    root_real = os.path.realpath(root)
    real = os.path.realpath(os.path.join(root_real, rel))
    if not _inside(real, root_real):
        raise ValueError("path is outside the folder")
    return real


def contained_in_any(path: str, roots: Iterable[str], cwd: Optional[str] = None) -> str:
    """The real path of `path` (relative paths are taken from `cwd`, else the first root) when it lies inside one of the
    roots; ValueError otherwise (also when there are no roots at all)."""
    path = str(path or "")
    if "\0" in path:
        raise ValueError("bad path")
    real_roots = [os.path.realpath(r) for r in roots if r]
    if not real_roots:
        raise ValueError("no workspace folder is configured")
    base = os.path.realpath(cwd) if cwd else real_roots[0]
    real = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    if not any(_inside(real, r) for r in real_roots):
        raise ValueError("path is outside the workspace folders")
    return real
