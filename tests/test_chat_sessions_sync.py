"""
Unit tests for StoneSage Cross-Device Chat Session Sync & Trajectory Persistence.
Validates session lifecycle, reasoning trace recovery, multimodal payload handling,
and cross-device message fetching.
"""

import os
import time
import shutil
import tempfile
import unittest
from harness.data_fabric.pg_storage import RelationalStorage

class TestChatSessionsSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_harness.db")
        self.storage = RelationalStorage(db_path=self.db_path)

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_lifecycle(self):
        """Test creating, fetching, listing, and deleting chat sessions."""
        session_id = "test_sess_001"
        created = self.storage.create_chat_session(
            session_id=session_id,
            title="Refactor Vulkan Kernel",
            workspace_path="/opt/cluster-bridge/vulkan",
            node_id="vm102",
            permissions={"autonomy_level": "autonomous", "can_write": True}
        )
        self.assertEqual(created["session_id"], session_id)
        self.assertEqual(created["title"], "Refactor Vulkan Kernel")
        self.assertEqual(created["permissions"]["autonomy_level"], "autonomous")

        # Fetch session
        fetched = self.storage.get_session(session_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["workspace_path"], "/opt/cluster-bridge/vulkan")
        self.assertEqual(fetched["title"], "Refactor Vulkan Kernel")

        # List sessions
        sessions = self.storage.list_sessions()
        self.assertTrue(any(s["session_id"] == session_id for s in sessions))

        # Delete session
        deleted = self.storage.delete_session(session_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.storage.get_session(session_id))

    def test_cross_device_messages_and_reasoning_recovery(self):
        """Simulate Client A (Desktop) saving conversation and Client B (Phone) syncing."""
        session_id = "cross_device_sess"
        self.storage.create_chat_session(
            session_id=session_id,
            title="Cross Device Sync Test",
            workspace_path="/workspace/test"
        )

        # Step 1: User message with base64 image attachment
        step1 = self.storage.save_message(
            session_id=session_id,
            role="user",
            content="Analyze this diagram and write a shader.",
            images=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="]
        )
        self.assertEqual(step1, 0)

        # Step 2: Assistant streaming response with chain-of-thought reasoning and metrics
        thought_trace = "Deconstructing shader logic into compute passes. Step 1: memory barriers..."
        response_code = "```hlsl\n[numthreads(64,1,1)] void CSMain() {}\n```"
        metrics = {"ttft_ms": 142, "tps": 78, "total_tokens": 512, "model": "coordinator"}

        step2 = self.storage.save_message(
            session_id=session_id,
            role="assistant",
            content=response_code,
            thought=thought_trace,
            metrics=metrics
        )
        self.assertEqual(step2, 1)

        # Simulate Client B (Mobile or ROG Ally X) retrieving conversation history
        client_b_storage = RelationalStorage(db_path=self.db_path)
        synced_messages = client_b_storage.get_session_messages(session_id)
        client_b_storage.close()

        self.assertEqual(len(synced_messages), 2)
        
        # User message check
        u_msg = synced_messages[0]
        self.assertEqual(u_msg["role"], "user")
        self.assertIn("Analyze this diagram", u_msg["content"])
        self.assertEqual(len(u_msg["images"]), 1)
        self.assertTrue(u_msg["images"][0].startswith("data:image/png;base64,"))

        # Assistant message check
        a_msg = synced_messages[1]
        self.assertEqual(a_msg["role"], "assistant")
        self.assertEqual(a_msg["content"], response_code)
        self.assertEqual(a_msg["thought"], thought_trace)
        self.assertEqual(a_msg["metrics"]["tps"], 78)
        self.assertEqual(a_msg["metrics"]["model"], "coordinator")

    def test_auto_session_registration_on_message(self):
        """Verify save_message on an unregistered session automatically creates session row."""
        unregistered_sid = "sess_ad_hoc_999"
        self.storage.save_message(
            session_id=unregistered_sid,
            role="user",
            content="Direct inquiry without prior session creation"
        )
        session_meta = self.storage.get_session(unregistered_sid)
        self.assertIsNotNone(session_meta)
        self.assertEqual(session_meta["session_id"], unregistered_sid)

        sessions = self.storage.list_sessions()
        self.assertTrue(any(s["session_id"] == unregistered_sid for s in sessions))

if __name__ == "__main__":
    unittest.main()
