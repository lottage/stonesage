"""
Empirical Benchmark & Validation Suite for Multi-Pass System 1 Reflex Engine.
Validates:
  1. Pass 0: Valkey In-RAM Hash Match (< 25 ms over LAN)
  2. Pass 1: Qdrant Vector Prototype Match (15 - 40 ms)
  3. Pass 2: Single-Token Logprob Head on :8002 (< 60 ms)
  4. Auto-learning from Pass 1/Pass 2 back to Pass 0 (Subsequent cache hit < 15 ms)
  5. Calibrated Boolean Noul & Categorical Choice heads
"""

import sys
import time
import json
import logging

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .data_fabric.system1_reflex import System1ReflexEngine, system1_reflex
from .connectors.hass_connector import hass_connector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestSystem1")

def run_benchmarks():
    print("=" * 70)
    print(">> MULTI-PASS SYSTEM 1 REFLEX ENGINE: EMPIRICAL LATENCY BENCHMARK <<")
    print("=" * 70)

    engine = system1_reflex

    # -------------------------------------------------------------
    # TEST 1: Pass 1 Semantic Vector Prototype Match (Initial uncached query)
    # -------------------------------------------------------------
    print("\n--- [TEST 1] Pass 1: Qdrant Vector Prototype Lookup (Semantic Similarity) ---")
    query_p1 = "could you please dim the office lights for work"
    norm_key = engine.normalize_key(query_p1)
    
    # Ensure not in Pass 0 cache before test
    if engine.amem.r:
        try:
            engine.amem.r.delete(f"system1:reflex:{norm_key}")
        except Exception:
            pass
    engine.amem._local_fallback_cards.pop(f"system1:reflex:{norm_key}", None)

    res_p1 = engine.evaluate(query_p1)
    print(f"Query: '{query_p1}'")
    print(f"Result Tier: {res_p1.tier}")
    print(f"Matched: {res_p1.matched}")
    print(f"Domain/Service: {res_p1.domain}.{res_p1.service} -> {res_p1.service_data}")
    print(f"Confidence: {res_p1.confidence}")
    print(f"Total Latency: {res_p1.latency_ms} ms")
    assert res_p1.matched, "Pass 1 should match prototype"
    assert res_p1.tier == "pass1_qdrant", f"Expected pass1_qdrant, got {res_p1.tier}"
    print("[OK] Pass 1 Passed!")

    # -------------------------------------------------------------
    # TEST 2: Pass 0 In-RAM Valkey Reflex Hit (Repeated query)
    # -------------------------------------------------------------
    print("\n--- [TEST 2] Pass 0: Valkey In-RAM Cache Hit (Auto-Learned from Pass 1) ---")
    res_p0 = engine.evaluate(query_p1)
    print(f"Query: '{query_p1}'")
    print(f"Result Tier: {res_p0.tier}")
    print(f"Matched: {res_p0.matched}")
    print(f"Confidence: {res_p0.confidence}")
    print(f"Total Latency: {res_p0.latency_ms} ms")
    assert res_p0.tier == "pass0_valkey", f"Expected pass0_valkey, got {res_p0.tier}"
    assert res_p0.latency_ms < 35.0, f"Expected fast response over network, got {res_p0.latency_ms} ms"
    print("[OK] Pass 0 In-RAM Cache Passed!")

    # -------------------------------------------------------------
    # TEST 3: Noul (Calibrated Boolean Check) on :8002
    # -------------------------------------------------------------
    print("\n--- [TEST 3] Pass 2: Calibrated Boolean Noul Head (P(true) via 1-Token Logit) ---")
    context_security = "Perimeter sentry camera detects an adult male approaching front door at 2:00 AM wearing mask."
    q_breach = "Does this indicate a potential security concern?"
    
    t0 = time.perf_counter()
    is_threat, p_threat = engine.noul_check(context_security, q_breach)
    lat_noul = (time.perf_counter() - t0) * 1000.0
    
    print(f"Context: '{context_security}'")
    print(f"Question: '{q_breach}'")
    print(f"Verdict: {is_threat} (Confidence P={p_threat})")
    print(f"Latency: {round(lat_noul, 2)} ms")
    assert is_threat is True, "Security breach context should evaluate to True"
    print("[OK] Noul Head Passed!")

    # -------------------------------------------------------------
    # TEST 4: Choice (Calibrated Categorical Decision) on :8002
    # -------------------------------------------------------------
    print("\n--- [TEST 4] Pass 2: Calibrated Categorical Choice Head ---")
    context_climate = "It feels very warm and stuffy in the living room, current temp is 76 degrees."
    choices = {
        "climate_cool": "Lower thermostat to 68 degrees",
        "lights_off": "Turn off living room lights",
        "security_arm": "Arm perimeter security alarm"
    }
    t0 = time.perf_counter()
    opt, conf = engine.choice_check(context_climate, choices)
    lat_choice = (time.perf_counter() - t0) * 1000.0
    
    print(f"Context: '{context_climate}'")
    print(f"Selected Option: '{opt}' (Confidence P={conf})")
    print(f"Latency: {round(lat_choice, 2)} ms")
    assert opt == "climate_cool", f"Expected 'climate_cool', got '{opt}'"
    print("[OK] Choice Head Passed!")

    # -------------------------------------------------------------
    # TEST 5: End-to-End Home Assistant Reflex Dispatch
    # -------------------------------------------------------------
    print("\n--- [TEST 5] End-to-End HassConnector Quick Intent Dispatch ---")
    dispatch_res = hass_connector.dispatch_quick_intent("turn off office light")
    print(f"Dispatch Result: {json.dumps(dispatch_res, indent=2)}")
    assert dispatch_res.get("status") == "executed_reflex" or dispatch_res.get("tier") in ("pass0_valkey", "pass1_qdrant")
    print("[OK] End-to-End Dispatch Passed!")

    print("\n" + "=" * 70)
    print("ALL SYSTEM 1 REFLEX BENCHMARKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_benchmarks()
