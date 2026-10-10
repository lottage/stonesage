"""
Unified Harness Backend Daemon (Duplex WebSockets & REST Server).
Coordinates multi-node slot scheduling, stateful agent loops, multiplexed reasoning streams,
and cross-container Proxmox data fabric queries.
"""

import os
import sys
import json
import time
import asyncio
import logging
from typing import Dict, Any, List, Optional
import urllib.request

from .config import fleet_config
from .core.node_scheduler import node_scheduler
from .core.offload_calc import offload_engine
from .core.speculative import speculative_engine
from .core.nudge_tool import harness_nudge
from .core.openclaw_engine import openclaw_engine
from .core.context_fabric import context_fabric
from .data_fabric.pg_storage import relational_storage
from .data_fabric.valkey_amem import valkey_amem
from .data_fabric.obsidian_gateway import obsidian_gateway
from .edge_fleet.roaming_handover import roaming_protocol
from .edge_fleet.ota_reload import ota_distributor
from .core.terminal_pty import pty_manager

logger = logging.getLogger("Harness.Server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [HarnessServer]: %(message)s")

try:
    import websockets
except ImportError:
    websockets = None

class HarnessServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8088):
        self.host = host
        self.port = port
        self.connected_clients = set()
        self._running = False

    async def handle_websocket(self, websocket):
        """Duplex WebSocket handler for streaming reasoning and receiving out-of-band commands."""
        self.connected_clients.add(websocket)
        client_id = f"client_{id(websocket)}"
        logger.info(f"Client {client_id} connected to Harness WebSocket.")

        loop = asyncio.get_running_loop()
        term_session_id = client_id

        def pty_out(text: str):
            try:
                asyncio.run_coroutine_threadsafe(
                    websocket.send(json.dumps({"type": "output", "data": text})),
                    loop
                )
            except Exception:
                pass

        try:
            # Send initial fleet status
            await websocket.send(json.dumps({
                "type": "fleet_status",
                "nodes": node_scheduler.get_fleet_status(),
                "timestamp": time.time()
            }))

            async for message in websocket:
                try:
                    data = json.loads(message)
                except Exception:
                    continue

                action = data.get("action") or data.get("type")

                if action == "chat":
                    asyncio.create_task(self._stream_chat(websocket, data))
                elif action in ("terminal_init", "pty_init"):
                    term_session_id = data.get("session_id") or client_id
                    target_host = data.get("target_host")
                    session = pty_manager.get_or_create_session(
                        session_id=term_session_id,
                        on_output=pty_out,
                        target_host=target_host
                    )
                    history = session.get_scrollback()
                    if history:
                        await websocket.send(json.dumps({
                            "type": "scrollback",
                            "session_id": term_session_id,
                            "data": history
                        }))
                    await websocket.send(json.dumps({
                        "type": "terminal_ready",
                        "session_id": term_session_id,
                        "target_host": target_host or "local"
                    }))
                elif action == "input":
                    text = data.get("data", "")
                    term_session_id = data.get("session_id") or term_session_id
                    session = pty_manager.get_or_create_session(
                        session_id=term_session_id,
                        on_output=pty_out,
                        target_host=data.get("target_host")
                    )
                    session.write_input(text)
                elif action == "resize":
                    sid = data.get("session_id") or term_session_id
                    rows = int(data.get("rows", 24))
                    cols = int(data.get("cols", 80))
                    pty_manager.resize_session(sid, rows=rows, cols=cols)
                elif action == "terminal_close":
                    sid = data.get("session_id") or term_session_id
                    pty_manager.close_session(sid)
                elif action == "nudge":
                    agent_id = data.get("agent_id", "default")
                    directive = data.get("directive")
                    res = harness_nudge.nudge(agent_id=agent_id, directive=directive)
                    await websocket.send(json.dumps({"type": "nudge_result", "result": res}))
                elif action == "build_agent":
                    contract = openclaw_engine.build_contracts(
                        agent_id=data.get("agent_id", "custom-agent"),
                        name=data.get("name", "Custom Agent"),
                        role=data.get("role", "Specialist"),
                        mission=data.get("mission", "Assist user"),
                        autonomy_level=data.get("autonomy", "tiered"),
                        assigned_node=data.get("node", "node2_edge")
                    )
                    await websocket.send(json.dumps({"type": "agent_built", "agent_id": contract.agent_id}))
                elif action == "get_capacity":
                    ctx = data.get("context", 8192)
                    slots = data.get("slots", 1)
                    arch = data.get("arch_type", "9b")
                    quant = data.get("quant", "q4_k_m")
                    vram = float(data.get("target_vram_gb", 12.0))
                    est = offload_engine.estimate(
                        arch_type=arch,
                        quant=quant,
                        context_length=ctx,
                        parallel_slots=slots,
                        target_vram_gb=vram,
                    )
                    await websocket.send(json.dumps({"type": "capacity_estimate", "estimate": est.__dict__}))

        except Exception as e:
            logger.warning(f"WebSocket client error: {e}")
        finally:
            self.connected_clients.discard(websocket)
            logger.info(f"Client {client_id} disconnected.")

    async def _stream_chat(self, websocket, data: Dict[str, Any]):
        """Streams LLM reasoning and output deltas multiplexed over WebSocket."""
        req_id = data.get("id", f"req_{int(time.time()*1000)}")
        agent_id = data.get("agent_id", "coordinator")
        role = data.get("role", "worker")
        messages = data.get("messages", [{"role": "user", "content": data.get("prompt", "")}])

        # Acquire an execution slot on a compute engine or edge node
        lease = await node_scheduler.acquire_slot(agent_id=agent_id, preferred_role=role)
        if not lease:
            await websocket.send(json.dumps({
                "type": "error",
                "id": req_id,
                "error": "All execution slots saturated across fleet."
            }))
            return

        try:
            # Check A-MEM and dynamic context fabric
            user_query = messages[-1].get("content", "") if messages else ""
            has_system = any(m.get("role") == "system" for m in messages)
            if not has_system:
                sys_prompt = context_fabric.compile_dynamic_turn(user_query=user_query, agent_id=agent_id)
                messages = [{"role": "system", "content": sys_prompt}] + messages
            else:
                amem_atom = valkey_amem.format_injection_header(user_query)
                if amem_atom:
                    for m in messages:
                        if m.get("role") == "system":
                            m["content"] = m.get("content", "") + f"\n\n{amem_atom}"
                            break

            async for chunk in lease.client.chat_stream(messages=messages, request_id=req_id):
                payload = {
                    "type": "stream_chunk",
                    "id": req_id,
                    "agent_id": agent_id,
                    "node_id": lease.node_id,
                    "chunk_type": chunk.chunk_type,
                    "content": chunk.content,
                    "tokens_per_sec": chunk.tokens_per_sec,
                    "elapsed_ms": chunk.elapsed_ms,
                    "model": chunk.model_name
                }
                await websocket.send(json.dumps(payload))

        finally:
            await node_scheduler.release_slot(lease.lease_id)

    def run(self):
        if not websockets:
            logger.error("websockets package required. Install via 'pip install websockets'.")
            return
        logger.info(f"⚡ Starting Unified Harness Server on ws://{self.host}:{self.port}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        start_server = websockets.serve(self.handle_websocket, self.host, self.port)
        loop.run_until_complete(start_server)
        loop.run_forever()

harness_server = HarnessServer()

if __name__ == "__main__":
    harness_server.run()
