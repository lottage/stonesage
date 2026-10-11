"""Soul folder (backend/soul.py): persona that follows code until edited, one-time legacy copy, no data loss."""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "StoneSage", "backend"))
from stonesage import soul as soul_mod  # noqa: E402
from stonesage.soul import Soul, SoulError, SoulExists, export_soul, import_soul, list_souls  # noqa: E402
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


def archive(members, gz=True):
    """A tar.gz in memory. A member is (name, data) or (TarInfo, data) for the odd kinds (links, devices, directories)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w") as tf:
        for info, data in members:
            if isinstance(info, str):
                info = tarfile.TarInfo(info)
            info.size = len(data) if info.isreg() else 0
            tf.addfile(info, io.BytesIO(data) if info.isreg() else None)
    buf.seek(0)
    return buf


def sha(b):
    return hashlib.sha256(b).hexdigest()


def good_members(manifest_extra=None, files=None, sums=None, soul_id="agent-x"):
    files = files if files is not None else {"persona.md": b"You are A.", "memories.json": b'{"notes": []}'}
    manifest = {"id": soul_id, "name": "X", "format": 1,
                "export": {"format": 1, "exported_at": 1.0, "files": sums if sums is not None else {n: sha(b) for n, b in files.items()}}}
    manifest.update(manifest_extra or {})
    return [("manifest.json", json.dumps(manifest).encode())] + list(files.items())


class SoulArchiveTests(unittest.TestCase):
    def setUp(self):
        self.src, self.dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.soul = Soul.open(self.src, "agent-x", display_name="X", default_persona="You are A.")
        with open(self.soul.path("memories.json"), "w", encoding="utf-8") as f:
            f.write('{"notes": [{"text": "Sam likes tea"}]}')

    def exported(self):
        buf = io.BytesIO()
        self.soul.export(buf)
        buf.seek(0)
        return buf

    def dest(self, *parts):
        return os.path.join(self.dst, "souls", *parts)

    def test_round_trip(self):
        path = os.path.join(self.src, "x.tar.gz")
        manifest = self.soul.export(path)
        self.assertEqual(sorted(manifest["export"]["files"]), ["memories.json", "persona.md"])
        self.assertEqual(manifest["export"]["format"], 1)
        got = import_soul(self.dst, path)
        self.assertEqual(got["id"], "agent-x")
        self.assertNotIn("export", got)
        for name in ("persona.md", "memories.json", "manifest.json"):
            self.assertTrue(os.path.isfile(self.dest("agent-x", name)), name)
        self.assertEqual(open(self.dest("agent-x", "persona.md"), encoding="utf-8").read(), "You are A.")
        self.assertEqual(json.load(open(self.dest("agent-x", "memories.json")))["notes"][0]["text"], "Sam likes tea")
        self.assertEqual(Soul.open(self.dst, "agent-x").persona(), "You are A.")      # opens like any soul

    def test_export_has_only_top_level_regular_files_and_checksums(self):
        os.makedirs(os.path.join(self.soul.root, "sub"))
        open(os.path.join(self.soul.root, "sub", "deep.txt"), "w").write("x")
        open(os.path.join(self.soul.root, "stray.tmp"), "w").write("x")
        with tarfile.open(fileobj=self.exported(), mode="r:gz") as tf:
            self.assertEqual(sorted(tf.getnames()), ["manifest.json", "memories.json", "persona.md"])
            manifest = json.load(tf.extractfile("manifest.json"))
            self.assertEqual(manifest["export"]["files"]["persona.md"], sha(b"You are A."))

    def test_export_soul_by_id_and_unknown_id(self):
        out = os.path.join(self.src, "y.tar.gz")
        export_soul(self.src, "agent-x", out)
        self.assertTrue(os.path.isfile(out))
        for bad in ("nobody", "../escape"):
            with self.assertRaises(SoulError):
                export_soul(self.src, bad, out)

    def test_rename_on_import(self):
        got = import_soul(self.dst, self.exported(), agent_id="agent-y")
        self.assertEqual((got["id"], got["renamed_from"]), ("agent-y", "agent-x"))
        self.assertEqual(json.load(open(self.dest("agent-y", "manifest.json")))["id"], "agent-y")
        self.assertFalse(os.path.exists(self.dest("agent-x")))
        for bad in ("Agent Y", "a/b", "..", "", "x" * 65, "UPPER"):
            with self.assertRaises(SoulError, msg=bad):
                import_soul(self.dst, self.exported(), agent_id=bad)

    def refused(self, members, **kw):
        """The archive must be refused, and nothing (not even a half-written folder) may be left behind."""
        with self.assertRaises(SoulError):
            import_soul(self.dst, archive(members), **kw)
        self.assertEqual(list_souls(self.dst), [])
        base = self.dest()
        self.assertEqual(os.listdir(base) if os.path.isdir(base) else [], [])

    def test_refuses_path_traversal_absolute_and_subdirectories(self):
        for name in ("../x", "..", "/etc/passwd", "sub/file.txt", "a\\b.txt", ".hidden", "C:evil.txt", "dir/../x"):
            files = {"persona.md": b"p", name: b"evil"}
            self.refused(good_members(files=files))

    def test_refuses_links_devices_and_directories(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.DIRTYPE):
            info = tarfile.TarInfo("linked.txt")
            info.type, info.linkname = kind, "persona.md"
            self.refused(good_members() + [(info, b"")])

    def test_refuses_oversize_files_and_totals(self):
        self.refused(good_members(files={"persona.md": b"x" * (soul_mod.MAX_FILE_BYTES + 1)}))
        chunk = b"y" * soul_mod.MAX_FILE_BYTES
        self.refused(good_members(files={f"part{i}.bin": chunk for i in range(3)}))   # 60 MB: each fits, the total does not
        self.refused(good_members(files={f"f{i}.txt": b"1" for i in range(soul_mod.MAX_FILES + 1)}))

    def test_refuses_bad_checksum_and_mismatched_file_list(self):
        self.refused(good_members(sums={"persona.md": "0" * 64, "memories.json": sha(b'{"notes": []}')}))
        self.refused(good_members(sums={"persona.md": sha(b"You are A.")}))                       # a file with no sum
        self.refused(good_members(files={"persona.md": b"You are A."},
                                  sums={"persona.md": sha(b"You are A."), "ghost.md": "0" * 64}))   # a sum with no file

    def test_refuses_unknown_formats_and_missing_export_block(self):
        for fmt in (2, 0, "1", True, None):
            self.refused(good_members({"format": fmt}))
        self.refused(good_members({"export": {"format": 2, "files": {}}}, files={}))
        members = good_members()
        meta = json.loads(members[0][1])
        del meta["export"]
        self.refused([("manifest.json", json.dumps(meta).encode())] + members[1:])

    def test_refuses_bad_manifest_settings(self):
        self.refused(good_members({"tools": "ha_call"}))
        self.refused(good_members({"tools": ["ok_tool", "../bad"]}))
        self.refused(good_members({"brain": "no spaces allowed"}))
        self.refused(good_members({"id": "Bad Id"}))

    def test_refuses_things_that_are_not_souls(self):
        for junk in (b"", b"not a tar at all", b"\x1f\x8b garbage"):
            with self.assertRaises(SoulError):
                import_soul(self.dst, io.BytesIO(junk))
        with self.assertRaises(SoulError):
            import_soul(self.dst, archive(good_members(), gz=False))            # a plain tar is not an export
        with self.assertRaises(SoulError):
            import_soul(self.dst, archive([("persona.md", b"p")]))               # no manifest

    def test_existing_soul_is_refused_then_moved_aside_on_overwrite(self):
        import_soul(self.dst, self.exported())
        with open(self.dest("agent-x", "persona.md"), "w", encoding="utf-8") as f:
            f.write("Edited by hand.")
        with self.assertRaises(SoulExists) as cm:
            import_soul(self.dst, self.exported())
        self.assertEqual(cm.exception.agent_id, "agent-x")
        self.assertEqual(open(self.dest("agent-x", "persona.md"), encoding="utf-8").read(), "Edited by hand.")   # untouched
        import_soul(self.dst, self.exported(), overwrite=True)
        self.assertEqual(open(self.dest("agent-x", "persona.md"), encoding="utf-8").read(), "You are A.")
        asides = [n for n in os.listdir(self.dest()) if n.startswith("agent-x.bak-")]
        self.assertEqual(len(asides), 1)
        self.assertEqual(open(self.dest(asides[0], "persona.md"), encoding="utf-8").read(), "Edited by hand.")
        import_soul(self.dst, self.exported(), overwrite=True)            # again within the same second: a second backup
        self.assertEqual(len([n for n in os.listdir(self.dest()) if n.startswith("agent-x.bak-")]), 2)
        self.assertEqual([m["id"] for m in list_souls(self.dst)], ["agent-x"], "backups are not souls")

    def test_list_souls(self):
        Soul.open(self.dst, "agent-b", display_name="B")
        Soul.open(self.dst, "agent-a", display_name="A")
        os.makedirs(self.dest("no-manifest"))
        self.assertEqual([(m["id"], m["name"]) for m in list_souls(self.dst)], [("agent-a", "A"), ("agent-b", "B")])
        self.assertEqual(list_souls(os.path.join(self.dst, "missing")), [])

    def test_opening_a_soul_checks_the_id(self):
        for bad in ("../x", "A", "a b", ""):
            with self.assertRaises(SoulError):
                Soul.open(self.dst, bad)

    def test_manifest_settings_survive_the_trip(self):
        s = Soul.open(self.src, "agent-z", display_name="Z")
        s.manifest.update(tools=["memory_search"], brain="worker")
        s._save_manifest()
        buf = io.BytesIO()
        s.export(buf)
        buf.seek(0)
        got = import_soul(self.dst, buf)
        self.assertEqual((got["tools"], got["brain"]), (["memory_search"], "worker"))

    def test_command_line(self):
        out = os.path.join(self.src, "cli.tar.gz")
        self.assertEqual(soul_mod.main(["--data-dir", self.src, "export", "agent-x", out]), 0)
        self.assertEqual(soul_mod.main(["--data-dir", self.dst, "import", out, "--as", "agent-q"]), 0)
        self.assertEqual(soul_mod.main(["--data-dir", self.dst, "import", out, "--as", "agent-q"]), 1)         # exists
        self.assertEqual(soul_mod.main(["--data-dir", self.dst, "import", out, "--as", "agent-q", "--overwrite"]), 0)
        self.assertEqual(soul_mod.main(["--data-dir", self.dst, "list"]), 0)
        self.assertEqual(soul_mod.main(["--data-dir", self.dst, "export", "missing", out]), 1)

    def test_default_data_dir_is_the_servers(self):
        from stonesage import server
        self.assertEqual(os.path.normpath(soul_mod.default_data_dir()), os.path.normpath(server._stonesage_data_dir()))


if __name__ == "__main__":
    unittest.main()
