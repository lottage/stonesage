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

    def test_check_all_summary_and_cache(self):
        cfg = {"homeassistant": {"token": "x"}}
        calls = []

        def fake_http(url, headers=None, timeout=0):
            calls.append(url)
            if ":8004" in url:
                return None, "down", 1
            return (200, PROPS, 1) if url.endswith("/props") else (200, {}, 1)

        with mock.patch.object(health, "_http", side_effect=fake_http), \
                mock.patch.object(health, "_tcp", return_value=(True, 1)):
            r = health.check_all(cfg, use_cache=False)
            n = len(calls)
            again = health.check_all(cfg, use_cache=True)
        self.assertFalse(r["ok"])
        total = len(health.build_probes(cfg))                      # not a literal: every new service used to break this test
        self.assertEqual(r["summary"], f"{total - 1}/{total} up; down: Vision")
        self.assertIs(again, r)
        self.assertEqual(len(calls), n, "cached call must not probe again")


if __name__ == "__main__":
    unittest.main()
