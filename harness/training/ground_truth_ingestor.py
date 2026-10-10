"""
Ground Truth Training Ingestor.
Re-balances the fine-tuning pipeline to prevent synthetic model collapse:
  - 70% Real-World Ground Truth (actual human chat turns, accepted git commits, HA events).
  - 30% Audited Ruminations (ONLY those that passed Tier-1 Frontier audit or deterministic tests).
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("Harness.TrainingIngestor")

@dataclass
class DatasetCurationSummary:
    total_samples: int
    real_world_samples: int
    synthetic_audited_samples: int
    real_world_percentage: float
    output_jsonl_path: str

class GroundTruthIngestor:
    def __init__(self, data_root: Optional[str] = None):
        self.data_root = data_root or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "training")
        )
        os.makedirs(self.data_root, exist_ok=True)

    def curate_balanced_dataset(
        self,
        raw_user_transcripts: List[Dict[str, Any]],
        accepted_code_diffs: List[Dict[str, Any]],
        audited_ruminations: List[Dict[str, Any]],
        output_filename: str = "curated_sft_dataset.jsonl",
    ) -> DatasetCurationSummary:
        """
        Builds a high-precision SFT dataset enforcing the 70/30 ground-truth ratio.
        """
        curated_samples = []

        # 1. Ingest Real-World User Transcripts (Priority 1)
        for t in raw_user_transcripts:
            prompt = t.get("user_prompt", "")
            response = t.get("assistant_response", "")
            if prompt and response:
                curated_samples.append({
                    "type": "real_world_dialogue",
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": response}
                    ],
                    "weight": 1.5  # Higher weight for actual operator interactions
                })

        # 2. Ingest Accepted Git Commits & Verified Code Changes
        for c in accepted_code_diffs:
            task = c.get("task", "Code Refactoring")
            code = c.get("diff", "")
            if task and code:
                curated_samples.append({
                    "type": "real_world_code",
                    "messages": [
                        {"role": "user", "content": f"Implement changes for: {task}"},
                        {"role": "assistant", "content": f"```diff\n{code}\n```"}
                    ],
                    "weight": 2.0  # Double weight for verified code commits
                })

        real_world_count = len(curated_samples)

        # 3. Ingest Audited Ruminations (Max 30% of total)
        # Calculate budget for synthetic data: synthetic <= 0.43 * real_world (to maintain >= 70% real)
        max_synthetic_budget = max(int(real_world_count * 0.428), 5)
        accepted_synthetic = 0

        for r in audited_ruminations:
            if accepted_synthetic >= max_synthetic_budget:
                break
            # Enforce strict audit gate
            if not r.get("frontier_verified") and not r.get("test_passed"):
                continue  # Quarantine unverified synthetic self-debates!

            curated_samples.append({
                "type": "audited_synthetic",
                "messages": r.get("messages", []),
                "weight": 1.0
            })
            accepted_synthetic += 1

        total_samples = len(curated_samples)
        rw_pct = round((real_world_count / max(total_samples, 1)) * 100, 1)

        out_path = os.path.join(self.data_root, output_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            for s in curated_samples:
                f.write(json.dumps(s) + "\n")

        logger.info(
            f"⚡ [Dataset Curator] Generated balanced dataset: {total_samples} samples "
            f"({real_world_count} real-world / {accepted_synthetic} audited synthetic) -> {rw_pct}% Ground Truth."
        )

        return DatasetCurationSummary(
            total_samples=total_samples,
            real_world_samples=real_world_count,
            synthetic_audited_samples=accepted_synthetic,
            real_world_percentage=rw_pct,
            output_jsonl_path=out_path,
        )

ground_truth_ingestor = GroundTruthIngestor()
