"""
Edge Node Registry & Capability Tracker
Tracks distributed edge devices across the homelab and roaming continuum.
Supports handhelds, Raspberry Pis, mini-PCs and other edge endpoints listed in config.json.
"""

import time
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger("harness.edge_fleet.node")


@dataclass
class EdgeNodeCapability:
    device_id: str
    hostname: str
    ip: str
    port: int = 1234
    device_type: str = "edge_handheld"  # "edge_handheld", "sbc", "mini_pc", "mobile"
    compute_backend: str = "vulkan"     # "vulkan", "rocm", "cuda", "cpu"
    total_ram_gb: float = 24.0
    vram_allocated_gb: float = 8.0
    runtime_type: str = "lm-studio"     # "lm-studio", "llama-server", "ollama"
    loaded_model: str = "unknown"
    context_window: int = 8192
    parallel_slots: int = 4
    is_online: bool = False
    battery_pct: Optional[float] = None
    thermal_celsius: Optional[float] = None
    last_seen: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def api_base(self) -> str:
        return f"http://{self.ip}:{self.port}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EdgeNodeRegistry:
    """Manages discovery, capability tracking, and health of edge fleet nodes."""

    def __init__(self, persistence_file: Optional[Path] = None):
        self.persistence_file = persistence_file or Path("edge_nodes.json")
        self._nodes: Dict[str, EdgeNodeCapability] = {}
        self._load()

        # Seed from the edge devices configured in StoneSage's config.json (harness_instances); nothing hardcoded.
        # Model, context and online state stay unknown until the node is actually probed.
        if not self._nodes:
            from urllib.parse import urlparse
            from ..config import fleet_config
            for nid, node in fleet_config.nodes.items():
                if not node.is_roaming:
                    continue
                u = urlparse(node.base_url)
                self.register(EdgeNodeCapability(
                    device_id=nid, hostname=node.name, ip=u.hostname or "", port=u.port or 80,
                    device_type="edge", compute_backend="unknown",
                    total_ram_gb=round(node.total_memory_mb / 1024, 1) if node.total_memory_mb else 0.0,
                    vram_allocated_gb=0.0, runtime_type=node.api_type, loaded_model="unknown",
                    context_window=0, parallel_slots=node.slots, is_online=False))

    def register(self, capability: EdgeNodeCapability) -> None:
        capability.last_seen = time.time()
        self._nodes[capability.device_id] = capability
        self._save()
        logger.info(f"Registered edge node '{capability.device_id}' ({capability.ip}:{capability.port})")

    def heartbeat(self, device_id: str, battery_pct: Optional[float] = None, thermal_celsius: Optional[float] = None) -> bool:
        if device_id in self._nodes:
            node = self._nodes[device_id]
            node.is_online = True
            node.last_seen = time.time()
            if battery_pct is not None:
                node.battery_pct = battery_pct
            if thermal_celsius is not None:
                node.thermal_celsius = thermal_celsius
            return True
        return False

    def mark_offline(self, device_id: str) -> None:
        if device_id in self._nodes:
            self._nodes[device_id].is_online = False

    def get_node(self, device_id: str) -> Optional[EdgeNodeCapability]:
        return self._nodes.get(device_id)

    def list_nodes(self, only_online: bool = False) -> List[EdgeNodeCapability]:
        nodes = list(self._nodes.values())
        if only_online:
            # Stale nodes > 120s without heartbeat marked offline
            now = time.time()
            for n in nodes:
                if now - n.last_seen > 120:
                    n.is_online = False
            return [n for n in nodes if n.is_online]
        return nodes

    def select_optimal_node(self, required_context: int = 4096) -> Optional[EdgeNodeCapability]:
        """Selects the best online edge node with sufficient context headroom and parallel slots."""
        online_nodes = self.list_nodes(only_online=True)
        viable = [n for n in online_nodes if n.context_window >= required_context]
        if not viable:
            return None
        # Sort by available parallel slots descending, then lowest thermal
        return sorted(viable, key=lambda n: (n.parallel_slots, -(n.thermal_celsius or 50)), reverse=True)[0]

    def _save(self) -> None:
        try:
            data = {k: v.to_dict() for k, v in self._nodes.items()}
            self.persistence_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist edge nodes: {e}")

    def _load(self) -> None:
        if self.persistence_file.exists():
            try:
                raw = json.loads(self.persistence_file.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self._nodes[k] = EdgeNodeCapability(**v)
            except Exception as e:
                logger.warning(f"Failed to load edge nodes from {self.persistence_file}: {e}")
