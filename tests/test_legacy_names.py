"""Old names keep resolving (harness/legacy_names.py): the coding agent's id before it became "atlas", the registry tables and
download links from the product's first name. The only tests allowed to spell the old name (tests/test_personal_data.py)."""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from harness import legacy_names as ln  # noqa: E402
from harness.core.founders import FOUNDERS, get_founder, is_founder  # noqa: E402
from harness.core.openclaw_engine import OpenClawEngine  # noqa: E402
from harness.data_fabric.pg_storage import RelationalStorage  # noqa: E402


class TestAgentIds(unittest.TestCase):
    def test_old_ids_map_to_the_current_ones(self):
        self.assertEqual(ln.canonical_agent_id("aevum"), "atlas")
        self.assertEqual(ln.canonical_agent_id("  AEVUM "), "atlas")
        self.assertEqual(ln.canonical_agent_id("aevum-steve-child-unit"), "atlas-steve-child-unit")
        self.assertEqual(ln.canonical_agent_id("atlas"), "atlas")
        self.assertEqual(ln.canonical_agent_id("aevumx"), "aevumx")             # only the id or a dash-prefix, not any prefix
        self.assertEqual(ln.canonical_agent_id("steve"), "steve")
        self.assertEqual(ln.canonical_agent_name("Aevum-Steve-Child-Unit"), "Atlas-Steve-Child-Unit")
        self.assertEqual(ln.canonical_agent_name("Aevum"), "Atlas")
        self.assertEqual(ln.canonical_agent_name("Steve"), "Steve")
        self.assertEqual(ln.legacy_ids_for("atlas"), ["aevum"])
        self.assertEqual(ln.legacy_ids_for("atlas-steve-child-unit"), ["aevum-steve-child-unit"])
        self.assertEqual(ln.legacy_ids_for("steve"), [])

    def test_the_founder_answers_to_both_ids(self):
        self.assertIs(get_founder("aevum"), get_founder("atlas"))
        self.assertEqual(get_founder("aevum").id, "aule")
        self.assertTrue(is_founder("aevum") and is_founder("atlas"))
        self.assertIs(FOUNDERS["aevum"], FOUNDERS["atlas"])


class TestStoredProfiles(unittest.TestCase):
    def test_a_profile_stored_under_the_old_id_resolves_under_the_new_one(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "aevum"))
            with open(os.path.join(d, "aevum.json"), "w", encoding="utf-8") as f:
                json.dump({"agent_id": "aevum", "name": "Aevum", "role": "Manager"}, f)
            with open(os.path.join(d, "aevum", "SOUL.md"), "w", encoding="utf-8") as f:
                f.write("# SOUL")
            engine = OpenClawEngine(profiles_dir=d)
            for asked in ("atlas", "aevum"):
                prof = engine.get_profile(asked)
                self.assertEqual((prof["agent_id"], prof["name"], prof["soul_md"]), ("atlas", "Atlas", "# SOUL"), asked)
            listed = [p for p in engine.list_profiles() if p["role"] == "Manager"]
            self.assertEqual([(p["agent_id"], p["name"]) for p in listed], [("atlas", "Atlas")])

    def test_the_new_profile_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory() as d:
            for aid, name in (("aevum", "Aevum"), ("atlas", "Atlas")):
                with open(os.path.join(d, aid + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"agent_id": aid, "name": name, "role": aid}, f)
            self.assertEqual(OpenClawEngine(profiles_dir=d).get_profile("aevum")["role"], "atlas")


class TestRegistryMigration(unittest.TestCase):
    def test_an_old_database_is_renamed_and_re_keyed(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "harness.db")
            now = time.time()
            old = sqlite3.connect(db)
            old.execute("CREATE TABLE aevum_agent_registry (agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, "
                        "assigned_node TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle', current_task TEXT, "
                        "last_heartbeat_hive REAL NOT NULL, autonomous_focus TEXT, metadata_json TEXT, created_at REAL NOT NULL, "
                        "updated_at REAL NOT NULL)")
            for aid, name in (("aevum", "Aevum"), ("aevum-steve-child-unit", "Aevum-Steve-Child-Unit"), ("steve", "Steve")):
                old.execute("INSERT INTO aevum_agent_registry VALUES (?, ?, 'r', 'n', 'idle', 'a task', ?, NULL, '{}', ?, ?)",
                            (aid, name, now, now, now))
            old.execute("CREATE TABLE aevum_project_registry (project_id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, "
                        "node_id TEXT DEFAULT 'local', bound_agent_id TEXT, status TEXT NOT NULL DEFAULT 'active', "
                        "metadata_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            old.execute("INSERT INTO aevum_project_registry VALUES ('p', 'P', '/p', 'local', 'aevum', 'active', '{}', ?, ?)", (now, now))
            old.execute("CREATE TABLE agent_sessions (session_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, assigned_node TEXT NOT NULL, "
                        "status TEXT NOT NULL, context_floor INTEGER DEFAULT 4096, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                        "metadata_json TEXT)")
            old.execute("INSERT INTO agent_sessions VALUES ('s', 'aevum', 'n', 'ok', 4096, ?, ?, '{}')", (now, now))
            old.commit()
            old.close()

            store = RelationalStorage(db_path=db)
            try:
                ids = sorted(a["agent_id"] for a in store.list_hive_agents())
                self.assertEqual(ids, ["atlas", "atlas-steve-child-unit", "steve"])
                self.assertEqual(store.get_hive_agent("aevum")["name"], "Atlas")              # the old id still finds the row
                self.assertEqual(store.get_hive_agent("atlas")["current_task"], "a task")
                self.assertEqual(store.get_hive_agent("atlas-steve-child-unit")["name"], "Atlas-Steve-Child-Unit")
                self.assertEqual(store.get_hive_agent("steve")["name"], "Steve")
                self.assertEqual(store.get_project("p")["bound_agent_id"], "atlas")
                tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                self.assertNotIn("aevum_agent_registry", tables)
                self.assertIn("stonesage_agent_registry", tables)
                self.assertEqual(store.conn.execute("SELECT agent_id FROM agent_sessions").fetchone()[0], "atlas")
            finally:
                store.close()
            # a second start changes nothing
            again = RelationalStorage(db_path=db)
            try:
                self.assertEqual(len(again.list_hive_agents()), 3)
            finally:
                again.close()


class TestDownloadLinks(unittest.TestCase):
    def test_the_old_links_and_file_name_still_serve_the_package(self):
        from stonesage import server
        for path in ("/download/aevum", "/aevum", "/aevum.apk", "/download", "/apk", "/stonesage", "/anything.apk"):
            self.assertTrue(server.is_apk_request(path, path.rstrip("/")), path)
        self.assertFalse(server.is_apk_request("/api/health", "/api/health"))
        with tempfile.TemporaryDirectory() as d:
            real = server.FRONTEND_DIR
            server.FRONTEND_DIR = d
            try:
                self.assertEqual(os.path.basename(server.apk_file_path()), "stonesage.apk")      # nothing there yet: the new name
                open(os.path.join(d, ln.LEGACY_APK_NAMES[0]), "wb").close()
                self.assertEqual(os.path.basename(server.apk_file_path()), ln.LEGACY_APK_NAMES[0])
                open(os.path.join(d, "stonesage.apk"), "wb").close()
                self.assertEqual(os.path.basename(server.apk_file_path()), "stonesage.apk")      # the new file wins
            finally:
                server.FRONTEND_DIR = real


if __name__ == "__main__":
    unittest.main()
