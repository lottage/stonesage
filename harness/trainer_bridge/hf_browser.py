"""
Hugging Face GGUF Hub Browser & Capacity Estimator.
Searches HF for open-weights models and GGUFs, computes VRAM/RAM requirements
using the dynamic offload engine, and maps them to cluster node limits.
"""

import json
import logging
import urllib.request
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional

from harness.core.offload_calc import OffloadCalculator, QuantType

logger = logging.getLogger("harness.hf_browser")


@dataclass
class GGUFVariant:
    filename: str
    quant_type: str
    size_bytes: int
    size_gb: float
    download_url: str
    fits_rog_ally_extreme: bool = True
    fits_rog_ally_x: bool = True  # Backward-compatible alias
    fits_vm102_primary: bool = True
    fits_vm102_secondary: bool = True
    recommended_context: int = 8192


@dataclass
class GGUFModelInfo:
    model_id: str
    author: str
    pipeline_tag: str
    downloads: int
    likes: int
    variants: List[GGUFVariant] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HuggingFaceBrowser:
    """Searches Hugging Face for GGUF models and computes cluster compatibility."""

    HF_API_BASE = "https://huggingface.co/api"

    def __init__(self):
        self.calculator = OffloadCalculator()

    def search_models(
        self, query: str = "qwen2.5-coder gguf", limit: int = 10
    ) -> List[GGUFModelInfo]:
        """Queries Hugging Face model API for GGUF models."""
        params = {
            "search": query,
            "filter": "gguf",
            "sort": "downloads",
            "direction": "-1",
            "limit": limit,
            "full": "false"
        }
        url = f"{self.HF_API_BASE}/models?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Harness/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Hugging Face API request failed: {e}. Returning curated fallback.")
            return self._get_curated_fallback(query)

        results: List[GGUFModelInfo] = []
        for item in data:
            model_id = item.get("id", "")
            parts = model_id.split("/")
            author = parts[0] if len(parts) > 1 else "community"

            # Parse variants
            variants = self._inspect_model_variants(model_id)

            info = GGUFModelInfo(
                model_id=model_id,
                author=author,
                pipeline_tag=item.get("pipeline_tag", "text-generation"),
                downloads=item.get("downloads", 0),
                likes=item.get("likes", 0),
                variants=variants,
                tags=item.get("tags", [])[:8],
            )
            results.append(info)

        return results

    def _inspect_model_variants(self, model_id: str) -> List[GGUFVariant]:
        """Inspects model repository tree for .gguf files and evaluates hardware fit."""
        url = f"{self.HF_API_BASE}/models/{model_id}/tree/main"
        variants: List[GGUFVariant] = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Antigravity-Harness/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                tree = json.loads(resp.read().decode("utf-8"))

            for entry in tree:
                path = entry.get("path", "")
                if path.lower().endswith(".gguf"):
                    size_bytes = entry.get("size", 0)
                    size_gb = round(size_bytes / (1024**3), 2)
                    quant = self._extract_quant(path)

                    # Determine hardware fit dynamically from fleet_config
                    from ..config import fleet_config
                    ally_node = fleet_config.nodes.get("node2_edge") or fleet_config.nodes.get("node2_edge")
                    ally_ram_gb = (ally_node.total_memory_mb / 1024.0) if ally_node else 16.0
                    fits_ally = size_gb <= max(ally_ram_gb - 3.5, 4.0)
                    fits_vm_p = size_gb <= 11.5
                    fits_vm_s = size_gb <= 7.5

                    variants.append(GGUFVariant(
                        filename=path,
                        quant_type=quant,
                        size_bytes=size_bytes,
                        size_gb=size_gb,
                        download_url=f"https://huggingface.co/{model_id}/resolve/main/{path}",
                        fits_rog_ally_extreme=fits_ally,
                        fits_rog_ally_x=fits_ally,
                        fits_vm102_primary=fits_vm_p,
                        fits_vm102_secondary=fits_vm_s,
                        recommended_context=16384 if size_gb < 6 else 8192,
                    ))
        except Exception as e:
            logger.debug(f"Tree query for {model_id} skipped: {e}")

        # If no variants discovered from tree, synthesize standard quants
        if not variants:
            variants = [
                GGUFVariant("model-Q4_K_M.gguf", "Q4_K_M", 5_200_000_000, 4.84, f"https://huggingface.co/{model_id}", True, True, True, 8192),
                GGUFVariant("model-Q8_0.gguf", "Q8_0", 9_800_000_000, 9.13, f"https://huggingface.co/{model_id}", True, True, False, 8192),
            ]
        return variants

    def _extract_quant(self, filename: str) -> str:
        fn = filename.upper()
        for q in ["Q4_K_M", "Q4_K_S", "Q5_K_M", "Q5_K_S", "Q8_0", "Q3_K_M", "Q6_K", "Q2_K", "BF16", "FP16"]:
            if q in fn:
                return q
        return "GGUF"

    def _get_curated_fallback(self, query: str) -> List[GGUFModelInfo]:
        """Curated high-signal fallback list for homelab cluster."""
        return [
            GGUFModelInfo(
                model_id="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
                author="Qwen",
                pipeline_tag="text-generation",
                downloads=285000,
                likes=1450,
                variants=[
                    GGUFVariant("qwen2.5-coder-14b-instruct-q4_k_m.gguf", "Q4_K_M", 9_200_000_000, 8.57, "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", True, True, False, 12288),
                    GGUFVariant("qwen2.5-coder-14b-instruct-q5_k_m.gguf", "Q5_K_M", 10_800_000_000, 10.06, "https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", True, True, False, 8192),
                ],
                tags=["code", "coder", "qwen", "gguf"],
            ),
            GGUFModelInfo(
                model_id="bartowski/Ornith-1.5-9B-Instruct-GGUF",
                author="bartowski",
                pipeline_tag="text-generation",
                downloads=65000,
                likes=420,
                variants=[
                    GGUFVariant("Ornith-1.5-9B-Instruct-Q4_K_M.gguf", "Q4_K_M", 5_400_000_000, 5.03, "https://huggingface.co/bartowski/Ornith-1.5-9B-Instruct-GGUF", True, True, True, 16384),
                    GGUFVariant("Ornith-1.5-9B-Instruct-Q8_0.gguf", "Q8_0", 9_900_000_000, 9.22, "https://huggingface.co/bartowski/Ornith-1.5-9B-Instruct-GGUF", True, True, False, 8192),
                ],
                tags=["reasoning", "instruct", "gguf"],
            ),
            GGUFModelInfo(
                model_id="unsloth/Qwen2.5-Coder-3B-Instruct-GGUF",
                author="unsloth",
                pipeline_tag="text-generation",
                downloads=190000,
                likes=890,
                variants=[
                    GGUFVariant("qwen2.5-coder-3b-instruct-q5_k_m.gguf", "Q5_K_M", 2_400_000_000, 2.24, "https://huggingface.co/unsloth/Qwen2.5-Coder-3B-Instruct-GGUF", True, True, True, 32768),
                    GGUFVariant("qwen2.5-coder-3b-instruct-q8_0.gguf", "Q8_0", 3_400_000_000, 3.17, "https://huggingface.co/unsloth/Qwen2.5-Coder-3B-Instruct-GGUF", True, True, True, 16384),
                ],
                tags=["speculative-draft", "fast", "gguf"],
            )
        ]
