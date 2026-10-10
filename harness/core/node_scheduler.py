"""
Dynamic Multi-Node Slot Scheduler & Work-Stealing Dispatcher.
Manages concurrent execution slots across the configured compute engines and edge nodes (slot counts are read live).
Supports automatic work-stealing and roaming node failover.
"""

import time
import asyncio
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from ..config import fleet_config, NodeEndpoint
from .llama_client import LlamaClient

logger = logging.getLogger("Harness.Scheduler")

@dataclass
class SlotLease:
    lease_id: str
    node_id: str
    slot_index: int
    agent_id: str
    client: LlamaClient
    acquired_at: float = field(default_factory=time.time)
    task_description: str = ""

class NodeScheduler:
    def __init__(self):
        self.config = fleet_config
        self.clients: Dict[str, LlamaClient] = {}
        self._active_leases: Dict[str, SlotLease] = {}
        self._lock = asyncio.Lock()
        self._init_clients()

    def _init_clients(self):
        for nid, node in self.config.nodes.items():
            self.clients[nid] = LlamaClient(base_url=node.base_url)

    async def acquire_slot(
        self,
        agent_id: str,
        preferred_role: str = "worker",
        task_description: str = "",
        allow_work_stealing: bool = True
    ) -> Optional[SlotLease]:
        """
        Acquires an execution slot on the most suitable available node.
        Prioritizes the edge node pool for parallel subagents,
        and VM 102 Primary Accelerator for coordinator architecture tasks.
        """
        async with self._lock:
            # 1. Evaluate candidate nodes matching preferred role
            candidate_node_ids = []
            if preferred_role == "coordinator":
                candidate_node_ids = ["node1_primary", "node2_edge", "node1_secondary"]
            else:
                # For worker / subagent tasks, prefer the edge node to keep the coordinator free
                candidate_node_ids = ["node2_edge", "node1_secondary", "node1_primary"]

            for nid in candidate_node_ids:
                node = self.config.nodes.get(nid)
                if not node:
                    continue

                # Count active leases for this node
                active_for_node = [l for l in self._active_leases.values() if l.node_id == nid]
                if len(active_for_node) < node.slots:
                    slot_idx = len(active_for_node)
                    lease_id = f"lease_{nid}_{slot_idx}_{int(time.time()*1000)}"
                    client = self.clients[nid]
                    lease = SlotLease(
                        lease_id=lease_id,
                        node_id=nid,
                        slot_index=slot_idx,
                        agent_id=agent_id,
                        client=client,
                        task_description=task_description,
                    )
                    self._active_leases[lease_id] = lease
                    logger.info(
                        f"Allocated slot on '{node.name}' ({nid} slot {slot_idx}/{node.slots}) "
                        f"to agent '{agent_id}'."
                    )
                    return lease

            # 2. If preferred slots full and work-stealing allowed, try any available slot across fleet
            if allow_work_stealing:
                for nid, node in self.config.nodes.items():
                    active_for_node = [l for l in self._active_leases.values() if l.node_id == nid]
                    if len(active_for_node) < node.slots:
                        slot_idx = len(active_for_node)
                        lease_id = f"lease_{nid}_{slot_idx}_{int(time.time()*1000)}"
                        client = self.clients[nid]
                        lease = SlotLease(
                            lease_id=lease_id,
                            node_id=nid,
                            slot_index=slot_idx,
                            agent_id=agent_id,
                            client=client,
                            task_description=task_description,
                        )
                        self._active_leases[lease_id] = lease
                        logger.info(f"Work-stealing activated: Allocated slot on '{node.name}' to '{agent_id}'.")
                        return lease

            logger.warning(f"All slots currently saturated across all nodes for agent '{agent_id}'.")
            return None

    async def release_slot(self, lease_id: str) -> bool:
        """Releases a slot lease back to the node pool."""
        async with self._lock:
            lease = self._active_leases.pop(lease_id, None)
            if lease:
                logger.info(f"Released slot lease '{lease_id}' on node '{lease.node_id}'.")
                return True
            return False

    def get_fleet_status(self, auto_poll: bool = True) -> List[Dict[str, Any]]:
        """Returns live slot occupancy metrics across all nodes."""
        if auto_poll:
            try:
                self.config.poll_node_models()
            except Exception as e:
                logger.debug(f"Auto-poll in get_fleet_status error: {e}")

        status = []
        for nid, node in self.config.nodes.items():
            active_leases = [l for l in self._active_leases.values() if l.node_id == nid]
            model_display = node.active_model
            if not model_display:
                if node.status == "offline":
                    model_display = "[OFFLINE]"
                elif node.status == "online (no models loaded)":
                    model_display = "(no models loaded)"
                else:
                    model_display = "(idle / offline)"

            status.append({
                "node_id": nid,
                "name": node.name,
                "role": node.role,
                "total_slots": node.slots,
                "occupied_slots": len(active_leases),
                "available_slots": max(node.slots - len(active_leases), 0),
                "active_model": model_display,
                "active_agents": [l.agent_id for l in active_leases],
                "is_roaming": node.is_roaming,
                "status": node.status,
            })
        return status

node_scheduler = NodeScheduler()
