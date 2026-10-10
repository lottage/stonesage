#!/usr/bin/env python3
"""
StoneSage Reasoning Loop Watchdog & Automated Agent Nudge Module
Detects autoregressive repetition loops in streaming reasoning traces (<think> blocks),
tracks intercept telemetry, and triggers MCP / cluster agent nudges.
Zero-dependency: 100% Python 3 standard library.
"""

import re
import json
from lodestone import endpoints
import time
from datetime import datetime, timezone
import urllib.request
from typing import List, Optional, Tuple, Dict, Any

class ReasoningLoopDetector:
    def __init__(self, min_repeats: int = 3, max_ngram_size: int = 64):
        self.min_repeats = min_repeats
        self.max_ngram_size = max_ngram_size
        self.token_history: List[str] = []
        self.inside_think = False

    def reset(self):
        self.token_history.clear()
        self.inside_think = False

    def ingest_chunk(self, chunk: str) -> Tuple[bool, Optional[str]]:
        """
        Ingests a streaming chunk.
        Returns (loop_detected, repeated_pattern).
        """
        if "<think>" in chunk:
            self.inside_think = True
        if "</think>" in chunk:
            self.inside_think = False

        # Extract words and tokens
        tokens = re.findall(r"\b\w+\b|[^\w\s]", chunk)
        for tok in tokens:
            self.token_history.append(tok)
            # Bound sliding history window
            if len(self.token_history) > 600:
                self.token_history.pop(0)

            hist_len = len(self.token_history)
            # Check suffix repeats of length L from 1 up to max_ngram_size
            for L in range(1, min(self.max_ngram_size + 1, hist_len // 2 + 1)):
                pattern = self.token_history[-L:]
                # For long patterns (>= 8 tokens), 2 consecutive occurrences indicate a runaway loop
                required_repeats = 2 if L >= 8 else self.min_repeats
                if hist_len < required_repeats * L:
                    continue
                is_loop = True
                for rep in range(1, required_repeats):
                    start = hist_len - (rep + 1) * L
                    end = hist_len - rep * L
                    if self.token_history[start:end] != pattern:
                        is_loop = False
                        break

                if is_loop:
                    # Ignore pure punctuation, markdown fences (```), numbers or dividers
                    punct_chars = set(r",.-*`_=/\:;~|#! ()[]{}<>\"'")
                    has_real_word = any(tok.isalpha() and len(tok) > 2 for tok in pattern)
                    if not has_real_word:
                        continue
                    repeated_str = " ".join(pattern)
                    return True, repeated_str

        return False, None


class WatchdogManager:
    def __init__(self, mcp_url: str = ""):
        self.mcp_url = mcp_url or endpoints.url("mcp")
        self.armed = True
        self.intercept_count = 0
        self.last_intercept: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []

    def record_intercept(self, phrase: str, model: str = "coordinator", agent_id: str = "coordinator") -> Dict[str, Any]:
        self.intercept_count += 1
        event = {
            "id": f"intercept-{int(time.time()*1000)}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phrase": phrase,
            "model": model,
            "agent_id": agent_id,
            "count": self.intercept_count
        }
        self.last_intercept = event
        self.history.append(event)
        if len(self.history) > 30:
            self.history.pop(0)

        # Dispatch nudge directive via cluster MCP
        directive = (
            f"⚡ SYSTEM REASONING NUDGE: Repetition loop detected on '{phrase}'. "
            "Discard this circular thought path immediately. "
            "Reset your internal workspace and formulate a fresh, non-circular proof from first principles."
        )
        self.issue_mcp_nudge(agent_id=agent_id, directive=directive)
        return event

    def issue_mcp_nudge(self, agent_id: str, directive: str) -> bool:
        """Invokes the native nudge_agent tool via cluster bridge MCP JSON-RPC."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "nudge_agent",
                    "arguments": {
                        "agent_id": agent_id,
                        "directive": directive
                    }
                },
                "id": f"nudge-{int(time.time()*1000)}"
            }
            req = urllib.request.Request(
                f"{self.mcp_url}/jsonrpc",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return "result" in data
        except Exception:
            try:
                rest_url = f"{self.mcp_url}/api/nudge"
                rest_payload = json.dumps({"agent_id": agent_id, "directive": directive}).encode("utf-8")
                req = urllib.request.Request(rest_url, data=rest_payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    return resp.status == 200
            except Exception:
                pass
            return False

    def reset(self):
        self.intercept_count = 0
        self.last_intercept = None
        self.history.clear()

    def get_status(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "armed": self.armed,
            "intercept_count": self.intercept_count,
            "last_intercept": self.last_intercept,
            "history": self.history[-10:]
        }

# Global singleton instance for the StoneSage daemon
GLOBAL_WATCHDOG = WatchdogManager()
