"""
Unit tests for Multi-Stream Concurrent Reasoning Orchestrator & TUI.
Verifies interactive configuration plan generation, agent/node assignment,
live TUI rendering, and non-blocking interruption.
"""

import unittest
from unittest.mock import patch, MagicMock
from harness.cli.multi_stream_view import (
    MultiStreamTUI,
    AgentStreamConfig,
    interactive_stream_wizard,
)


class TestMultiStreamView(unittest.TestCase):
    def setUp(self):
        self.tui = MultiStreamTUI(task_title="Test Invariant Benchmark")

    def test_stream_registration_and_update(self):
        """Verify stream state transitions and chunk appending."""
        state = self.tui.register_stream(
            agent_id="test-agent",
            agent_name="TestAgent",
            node_id="node1_primary",
            node_name="VM 102 Vulkan0",
            model_name="moe",
            slot_id=0
        )
        self.assertEqual(state.status, "connecting")
        self.assertEqual(state.agent_id, "test-agent")

        # Append thought chunk
        self.tui.update_stream("test-agent", thought="Analyzing concurrency invariants...", tps=45.0)
        self.assertEqual(state.status, "thinking")
        self.assertIn("concurrency", state.current_thought)

        # Append content chunk
        self.tui.update_stream("test-agent", content="def test(): pass", tps=50.0)
        self.assertEqual(state.status, "executing")
        self.assertIn("def test", state.current_content)

        # Mark done
        self.tui.mark_done("test-agent")
        self.assertEqual(state.status, "done")
        self.assertIsNotNone(state.end_time)

    def test_render_view_output(self):
        """Verify Rich panel layout generates without exceptions."""
        self.tui.register_stream("a1", "Agent 1", "node1_primary", "VM 102", "moe", 0)
        self.tui.register_stream("a2", "Agent 2", "node2_ally_x", "ROG Ally X", "gemma", 1)
        self.tui.update_stream("a1", thought="Thinking step 1", tps=40.0)
        self.tui.update_stream("a2", content="Code output 1", tps=80.0)

        panel = self.tui.render_view()
        self.assertIsNotNone(panel)

    @patch("harness.cli.multi_stream_view.fleet_config.poll_node_models")
    @patch("rich.prompt.Prompt.ask")
    def test_interactive_wizard_plan(self, mock_ask, mock_poll):
        """Verify wizard prompts for prompt, stream count, agents, and nodes."""
        mock_poll.return_value = {
            "node1_primary": {"status": "online", "model": "coordinator-14b", "models": []},
            "node2_ally_x": {
                "status": "online",
                "model": "gemma-4-12b-coder-fable5-composer2.5-v1",
                "models": [
                    {"id": "gemma-4-12b-coder-fable5-composer2.5-v1"},
                    {"id": "qwen2.5-coder-7b"}
                ]
            }
        }
        # Mock answers:
        # 1. Number of streams -> "2"
        # 2. Agent 1 -> "aevum"
        # 3. Node for Agent 1 -> "node1_primary"
        # 4. Agent 2 -> "sentinel"
        # 5. Node for Agent 2 -> "node2_ally_x"
        # 6. Model on node2_ally_x -> "gemma-4-12b-coder-fable5-composer2.5-v1"
        # 7. Confirm launch -> "y"
        mock_ask.side_effect = [
            "2",
            "aevum",
            "node1_primary",
            "sentinel",
            "node2_ally_x",
            "gemma-4-12b-coder-fable5-composer2.5-v1",
            "y"
        ]

        # the fleet's edge node comes from the operator's private config.json; give the test its own so it runs anywhere (CI)
        from harness.cli.multi_stream_view import fleet_config
        from harness.config import NodeEndpoint
        edge = NodeEndpoint(node_id="node2_edge", name="Edge node", base_url="http://127.0.0.1:1234/v1",
                            api_type="lm-studio", role="edge_pool", is_roaming=True)
        with patch.dict(fleet_config.nodes, {"node2_edge": edge}):
            plan = interactive_stream_wizard(prompt_arg="Verify thread safety in SQLite WAL")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["prompt"], "Verify thread safety in SQLite WAL")
        self.assertEqual(len(plan["configs"]), 2)

        cfg1 = plan["configs"][0]
        self.assertEqual(cfg1.agent_id, "aevum")
        self.assertEqual(cfg1.node_id, "node1_primary")

        cfg2 = plan["configs"][1]
        self.assertEqual(cfg2.agent_id, "sentinel")
        self.assertEqual(cfg2.node_id, "node2_ally_x")
        self.assertEqual(cfg2.model_name, "gemma-4-12b-coder-fable5-composer2.5-v1")


if __name__ == "__main__":
    unittest.main()
