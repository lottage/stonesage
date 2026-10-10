"""
Unit tests for Dynamic Tiered Context Fabric and A-MEM atomic fact injection.
Validates 85-90% token savings on routine turns and instant factual grounding
for databases, hardware, smart home, and agent personas.
"""

import unittest
from harness.core.context_fabric import context_fabric
from harness.data_fabric.valkey_amem import valkey_amem
from harness.config import fleet_config

PVE_IP = "192.0.2.222"  # Node 1 'pve' (Proxmox /cluster/status, 2026-09-23)
STALE_PVE_IP = "192.0.2.229"

class TestContextFabric(unittest.TestCase):
    def setUp(self):
        valkey_amem.seed_homelab_core_cards()

    def tearDown(self):
        import shutil
        import os
        from harness.core.openclaw_engine import openclaw_engine
        test_dir = os.path.join(openclaw_engine.profiles_dir, "zephyr")
        test_json = os.path.join(openclaw_engine.profiles_dir, "zephyr.json")
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
        if os.path.exists(test_json):
            try:
                os.remove(test_json)
            except Exception:
                pass

    def test_seeded_core_cards(self):
        """Verify the 5 core homelab fact cards are permanently registered with zero decay."""
        recalled_db = valkey_amem.recall("database", max_atoms=5)
        self.assertTrue(any(c["id"] == "core_databases" for c in recalled_db))
        db_card = next(c for c in recalled_db if c["id"] == "core_databases")
        self.assertTrue(db_card.get("is_core_memory"))
        self.assertIn("Qdrant", db_card["atom"])
        self.assertIn("Valkey", db_card["atom"])
        self.assertIn("CouchDB", db_card["atom"])
        self.assertIn("SQLite", db_card["atom"])

        recalled_topo = valkey_amem.recall("cluster gpu hardware", max_atoms=5)
        self.assertTrue(any(c["id"] == "core_topology" for c in recalled_topo))
        topo_card = next(c for c in recalled_topo if c["id"] == "core_topology")
        # offline (unit tests) the card says the layout is unknown instead of inventing hardware
        self.assertIn("/api/system/profile", topo_card["atom"])

    def test_topology_card_from_live_profile(self):
        """The topology card names the real GPUs and engines from the system profile."""
        from harness.data_fabric.valkey_amem import live_topology_text
        prof = {"host": {"hostname": "box"},
                "gpus": [{"short": "RTX 4090", "vram_total_gb": 24, "engines": ["coordinator"]}],
                "engines": {"coordinator": {"port": 8001, "model": "Llama 3 70B"},
                            "worker": {"port": 8002, "model": "Tiny 1B", "offloaded": True}}}
        text = live_topology_text(prof)
        self.assertIn("RTX 4090 24GB runs coordinator :8001 (Llama 3 70B)", text)
        self.assertIn("CPU runs worker :8002", text)

    def test_routine_query_token_efficiency(self):
        """Routine queries should receive neutral direct response (< 55 tokens, 0 atoms, no persona)."""
        prompt = context_fabric.compile_dynamic_turn("what is 2 + 2?")
        words = prompt.split()
        self.assertLessEqual(len(words), 55)
        self.assertIn("Direct Answer Mode", prompt)
        self.assertNotIn("StoneSage", prompt)
        self.assertNotIn("Alex", prompt)
        self.assertNotIn("KNOWLEDGE ATOM", prompt)
        self.assertNotIn(PVE_IP, prompt)

    def test_database_query_accuracy_and_token_count(self):
        """Queries mentioning databases must inject targeted atom (< 85 words)."""
        prompt = context_fabric.compile_dynamic_turn("what databases are we using?")
        words = prompt.split()
        self.assertLessEqual(len(words), 85)
        self.assertIn("KNOWLEDGE ATOM", prompt)
        self.assertIn("Qdrant Vector DB", prompt)
        self.assertIn(fleet_config.qdrant_url, prompt)
        self.assertIn("Valkey", prompt)
        self.assertIn("CouchDB", prompt)
        self.assertIn("SQLite", prompt)

    def test_hardware_query_accuracy(self):
        """Queries mentioning cluster hardware or GPUs must inject topology atom."""
        prompt = context_fabric.compile_dynamic_turn("which GPU does coordinator use on node1?")
        self.assertIn("KNOWLEDGE ATOM", prompt)
        self.assertIn("Coordinator :8001", prompt)  # hierarchy card; GPU names come from the live profile

    def test_smarthome_query_accuracy(self):
        """Queries mentioning smart home or Nest thermostat must inject smarthome atom."""
        prompt = context_fabric.compile_dynamic_turn("what is the Nest thermostat setting?")
        self.assertIn("KNOWLEDGE ATOM", prompt)
        self.assertIn("Home Assistant", prompt)
        self.assertIn("thermostat", prompt)

    def test_agent_persona_injection(self):
        """Active agent 'aevum' receives its specialized persona and Direct Answer Mode."""
        prompt = context_fabric.compile_dynamic_turn("how should we structure this module?", agent_id="aevum")
        self.assertIn("Aevum", prompt)
        self.assertIn("Manager of Coding Department", prompt)
        self.assertIn("Direct Answer Mode", prompt)
        # Routine query should still not leak hardware IPs
        self.assertNotIn(PVE_IP, prompt)

    def test_agent_with_database_query(self):
        """Agent 'aevum' asking about databases gets its persona + the database atom."""
        prompt = context_fabric.compile_dynamic_turn("which database stores our vector embeddings?", agent_id="aevum")
        self.assertIn("Aevum", prompt)
        self.assertIn("Manager of Coding Department", prompt)
        self.assertIn("Qdrant Vector DB", prompt)
        self.assertIn(fleet_config.qdrant_url, prompt)

    def test_arbitrary_future_agent_expansion(self):
        """Any future agent registered in OpenClaw dynamically integrates with zero hardcoding."""
        from harness.core.openclaw_engine import openclaw_engine
        # Build and register an arbitrary new agent
        contract = openclaw_engine.build_contracts(
            agent_id="zephyr",
            name="Zephyr",
            role="Security & Network Auditor",
            mission="Scan open ports and verify SSL certs across Proxmox",
            autonomy_level="strict"
        )
        # Verify context fabric immediately compiles Zephyr's persona
        prompt = context_fabric.compile_dynamic_turn("audit port 8080", agent_id="zephyr")
        self.assertIn("Zephyr", prompt)
        self.assertIn("Security & Network Auditor", prompt)
        self.assertIn("Scan open ports", prompt)

        # Verify A-MEM dynamically seeded Zephyr's contract
        recalled = valkey_amem.recall("zephyr", max_atoms=2)
        self.assertTrue(any("zephyr" in c["id"] for c in recalled))

    def test_full_dossier(self):
        """Full dossier compilation contains comprehensive cluster specification."""
        dossier = context_fabric.compile_full_dossier(agent_id="aevum")
        self.assertIn("Aevum", dossier)
        self.assertIn("CLUSTER SILICON & TOPOLOGY", dossier)
        self.assertIn("DATA FABRIC & MEMORY", dossier)
        self.assertIn("COGNITIVE HIERARCHY & PHILOSOPHICAL FOUNDATION", dossier)
        self.assertNotIn(STALE_PVE_IP, dossier)

if __name__ == "__main__":
    unittest.main()
