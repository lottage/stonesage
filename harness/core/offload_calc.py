"""
Mathematical Context & Hardware Capacity Engine.
Continuous unbounded context window calculations with a strict >= 4096 token agent floor.
Dynamically computes VRAM/RAM allocations, KV cache quantization, and GPU offload layers.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass

MIN_AGENT_CONTEXT_FLOOR = 4096

@dataclass
class MemoryEstimate:
    param_billions: float
    quant_bits: float
    context_length: int
    parallel_slots: int
    kv_precision: str
    base_weights_gb: float
    kv_cache_gb: float
    activation_gb: float
    total_required_gb: float
    target_vram_gb: float
    fits_in_vram: bool
    vram_headroom_gb: float
    gpu_layers_offload: int
    total_layers: int
    cpu_ram_spillover_gb: float
    recommendation: str

class HardwareCapacityEngine:
    KV_PRECISION_BYTES = {
        "q4_0": 0.5,
        "q4_1": 0.56,
        "iq4_nl": 0.5,
        "q5_0": 0.625,
        "q5_1": 0.6875,
        "q8_0": 1.0,
        "f16": 2.0,
        "fp16": 2.0,
        "f32": 4.0,
        "fp32": 4.0,
    }

    MODEL_ARCH_PRESETS = {
        "0.5b": {"layers": 24, "kv_heads": 2, "head_dim": 64, "params": 0.5},
        "1.5b": {"layers": 28, "kv_heads": 2, "head_dim": 64, "params": 1.54},
        "3b": {"layers": 24, "kv_heads": 4, "head_dim": 128, "params": 3.1},
        "7b": {"layers": 28, "kv_heads": 8, "head_dim": 128, "params": 7.2},
        "8b": {"layers": 32, "kv_heads": 8, "head_dim": 128, "params": 8.0},
        "9b": {"layers": 32, "kv_heads": 8, "head_dim": 128, "params": 9.2},
        "11b": {"layers": 40, "kv_heads": 8, "head_dim": 128, "params": 11.0},
        "14b": {"layers": 48, "kv_heads": 8, "head_dim": 128, "params": 14.7},
        "27b": {"layers": 46, "kv_heads": 16, "head_dim": 128, "params": 27.2},
        "32b": {"layers": 64, "kv_heads": 8, "head_dim": 128, "params": 32.5},
        "35b_moe": {"layers": 40, "kv_heads": 8, "head_dim": 128, "params": 35.0, "active_params": 6.8},
        "70b": {"layers": 80, "kv_heads": 8, "head_dim": 128, "params": 70.6},
        "120b": {"layers": 88, "kv_heads": 8, "head_dim": 128, "params": 120.0},
        "236b": {"layers": 60, "kv_heads": 16, "head_dim": 128, "params": 236.0},
        "405b": {"layers": 126, "kv_heads": 8, "head_dim": 128, "params": 405.0},
    }

    QUANT_BITS = {
        "iq1_s": 1.56,
        "iq1_m": 1.75,
        "iq2_xxs": 2.06,
        "iq2_xs": 2.31,
        "iq2_s": 2.50,
        "iq2_m": 2.70,
        "iq3_xxs": 3.06,
        "iq3_xs": 3.30,
        "iq3_s": 3.44,
        "iq3_m": 3.66,
        "q2_k": 2.62,
        "q3_k_s": 3.41,
        "q3_k_m": 3.91,
        "q3_k_l": 4.27,
        "iq4_nl": 4.50,
        "iq4_xs": 4.25,
        "q4_0": 4.55,
        "q4_1": 5.00,
        "q4_k_s": 4.58,
        "q4_k_m": 4.85,
        "q5_0": 5.54,
        "q5_1": 6.00,
        "q5_k_s": 5.54,
        "q5_k_m": 5.69,
        "q6_k": 6.56,
        "q8_0": 8.50,
        "f16": 16.0,
        "fp16": 16.0,
        "f32": 32.0,
        "fp32": 32.0,
    }

    def estimate(
        self,
        arch_type: str = "9b",
        quant: str = "q4_k_m",
        context_length: int = 8192,
        parallel_slots: int = 1,
        kv_precision: str = "q4_0",
        target_vram_gb: float = 12.0,
        total_system_ram_gb: float = 32.0,
        custom_params_b: Optional[float] = None,
        custom_layers: Optional[int] = None,
        enforce_floor: bool = True,
        custom_kv_heads: Optional[int] = None,
        custom_head_dim: Optional[int] = None,
    ) -> MemoryEstimate:
        """
        Computes accurate memory footprint across VRAM and system RAM.
        Enforces context >= 4096 tokens by default for agent stability (enforce_floor=True),
        or supports unbounded micro-contexts (enforce_floor=False).
        """
        if enforce_floor:
            effective_context = max(int(context_length), MIN_AGENT_CONTEXT_FLOOR)
        else:
            effective_context = max(int(context_length), 1)

        # Allow arch_type like "14", "14b", "3.0"
        if custom_params_b is None:
            try:
                clean_arch = arch_type.lower().rstrip("b")
                custom_params_b = float(clean_arch)
            except (ValueError, AttributeError):
                pass

        spec = self.MODEL_ARCH_PRESETS.get(arch_type.lower(), self.MODEL_ARCH_PRESETS.get("9b", {}))
        params_b = custom_params_b if custom_params_b is not None else spec.get("params", 9.2)

        if custom_layers:  # real geometry from the GGUF header when the caller has it
            layers = custom_layers
            kv_heads = custom_kv_heads or spec.get("kv_heads", 8)
            head_dim = custom_head_dim or spec.get("head_dim", 128)
        elif "layers" in spec and (custom_params_b is None or abs(params_b - spec.get("params", 0)) < 0.1):
            layers = spec["layers"]
            kv_heads = spec.get("kv_heads", 8)
            head_dim = spec.get("head_dim", 128)
        else:
            # Dynamically derive architecture geometry from parameter count
            if params_b <= 1.0:
                layers = 24
                kv_heads = 2
                head_dim = 64
            elif params_b <= 4.0:
                layers = 24
                kv_heads = 4
                head_dim = 128
            elif params_b <= 16.0:
                layers = int(24 + (params_b - 4.0) * 2.4)
                kv_heads = 8
                head_dim = 128
            elif params_b <= 40.0:
                layers = 64
                kv_heads = 8
                head_dim = 128
            else:
                layers = 80
                kv_heads = 8
                head_dim = 128

        # Base weights calculation (with 15% GGUF tensor/metadata overhead)
        bits = self.QUANT_BITS.get(quant.lower(), 4.85)
        base_weights_gb = round((params_b * bits / 8.0) * 1.15, 2)

        # KV Cache buffer calculation (unbounded)
        bytes_per_elem = self.KV_PRECISION_BYTES.get(kv_precision.lower(), 0.5)
        raw_kv_bytes = 2 * layers * kv_heads * head_dim * effective_context * bytes_per_elem * max(int(parallel_slots), 1)
        kv_cache_gb = round(raw_kv_bytes / 1e9, 2)

        # Activation buffer & driver runtime overhead
        if effective_context <= 16384:
            activation_gb = 0.55
        else:
            activation_gb = round(0.55 + ((effective_context - 16384) / 10000.0) * 0.15 * max(int(parallel_slots), 1), 2)
        driver_overhead_gb = 0.45

        # 6. Total VRAM needed for 100% GPU execution
        total_required_gb = round(base_weights_gb + kv_cache_gb + activation_gb + driver_overhead_gb, 2)
        fits_in_vram = total_required_gb <= target_vram_gb
        headroom_gb = round(target_vram_gb - total_required_gb, 2)

        # 7. Layer offload & CPU RAM spillover if exceeding target VRAM
        if fits_in_vram:
            gpu_layers_offload = layers
            cpu_ram_spillover_gb = 0.0
            recommendation = (
                f"Optimal! 100% GPU accelerated ({gpu_layers_offload}/{layers} layers in VRAM). "
                f"Headroom: {headroom_gb} GB."
            )
        else:
            # Memory available for weights after reserving KV cache & activations
            available_for_weights = max(target_vram_gb - kv_cache_gb - activation_gb - driver_overhead_gb, 0.0)
            offload_ratio = min(available_for_weights / base_weights_gb, 1.0)
            gpu_layers_offload = max(int(layers * offload_ratio), 0)
            spillover_layers = layers - gpu_layers_offload
            weights_spillover = base_weights_gb * (spillover_layers / layers)
            kv_spillover = max(0.0, (kv_cache_gb + activation_gb + driver_overhead_gb) - target_vram_gb)
            cpu_ram_spillover_gb = round(weights_spillover + kv_spillover, 2)
            recommendation = (
                f"Partial offload required: {gpu_layers_offload}/{layers} layers on GPU (-ngl {gpu_layers_offload}), "
                f"{cpu_ram_spillover_gb} GB spillover into system RAM."
            )

        return MemoryEstimate(
            param_billions=params_b,
            quant_bits=bits,
            context_length=effective_context,
            parallel_slots=parallel_slots,
            kv_precision=kv_precision,
            base_weights_gb=base_weights_gb,
            kv_cache_gb=kv_cache_gb,
            activation_gb=activation_gb,
            total_required_gb=total_required_gb,
            target_vram_gb=target_vram_gb,
            fits_in_vram=fits_in_vram,
            vram_headroom_gb=headroom_gb,
            gpu_layers_offload=gpu_layers_offload,
            total_layers=layers,
            cpu_ram_spillover_gb=cpu_ram_spillover_gb,
            recommendation=recommendation,
        )

    def get_presets(self, target_vram_gb: float = 12.0) -> Dict[str, Dict[str, Any]]:
        """Returns standard fleet operational presets spanning light to unbounded context."""
        return {
            "subagent_farm": {
                "name": "Subagent Farm",
                "description": "4 parallel slots @ 8k context for high-throughput multi-agent tasks (ROG Ally Extreme / 16GB)",
                "context": 8192,
                "slots": 4,
                "kv_precision": "q4_0",
                "quant": "q4_k_m"
            },
            "hybrid_2_plus_2": {
                "name": "Hybrid 2+2",
                "description": "2 persistent agent slots (12k) + 2 elastic burst slots (8k)",
                "context": 12288,
                "slots": 2,
                "kv_precision": "q4_0",
                "quant": "q4_k_m"
            },
            "deep_monolith": {
                "name": "Deep Context Monolith",
                "description": "1 slot @ 32k context for uncompressed multi-file refactoring and proofs",
                "context": 32768,
                "slots": 1,
                "kv_precision": "q4_0",
                "quant": "q8_0"
            },
            "frontier_128k": {
                "name": "Frontier Codebase Deep Scan",
                "description": "1 slot @ 128k context for whole-repo audits and massive cross-file synthesis",
                "context": 131072,
                "slots": 1,
                "kv_precision": "q4_0",
                "quant": "q4_k_m"
            },
            "ultra_256k": {
                "name": "Ultra-Deep Research Needle",
                "description": "1 slot @ 256k context for massive multi-document ingestion and literature cross-analysis",
                "context": 262144,
                "slots": 1,
                "kv_precision": "q4_0",
                "quant": "q4_0"
            },
            "needle_1m": {
                "name": "Infinite Context Horizon (1M)",
                "description": "1 slot @ 1,048,576 tokens for unbounded frontier needle-in-haystack benchmarking",
                "context": 1048576,
                "slots": 1,
                "kv_precision": "q4_0",
                "quant": "q4_0"
            }
        }

offload_engine = HardwareCapacityEngine()
OffloadCalculator = HardwareCapacityEngine
QuantType = str
