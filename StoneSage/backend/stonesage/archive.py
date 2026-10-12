"""Safe reading of a tar.gz that came from somewhere else (a soul export, a backup): one set of rules for what a member may be.

`MemberRules.check(member)` says whether an archive member is acceptable and returns its name; it raises the caller's own error
type (so each caller keeps its own messages and exit codes) when it is not. A member must be a plain regular file (no
directory, link, device or fifo), its name must pass the caller's `name_ok` test (soul exports allow plain names only, backups
allow `dir/dir/file` paths of plain segments; neither allows `..`, an absolute path, a drive, a backslash or a hidden segment),
no name may appear twice, and the file count, the size of one file and the total size stay under the caller's limits.
Nothing is extracted by this module: callers read each member's bytes themselves (soul.py into memory, backup.py streaming
into a staging folder) and write nothing until every check has passed.
"""

import re
import tarfile
from typing import Callable, Dict, Optional, Type

SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,99}$")         # one path segment: plain, no leading dot


def plain_path(name: str, max_depth: int = 8) -> bool:
    """`a/b/c.json`: segments of plain characters joined by '/', no '..', no hidden or empty segment, no drive or backslash."""
    if not isinstance(name, str) or not name or len(name) > 400 or "\\" in name or ":" in name:
        return False
    parts = name.split("/")
    return len(parts) <= max_depth and all(SEGMENT.match(p) and p not in (".", "..") for p in parts)


class MemberRules:
    def __init__(self, error: Type[Exception], name_ok: Callable[[str], bool], max_file: int, max_total: int, max_files: int,
                 name_hint: str = "no paths, no '..', no hidden names"):
        self.error, self.name_ok = error, name_ok
        self.max_file, self.max_total, self.max_files = max_file, max_total, max_files
        self.name_hint = name_hint
        self.seen: Dict[str, int] = {}
        self.total = 0

    def check(self, m: tarfile.TarInfo) -> str:
        name = m.name
        if not m.isreg():
            raise self.error(f"'{name}' is not a plain file (links, directories and devices are refused)")
        if not self.name_ok(name):
            raise self.error(f"'{name}' is not a plain file name ({self.name_hint})")
        if name in self.seen:
            raise self.error(f"'{name}' appears twice")
        if len(self.seen) >= self.max_files:
            raise self.error(f"more than {self.max_files} files")
        if m.size > self.max_file:
            raise self.error(f"'{name}' is over the {self.max_file // (1024 * 1024)} MB limit for one file")
        self.total += m.size
        if self.total > self.max_total:
            raise self.error(f"the archive is over the {self.max_total // (1024 * 1024)} MB limit in total")
        self.seen[name] = m.size
        return name


def read_member(tf: tarfile.TarFile, m: tarfile.TarInfo, error: Type[Exception]) -> bytes:
    """The member's whole content, read to its declared size ('cut short' when the archive ends early)."""
    f = tf.extractfile(m)
    data = f.read(m.size) if f is not None else b""
    if len(data) != m.size:
        raise error(f"'{m.name}' is cut short")
    return data
