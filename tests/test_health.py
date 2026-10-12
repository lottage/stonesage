"""Unit tests for StoneSage/backend/health.py (no network: probes are faked)."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from stonesage import health  # noqa: E402

PROPS = {"model_path": "/opt/models/qwen3-14b-q4_k_m.gguf", "total_slots": 2,
         "default_generation_settings": {"n_ctx": 6144}}


class TestHealth(unittest.TestCase):
    def test_base_strips_v1(self):
        self.assertEqual(health._base("http://h:8001/v1/"), "http://h:8001")
        self.assertEqual(health._base("http://h:8765"), "http://h:8765")

    def test_llama_ok_reports_props(self):
        def fake(url, headers=None, timeout=0):
            return (200, PROPS, 12) if url.endswith("/props") else (200, {"status": "ok"}, 12)
        with mock.patch.object(health, "_http", side_effect=fake):
            r = health.probe_llama("Coordinator", "http://h:8001/v1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["target"], "http://h:8001")
        self.assertEqual(r["detail"], {"model": "qwen3-14b-q4_k_m.gguf", "ctx_per_slot": 6144, "slots": 2})

    def test_llama_loading_and_down(self):
        with mock.patch.object(health, "_http", return_value=(503, {"error": "loading"}, 5)):
            self.assertEqual(health.probe_llama("V", "http://h:8004/v1")["status"], "loading")
        with mock.patch.object(health, "_http", return_value=(None, "URLError: refused", 3)):
            r = health.probe_llama("V", "http://h:8004/v1")
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "down")

    def test_http_unexpected_status_is_not_ok(self):
        with mock.patch.object(health, "_http", return_value=(401, "unauthorized", 4)):
            r = health.probe_http("CouchDB", "services", "http://c:5984/_up")
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "http 401")

    # A homelab: every service named in config.json, so every probe runs.
    FULL = {"homeassistant": {"token": "x", "url": "http://10.0.0.9:8123"},
            "cluster": {"coordinator_url": "http://10.0.0.5:8001/v1", "worker_url": "http://10.0.0.5:8002/v1",
                        "embedder_url": "http://10.0.0.5:8003/v1", "vision_url": "http://10.0.0.5:8004/v1",
                        "mcp_url": "http://10.0.0.5:8765", "qdrant_url": "http://10.0.0.6:6333"},
            "couchdb": {"url": "http://10.0.0.7:5984", "username": "u", "password": "p"},
            "voice": {"stt_url": "http://10.0.0.5:8210"}}

    def test_check_all_summary_and_cache(self):
        cfg = self.FULL
        calls = []

        def fake_http(url, headers=None, timeout=0):
            calls.append(url)
            if ":8004" in url:
                return None, "down", 1
            return (200, PROPS, 1) if url.endswith("/props") else (200, {}, 1)

        with mock.patch.object(health, "_http", side_effect=fake_http),                 mock.patch.object(health, "_tcp", return_value=(True, 1)):
            r = health.check_all(cfg, use_cache=False)
            n = len(calls)
            again = health.check_all(cfg, use_cache=True)
        self.assertFalse(r["ok"])
        total = len(health.build_probes(cfg))                      # not a literal: every new service used to break this test
        self.assertGreaterEqual(total, 12)
        self.assertEqual(r["summary"], f"{total - 1}/{total} up; down: Vision")
        self.assertEqual(r["not_set_up"], [])
        self.assertIs(again, r)
        self.assertEqual(len(calls), n, "cached call must not probe again")

    def test_a_fresh_install_probes_only_what_it_has_and_lists_the_rest_as_not_set_up(self):
        """M6 dry run #9: a Docker install showed 2/12 UP in red because ten services nobody set up were probed."""
        cfg = {"cluster": {"coordinator_url": "http://127.0.0.1:8001/v1", "embedder_url": "http://embed:8003/v1"}}
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.endswith(("_URL", "_HOST", "_TOKEN", "_PASSWORD"))},
                             clear=True),                 mock.patch.object(health, "_http", return_value=(200, PROPS, 1)):
            r = health.check_all(cfg, use_cache=False)
        self.assertEqual([s["name"] for s in r["services"]], ["Coordinator", "Embedder"])
        self.assertTrue(r["ok"], "services nobody set up must not turn the badge red")
        idle = {i["name"] for i in r["not_set_up"]}
        self.assertTrue({"Worker", "Vision", "Cluster MCP bridge", "Qdrant", "CouchDB", "Voice server", "Wake word",
                         "Home Assistant"} <= idle, idle)
        self.assertNotIn("Coordinator", idle)
        self.assertTrue(r["summary"].startswith("2/2 up; not set up: Worker"))

    def test_the_coordinator_is_core_and_probed_even_when_nothing_is_set(self):
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.endswith("_URL")}, clear=True),                 mock.patch.object(health, "_http", return_value=(None, "refused", 1)):
            r = health.check_all({}, use_cache=False)
        self.assertEqual([s["name"] for s in r["services"]], ["Coordinator"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["summary"].split(";")[0], "0/1 up")

    def test_home_assistant_is_probed_once_it_has_a_token(self):
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "HASS_TOKEN"}, clear=True):
            names = [p[1] for p in health.build_probes({"apps": ["hearth"]})]
            self.assertNotIn("Home Assistant", names)
            self.assertIn("Home Assistant", [i["name"] for i in health.not_set_up({"apps": ["hearth"]})])
            self.assertIn("Home Assistant", [p[1] for p in health.build_probes({"homeassistant": {"token": "t"}})])
            self.assertNotIn("Home Assistant", [i["name"] for i in health.not_set_up({"apps": []})])   # no Home app: not listed at all


if __name__ == "__main__":
    unittest.main()
