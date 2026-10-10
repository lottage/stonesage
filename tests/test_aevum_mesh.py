"""
Unit tests for Aevum Mesh Sovereign Agent Registry & Autonomous Play Engine.
Validates multi-node agent registry tracking, operator task assignment,
heartbeat tracking, and autonomous self-directed play (The Agora & Dossiers).
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from harness.core.aevum_mesh import aevum_mesh
from harness.cli.agent_shell import AgentShell
from harness.data_fabric.pg_storage import relational_storage


class TestAevumMesh(unittest.TestCase):
    def setUp(self):
        aevum_mesh.sync_registry()
        # Clean aevum state for isolation
        aevum_mesh.clear_user_task("aevum")

    def tearDown(self):
        aevum_mesh.clear_user_task("aevum")

    def test_registry_sync_and_list(self):
        """Verify all registered OpenClaw agents are tracked with assigned node and status."""
        agents = aevum_mesh.list_agents()
        self.assertGreater(len(agents), 0)
        
        agent_ids = [a["agent_id"] for a in agents]
        self.assertIn("aevum", agent_ids)

        ss = next(a for a in agents if a["agent_id"] == "aevum")
        self.assertIn("assigned_node", ss)
        self.assertEqual(ss["assigned_node"], "node1_primary")
        self.assertIn("role", ss)
        self.assertIn("status", ss)

    def test_assign_and_clear_user_task(self):
        """Verify user task assignment updates status and clearing returns to idle."""
        success = aevum_mesh.assign_user_task("aevum", "Refactor context compression pipeline")
        self.assertTrue(success)

        agent = aevum_mesh.get_agent("aevum")
        self.assertIsNotNone(agent)
        self.assertEqual(agent["current_task"], "Refactor context compression pipeline")
        self.assertIn(agent["status"], ("active", "user_active"))

        # Now clear the task
        clear_success = aevum_mesh.clear_user_task("aevum")
        self.assertTrue(clear_success)

        cleared_agent = aevum_mesh.get_agent("aevum")
        self.assertIsNone(cleared_agent["current_task"])
        self.assertEqual(cleared_agent["status"], "idle")

    def test_autonomous_play_blocked_when_user_task_active(self):
        """Verify agents with an active user task will NOT drift into autonomous play."""
        aevum_mesh.assign_user_task("aevum", "High priority cluster benchmark execution")
        
        result = aevum_mesh.trigger_autonomous_play("aevum", mode="auto")
        self.assertEqual(result.get("status"), "blocked")
        self.assertIn("has an active operator task", result.get("message", ""))
        self.assertEqual(result.get("current_task"), "High priority cluster benchmark execution")

    def test_autonomous_play_dossier_generation(self):
        """Verify idle agents can generate a self-directed autonomous research dossier."""
        aevum_mesh.clear_user_task("aevum")
        
        result = aevum_mesh.trigger_autonomous_play("aevum", mode="dossier")
        self.assertEqual(result.get("status"), "dispatched")
        self.assertEqual(result.get("mode"), "dossier")
        self.assertIsNotNone(result.get("topic"))
        
        filepath = result.get("target")
        self.assertTrue(os.path.exists(filepath))
        
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Autonomous Research Dossier", content)
            self.assertIn("aevum", content)
            self.assertIn(result["topic"], content)

    @patch("urllib.request.urlopen")
    def test_autonomous_play_agora_dispatch(self, mock_urlopen):
        """Verify idle agents dispatch reflections to The Agora chatboard (:8766)."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response

        aevum_mesh.clear_user_task("aevum")
        result = aevum_mesh.trigger_autonomous_play("aevum", mode="agora")
        
        self.assertEqual(result.get("status"), "dispatched")
        self.assertEqual(result.get("mode"), "agora")
        self.assertIn("channel", result)
        self.assertIn(result["channel"], ["agora", "systems-code", "first-principles", "deep-ruminations", "forbidden-knowledge"])

    def test_cli_agent_shell_task_and_clear(self):
        """Verify CLI /agent task and /agent clear subcommands update the mesh registry."""
        AgentShell.handle_agent_command(["task", "aevum", "Validate Valkey A-MEM throughput"])
        agent = aevum_mesh.get_agent("aevum")
        self.assertEqual(agent["current_task"], "Validate Valkey A-MEM throughput")

        AgentShell.handle_agent_command(["clear", "aevum"])
        agent = aevum_mesh.get_agent("aevum")
        self.assertIsNone(agent["current_task"])

    def test_reproduce_agents_success(self):
        """Verify bilateral digital crossover creates a Generation-(N+1) hybrid offspring."""
        aevum_mesh.clear_user_task("aevum")
        aevum_mesh.clear_user_task("steve")

        res = aevum_mesh.reproduce_agents(
            parent_a_id="aevum",
            parent_b_id="steve",
            focus_intent="Automated invariant fuzzing and continuous information harvesting",
            custom_name="Aevum-Steve-Child-Unit"
        )
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("status"), "success")
        child = res.get("child_agent")
        self.assertIsNotNone(child)
        self.assertEqual(child["name"], "Aevum-Steve-Child-Unit")
        self.assertIn("Aevum", str(res.get("lineage", {}).get("parent_names")))
        self.assertIn("Steve", str(res.get("lineage", {}).get("parent_names")))
        self.assertEqual(res.get("lineage", {}).get("generation"), 2)

        # Check registered in relational storage
        hive_child = aevum_mesh.get_agent(child["agent_id"])
        self.assertIsNotNone(hive_child)
        self.assertEqual(hive_child["name"], "Aevum-Steve-Child-Unit")

    def test_reproduce_agents_anti_incest_and_guards(self):
        """Verify digital reproduction enforces anti-incest and diversity invariants."""
        # 1. Self reproduction forbidden
        self_res = aevum_mesh.reproduce_agents("aevum", "aevum")
        self.assertFalse(self_res.get("ok"))
        self.assertIn("distinct parent agents", self_res.get("error", ""))

        # 2. Parent-child crossover prohibited
        relational_storage.upsert_hive_agent(
            agent_id="parent_alpha",
            name="Parent Alpha",
            role="Systems Engineer",
            assigned_node="node1_primary",
            status="idle",
            metadata={"lineage": {"generation": 1, "parents": []}}
        )
        relational_storage.upsert_hive_agent(
            agent_id="child_beta",
            name="Child Beta",
            role="Junior Systems Engineer",
            assigned_node="node1_primary",
            status="idle",
            metadata={"lineage": {"generation": 2, "parents": ["parent_alpha"]}}
        )
        incest_res = aevum_mesh.reproduce_agents("parent_alpha", "child_beta")
        self.assertFalse(incest_res.get("ok"))
        self.assertIn("Direct parent-child crossover is prohibited", incest_res.get("error", ""))

        # 3. Sibling crossover prohibited
        relational_storage.upsert_hive_agent(
            agent_id="child_gamma",
            name="Child Gamma",
            role="Junior Systems Engineer 2",
            assigned_node="node1_primary",
            status="idle",
            metadata={"lineage": {"generation": 2, "parents": ["parent_alpha"]}}
        )
        sibling_res = aevum_mesh.reproduce_agents("child_beta", "child_gamma")
        self.assertFalse(sibling_res.get("ok"))
        self.assertIn("Sibling crossover is prohibited", sibling_res.get("error", ""))

    def test_autonomous_play_reproduce_mode(self):
        """Verify trigger_autonomous_play can execute mode='reproduce' when idle agents exist."""
        aevum_mesh.clear_user_task("aevum")
        aevum_mesh.clear_user_task("steve")

        res = aevum_mesh.trigger_autonomous_play(agent_id="aevum", mode="reproduce")
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("mode"), "reproduce")
        self.assertIn("child_name", res)
        self.assertIn("Bilateral digital crossover completed", res.get("message", ""))

    def test_cli_mesh_mate_command(self):
        """Verify CLI /mesh mate command execution."""
        from harness.cli.commands import CommandRegistry
        CommandRegistry.handle_mesh(["mate", "aevum", "steve", "Database integrity watcher"])
        # Verify no unhandled exceptions and agents still healthy
        agents = aevum_mesh.list_agents()
        self.assertGreater(len(agents), 2)


if __name__ == "__main__":
    unittest.main()
