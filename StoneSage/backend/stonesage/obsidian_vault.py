"""
Zero-Delete Tertiary Obsidian Vault Engine for StoneSage
Provides an unyielding, append-only and snapshot-protected backup vault.
Files can be created and modified, but deletions are intercepted and moved
to an immutable timestamped _archive directory to prevent data loss.
"""

import os
import sys
import shutil
import time
import subprocess
from typing import Dict, Any, List, Optional

class ObsidianVault:
    def __init__(self, vault_dir: str):
        self.vault_dir = os.path.abspath(vault_dir)
        self.archive_dir = os.path.join(self.vault_dir, "_archive")
        os.makedirs(self.vault_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)
        self._init_git()

    def _init_git(self):
        """Ensure git repository is initialized for micro-commit version history."""
        git_dir = os.path.join(self.vault_dir, ".git")
        if not os.path.exists(git_dir):
            try:
                subprocess.run(["git", "init"], cwd=self.vault_dir, capture_output=True, check=False)
                subprocess.run(["git", "config", "user.name", "StoneSage"], cwd=self.vault_dir, capture_output=True, check=False)
                subprocess.run(["git", "config", "user.email", "stonesage@homelab.local"], cwd=self.vault_dir, capture_output=True, check=False)
                # Create initial commit
                readme_path = os.path.join(self.vault_dir, "VAULT_README.md")
                if not os.path.exists(readme_path):
                    with open(readme_path, "w", encoding="utf-8") as f:
                        f.write("# StoneSage Obsidian Tertiary Vault\nProtected by Zero-Delete Archival Policy.\n")
                subprocess.run(["git", "add", "."], cwd=self.vault_dir, capture_output=True, check=False)
                subprocess.run(["git", "commit", "-m", "Initial vault creation"], cwd=self.vault_dir, capture_output=True, check=False)
            except Exception:
                pass
        else:
            try:
                subprocess.run(["git", "config", "user.name", "StoneSage"], cwd=self.vault_dir, capture_output=True, check=False)
                subprocess.run(["git", "config", "user.email", "stonesage@homelab.local"], cwd=self.vault_dir, capture_output=True, check=False)
            except Exception:
                pass

    def _git_commit(self, message: str):
        try:
            subprocess.run(["git", "add", "."], cwd=self.vault_dir, capture_output=True, check=False)
            subprocess.run(["git", "commit", "-m", message], cwd=self.vault_dir, capture_output=True, check=False)
        except Exception:
            pass

    def save_note(self, rel_path: str, content: str) -> Dict[str, Any]:
        """Save or update note. If previous version exists, back it up to _archive first."""
        clean_path = os.path.normpath(rel_path).lstrip("\\/").replace("..", "")
        if not clean_path.endswith(".md") and not clean_path.endswith(".txt"):
            clean_path += ".md"

        full_path = os.path.join(self.vault_dir, clean_path)
        obsidian_sub = os.path.join(self.vault_dir, "obsidian", clean_path)
        if not os.path.exists(full_path) and os.path.exists(obsidian_sub):
            full_path = obsidian_sub

        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        is_update = os.path.exists(full_path)
        if is_update:
            # Backup prior revision
            ts = time.strftime("%Y%m%d_%H%M%S")
            archive_filename = f"{os.path.basename(clean_path)}.{ts}.rev"
            archive_path = os.path.join(self.archive_dir, archive_filename)
            try:
                shutil.copy2(full_path, archive_path)
            except Exception:
                pass

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        action = "Updated" if is_update else "Created"
        self._git_commit(f"{action} note: {clean_path}")

        return {
            "ok": True,
            "path": clean_path,
            "action": action.lower(),
            "bytes": len(content.encode("utf-8")),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    def delete_note(self, rel_path: str) -> Dict[str, Any]:
        """
        Zero-Delete Policy:
        Files are NEVER permanently deleted. They are archived with a tombstone stamp.
        """
        clean_path = os.path.normpath(rel_path).lstrip("\\/").replace("..", "")
        full_path = os.path.join(self.vault_dir, clean_path)

        if not os.path.exists(full_path):
            return {"ok": False, "error": "File not found"}

        ts = time.strftime("%Y%m%d_%H%M%S")
        tombstone_name = f"{os.path.basename(clean_path)}.{ts}.tombstone"
        tombstone_path = os.path.join(self.archive_dir, tombstone_name)

        # Move file to archive rather than deleting
        shutil.move(full_path, tombstone_path)
        self._git_commit(f"Archived (Zero-Delete): {clean_path}")

        return {
            "ok": True,
            "deleted": False,
            "archived": True,
            "archive_location": f"_archive/{tombstone_name}",
            "message": "Zero-Delete Policy Enforced: File preserved in immutable archive."
        }

    def list_notes(self) -> List[Dict[str, Any]]:
        """List all active notes in the vault (excluding _archive and .git)."""
        notes = []
        for root, dirs, files in os.walk(self.vault_dir):
            if "_archive" in root or ".git" in root:
                continue
            for f in files:
                if f.endswith((".md", ".txt", ".canvas")):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.vault_dir)
                    stat = os.stat(full)
                    notes.append({
                        "name": f,
                        "path": rel.replace("\\", "/"),
                        "size_bytes": stat.st_size,
                        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                    })
        return sorted(notes, key=lambda x: x["modified"], reverse=True)

    def get_note(self, rel_path: str) -> Dict[str, Any]:
        """Fetch content and historical revision count for a note."""
        clean_path = os.path.normpath(rel_path).lstrip("\\/").replace("..", "")
        full_path = os.path.join(self.vault_dir, clean_path)

        if not (os.path.exists(full_path) and os.path.isfile(full_path)):
            obsidian_sub = os.path.join(self.vault_dir, "obsidian", clean_path)
            if os.path.exists(obsidian_sub) and os.path.isfile(obsidian_sub):
                full_path = obsidian_sub
            else:
                target_name = os.path.basename(clean_path).lower()
                for root, _, files in os.walk(self.vault_dir):
                    if "_archive" in root or ".git" in root:
                        continue
                    for f in files:
                        if f.lower() == target_name:
                            cand = os.path.join(root, f)
                            if os.path.isfile(cand):
                                full_path = cand
                                break
                    if os.path.exists(full_path) and os.path.isfile(full_path):
                        break

        if not (os.path.exists(full_path) and os.path.isfile(full_path)):
            return {"ok": False, "error": "Note not found"}

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Count archived revisions
        base_name = os.path.basename(clean_path)
        revisions = []
        if os.path.exists(self.archive_dir):
            for f in os.listdir(self.archive_dir):
                if f.startswith(base_name):
                    revisions.append(f)

        return {
            "ok": True,
            "path": clean_path.replace("\\", "/"),
            "content": content,
            "revisions_count": len(revisions)
        }
