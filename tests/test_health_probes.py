"""Offline test for the health probe list: the voice server on the engine host is probed next to the legacy voice host (its fallback)."""

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


VOICE = {"voice": {"stt_url": "http://10.0.0.5:8210"}}


class TestVoiceProbes(unittest.TestCase):
    def test_the_voice_server_and_its_wyoming_ports_are_probed_once_configured(self):
        p = probes(VOICE)
        self.assertEqual(p["Voice server"][3], "http://10.0.0.5:8210/health")
        self.assertEqual(p["Wyoming STT"][3:], ("10.0.0.5", 10301))
        self.assertEqual(p["Wyoming TTS"][3:], ("10.0.0.5", 10201))

    def test_nothing_voice_is_probed_on_an_install_without_a_voice_server(self):
        p = probes()                                   # nothing configured: not "down", not probed (M6 dry run #9)
        for name in ("Voice server", "Wyoming STT", "Wyoming TTS", "Wake word"):
            self.assertNotIn(name, p)

    def test_the_wake_word_service_is_probed(self):
        self.assertEqual(probes(VOICE)["Wake word"][3:], ("10.0.0.5", 10400))

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
        self.assertEqual(p["Voice server"][3], "http://10.9.8.7:9000/health")
        self.assertEqual(p["Wyoming STT"][3], "10.9.8.7")
        self.assertEqual(p["Wake word"][3], "10.9.8.7")

    def test_optional_engines_and_services_are_probed_only_when_named(self):
        self.assertEqual(set(probes()), {"Coordinator"})
        named = probes({"cluster": {"worker_url": "http://10.0.0.5:8002/v1", "qdrant_url": "http://10.0.0.6:6333"},
                        "couchdb": {"username": "u"}})
        self.assertEqual(set(named), {"Coordinator", "Worker", "Qdrant", "CouchDB"})


if __name__ == "__main__":
    unittest.main()
