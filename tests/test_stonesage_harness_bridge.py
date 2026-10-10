"""
Test StoneSage Web GUI Multi-Workstation Harness Bridge & Capacity Endpoints.
Verifies:
  1. GET /api/harness/instances returns registered workstations & ping telemetry
  2. POST /api/harness/instances/select switches active harness instance
  3. POST /api/harness/instances/add adds custom/Tailscale node
  4. POST /api/harness/capacity calculates VRAM/RAM offload with >= 4096 floor
  5. Proxy forwarding and fallback when external instance is probed
"""

import sys
import os
import json
import unittest
from pathlib import Path

# Safe encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "StoneSage" / "backend"))

from stonesage import server as ss_server


FIXTURE_CONFIG = {
    "harness_instances": [
        {"id": "workstation_primary", "name": "Workstation", "url": "http://127.0.0.1:8088"},
        {"id": "vm102_compute", "name": "Compute host", "url": "http://127.0.0.1:8001"},
        {"id": "rog_ally_x", "name": "ROG Ally (LM Studio)", "url": "http://192.0.2.213:1234", "engine": True},
    ]
}


class TestStoneSageHarnessBridge(unittest.TestCase):
    # These tests save the config (select / add an instance). They get a throwaway copy, so they never rewrite the
    # operator's real config.json and they run anywhere, including CI where no config.json exists.
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.mkdtemp(prefix="harness-bridge-")
        cls._real_config_file = ss_server.CONFIG_FILE
        ss_server.CONFIG_FILE = os.path.join(cls._tmp, "config.json")
        with open(ss_server.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(FIXTURE_CONFIG, f)

    @classmethod
    def tearDownClass(cls):
        import shutil
        ss_server.CONFIG_FILE = cls._real_config_file
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_01_get_instances(self):
        """Verify default instances and structure."""
        instances = ss_server.get_harness_instances()
        self.assertIsInstance(instances, list)
        self.assertGreaterEqual(len(instances), 4)

        ids = [i["id"] for i in instances]
        self.assertIn("base_server", ids)
        self.assertIn("workstation_primary", ids)
        self.assertIn("vm102_compute", ids)
        self.assertIn("rog_ally_x", ids)

    def test_02_switch_active_instance(self):
        """Verify selecting a harness instance persists in config."""
        cfg = ss_server.load_config()
        original_active = cfg.get("active_harness_instance", "base_server")

        # Select rog_ally_x
        cfg["active_harness_instance"] = "rog_ally_x"
        ss_server.save_config(cfg)

        active = ss_server.get_active_harness_instance()
        self.assertEqual(active["id"], "rog_ally_x")
        self.assertEqual(active["url"], "http://192.0.2.213:1234")

        # Restore original
        cfg["active_harness_instance"] = original_active
        ss_server.save_config(cfg)

    def test_03_add_and_delete_tailscale_instance(self):
        """Verify registering a custom Tailscale node."""
        cfg = ss_server.load_config()
        instances = ss_server.get_harness_instances()

        test_id = "test_tailscale_node"
        test_url = "http://100.85.12.34:8088"
        instances.append({
            "id": test_id,
            "name": "Remote Laptop (Tailscale)",
            "url": test_url,
            "description": "Custom Tailscale test node"
        })
        cfg["harness_instances"] = instances
        ss_server.save_config(cfg)

        # Assert discovered
        reloaded = ss_server.get_harness_instances()
        found = next((i for i in reloaded if i["id"] == test_id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["url"], test_url)

        # Clean up
        cleaned = [i for i in reloaded if i["id"] != test_id]
        cfg["harness_instances"] = cleaned
        ss_server.save_config(cfg)

    def test_04_capacity_calculation_with_agent_floor(self):
        """Verify continuous capacity calculation and >= 4096 agent floor."""
        from harness.core.offload_calc import offload_engine

        # Request 1024 context -> must be clamped to 4096
        est_clamped = offload_engine.estimate(
            arch_type="9b",
            quant="q4_k_m",
            context_length=1024,
            parallel_slots=2,
            target_vram_gb=12.0
        )
        self.assertEqual(est_clamped.context_length, 4096)
        self.assertTrue(est_clamped.fits_in_vram)

        # 32k context on 35B MoE on 24GB
        est_moe = offload_engine.estimate(
            arch_type="35b_moe",
            quant="q8_0",
            context_length=32768,
            parallel_slots=1,
            target_vram_gb=24.0
        )
        self.assertGreater(est_moe.total_required_gb, 10.0)

    def test_05_ping_probe(self):
        """Verify ping probe function handles online/offline gracefully."""
        # Probing invalid port/host must return is_online=False without throwing
        res = ss_server.ping_harness_instance("http://192.0.2.1:9999", timeout=0.2)
        self.assertFalse(res.get("is_online"))
        self.assertIsNone(res.get("ping_ms"))


class TestProxmoxNodeKeys(unittest.TestCase):
    """config.json's proxmox block: pve_node first, then every other *_node entry, whatever the second node is called."""

    def test_the_second_node_may_have_any_name(self):
        block = {"host": "10.0.0.2", "token_value": "x", "pve_node": {}, "zeta_node": {}, "app_node": {}}
        self.assertEqual(ss_server.proxmox_node_keys({"proxmox": block}), ["pve_node", "app_node", "zeta_node"])
        self.assertEqual(ss_server.proxmox_node_keys({"proxmox": {"old_name_node": {}, "pve_node": {}}}), ["pve_node", "old_name_node"])

    def test_without_a_block_the_defaults_are_the_example_config_keys(self):
        self.assertEqual(ss_server.proxmox_node_keys({}), ["pve_node", "app_node"])
        self.assertEqual(ss_server.proxmox_node_keys({"proxmox": {"host": "10.0.0.2"}}), ["pve_node", "app_node"])


if __name__ == "__main__":
    unittest.main()
