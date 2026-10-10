"""
Unit tests for Multi-Stream Cross-Examination & Consensus Engine.
Verifies plan construction, 3-stage execution, peer critiques, and Founder adjudication.
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock
from harness.core.consensus_engine import (
    ConsensusEngine,
    ConsensusPlan,
    ConsensusResult,
)
from harness.core.llama_client import StreamChunk


class TestConsensusEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ConsensusEngine()

    def test_build_plan_defaults(self):
        """Verify consensus plan maps default agents, lead founder, and endpoints."""
        plan = self.engine.build_plan(
            prompt="Implement an atomic lock-free queue in Python",
            agent_ids=["aevum", "sentinel"],
            domain="software"
        )
        self.assertEqual(plan.task_prompt, "Implement an atomic lock-free queue in Python")
        self.assertEqual(plan.lead_founder_id, "aule")
        self.assertEqual(len(plan.agents), 2)
        self.assertEqual(plan.agents[0]["agent_id"], "aevum")
        self.assertEqual(plan.agents[1]["agent_id"], "sentinel")

    @patch("harness.core.llama_client.LlamaClient.chat_stream")
    def test_execute_consensus_three_stages(self, mock_chat_stream):
        """Verify the 3-stage flow: (1) Parallel Drafts -> (2) Peer Critiques -> (3) Founder Synthesis."""
        # Setup mock generator returning simple output chunks
        async def mock_generator(messages, request_id="", **kwargs):
            if "sol_" in request_id:
                yield StreamChunk(chunk_type="output", content=f"Draft solution from {request_id}")
            elif "crit_" in request_id:
                yield StreamChunk(chunk_type="output", content=f"Critique for {request_id}: Needs lock verification")
            elif "founder_synth" in request_id:
                yield StreamChunk(chunk_type="output", content="Verified Consensus Solution by Founder Aule")
            yield StreamChunk(chunk_type="done", content="")

        mock_chat_stream.side_effect = mock_generator

        plan = self.engine.build_plan(
            prompt="Analyze race conditions in shared memory ring buffer",
            agent_ids=["aevum", "sentinel"],
            domain="software"
        )

        stages_recorded = []
        def progress_cb(stage_type, data):
            stages_recorded.append((stage_type, data))

        result = asyncio.run(self.engine.execute_consensus(plan, progress_callback=progress_cb))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.task_prompt, "Analyze race conditions in shared memory ring buffer")
        self.assertEqual(result.lead_founder_id, "aule")

        # Stage 1 assertions
        self.assertIn("aevum", result.initial_solutions)
        self.assertIn("sentinel", result.initial_solutions)
        self.assertIn("Draft solution", result.initial_solutions["aevum"])

        # Stage 2 assertions (bilateral critique)
        self.assertIn("aevum", result.peer_critiques)
        self.assertIn("sentinel", result.peer_critiques["aevum"])
        self.assertIn("Needs lock verification", result.peer_critiques["aevum"]["sentinel"])

        # Stage 3 assertions (Founder synthesis)
        self.assertIn("Verified Consensus Solution", result.final_synthesis)
        self.assertGreaterEqual(result.elapsed_seconds, 0.0)  # mocked run can finish inside one Windows clock tick

        # Verify progress stages were reported
        stage_starts = [d["stage"] for t, d in stages_recorded if t == "stage_start"]
        self.assertEqual(stage_starts, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
