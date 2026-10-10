"""Offline test for the health probe list: the voice server on VM 102 is probed next to the old voice LXC (its fallback)."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from stonesage import health  # noqa: E402
from lodestone import endpoints  # noqa: E402
from unittest import mock  # noqa: E402

SERVICE_ENV = {env for _, env, _ in endpoints.SERVICES.values()}


def probes(cfg=None):
    """The probe list with only `cfg` to go on (the test runner's service env vars removed: they point at TEST-NET)."""
    with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in SERVICE_ENV}, clear=True):
        return {p[1]: p for p in health.build_probes(cfg or {})}


class TestVoiceProbes(unittest.TestCase):
    def test_the_voice_server_and_its_wyoming_ports_are_probed(self):
        p = probes()                                   # nothing configured: one box, everything on localhost
        self.assertEqual(p["Voice server (VM 102)"][3], "http://127.0.0.1:8210/health")
        self.assertEqual(p["Wyoming STT (VM 102)"][3:], ("127.0.0.1", 10301))
        self.assertEqual(p["Wyoming TTS (VM 102)"][3:], ("127.0.0.1", 10201))

    def test_the_wake_word_service_is_probed(self):
        self.assertEqual(probes()["Wake word (VM 102)"][3:], ("127.0.0.1", 10400))

    def test_the_old_voice_lxc_is_probed_only_when_configured(self):
        old = ("Whisper STT", "Kokoro TTS", "Wyoming Whisper", "Wyoming Piper")
        p = probes({"voice": {"host": "10.0.0.7"}})
        for name in old:
            self.assertEqual(p[name][3], "10.0.0.7")
        p = probes()                                   # not configured: no probe of "" (which would mean this machine)
        for name in old:
            self.assertNotIn(name, p)

    def test_proxmox_probe_only_when_configured(self):
        self.assertNotIn("Proxmox API", probes())
        self.assertEqual(probes({"proxmox": {"host": "10.0.0.2"}})["Proxmox API"][3:], ("10.0.0.2", 8006))

    def test_the_configured_voice_server_url_is_followed(self):
        p = probes({"voice": {"stt_url": "http://10.9.8.7:9000/"}})
        self.assertEqual(p["Voice server (VM 102)"][3], "http://10.9.8.7:9000/health")
        self.assertEqual(p["Wyoming STT (VM 102)"][3], "10.9.8.7")
        self.assertEqual(p["Wake word (VM 102)"][3], "10.9.8.7")


if __name__ == "__main__":
    unittest.main()
