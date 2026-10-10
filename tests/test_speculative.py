"""
Unit Tests for Dual-GPU Speculative Decoding & Realtime Dynamic Resource Scaling.
Tests SpeculativeEngine lifecycle, status reporting, benchmark calculations,
and multi-model co-existence resource audit.
"""

import unittest
from unittest.mock import patch, MagicMock
import json

from harness.core.speculative import SpeculativeEngine, SpeculativeMetrics
from harness.edge_fleet.ally_model_manager import AllyModelManager
from harness.cli.commands import CommandRegistry


class TestSpeculativeEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SpeculativeEngine(
            target_url="http://127.0.0.1:8001/v1",
            draft_url="http://127.0.0.1:8002/v1",
            gamma=5
        )

    def test_enable_disable(self):
        # Invariant: Speculative decoding defaults to ON for interactive user turns
        self.assertTrue(self.engine.is_enabled)
        self.assertTrue(self.engine.should_use_speculative(is_interactive=True))
        # Background automation defaults to single-model execution (False)
        self.assertFalse(self.engine.should_use_speculative(is_interactive=False, task_type="automation"))
        # Unless notable improvement / speedup is explicitly flagged
        self.assertTrue(self.engine.should_use_speculative(is_interactive=False, task_type="automation", requires_speedup=True))

        self.engine.enable(gamma=6)
        self.assertTrue(self.engine.is_enabled)
        self.assertEqual(self.engine.gamma, 6)

        self.engine.disable()
        self.assertFalse(self.engine.is_enabled)
        self.assertFalse(self.engine.should_use_speculative(is_interactive=True))

    @patch.object(SpeculativeEngine, "_probe_endpoint")
    def test_get_status_alignment(self, mock_probe):
        # Mock target 14B Qwen and draft 3B Qwen
        mock_probe.side_effect = [
            (True, "qwen2.5-coder-14b-instruct", 1.2),
            (True, "qwen2.5-coder-3b-instruct", 0.9)
        ]
        st = self.engine.get_status()
        self.assertTrue(st["target_online"])
        self.assertTrue(st["draft_online"])
        self.assertIn("100% Match", st["alignment"])
        self.assertIn("Qwen2", st["alignment"])

    @patch("harness.core.llama_client.LlamaClient.chat_stream")
    def test_run_benchmark(self, mock_stream):
        async def mock_gen(*args, **kwargs):
            from harness.core.llama_client import StreamChunk
            for _ in range(10):
                yield StreamChunk(chunk_type="output", content=" token")

        mock_stream.side_effect = mock_gen
        import asyncio
        res = asyncio.run(self.engine.run_benchmark(prompt="Hello test"))
        self.assertIn("target_tps", res)
        self.assertIn("draft_tps", res)
        self.assertIn("speculative_tps", res)
        self.assertGreater(res["speedup_ratio"], 1.0)

    @patch.object(SpeculativeEngine, "get_status")
    def test_command_registry_speculative(self, mock_status):
        mock_status.return_value = {
            "is_enabled": True,
            "target_endpoint": "http://127.0.0.1:8001/v1",
            "target_online": True,
            "target_model": "qwen2.5-coder-14b",
            "target_latency_ms": 1.5,
            "target_device": "RX 6750 XT",
            "draft_endpoint": "http://127.0.0.1:8002/v1",
            "draft_online": True,
            "draft_model": "qwen2.5-coder-3b",
            "draft_latency_ms": 0.8,
            "draft_device": "RX 6600 XT",
            "alignment": "100% Match",
            "gamma": 5,
            "metrics": SpeculativeMetrics()
        }
        CommandRegistry.handle_speculative(["status"])
        CommandRegistry.handle_speculative(["on", "6"])
        CommandRegistry.handle_speculative(["off"])


class TestDynamicResourceScaling(unittest.TestCase):
    def setUp(self):
        self.mgr = AllyModelManager(node_url="http://127.0.0.1:1234")

    @patch.object(AllyModelManager, "list_local_models")
    def test_audit_node_resources(self, mock_models):
        mock_models.return_value = [
            {
                "key": "qwen3.5-9b",
                "size_gb": 6.8,
                "is_loaded": True,
                "instances": [{"id": "inst_1", "config": {"context_length": 16384}}]
            }
        ]
        audit = self.mgr.audit_node_resources(total_ram_gb=12.0)
        self.assertEqual(audit["total_hardware_ram_gb"], 12.0)
        self.assertEqual(audit["active_count"], 1)
        self.assertGreater(audit["total_loaded_gb"], 6.8)
        self.assertLess(audit["net_available_coexist_gb"], audit["net_available_replace_gb"])

    def test_calculate_best_fit_dynamic_coexist_scaling(self):
        m_info = {"params": "9B", "size_gb": 6.8}
        # Case A: Full 20.5GB headroom available (replacing mode)
        params_replace = self.mgr.calculate_best_fit(m_info, available_ram_gb=20.5, keep_existing_models=False)
        self.assertGreaterEqual(params_replace["context_length"], 16384)
        self.assertTrue(params_replace["offload_kv_cache_to_gpu"])

        # Case B: Tightly constrained headroom (e.g. 2 models co-existing on 1 node, only 8.5GB free)
        params_coexist = self.mgr.calculate_best_fit(m_info, available_ram_gb=8.5, keep_existing_models=True)
        # With 8.5GB free and 6.8GB weights, only ~0.95GB left for KV cache!
        self.assertLessEqual(params_coexist["context_length"], 8192)
        self.assertEqual(params_coexist["context_length"], 4096)  # Drops safely to agent floor


if __name__ == "__main__":
    unittest.main()
