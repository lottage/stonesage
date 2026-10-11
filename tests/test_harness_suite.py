"""
StoneSage Unified LLM Harness: Verification & Invariant Test Suite.
Deterministically tests all 6 core system guarantees:
  1. A-MEM core-memory immutability vs temporal decay
  2. 70/30 Ground Truth dataset rebalancing & rumination quarantine
  3. Context floor >= 4096 & continuous hardware capacity math
  4. agent-nudge watchdog detection & stream intervention
  5. Obsidian Vault strict 3-file boundary
  6. Edge Fleet Roaming Handover & OTA lifecycle
"""

import os
import sys
import time
import json
import unittest
from pathlib import Path

# Safe encoding for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add parent directory to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "agent-nudge"))

from harness.data_fabric.valkey_amem import ValkeyAMEM
from harness.training.curated_rumination_filter import CuratedRuminationFilter
from harness.training.ground_truth_ingestor import GroundTruthIngestor
from harness.core.offload_calc import HardwareCapacityEngine, MIN_AGENT_CONTEXT_FLOOR
from harness.data_fabric.obsidian_gateway import ObsidianGateway
from harness.edge_fleet.roaming_handover import RoamingHandoverManager
from harness.edge_fleet.ota_reload import OTAModelDistributor
try:  # agent-nudge is a separate repo checked out beside this one; CI and fresh clones do not have it
    from agent_nudge.core import NudgeEngine
    from agent_nudge.watchdog import WatchdogDetector
    HAVE_AGENT_NUDGE = True
except ImportError:
    HAVE_AGENT_NUDGE = False


class TestHarnessInvariants(unittest.TestCase):

    def setUp(self):
        self.test_dir = BASE_DIR / "data" / "test_scratch"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def test_01_amem_core_memory_immutability(self):
        """Invariant 1: core-memory tagged atoms never decay; transient atoms decay over 14d half-life."""
        amem = ValkeyAMEM()

        # Store a transient atom
        amem.store_atom(
            atom_id="transient_note",
            atom_text="Temporary scratch note about test job 442",
            keywords=["scratch", "note", "job"],
            is_core_memory=False,
            confidence=1.0,
        )

        # Store an immutable core-memory atom
        amem.store_atom(
            atom_id="cluster_vip",
            atom_text="Cluster VIP is https://192.0.2.245:8006 and management token has root@pam",
            keywords=["cluster", "vip", "management"],
            is_core_memory=True,
            confidence=1.0,
        )

        # Simulate 30 days of elapsed time (30 * 86400 seconds)
        past_time = time.time() - (30 * 86400)
        if amem.r:
            for cid in ("transient_note", "cluster_vip"):
                raw = amem.r.get(f"amem:card:{cid}")
                if raw:
                    data = json.loads(raw)
                    data["last_accessed_at"] = past_time
                    amem.r.set(f"amem:card:{cid}", json.dumps(data))
        else:
            amem._local_fallback_cards["transient_note"]["last_accessed_at"] = past_time
            amem._local_fallback_cards["cluster_vip"]["last_accessed_at"] = past_time

        # Recall atoms
        recalled_transient = amem.recall("scratch note", max_atoms=1)
        recalled_core = amem.recall("cluster vip", max_atoms=1)

        self.assertTrue(len(recalled_transient) > 0)
        self.assertTrue(len(recalled_core) > 0)

        # Transient card must have decayed significantly (half-life = 14 days; after 30 days, ~0.22)
        transient_conf = recalled_transient[0]["confidence"]
        core_conf = recalled_core[0]["confidence"]

        self.assertLess(transient_conf, 0.40, f"Transient atom did not decay: {transient_conf}")
        self.assertEqual(core_conf, 1.0, f"Core-memory atom decayed! Expected 1.0, got {core_conf}")


    def test_02_training_rebalance_70_30_and_quarantine(self):
        """Invariant 2: Dataset must enforce >= 70% Ground Truth and quarantine unverified ruminations."""
        rumination_filter = CuratedRuminationFilter(quarantine_dir=self.test_dir / "quarantine")
        ingestor = GroundTruthIngestor(data_root=str(self.test_dir / "training"))

        # Candidate ruminations (some unreviewed, some verified)
        candidate_ruminations = [
            {"id": "RUM-01", "messages": [{"role": "assistant", "content": "Unverified speculation"}], "frontier_verified": False, "test_passed": False},
            {"id": "RUM-02", "messages": [{"role": "assistant", "content": "Audited proof"}], "frontier_verified": True, "test_passed": True, "novelty_score": 0.92},
            {"id": "RUM-03", "messages": [{"role": "assistant", "content": "Redundant echo"}], "frontier_verified": True, "test_passed": True, "novelty_score": 0.05}, # fails novelty
            {"id": "RUM-04", "messages": [{"role": "assistant", "content": "Another unverified monologue"}], "frontier_verified": False, "test_passed": False},
        ]

        approved, quarantined = rumination_filter.filter_batch(candidate_ruminations)
        self.assertEqual(len(approved), 1, "Only RUM-02 should be approved")
        self.assertEqual(len(quarantined), 3, "RUM-01, RUM-03, and RUM-04 must be quarantined")

        # Create real-world samples
        real_transcripts = [
            {"user_prompt": f"Fix function #{i}", "assistant_response": f"return {i} * 2"}
            for i in range(15)
        ]
        real_diffs = [
            {"task": f"Refactor module {i}", "diff": f"+ def test_{i}(): pass"}
            for i in range(5)
        ]

        summary = ingestor.curate_balanced_dataset(
            raw_user_transcripts=real_transcripts,
            accepted_code_diffs=real_diffs,
            audited_ruminations=approved,
            output_filename="test_curated.jsonl",
        )

        self.assertGreaterEqual(summary.real_world_percentage, 70.0, "Real-world percentage fell below 70%!")
        self.assertGreater(summary.real_world_samples, summary.synthetic_audited_samples)

    def test_03_hardware_context_floor_and_offload_math(self):
        """Invariant 3: Context floor >= 4096 tokens must be strictly enforced."""
        engine = HardwareCapacityEngine()

        # Requesting 1024 context should be clamped to MIN_AGENT_CONTEXT_FLOOR (4096)
        estimate_clamped = engine.estimate(arch_type="9b", quant="q4_k_m", context_length=1024)
        self.assertEqual(estimate_clamped.context_length, MIN_AGENT_CONTEXT_FLOOR)

        # ROG Ally X 4 parallel slots @ 8192 context on 9B Q4_K_M
        estimate_ally = engine.estimate(
            arch_type="9b",
            quant="q4_k_m",
            context_length=8192,
            parallel_slots=4,
            kv_precision="q4_0",
            target_vram_gb=16.0,
        )
        self.assertTrue(estimate_ally.fits_in_vram)
        self.assertLess(estimate_ally.total_required_gb, 16.0)
        self.assertEqual(estimate_ally.gpu_layers_offload, estimate_ally.total_layers)

        # Invariant 3-2: Uncapped customization - 128k context on 14B
        estimate_128k = engine.estimate(
            arch_type="14b",
            quant="q4_k_m",
            context_length=131072,
            target_vram_gb=12.0,
        )
        self.assertEqual(estimate_128k.context_length, 131072)
        self.assertGreater(estimate_128k.kv_cache_gb, 5.0)
        self.assertGreater(estimate_128k.cpu_ram_spillover_gb, 0.0)

        # Invariant 3-2: Infinite Horizon - 1,048,576 (1M) context on 3B
        estimate_1m = engine.estimate(
            arch_type="3b",
            quant="q4_0",
            context_length=1048576,
            target_vram_gb=24.0,
        )
        self.assertEqual(estimate_1m.context_length, 1048576)
        self.assertGreater(estimate_1m.kv_cache_gb, 10.0)
        self.assertGreater(estimate_1m.total_required_gb, 20.0)

        # Presets check
        presets = engine.get_presets()
        self.assertIn("frontier_128k", presets)
        self.assertIn("ultra_256k", presets)
        self.assertIn("needle_1m", presets)

    @unittest.skipUnless(HAVE_AGENT_NUDGE, "agent-nudge repo is not checked out beside this one")
    def test_04_agent_nudge_watchdog_and_interrupt(self):
        """Invariant 4: Standalone agent-nudge detects stuck loops and unblocks agents."""
        nudge_engine = NudgeEngine()
        nudge_engine.register_agent("agent-test", name="TestAgent")
        detector = WatchdogDetector(engine=nudge_engine, max_identical_tool_calls=3)

        # Simulate 3 identical repeated tool calls
        detector.record_tool_call("agent-test", "read_file", {"path": "empty.log"})
        detector.record_tool_call("agent-test", "read_file", {"path": "empty.log"})
        is_stuck = detector.record_tool_call("agent-test", "read_file", {"path": "empty.log"})

        self.assertTrue(is_stuck, "Watchdog failed to detect 3 repeated identical tool calls")

        # Nudge the agent
        res = nudge_engine.nudge("agent-test", directive="Stop polling empty.log; report timeout.")
        self.assertTrue(res.ok)
        self.assertEqual(res.agent_id, "agent-test")



    def test_05_obsidian_vault_3_file_cleanliness(self):
        """Invariant 5: Obsidian Vault strictly allows only the 3 human coordination files."""
        gateway = ObsidianGateway(vault_root=str(self.test_dir / "obsidian_vault"))

        # Writing to allowed file 1: AI/CLUSTER_FLEET_STATUS.md
        res1 = gateway.update_cluster_fleet_status({
            "status": "healthy",
            "nodes_online": 4,
            "gpu_primary": "RX 6750 XT 12GB (Online)",
            "gpu_secondary": "RX 6600 XT 8GB (Online)",
            "edge_node": "Asus ROG Ally X 24GB (Active)",
        })
        self.assertTrue(res1.get("ok"))

        # Writing to allowed file 2: AI/ACTIVE_PROJECTS_DIGEST.md
        res2 = gateway.update_active_projects_digest([
            {"project": "Unified Harness", "lead": "Antigravity", "milestone": "Testing"}
        ])
        self.assertTrue(res2.get("ok"))

        # Writing to allowed file 3: AI/FRONTIER_VERIFIED_INSIGHTS.md
        res3 = gateway.append_frontier_insight(
            insight_title="Dual-GPU Speculative Speedup",
            body="RX 6600 XT draft + RX 6750 XT target achieves ~2.1x throughput.",
            invariant_id="INV-SPEC-01",
        )
        self.assertTrue(res3.get("ok"))

        # Attempt to write an unauthorized file (e.g. raw agent identity or raw dossier)
        res_blocked = gateway.write_arbitrary_file("raw_dossier_102.md", "Internal AI thoughts...")
        self.assertFalse(res_blocked.get("ok"))
        self.assertIn("strictly restricted", res_blocked.get("error", "").lower())

    def test_06_edge_fleet_handover_protocol(self):
        """Invariant 6: Roaming Handover package compile, serialization, and OTA distribution."""
        handover_mgr = RoamingHandoverManager(storage_dir=self.test_dir / "handovers")

        package = handover_mgr.create_handover_package(
            task_id="TASK-EDGE-ROAM-01",
            objective="Compile localized unit tests and optimize STT latency",
            actions_summary="Ran 24 benchmarks, reduced Whisper STT chunk latency by 14ms.",
            git_diff="+ def optimize_stt_chunks(): pass",
            invariant_score=0.95,
        )

        self.assertIsNotNone(package)
        self.assertTrue(Path(package.handover_file).exists())
        self.assertEqual(package.test_results.get("task_id"), "TASK-EDGE-ROAM-01")


        # Test OTA distributor notification
        ota = OTAModelDistributor()
        res = ota.notify_edge_nodes_new_model(
            model_name="Ornith-1.5-9B-edge-v2",
            gguf_artifact_url="http://192.0.2.105:8088/models/Ornith-1.5-9B-edge-v2.gguf",
            sha256_checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_quant="q4_k_m"
        )
        self.assertIsInstance(res, dict)


if __name__ == "__main__":
    unittest.main()
