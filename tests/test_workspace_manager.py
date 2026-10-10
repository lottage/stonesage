"""
Unit tests for StoneSage Workspace Management, Real-time Directory Scanning,
Custom Roots Persistence, and Sandbox Security Boundaries.
"""

import os
import json
import shutil
import tempfile
import unittest
from harness.core.openclaw_engine import WorkspacePermissionBoundary

# Import workspace root helpers from server
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "StoneSage", "backend")))
from stonesage.server import get_workspace_roots, save_workspace_roots, list_workspace_directories

class TestWorkspaceManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.root_a = os.path.join(self.temp_dir, "projects_root")
        self.root_b = os.path.join(self.temp_dir, "secondary_root")
        os.makedirs(self.root_a, exist_ok=True)
        os.makedirs(self.root_b, exist_ok=True)

        # Create a mock config file in temp dir
        self.orig_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_custom_roots_persistence(self):
        """Test saving and retrieving user-customizable workspace roots."""
        custom_roots = [
            {"id": "root_1", "label": "Main Projects", "path": self.root_a, "node_id": "local"},
            {"id": "root_2", "label": "NAS Storage", "path": self.root_b, "node_id": "node2"}
        ]
        save_workspace_roots(custom_roots)

        loaded = get_workspace_roots()
        self.assertGreaterEqual(len(loaded), 2)
        paths = [r["path"] for r in loaded]
        self.assertIn(self.root_a, paths)
        self.assertIn(self.root_b, paths)

    def test_workspace_scanning_and_project_creation(self):
        """Test scanning directories under roots and recognizing project metadata."""
        custom_roots = [
            {"id": "root_test", "label": "Test Root", "path": self.root_a, "node_id": "local"}
        ]
        save_workspace_roots(custom_roots)

        # Create a project folder with .stonesage-project.json
        proj_dir = os.path.join(self.root_a, "autonomous-agent")
        os.makedirs(proj_dir, exist_ok=True)
        meta_file = os.path.join(proj_dir, ".stonesage-project.json")
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump({
                "name": "autonomous-agent",
                "autonomy_level": "autonomous",
                "created_at": 1700000000
            }, f)

        # Create a regular directory without project metadata
        plain_dir = os.path.join(self.root_a, "plain-subfolder")
        os.makedirs(plain_dir, exist_ok=True)

        workspaces = list_workspace_directories()
        ws_names = [w["name"] for w in workspaces]
        self.assertIn("autonomous-agent", ws_names)
        self.assertIn("plain-subfolder", ws_names)

        # Verify metadata extraction
        agent_ws = next(w for w in workspaces if w["name"] == "autonomous-agent")
        self.assertTrue(agent_ws["has_project_meta"])
        self.assertEqual(agent_ws["meta"].get("autonomy_level"), "autonomous")

    def test_sandbox_security_boundary(self):
        """Verify WorkspacePermissionBoundary prevents directory traversal and enforces permissions."""
        boundary = WorkspacePermissionBoundary(
            root_path=self.root_a,
            autonomy_level="tiered"
        )

        # Valid subpath should pass
        subpath = os.path.join(self.root_a, "src", "main.py")
        self.assertTrue(boundary.validate_path(subpath))

        # Path traversal attack (..) should fail
        escape_path = os.path.join(self.root_a, "..", "..", "Windows", "System32")
        self.assertFalse(boundary.validate_path(escape_path))

        # Check permissions in tiered mode
        self.assertTrue(boundary.check_tool_permission("read_file"))
        self.assertFalse(boundary.check_tool_permission("run_command"))  # requires approval in tiered

        # Check permissions in read_only mode
        ro_boundary = WorkspacePermissionBoundary(root_path=self.root_a, autonomy_level="read_only")
        self.assertTrue(ro_boundary.check_tool_permission("read_file"))
        self.assertFalse(ro_boundary.check_tool_permission("write_file"))

        # Check permissions in autonomous mode
        auto_boundary = WorkspacePermissionBoundary(root_path=self.root_a, autonomy_level="autonomous")
        self.assertTrue(auto_boundary.check_tool_permission("write_file"))
        self.assertTrue(auto_boundary.check_tool_permission("run_command"))

if __name__ == "__main__":
    unittest.main()
