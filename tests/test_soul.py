"""Soul folder (backend/soul.py): persona that follows code until edited, one-time legacy copy, no data loss."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "StoneSage", "backend"))
from stonesage.soul import Soul  # noqa: E402
from stonesage.courage import prompt  # noqa: E402


class SoulTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def open(self, persona="You are A.", **kw):
        return Soul.open(self.d, "agent-x", display_name="X", default_persona=persona, **kw)

    def test_creates_folder_manifest_and_persona(self):
        s = self.open()
        self.assertTrue(os.path.isfile(s.path("manifest.json")))
        self.assertEqual(s.persona(), "You are A.")
        self.assertEqual(json.load(open(s.path("manifest.json")))["name"], "X")

    def test_unedited_persona_follows_code(self):
        self.open("You are A.")
        s = self.open("You are B.")
        self.assertEqual(s.persona(), "You are B.")

    def test_edited_persona_is_never_overwritten(self):
        s = self.open("You are A.")
        open(s.path("persona.md"), "w", encoding="utf-8").write("You are mine.")
        s2 = self.open("You are B.")
        self.assertEqual(s2.persona(), "You are mine.")
        self.assertEqual(self.open("You are C.").persona(), "You are mine.")

    def test_persona_edit_applies_without_reopen(self):
        s = self.open()
        open(s.path("persona.md"), "w", encoding="utf-8").write("Changed.")
        self.assertEqual(s.persona(), "Changed.")

    def test_legacy_file_copied_once_and_kept(self):
        old = os.path.join(self.d, "courage_memories.json")
        open(old, "w").write('{"notes": [1]}')
        s = self.open(legacy={"memories.json": old})
        self.assertEqual(json.load(open(s.path("memories.json"))), {"notes": [1]})
        open(s.path("memories.json"), "w").write('{"notes": [1, 2]}')
        s2 = self.open(legacy={"memories.json": old})
        self.assertEqual(json.load(open(s2.path("memories.json"))), {"notes": [1, 2]})   # not clobbered
        self.assertTrue(os.path.exists(old))

    def test_prompt_uses_soul_persona_or_falls_back(self):
        self.assertTrue(prompt.build_static_prompt(None).startswith(prompt.PERSONA))
        self.assertTrue(prompt.build_static_prompt("a GPU", "Mine.").startswith("Mine."))
        self.assertIn("Mine.", prompt.build_system_prompt(None, persona="Mine."))
        self.assertNotIn(prompt.PERSONA[:30], prompt.build_system_prompt(None, persona="Mine."))


if __name__ == "__main__":
    unittest.main()
