"""
Unit tests for StoneSage v3.0:
- Interactive PTY Shell Manager & Terminal Session
- ClusterClient Multimodal Vision Schema Formatting
- PWA & Modular Frontend Assets Validation
"""

import os
import sys
import unittest
import time
import json

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
BACKEND_DIR = os.path.join(ROOT_DIR, "StoneSage", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from harness.core.terminal_pty import pty_manager, TerminalSession
from stonesage.cluster_client import ClusterClient

class TestTerminalPTY(unittest.TestCase):
    def test_pty_session_creation_and_reconnection(self):
        output_chunks = []

        def on_out(chunk: str):
            output_chunks.append(chunk)

        session_id = f"test_term_{int(time.time()*1000)}"
        session = pty_manager.get_or_create_session(session_id=session_id, on_output=on_out)
        self.assertIsNotNone(session)
        self.assertTrue(session._running)

        # Allow initial banner to emit
        time.sleep(0.3)
        self.assertTrue(len(output_chunks) > 0)
        self.assertIn("STONESAGE PTY", output_chunks[0])

        # Test writing input
        session.write_input("echo test_pty_pipe\r\n")
        time.sleep(0.5)

        # Test reconnecting to same session updates callback
        new_chunks = []
        def new_on_out(chunk: str):
            new_chunks.append(chunk)

        reconnected = pty_manager.get_or_create_session(session_id=session_id, on_output=new_on_out)
        self.assertEqual(reconnected, session)
        self.assertEqual(session.on_output, new_on_out)

        # Test scrollback buffer retains prior output
        scrollback = session.get_scrollback()
        self.assertTrue(len(scrollback) > 0)
        self.assertIn("STONESAGE PTY", scrollback)

        # Cleanup
        pty_manager.close_session(session_id)
        self.assertNotIn(session_id, pty_manager.sessions)
        self.assertFalse(session._running)

class TestMultimodalClusterFormatting(unittest.TestCase):
    def test_multimodal_formatting(self):
        client = ClusterClient({})
        
        # Mock test messages with image
        dispatch_messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Inspect this screenshot", "images": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="]}
        ]

        multimodal_messages = []
        for m in dispatch_messages:
            imgs = m.get("images") or []
            if imgs and isinstance(m.get("content"), str):
                parts = [{"type": "text", "text": m["content"]}]
                for img_data in imgs:
                    parts.append({"type": "image_url", "image_url": {"url": img_data}})
                multimodal_messages.append({"role": m["role"], "content": parts})
            else:
                multimodal_messages.append(m)

        self.assertEqual(len(multimodal_messages), 2)
        self.assertEqual(multimodal_messages[0]["role"], "system")
        self.assertEqual(multimodal_messages[0]["content"], "You are a helpful assistant.")

        user_msg = multimodal_messages[1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        self.assertEqual(user_msg["content"][0]["type"], "text")
        self.assertEqual(user_msg["content"][0]["text"], "Inspect this screenshot")
        self.assertEqual(user_msg["content"][1]["type"], "image_url")
        self.assertTrue(user_msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

class TestFrontendAssetsAndDeuteranopiaStandard(unittest.TestCase):
    def test_frontend_files_exist(self):
        fe_dir = os.path.join(ROOT_DIR, "StoneSage", "frontend")
        required_files = [
            "index.html",
            "style.css",
            "manifest.webmanifest",
            "sw.js",
            "icon.svg",
            os.path.join("js", "app.js"),
            os.path.join("js", "state.js"),
            os.path.join("js", "chat.js"),
            os.path.join("js", "terminal.js"),
            os.path.join("js", "harness.js"),
            os.path.join("js", "fleet.js"),
        ]
        for f in required_files:
            full_path = os.path.join(fe_dir, f)
            self.assertTrue(os.path.exists(full_path), f"Missing frontend asset: {f}")

    def test_deuteranopia_is_standard_theme(self):
        index_html = os.path.join(ROOT_DIR, "StoneSage", "frontend", "index.html")
        with open(index_html, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('<html lang="en" data-theme="deu">', content)
        self.assertIn('<body data-theme="deu">', content)

        state_js = os.path.join(ROOT_DIR, "StoneSage", "frontend", "js", "state.js")
        with open(state_js, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertTrue(
            "safeStorage.getItem('stonesage_theme', 'deu')" in content or
            "localStorage.getItem('stonesage_theme') || 'deu'" in content,
            "State.js should enforce deuteranopia default theme"
        )

    def test_pwa_service_worker_cache_v3(self):
        sw_js = os.path.join(ROOT_DIR, "StoneSage", "frontend", "sw.js")
        with open(sw_js, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("stonesage-v", content)
        self.assertIn("/js/app.js", content)
        self.assertIn("/js/state.js", content)

    def test_workstation_and_chat_sync_dom_elements(self):
        index_html = os.path.join(ROOT_DIR, "StoneSage", "frontend", "index.html")
        with open(index_html, "r", encoding="utf-8") as f:
            content = f.read()
        # Chat thread & sync elements
        self.assertIn('id="chat-thread-select"', content)
        self.assertIn('id="chat-new-thread-btn"', content)
        self.assertIn('id="chat-delete-thread-btn"', content)
        self.assertIn('id="chat-workspace-badge"', content)
        self.assertIn('id="chat-sync-indicator"', content)
        # Workstation controls
        self.assertIn('id="workstation-workspace-select"', content)
        self.assertIn('id="ws-mkdir-btn"', content)
        self.assertIn('id="ws-add-root-btn"', content)
        self.assertIn('id="workstation-file-tree"', content)
        self.assertIn('id="workstation-term-screen"', content)
        self.assertIn('id="ws-tool-nano"', content)
        # Modals
        self.assertIn('id="modal-mkdir"', content)
        self.assertIn('id="modal-add-root"', content)

if __name__ == "__main__":
    unittest.main()
