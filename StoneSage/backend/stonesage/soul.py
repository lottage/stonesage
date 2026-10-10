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
"""

import hashlib
import json
import os
import shutil
import time
from typing import Dict, Optional

FORMAT_VERSION = 1


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
        return cls(os.path.join(data_dir, "souls", agent_id), agent_id, **kw)

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
