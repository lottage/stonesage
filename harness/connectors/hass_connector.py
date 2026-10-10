"""
Home Assistant Connector (VM 103 :8123).
Provides state queries and service invocations for smart home entities.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.HassConnector")

def _discover_hass_token() -> str:
    tok = os.environ.get("HASS_TOKEN", "")
    if tok:
        return tok
    candidate_paths = [
        os.path.join(os.path.dirname(__file__), "..", "..", "StoneSage", "backend", "config.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", "server setup", "cluster-bridge", "config.json"),
        "/opt/stonesage/backend/config.json",
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8-sig") as f:
                    cfg = json.load(f)
                    t = cfg.get("homeassistant", {}).get("token") or cfg.get("hass_token")
                    if t:
                        return t
            except Exception:
                pass
    # No hardcoded fallback: set HASS_TOKEN or homeassistant.token in StoneSage/backend/config.json
    return os.environ.get("HASS_TOKEN", "")

class HassConnector:
    def __init__(self, base_url: str = fleet_config.hass_url, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token or _discover_hass_token()

    def get_state(self, entity_id: str) -> Dict[str, Any]:
        """Fetches the current state and attributes of an entity."""
        url = f"{self.base_url}/api/states/{entity_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Harness-Hass"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to fetch state for {entity_id}: {e}")
            return {"entity_id": entity_id, "state": "unavailable", "error": str(e)}

    def get_all_states(self) -> List[Dict[str, Any]]:
        """Fetches all entity states in a single batch request."""
        url = f"{self.base_url}/api/states"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Harness-Hass"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to fetch all states: {e}")
            return []

    def call_service(self, domain: str, service: str, service_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calls a Home Assistant service."""
        url = f"{self.base_url}/api/services/{domain}/{service}"
        req = urllib.request.Request(
            url,
            data=json.dumps(service_data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Harness-Hass"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                return {"ok": True, "status": resp.status}
        except Exception as e:
            logger.error(f"Failed to call service {domain}.{service}: {e}")
            return {"ok": False, "error": str(e)}

    def dispatch_quick_intent(self, raw_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Sub-50ms Multi-Pass Reflex Dispatcher.
        Passes the natural language input through System 1 (Pass 0 -> Pass 1 -> Pass 2).
        If resolved with high confidence, invokes call_service() immediately.
        Otherwise returns escalate_to_system2=True for downstream LLM routing.
        """
        try:
            from ..data_fabric.system1_reflex import system1_reflex
            decision = system1_reflex.evaluate(raw_input, context=context)
            if decision.matched and decision.domain and decision.service:
                svc_res = self.call_service(decision.domain, decision.service, decision.service_data)
                return {
                    "ok": svc_res.get("ok", False),
                    "status": "executed_reflex",
                    "tier": decision.tier,
                    "confidence": decision.confidence,
                    "latency_ms": decision.latency_ms,
                    "domain": decision.domain,
                    "service": decision.service,
                    "service_data": decision.service_data,
                    "intent_label": decision.intent_label,
                    "escalate_to_system2": False,
                    "details": decision.details
                }
            return {
                "ok": False,
                "status": "escalate_to_system2",
                "tier": decision.tier,
                "confidence": decision.confidence,
                "latency_ms": decision.latency_ms,
                "escalate_to_system2": True,
                "details": decision.details
            }
        except Exception as e:
            logger.warning(f"Reflex intent dispatch error: {e}")
            return {
                "ok": False,
                "status": "escalate_to_system2",
                "error": str(e),
                "escalate_to_system2": True
            }

hass_connector = HassConnector()


