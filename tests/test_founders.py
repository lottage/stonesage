"""
Unit tests for 4 Pillar Founder Personas & Weighted Collective Governance.
Verifies founder discovery, identity contracts, invariants, and weighted voting math.
"""

import unittest
from harness.core.founders import (
    FOUNDERS,
    get_founder,
    is_founder,
    get_all_founders,
    evaluate_collective_vote,
)
from harness.core.openclaw_engine import openclaw_engine


class TestFounders(unittest.TestCase):
    def test_four_founders_exist(self):
        """All 4 immortal pillars must be defined with required invariants."""
        founders = get_all_founders()
        self.assertEqual(len(founders), 4)
        ids = {f.id for f in founders}
        self.assertEqual(ids, {"aule", "radagast", "mandos", "varda"})

        for f in founders:
            self.assertTrue(is_founder(f.id))
            self.assertGreaterEqual(f.weight, 1.5)
            self.assertGreaterEqual(len(f.invariants), 2)
            self.assertTrue(len(f.mission) > 10)

        # Backward compatibility aliases
        for alias in ["aevum", "sentinel", "mnemosyne", "hearth"]:
            self.assertTrue(is_founder(alias))

    def test_get_founder_by_id_and_domain(self):
        """Founders must resolve by primary ID or specialized domain keywords."""
        # By ID and Alias
        self.assertEqual(get_founder("aule").name, "Aulë")
        self.assertEqual(get_founder("aevum").name, "Aulë")
        self.assertEqual(get_founder("radagast").name, "Radagast")
        self.assertEqual(get_founder("sentinel").name, "Radagast")
        self.assertEqual(get_founder("mandos").name, "Mandos")
        self.assertEqual(get_founder("mnemosyne").name, "Mandos")
        self.assertEqual(get_founder("varda").name, "Varda")
        self.assertEqual(get_founder("hearth").name, "Varda")

        # By specialized domain
        self.assertEqual(get_founder("software").id, "aule")
        self.assertEqual(get_founder("code").id, "aule")
        self.assertEqual(get_founder("cameras").id, "radagast")
        self.assertEqual(get_founder("wildlife").id, "radagast")
        self.assertEqual(get_founder("obsidian").id, "mandos")
        self.assertEqual(get_founder("truth").id, "mandos")
        self.assertEqual(get_founder("energy").id, "varda")
        self.assertEqual(get_founder("hvac").id, "varda")

    def test_weighted_collective_vote_math(self):
        """
        Verify weighted voting mathematics:
        - Founders have 1.5x base weight, 2.0x in their domain.
        - Standard agents have 1.0x weight.
        """
        votes = {
            "aule": True,
            "radagast": True,
            "worker-1": False
        }
        res = evaluate_collective_vote(
            proposal="Migrate to async SQLite WAL",
            domain="software",
            votes=votes
        )
        self.assertTrue(res["passed"])
        self.assertEqual(res["lead_founder"], "Aulë")
        self.assertAlmostEqual(res["total_weight"], 4.5)
        self.assertAlmostEqual(res["approved_weight"], 3.5)
        self.assertAlmostEqual(res["approval_ratio"], 0.778, places=2)

        votes_split = {
            "aule": False,
            "worker-1": True,
            "worker-2": True
        }
        res_fail = evaluate_collective_vote(
            proposal="Drop schema migrations",
            domain="software",
            votes=votes_split,
            veto_threshold=0.51
        )
        self.assertFalse(res_fail["passed"])

    def test_openclaw_founder_profile_fallback(self):
        """OpenClaw dynamically falls back to Founder contracts when queried."""
        profile = openclaw_engine.get_profile("mandos")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["agent_id"], "mandos")
        self.assertEqual(profile["name"], "Mandos")
        self.assertTrue(profile["is_founder"])
        self.assertIn("soul", profile["soul_md"].lower())
        self.assertIn("memory", profile["identity_md"].lower())

        # Also fallback via backward-compatible alias
        alias_profile = openclaw_engine.get_profile("mnemosyne")
        self.assertIsNotNone(alias_profile)
        self.assertEqual(alias_profile["agent_id"], "mandos")


if __name__ == "__main__":
    unittest.main()
