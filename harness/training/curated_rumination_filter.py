"""
Curated Rumination Filter
Guards against synthetic model degradation and collapse.
Strictly quarantines unreviewed, non-deterministic self-debates.
Only passes ruminations that possess:
  1. Formal Tier-1 Frontier (Antigravity/Gemini) meta-verification verdict, OR
  2. 100% passing deterministic runtime unit test suite execution.
"""

import os
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("harness.training.filter")


@dataclass
class RuminationAuditResult:
    dossier_id: str
    is_approved: bool
    rejection_reason: Optional[str] = None
    frontier_verified: bool = False
    tests_passed: bool = False
    novelty_score: float = 1.0
    invariant_extracted: Optional[str] = None


class CuratedRuminationFilter:
    """Filters synthetic thinking dossiers before fine-tuning ingestion."""

    def __init__(self, quarantine_dir: Optional[Path] = None):
        self.quarantine_dir = quarantine_dir or Path("data/quarantined_ruminations")
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def audit_dossier(self, dossier: Dict[str, Any]) -> RuminationAuditResult:
        """
        Evaluates an individual synthetic rumination dossier.
        Enforces the Synthetic Degradation Trap invariant.
        """
        dossier_id = dossier.get("id") or dossier.get("dossier_id") or "unknown_dossier"

        # 1. Check Frontier Meta-Verification
        is_frontier_verified = bool(
            dossier.get("frontier_verified")
            or dossier.get("tier1_verified")
            or dossier.get("antigravity_verdict") in ("APPROVED", "CONFIRMED", "VERIFIED")
            or (isinstance(dossier.get("metadata"), dict) and dossier["metadata"].get("frontier_audit") == "PASSED")
        )

        # 2. Check Deterministic Unit Tests
        tests_passed = False
        test_data = dossier.get("test_results") or {}
        if isinstance(test_data, dict):
            passed = test_data.get("passed", 0)
            failed = test_data.get("failed", 0)
            tests_passed = (passed > 0 and failed == 0)
        elif dossier.get("test_passed") is True:
            tests_passed = True

        # 3. Check Novelty Gate (< 0.85 cosine similarity)
        novelty_score = float(dossier.get("novelty_score", 1.0))
        is_redundant = novelty_score < 0.15  # In similarity terms, similarity > 0.85 => novelty < 0.15

        # Invariant check
        invariant = dossier.get("invariant") or dossier.get("architectural_invariant")

        if is_redundant:
            return RuminationAuditResult(
                dossier_id=dossier_id,
                is_approved=False,
                rejection_reason=f"Failed Novelty Gate: redundant with existing vectors (similarity > 0.85)",
                frontier_verified=is_frontier_verified,
                tests_passed=tests_passed,
                novelty_score=novelty_score,
                invariant_extracted=invariant,
            )

        if not (is_frontier_verified or tests_passed):
            return RuminationAuditResult(
                dossier_id=dossier_id,
                is_approved=False,
                rejection_reason="Unverified synthetic rumination: lacks Tier-1 Frontier audit and deterministic test pass",
                frontier_verified=is_frontier_verified,
                tests_passed=tests_passed,
                novelty_score=novelty_score,
                invariant_extracted=invariant,
            )

        return RuminationAuditResult(
            dossier_id=dossier_id,
            is_approved=True,
            frontier_verified=is_frontier_verified,
            tests_passed=tests_passed,
            novelty_score=novelty_score,
            invariant_extracted=invariant,
        )

    def filter_batch(
        self, dossiers: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[RuminationAuditResult]]:
        """
        Splits a batch of candidate dossiers into approved vs quarantined.
        """
        approved = []
        rejected_audits = []

        for d in dossiers:
            result = self.audit_dossier(d)
            if result.is_approved:
                approved.append(d)
            else:
                rejected_audits.append(result)
                self._quarantine(d, result)

        logger.info(
            f"Audit complete: {len(approved)} approved for 30% synthetic quota, "
            f"{len(rejected_audits)} quarantined."
        )
        return approved, rejected_audits

    def _quarantine(self, dossier: Dict[str, Any], audit: RuminationAuditResult) -> None:
        try:
            filename = f"quarantined_{audit.dossier_id}.json"
            q_file = self.quarantine_dir / filename
            record = {
                "audit": asdict(audit),
                "dossier": dossier,
            }
            q_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write quarantine record for {audit.dossier_id}: {e}")
