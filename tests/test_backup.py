"""Backups and restore (stonesage/backup.py, stonesage/archive.py): what goes in and what stays out, the archive's mode and manifest,
the round trip, every way a restore must refuse, move-aside-never-delete, the daily service, and the two routes.
Offline; everything happens in temporary folders with made-up names (Alex, Sam, Bella, Rex)."""

import hashlib
import io
import json
import logging
import os
import socket
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from stonesage import archive, backup, config_migrate  # noqa: E402

SECRET = "sk-test-secret-value-0123456789"
CONFIG = {"config_version": config_migrate.LATEST, "api": {"key": SECRET}, "operator": {"name": "Alex"}, "server": {"port": 8888}}


class Install(unittest.TestCase):
    """A small made-up install in a temp folder: config.json, a data folder, a certs folder, agents.json."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="backup-test-")
        self.addCleanup(self._clean)
        self.root = os.path.join(self.tmp, "root")
        self.backend = os.path.join(self.root, "backend")
        self.data = os.path.join(self.root, "data")
        self.config_file = os.path.join(self.backend, "config.json")
        os.makedirs(self.backend)
        os.makedirs(self.data)
        self.loc = backup.Locations(config_file=self.config_file, data_dir=self.data, backend_dir=self.backend, root_dir=self.root)
        self.out = os.path.join(self.tmp, "out")
        self.write(self.config_file, json.dumps(CONFIG))
        self.write(os.path.join(self.backend, "agents.json"), "[]")
        self.write(os.path.join(self.root, "certs", "stonesage.pem"), "CERT")
        self.write(os.path.join(self.root, "certs", "stonesage-key.pem"), "KEY")
        self.write(os.path.join(self.root, "certs", "notes.txt"), "not a pem")
        self.write(os.path.join(self.data, "souls", "atlas", "persona.md"), "I am Atlas, Sam's assistant.")
        self.write(os.path.join(self.data, "souls", "atlas", "memories.json"), json.dumps([{"text": "Bella is a cat"}]))
        self.write(os.path.join(self.data, "souls", "atlas.bak-20260101-000000", "persona.md"), "old")
        self.write(os.path.join(self.data, "courage_reflexes.json"), '{"turn on lamp": 1}')
        self.write(os.path.join(self.data, "patrol_corrections.jsonl"), '{"was": "Rex"}\n')
        self.write(os.path.join(self.data, "profiles", "entity_profiles.json"), '{"people": [], "pets": []}')
        self.write(os.path.join(self.data, "a_new_feature_state.json"), '{"hello": 1}')
        # what must stay out
        self.write(os.path.join(self.data, "tts_cache", "a.wav"), "RIFF")
        self.write(os.path.join(self.data, "voice_samples", "take1.webm"), "voice")
        self.write(os.path.join(self.data, "backups", "stonesage-backup-old.tar.gz"), "old backup")
        self.write(os.path.join(self.data, "sighting_boxes.json"), "{}")
        self.write(os.path.join(self.data, "engine_lease.json"), '{"lease": 1}')
        self.write(os.path.join(self.data, "boost_quota.json"), "{}")
        self.write(os.path.join(self.data, "training", "big.jsonl"), "x")
        self.write(os.path.join(self.data, "autonomous_dossiers", "d.md"), "x")
        self.write(os.path.join(self.data, "pkg", "__pycache__", "m.cpython-312.pyc"), "x")
        self.write(os.path.join(self.data, "half.tmp"), "x")
        self.write(os.path.join(self.data, "custom_tools.json.bak-1"), "x")
        self.write(os.path.join(self.data, "profiles", "pet.json.pre-merge-20261001-120000"), "x")
        self.write(os.path.join(self.data, "profiles", "pet.json.pre-restore-20261001-120000"), "x")
        self.write(os.path.join(self.data, "harness.db-wal"), "x")
        self.db = os.path.join(self.data, "harness.db")
        con = sqlite3.connect(self.db)
        con.execute("create table chat (id integer primary key, text text)")
        con.execute("insert into chat (text) values ('hello from Alex')")
        con.commit()
        con.close()

    def _clean(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def create(self, **kw):
        return backup.create(self.loc, dict(CONFIG), out_dir=self.out, **kw)

    def names(self, path):
        with tarfile.open(path, "r:gz") as tf:
            return [m.name for m in tf.getmembers()]

    def tree(self, *folders):
        """{path: sha256} of every file under the folders: to prove a refused restore changed nothing."""
        out = {}
        for folder in folders:
            for d, _s, fs in os.walk(folder):
                for f in fs:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        out[p] = hashlib.sha256(fh.read()).hexdigest()
        return out


class TestWhatGoesIn(Install):
    def test_every_rule_has_a_reason_and_a_valid_shape(self):
        for r in backup.RULES:
            self.assertIn(r.root, ("config", "certs", "data"), r)   # harness-data uses the data rules
            self.assertIn(r.action, ("include", "exclude"), r)
            self.assertGreater(len(r.reason), 12, f"{r.root}/{r.pattern} needs a reason")
            self.assertFalse(r.pattern.startswith("/") or ".." in r.pattern.split("/"), r)
        keys = [(r.root, r.pattern) for r in backup.RULES]
        self.assertEqual(len(keys), len(set(keys)), "a rule appears twice (the second can never match)")

    def test_each_root_ends_in_a_catch_all(self):
        for root in ("certs", "data"):
            last = [r for r in backup.RULES if r.root == root][-1]
            self.assertEqual(last.pattern, "**", root)
        self.assertEqual(backup.rule_for("data", "anything/at/all.json").action, "include")
        self.assertEqual(backup.rule_for("certs", "notes.txt").action, "exclude")
        self.assertIsNone(backup.rule_for("config", "other.json"))

    def test_pattern_matching_is_by_segment(self):
        self.assertTrue(backup.matches("tts_cache", "tts_cache/a.wav"))
        self.assertTrue(backup.matches("tts_cache", "tts_cache"))
        self.assertFalse(backup.matches("tts_cache", "tts_cache2/a.wav"))
        self.assertTrue(backup.matches("**/__pycache__", "a/b/__pycache__/x.pyc"))
        self.assertTrue(backup.matches("**/__pycache__", "__pycache__/x.pyc"))
        self.assertTrue(backup.matches("souls", "souls/atlas/persona.md"))
        self.assertTrue(backup.matches("**/*.bak-*", "souls/atlas.bak-1/persona.md"))
        self.assertFalse(backup.matches("**/*.bak-*", "souls/atlas/persona.md"))
        self.assertTrue(backup.matches("*.db", "harness.db"))
        self.assertFalse(backup.matches("*.db", "sub/harness.db"))

    def test_the_archive_has_what_a_person_would_miss_and_nothing_regenerable(self):
        res = self.create()
        names = set(self.names(res.path))
        for want in ("manifest.json", "config/config.json", "config/agents.json", "certs/stonesage.pem", "certs/stonesage-key.pem",
                     "data/souls/atlas/persona.md", "data/souls/atlas/memories.json", "data/courage_reflexes.json",
                     "data/patrol_corrections.jsonl", "data/profiles/entity_profiles.json", "data/harness.db",
                     "data/a_new_feature_state.json"):
            self.assertIn(want, names)
        for left_out in ("certs/notes.txt", "data/tts_cache/a.wav", "data/voice_samples/take1.webm",
                         "data/backups/stonesage-backup-old.tar.gz", "data/sighting_boxes.json", "data/engine_lease.json",
                         "data/boost_quota.json", "data/training/big.jsonl", "data/autonomous_dossiers/d.md",
                         "data/pkg/__pycache__/m.cpython-312.pyc", "data/half.tmp", "data/custom_tools.json.bak-1",
                         "data/harness.db-wal", "data/profiles/pet.json.pre-merge-20261001-120000",
                         "data/profiles/pet.json.pre-restore-20261001-120000", "data/souls/atlas.bak-20260101-000000/persona.md"):
            self.assertNotIn(left_out, names)

    def test_a_big_unnamed_file_is_skipped_and_listed_with_the_reason(self):
        self.write(os.path.join(self.data, "huge_unknown.bin"), "x" * (backup.DEFAULT_MAX_FILE + 1))
        self.write(os.path.join(self.data, "courage_trace.jsonl"), "x" * 1000)
        self.write(os.path.join(self.data, "courage_trace.jsonl.1"), "x" * (9 * 1024 * 1024))     # over the trace cap
        res = self.create()
        names = set(self.names(res.path))
        self.assertNotIn("data/huge_unknown.bin", names)
        self.assertIn("data/courage_trace.jsonl", names)
        self.assertNotIn("data/courage_trace.jsonl.1", names)
        manifest = backup.read_manifest(res.path)
        skipped = {s["path"]: s["reason"] for s in manifest["skipped"]}
        self.assertIn("data/huge_unknown.bin", skipped)
        self.assertIn("MB", skipped["data/huge_unknown.bin"])
        self.assertIn("data/courage_trace.jsonl.1", skipped)

    def test_a_name_a_restore_would_refuse_is_skipped_not_stored(self):
        self.write(os.path.join(self.data, "has space@and(odd).json"), "{}")
        self.write(os.path.join(self.data, ".hidden"), "x")
        res = self.create()
        skipped = [s["path"] for s in backup.read_manifest(res.path)["skipped"]]
        self.assertIn("data/has space@and(odd).json", skipped)
        self.assertIn("data/.hidden", skipped)
        self.restore_ok(res.path)                                  # and the archive it made restores cleanly

    def restore_ok(self, path):
        return backup.restore(path, self.loc, CONFIG, running=lambda: False)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks here")
    def test_links_are_never_followed(self):
        outside = os.path.join(self.tmp, "outside.txt")
        self.write(outside, "private")
        try:
            os.symlink(outside, os.path.join(self.data, "link.json"))
            os.symlink(self.tmp, os.path.join(self.data, "linked_dir"))
        except (OSError, NotImplementedError):
            self.skipTest("the OS refuses symlinks for this user")
        res = self.create()
        names = self.names(res.path)
        self.assertFalse(any("link" in n for n in names), names)
        self.assertFalse(any("outside" in n or "private" in n for n in names))
        self.assertTrue(any("link" in s["path"] for s in backup.read_manifest(res.path)["skipped"]))


    def test_links_are_skipped_wherever_the_os_says_a_path_is_a_link(self):
        """The same, with the OS's answer faked (a Windows user without the symlink privilege cannot make a real one)."""
        self.write(os.path.join(self.data, "pointer.json"), "{}")
        os.makedirs(os.path.join(self.data, "pointer_dir"))
        self.write(os.path.join(self.data, "pointer_dir", "inside.json"), "{}")
        real = os.path.islink
        fake = lambda p: real(p) or os.path.basename(p) in ("pointer.json", "pointer_dir")
        with mock.patch.object(backup.os.path, "islink", fake):
            res = self.create()
        names = self.names(res.path)
        self.assertFalse([n for n in names if "pointer" in n], names)
        self.assertFalse([n for n in names if "inside" in n], "a folder that is a link is not entered")
        skipped = [s["path"] for s in backup.read_manifest(res.path)["skipped"]]
        self.assertIn("data/pointer.json", skipped)
        self.assertIn("data/pointer_dir", skipped)


class TestTheArchive(Install):
    def test_manifest_content(self):
        res = self.create(now=1_800_000_000.0)
        m = backup.read_manifest(res.path)
        self.assertEqual((m["kind"], m["format"], m["created"]), ("stonesage-backup", backup.FORMAT_VERSION, 1_800_000_000.0))
        self.assertRegex(m["created_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        import stonesage
        self.assertEqual((m["stonesage_version"], m["api_version"]), (stonesage.__version__, stonesage.API_VERSION))
        self.assertEqual((m["config_version"], m["config_version_latest"]), (config_migrate.LATEST, config_migrate.LATEST))
        self.assertEqual(sorted(m["secret_files"]), ["certs/stonesage-key.pem", "certs/stonesage.pem", "config/config.json"])
        with tarfile.open(res.path, "r:gz") as tf:
            first = tf.next()
            self.assertEqual(first.name, "manifest.json", "the manifest is first so a list or a refusal reads little")
            for arc, meta in m["files"].items():
                data = tf.extractfile(arc).read()
                self.assertEqual((meta["size"], meta["sha256"]), (len(data), hashlib.sha256(data).hexdigest()), arc)
        self.assertNotIn(SECRET, json.dumps(m), "the manifest names files, never their contents")

    @unittest.skipUnless(os.name == "posix", "file modes are a POSIX thing")
    def test_the_archive_and_its_folder_are_owner_only(self):
        res = self.create()
        self.assertEqual(stat.S_IMODE(os.stat(res.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o700)
        with tarfile.open(res.path, "r:gz") as tf:
            self.assertEqual(tf.getmember("config/config.json").mode, 0o600)

    def test_no_staging_or_partial_files_are_left_behind(self):
        self.create()
        self.assertEqual([n for n in os.listdir(self.out) if not n.endswith(".tar.gz")], [])

    def test_the_log_line_names_the_file_and_never_contents(self):
        with self.assertLogs("StoneSage.backup", level="INFO") as logs:
            res = self.create()
        text = "\n".join(logs.output)
        self.assertIn(os.path.basename(res.path), text)
        for secret in (SECRET, "Alex", "Bella", "KEY", "hello from"):
            self.assertNotIn(secret, text)

    def test_a_file_that_cannot_be_read_is_an_error_in_the_result_not_a_failed_backup(self):
        real = backup.shutil.copyfile

        def flaky(src, dst, *a, **k):
            if src.endswith("courage_reflexes.json"):
                raise PermissionError("no")
            return real(src, dst, *a, **k)
        with mock.patch.object(backup.shutil, "copyfile", flaky):
            res = self.create()
        self.assertEqual(res.errors, ["data/courage_reflexes.json: PermissionError"])
        self.assertIn("data/souls/atlas/persona.md", self.names(res.path))

    def test_two_backups_in_one_second_get_two_names(self):
        a, b = self.create(now=1_800_000_000.0), self.create(now=1_800_000_000.0)
        self.assertNotEqual(a.path, b.path)

    def test_old_backups_are_pruned_only_in_the_managed_folder(self):
        cfg = dict(CONFIG, backup={"keep": 2, "dir": self.out})
        paths = [backup.create(self.loc, cfg, now=1_800_000_000.0 + i * 100) for i in range(4)]
        self.assertEqual(backup.list_backups(self.out), [paths[2].path, paths[3].path])
        other = os.path.join(self.tmp, "chosen")
        for i in range(4):
            backup.create(self.loc, cfg, out_dir=other, now=1_800_001_000.0 + i)
        self.assertEqual(len(backup.list_backups(other)), 4, "a folder the operator chose is never pruned")
        self.write(os.path.join(self.out, "notes.txt"), "mine")
        backup.create(self.loc, cfg, now=1_800_002_000.0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "notes.txt")), "only files named like backups are ever removed")

    def test_the_default_folder_is_the_data_folders_backups_and_settings_are_validated(self):
        self.assertEqual(backup.backup_dir_for(self.loc, {}), os.path.join(self.data, "backups"))
        self.assertEqual(backup.backup_dir_for(self.loc, {"backup": {"dir": self.out}}), os.path.abspath(self.out))
        s = backup.backup_settings({"backup": {"enabled": False, "keep": 0, "hour": 25}})
        self.assertEqual(s, {"enabled": False, "dir": "", "keep": 14, "hour": 3})
        s = backup.backup_settings({"backup": {"keep": True, "hour": "3"}})
        self.assertEqual((s["keep"], s["hour"], s["enabled"]), (14, 3, True))
        self.assertEqual(backup.backup_settings(None), {"enabled": True, "dir": "", "keep": 14, "hour": 3})


class TestRestore(Install):
    def fresh_target(self):
        """A second, empty install to restore into."""
        other = Install.__new__(Install)
        base = os.path.join(self.tmp, "second")
        loc = backup.Locations(config_file=os.path.join(base, "backend", "config.json"), data_dir=os.path.join(base, "data"),
                               backend_dir=os.path.join(base, "backend"), root_dir=base)
        return loc

    def test_round_trip_onto_a_new_machine(self):
        res = self.create()
        loc2 = self.fresh_target()
        rep = backup.restore(res.path, loc2, running=lambda: False)
        self.assertTrue(rep.applied)
        self.assertEqual(rep.replaced, [])
        self.assertEqual(json.loads(self.read(loc2.config_file)), CONFIG)
        self.assertEqual(self.read(os.path.join(loc2.data_dir, "souls", "atlas", "persona.md")), "I am Atlas, Sam's assistant.")
        self.assertEqual(self.read(os.path.join(loc2.data_dir, "a_new_feature_state.json")), '{"hello": 1}')
        self.assertEqual(self.read(os.path.join(loc2.root_dir, "certs", "stonesage-key.pem")), "KEY")
        con = sqlite3.connect(os.path.join(loc2.data_dir, "harness.db"))
        self.assertEqual(con.execute("select text from chat").fetchall(), [("hello from Alex",)])
        con.close()
        self.assertFalse(os.path.exists(os.path.join(loc2.data_dir, "tts_cache")))

    def test_the_harness_folder_is_backed_up_when_it_is_not_the_data_folder(self):
        """A native install without STONESAGE_DATA_DIR: the harness keeps harness.db in <checkout>/data, the server in its own
        data folder (found live 2026-10-12: the first backup on the homelab missed harness.db)."""
        hdir = os.path.join(self.backend, "data")
        self.write(os.path.join(hdir, "agent_dna", "coder-agent.json"), '{"name": "Atlas"}')
        self.write(os.path.join(hdir, "harness.db-wal"), "x")
        con = sqlite3.connect(os.path.join(hdir, "harness.db"))
        con.execute("create table t (v text)")
        con.execute("insert into t values ('from the harness')")
        con.commit()
        con.close()
        self.loc.harness_dir = hdir
        res = self.create()
        names = self.names(res.path)
        self.assertIn("harness-data/harness.db", names)
        self.assertIn("harness-data/agent_dna/coder-agent.json", names)
        self.assertNotIn("harness-data/harness.db-wal", names)               # the data rules apply here too
        loc2 = self.fresh_target()
        loc2.harness_dir = os.path.join(self.tmp, "second", "backend", "data")
        backup.restore(res.path, loc2, running=lambda: False)
        con = sqlite3.connect(os.path.join(loc2.harness_dir, "harness.db"))
        self.assertEqual(con.execute("select v from t").fetchall(), [("from the harness",)])
        con.close()
        loc3 = backup.Locations(config_file=os.path.join(self.tmp, "third", "config.json"), data_dir=os.path.join(self.tmp, "third", "data"),
                                backend_dir=os.path.join(self.tmp, "third"), root_dir=os.path.join(self.tmp, "third"))
        rep3 = backup.restore(res.path, loc3, running=lambda: False)          # one shared folder (Docker): into the data folder
        self.assertTrue(os.path.exists(os.path.join(loc3.data_dir, "agent_dna", "coder-agent.json")))
        self.assertTrue(any(s.startswith("harness-data/harness.db") for s in rep3.skipped), rep3.skipped)

    @unittest.skipUnless(os.name == "posix", "file modes are a POSIX thing")
    def test_restored_secret_files_are_owner_only(self):
        res = self.create()
        loc2 = self.fresh_target()
        backup.restore(res.path, loc2, running=lambda: False)
        self.assertEqual(stat.S_IMODE(os.stat(loc2.config_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(loc2.root_dir, "certs", "stonesage-key.pem")).st_mode), 0o600)

    def test_every_replaced_file_is_moved_aside_first_and_nothing_is_deleted(self):
        res = self.create()
        self.write(self.config_file, '{"changed": "after the backup"}')
        self.write(os.path.join(self.data, "courage_reflexes.json"), '{"newer": 1}')
        self.write(os.path.join(self.data, "only_here.json"), "keep me")
        con = sqlite3.connect(self.db)
        con.execute("insert into chat (text) values ('written after the backup')")
        con.commit()
        con.close()
        self.write(self.db + "-wal", "stale write-ahead log")
        rep = backup.restore(res.path, self.loc, running=lambda: False, now=1_800_000_000.0)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(1_800_000_000.0))
        self.assertEqual(json.loads(self.read(self.config_file)), CONFIG)
        self.assertEqual(self.read(self.config_file + f".pre-restore-{stamp}"), '{"changed": "after the backup"}')
        self.assertEqual(self.read(os.path.join(self.data, "courage_reflexes.json.pre-restore-" + stamp)), '{"newer": 1}')
        self.assertEqual(self.read(os.path.join(self.data, "only_here.json")), "keep me", "files the archive lacks are left alone")
        self.assertEqual(self.read(self.db + "-wal.pre-restore-" + stamp), "stale write-ahead log")
        self.assertFalse(os.path.exists(self.db + "-wal"), "a leftover write-ahead log would corrupt the restored database")
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("select count(*) from chat").fetchone(), (1,))
        con.close()
        self.assertEqual(len(rep.moved_aside), len(rep.replaced))
        for aside in rep.moved_aside:
            self.assertTrue(os.path.exists(aside))
        # a second restore in the same second does not overwrite the first set of safety copies
        backup.restore(res.path, self.loc, running=lambda: False, now=1_800_000_000.0)
        self.assertTrue(os.path.exists(self.config_file + f".pre-restore-{stamp}-2"))
        self.assertEqual(self.read(self.config_file + f".pre-restore-{stamp}"), '{"changed": "after the backup"}')

    def test_files_moved_aside_by_a_restore_are_not_backed_up_again(self):
        res = self.create()
        backup.restore(res.path, self.loc, running=lambda: False)
        names = self.names(self.create(now=time.time() + 5).path)
        self.assertFalse([n for n in names if "pre-restore" in n])

    def test_dry_run_changes_nothing(self):
        res = self.create()
        self.write(self.config_file, '{"x": 1}')
        before = self.tree(self.root)
        rep = backup.restore(res.path, self.loc, dry_run=True)
        self.assertFalse(rep.applied)
        self.assertIn(self.config_file, rep.replaced)
        self.assertEqual(self.tree(self.root), before)

    def test_a_running_server_blocks_a_restore_unless_yes(self):
        res = self.create()
        self.write(self.config_file, '{"x": 1}')
        before = self.tree(self.root)
        with self.assertRaises(backup.RestoreError) as cm:
            backup.restore(res.path, self.loc, running=lambda: True)
        self.assertIn("--yes", str(cm.exception))
        self.assertIn("file(s) would be replaced", str(cm.exception))
        self.assertEqual(self.tree(self.root), before)
        self.assertTrue(backup.restore(res.path, self.loc, yes=True, running=lambda: True).applied)

    def test_server_running_sees_a_listening_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            self.assertTrue(backup.server_running({}, port=port))
        finally:
            s.close()
        self.assertFalse(backup.server_running({}, port=port))

    def test_a_failure_part_way_puts_everything_back(self):
        res = self.create()
        self.write(self.config_file, '{"changed": 1}')
        self.write(os.path.join(self.data, "courage_reflexes.json"), '{"newer": 1}')
        before = self.tree(self.root)
        real, calls = os.replace, {"n": 0}

        def failing(src, dst, *a, **k):
            calls["n"] += 1
            if calls["n"] == 6:
                raise OSError("disk full")
            return real(src, dst, *a, **k)
        with mock.patch.object(backup.os, "replace", failing):
            with self.assertRaises(OSError):
                backup.restore(res.path, self.loc, running=lambda: False)
        self.assertEqual(self.tree(self.root), before)
        self.assertEqual([n for n in os.listdir(self.data) if n.endswith(".restore-tmp")], [])

    # ---- refusals: each leaves the install exactly as it was
    def tamper(self, mutate):
        """A copy of a real backup with its members changed by mutate(list of [TarInfo, bytes]) -> list."""
        res = self.create()
        members = []
        with tarfile.open(res.path, "r:gz") as tf:
            for m in tf:
                members.append([m, tf.extractfile(m).read() if m.isreg() else b""])
        members = mutate(members)
        out = os.path.join(self.tmp, "tampered.tar.gz")
        with tarfile.open(out, "w:gz") as tf:
            for info, data in members:
                tf.addfile(info, io.BytesIO(data) if info.isreg() else None)
        return out

    def assert_refused(self, path, message_part):
        self.write(self.config_file, '{"local": "settings"}')
        before = self.tree(self.root)
        with self.assertRaises(backup.RestoreError) as cm:
            backup.restore(path, self.loc, running=lambda: False)
        self.assertIn(message_part, str(cm.exception))
        self.assertEqual(self.tree(self.root), before, "a refused restore must change nothing")
        self.assertFalse([n for n in os.listdir(self.backend) if "restore" in n])

    @staticmethod
    def manifest_edit(members, fn):
        for item in members:
            if item[0].name == "manifest.json":
                m = json.loads(item[1])
                fn(m)
                item[1] = json.dumps(m).encode()
                item[0].size = len(item[1])
        return members

    def test_a_newer_format_is_refused(self):
        self.assert_refused(self.tamper(lambda ms: self.manifest_edit(ms, lambda m: m.update(format=backup.FORMAT_VERSION + 1))),
                            "newer StoneSage")

    def test_settings_newer_than_this_code_are_refused(self):
        self.assert_refused(self.tamper(lambda ms: self.manifest_edit(ms, lambda m: m.update(config_version=config_migrate.LATEST + 1))),
                            "newer than this StoneSage understands")

    def test_not_a_backup(self):
        self.assert_refused(self.tamper(lambda ms: self.manifest_edit(ms, lambda m: m.update(kind="something-else"))), "not a StoneSage backup")
        junk = os.path.join(self.tmp, "junk.tar.gz")
        with open(junk, "wb") as f:
            f.write(b"this is not an archive")
        self.assert_refused(junk, "not a readable backup")
        self.assert_refused(self.tamper(lambda ms: [x for x in ms if x[0].name != "manifest.json"]), "no manifest.json")

    def test_a_damaged_file_fails_its_checksum(self):
        def damage(ms):
            for item in ms:
                if item[0].name == "data/courage_reflexes.json":
                    item[1] = b'{"turn on lamp": 2}'
                    item[0].size = len(item[1])
            return ms
        self.assert_refused(self.tamper(damage), "checksum mismatch")

    def test_the_manifest_must_match_the_files(self):
        self.assert_refused(self.tamper(lambda ms: ms[:-1]), "does not match the archive")

        def extra(ms):
            info = tarfile.TarInfo("data/extra.json")
            info.size = 2
            return ms + [[info, b"{}"]]
        self.assert_refused(self.tamper(extra), "does not match the archive")

    def test_paths_that_could_escape_are_refused(self):
        for evil in ("../escape.json", "data/../../escape.json", "/etc/escape.json", "data//x.json", "data\\..\\x.json",
                     "C:/Windows/x.json", "data/.hidden/x.json", "data/a:b.json"):
            def add(ms, evil=evil):
                info = tarfile.TarInfo(evil)
                info.size = 2
                return ms + [[info, b"{}"]]
            with self.subTest(name=evil):
                self.assert_refused(self.tamper(add), "not a plain file name")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.json")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape.json")))

    def test_links_directories_and_devices_are_refused(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            def add(ms, kind=kind):
                info = tarfile.TarInfo("data/trap")
                info.type, info.linkname = kind, "/etc/passwd"
                return ms + [[info, b""]]
            with self.subTest(kind=kind):
                self.assert_refused(self.tamper(add), "not a plain file")

    def test_a_name_twice_or_a_place_this_install_does_not_restore_to_is_refused(self):
        def twice(ms):
            dup = next(x for x in ms if x[0].name == "data/courage_reflexes.json")
            return ms + [[dup[0], dup[1]]]
        self.assert_refused(self.tamper(twice), "appears twice")

        def elsewhere(ms):
            info = tarfile.TarInfo("etc/cron.json")
            info.size = 2
            ms = self.manifest_edit(ms, lambda m: m["files"].update({"etc/cron.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "size": 2}}))
            return ms + [[info, b"{}"]]
        self.assert_refused(self.tamper(elsewhere), "not a place this install restores to")

        def other_config(ms):
            info = tarfile.TarInfo("config/evil.json")
            info.size = 2
            ms = self.manifest_edit(ms, lambda m: m["files"].update({"config/evil.json": {"sha256": hashlib.sha256(b"{}").hexdigest(), "size": 2}}))
            return ms + [[info, b"{}"]]
        self.assert_refused(self.tamper(other_config), "not a config file this install knows")

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks here")
    def test_a_folder_in_the_install_that_is_a_link_out_is_not_written_through(self):
        res = self.create()
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        import shutil
        shutil.rmtree(os.path.join(self.data, "souls"))
        try:
            os.symlink(outside, os.path.join(self.data, "souls"))
        except (OSError, NotImplementedError):
            self.skipTest("the OS refuses symlinks for this user")
        with self.assertRaises(backup.RestoreError):
            backup.restore(res.path, self.loc, running=lambda: False)
        self.assertEqual(os.listdir(outside), [])

    def test_a_destination_that_is_a_folder_is_refused(self):
        res = self.create()
        os.remove(os.path.join(self.data, "courage_reflexes.json"))
        os.makedirs(os.path.join(self.data, "courage_reflexes.json"))
        with self.assertRaises(backup.RestoreError) as cm:
            backup.restore(res.path, self.loc, running=lambda: False)
        self.assertIn("not a plain file", str(cm.exception))


class TestMergeFolders(Install):
    """merge-folders: the harness's old <checkout>/data moves into the data folder; a file both have and that differs is the
    owner's choice; nothing is deleted; the old folder is renamed only at the end."""

    def setUp(self):
        super().setUp()
        self.hdir = os.path.join(self.backend, "data")
        self.write(os.path.join(self.hdir, "agent_dna", "coder-agent.json"), '{"name": "Atlas"}')                 # only here
        self.write(os.path.join(self.hdir, "souls", "atlas", "persona.md"), "I am Atlas, Sam's assistant.")       # identical
        self.write(os.path.join(self.hdir, "profiles", "entity_profiles.json"), '{"people": ["Alex"], "pets": ["Rex"]}')
        os.utime(os.path.join(self.data, "profiles", "entity_profiles.json"), (1_000_000, 1_000_000))            # data copy older
        self.write(os.path.join(self.hdir, "harness.db-wal"), "x")
        con = sqlite3.connect(os.path.join(self.hdir, "irrigation.db"))
        con.execute("create table t (v text)")
        con.execute("insert into t values ('zone 1 watered')")
        con.commit()
        con.close()
        self.loc.harness_dir = self.hdir
        self.profiles = os.path.join(self.data, "profiles", "entity_profiles.json")

    def merge(self, **kw):
        kw.setdefault("running", lambda: False)
        return backup.merge_folders(self.loc, dict(CONFIG), **kw)

    def test_the_plan_sorts_files_into_copy_identical_and_duplicates(self):
        rep = backup.merge_plan(self.loc)
        self.assertEqual(sorted(rep.copied), ["agent_dna/coder-agent.json", "irrigation.db"])     # the -wal is not a file to move
        self.assertEqual(rep.identical, ["souls/atlas/persona.md"])
        self.assertEqual([d.rel for d in rep.duplicates], ["profiles/entity_profiles.json"])
        self.assertEqual(rep.duplicates[0].newer(), "harness")

    def test_a_difference_with_nobody_to_ask_is_refused_and_changes_nothing(self):
        before = self.tree(self.data, self.hdir)
        with self.assertRaisesRegex(backup.RestoreError, "profiles/entity_profiles.json"):
            self.merge()
        self.assertEqual(self.tree(self.data, self.hdir), before)

    def test_prefer_newer_takes_the_harness_copy_and_keeps_the_other(self):
        rep = self.merge(prefer="newer")
        self.assertTrue(rep.applied)
        self.assertEqual(json.loads(self.read(self.profiles)), {"people": ["Alex"], "pets": ["Rex"]})
        self.assertEqual(len(rep.moved_aside), 1)
        self.assertIn(".pre-merge-", rep.moved_aside[0])
        self.assertEqual(json.loads(self.read(rep.moved_aside[0])), {"people": [], "pets": []})          # the old one, kept
        self.assertEqual(self.read(os.path.join(self.data, "agent_dna", "coder-agent.json")), '{"name": "Atlas"}')
        con = sqlite3.connect(os.path.join(self.data, "irrigation.db"))
        self.assertEqual(con.execute("select v from t").fetchall(), [("zone 1 watered",)])
        con.close()
        self.assertFalse(os.path.exists(self.hdir))                                                     # renamed, not deleted
        self.assertTrue(os.path.isfile(os.path.join(rep.old_folder_now, "harness.db-wal")))
        self.assertIn("harness-data/irrigation.db", self.names(rep.backup))                             # backed up first

    def test_the_owner_chooses_each_duplicate(self):
        asked = []
        rep = self.merge(ask=lambda d: asked.append(d.rel) or "data")
        self.assertEqual(asked, ["profiles/entity_profiles.json"])
        self.assertEqual(json.loads(self.read(self.profiles)), {"people": [], "pets": []})               # the data copy stays
        self.assertEqual(rep.moved_aside, [])

    def test_refused_while_the_server_runs_and_dry_run_changes_nothing(self):
        before = self.tree(self.data, self.hdir)
        with self.assertRaisesRegex(backup.RestoreError, "running"):
            self.merge(prefer="data", running=lambda: True)
        self.merge(prefer="harness", dry_run=True)
        self.assertEqual(self.tree(self.data, self.hdir), before)

    def test_nothing_to_merge_on_a_one_folder_install(self):
        self.loc.harness_dir = ""
        with self.assertRaisesRegex(backup.RestoreError, "one data folder"):
            self.merge(prefer="newer")

    def test_restore_prefer_picks_the_copy_for_a_shared_folder(self):
        res = self.create()
        loc3 = backup.Locations(config_file=os.path.join(self.tmp, "third", "config.json"), data_dir=os.path.join(self.tmp, "third", "data"),
                                backend_dir=os.path.join(self.tmp, "third"), root_dir=os.path.join(self.tmp, "third"))
        rep = backup.restore(res.path, loc3, running=lambda: False, prefer="harness")
        self.assertEqual(json.loads(self.read(os.path.join(loc3.data_dir, "profiles", "entity_profiles.json"))),
                         {"people": ["Alex"], "pets": ["Rex"]})
        self.assertTrue(any(s.startswith("data/profiles/entity_profiles.json") for s in rep.skipped), rep.skipped)


class TestHarnessDataFolder(unittest.TestCase):
    """harness/core/datadir.py: one data folder by default, without changing which copy an existing install reads."""

    def setUp(self):
        sys.path.insert(0, ROOT)
        from harness.core import datadir
        self.dd = datadir
        self.tmp = tempfile.mkdtemp(prefix="datadir-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.legacy, self.data = os.path.join(self.tmp, "legacy"), os.path.join(self.tmp, "data")
        for d in (self.legacy, self.data):
            os.makedirs(os.path.join(d, "profiles"))
        Install.write(os.path.join(self.legacy, "profiles", "p.json"), "legacy")
        Install.write(os.path.join(self.data, "profiles", "p.json"), "data")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(datadir, "LEGACY_DIR", self.legacy).start()
        mock.patch.object(datadir, "_server_data_dir", lambda: self.data).start()

    def test_native_install_keeps_reading_the_old_folder_until_merged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STONESAGE_DATA_DIR", None)
            self.assertEqual(self.dd.data_dir(), self.data)                                        # the server's folder
            self.assertEqual(self.dd.resolve("profiles", "p.json"), os.path.join(self.legacy, "profiles", "p.json"))
            self.assertEqual(self.dd.resolve("new.json"), os.path.join(self.data, "new.json"))     # new files: one folder
            os.rename(self.legacy, self.legacy + ".merged-1")                                      # after merge-folders
            self.assertEqual(self.dd.resolve("profiles", "p.json"), os.path.join(self.data, "profiles", "p.json"))

    def test_with_the_data_dir_set_the_data_folder_wins(self):
        with mock.patch.dict(os.environ, {"STONESAGE_DATA_DIR": self.data}):
            self.assertEqual(self.dd.resolve("profiles", "p.json"), os.path.join(self.data, "profiles", "p.json"))
            os.remove(os.path.join(self.data, "profiles", "p.json"))
            self.assertEqual(self.dd.resolve("profiles", "p.json"), os.path.join(self.legacy, "profiles", "p.json"))


class TestSharedMemberRules(unittest.TestCase):
    def test_plain_path(self):
        for ok in ("a.json", "data/souls/atlas/persona.md", "data/courage_trace.jsonl.1", "x/y z/file-1.txt"):
            self.assertTrue(archive.plain_path(ok), ok)
        for bad in ("", "..", "../a", "a/../b", "/a", "a//b", "a/", ".hidden", "a/.h", "a\\b", "C:/a", "a:b", "x" * 401, "/".join("d" * 9),
                    "a\x00b", "a/b/c/d/e/f/g/h/i"):
            self.assertFalse(archive.plain_path(bad), repr(bad))

    def test_soul_exports_use_the_same_rules(self):
        """soul.py's archive reader is built on archive.MemberRules: one set of rules, two callers."""
        from stonesage import soul
        src = open(soul.__file__, encoding="utf-8").read()
        self.assertIn("MemberRules", src)
        self.assertNotIn("m.isreg()", src, "the member checks live in archive.py only")


class TestDailyService(Install):
    def service(self, config=None, clock=None, tz=timezone.utc):
        cfg = dict(CONFIG, backup={"dir": self.out}) if config is None else config
        return backup.BackupService(self.loc, lambda: cfg, clock=clock or (lambda: 1_800_000_000.0), tz_fn=lambda: tz)

    def test_next_run_is_the_next_hour_in_the_installs_zone(self):
        base = datetime(2027, 1, 15, 1, 30, tzinfo=timezone.utc).timestamp()
        svc = self.service(clock=lambda: base)
        self.assertEqual(datetime.fromtimestamp(svc.next_run(), timezone.utc), datetime(2027, 1, 15, 3, 0, tzinfo=timezone.utc))
        after = datetime(2027, 1, 15, 3, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(svc.next_run(after), timezone.utc), datetime(2027, 1, 16, 3, 0, tzinfo=timezone.utc))
        from zoneinfo import ZoneInfo
        try:
            zone = ZoneInfo("America/Chicago")
        except Exception:
            self.skipTest("no time zone database")
        local = self.service(clock=lambda: base, tz=zone)
        nxt = datetime.fromtimestamp(local.next_run(), zone)
        self.assertEqual((nxt.hour, nxt.minute, nxt.day), (3, 0, 15))      # 01:30 UTC is 20:30 the evening before: the next 03:00 is the 15th, local

    def test_the_hour_setting_and_the_off_switch(self):
        base = datetime(2027, 1, 15, 1, 30, tzinfo=timezone.utc).timestamp()
        svc = self.service(dict(CONFIG, backup={"hour": 5}), clock=lambda: base)
        self.assertEqual(datetime.fromtimestamp(svc.next_run(), timezone.utc).hour, 5)
        off = self.service(dict(CONFIG, backup={"enabled": False}), clock=lambda: base)
        self.assertIsNone(off.next_run())
        self.assertIsNone(off.start(), "no thread when automatic backups are off")
        self.assertIsNone(off.status()["next_run"])

    def test_run_now_and_status(self):
        svc = self.service()
        self.assertIsNone(svc.status()["last"])
        res = svc.run_now("test")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["files"], 5)
        st = svc.status()
        self.assertEqual(st["count"], 1)
        self.assertEqual(st["last"]["file"], res["file"])
        self.assertGreater(st["last"]["size"], 100)
        self.assertGreaterEqual(st["last"]["files"], 5)
        self.assertEqual((st["keep"], st["hour"], st["enabled"]), (14, 3, True))
        self.assertIsNone(st["last_error"])
        self.assertIn("no download", st["note"].lower())
        self.assertNotIn(SECRET, json.dumps(st))

    def test_a_failed_backup_is_reported_not_raised(self):
        svc = self.service()
        with mock.patch.object(backup, "create", side_effect=OSError("disk full")):
            res = svc.run_now()
        self.assertEqual(res["ok"], False)
        self.assertIn("disk full", res["error"])
        self.assertIn("disk full", svc.status()["last_error"])
        self.assertTrue(svc.run_now()["ok"])
        self.assertIsNone(svc.status()["last_error"])

    def test_two_backups_do_not_run_at_once(self):
        svc = self.service()
        self.assertTrue(svc._lock.acquire(blocking=False))
        try:
            res = svc.run_now()
            self.assertFalse(res["ok"])
            self.assertIn("already running", res["error"])
            self.assertTrue(svc.status()["running"])
        finally:
            svc._lock.release()


class TestCommandLine(Install):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = backup.main(["--config", self.config_file, "--data-dir", self.data] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_create_list_restore(self):
        code, out, _ = self.run_cli("create", "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("login key and every token", out)
        self.assertNotIn(SECRET, out)
        code, out, _ = self.run_cli("list", "--dir", self.out)
        self.assertEqual(code, 0)
        self.assertIn("holds secrets", out)
        archive_path = backup.list_backups(self.out)[0]
        self.write(self.config_file, "{}")
        code, out, _ = self.run_cli("restore", archive_path, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would replace", out)
        self.assertEqual(self.read(self.config_file), "{}")
        with mock.patch.object(backup, "server_running", return_value=False):
            code, out, _ = self.run_cli("restore", archive_path)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.read(self.config_file))["api"]["key"], SECRET)
        self.assertIn(".pre-restore-", out)

    def test_a_refusal_is_exit_code_2_with_a_message(self):
        junk = os.path.join(self.tmp, "junk.tar.gz")
        with open(junk, "wb") as f:
            f.write(b"nope")
        code, _, err = self.run_cli("restore", junk)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_running_server_needs_yes_on_the_command_line(self):
        self.run_cli("create", "--out", self.out)
        archive_path = backup.list_backups(self.out)[0]
        with mock.patch.object(backup, "server_running", return_value=True):
            code, _, err = self.run_cli("restore", archive_path)
            self.assertEqual(code, 2)
            self.assertIn("--yes", err)
            self.assertEqual(self.run_cli("restore", archive_path, "--yes")[0], 0)


class TestRoutes(Install):
    """GET /api/backup/status and POST /api/backup/run through the real handler; there is no download route."""

    def setUp(self):
        super().setUp()
        from route_harness import call
        from stonesage import server
        self.call, self.server = call, server
        svc = backup.BackupService(self.loc, lambda: dict(CONFIG, backup={"dir": self.out}), tz_fn=lambda: timezone.utc)
        patcher = mock.patch.object(server, "get_backup_service", return_value=svc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.svc = svc

    def test_the_routes_need_the_login(self):
        self.assertEqual(self.call("GET", "/api/backup/status", auth=False)[0], 401)
        self.assertEqual(self.call("POST", "/api/backup/run", {}, auth=False)[0], 401)

    def test_status_and_run(self):
        status, _, payload = self.call("GET", "/api/backup/status")
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertEqual((data["ok"], data["count"], data["last"]), (True, 0, None))
        self.assertIsNotNone(data["next_run"])
        status, _, payload = self.call("POST", "/api/backup/run", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        data = json.loads(self.call("GET", "/api/backup/status")[2])
        self.assertEqual(data["count"], 1)
        self.assertTrue(data["last"]["file"].startswith("stonesage-backup-"))

    def test_a_failed_run_is_a_500_with_the_reason(self):
        with mock.patch.object(backup, "create", side_effect=OSError("no space")):
            status, _, payload = self.call("POST", "/api/backup/run", {})
        self.assertEqual(status, 500)
        self.assertIn("no space", json.loads(payload)["error"])

    def test_there_is_no_way_to_download_a_backup(self):
        self.svc.run_now()
        name = os.path.basename(backup.list_backups(self.out)[0])
        for path in ("/api/backup/download", f"/api/backup/download?file={name}", f"/api/backup/{name}", "/api/backup/file",
                     f"/api/uploads/{name}", f"/data/backups/{name}", f"/backups/{name}"):
            status, _, payload = self.call("GET", path)
            self.assertIn(status, (404, 400, 403, 401), path)
            self.assertNotIn(SECRET.encode(), payload)
            self.assertNotEqual(payload[:2], b"\x1f\x8b", "a gzip archive came back from " + path)
        import importlib.util
        src = open(importlib.util.find_spec("stonesage.server").origin, encoding="utf-8").read()      # however it is installed
        self.assertNotIn("/api/backup/download", src)


if __name__ == "__main__":
    unittest.main()
