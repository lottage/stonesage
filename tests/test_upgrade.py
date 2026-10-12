"""The upgrade path (stonesage/config_migrate.py, the layer versions and API pins, lodestone/compat.py): settings steps are pure and
tested one by one, a newer config is refused, an older one is backed up and then migrated, and a layer that finds the wrong layer
below it says so in one sentence. Offline; made-up names (Alex, Sam, Bella, Rex)."""

import copy
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "StoneSage", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import lodestone  # noqa: E402
import stonesage  # noqa: E402
from lodestone import compat  # noqa: E402
from stonesage import backup, backup_dest, config_migrate as cm  # noqa: E402

# Hearth is another layer (and another repository once the layers are split): found by name, absent = its checks are skipped
hearth = importlib.import_module("hearth") if importlib.util.find_spec("hearth") else None
DOCKER_DIR = next((d for d in (os.path.join(ROOT, "StoneSage", "docker"), os.path.join(ROOT, "docker")) if os.path.isdir(d)), None)
BUILDER = os.path.join(ROOT, "server setup", "build_public_repos.py")

OLD = {
    "apps": ["home"],
    "garden": {"zones": {"beds": {"name": "Beds", "valve": "valve.beds"}}, "rain_delay_switch": "switch.rain"},
    "hass": {"url": "http://192.0.2.10:8123", "token": "t0ken"},
    "cluster": {"valkey_host": "192.0.2.11", "coordinator_url": "http://192.0.2.12:8001/v1"},
    "voice": {"host": "192.0.2.13", "stt_url": "http://192.0.2.14:8210"},
    "operator": {"name": "Alex"},
}


class TestSteps(unittest.TestCase):
    def test_the_list_is_well_formed(self):
        self.assertEqual(cm.LATEST, len(cm.MIGRATIONS))
        names = [n for n, _ in cm.MIGRATIONS]
        self.assertEqual(len(names), len(set(names)))
        for name, fn in cm.MIGRATIONS:
            self.assertTrue((fn.__doc__ or "").strip(), f"step {name} needs a docstring that says what it moves and why")
        self.assertGreaterEqual(cm.LATEST, 5)

    def test_every_step_is_pure(self):
        for name, fn in cm.MIGRATIONS:
            before = copy.deepcopy(OLD)
            new, notes = fn(OLD)
            self.assertEqual(OLD, before, f"step {name} changed the settings it was given")
            self.assertIsNot(new, OLD)
            self.assertIsInstance(notes, list)

    def test_every_step_does_nothing_to_settings_it_does_not_apply_to(self):
        plain = {"server": {"port": 8888}, "operator": {"name": "Sam"}, "apps": ["hearth"]}
        for name, fn in cm.MIGRATIONS:
            new, notes = fn(plain)
            self.assertEqual(new, plain, name)
            self.assertEqual(notes, [], name)

    def test_apps_home_becomes_hearth(self):
        new, notes = cm.apps_home_to_hearth({"apps": ["home", "other", "hearth", "home"]})
        self.assertEqual(new["apps"], ["hearth", "other"])
        self.assertTrue(notes)
        self.assertEqual(cm.apps_home_to_hearth({"apps": "home"})[0], {"apps": "home"})      # not a list: left alone

    def test_garden_becomes_irrigation(self):
        new, notes = cm.garden_to_irrigation(OLD)
        self.assertNotIn("garden", new)
        self.assertEqual(new["irrigation"], OLD["garden"])
        self.assertIn("moved", notes[0])

    def test_garden_is_left_alone_when_irrigation_exists(self):
        cfg = {"garden": {"zones": {"old": {}}}, "irrigation": {"zones": {"new": {}}}}
        new, notes = cm.garden_to_irrigation(cfg)
        self.assertEqual(new, cfg)                       # nothing thrown away; the code already reads irrigation first
        self.assertIn("both are set", notes[0])

    def test_hass_becomes_homeassistant_only_when_that_is_free(self):
        new, _ = cm.hass_to_homeassistant(OLD)
        self.assertEqual(new["homeassistant"], OLD["hass"])
        self.assertNotIn("hass", new)
        both = {"hass": {"token": "a"}, "homeassistant": {"token": "b"}}
        self.assertEqual(cm.hass_to_homeassistant(both)[0], both)

    def test_valkey_host_and_voice_host_move_into_their_sections(self):
        new, _ = cm.valkey_host_to_url(OLD)
        self.assertEqual(new["cluster"], {"valkey_url": "192.0.2.11", "coordinator_url": "http://192.0.2.12:8001/v1"})
        new, _ = cm.voice_host_to_legacy_host(OLD)
        self.assertEqual(new["voice"], {"legacy_host": "192.0.2.13", "stt_url": "http://192.0.2.14:8210"})
        only = cm.voice_host_to_legacy_host({"voice": {"host": "x"}})[0]
        self.assertEqual(only, {"voice": {"legacy_host": "x"}})

    def test_a_section_that_is_not_a_section_is_never_clobbered(self):
        cfg = {"hass": {"url": "x"}, "homeassistant": "oops"}
        self.assertEqual(cm.hass_to_homeassistant(cfg)[0], cfg)
        cfg = {"cluster": "text", "voice": {"host": "x"}}
        self.assertEqual(cm.valkey_host_to_url(cfg)[0], cfg)

    def test_the_new_places_are_the_ones_the_code_reads(self):
        from lodestone import endpoints
        from harness.core import irrigation_engine as irr
        new, _ = cm.migrate(OLD)
        self.assertEqual(endpoints.url("homeassistant", new), "http://192.0.2.10:8123")
        self.assertEqual(endpoints.url("valkey", new), "redis://192.0.2.11:6379")
        self.assertEqual(endpoints.url("voice_legacy", new), "http://192.0.2.13")
        with mock.patch.object(irr, "_load_stonesage_config", return_value=new):
            self.assertEqual(list(irr.zone_map()), ["beds"])
        # and they give the same answers as the old names did
        self.assertEqual(endpoints.url("homeassistant", OLD), endpoints.url("homeassistant", new))
        self.assertEqual(endpoints.url("valkey", OLD), endpoints.url("valkey", new))
        self.assertEqual(endpoints.url("voice_legacy", OLD), endpoints.url("voice_legacy", new))


class TestMigrate(unittest.TestCase):
    def test_migrate_runs_every_step_stamps_the_version_and_leaves_the_input_alone(self):
        before = copy.deepcopy(OLD)
        new, ran = cm.migrate(OLD)
        self.assertEqual(OLD, before)
        self.assertEqual(new["config_version"], cm.LATEST)
        self.assertEqual([n for n, _ in ran], [n for n, _ in cm.MIGRATIONS])
        self.assertEqual(new["apps"], ["hearth"])
        self.assertEqual(new["operator"], {"name": "Alex"})
        self.assertEqual(new["cluster"]["coordinator_url"], "http://192.0.2.12:8001/v1")

    def test_it_is_idempotent(self):
        once, _ = cm.migrate(OLD)
        twice, ran = cm.migrate(once)
        self.assertEqual(once, twice)
        self.assertEqual(ran, [])

    def test_only_the_steps_after_the_files_version_run(self):
        cfg = dict(OLD, config_version=1)                     # step 1 (apps) is behind it, the rest are ahead
        new, ran = cm.migrate(cfg)
        self.assertEqual(new["apps"], ["home"], "step 1 already ran for a version-1 file, so it is not run again")
        self.assertNotIn("garden", new)
        self.assertEqual([n for n, _ in ran], [n for n, _ in cm.MIGRATIONS[1:]])

    def test_a_config_from_the_future_is_refused(self):
        with self.assertRaises(cm.ConfigTooNew) as cm_:
            cm.migrate({"config_version": cm.LATEST + 1})
        msg = str(cm_.exception)
        self.assertIn(str(cm.LATEST + 1), msg)
        self.assertIn("Upgrade StoneSage", msg)
        self.assertIn("Nothing was changed", msg)

    def test_a_config_version_that_is_not_a_whole_number_is_an_error(self):
        for bad in ("3", 1.5, True, -1, None, [], {}):
            with self.assertRaises(cm.ConfigError, msg=repr(bad)):
                cm.version_of({"config_version": bad})
        self.assertEqual(cm.version_of({}), 0)


class TestUpgradeFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upgrade-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "config.json")

    def put(self, data, bom=False):
        with open(self.path, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
            json.dump(data, f)

    def get(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_no_file_means_nothing_to_do(self):
        self.assertEqual(cm.upgrade_file(self.path)["status"], "no-config")

    def test_a_current_file_is_left_untouched(self):
        self.put({"config_version": cm.LATEST, "garden": {"a": 1}})
        called = []
        report = cm.upgrade_file(self.path, backup=lambda: called.append(1))
        self.assertEqual(report["status"], "current")
        self.assertEqual(called, [])
        self.assertIn("garden", self.get())

    def test_an_old_file_is_backed_up_first_and_then_upgraded(self):
        self.put(OLD)
        seen = []
        report = cm.upgrade_file(self.path, backup=lambda: seen.append(self.get()))
        self.assertEqual(report["status"], "upgraded")
        self.assertEqual((report["from"], report["to"]), (0, cm.LATEST))
        self.assertEqual(seen, [OLD], "the backup ran before the file changed")
        self.assertEqual(self.get()["config_version"], cm.LATEST)
        self.assertIn("irrigation", self.get())
        self.assertEqual(len(report["steps"]), cm.LATEST)
        self.assertEqual([n for n in os.listdir(self.tmp) if n != "config.json"], [], "no temp files left")

    @unittest.skipUnless(os.name == "posix", "file modes are a POSIX thing")
    def test_the_rewritten_file_is_owner_only(self):
        self.put(OLD)
        os.chmod(self.path, 0o644)
        cm.upgrade_file(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_failed_backup_means_no_change(self):
        self.put(OLD)

        def broken():
            raise OSError("disk full")
        with self.assertLogs("StoneSage.config", level="ERROR") as logs:
            report = cm.upgrade_file(self.path, backup=broken)
        self.assertEqual(report["status"], "backup-failed")
        self.assertEqual(self.get(), OLD)
        self.assertIn("NOT done", logs.output[0])

    def test_dry_run_changes_nothing_and_does_not_back_up(self):
        self.put(OLD)
        called = []
        report = cm.upgrade_file(self.path, backup=lambda: called.append(1), dry_run=True)
        self.assertEqual(report["status"], "would-upgrade")
        self.assertEqual(called, [])
        self.assertEqual(self.get(), OLD)

    def test_a_newer_file_is_refused_and_not_touched(self):
        self.put({"config_version": cm.LATEST + 3, "x": 1})
        with self.assertRaises(cm.ConfigTooNew):
            cm.upgrade_file(self.path, backup=lambda: None)
        self.assertEqual(self.get(), {"config_version": cm.LATEST + 3, "x": 1})

    def test_a_file_that_is_not_json_is_an_error_not_a_rewrite(self):
        with open(self.path, "w") as f:
            f.write("{ this is not json")
        with self.assertRaises(cm.ConfigError):
            cm.upgrade_file(self.path)
        with open(self.path) as f:
            self.assertEqual(f.read(), "{ this is not json")
        self.put([1, 2])
        with self.assertRaises(cm.ConfigError):
            cm.upgrade_file(self.path)

    def test_a_file_with_a_byte_order_mark_reads(self):
        self.put(OLD, bom=True)
        self.assertEqual(cm.upgrade_file(self.path)["status"], "upgraded")

    def test_the_backup_taken_before_the_upgrade_restores_the_old_settings(self):
        """The rollback path of docs/UPGRADING.md, end to end: upgrade with a real backup, then restore it."""
        root = os.path.join(self.tmp, "root")
        loc = backup.Locations(config_file=self.path, data_dir=os.path.join(root, "data"), backend_dir=os.path.join(root, "be"),
                               root_dir=root)
        os.makedirs(loc.data_dir)
        with open(os.path.join(loc.data_dir, "courage_reflexes.json"), "w") as f:
            f.write("{}")
        self.put(OLD)
        out = os.path.join(self.tmp, "out")
        report = cm.upgrade_file(self.path, backup=lambda: backup.create(loc, backup._read_json(self.path), out_dir=out,
                                                                         reason="before settings upgrade"))
        self.assertEqual(report["status"], "upgraded")
        archive_path = backup.list_backups(out)[0]
        self.assertEqual(backup.read_manifest(archive_path)["reason"], "before settings upgrade")
        self.assertEqual(self.get()["config_version"], cm.LATEST)
        backup.restore(archive_path, loc, running=lambda: False)
        self.assertEqual(self.get(), OLD)


class TestServerStart(unittest.TestCase):
    def setUp(self):
        from stonesage import server
        self.server = server
        self.tmp = tempfile.mkdtemp(prefix="upgrade-start-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "config.json")
        patcher = mock.patch.object(server, "CONFIG_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"STONESAGE_DATA_DIR": os.path.join(self.tmp, "data")})
        env.start()
        self.addCleanup(env.stop)

    def test_a_newer_config_stops_the_server_with_a_clear_message(self):
        with open(self.path, "w") as f:
            json.dump({"config_version": cm.LATEST + 1}, f)
        with mock.patch("sys.stderr") as err, self.assertRaises(SystemExit) as ex:
            self.server._upgrade_settings_at_start()
        self.assertEqual(ex.exception.code, 1)
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("newer version", written)

    def test_an_old_config_is_backed_up_then_upgraded_at_start(self):
        with open(self.path, "w") as f:
            json.dump(OLD, f)
        self.server._upgrade_settings_at_start()
        with open(self.path) as f:
            self.assertEqual(json.load(f)["config_version"], cm.LATEST)
        backups = backup.list_backups(os.path.join(self.tmp, "data", "backups"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backup.read_manifest(backups[0])["reason"], "before settings upgrade")

    def test_it_runs_only_when_the_server_is_started_not_when_the_module_is_imported(self):
        src = open(importlib.util.find_spec("stonesage.server").origin, encoding="utf-8").read()      # however it is installed
        self.assertRegex(src, r'if __name__ == "__main__":\n    _upgrade_settings_at_start\(\)\nconfig = load_config\(\)')


class TestFirstRunAndExample(unittest.TestCase):
    def test_a_new_config_is_born_current_and_an_existing_one_keeps_its_number(self):
        from stonesage import first_run
        self.assertEqual(first_run.build_config({})["config_version"], cm.LATEST)
        old = first_run.build_config({}, existing={"operator": {"name": "Sam"}})
        self.assertNotIn("config_version", old, "an old file must still go through the upgrade at the next start")

    def test_the_docker_entrypoint_config_is_upgraded_not_trusted(self):
        if not DOCKER_DIR or not os.path.exists(os.path.join(DOCKER_DIR, "entrypoint.py")):
            self.skipTest("the Docker entrypoint is not in this tree")
        sys.path.insert(0, DOCKER_DIR)
        try:
            import entrypoint
        finally:
            sys.path.pop(0)
        seed = entrypoint.seed_config({"STONESAGE_APPS": "hearth"})
        new, _ = cm.migrate(seed)           # an unversioned first-start config migrates cleanly and is then current
        self.assertEqual(new["config_version"], cm.LATEST)

    def test_the_repo_example_config_is_valid_for_the_current_version(self):
        example_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(stonesage.__file__))), "config.example.json")
        if not os.path.exists(example_path):
            self.skipTest("no example config next to the package in this layout")
        with open(example_path, encoding="utf-8") as f:
            example = json.load(f)
        cm.check_not_newer(example)
        self.assertEqual(example["features"]["shelved"], False)
        self.assertEqual({k: v for k, v in example["backup"].items() if not k.startswith("_")},
                         {"enabled": True, "keep": 14, "hour": 3, "destinations": []})
        samples = [dict(e, path=os.path.abspath("nas")) if e.get("type") == "folder" else e for e in example["backup"]["_destinations_example"]]
        shipped = backup_dest.load_destinations({"backup": {"destinations": samples}})      # (a /mnt path is absolute only on POSIX)
        self.assertEqual([(d.type, d.error) for d in shipped], [("folder", ""), ("rclone", "")], "the shipped examples must be valid")


class TestVersions(unittest.TestCase):
    def test_each_layer_has_a_version_and_an_api_version(self):
        for pkg in (lodestone, stonesage) + ((hearth,) if hearth else ()):
            self.assertRegex(pkg.__version__, r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$", pkg.__name__)   # PEP 440: a stamped tree says 0.1.0rc4
        for v in (lodestone.API_VERSION, stonesage.API_VERSION):
            self.assertRegex(v, r"^\d+\.\d+$")

    def test_each_layer_pins_the_api_of_the_layer_below(self):
        self.assertEqual(stonesage.LODESTONE_API, lodestone.API_VERSION)
        if hearth:
            self.assertEqual(hearth.LODESTONE_API, lodestone.API_VERSION)
            self.assertEqual(hearth.STONESAGE_API, stonesage.API_VERSION)

    def test_the_servers_health_answer_reports_the_platform_version(self):
        from route_harness import call
        from stonesage import server
        self.assertEqual(server.APP_VERSION, stonesage.__version__)
        self.assertEqual(json.loads(call("GET", "/api/health", auth=False)[2])["version"], stonesage.__version__)

    @unittest.skipUnless(os.path.exists(BUILDER) and hearth, "ops tooling: only in the full repository")
    def test_the_builder_takes_its_release_numbers_from_the_packages(self):
        spec = importlib.util.spec_from_file_location("build_public_repos", BUILDER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for pkg in (lodestone, stonesage, hearth):
            self.assertEqual(mod.package_version(ROOT, pkg.__name__), pkg.__version__)


class TestCompat(unittest.TestCase):
    def test_compatibility_rule(self):
        for have, wanted, ok in (("0.1", "0.1", True), ("0.2", "0.1", False), ("0.1", "0.2", False), ("1.0", "1.0", True),
                                 ("1.3", "1.1", True), ("1.0", "1.1", False), ("2.0", "1.9", False), ("1.2", "2.0", False)):
            self.assertEqual(compat.compatible(have, wanted), ok, (have, wanted))

    def test_a_version_that_is_not_major_minor_is_an_error(self):
        for bad in ("1", "1.2.3", "a.b", "", None, "0.1 "[:0]):
            with self.assertRaises(ValueError):
                compat.parse_api(bad)

    def test_a_mismatch_says_what_to_do(self):
        with self.assertRaises(compat.LayerMismatch) as cm_:
            compat.require("StoneSage", "0.1.0", "Lodestone", "0.2", "0.1")
        msg = str(cm_.exception)
        for part in ("StoneSage 0.1.0", "Lodestone API 0.1", "offers API 0.2", "UPGRADING.md"):
            self.assertIn(part, msg)
        compat.require("StoneSage", "0.1.0", "Lodestone", "0.1", "0.1")        # a match is silent
        with self.assertRaises(compat.LayerMismatch):
            compat.require("X", "1", "Lodestone", "junk", "0.1")

    def _import_with(self, code):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {BACKEND!r})\n" + code],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_the_platform_refuses_to_start_on_the_wrong_foundation(self):
        r = self._import_with("import lodestone; lodestone.API_VERSION = '0.9'\nimport stonesage")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LayerMismatch", r.stderr)
        self.assertIn("Lodestone API 0.1", r.stderr)
        self.assertIn("offers API 0.9", r.stderr)
        ok = self._import_with("import stonesage; print(stonesage.__version__)")
        self.assertEqual(ok.returncode, 0, ok.stderr)

    @unittest.skipUnless(hearth, "the Home app is not installed")
    def test_the_app_refuses_to_load_on_the_wrong_platform(self):
        r = self._import_with("import stonesage; stonesage.API_VERSION = '0.9'\nimport hearth")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("StoneSage API 0.1", r.stderr)
        self.assertIn("offers API 0.9", r.stderr)


class TestAppApiCheck(unittest.TestCase):
    """apps.load_apps skips an app that was written for another platform API, with the reason in the log."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="app-api-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        sys.path.insert(0, self.tmp)
        self.addCleanup(lambda: sys.path.remove(self.tmp))

    def make_app(self, name, api):
        folder = os.path.join(self.tmp, name)
        os.makedirs(folder)
        with open(os.path.join(folder, "__init__.py"), "w") as f:
            f.write(f'__version__ = "3.1.4"\n' + (f'STONESAGE_API = "{api}"\n' if api else ""))
        with open(os.path.join(folder, "app.py"), "w") as f:
            f.write("LOADED = []\ndef install(ctx):\n    LOADED.append(1)\n")
        self.addCleanup(lambda: [sys.modules.pop(m, None) for m in (name, name + ".app")])

    def load(self, name):
        from stonesage.apps import AppContext, load_apps
        ctx = AppContext({}, self.tmp, lambda: self.tmp)
        return load_apps([name], ctx)

    def test_a_matching_app_loads(self):
        self.make_app("apptwin", stonesage.API_VERSION)
        self.assertEqual(self.load("apptwin"), ["apptwin"])

    def test_an_app_that_declares_nothing_loads(self):
        self.make_app("appplain", None)
        self.assertEqual(self.load("appplain"), ["appplain"])

    def test_an_app_for_another_platform_api_is_skipped_with_a_reason(self):
        self.make_app("appfuture", "7.0")
        with self.assertLogs("StoneSage.apps", level="ERROR") as logs:
            loaded = self.load("appfuture")
        self.assertEqual(loaded, [])
        self.assertIn("StoneSage API 7.0", logs.output[0])
        self.assertIn("app appfuture 3.1.4", logs.output[0])
        self.assertNotIn("appfuture.app", sys.modules, "its code never ran")


if __name__ == "__main__":
    unittest.main()
