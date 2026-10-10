"""
Unit tests for CLI Cross-Endpoint Project Lifecycle and In-Repo Agent DNA:
- Project registration and persistence in relational storage (aevum_project_registry)
- ProjectManager creation of .stonesage/ DNA and StoneSage metadata
- Project entering, context switching, and in-repo Agent DNA loading
- Dynamic per-turn context injection of project persona and invariants
- CLI CommandRegistry.handle_project execution (list, enter, create, leave, status, invariant)
"""

import os
import json
import shutil
import tempfile
import unittest

from harness.data_fabric.pg_storage import relational_storage
from harness.core.project_manager import project_manager
from harness.core.openclaw_engine import openclaw_engine
from harness.core.context_fabric import context_fabric
from harness.cli.agent_shell import AgentShell
from harness.cli.commands import CommandRegistry


class TestProjectCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.proj_path = os.path.join(self.temp_dir, "quantum_solver").replace("\\", "/")
        AgentShell.active_agent = None
        AgentShell.active_project = None

    def tearDown(self):
        AgentShell.active_agent = None
        AgentShell.active_project = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Cleanup DB
        relational_storage.delete_project("quantum-solver")
        relational_storage.delete_project("custom-proj")

    def test_project_registry_crud(self):
        """Test relational storage CRUD for aevum_project_registry."""
        proj = relational_storage.upsert_project(
            project_id="test-proj-01",
            name="Test Project",
            path="c:/test/path",
            node_id="vm102_compute",
            bound_agent_id="agent_test",
            status="active",
            metadata={"priority": "high"}
        )
        self.assertIsNotNone(proj)
        self.assertEqual(proj["project_id"], "test-proj-01")
        self.assertEqual(proj["name"], "Test Project")
        self.assertEqual(proj["node_id"], "vm102_compute")
        self.assertEqual(proj["bound_agent_id"], "agent_test")

        # Get by ID
        fetched = relational_storage.get_project("test-proj-01")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "Test Project")

        # Get by name (case-insensitive)
        fetched_by_name = relational_storage.get_project("test project")
        self.assertIsNotNone(fetched_by_name)
        self.assertEqual(fetched_by_name["project_id"], "test-proj-01")

        # Bind agent
        relational_storage.bind_agent_to_project("test-proj-01", "agent_quantum")
        updated = relational_storage.get_project("test-proj-01")
        self.assertEqual(updated["bound_agent_id"], "agent_quantum")

        # Delete
        relational_storage.delete_project("test-proj-01")
        self.assertIsNone(relational_storage.get_project("test-proj-01"))

    def test_project_manager_create_and_scaffold(self):
        """Test ProjectManager scaffolding of directory, .stonesage DNA, and StoneSage metadata."""
        proj = project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_name="Schrodinger",
            agent_role="Quantum Circuit Architect",
            agent_mission="Optimize unitary gate decompositions",
            autonomy_level="tiered"
        )
        self.assertEqual(proj["project_id"], "quantum-solver")
        self.assertEqual(proj["name"], "Quantum Solver")

        # Verify .stonesage/ filesystem DNA
        dot_stonesage = os.path.join(self.proj_path, ".stonesage")
        self.assertTrue(os.path.exists(dot_stonesage))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "agent.json")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "IDENTITY.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "SOUL.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "INVARIANTS.md")))
        self.assertTrue(os.path.exists(os.path.join(dot_stonesage, "HANDOVER.md")))

        # Verify StoneSage metadata file (.stonesage-project.json)
        self.assertTrue(os.path.exists(os.path.join(self.proj_path, ".stonesage-project.json")))

        # Verify Hive Network agent registration
        hive_agent = relational_storage.get_hive_agent(proj["bound_agent_id"])
        self.assertIsNotNone(hive_agent)
        self.assertEqual(hive_agent["name"], "Schrodinger")

    def test_enter_project_and_context_switching(self):
        """Test entering a project, loading in-repo Agent DNA, and active session context."""
        project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_name="Schrodinger",
            agent_role="Quantum Circuit Architect",
            agent_mission="Optimize unitary gate decompositions"
        )

        proj, agent_profile = project_manager.enter_project("quantum-solver")
        self.assertIsNotNone(proj)
        self.assertIsNotNone(agent_profile)
        self.assertEqual(proj["name"], "Quantum Solver")
        self.assertEqual(agent_profile["name"], "Schrodinger")
        self.assertEqual(agent_profile["role"], "Quantum Circuit Architect")
        self.assertIn("Schrodinger", agent_profile["identity_md"])

    def test_context_fabric_with_active_project(self):
        """Test per-turn context injection with active project persona and invariants."""
        project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_name="Schrodinger",
            agent_role="Quantum Circuit Architect"
        )

        # Record a project invariant
        openclaw_engine.append_project_invariant(
            self.proj_path,
            "Unitary gates must preserve Hermitian conjugate norms under SU(2)."
        )

        prompt = context_fabric.compile_dynamic_turn(
            user_query="Design a Hadamard gate decomposition.",
            project_dir=self.proj_path
        )

        self.assertIn("Schrodinger", prompt)
        self.assertIn("Quantum Circuit Architect", prompt)
        self.assertIn("Project: `quantum_solver`", prompt)
        self.assertIn("[ACTIVE PROJECT INVARIANTS]", prompt)
        self.assertIn("Unitary gates must preserve Hermitian conjugate norms", prompt)

    def test_cli_handle_project_commands(self):
        """Test CommandRegistry.handle_project subcommands: list, create, enter, status, leave, invariant."""
        # 1. Create project via ProjectManager
        project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_name="Schrodinger"
        )

        # 2. Test /project list
        CommandRegistry.handle_project(["list"])

        # 3. Test /project enter
        CommandRegistry.handle_project(["enter", "quantum-solver"])
        self.assertIsNotNone(AgentShell.active_project)
        self.assertEqual(AgentShell.active_project["project_id"], "quantum-solver")
        self.assertIsNotNone(AgentShell.active_agent)
        self.assertEqual(AgentShell.active_agent["name"], "Schrodinger")

        # 4. Test /project status
        CommandRegistry.handle_project(["status"])

        # 5. Test /project invariant
        CommandRegistry.handle_project(["invariant", "Zero decoherence during gate pulse sequences."])
        p_agent = openclaw_engine.load_project_agent(self.proj_path)
        self.assertIn("Zero decoherence during gate pulse sequences.", p_agent["invariants_md"])

        # 6. Test /project leave
        CommandRegistry.handle_project(["leave"])
        self.assertIsNone(AgentShell.active_project)

    def test_promote_project_agent_overwrite(self):
        """Test promoting project agent by overwriting the base agent."""
        # Setup base project
        proj = project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_id="base-schrodinger",
            agent_name="Schrodinger Base",
            agent_role="Junior Gate Optimizer"
        )
        # Record an invariant in project
        openclaw_engine.append_project_invariant(
            self.proj_path,
            "Hamiltonian evolution must preserve unitarity under time-reversal symmetry."
        )

        res = project_manager.promote_project_agent(
            project_identifier="quantum-solver",
            mode="overwrite",
            target_agent_id="base-schrodinger"
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "overwrite")
        self.assertEqual(res["agent_id"], "base-schrodinger")
        self.assertGreaterEqual(res["invariants_count"], 1)

        # Verify contracts in profiles_dir
        dest_dir = os.path.join(openclaw_engine.profiles_dir, "base-schrodinger")
        self.assertTrue(os.path.exists(dest_dir))
        with open(os.path.join(dest_dir, "SOUL.md"), "r", encoding="utf-8") as f:
            soul = f.read()
        self.assertIn("Agent Evolution Lineage", soul)
        self.assertIn("Quantum Solver", soul)

        with open(os.path.join(dest_dir, "INVARIANTS.md"), "r", encoding="utf-8") as f:
            inv = f.read()
        self.assertIn("Hamiltonian evolution must preserve unitarity", inv)

        # Verify registered in Aevum Hive Network
        hive_a = relational_storage.get_hive_agent("base-schrodinger")
        self.assertIsNotNone(hive_a)
        self.assertIn("Promoted from project", hive_a["current_task"])

        # Cleanup
        shutil.rmtree(dest_dir, ignore_errors=True)
        json_file = os.path.join(openclaw_engine.profiles_dir, "base-schrodinger.json")
        if os.path.exists(json_file):
            os.remove(json_file)

    def test_promote_project_agent_spawn_new(self):
        """Test promoting project agent by spawning a brand-new standalone agent."""
        proj = project_manager.create_project(
            path=self.proj_path,
            name="Quantum Solver",
            agent_id="agent_source",
            agent_name="Schrodinger Source"
        )
        openclaw_engine.append_project_invariant(
            self.proj_path,
            "Adiabatic shortcuts require counter-diabatic driving terms."
        )

        res = project_manager.promote_project_agent(
            project_identifier="quantum-solver",
            mode="new",
            target_agent_id="quantum-prime",
            new_name="Quantum Prime",
            assigned_node="node2_ally_extreme"
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "new")
        self.assertEqual(res["agent_id"], "quantum-prime")
        self.assertEqual(res["name"], "Quantum Prime")
        self.assertEqual(res["assigned_node"], "node2_ally_extreme")

        # Verify new contracts in profiles_dir
        dest_dir = os.path.join(openclaw_engine.profiles_dir, "quantum-prime")
        self.assertTrue(os.path.exists(dest_dir))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "IDENTITY.md")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "SOUL.md")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "INVARIANTS.md")))
        self.assertTrue(os.path.exists(os.path.join(dest_dir, "HANDOVER.md")))

        with open(os.path.join(dest_dir, "INVARIANTS.md"), "r", encoding="utf-8") as f:
            inv = f.read()
        self.assertIn("Adiabatic shortcuts require counter-diabatic driving terms.", inv)

        # Verify Aevum Hive registry and session
        hive_a = relational_storage.get_hive_agent("quantum-prime")
        self.assertIsNotNone(hive_a)
        self.assertEqual(hive_a["name"], "Quantum Prime")
        self.assertEqual(hive_a["assigned_node"], "node2_ally_extreme")

        sess = relational_storage.get_session("sess_quantum-prime")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["agent_id"], "quantum-prime")

        # Cleanup
        shutil.rmtree(dest_dir, ignore_errors=True)
        json_file = os.path.join(openclaw_engine.profiles_dir, "quantum-prime.json")
        if os.path.exists(json_file):
            os.remove(json_file)


if __name__ == "__main__":
    unittest.main()
