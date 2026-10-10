"""
Voice Services Connector (LXC 121).
Bridges Faster Whisper STT (:8200) and Kokoro TTS (:8300) for ambient audio streaming.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.VoiceConnector")

class VoiceConnector:
    def __init__(
        self,
        whisper_url: str = fleet_config.voice_whisper_url,
        kokoro_url: str = fleet_config.voice_kokoro_url,
        kokoro_fallback_url: str = fleet_config.voice_kokoro_fallback_url
    ):
        self.whisper_url = whisper_url.rstrip("/")
        self.kokoro_url = kokoro_url.rstrip("/")
        self.kokoro_fallback_url = (kokoro_fallback_url or "").rstrip("/")

    def synthesize_speech(self, text: str, voice: str = "am_adam", speed: float = 1.0) -> Optional[bytes]:
        """Generates audio WAV stream from Kokoro: the voice server first, the old voice LXC if it does not answer."""
        payload = {
            "model": "kokoro",
            "input": text[:800],
            "voice": voice,
            "speed": speed,
            "response_format": "wav"
        }
        for base in (self.kokoro_url, self.kokoro_fallback_url):
            if not base:
                continue
            url = f"{base}/v1/audio/speech"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Harness-Voice"},
                method="POST"
            )
            # Kokoro on the old CPU LXC needs ~40 ms per character (330 chars: 12-13 s): a flat 10 s failed long replies
            try:
                with urllib.request.urlopen(req, timeout=max(10.0, 0.08 * len(payload["input"]))) as resp:
                    return resp.read()
            except Exception as e:
                logger.warning(f"Speech synthesis error on {url}: {e}")
        return None

voice_connector = VoiceConnector()
