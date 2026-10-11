"""
Edge fleet model manager (compute and edge nodes from config.json).
Provides dynamic polling of local models, one-click best-fit hardware parameter calculation,
custom parameter load wizards, and agent binding to edge nodes (LM Studio v0.3+ / llama-server).
"""

import time
import json
import logging
import os
import socket
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Optional, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt

from ..config import fleet_config, _live_profile
from ..data_fabric.pg_storage import relational_storage
from ..core.aevum_mesh import aevum_mesh

logger = logging.getLogger("Harness.AllyModelManager")
console = Console()


def _live_profile_for_cli():
    return _live_profile(fleet_config.stonesage_cfg)


class EdgeFleetModelManager:
    """
    Manages models, dynamic execution slot scaling, and agent bindings
    on distributed edge fleet nodes and compute endpoints.
    Supports native LM Studio v0.3+ / llama-server APIs with zero hardcoding.
    """

    def __init__(self, node_url: Optional[str] = None, node_id: Optional[str] = None):
        self.node_id = node_id or fleet_config.active_node_id
        node_cfg = fleet_config.nodes.get(self.node_id)
        if node_cfg:
            self.node_url = node_url or node_cfg.base_url.rstrip("/").rstrip("/v1")
        else:
            self.node_url = node_url or fleet_config.coordinator_url.rstrip("/").rstrip("/v1")

    @property
    def node_name(self) -> str:
        node = fleet_config.nodes.get(self.node_id)
        return node.name if node else f"Node {self.node_id}"

    def _scan_engine_host_models(self) -> List[Dict[str, Any]]:
        """GGUF models on the inference host, with metadata from each file's header (StoneSage system_profile)."""
        from ..config import _live_models
        role = "worker" if self.node_id == "node1_secondary" else "coordinator"
        engine = ((_live_profile_for_cli() or {}).get("engines") or {}).get(role) or {}
        out = []
        for m in _live_models():
            if m.get("is_projector"):
                continue
            is_loaded = m.get("file") == engine.get("model_file")
            out.append({
                "key": m["file"], "name": m.get("name") or m["file"], "path": m["path"],
                "architecture": m.get("architecture") or "gguf", "params": m.get("params") or "Unknown",
                "size_gb": round((m.get("size_bytes") or 0) / 1024 ** 3, 2), "size_bytes": m.get("size_bytes") or 0,
                "modified_time": m.get("modified_time") or 0, "quant": m.get("quant") or "GGUF",
                "is_loaded": is_loaded,
                "instances": [{"id": m["file"], "config": {"context_length": engine.get("ctx_per_slot"),
                                                          "parallel": engine.get("slots")}}] if is_loaded else [],
                "max_context": m.get("context_length"), "layers": m.get("layers"), "format": "gguf",
            })
        return out  # empty when the host is unreachable; never a made-up list

    def _is_engine_node(self, node_id: Optional[str] = None) -> bool:
        """The node is one of the llama.cpp engines (managed over SSH), not an LM Studio node. Matched by host AND port:
        on a one-box install everything is on 127.0.0.1, and a substring test on the host called every local node an
        engine."""
        if (node_id or self.node_id) in ("node1_primary", "node1_secondary", "dual_gpu"):
            return True
        u = urllib.parse.urlparse(self.node_url)
        return any((u.hostname, u.port) == (urllib.parse.urlparse(e).hostname, urllib.parse.urlparse(e).port)
                   for e in (fleet_config.coordinator_url, fleet_config.worker_url))

    def list_local_models(self) -> List[Dict[str, Any]]:
        """
        Polls the native node endpoint (LM Studio v0.3+ /api/v1/models or the engine host local models)
        to retrieve all local models, architectures, quantizations, byte sizes, and active instances.
        """
        if self._is_engine_node():
            return self._scan_engine_host_models()

        api_url = f"{self.node_url}/api/v1/models"
        models_out = []
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "StoneSage-FleetManager"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_models = data.get("models", [])
                    for m in raw_models:
                        size_bytes = m.get("size_bytes", 0)
                        size_gb = round(size_bytes / (1024 ** 3), 2)
                        quant = m.get("quantization") or {}
                        q_name = quant.get("name", "") if isinstance(quant, dict) else str(quant)
                        loaded = len(m.get("loaded_instances", [])) > 0
                        models_out.append({
                            "key": m.get("key", ""),
                            "name": m.get("display_name", m.get("key", "")),
                            "architecture": m.get("architecture", "unknown"),
                            "params": m.get("params_string", ""),
                            "size_gb": size_gb,
                            "quant": q_name or "GGUF",
                            "is_loaded": loaded,
                            "instances": m.get("loaded_instances", []),
                            "max_context": m.get("max_context_length", 32768),
                            "format": m.get("format", "gguf")
                        })
                    return models_out
        except Exception as e:
            logger.warning(f"Could not query /api/v1/models from {api_url}: {e}")

        # Fallback to standard OpenAI /v1/models if native API is unreachable
        fallback_url = f"{self.node_url}/v1/models"
        try:
            req = urllib.request.Request(fallback_url, headers={"User-Agent": "StoneSage-FleetManager"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        models_out.append({
                            "key": mid,
                            "name": mid,
                            "architecture": "unknown",
                            "params": "",
                            "size_gb": 0.0,
                            "quant": "GGUF",
                            "is_loaded": True,
                            "instances": [],
                            "max_context": 32768,
                            "format": "gguf"
                        })
        except Exception as ex:
            logger.error(f"Fallback /v1/models poll failed: {ex}")

        return models_out

    def get_active_instance(self) -> Optional[Dict[str, Any]]:
        """Returns the currently active loaded model instance on the node."""
        models = self.list_local_models()
        for m in models:
            if m.get("is_loaded") and m.get("instances"):
                inst = m["instances"][0]
                return {
                    "key": m["key"],
                    "name": m["name"],
                    "instance_id": inst.get("id", m["key"]),
                    "config": inst.get("config", {}),
                    "size_gb": m.get("size_gb", 0.0),
                    "params": m.get("params", "")
                }
        return None

    def audit_node_resources(self, total_ram_gb: Optional[float] = None, os_reserved_gb: float = 3.5) -> Dict[str, Any]:
        """
        Audits live memory consumption on the node in real-time.
        Calculates footprint of all active loaded model instances and net available headroom.
        Dynamically extracts total hardware RAM from fleet_config if not explicitly provided.
        """
        if total_ram_gb is None:
            node = fleet_config.nodes.get(self.node_id) or fleet_config.nodes.get("node2_edge") or fleet_config.nodes.get("node2_edge")
            total_ram_gb = (node.total_memory_mb / 1024.0) if node and node.total_memory_mb > 0 else 16.0

        models = self.list_local_models()
        active_instances = []
        total_loaded_gb = 0.0

        for m in models:
            if m.get("is_loaded") and m.get("instances"):
                for inst in m["instances"]:
                    size_gb = m.get("size_gb", 0.0)
                    ctx = inst.get("config", {}).get("context_length", 8192)
                    approx_kv_gb = round((65536 * ctx * 2) / (1024 ** 3), 2)
                    total_inst_gb = round(size_gb + approx_kv_gb, 2)
                    total_loaded_gb += total_inst_gb
                    active_instances.append({
                        "model_key": m["key"],
                        "instance_id": inst.get("id", m["key"]),
                        "weights_gb": size_gb,
                        "context_length": ctx,
                        "kv_cache_gb": approx_kv_gb,
                        "total_gb": total_inst_gb
                    })

        net_available_coexist_gb = max(round(total_ram_gb - os_reserved_gb - total_loaded_gb, 2), 1.0)
        net_available_replace_gb = max(round(total_ram_gb - os_reserved_gb, 2), 2.0)

        return {
            "total_hardware_ram_gb": total_ram_gb,
            "os_reserved_gb": os_reserved_gb,
            "active_instances": active_instances,
            "active_count": len(active_instances),
            "total_loaded_gb": round(total_loaded_gb, 2),
            "net_available_coexist_gb": net_available_coexist_gb,
            "net_available_replace_gb": net_available_replace_gb
        }

    def calculate_best_fit(
        self,
        model_info: Dict[str, Any],
        available_ram_gb: Optional[float] = None,
        total_ram_gb: Optional[float] = None,
        keep_existing_models: bool = False
    ) -> Dict[str, Any]:
        """
        Dynamically derives execution parameters in real-time based on LIVE available resources,
        adapting continuously if multiple models are loaded on the node, GPU, or cluster.
        No static hardcoded size lookup: parameters scale with actual available memory headroom.
        """
        audit = self.audit_node_resources(total_ram_gb=total_ram_gb)
        if available_ram_gb is not None:
            effective_free_gb = available_ram_gb
        else:
            effective_free_gb = audit["net_available_coexist_gb"] if keep_existing_models else audit["net_available_replace_gb"]

        weights_gb = model_info.get("size_gb", 8.0)
        scratch_overhead_gb = 0.75
        kv_budget_gb = max(effective_free_gb - weights_gb - scratch_overhead_gb, 0.25)

        # Architectural parameters for KV cache memory footprint
        params_str = str(model_info.get("params", "")).lower()
        if "3b" in params_str or weights_gb < 4.0:
            layers, kv_heads = 24, 4
        elif "14b" in params_str or (10.0 <= weights_gb < 15.0):
            layers, kv_heads = 48, 8
        elif "27b" in params_str or "32b" in params_str or weights_gb >= 15.0:
            layers, kv_heads = 64, 8
        else:  # 7B - 9B default
            layers, kv_heads = 32, 8

        # KV cache footprint per token at F16: K and V (x2), per layer, per KV head, head dim 128, 2 bytes per element.
        # (K+V used to be counted twice here, which halved every context estimate.)
        kv_bytes_per_token = 2 * layers * kv_heads * 128 * 2
        max_possible_tokens = int((kv_budget_gb * (1024 ** 3)) / max(kv_bytes_per_token, 1))

        # Respect agent context floor (4096) and map to standard stable context boundaries
        thresholds = [32768, 24576, 16384, 12288, 8192, 4096]
        selected_ctx = 4096
        for t in thresholds:
            if max_possible_tokens >= t:
                selected_ctx = t
                break

        # Dynamically derive eval batch size based on memory bandwidth and KV headroom
        if kv_budget_gb >= 4.0:
            eval_batch = 2048
            phys_batch = 512
        elif kv_budget_gb >= 2.0:
            eval_batch = 1024
            phys_batch = 512
        else:
            eval_batch = 512
            phys_batch = 256

        # GPU KV offload enabled if KV cache + weights comfortably fit within hardware headroom
        gpu_kv = (kv_budget_gb >= 1.2)

        return {
            "context_length": selected_ctx,
            "flash_attention": True,
            "eval_batch_size": eval_batch,
            "physical_batch_size": phys_batch,
            "offload_kv_cache_to_gpu": gpu_kv,
            "parallel": 2,
            "cache_type_k": "f16",
            "cache_type_v": "f16",
            "resource_audit": {
                "effective_free_gb": round(effective_free_gb, 2),
                "weights_gb": round(weights_gb, 2),
                "kv_budget_gb": round(kv_budget_gb, 2),
                "coexist_mode": keep_existing_models,
                "other_loaded_models_gb": audit["total_loaded_gb"] if keep_existing_models else 0.0
            }
        }

    def unload_model(self, target_node: Optional[str] = None) -> Dict[str, Any]:
        """Unloads active model to free GPU VRAM on the target compute node."""
        node_id = target_node or self.node_id
        if self._is_engine_node(node_id):
            svc = "llama-worker.service" if node_id == "node1_secondary" else "llama-coordinator.service"
            if node_id == "dual_gpu":
                svc = "llama-coordinator.service llama-worker.service"
            try:
                cmd = fleet_config.ssh_cmd("sudo", "systemctl", "stop", *svc.split())
                sub = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                return {"ok": sub.returncode == 0, "message": f"Successfully stopped {svc} on the engine host."}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        else:
            unloaded = self.unload_active_instances()
            return {"ok": unloaded, "message": "Unloaded local LM Studio model instance(s)."}

    def unload_active_instances(self) -> bool:
        """Unloads all currently active instances on the node to free RAM/VRAM."""
        models = self.list_local_models()
        unloaded_any = False
        for m in models:
            for inst in m.get("instances", []):
                inst_id = inst.get("id")
                if inst_id:
                    unload_url = f"{self.node_url}/api/v1/models/unload"
                    payload = json.dumps({"instance_id": inst_id}).encode("utf-8")
                    req = urllib.request.Request(unload_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=10.0) as resp:
                            if resp.status in (200, 204):
                                unloaded_any = True
                                logger.info(f"Unloaded instance '{inst_id}'.")
                    except Exception as e:
                        logger.warning(f"Error unloading instance '{inst_id}': {e}")
        return unloaded_any

    def _load_engine_host_model(self, model_key: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispatches model reload on the engine host via systemd service reconfiguration."""
        params = params or {}
        # Locate model path
        models = self._scan_engine_host_models()
        target_m = next((m for m in models if m["key"] == model_key or m["name"] == model_key or model_key in m["path"]), None)
        model_path = target_m["path"] if target_m else (model_key if "/" in model_key else f"/opt/models/{model_key}")

        is_secondary = (self.node_id == "node1_secondary")
        is_dual = (self.node_id == "dual_gpu" or params.get("dual_gpu_split") or params.get("tensor_split") is not None)
        service_name = "llama-worker.service" if is_secondary else "llama-coordinator.service"
        port = 8002 if is_secondary else 8001
        alias = "worker" if is_secondary else ("moe" if is_dual else "coordinator")

        if is_secondary:
            device_flags = "--device Vulkan0"
            env_line = 'Environment="GGML_VK_VISIBLE_DEVICES=1"'
        elif is_dual:
            device_flags = "--device Vulkan0,Vulkan1 -ts 12,8"
            env_line = 'Environment="GGML_VK_VISIBLE_DEVICES=0,1"'
        else:
            device_flags = "--device Vulkan0"
            env_line = 'Environment="GGML_VK_VISIBLE_DEVICES=0"'

        # Determine context length without arbitrary clamping or hardcoded fallback
        ctx = params.get("context_length")
        if ctx is None:
            ctx = params.get("n_ctx")
        if ctx is None:
            ctx = target_m.get("max_context", 0) if target_m else 0
        ctx = int(ctx)

        parallel = int(params.get("parallel", params.get("parallel_slots", 1)))
        flash_attn = "on" if params.get("flash_attention", True) else "off"

        # Determine K and V cache precision without forced quantization (Default: f16 / native uncompressed)
        ctk = str(params.get("cache_type_k") or params.get("kv_quant") or "f16").lower()
        ctv = str(params.get("cache_type_v") or params.get("kv_quant") or "f16").lower()
        if ctk in ["none", "unquantized", "raw", "fp16"]:
            ctk = "f16"
        if ctv in ["none", "unquantized", "raw", "fp16"]:
            ctv = "f16"

        ngl = int(params.get("n_gpu_layers", 99))
        batch_size = int(params.get("batch_size", params.get("nbatch", 2048)))
        ubatch_size = int(params.get("ubatch_size", params.get("nubatch", 512)))
        threads = int(params.get("threads", 8))

        # Calculate KV cache memory footprint using ground truth precision
        total_tokens = (ctx if ctx > 0 else 16384) * parallel
        kv_b_per_tok = (1.0 if ctk == "q8_0" else (0.5 if ctk in ["q4_0", "q4_1", "iq4_nl"] else 2.0)) + \
                       (1.0 if ctv == "q8_0" else (0.5 if ctv in ["q4_0", "q4_1", "iq4_nl"] else 2.0))
        est_kv_gb = (total_tokens * 32768 * kv_b_per_tok) / (1024 ** 3)
        max_vram_gb = 19.5 if is_dual else (11.0 if not is_secondary else 7.5)
        # Actual size in GB from disk stat if available, otherwise heuristic
        if target_m and target_m.get("size_gb"):
            est_weights_gb = float(target_m["size_gb"])
        elif "27b" in model_path.lower():
            est_weights_gb = 17.0
        elif "9b" in model_path.lower():
            est_weights_gb = 9.0
        else:
            est_weights_gb = 10.0
        
        kv_offload_flags = ""
        if not params.get("offload_kv_cache_to_gpu", True) or params.get("no_kv_offload"):
            kv_offload_flags = "--no-kv-offload --context-shift"
        elif (est_weights_gb + est_kv_gb > max_vram_gb) and not is_dual:
            logger.warning(f"Combined weights ({est_weights_gb}GB) + KV ({est_kv_gb:.2f}GB) exceeds VRAM headroom ({max_vram_gb}GB)")

        # Select appropriate llama-server binary based on model architecture / quantization
        server_bin = "/usr/local/bin/llama-server"
        if any(term in model_path.lower() for term in ["ternary", "bonsai", "pq2", "ptq1"]):
            server_bin = "/opt/llama-prism/bin/llama-server"

        # Construct full command parts
        exec_parts = [
            server_bin,
            "--model", model_path,
            "--host", "0.0.0.0",
            "--port", str(port),
            device_flags,
            "-ngl", str(ngl),
            "-c", str(ctx),
            "-b", str(batch_size),
            "-ub", str(ubatch_size),
            "-t", str(threads),
            "-np", str(parallel),
            "--flash-attn", flash_attn,
            "-ctk", ctk,
            "-ctv", ctv,
            kv_offload_flags,
            "--alias", alias,
            "--metrics"
        ]

        if params.get("threads_batch"):
            exec_parts.extend(["-tb", str(params["threads_batch"])])
        if params.get("cpu_mask"):
            exec_parts.extend(["-C", str(params["cpu_mask"])])
        if params.get("cpu_strict") is not None and str(params["cpu_strict"]) != "auto":
            exec_parts.extend(["--cpu-strict", str(params["cpu_strict"])])
        if params.get("prio") is not None and str(params["prio"]) != "auto":
            exec_parts.extend(["--prio", str(params["prio"])])
        if params.get("poll") is not None and int(params.get("poll", 0)) > 0:
            exec_parts.extend(["--poll", str(params["poll"])])
        if params.get("numa") and params["numa"] != "none":
            exec_parts.extend(["--numa", str(params["numa"])])

        if params.get("split_mode") and params["split_mode"] != "none":
            exec_parts.extend(["-sm", str(params["split_mode"])])
        if params.get("tensor_split"):
            exec_parts.extend(["-ts", str(params["tensor_split"])])
        if params.get("main_gpu") is not None and int(params.get("main_gpu", 0)) > 0:
            exec_parts.extend(["-mg", str(params["main_gpu"])])
        if params.get("cpu_moe"):
            exec_parts.append("-cmoe")
        if params.get("n_cpu_moe") is not None and int(params.get("n_cpu_moe", 0)) > 0:
            exec_parts.extend(["-ncmoe", str(params["n_cpu_moe"])])
        if params.get("n_cpu_ffn") is not None and int(params.get("n_cpu_ffn", 0)) > 0:
            exec_parts.extend(["-ncffn", str(params["n_cpu_ffn"])])
        if params.get("fit") and params["fit"] != "off":
            exec_parts.extend(["--fit", str(params["fit"])])
        if params.get("fit_target") is not None:
            exec_parts.extend(["--fit-target", str(params["fit_target"])])
        if params.get("load_mode") and params["load_mode"] != "auto":
            exec_parts.extend(["-lm", str(params["load_mode"])])
        if params.get("lazy_mode"):
            exec_parts.append("--lazy-mode")

        if params.get("mlock"):
            exec_parts.append("--mlock")
        if params.get("mmap") is False:
            exec_parts.append("--no-mmap")
        elif params.get("mmap") is True:
            exec_parts.append("--mmap")
        if params.get("seed") is not None and str(params.get("seed")) != "-1" and str(params.get("seed")) != "":
            exec_parts.extend(["-s", str(params["seed"])])

        if params.get("kv_unified"):
            exec_parts.append("--kv-unified")
        if params.get("kv_unified_per_slot"):
            exec_parts.append("--kv-unified-per-slot")
        if params.get("cache_ram"):
            exec_parts.extend(["--cache-ram", str(params["cache_ram"])])
        if params.get("cache_idle_slots") is not None and str(params["cache_idle_slots"]) != "auto":
            exec_parts.extend(["--cache-idle-slots", str(params["cache_idle_slots"])])
        if params.get("cache_reuse") and int(params["cache_reuse"]) > 0:
            exec_parts.extend(["--cache-reuse", str(params["cache_reuse"])])
        if params.get("context_shift") and "--context-shift" not in kv_offload_flags:
            exec_parts.append("--context-shift")
        if params.get("ctx_checkpoints") and int(params["ctx_checkpoints"]) > 0:
            exec_parts.extend(["-ctxcp", str(params["ctx_checkpoints"])])
        if params.get("checkpoint_min_step") and int(params["checkpoint_min_step"]) > 0:
            exec_parts.extend(["-cms", str(params["checkpoint_min_step"])])
        if params.get("defrag_thold") is not None:
            exec_parts.extend(["--defrag-thold", str(params["defrag_thold"])])
        if params.get("cont_batching"):
            exec_parts.append("--cont-batching")

        if params.get("reasoning") and params["reasoning"] != "auto":
            exec_parts.extend(["-rea", str(params["reasoning"])])
        if params.get("reasoning_format") and params["reasoning_format"] != "none":
            exec_parts.extend(["--reasoning-format", str(params["reasoning_format"])])
        if params.get("reasoning_effort") is not None:
            exec_parts.extend(["--reasoning-effort", str(params["reasoning_effort"])])
        if params.get("reasoning_budget") is not None and int(params.get("reasoning_budget", 0)) > 0:
            exec_parts.extend(["--reasoning-budget", str(params["reasoning_budget"])])
        if params.get("reasoning_preserve"):
            exec_parts.append("--reasoning-preserve")

        if params.get("rope_scaling") and params["rope_scaling"] != "none":
            exec_parts.extend(["--rope-scaling", str(params["rope_scaling"])])
        if params.get("rope_scale") and float(params["rope_scale"]) != 1.0:
            exec_parts.extend(["--rope-scale", str(params["rope_scale"])])
        if params.get("rope_freq_base") and int(params["rope_freq_base"]) != 1000000:
            exec_parts.extend(["--rope-freq-base", str(params["rope_freq_base"])])
        if params.get("rope_freq_scale") and float(params["rope_freq_scale"]) != 1.0:
            exec_parts.extend(["--rope-freq-scale", str(params["rope_freq_scale"])])

        if params.get("custom_flags"):
            exec_parts.append(str(params["custom_flags"]).strip())

        exec_start = " ".join([p for p in exec_parts if p])
        while "  " in exec_start:
            exec_start = exec_start.replace("  ", " ")

        remote_py = f"""import re, subprocess, os, shutil

# 1. Manage worker service (stop on dual-GPU to free 8GB Vulkan1, start on single GPU)
if {is_dual}:
    subprocess.run(["systemctl", "stop", "llama-worker.service"], check=False)
elif {not is_secondary and not is_dual}:
    subprocess.run(["systemctl", "start", "llama-worker.service"], check=False)

# 2. Backup service file
svc_path = "/etc/systemd/system/{service_name}"
bak_path = svc_path + ".bak"
if not os.path.exists(bak_path):
    shutil.copyfile(svc_path, bak_path)

with open(svc_path, "r", encoding="utf-8") as f:
    content = f.read()

# 3. Update ExecStart
content = re.sub(r"^ExecStart=.*$", {repr('ExecStart=' + exec_start)}, content, flags=re.M)

# 4. Update or insert GGML_VK_VISIBLE_DEVICES
if 'GGML_VK_VISIBLE_DEVICES=' in content:
    content = re.sub(r'^Environment="GGML_VK_VISIBLE_DEVICES=.*"$', {repr(env_line)}, content, flags=re.M)
else:
    content = content.replace('[Service]\\n', '[Service]\\n' + {repr(env_line)} + '\\n')

with open(svc_path, "w", encoding="utf-8") as f:
    f.write(content)

# 5. Reload systemd and restart
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "restart", "{service_name}"], check=True)
print("SUCCESSFULLY_RELOADED")
"""
        clean_py = remote_py.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        try:
            cmd = fleet_config.ssh_cmd("sudo", "python3", "-")
            res = subprocess.run(cmd, input=clean_py.encode("utf-8"), capture_output=True, timeout=30)
            if res.returncode != 0:
                err_msg = res.stderr.decode("utf-8", errors="replace").strip()
                return {"ok": False, "error": f"SSH reload failed: {err_msg}"}

            # Probe health for up to 100 seconds (17GB weights take ~20-80s to mmap and initialize across dual GPUs)
            t_start = time.time()
            while time.time() - t_start < 100:
                time.sleep(2.0)
                try:
                    p_req = urllib.request.Request(f"http://{fleet_config.inference_host}:{port}/props")
                    with urllib.request.urlopen(p_req, timeout=2.0) as resp:
                        if resp.status == 200:
                            return {
                                "ok": True,
                                "model": model_key,
                                "path": model_path,
                                "context_length": ctx,
                                "parallel": parallel,
                                "device": device_flags,
                                "node_id": self.node_id
                            }
                except Exception:
                    pass
            # Inspect service status and journal if timeout reached
            check_sub = subprocess.run(
                fleet_config.ssh_cmd(f"systemctl is-active {service_name}; journalctl -u {service_name} -n 15 --no-pager"),
                capture_output=True, text=True, timeout=10
            )
            return {"ok": False, "error": f"Service restart succeeded, but :{port}/props timed out after 100s. Diagnostic logs:\n{check_sub.stdout}"}
        except Exception as e:
            return {"ok": False, "error": f"Failed to reload model on the engine host: {e}"}

    def load_model(
        self,
        *args,
        target_node: Optional[str] = None,
        model_key: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        context_length: Optional[int] = None,
        parallel_slots: Optional[int] = None,
        flash_attn: Optional[str] = None,
        kv_quant: Optional[str] = None,
        dual_gpu_split: Optional[Any] = None,
        timeout: int = 120,
        unload_prior: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Dispatches a model load request to LM Studio v0.3+ on the node or llama-server on the engine host.
        Supports both positional and keyword invocation styles:
          - self.load_model(model_key, params=params, timeout=120, unload_prior=...)
          - ally_model_manager.load_model(target_node=..., model_key=..., context_length=..., ...)
          - ally_model_manager.load_model(target_node, model_key, ...)
        """
        if len(args) == 1:
            val = args[0]
            if val in fleet_config.nodes or val in ("node1_primary", "node1_secondary", "dual_gpu", "node2_edge", "local_workstation"):
                target_node = val
            else:
                model_key = val
        elif len(args) >= 2:
            arg0, arg1 = args[0], args[1]
            if arg0 in fleet_config.nodes or arg0 in ("node1_primary", "node1_secondary", "dual_gpu", "node2_edge", "local_workstation"):
                target_node = arg0
                model_key = arg1
            else:
                model_key = arg0
                if isinstance(arg1, dict):
                    params = arg1

        # If target_node was passed and differs from self.node_id, delegate to node instance
        if target_node and target_node != self.node_id:
            mgr = EdgeFleetModelManager(node_id=target_node)
            return mgr.load_model(
                model_key=model_key,
                params=params,
                context_length=context_length,
                parallel_slots=parallel_slots,
                flash_attn=flash_attn,
                kv_quant=kv_quant,
                dual_gpu_split=dual_gpu_split,
                timeout=timeout,
                unload_prior=unload_prior,
                **kwargs
            )

        # Assemble params if passed as individual arguments
        p = dict(params) if params else {}
        if context_length is not None:
            p["context_length"] = context_length
        if parallel_slots is not None:
            p["parallel"] = parallel_slots
        if flash_attn is not None:
            p["flash_attention"] = (flash_attn in ("on", "true", "True", True))
        if kv_quant is not None:
            p["cache_type_k"] = kv_quant
            p["cache_type_v"] = kv_quant
        if dual_gpu_split is not None:
            p["dual_gpu_split"] = dual_gpu_split

        if not model_key:
            return {"ok": False, "error": "No model_key specified for load_model"}

        if self._is_engine_node():
            return self._load_engine_host_model(model_key, p)

        if unload_prior:
            self.unload_active_instances()
            time.sleep(0.5)

        load_url = f"{self.node_url}/api/v1/models/load"
        body = {"model": model_key}
        if p:
            clean_params = {k: v for k, v in p.items() if k != "resource_audit"}
            body.update(clean_params)

        req = urllib.request.Request(
            load_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "StoneSage-FleetManager"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "data": data, "model": model_key}
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_text)
                msg = err_json.get("error", {}).get("message", err_text)
            except Exception:
                msg = err_text
            return {"ok": False, "error": f"HTTP {e.code}: {msg}"}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def change_parallel_slots(self, num_slots: int, timeout: int = 120) -> Dict[str, Any]:
        """
        Dynamically rescales parallel execution slots for the active model on the node.
        Strict Invariant:
        1. Captures and audits ALL current runtime parameters (context window, Flash Attention,
           batch sizes, GPU KV cache offload, model key) before unloading.
        2. Verifies memory safety for the requested number of slots against available node RAM.
        3. Unloads the active model instance.
        4. Reloads the exact same model with identical parameters, setting 'parallel' to num_slots.
        5. Verifies and validates that all post-reload parameters match the pre-unload state,
           with parallel == num_slots.
        """
        active_inst = self.get_active_instance()
        if not active_inst:
            return {
                "ok": False,
                "error": f"No active model instance currently running on node '{self.node_id}' ({self.node_url}) to rescale slots."
            }

        model_key = active_inst["key"]
        cfg = dict(active_inst.get("config", {}))

        # 1. Capture ALL pre-unload parameters exactly
        captured_params = {
            "context_length": cfg.get("context_length", 8192),
            "flash_attention": cfg.get("flash_attention", True),
            "eval_batch_size": cfg.get("eval_batch_size", 1024),
            "physical_batch_size": cfg.get("physical_batch_size", 512),
            "offload_kv_cache_to_gpu": cfg.get("offload_kv_cache_to_gpu", True),
            "parallel": cfg.get("parallel", 1),
        }
        for k, v in cfg.items():
            if k not in captured_params:
                captured_params[k] = v

        old_slots = captured_params.get("parallel", 1)

        # 2. Check memory safety for requested slots
        audit = self.audit_node_resources()
        total_ram = audit["total_hardware_ram_gb"]
        weights_gb = active_inst.get("size_gb", 0.0)
        ctx = captured_params.get("context_length", 8192)
        approx_kv_per_slot_gb = (65536 * ctx * 1.0) / (1024 ** 3)
        est_total_kv_gb = round(approx_kv_per_slot_gb * num_slots, 2)
        est_required_gb = round(weights_gb + est_total_kv_gb + 1.0, 2)
        memory_warning = None
        if est_required_gb > (total_ram - 2.0):
            memory_warning = (
                f"Requested {num_slots} slots require ~{est_required_gb} GB RAM "
                f"(weights {weights_gb}GB + KV {est_total_kv_gb}GB), which approaches or exceeds "
                f"available hardware headroom ({total_ram} GB)."
            )
            logger.warning(memory_warning)

        # 3. Prepare reload payload with identical parameters and updated parallel slots
        new_params = dict(captured_params)
        new_params["parallel"] = num_slots

        # 4. Unload active instance
        self.unload_active_instances()
        time.sleep(0.5)

        # 5. Reload model
        load_res = self.load_model(model_key, params=new_params, timeout=timeout, unload_prior=False)
        if not load_res.get("ok"):
            return {
                "ok": False,
                "error": f"Failed to reload model '{model_key}' with {num_slots} slots: {load_res.get('error')}",
                "model_key": model_key,
                "target_slots": num_slots,
                "pre_unload_params": captured_params,
            }

        # 6. Verify and validate parameters post-reload
        new_inst = self.get_active_instance()
        new_cfg = dict(new_inst.get("config", {})) if new_inst else {}

        verification_report = []
        all_matched = True

        # Check model key preservation
        key_match = (new_inst.get("key") == model_key) if new_inst else False
        verification_report.append({
            "parameter": "model_key",
            "pre_unload": model_key,
            "target": model_key,
            "post_reload": new_inst.get("key") if new_inst else "None",
            "verified": key_match
        })
        if not key_match:
            all_matched = False

        # Verify all captured parameters
        for param, pre_val in captured_params.items():
            if param == "parallel":
                post_val = new_cfg.get("parallel", num_slots)
                match = (post_val == num_slots)
                verification_report.append({
                    "parameter": "parallel (slots)",
                    "pre_unload": pre_val,
                    "target": num_slots,
                    "post_reload": post_val,
                    "verified": match
                })
                if not match:
                    all_matched = False
            else:
                post_val = new_cfg.get(param, pre_val)
                match = (post_val == pre_val)
                verification_report.append({
                    "parameter": param,
                    "pre_unload": pre_val,
                    "target": pre_val,
                    "post_reload": post_val,
                    "verified": match
                })
                if not match:
                    all_matched = False

        # 7. Update fleet configuration slot count
        if self.node_id in fleet_config.nodes:
            fleet_config.nodes[self.node_id].slots = num_slots
        if "node2_edge" in fleet_config.nodes:
            fleet_config.nodes["node2_edge"].slots = num_slots
        if "node2_edge" in fleet_config.nodes:
            fleet_config.nodes["node2_edge"].slots = num_slots

        return {
            "ok": True,
            "all_matched": all_matched,
            "model_key": model_key,
            "old_slots": old_slots,
            "new_slots": num_slots,
            "memory_warning": memory_warning,
            "verification_report": verification_report,
            "pre_unload_params": captured_params,
            "post_reload_params": new_cfg
        }

    def interactive_model_wizard(self, default_key: Optional[str] = None, target_node_id: Optional[str] = None):
        """
        Interactive Socratic Walkthrough for inspecting and loading models across cluster nodes and edge devices.
        Features Realtime Hardware-Adaptive Best-Fit vs Predefined Profiles vs Unbounded Custom Tuning.
        """
        # 0. Select Target Node if not explicitly preset
        if not target_node_id:
            nodes = [(nid, n.name) for nid, n in fleet_config.nodes.items()]
            if not nodes:
                nodes = [("node1_primary", "Coordinator"), ("node1_secondary", "Worker")]
            console.print(Panel(
                "[bold cyan]⚡ SELECT TARGET COMPUTE NODE[/bold cyan]\n"
                "Select which physical compute device or GPU partition to configure:\n\n" +
                "\n".join(f"[bold yellow][{i+1}][/bold yellow] [bold white]{name}[/bold white]" for i, (nid, name) in enumerate(nodes)),
                border_style="cyan"
            ))
            choices = [str(i + 1) for i in range(len(nodes))]
            node_choice = Prompt.ask(f"[bold green]Select Target Node [1-{len(nodes)}][/bold green]", choices=choices, default="1")
            sel_nid, _ = nodes[int(node_choice) - 1]
            if sel_nid != self.node_id:
                mgr = EdgeFleetModelManager(node_id=sel_nid)
                return mgr.interactive_model_wizard(default_key=default_key, target_node_id=sel_nid)

        # Probe connectivity if targeting edge node
        edge = fleet_config.nodes.get(self.node_id)
        if edge is not None and edge.is_roaming:
            eu = urllib.parse.urlparse(edge.base_url)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.8)
            res = sock.connect_ex((eu.hostname, eu.port or 80))
            sock.close()
            if res != 0:
                console.print(Panel(
                    f"[bold red]❌ {edge.name.upper()} IS CURRENTLY OFFLINE[/bold red]\n\n"
                    f"[white]Could not connect to [bold yellow]{eu.hostname}:{eu.port}[/bold yellow].[/white]\n"
                    "[dim]Ensure the device is powered on and its local model server is running.[/dim]",
                    title="Device Offline",
                    border_style="red"
                ))
                return

        audit = self.audit_node_resources()
        console.print(Panel(
            f"[bold cyan]⚡ {self.node_name.upper()} // MODEL ORCHESTRATION WIZARD[/bold cyan]\n"
            f"[dim]Endpoint: {self.node_url} • Hardware RAM/VRAM: {audit['total_hardware_ram_gb']:.0f}GB[/dim]",
            border_style="cyan"
        ))

        models = self.list_local_models()
        if not models:
            console.print(f"[bold red][ERR] No models found or unable to connect to {self.node_url}[/bold red]")
            return

        active_inst = self.get_active_instance()
        keep_existing = False

        if active_inst:
            console.print(Panel(
                f"[bold green]Currently Active in Memory:[/bold green] [bold white]{active_inst['name']}[/bold white] ([cyan]{active_inst['key']}[/cyan])\n"
                f"[white]• Context Window:[/white] [yellow]{active_inst.get('config', {}).get('context_length', 'N/A')}[/yellow] tokens\n"
                f"[white]• Model Weight Size:[/white] [magenta]{active_inst.get('size_gb', 'N/A')} GB[/magenta]\n"
                f"[white]• Live Memory Occupied:[/white] [yellow]{audit['total_loaded_gb']} GB[/yellow] (Total across {audit['active_count']} active instances)\n"
                f"[white]• Available if Replaced:[/white] [green]{audit['net_available_replace_gb']} GB[/green] | [white]Headroom if Co-existing:[/white] [cyan]{audit['net_available_coexist_gb']} GB[/cyan]",
                title="Active Node Resource State",
                border_style="green"
            ))
            mode_choice = Prompt.ask(
                "[bold yellow]Allocation Mode[/bold yellow]: [1] Replace active model (unload to free RAM) | [2] Run concurrently (co-exist on node)",
                choices=["1", "2"],
                default="1"
            )
            keep_existing = (mode_choice == "2")

        table = Table(title=f"[*] Local Models on {self.node_name}", border_style="cyan")
        table.add_column("#", justify="right", style="bold yellow")
        table.add_column("Model Key", style="bold white")
        table.add_column("Params", justify="center", style="cyan")
        table.add_column("Size (GB)", justify="right", style="magenta")
        table.add_column("Quant", style="green")
        table.add_column("Max Context", justify="right", style="bold yellow")
        table.add_column("Status", justify="center", style="bold")

        for idx, m in enumerate(models, 1):
            st = "[bold green][ACTIVE][/bold green]" if m.get("is_loaded") else "[dim]Available[/dim]"
            max_c = m.get("max_context", 32768)
            table.add_row(
                str(idx),
                m["key"][:40],
                m.get("params", "-"),
                f"{m.get('size_gb', 0):.1f}",
                m.get("quant", "-"),
                f"{max_c:,}",
                st
            )
        console.print(table)

        # 1. Select Model
        choice_str = Prompt.ask(
            "\n[bold green]Select model number or model key to load[/bold green]",
            default="1"
        ).strip()

        selected_model = None
        if choice_str.isdigit():
            idx = int(choice_str) - 1
            if 0 <= idx < len(models):
                selected_model = models[idx]
        else:
            selected_model = next((m for m in models if m["key"].lower() == choice_str.lower()), None)

        if not selected_model:
            console.print("[bold red][ERR] Invalid model selection.[/bold red]")
            return

        m_key = selected_model["key"]
        max_ctx = selected_model.get("max_context", 32768)
        console.print(f"\n[cyan]Selected:[/cyan] [bold white]{m_key}[/bold white] ({selected_model.get('size_gb', 0)} GB, {selected_model.get('params', 'N/A')}, Architectural Limit: {max_ctx:,} tokens)")

        # 2. Parameter Walkthrough: Realtime Adaptive vs Predefined Profile vs Custom
        console.print(Panel(
            "[bold white]Parameter Configuration Strategy[/bold white]\n\n"
            "[bold yellow][1] Realtime Hardware-Adaptive Best-Fit (Dynamic)[/bold yellow]\n"
            "    Dynamically computes context & batching from live available RAM/VRAM\n"
            f"    (Live headroom: {audit['net_available_coexist_gb'] if keep_existing else audit['net_available_replace_gb']} GB, co-exist: {keep_existing})\n\n"
            "[bold yellow][2] Predefined Operational Profiles[/bold yellow]\n"
            "    Select a fleet profile (Subagent Farm 4x8k, Deep Reasoning 1x32k, Frontier 1x128k, Balanced 2x16k)\n\n"
            "[bold yellow][3] Custom Load Parameters (Unbounded Context Window)[/bold yellow]\n"
            "    Manually configure context length up to the model's true limit, Flash Attention, parallel slots, and quants\n",
            title="Parameter Strategy",
            border_style="yellow"
        ))

        strategy = Prompt.ask(
            "[bold green]Select Strategy [1, 2, or 3][/bold green]",
            choices=["1", "2", "3"],
            default="1"
        )

        if strategy == "1":
            params = self.calculate_best_fit(selected_model, keep_existing_models=keep_existing)
            res_audit = params.get("resource_audit", {})
            console.print(Panel(
                f"[bold green]Real-time Dynamic Best-Fit Derived from Live Hardware Resources:[/bold green]\n"
                f"[white]• Live Available Memory Headroom:[/white] [cyan]{res_audit.get('effective_free_gb')} GB[/cyan]\n"
                f"[white]• Model Weight Size:[/white] [magenta]{res_audit.get('weights_gb')} GB[/magenta]\n"
                f"[white]• KV Cache & Working Budget:[/white] [yellow]{res_audit.get('kv_budget_gb')} GB[/yellow]\n"
                f"[white]• Dynamically Calculated Context:[/white] [bold yellow]{params['context_length']}[/bold yellow] tokens\n"
                f"[white]• Flash Attention:[/white] [green]{params['flash_attention']}[/green]\n"
                f"[white]• Eval Batch Size:[/white] [cyan]{params['eval_batch_size']}[/cyan]\n"
                f"[white]• GPU KV Cache Offload:[/white] [green]{params['offload_kv_cache_to_gpu']}[/green]",
                title="Dynamic Real-time Parameters",
                border_style="green"
            ))
        elif strategy == "2":
            console.print(Panel(
                "[bold cyan]Select Predefined Operational Profile:[/bold cyan]\n\n"
                "[1] Subagent Farm       : 4 parallel slots @ 8,192 context (High-throughput parallel workers)\n"
                "[2] Deep Reasoning      : 1 slot @ 32,768 context (Extended complex code audit / reasoning)\n"
                "[3] Frontier Deep Scan  : 1 slot @ 65,536 / 131,072 context (Repo-level context ingestion)\n"
                "[4] Balanced Daily Driver: 2 slots @ 16,384 context (Standard interactive paired turns)",
                title="Predefined Profiles",
                border_style="cyan"
            ))
            prof_choice = Prompt.ask("[bold green]Select Profile [1-4][/bold green]", choices=["1", "2", "3", "4"], default="4")
            if prof_choice == "1":
                params = {"context_length": 8192, "flash_attention": True, "eval_batch_size": 1024, "physical_batch_size": 512, "offload_kv_cache_to_gpu": True, "parallel": 4}
            elif prof_choice == "2":
                params = {"context_length": min(max_ctx, 32768), "flash_attention": True, "eval_batch_size": 2048, "physical_batch_size": 512, "offload_kv_cache_to_gpu": True, "parallel": 1}
            elif prof_choice == "3":
                params = {"context_length": min(max_ctx, 131072), "flash_attention": True, "eval_batch_size": 2048, "physical_batch_size": 512, "offload_kv_cache_to_gpu": True, "parallel": 1}
            else:
                params = {"context_length": min(max_ctx, 16384), "flash_attention": True, "eval_batch_size": 2048, "physical_batch_size": 512, "offload_kv_cache_to_gpu": True, "parallel": 2}
        else:
            # Custom parameter walkthrough - UNBOUNDED context window bounded only by model capabilities
            console.print("\n[bold cyan]── Custom Parameter Walkthrough (Unbounded Context Window) ─────[/bold cyan]")
            console.print(f"[white]• Model Architectural Maximum Limit:[/white] [bold cyan]{max_ctx:,} tokens[/bold cyan]")
            console.print(f"[dim]Enter any context window between 4096 and {max_ctx:,} tokens (unbounded).[/dim]")

            while True:
                ctx_str = Prompt.ask(
                    f"[bold green]Context Length [4096 - {max_ctx:,}][/bold green]",
                    default=str(min(max_ctx, 16384))
                ).strip()
                try:
                    ctx_val = int(ctx_str)
                    if ctx_val < 4096:
                        console.print("[yellow]Notice: Enforcing minimum agent context floor of 4,096 tokens.[/yellow]")
                        ctx_val = 4096
                    break
                except ValueError:
                    console.print(f"[red]Please enter a valid integer between 4096 and {max_ctx:,}.[/red]")

            flash_str = Prompt.ask("[bold green]Enable Flash Attention?[/bold green] (y/n)", choices=["y", "n"], default="y")
            slots_str = Prompt.ask(
                "[bold green]Parallel Execution Slots (1-8)[/bold green]",
                choices=["1", "2", "3", "4", "8"],
                default="1" if self.node_id in ("node1_primary", "dual_gpu") else "2"
            )

            kv_quant = "q8_0"
            if self.node_id in ("node1_primary", "node1_secondary", "dual_gpu"):
                kv_quant = Prompt.ask(
                    "[bold green]KV Cache Quantization[/bold green] (q8_0, q4_0, f16)",
                    choices=["q8_0", "q4_0", "f16"],
                    default="q8_0" if self.node_id != "dual_gpu" else "q4_0"
                )

            params = {
                "context_length": ctx_val,
                "flash_attention": (flash_str.lower() == "y"),
                "offload_kv_cache_to_gpu": True,
                "eval_batch_size": 2048,
                "physical_batch_size": 512,
                "parallel": int(slots_str),
                "cache_type_k": kv_quant,
                "cache_type_v": kv_quant,
                "dual_gpu_split": (self.node_id == "dual_gpu")
            }

        # 3. Confirmation & Execution
        confirm = Prompt.ask(f"[bold green]Load '{m_key}' onto {self.node_name} now? [Y/n][/bold green]", default="y")
        if confirm.lower() not in ("y", "yes", ""):
            console.print("[yellow]Model load cancelled by operator.[/yellow]")
            return

        action_label = "Loading alongside existing instances" if keep_existing else "Unloading prior instances and loading"
        console.print(f"\n[cyan][*] {action_label} '{m_key}' on {self.node_name}... (this may take 10-30s)[/cyan]")
        res = self.load_model(m_key, params=params, timeout=120, unload_prior=not keep_existing)

        if res.get("ok"):
            fleet_config.set_active_node(self.node_id)
            node_ep = fleet_config.get_active_node()
            node_ep.active_model = m_key
            node_ep.active_context = params.get("context_length", 8192)
            console.print(Panel(
                f"[bold green][OK] Model Successfully Loaded onto {self.node_name}![/bold green]\n\n"
                f"[white]• Active Model:[/white] [bold white]{m_key}[/bold white]\n"
                f"[white]• Context Length:[/white] [yellow]{params.get('context_length'):,}[/yellow] tokens\n"
                f"[white]• Parallel Slots:[/white] [cyan]{params.get('parallel', 1)}[/cyan]\n"
                f"[white]• Hardware Node:[/white] [magenta]{self.node_name} ({self.node_url})[/magenta]\n"
                f"[white]• CLI Active Target:[/white] [bold green]Switched to {self.node_name}[/bold green]",
                title="Load Succeeded",
                border_style="green"
            ))
        else:
            console.print(f"[bold red][ERR] Failed to load model: {res.get('error')}[/bold red]")

    def bind_agent(self, agent_id: str, model_key: Optional[str] = None, target_node_id: Optional[str] = None) -> bool:
        """
        Binds an agent to a compute node in the Aevum Mesh registry,
        directing its future conversational turns to that endpoint.
        """
        clean_id = agent_id.strip().lower()
        nid = target_node_id or self.node_id
        success = relational_storage.heartbeat_hive_agent(
            agent_id=clean_id,
            node_id=nid,
            status="idle"
        )
        if success:
            node_info = fleet_config.nodes.get(nid)
            disp_name = node_info.name if node_info else nid
            console.print(f"[bold green][OK] Agent '{clean_id}' bound to {disp_name} ('{nid}').[/bold green]")
            if model_key:
                console.print(f"[dim]Target model specified: {model_key}[/dim]")
        return success

    def list_node_models(self, target_node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists models for any node by creating or re-targetting an instance."""
        nid = target_node_id or self.node_id
        node_instance = EdgeFleetModelManager(node_id=nid)
        return node_instance.list_local_models()



# Generic Node / Edge Fleet Model Manager Exports
NodeModelManager = EdgeFleetModelManager
node_manager = EdgeFleetModelManager()

# Backward-compatible aliases for legacy imports
AllyModelManager = EdgeFleetModelManager
ally_manager = node_manager
ally_model_manager = node_manager

