"""
Unit tests for In-Repository Project-Bound Agent DNA:
- Lifecycle: init_project_agent, load_project_agent, compile_agent_system_prompt
- Living Invariants: append_project_invariant with semantic deduplication guard
- Rolling Handover: update_project_handover
- Option E Hybrid Sentinels: extract_sentinel_tags
- Backend workspace scanning agent association
"""

import os
import json
import shutil
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "StoneSage", "backend")))

from harness.core.openclaw_engine import openclaw_engine
from stonesage.server import list_workspace_directories, save_workspace_roots

class TestProjectAgentDNA(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.proj_dir = os.path.join(self.temp_dir, "vulkan_engine")
        os.makedirs(self.proj_dir, exist_ok=True)

        self.orig_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_and_load_project_agent(self):
        """Verify initialization of .stonesage/ contracts and loading of agent DNA."""
        agent_meta = openclaw_engine.init_project_agent(
            project_dir=self.proj_dir,
            agent_id="agent_vulkan_101",
            name="Hephaestus",
            role="Vulkan Compute Architect",
            mission="Maintain zero-divergence GPU memory barriers",
            autonomy_level="tiered",
            tone="Analytical and rigorous"
        )

        self.assertEqual(agent_meta["name"], "Hephaestus")
        self.assertEqual(agent_meta["agent_id"], "agent_vulkan_101")

        # Verify filesystem artifacts in .stonesage/
        dot_stonesage = os.path.join(self.proj_dir, ".stonesage")
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "agent.json")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "IDENTITY.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "SOUL.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "INVARIANTS.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "HANDOVER.md")))

        # Load back via load_project_agent
        loaded = openclaw_engine.load_project_agent(self.proj_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["agent_id"], "agent_vulkan_101")
        self.assertEqual(loaded["name"], "Hephaestus")
        self.assertEqual(loaded["role"], "Vulkan Compute Architect")
        self.assertIn("Hephaestus", loaded["identity_md"])
        self.assertIn("Core Imperatives", loaded["soul_md"])
        self.assertIn("PROJECT ARCHITECTURAL INVARIANTS", loaded["invariants_md"])
        self.assertIn("ROLLING MILESTONE HANDOVER", loaded["handover_md"])

    def test_compile_agent_system_prompt(self):
        """Verify compilation of model-agnostic system prompt including persona, invariants, and handovers."""
        openclaw_engine.init_project_agent(
            project_dir=self.proj_dir,
            agent_id="agent_vulkan_101",
            name="Hephaestus",
            role="Vulkan Compute Architect",
            mission="Maintain zero-divergence GPU memory barriers",
            autonomy_level="tiered"
        )

        prompt = openclaw_engine.compile_agent_system_prompt(
            project_dir=self.proj_dir,
            base_prompt="You are an expert AI software harness."
        )

        self.assertIn("You are an expert AI software harness.", prompt)
        self.assertIn("ASSIGNED PROJECT AGENT: Hephaestus", prompt)
        self.assertIn("agent_vulkan_101", prompt)
        self.assertIn("Vulkan Compute Architect", prompt)
        self.assertIn("<invariant>", prompt)
        self.assertIn("<handover>", prompt)
        self.assertIn("Active Project Invariants", prompt)
        self.assertIn("Rolling Handover & Recent Memory State", prompt)

    def test_append_invariant_and_deduplication_guard(self):
        """Verify appending project invariants and semantic deduplication."""
        openclaw_engine.init_project_agent(
            project_dir=self.proj_dir,
            agent_id="agent_vulkan_101",
            name="Hephaestus",
            role="Architect",
            mission="Testing invariants",
            autonomy_level="tiered"
        )

        inv_text = "Vulkan command pools must never be reset across worker threads without explicit barrier synchronization."

        # First append should succeed
        ok1, msg1 = openclaw_engine.append_project_invariant(self.proj_dir, inv_text, deduplicate=True)
        self.assertTrue(ok1)
        self.assertIn("recorded", msg1.lower())

        # Verify invariant is in file
        agent_data = openclaw_engine.load_project_agent(self.proj_dir)
        self.assertIn(inv_text, agent_data["invariants_md"])

        # Duplicate append should be rejected by deduplication guard
        ok2, msg2 = openclaw_engine.append_project_invariant(self.proj_dir, inv_text, deduplicate=True)
        self.assertFalse(ok2)
        self.assertIn("duplicate", msg2.lower())

        # Normalized duplicate (different case and punctuation) should also be rejected
        norm_text = "Vulkan command pools must never be reset across worker threads without explicit barrier synchronization!!!"
        ok3, msg3 = openclaw_engine.append_project_invariant(self.proj_dir, norm_text, deduplicate=True)
        self.assertFalse(ok3)
        self.assertIn("duplicate", msg3.lower())

        # Distinct invariant should succeed
        distinct_text = "Buffer allocations exceeding 64MB must utilize dedicated device local heaps."
        ok4, msg4 = openclaw_engine.append_project_invariant(self.proj_dir, distinct_text, deduplicate=True)
        self.assertTrue(ok4)
        self.assertIn("recorded", msg4.lower())

    def test_update_project_handover(self):
        """Verify appending rolling milestone handover progress to HANDOVER.md."""
        openclaw_engine.init_project_agent(
            project_dir=self.proj_dir,
            agent_id="agent_vulkan_101",
            name="Hephaestus",
            role="Architect",
            mission="Testing handovers",
            autonomy_level="tiered"
        )

        handover_entry = "Completed Phase 1 compute shader pipeline benchmarks on Vulkan0 (RX 6750 XT)."
        ok = openclaw_engine.update_project_handover(self.proj_dir, handover_entry)
        self.assertTrue(ok)

        loaded = openclaw_engine.load_project_agent(self.proj_dir)
        self.assertIn(handover_entry, loaded["handover_md"])

    def test_extract_sentinel_tags(self):
        """Verify Option E autonomous regex extraction of <invariant> and <handover> sentinel tags."""
        streaming_output = (
            "I have analyzed the buffer allocation strategy.\n"
            "<invariant>Always map staging buffers with VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT.</invariant>\n"
            "We also finished the benchmark step.\n"
            "<handover>Milestone 2 completed: memory heaps calibrated across 12GB VRAM.</handover>\n"
            "Ready for operator directive."
        )

        invariants, handovers, clean_text = openclaw_engine.extract_sentinel_tags(streaming_output)

        self.assertEqual(len(invariants), 1)
        self.assertEqual(
            invariants[0],
            "Always map staging buffers with VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT."
        )
        self.assertEqual(len(handovers), 1)
        self.assertEqual(
            handovers[0],
            "Milestone 2 completed: memory heaps calibrated across 12GB VRAM."
        )

        # Ensure sentinel tags were cleanly stripped from readable content
        self.assertNotIn("<invariant>", clean_text)
        self.assertNotIn("</invariant>", clean_text)
        self.assertNotIn("<handover>", clean_text)
        self.assertNotIn("</handover>", clean_text)
        self.assertIn("I have analyzed the buffer allocation strategy.", clean_text)
        self.assertIn("Ready for operator directive.", clean_text)

    def test_workspace_scanning_with_bound_agent(self):
        """Verify list_workspace_directories discovers and exposes the bound agent DNA."""
        save_workspace_roots([
            {"id": "root_test", "label": "Test Root", "path": self.temp_dir, "node_id": "local"}
        ])

        # Bind agent to vulkan_engine project
        openclaw_engine.init_project_agent(
            project_dir=self.proj_dir,
            agent_id="agent_vulkan_101",
            name="Hephaestus",
            role="Vulkan Compute Architect",
            mission="Long term GPU engine maintenance",
            autonomy_level="tiered"
        )

        workspaces = list_workspace_directories()
        vulkan_ws = next((w for w in workspaces if w["name"] == "vulkan_engine"), None)
        self.assertIsNotNone(vulkan_ws)
        self.assertIsNotNone(vulkan_ws["agent"])
        self.assertEqual(vulkan_ws["agent"]["name"], "Hephaestus")
        self.assertEqual(vulkan_ws["agent"]["agent_id"], "agent_vulkan_101")
        self.assertEqual(vulkan_ws["agent"]["role"], "Vulkan Compute Architect")

if __name__ == "__main__":
    unittest.main()
