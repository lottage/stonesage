"""Runs the headless JavaScript checks for conversation mode (end-of-speech detection, filler) and cross-checks the page's filler
lines against the lines the server pre-makes. Skipped when node is not installed."""

import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from stonesage.courage.agent import CANNED_LINES  # noqa: E402

CHAT_JS = os.path.join(ROOT, "StoneSage", "frontend", "js", "chat.js")
CONVO_AUDIO_JS = os.path.join(ROOT, "StoneSage", "frontend", "js", "convo_audio.js")


class TestConversationModeJs(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_headless_checks_pass(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "voice_convo.test.mjs"), CHAT_JS, CONVO_AUDIO_JS],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_barge_in_module_checks_pass(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "convo_audio.test.mjs"), CONVO_AUDIO_JS, CHAT_JS],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_filler_line_is_pre_made_by_the_server(self):
        src = open(CHAT_JS, encoding="utf-8").read()
        block = re.search(r"const SLOW_TOOL_FILLER = \{(.*?)\};", src, re.S).group(1)
        lines = re.findall(r":\s*'([^']+)'", block)
        self.assertTrue(lines, "no filler lines found in chat.js")
        for line in lines:
            self.assertIn(line, CANNED_LINES, f"'{line}' is spoken by the page but never pre-made")


if __name__ == "__main__":
    unittest.main()
