"""Off-box copies of backups (stonesage/backup_dest.py and its use in stonesage/backup.py): no destinations = nothing changes, a mounted
folder (copied, 0600, pruned only by backup name, never created), rclone (exact argument lists, crypt required unless allowed,
pruning, missing program, timeout), failures that never touch the local backup, the status route without secrets, the command line.
Offline: a fake rclone is injected, no rclone is ever started. Made-up names only (Alex, Sam, Bella, Rex)."""

import io
import json
import os
import shutil
import subprocess
import sys
import time
import types
import unittest
from datetime import timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_backup as tb  # noqa: E402
from stonesage import backup, backup_dest  # noqa: E402

CONFIG, SECRET = tb.CONFIG, tb.SECRET
EXE = "/opt/fake/rclone"
OLD_STAMPS = ["20260101T030000Z", "20260102T030000Z", "20260103T030000Z", "20260104T030000Z"]


def bname(stamp):
    return f"stonesage-backup-{stamp}.tar.gz"


class FakeRclone:
    """Stands in for the rclone program: answers listremotes / copyto / lsf / deletefile from a small in-memory model."""

    def __init__(self, remotes=None, store=None, fail=None, raises=None):
        self.remotes = {"cryptdrive": "crypt", "plaindrive": "drive"} if remotes is None else remotes
        self.store = {k: set(v) for k, v in (store or {}).items()}        # "cryptdrive:StoneSage" -> {file names}
        self.fail = fail or {}                                              # command -> (exit code, stderr)
        self.raises = raises or {}                                          # command -> exception
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), "an argument list, never a shell string"
        cmd = argv[1]
        if cmd in self.raises:
            raise self.raises[cmd]
        if cmd in self.fail:
            return self.fail[cmd][0], "", self.fail[cmd][1]
        if cmd == "listremotes":
            return 0, "".join(f"{n}:".ljust(14) + f"{t}\n" for n, t in self.remotes.items()), ""
        if cmd == "copyto":
            src, dst = argv[-2], argv[-1]
            base, _, name = dst.rpartition("/") if "/" in dst else (dst.partition(":")[0] + ":", "", dst.partition(":")[2])
            assert os.path.isfile(src)
            self.store.setdefault(base, set()).add(name)
            return 0, "", ""
        if cmd == "lsf":
            return 0, "".join(n + "\n" for n in sorted(self.store.get(argv[-1], ()))), ""
        if cmd == "deletefile":
            base, _, name = argv[-1].rpartition("/")
            self.store.get(base, set()).discard(name)
            return 0, "", ""
        raise AssertionError(f"unexpected rclone command {argv}")

    def commands(self):
        return [c[0][1] for c in self.calls]


class Dest(tb.Install):
    def setUp(self):
        super().setUp()
        self.nas = os.path.join(self.tmp, "nas-share")
        os.makedirs(self.nas)

    def cfg(self, *dests, **backup_settings):
        return dict(CONFIG, backup=dict({"dir": self.out, "destinations": list(dests)}, **backup_settings))

    def folder(self, **kw):
        return dict({"name": "nas", "type": "folder", "path": self.nas}, **kw)

    def rclone(self, remote="cryptdrive:StoneSage", **kw):
        return dict({"name": "cloud", "type": "rclone", "remote": remote}, **kw)

    def make(self, cfg, now=1_800_000_000.0, **kw):
        return backup.create(self.loc, cfg, out_dir=self.out, now=now, push=True, **kw)

    def push(self, cfg, fake=None, which=lambda n: EXE, only="", archive=None):
        archive = archive or self.create(now=1_800_000_000.0).path
        fake = fake or FakeRclone()
        rows = backup.push_to_destinations(archive, self.loc, cfg, only=only, runner=fake, which=which)
        return rows, fake, archive

    def state(self):
        with open(os.path.join(self.out, backup_dest.STATE_NAME), encoding="utf-8") as f:
            return json.load(f)["destinations"]


class TestSettings(unittest.TestCase):
    def load(self, *entries, **sec):
        return backup_dest.load_destinations({"backup": dict(sec, destinations=list(entries))})

    def test_absent_null_and_empty_are_no_destinations(self):
        for cfg in (None, {}, {"backup": {}}, {"backup": None}, {"backup": {"destinations": None}}, {"backup": {"destinations": []}}):
            self.assertEqual(backup_dest.load_destinations(cfg), [], cfg)
        self.assertFalse(backup_dest.has_work({"backup": {"destinations": []}}))

    def test_defaults_per_type(self):
        f, r = self.load({"name": "nas", "type": "folder", "path": os.path.abspath("nas")},
                         {"name": "cloud", "type": "rclone", "remote": "drive:Backups"}, keep=5)
        self.assertEqual((f.error, f.keep, f.enabled, f.require_encryption, f.allow_unencrypted), ("", 5, True, False, False))
        self.assertEqual((r.error, r.keep, r.timeout_s, r.require_encryption, r.rclone), ("", 5, 1800.0, True, "rclone"))
        r2, = self.load({"name": "c", "type": "rclone", "remote": "drive:x", "keep": 2, "rclone_path": "/opt/rc/rclone"}, rclone_path="/x/rc")
        self.assertEqual((r2.keep, r2.rclone), (2, "/opt/rc/rclone"))
        r3, = self.load({"name": "c", "type": "rclone", "remote": "drive:x"}, rclone_path="/x/rc")
        self.assertEqual(r3.rclone, "/x/rc")
        self.assertEqual(self.load({"name": "n", "type": "folder", "path": os.path.abspath("n")}, keep=0)[0].keep, 14)

    def test_each_kind_of_mistake_is_one_clear_error_on_that_entry_only(self):
        good = {"name": "nas", "type": "folder", "path": os.path.abspath("nas")}
        bad = [
            ({"name": "a", "type": "ftp", "path": "/x"}, "type must be one of"),
            ({"name": "a", "type": "folder"}, "needs \"path\""),
            ({"name": "a", "type": "folder", "path": "relative/dir"}, "absolute"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "keep": 0}, "keep must be"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "keep": True}, "keep must be"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "keep": "3"}, "keep must be"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "enabled": "no"}, "enabled must be"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "timeout_s": 0}, "timeout_s"),
            ({"name": "a", "type": "folder", "path": os.path.abspath("x"), "require_encryption": "yes"}, "require_encryption must be"),
            ({"name": "a", "type": "rclone"}, "needs \"remote\""),
            ({"name": "a", "type": "rclone", "remote": "no-colon"}, "name:folder"),
            ({"name": "a", "type": "rclone", "remote": ":sftp,host=192.0.2.5,pass=x:/b"}, "connection strings"),
            ({"name": "a", "type": "rclone", "remote": "-flag:path"}, "name:folder"),
            ({"name": "a", "type": "rclone", "remote": "C:\\Backups\\Stone"}, "name:folder"),
            ({"name": "a", "type": "rclone", "remote": "x:line\nbreak"}, "needs \"remote\""),
            ({"name": "", "type": "folder", "path": os.path.abspath("x")}, "name must be"),
            ({"type": "folder", "path": os.path.abspath("x")}, "name must be"),
            ({"name": "../x", "type": "folder", "path": os.path.abspath("x")}, "name must be"),
            ("not an object", "must be an object"),
        ]
        for entry, message in bad:
            dests = self.load(entry, good)
            self.assertIn(message, dests[0].error, entry)
            self.assertEqual(dests[1].error, "", "a mistake in one entry must not break the next")
        twice = self.load(good, dict(good))
        self.assertEqual(twice[0].error, "")
        self.assertIn("used by another", twice[1].error)
        self.assertIn("must be a list", backup_dest.load_destinations({"backup": {"destinations": {"name": "x"}}})[0].error)

    def test_backup_settings_is_unchanged_by_destinations(self):
        self.assertEqual(backup.backup_settings({"backup": {"destinations": [{"name": "n"}], "rclone_path": "/x"}}),
                         {"enabled": True, "dir": "", "keep": 14, "hour": 3})

    def test_backup_names_and_their_order(self):
        for ok in ("stonesage-backup-20270115T030000Z.tar.gz", "stonesage-backup-20270115T030000Z-2.tar.gz"):
            self.assertTrue(backup_dest.is_backup_name(ok), ok)
        for bad in ("notes.txt", "stonesage-backup-old.tar.gz", "stonesage-backup-20270115T030000Z.tar.gz.partial",
                    "stonesage-backup-20270115T030000Z.tar.gz.bak", "x/stonesage-backup-20270115T030000Z.tar.gz", "stonesage-backup-.tar.gz"):
            self.assertFalse(backup_dest.is_backup_name(bad), bad)
        names = ["stonesage-backup-20270115T030000Z-3.tar.gz", "stonesage-backup-20270115T030001Z.tar.gz",
                 "stonesage-backup-20270115T030000Z.tar.gz", "stonesage-backup-20270115T030000Z-2.tar.gz"]
        self.assertEqual(sorted(names, key=backup_dest.sort_key),
                         ["stonesage-backup-20270115T030000Z.tar.gz", "stonesage-backup-20270115T030000Z-2.tar.gz",
                          "stonesage-backup-20270115T030000Z-3.tar.gz", "stonesage-backup-20270115T030001Z.tar.gz"])
        self.assertEqual(backup.NAME_PREFIX, backup_dest.NAME_PREFIX)


class TestNoDestinations(Dest):
    def test_nothing_runs_when_none_are_configured(self):
        for cfg in (dict(CONFIG), self.cfg(), dict(CONFIG, backup={"destinations": None})):
            with mock.patch.object(backup_dest.Pusher, "push", side_effect=AssertionError("must not be called")), \
                    mock.patch.object(backup_dest, "run_program", side_effect=AssertionError("no program")):
                res = backup.create(self.loc, cfg, out_dir=self.out, push=True, now=time.time())
            self.assertEqual(res.pushes, [])
        self.assertFalse(os.path.exists(os.path.join(self.out, backup_dest.STATE_NAME)), "no state file without destinations")

    def test_the_service_answers_as_before(self):
        svc = backup.BackupService(self.loc, lambda: dict(CONFIG, backup={"dir": self.out}), tz_fn=lambda: timezone.utc)
        out = svc.run_now()
        self.assertEqual(sorted(out), ["errors", "file", "files", "ok", "size", "skipped"])
        self.assertEqual(sorted(svc.run_now(background_push=True)), ["errors", "file", "files", "ok", "size", "skipped"])
        st = svc.status()
        self.assertEqual(st["destinations"], [])
        self.assertFalse(st["pushing"])
        self.assertFalse(os.path.exists(os.path.join(self.out, backup_dest.STATE_NAME)))


class TestFolder(Dest):
    def test_copied_complete_and_owner_only_with_no_partial_left(self):
        res = self.make(self.cfg(self.folder()))
        self.assertEqual(res.pushes, [{"name": "nas", "ok": True, "file": os.path.basename(res.path)}])
        copy = os.path.join(self.nas, os.path.basename(res.path))
        with open(copy, "rb") as a, open(res.path, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual(os.listdir(self.nas), [os.path.basename(res.path)])
        backup.read_manifest(copy)
        if os.name == "posix":
            self.assertEqual(os.stat(copy).st_mode & 0o777, 0o600)
        rec = self.state()["nas"]
        self.assertEqual((rec["last_file"], rec["last_error"]), (os.path.basename(res.path), None))
        self.assertGreater(rec["last_ok"], 1_700_000_000)

    def test_the_state_file_lives_in_the_backup_folder_and_never_ends_up_in_a_backup(self):
        managed = os.path.join(self.data, "backups")
        self.make(self.cfg(self.folder(), dir=managed))
        self.assertTrue(os.path.isfile(os.path.join(managed, backup_dest.STATE_NAME)))
        res = backup.create(self.loc, self.cfg(self.folder(), dir=managed), out_dir=self.out, now=1_800_000_100.0)
        self.assertFalse([n for n in self.names(res.path) if "destinations-state" in n or n.startswith("data/backups")])

    def test_pruning_keeps_the_newest_and_touches_only_backup_files(self):
        for stamp in OLD_STAMPS:
            self.write(os.path.join(self.nas, bname(stamp)), "old " + stamp)
        others = ["notes.txt", "stonesage-backup-old.tar.gz", bname("20260101T030000Z") + ".partial", "photos/a.jpg",
                  "stonesage-backup-20260101T030000Z.tar.gz.bak"]
        for n in others:
            self.write(os.path.join(self.nas, n), "mine")
        os.makedirs(os.path.join(self.nas, bname("20250101T030000Z")))              # a folder that looks like a backup
        res = self.make(self.cfg(self.folder(keep=3)))
        left = sorted(os.listdir(self.nas))
        newest = os.path.basename(res.path)
        self.assertEqual([n for n in left if backup_dest.is_backup_name(n) and os.path.isfile(os.path.join(self.nas, n))],
                         [bname("20260103T030000Z"), bname("20260104T030000Z"), newest])
        for n in others:
            self.assertTrue(os.path.exists(os.path.join(self.nas, n)), n)
        self.assertTrue(os.path.isdir(os.path.join(self.nas, bname("20250101T030000Z"))))

    def test_keep_defaults_to_the_backup_keep_setting(self):
        for stamp in OLD_STAMPS:
            self.write(os.path.join(self.nas, bname(stamp)), "old")
        self.make(self.cfg(self.folder(), keep=2))
        self.assertEqual(len([n for n in os.listdir(self.nas) if backup_dest.is_backup_name(n)]), 2)

    def test_the_new_copy_is_never_the_one_pruned(self):
        for stamp in ("20300101T000000Z", "20300102T000000Z"):               # newer-named files already there
            self.write(os.path.join(self.nas, bname(stamp)), "x")
        res = self.make(self.cfg(self.folder(keep=1)))
        self.assertTrue(os.path.exists(os.path.join(self.nas, os.path.basename(res.path))))

    def test_a_missing_folder_is_refused_and_not_created(self):
        gone = os.path.join(self.tmp, "mnt", "nas-not-mounted")
        res = self.make(self.cfg(self.folder(path=gone)))
        self.assertFalse(res.pushes[0]["ok"])
        self.assertIn("not an existing folder", res.pushes[0]["error"])
        self.assertFalse(os.path.exists(gone))
        self.assertFalse(os.path.exists(os.path.dirname(gone)))
        self.assertTrue(os.path.isfile(res.path), "the local backup stays")
        self.assertIn("not an existing folder", self.state()["nas"]["last_error"])

    def test_a_file_where_the_folder_should_be_is_refused(self):
        file_path = os.path.join(self.tmp, "a-file")
        self.write(file_path, "x")
        self.assertIn("not an existing folder", self.make(self.cfg(self.folder(path=file_path))).pushes[0]["error"])

    def test_a_failing_copy_leaves_the_local_backup_and_no_partial(self):
        def full(*a, **k):
            raise OSError("disk full")
        with mock.patch.object(backup_dest, "shutil", types.SimpleNamespace(copyfileobj=full, which=shutil.which)):
            res = self.make(self.cfg(self.folder()))
        self.assertFalse(res.pushes[0]["ok"])
        self.assertIn("disk full", res.pushes[0]["error"])
        self.assertEqual(os.listdir(self.nas), [])
        self.assertTrue(os.path.isfile(res.path))
        backup.read_manifest(res.path)
        self.assertIn("disk full", self.state()["nas"]["last_error"])

    def test_a_good_run_after_a_bad_one_clears_the_error_and_keeps_the_history(self):
        self.make(self.cfg(self.folder(path=os.path.join(self.tmp, "nope"))))
        self.assertTrue(self.state()["nas"]["last_error"])
        self.assertNotIn("last_ok", self.state()["nas"])
        self.make(self.cfg(self.folder()), now=1_800_000_100.0)
        rec = self.state()["nas"]
        self.assertIsNone(rec["last_error"])
        self.assertTrue(rec["last_ok"])

    def test_the_backup_folder_itself_is_refused_as_a_destination(self):
        res = self.make(self.cfg(self.folder(path=self.out)))
        self.assertIn("folder the backups are written to", res.pushes[0]["error"])
        self.assertEqual(len(backup.list_backups(self.out)), 1)

    def test_require_encryption_refuses_a_plain_folder(self):
        res = self.make(self.cfg(self.folder(require_encryption=True)))
        self.assertIn("crypt", res.pushes[0]["error"])
        self.assertEqual(os.listdir(self.nas), [])
        self.assertTrue(self.make(self.cfg(self.folder(require_encryption=True, allow_unencrypted=True)), now=1_800_000_100.0).pushes[0]["ok"])

    def test_disabled_and_broken_entries_do_not_stop_the_others(self):
        other = os.path.join(self.tmp, "usb")
        os.makedirs(other)
        cfg = self.cfg({"name": "bad", "type": "folder"}, self.folder(name="off", enabled=False), self.folder(name="usb", path=other),
                       self.folder(path=self.nas))
        with self.assertLogs("StoneSage.backup", level="ERROR") as logs:
            res = self.make(cfg)
        by = {r["name"]: r for r in res.pushes}
        self.assertEqual(sorted(by), ["bad", "nas", "usb"])
        self.assertFalse(by["bad"]["ok"])
        self.assertIn("settings:", by["bad"]["error"])
        self.assertTrue(by["usb"]["ok"] and by["nas"]["ok"])
        self.assertTrue(any("'bad' skipped" in line for line in logs.output))
        self.assertEqual(len(os.listdir(other)), 1)
        self.assertNotIn("off", self.state())

    def test_a_stale_partial_from_a_crash_is_replaced(self):
        res = self.create(now=1_800_000_000.0)
        self.write(os.path.join(self.nas, os.path.basename(res.path) + ".partial"), "half")
        rows, _f, _a = self.push(self.cfg(self.folder()), archive=res.path)
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(os.listdir(self.nas), [os.path.basename(res.path)])


class TestRclone(Dest):
    def test_the_exact_argument_lists_for_a_crypt_remote(self):
        rows, fake, archive = self.push(self.cfg(self.rclone()))
        name = os.path.basename(archive)
        self.assertEqual(rows, [{"name": "cloud", "ok": True, "file": name}])
        self.assertEqual([c[0] for c in fake.calls], [
            [EXE, "listremotes", "--long"],
            [EXE, "copyto", "--", archive, f"cryptdrive:StoneSage/{name}"],
            [EXE, "lsf", "--files-only", "--", "cryptdrive:StoneSage"]])
        self.assertTrue(all(c[1] == 1800.0 for c in fake.calls))
        self.assertEqual(fake.store["cryptdrive:StoneSage"], {name})

    def test_a_remote_at_the_root_and_a_trailing_slash(self):
        self.assertEqual(backup_dest.remote_join("cryptdrive:", "a.tar.gz"), "cryptdrive:a.tar.gz")
        self.assertEqual(backup_dest.remote_join("cryptdrive:dir/", "a.tar.gz"), "cryptdrive:dir/a.tar.gz")
        rows, fake, archive = self.push(self.cfg(self.rclone("cryptdrive:dir/")))
        self.assertEqual(fake.calls[1][0][-1], f"cryptdrive:dir/{os.path.basename(archive)}")

    def test_a_plain_remote_is_refused_before_anything_is_uploaded(self):
        rows, fake, _a = self.push(self.cfg(self.rclone("plaindrive:StoneSage")))
        self.assertFalse(rows[0]["ok"])
        self.assertIn("not crypt", rows[0]["error"])
        self.assertIn("allow_unencrypted", rows[0]["error"])
        self.assertEqual(fake.commands(), ["listremotes"])
        self.assertEqual(fake.store, {})

    def test_an_unknown_remote_is_refused(self):
        rows, fake, _a = self.push(self.cfg(self.rclone("nosuch:StoneSage")))
        self.assertIn("no remote named 'nosuch'", rows[0]["error"])
        self.assertNotIn("copyto", fake.commands())

    def test_unreadable_type_listing_is_refused(self):
        rows, fake, _a = self.push(self.cfg(self.rclone()), fake=FakeRclone(fail={"listremotes": (1, "config locked")}))
        self.assertFalse(rows[0]["ok"])
        self.assertIn("could not tell", rows[0]["error"])
        self.assertNotIn("copyto", fake.commands())

    def test_allow_unencrypted_uploads_and_warns_every_run(self):
        cfg = self.cfg(self.rclone("plaindrive:StoneSage", allow_unencrypted=True))
        archive = self.create(now=1_800_000_000.0).path
        for _ in range(2):
            with self.assertLogs("StoneSage.backup", level="WARNING") as logs:
                rows, fake, _a = self.push(cfg, archive=archive)
            self.assertTrue(rows[0]["ok"])
            self.assertTrue(any("without StoneSage-side encryption" in line for line in logs.output))
            self.assertEqual(fake.commands(), ["copyto", "lsf"])

    def test_require_encryption_off_uploads_without_asking_the_type_and_warns(self):
        with self.assertLogs("StoneSage.backup", level="WARNING"):
            rows, fake, _a = self.push(self.cfg(self.rclone("plaindrive:x", require_encryption=False)))
        self.assertTrue(rows[0]["ok"])
        self.assertEqual(fake.commands(), ["copyto", "lsf"])

    def test_pruning_deletes_only_backup_names_beyond_keep_and_never_the_new_one(self):
        fake = FakeRclone(store={"cryptdrive:StoneSage": {bname(s) for s in OLD_STAMPS} | {"notes.txt", "other.tar.gz", "stonesage-backup-old.tar.gz"}})
        rows, fake, archive = self.push(self.cfg(self.rclone(keep=3)), fake=fake)
        name = os.path.basename(archive)
        self.assertTrue(rows[0]["ok"])
        deletes = [c[0] for c in fake.calls if c[0][1] == "deletefile"]
        self.assertEqual(deletes, [[EXE, "deletefile", "--", f"cryptdrive:StoneSage/{bname(s)}"] for s in OLD_STAMPS[:2]])
        self.assertEqual(fake.store["cryptdrive:StoneSage"],
                         {bname(OLD_STAMPS[2]), bname(OLD_STAMPS[3]), name, "notes.txt", "other.tar.gz", "stonesage-backup-old.tar.gz"})

    def test_a_pruning_failure_is_a_warning_not_a_failed_copy(self):
        rows, fake, archive = self.push(self.cfg(self.rclone()), fake=FakeRclone(fail={"lsf": (3, "directory not found")}))
        self.assertTrue(rows[0]["ok"])
        self.assertIn("not pruned", rows[0]["warning"])
        self.assertEqual(self.state()["cloud"]["last_file"], os.path.basename(archive))
        self.assertIn("not pruned", self.state()["cloud"]["last_warning"])

    def test_a_missing_program_says_how_to_install_it(self):
        rows, fake, _a = self.push(self.cfg(self.rclone()), which=lambda n: None)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("https://rclone.org/install/", rows[0]["error"])
        self.assertEqual(fake.calls, [])
        rows, _f, _a = self.push(self.cfg(self.rclone()), fake=FakeRclone(raises={"listremotes": FileNotFoundError()}))
        self.assertIn("rclone.org/install", rows[0]["error"])

    def test_the_program_comes_from_rclone_path_else_the_path(self):
        seen = []
        which = lambda n: seen.append(n) or "/resolved/" + n
        self.push(self.cfg(self.rclone(rclone_path="/opt/rc/rclone")), which=which)
        self.push(self.cfg(self.rclone(), rclone_path="/shared/rclone"), which=which)
        self.push(self.cfg(self.rclone()), which=which)
        self.assertEqual(seen, ["/opt/rc/rclone", "/shared/rclone", "rclone"])

    def test_a_timeout_is_an_error_with_the_configured_limit(self):
        fake = FakeRclone(raises={"copyto": subprocess.TimeoutExpired(["rclone"], 5)})
        rows, fake, _a = self.push(self.cfg(self.rclone(timeout_s=5)), fake=fake)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("did not finish within 5 s", rows[0]["error"])
        self.assertTrue(all(c[1] == 5.0 for c in fake.calls))

    def test_a_failing_upload_reports_rclones_message_shortened(self):
        rows, _f, _a = self.push(self.cfg(self.rclone()), fake=FakeRclone(fail={"copyto": (7, "quota exceeded " + "x" * 2000)}))
        self.assertIn("exit 7", rows[0]["error"])
        self.assertIn("quota exceeded", rows[0]["error"])
        self.assertLess(len(rows[0]["error"]), 400)

    def test_the_real_runner_uses_an_argument_list_and_no_shell(self):
        completed = subprocess.CompletedProcess(["rclone"], 0, stdout="ok", stderr="")
        with mock.patch.object(backup_dest.subprocess, "run", return_value=completed) as run:
            self.assertEqual(backup_dest.run_program(["rclone", "lsf", "--", "a:b"], 12.0), (0, "ok", ""))
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["rclone", "lsf", "--", "a:b"])
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 12.0)

    def test_listremotes_parsing(self):
        self.assertEqual(backup_dest.parse_remote_types("cryptdrive: crypt\nplain:    drive\nweird name:  s3\n\njunk\n"),
                         {"cryptdrive": "crypt", "plain": "drive", "weird name": "s3"})


class TestWhoPushes(Dest):
    def test_create_in_the_background_and_a_destination_error_never_raises(self):
        cfg = self.cfg(self.folder())
        backup.create(self.loc, cfg, out_dir=self.out, now=1_800_000_000.0, push="background")
        self.wait(lambda: os.listdir(self.nas))
        with mock.patch.object(backup, "push_to_destinations", side_effect=RuntimeError("boom")):
            res = backup.create(self.loc, cfg, out_dir=self.out, now=1_800_000_100.0, push=True)
        self.assertEqual(res.pushes[0]["ok"], False)
        self.assertTrue(os.path.isfile(res.path))

    @staticmethod
    def wait(cond, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return
            time.sleep(0.02)
        raise AssertionError("timed out")

    def test_a_push_is_off_unless_asked_for(self):
        res = backup.create(self.loc, self.cfg(self.folder()), out_dir=self.out, now=1_800_000_000.0)
        self.assertEqual((res.pushes, os.listdir(self.nas)), ([], []))

    def test_merge_folders_copies_its_safety_backup_off_the_box(self):
        hdir = os.path.join(self.backend, "data")
        self.write(os.path.join(hdir, "agent_dna", "coder-agent.json"), '{"name": "Atlas"}')
        self.loc.harness_dir = hdir
        rep = backup.merge_folders(self.loc, self.cfg(self.folder(), dir=os.path.join(self.data, "backups")), prefer="newer",
                                   running=lambda: False)
        self.assertEqual(os.listdir(self.nas), [os.path.basename(rep.backup)])

    def test_the_server_and_the_cli_ask_for_the_copy_after_a_pre_upgrade_backup(self):
        import importlib.util
        for mod, want in (("stonesage.config_migrate", 'reason="before settings upgrade", push=True'),
                          ("stonesage.server", 'reason="before settings upgrade", push="background"')):
            with open(importlib.util.find_spec(mod).origin, encoding="utf-8") as f:
                self.assertIn(want, f.read(), mod)


class TestServiceAndStatus(Dest):
    def service(self, cfg, **kw):
        return backup.BackupService(self.loc, lambda: cfg, tz_fn=lambda: timezone.utc, **kw)

    def test_run_now_copies_and_the_status_shows_each_destination(self):
        fake = FakeRclone()
        svc = self.service(self.cfg(self.folder(), self.rclone()), dest_runner=fake, dest_which=lambda n: EXE)
        out = svc.run_now("test")
        self.assertTrue(out["ok"])
        self.assertEqual([(r["name"], r["ok"]) for r in out["destinations"]], [("nas", True), ("cloud", True)])
        st = svc.status()
        self.assertEqual([r["name"] for r in st["destinations"]], ["nas", "cloud"])
        for row in st["destinations"]:
            self.assertEqual(sorted(row), sorted(["name", "type", "target", "enabled", "keep", "require_encryption", "allow_unencrypted",
                                                  "settings_error", "last_attempt", "last_ok", "last_file", "last_error", "last_warning"]))
            self.assertTrue(row["last_ok"] and row["last_file"] and row["last_error"] is None)
        self.assertEqual(st["destinations"][1]["target"], "cryptdrive:StoneSage")
        self.assertEqual(st["destinations"][0]["target"], self.nas)

    def test_a_failed_destination_does_not_fail_or_taint_the_backup(self):
        svc = self.service(self.cfg(self.folder(path=os.path.join(self.tmp, "unmounted"))))
        out = svc.run_now()
        self.assertTrue(out["ok"])
        self.assertFalse(out["destinations"][0]["ok"])
        st = svc.status()
        self.assertEqual((st["count"], st["last_error"]), (1, None))
        self.assertIn("not an existing folder", st["destinations"][0]["last_error"])
        self.assertIsNone(st["destinations"][0]["last_ok"])

    def test_a_broken_entry_shows_its_settings_error_in_the_status(self):
        svc = self.service(self.cfg({"name": "oops", "type": "dropbox"}, self.folder()))
        st = svc.status()
        self.assertIn("type must be one of", st["destinations"][0]["settings_error"])
        self.assertIsNone(st["destinations"][1]["settings_error"])
        self.assertTrue(svc.run_now()["destinations"][1]["ok"])

    def test_background_push_returns_at_once_and_the_copy_lands(self):
        svc = self.service(self.cfg(self.folder()))
        out = svc.run_now(background_push=True)
        self.assertEqual((out["ok"], out["pushing"]), (True, True))
        self.assertNotIn("destinations", out)
        TestWhoPushes.wait(lambda: svc.status()["destinations"][0]["last_ok"])
        self.assertEqual(len(os.listdir(self.nas)), 1)

    def test_the_status_never_holds_a_secret_from_the_settings(self):
        entry = self.rclone(token="tok-hunter2-not-for-display", password="hunter2-not-for-display", client_secret="cs-hunter2")
        cfg = self.cfg(self.folder(secret="hunter2-not-for-display"), entry)
        cfg["operator"] = {"name": "Alex"}
        svc = self.service(cfg, dest_runner=FakeRclone(fail={"copyto": (1, "boom")}), dest_which=lambda n: EXE)
        svc.run_now()
        with open(os.path.join(self.out, backup_dest.STATE_NAME), encoding="utf-8") as f:
            text = json.dumps(svc.status()) + f.read()
        for secret in (SECRET, "hunter2", "cs-hunter2", "Alex"):
            self.assertNotIn(secret, text)


class TestRoutes(Dest):
    def setUp(self):
        super().setUp()
        from route_harness import call
        from stonesage import server
        self.call = call
        cfg = self.cfg(self.folder(secret="hunter2-not-for-display"))
        svc = backup.BackupService(self.loc, lambda: cfg, tz_fn=lambda: timezone.utc)
        patcher = mock.patch.object(server, "get_backup_service", return_value=svc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_returns_without_waiting_and_the_status_shows_the_copy(self):
        status, _, payload = self.call("POST", "/api/backup/run", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["pushing"])
        TestWhoPushes.wait(lambda: json.loads(self.call("GET", "/api/backup/status")[2])["destinations"][0]["last_ok"])
        status, _, payload = self.call("GET", "/api/backup/status")
        data = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual([(d["name"], d["last_error"]) for d in data["destinations"]], [("nas", None)])
        for secret in (SECRET, "hunter2"):
            self.assertNotIn(secret.encode(), payload)

    def test_the_status_route_still_needs_the_login(self):
        self.assertEqual(self.call("GET", "/api/backup/status", auth=False)[0], 401)


class TestCommandLine(Dest):
    def run_cli(self, *args, config=None):
        self.write(self.config_file, json.dumps(config if config is not None else self.cfg(self.folder())))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = backup.main(["--config", self.config_file, "--data-dir", self.data] + list(args))
        return code, out.getvalue(), err.getvalue()

    def test_create_pushes_unless_told_not_to(self):
        code, out, _ = self.run_cli("create", "--out", self.out, "--no-push")
        self.assertEqual((code, os.listdir(self.nas)), (0, []))
        self.assertNotIn("copied to", out)
        code, out, _ = self.run_cli("create", "--out", self.out)
        self.assertEqual(code, 0)
        self.assertIn("copied to 'nas'", out)
        self.assertEqual(len(os.listdir(self.nas)), 1)

    def test_create_reports_a_failed_copy_with_exit_1_and_keeps_the_backup(self):
        code, out, _ = self.run_cli("create", "--out", self.out, config=self.cfg(self.folder(path=os.path.join(self.tmp, "nope"))))
        self.assertEqual(code, 1)
        self.assertIn("NOT copied to 'nas'", out)
        self.assertEqual(len(backup.list_backups(self.out)), 1)

    def test_push_the_newest_a_named_file_and_one_destination(self):
        other = os.path.join(self.tmp, "usb")
        os.makedirs(other)
        cfg = self.cfg(self.folder(), self.folder(name="usb", path=other))
        a = self.create(now=1_800_000_000.0).path
        b = self.create(now=1_800_000_100.0).path
        code, out, _ = self.run_cli("push", "--dest", "usb", config=cfg)
        self.assertEqual((code, os.listdir(self.nas), os.listdir(other)), (0, [], [os.path.basename(b)]))
        code, out, _ = self.run_cli("push", a, config=cfg)
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.nas), [os.path.basename(a)])
        self.assertIn("copying " + os.path.basename(a), out)

    def test_push_refusals(self):
        self.create(now=1_800_000_000.0)
        code, _, err = self.run_cli("push", "--dest", "nosuch")
        self.assertEqual(code, 1)
        self.assertIn("no destination named 'nosuch'", err)
        code, _, err = self.run_cli("push", config=dict(CONFIG, backup={"dir": self.out}))
        self.assertEqual(code, 1)
        self.assertIn("no destinations are configured", err)
        junk = os.path.join(self.tmp, "junk.tar.gz")
        self.write(junk, "nope")
        self.assertEqual(self.run_cli("push", junk)[0], 1)
        renamed = os.path.join(self.tmp, "my-copy.tar.gz")
        with open(backup.list_backups(self.out)[0], "rb") as a, open(renamed, "wb") as b:
            b.write(a.read())
        code, _, err = self.run_cli("push", renamed)
        self.assertEqual(code, 1)
        self.assertIn("not named like a backup", err)
        self.assertEqual(os.listdir(self.nas), [])

    def test_push_exit_code_is_1_when_a_destination_fails(self):
        self.create(now=1_800_000_000.0)
        code, out, _ = self.run_cli("push", config=self.cfg(self.folder(path=os.path.join(self.tmp, "nope"))))
        self.assertEqual(code, 1)
        self.assertIn("NOT copied to 'nas'", out)

    def test_push_a_disabled_destination_by_name_says_so(self):
        self.create(now=1_800_000_000.0)
        code, out, _ = self.run_cli("push", "--dest", "nas", config=self.cfg(self.folder(enabled=False)))
        self.assertEqual(code, 1)
        self.assertIn("switched off", out)


if __name__ == "__main__":
    unittest.main()
