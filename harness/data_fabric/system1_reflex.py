"""
Multi-Pass System 1 Reflex Decision Engine.
Implements a fast cascading fallback pipeline:
  - Pass 0: In-RAM Valkey Reflex Hash/Tag Cache (< 1 ms)
  - Pass 1: BGE-Large + Qdrant Semantic Vector Prototype Matching (15 - 25 ms)
  - Pass 2: 1-Pass Prefill Logprob Head on local llama-server worker :8002 (35 - 55 ms)
  - Pass 3: Escalate to System 2 Full Reasoning (coordinator engine / cloud)

Provides sub-50ms decision reflexes, eliminating LLM token latency and JSON formatting errors.
"""

import time
import json
import uuid
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, field

from ..config import fleet_config
from .valkey_amem import ValkeyAMEM
from .qdrant_brain import QdrantBrain

logger = logging.getLogger("Harness.System1Reflex")

@dataclass
class System1Decision:
    tier: str  # 'pass0_valkey', 'pass1_qdrant', 'pass2_logit', 'pass3_escalate'
    matched: bool
    confidence: float
    domain: Optional[str] = None
    service: Optional[str] = None
    service_data: Dict[str, Any] = field(default_factory=dict)
    intent_label: Optional[str] = None
    raw_input: str = ""
    latency_ms: float = 0.0
    escalate_to_system2: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

class System1ReflexEngine:
    def __init__(
        self,
        valkey_amem: Optional[ValkeyAMEM] = None,
        qdrant_brain: Optional[QdrantBrain] = None,
        worker_base_url: Optional[str] = None,
        collection_name: str = "home_automation_registry"
    ):
        self.amem = valkey_amem or ValkeyAMEM()
        self.qdrant = qdrant_brain or QdrantBrain()
        # Direct llama-server completion endpoint (:8002)
        raw_worker = worker_base_url or fleet_config.worker_url
        self.worker_server_url = raw_worker.replace("/v1", "").rstrip("/")
        self.collection_name = collection_name
        self._ensure_qdrant_collection()
        self.seed_default_prototypes()

    def _ensure_qdrant_collection(self):
        """Ensures the home automation prototype collection exists in Qdrant."""
        try:
            url = f"{self.qdrant.qdrant_url}/collections/{self.collection_name}"
            req = urllib.request.Request(url, headers={"User-Agent": "Harness-System1"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                pass
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Create collection with 1024-d Cosine vectors (BGE-Large)
                create_url = f"{self.qdrant.qdrant_url}/collections/{self.collection_name}"
                payload = {
                    "vectors": {
                        "size": 1024,
                        "distance": "Cosine"
                    }
                }
                creq = urllib.request.Request(
                    create_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT"
                )
                try:
                    with urllib.request.urlopen(creq, timeout=5.0) as resp:
                        logger.info(f"Created Qdrant collection '{self.collection_name}' for System-1 prototypes.")
                except Exception as ce:
                    logger.warning(f"Could not auto-create Qdrant collection '{self.collection_name}': {ce}")
        except Exception as e:
            logger.debug(f"Qdrant collection check note: {e}")

    def normalize_key(self, text: str) -> str:
        """Creates a standardized lowercase string key."""
        return " ".join(text.strip().lower().split())

    def evaluate(
        self,
        event_text: str,
        context: Optional[Dict[str, Any]] = None,
        confidence_threshold_pass1: float = 0.85,
        confidence_threshold_pass2: float = 0.75
    ) -> System1Decision:
        """
        Cascading execution through Pass 0 -> Pass 1 -> Pass 2 -> Fallback.
        """
        t0 = time.perf_counter()
        normalized = self.normalize_key(event_text)

        # -------------------------------------------------------------
        # PASS 0: In-RAM Valkey Reflex Hash/Tag Cache (< 1.0 ms)
        # -------------------------------------------------------------
        p0_start = time.perf_counter()
        p0_hit = self._pass0_valkey_lookup(normalized)
        if p0_hit and p0_hit.get("confidence", 0.0) >= 0.80:
            lat = (time.perf_counter() - t0) * 1000.0
            return System1Decision(
                tier="pass0_valkey",
                matched=True,
                confidence=p0_hit.get("confidence", 1.0),
                domain=p0_hit.get("domain"),
                service=p0_hit.get("service"),
                service_data=p0_hit.get("service_data", {}),
                intent_label=p0_hit.get("intent_label"),
                raw_input=event_text,
                latency_ms=round(lat, 2),
                escalate_to_system2=False,
                details={"p0_latency_ms": round((time.perf_counter() - p0_start) * 1000.0, 2)}
            )

        # -------------------------------------------------------------
        # PASS 1: Semantic Vector Prototype Matching via Qdrant (15 - 25 ms)
        # -------------------------------------------------------------
        p1_start = time.perf_counter()
        p1_hit = self._pass1_vector_lookup(event_text)
        if p1_hit and p1_hit.get("score", 0.0) >= confidence_threshold_pass1:
            lat = (time.perf_counter() - t0) * 1000.0
            # Auto-learn back to Valkey Pass 0 for sub-ms hits on exact future repetitions
            self._pass0_store_reflex(
                normalized,
                p1_hit["domain"],
                p1_hit["service"],
                p1_hit.get("service_data", {}),
                p1_hit.get("intent_label", "vector_prototype"),
                confidence=p1_hit["score"]
            )
            return System1Decision(
                tier="pass1_qdrant",
                matched=True,
                confidence=round(p1_hit["score"], 4),
                domain=p1_hit.get("domain"),
                service=p1_hit.get("service"),
                service_data=p1_hit.get("service_data", {}),
                intent_label=p1_hit.get("intent_label"),
                raw_input=event_text,
                latency_ms=round(lat, 2),
                escalate_to_system2=False,
                details={"prototype_score": p1_hit["score"], "p1_latency_ms": round((time.perf_counter() - p1_start) * 1000.0, 2)}
            )

        # -------------------------------------------------------------
        # PASS 2: Single-Token Prefill Logprob Head on :8002 (35 - 55 ms)
        # Evaluated when Pass 1 similarity is moderate/ambiguous (0.65 <= sim < 0.88)
        # -------------------------------------------------------------
        p2_start = time.perf_counter()
        candidate_prototypes = self._pass1_get_candidates(event_text, limit=4)
        if candidate_prototypes:
            p2_decision = self._pass2_logit_choice(event_text, candidate_prototypes)
            if p2_decision and p2_decision.get("confidence", 0.0) >= confidence_threshold_pass2:
                lat = (time.perf_counter() - t0) * 1000.0
                # Auto-learn to Valkey & reinforce Qdrant
                self._pass0_store_reflex(
                    normalized,
                    p2_decision["domain"],
                    p2_decision["service"],
                    p2_decision.get("service_data", {}),
                    p2_decision.get("intent_label", "logit_calibrated"),
                    confidence=p2_decision["confidence"]
                )
                return System1Decision(
                    tier="pass2_logit",
                    matched=True,
                    confidence=round(p2_decision["confidence"], 4),
                    domain=p2_decision.get("domain"),
                    service=p2_decision.get("service"),
                    service_data=p2_decision.get("service_data", {}),
                    intent_label=p2_decision.get("intent_label"),
                    raw_input=event_text,
                    latency_ms=round(lat, 2),
                    escalate_to_system2=False,
                    details={"p2_latency_ms": round((time.perf_counter() - p2_start) * 1000.0, 2)}
                )

        # -------------------------------------------------------------
        # PASS 3: Fallback / Escalate to System 2 Full Reasoning LLM
        # -------------------------------------------------------------
        lat = (time.perf_counter() - t0) * 1000.0
        return System1Decision(
            tier="pass3_escalate",
            matched=False,
            confidence=round(p1_hit.get("score", 0.0) if p1_hit else 0.0, 4),
            raw_input=event_text,
            latency_ms=round(lat, 2),
            escalate_to_system2=True,
            details={"reason": "Confidence below threshold across Pass 0, 1, and 2."}
        )

    # =================================================================
    # PASS 0 HELPERS: Valkey In-RAM Reflex Store
    # =================================================================
    def _pass0_valkey_lookup(self, normalized_text: str) -> Optional[Dict[str, Any]]:
        """Looks up an exact reflex entry from Valkey :6379."""
        if self.amem.r:
            try:
                raw = self.amem.r.get(f"system1:reflex:{normalized_text}")
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.debug(f"Valkey Pass 0 lookup note: {e}")
        # Local RAM fallback
        return self.amem._local_fallback_cards.get(f"system1:reflex:{normalized_text}")

    def _pass0_store_reflex(
        self,
        normalized_text: str,
        domain: str,
        service: str,
        service_data: Dict[str, Any],
        intent_label: str,
        confidence: float = 1.0
    ):
        """Stores a verified decision into Valkey with 24h expiration (or permanent)."""
        card = {
            "domain": domain,
            "service": service,
            "service_data": service_data,
            "intent_label": intent_label,
            "confidence": confidence,
            "stored_at": time.time()
        }
        key = f"system1:reflex:{normalized_text}"
        if self.amem.r:
            try:
                self.amem.r.setex(key, 86400, json.dumps(card))
            except Exception as e:
                logger.debug(f"Valkey Pass 0 store note: {e}")
        self.amem._local_fallback_cards[key] = card

    # =================================================================
    # PASS 1 HELPERS: Qdrant Vector Prototype Matching
    # =================================================================
    def _pass1_vector_lookup(self, text: str) -> Optional[Dict[str, Any]]:
        """Searches top-1 nearest prototype in Qdrant with BGE embedding."""
        results = self.qdrant.search_memory(
            collection_name=self.collection_name,
            query=text,
            limit=1,
            score_threshold=0.50
        )
        if results and len(results) > 0:
            top = results[0]
            payload = top.get("payload", {})
            return {
                "score": float(top.get("score", 0.0)),
                "domain": payload.get("domain"),
                "service": payload.get("service"),
                "service_data": payload.get("service_data", {}),
                "intent_label": payload.get("intent_label")
            }
        return None

    def _pass1_get_candidates(self, text: str, limit: int = 4) -> List[Dict[str, Any]]:
        """Retrieves top-N candidate prototypes for Pass 2 logit arbitration."""
        results = self.qdrant.search_memory(
            collection_name=self.collection_name,
            query=text,
            limit=limit,
            score_threshold=0.60
        )
        candidates = []
        for r in results:
            p = r.get("payload", {})
            candidates.append({
                "score": float(r.get("score", 0.0)),
                "domain": p.get("domain"),
                "service": p.get("service"),
                "service_data": p.get("service_data", {}),
                "intent_label": p.get("intent_label", f"{p.get('domain')}.{p.get('service')}")
            })
        return candidates

    # =================================================================
    # PASS 2 HELPERS: Single-Token Logit Prefill Head (Noul / Choice)
    # =================================================================
    def noul_check(self, context: str, question: str) -> Tuple[bool, float]:
        """
        Calibrated Boolean Check (P(true)).
        Sends context to :8002 with n_predict: 1 and extracts Softmax(Logit('Yes'), Logit('No')).
        Takes ~35-50ms (Zero Autoregressive Generation).
        """
        prompt = (
            f"Context: {context.strip()}\n"
            f"Question: {question.strip()}\n"
            f"Direct Answer (Yes or No):"
        )
        try:
            req_data = {
                "prompt": prompt,
                "n_predict": 1,
                "temperature": 0.0,
                "logprobs": True,
                "top_logprobs": 5,
                "stream": False
            }
            url = f"{self.worker_server_url}/completion"
            req = urllib.request.Request(
                url,
                data=json.dumps(req_data).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Harness-System1"}
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            probs = data.get("completion_probabilities", [{}])[0].get("probs", [])
            p_yes = sum(p["prob"] for p in probs if p["tok_str"].strip().lower() in ("yes", "true", "y"))
            p_no = sum(p["prob"] for p in probs if p["tok_str"].strip().lower() in ("no", "false", "n"))
            
            total = p_yes + p_no
            if total > 0.0:
                p_calibrated = p_yes / total
            else:
                p_calibrated = 0.5
            return (p_calibrated >= 0.5, round(p_calibrated, 4))
        except Exception as e:
            logger.warning(f"Pass 2 Noul check error: {e}")
            return (False, 0.0)

    def choice_check(self, context: str, options: Dict[str, str]) -> Tuple[str, float]:
        """
        Calibrated Categorical Decision.
        Maps options to letters (A, B, C, D) and extracts normalized 1-token logit probabilities.
        """
        letters = ["A", "B", "C", "D", "E", "F"]
        mapping = {}
        opts_formatted = []
        for i, (k, desc) in enumerate(options.items()):
            if i >= len(letters):
                break
            letter = letters[i]
            mapping[letter] = k
            opts_formatted.append(f"{letter}: {desc}")

        options_str = "\n".join(opts_formatted)
        prompt = (
            f"Context: {context.strip()}\n"
            f"Available Actions:\n{options_str}\n"
            f"Select Option Letter ({', '.join(mapping.keys())}):"
        )
        try:
            req_data = {
                "prompt": prompt,
                "n_predict": 1,
                "temperature": 0.0,
                "logprobs": True,
                "top_logprobs": len(mapping) + 2,
                "stream": False
            }
            url = f"{self.worker_server_url}/completion"
            req = urllib.request.Request(
                url,
                data=json.dumps(req_data).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Harness-System1"}
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            probs = data.get("completion_probabilities", [{}])[0].get("probs", [])
            scores = {}
            for p in probs:
                tok = p["tok_str"].strip().upper()
                if tok in mapping:
                    scores[mapping[tok]] = scores.get(mapping[tok], 0.0) + p["prob"]

            if scores:
                best_opt = max(scores, key=scores.get)
                tot = sum(scores.values())
                calibrated_conf = scores[best_opt] / tot if tot > 0 else 0.0
                return (best_opt, round(calibrated_conf, 4))
            
            first_key = list(options.keys())[0]
            return (first_key, 0.5)
        except Exception as e:
            logger.warning(f"Pass 2 Choice check error: {e}")
            return (list(options.keys())[0], 0.0)

    def _pass2_logit_choice(self, text: str, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Arbitrates between top vector candidates using 1-token logprobs."""
        opts = {}
        for i, c in enumerate(candidates):
            opts[str(i)] = f"{c.get('intent_label')} -> {c.get('domain')}.{c.get('service')}"

        chosen_idx_str, conf = self.choice_check(text, opts)
        try:
            idx = int(chosen_idx_str)
            if 0 <= idx < len(candidates):
                winner = dict(candidates[idx])
                winner["confidence"] = conf
                return winner
        except Exception:
            pass
        return None

    # =================================================================
    # SEEDING DEFAULT PROTOTYPES (Instant Homelab Intelligence)
    # =================================================================
    def seed_default_prototypes(self):
        """Seeds standard homelab intents into Qdrant & Valkey."""
        prototypes = [
            # Office & Desk Lights
            {
                "phrase": "turn off office light",
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": "light.office_lights"},
                "intent_label": "office_lights_off"
            },
            {
                "phrase": "turn on office light",
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": "light.office_lights", "brightness": 255},
                "intent_label": "office_lights_on"
            },
            {
                "phrase": "dim office lights",
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": "light.office_lights", "brightness": 64},
                "intent_label": "office_lights_dim"
            },
            # Living Room & Night Mode
            {
                "phrase": "living room lights off",
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": "light.living_room_lights"},
                "intent_label": "living_room_off"
            },
            {
                "phrase": "night mode bedtime activate sleep",
                "domain": "scene",
                "service": "turn_on",
                "service_data": {"entity_id": "scene.night_mode"},
                "intent_label": "activate_night_mode"
            },
            {
                "phrase": "nightlight pathway on",
                "domain": "light",
                "service": "turn_on",
                "service_data": {"entity_id": "light.hallway_nightlight", "brightness": 20},
                "intent_label": "nightlight_on"
            },
            # HVAC & Thermostat
            {
                "phrase": "set temperature to 70 degrees",
                "domain": "climate",
                "service": "set_temperature",
                "service_data": {"entity_id": "climate.nest_thermostat", "temperature": 70},
                "intent_label": "thermostat_set_70"
            },
            {
                "phrase": "make it cooler thermostat down",
                "domain": "climate",
                "service": "set_temperature",
                "service_data": {"entity_id": "climate.nest_thermostat", "temperature": 68},
                "intent_label": "thermostat_cool"
            },
            # Smart Plugs & Appliances
            {
                "phrase": "check dryer status power",
                "domain": "sensor",
                "service": "get_state",
                "service_data": {"entity_id": "sensor.lg_smart_dryer_power"},
                "intent_label": "dryer_status"
            }
        ]

        # Seed into Valkey (Pass 0) and Qdrant (Pass 1)
        for p in prototypes:
            norm = self.normalize_key(p["phrase"])
            # Valkey exact cache
            self._pass0_store_reflex(
                norm,
                p["domain"],
                p["service"],
                p["service_data"],
                p["intent_label"],
                confidence=1.0
            )
            # Qdrant vector prototype with valid UUID
            try:
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, norm))
                vec = self.qdrant.get_embedding(p["phrase"])
                self.qdrant.upsert_point(
                    collection_name=self.collection_name,
                    point_id=point_id,
                    vector=vec,
                    payload={
                        "phrase": p["phrase"],
                        "domain": p["domain"],
                        "service": p["service"],
                        "service_data": p["service_data"],
                        "intent_label": p["intent_label"]
                    }
                )
            except Exception as e:
                logger.debug(f"Prototype vector indexing note for '{p['phrase']}': {e}")

system1_reflex = System1ReflexEngine()
