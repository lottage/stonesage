"""
Unit Tests for ROG Ally X & Edge Fleet Model Manager (LM Studio v0.3+).
Tests model polling, best-fit calculation, agent binding, and CLI command execution.
"""

import unittest
from unittest.mock import patch, MagicMock
import json

from harness.edge_fleet.ally_model_manager import AllyModelManager
from harness.cli.commands import CommandRegistry
from harness.cli.agent_shell import AgentShell
from harness.data_fabric.pg_storage import relational_storage


class TestAllyModelManager(unittest.TestCase):
    def setUp(self):
        self.mgr = AllyModelManager(node_url="http://127.0.0.1:1234", node_id="node2_ally_x")

    def test_calculate_best_fit_7b_9b(self):
        m_info = {"params": "9B", "size_gb": 6.8}
        # Unconstrained 20.5GB headroom -> dynamically scales up to 32k context
        params = self.mgr.calculate_best_fit(m_info, available_ram_gb=20.5)
        self.assertGreaterEqual(params["context_length"], 16384)
        self.assertTrue(params["flash_attention"])
        self.assertTrue(params["offload_kv_cache_to_gpu"])
        self.assertEqual(params["eval_batch_size"], 2048)

    def test_calculate_best_fit_14b(self):
        m_info = {"params": "14B", "size_gb": 9.5}
        # Constrained headroom (12.0GB free, e.g. co-existing models) -> dynamically scales to 8192
        params = self.mgr.calculate_best_fit(m_info, available_ram_gb=12.0)
        self.assertIn(params["context_length"], (8192, 12288))
        self.assertTrue(params["flash_attention"])
        self.assertTrue(params["offload_kv_cache_to_gpu"])

    def test_calculate_best_fit_large_32b(self):
        m_info = {"params": "32B", "size_gb": 19.5}
        # 32B model on 20.5GB headroom leaves < 1GB for KV cache -> drops to 4096 floor and disables GPU KV
        params = self.mgr.calculate_best_fit(m_info, available_ram_gb=20.5)
        self.assertEqual(params["context_length"], 4096)
        self.assertTrue(params["flash_attention"])
        self.assertFalse(params["offload_kv_cache_to_gpu"])
        self.assertEqual(params["eval_batch_size"], 512)

    @patch("urllib.request.urlopen")
    def test_list_local_models_native_api(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        native_payload = {
            "models": [
                {
                    "key": "qwen3.5-9b-claude",
                    "display_name": "Qwen 3.5 9B Claude",
                    "architecture": "qwen2",
                    "params_string": "9B",
                    "size_bytes": 7 * (1024 ** 3),
                    "quantization": {"name": "Q4_K_M"},
                    "loaded_instances": [{"id": "inst_123", "config": {"context_length": 16896}}],
                    "max_context_length": 32768,
                    "format": "gguf"
                }
            ]
        }
        mock_resp.read.return_value = json.dumps(native_payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        models = self.mgr.list_local_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["key"], "qwen3.5-9b-claude")
        self.assertTrue(models[0]["is_loaded"])
        self.assertEqual(models[0]["size_gb"], 7.0)

    @patch("urllib.request.urlopen")
    def test_get_active_instance(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        native_payload = {
            "models": [
                {
                    "key": "qwen3.5-9b-claude",
                    "display_name": "Qwen 3.5 9B Claude",
                    "architecture": "qwen2",
                    "params_string": "9B",
                    "size_bytes": 7 * (1024 ** 3),
                    "quantization": {"name": "Q4_K_M"},
                    "loaded_instances": [{"id": "inst_123", "config": {"context_length": 16896}}],
                }
            ]
        }
        mock_resp.read.return_value = json.dumps(native_payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        active = self.mgr.get_active_instance()
        self.assertIsNotNone(active)
        self.assertEqual(active["key"], "qwen3.5-9b-claude")
        self.assertEqual(active["instance_id"], "inst_123")

    def test_bind_agent(self):
        # Register a test agent first
        relational_storage.upsert_hive_agent(
            agent_id="test-sentinel",
            name="Test Sentinel",
            role="Auditor",
            assigned_node="node1_primary"
        )
        ok = self.mgr.bind_agent(agent_id="test-sentinel", model_key="qwen3.5-9b-claude")
        self.assertTrue(ok)
        agent = relational_storage.get_hive_agent("test-sentinel")
        self.assertIsNotNone(agent)
        self.assertIn(agent["assigned_node"], ("node2_edge", "node2_ally_extreme", "node2_ally_x"))

    def test_agent_shell_bind_and_detach(self):
        relational_storage.upsert_hive_agent(
            agent_id="test-ally-bot",
            name="Ally Bot",
            role="Edge Agent",
            assigned_node="node1_primary"
        )
        # Test /agent bind
        AgentShell.handle_agent_command(["bind", "test-ally-bot", "ally"])
        ag = relational_storage.get_hive_agent("test-ally-bot")
        self.assertIn(ag["assigned_node"], ("node2_edge", "node2_ally_extreme", "node2_ally_x"))

        # Test attach then detach
        AgentShell.active_agent = {"agent_id": "test-ally-bot", "name": "Ally Bot"}
        self.assertIsNotNone(AgentShell.active_agent)
        AgentShell.handle_agent_command(["detach"])
        self.assertIsNone(AgentShell.active_agent)

    @patch("harness.edge_fleet.ally_model_manager.EdgeFleetModelManager.list_local_models")
    def test_command_registry_ally_models(self, mock_list):
        mock_list.return_value = [
            {
                "key": "test-model-9b",
                "name": "Test Model 9B",
                "architecture": "qwen2",
                "params": "9B",
                "size_gb": 6.5,
                "quant": "Q4_K_M",
                "is_loaded": True
            }
        ]
        # Should execute without throwing
        CommandRegistry.handle_ally(["models"])
        CommandRegistry.handle_ally(["status"])
        CommandRegistry.handle_node(["list"])
        CommandRegistry.handle_node(["models"])
        CommandRegistry.handle_node(["status"])

    def test_dynamic_memory_derivation_from_config(self):
        # memory comes from the node's configured hardware (config.json), not a constant in code
        from harness.config import NodeEndpoint, fleet_config
        saved = fleet_config.nodes.get("node2_edge")
        fleet_config.nodes["node2_edge"] = NodeEndpoint(node_id="node2_edge", name="edge", base_url="http://127.0.0.1:1234/v1",
                                                        total_memory_mb=24576)
        try:
            audit = self.mgr.audit_node_resources()
        finally:
            if saved is not None:
                fleet_config.nodes["node2_edge"] = saved
            else:
                fleet_config.nodes.pop("node2_edge", None)
        self.assertEqual(audit["total_hardware_ram_gb"], 24.0)
        self.assertLessEqual(audit["net_available_replace_gb"], 21.0)

    @patch.object(AllyModelManager, "load_model")
    @patch.object(AllyModelManager, "unload_active_instances")
    @patch.object(AllyModelManager, "get_active_instance")
    def test_change_parallel_slots_full_verification(self, mock_get_active, mock_unload, mock_load):
        # Pre-unload active instance with 2 slots
        initial_inst = {
            "key": "qwen2.5-coder-7b",
            "name": "Qwen 2.5 Coder 7B",
            "instance_id": "inst_100",
            "size_gb": 4.5,
            "config": {
                "context_length": 16384,
                "flash_attention": True,
                "eval_batch_size": 1024,
                "physical_batch_size": 512,
                "offload_kv_cache_to_gpu": True,
                "parallel": 2
            }
        }
        # Post-reload active instance with 4 slots and identical preserved parameters
        reloaded_inst = {
            "key": "qwen2.5-coder-7b",
            "name": "Qwen 2.5 Coder 7B",
            "instance_id": "inst_101",
            "size_gb": 4.5,
            "config": {
                "context_length": 16384,
                "flash_attention": True,
                "eval_batch_size": 1024,
                "physical_batch_size": 512,
                "offload_kv_cache_to_gpu": True,
                "parallel": 4
            }
        }
        mock_get_active.side_effect = [initial_inst, reloaded_inst]
        mock_unload.return_value = True
        mock_load.return_value = {"ok": True, "model": "qwen2.5-coder-7b"}

        res = self.mgr.change_parallel_slots(num_slots=4)
        self.assertTrue(res["ok"])
        self.assertTrue(res["all_matched"])
        self.assertEqual(res["new_slots"], 4)
        self.assertEqual(res["old_slots"], 2)
        mock_unload.assert_called_once()
        mock_load.assert_called_once()

        # Check verification report
        for item in res["verification_report"]:
            self.assertTrue(item["verified"], f"Parameter {item['parameter']} failed verification!")

    @patch.object(AllyModelManager, "change_parallel_slots")
    def test_command_registry_slots(self, mock_change):
        mock_change.return_value = {
            "ok": True,
            "all_matched": True,
            "model_key": "qwen2.5-coder-7b",
            "old_slots": 2,
            "new_slots": 4,
            "verification_report": [
                {"parameter": "model_key", "pre_unload": "qwen2.5-coder-7b", "target": "qwen2.5-coder-7b", "post_reload": "qwen2.5-coder-7b", "verified": True},
                {"parameter": "parallel (slots)", "pre_unload": 2, "target": 4, "post_reload": 4, "verified": True},
                {"parameter": "context_length", "pre_unload": 16384, "target": 16384, "post_reload": 16384, "verified": True},
            ]
        }
        CommandRegistry.handle_slots(["4"])
        mock_change.assert_called_with(4)


if __name__ == "__main__":
    unittest.main()
