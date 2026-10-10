"""
Dynamic Fleet Configuration & Multi-Node Network Registry.
Zero hardcoded model strings: all models, quants, and context lengths are polled live.
"""

import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("Harness.Config")

@dataclass
class NodeEndpoint:
    node_id: str
    name: str
    base_url: str
    api_type: str = "openai"  # "openai", "llama-server", "lm-studio", "mcp"
    slots: int = 1
    device_name: str = "Unknown Device"
    total_memory_mb: int = 0
    role: str = "worker"
    is_roaming: bool = False
    active_model: Optional[str] = None
    active_context: int = 4096
    status: str = "unknown"

def _stonesage_backend_dir() -> str:
    """StoneSage/backend next to this repo's harness/ (dev), or the backend dir harness/ is installed in (LXC)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.environ.get("STONESAGE_BACKEND", ""), os.path.join(here, "..", "StoneSage", "backend"), os.path.join(here, "..")):
        if cand and os.path.exists(os.path.join(cand, "lodestone", "system_profile.py")):
            return os.path.abspath(cand)
    return ""


def _load_stonesage_config() -> Dict[str, Any]:
    d = _stonesage_backend_dir()
    try:
        with open(os.path.join(d, "config.json"), encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _is_engine_instance(inst: Dict[str, Any]) -> bool:
    url = (inst.get("url") or "").rstrip("/")
    return bool(inst.get("engine")) or url.endswith(":1234") or url.endswith(":11434")


def _live_models() -> List[Dict[str, Any]]:
    """GGUF files on the inference host with header metadata (via StoneSage system_profile); [] if unavailable."""
    d = _stonesage_backend_dir()
    if not d:
        return []
    import sys
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from lodestone import system_profile
        return system_profile.get_models(_load_stonesage_config())
    except Exception as e:
        logger.debug(f"model list unavailable: {e}")
        return []


def _live_profile(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """StoneSage's live system profile (engines, GPUs); None when it can't be built (offline, no SSH)."""
    d = _stonesage_backend_dir()
    if not d:
        return None
    import sys
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from lodestone import system_profile
        return system_profile.get_profile(cfg)
    except Exception as e:
        logger.debug(f"live profile unavailable: {e}")
        return None


def _endpoints():
    """StoneSage's endpoint table (lodestone/endpoints.py): the one place service addresses come from."""
    d = _stonesage_backend_dir()
    import sys
    if d and d not in sys.path:
        sys.path.insert(0, d)
    from lodestone import endpoints
    return endpoints


class FleetConfig:
    def __init__(self):
        # Service addresses: the CLI's own environment variable first, then config.json, then the endpoint table's
        # default (localhost for a one-box install). Nothing about this network is written here.
        self.stonesage_cfg = _load_stonesage_config()
        cfg, ep = self.stonesage_cfg, _endpoints()

        def svc(name: str, *env: str) -> str:
            return next((os.environ[e] for e in env if os.environ.get(e)), "") or ep.url(name, cfg)

        self.coordinator_url = svc("coordinator", "COORDINATOR_URL")
        self.worker_url = svc("worker", "WORKER_URL")
        self.embedder_url = svc("embedder", "EMBEDDER_URL")
        # an edge node only from the environment here; config.json's harness_instances fill it in below
        self.edge_node_url = next((os.environ[e] for e in ("EDGE_NODE_URL", "EDGE_URL", "ALLY_EXTREME_URL", "ALLY_URL")
                                   if os.environ.get(e)), "")
        self.ally_extreme_url = self.edge_node_url  # Backward-compatible alias
        self.ally_x_url = self.edge_node_url        # Backward-compatible alias

        # Storage & Cluster Services
        self.valkey_host = os.environ.get("VALKEY_HOST") or ep.host("valkey", cfg)
        self.valkey_port = int(os.environ.get("VALKEY_PORT") or ep.port("valkey", cfg))
        self.qdrant_url = svc("qdrant", "QDRANT_URL")
        self.couchdb_url = svc("couchdb", "COUCHDB_URL")
        self.postgres_url = os.environ.get("POSTGRES_URL", "")

        # Cluster Bridges & WebSockets
        self.assembly_http = svc("assembly", "ASSEMBLY_HTTP")
        self.assembly_ws = os.environ.get("ASSEMBLY_WS") or self.assembly_http.replace("http", "ws", 1) + "/ws"
        self.cluster_mcp_url = svc("mcp", "CLUSTER_MCP_URL")

        # Home services ("" = not configured)
        self.proxmox_vip = svc("proxmox", "PROXMOX_VIP")
        self.hass_url = svc("homeassistant", "HASS_URL")
        self.immich_url = svc("immich", "IMMICH_URL")
        self.freshrss_url = svc("freshrss", "FRESHRSS_URL")
        self.kavita_url = svc("kavita", "KAVITA_URL")
        # TTS: the voice server first (docs/evals/voice-bench-2026-09-29.md), the old voice host as the fallback
        legacy = ep.url("voice_legacy", cfg)
        self.voice_whisper_url = os.environ.get("VOICE_WHISPER_URL") or (f"{legacy}:8200" if legacy else "")
        self.voice_kokoro_url = svc("voice", "VOICE_KOKORO_URL")
        self.voice_kokoro_fallback_url = os.environ.get("VOICE_KOKORO_FALLBACK_URL") or (f"{legacy}:8300" if legacy else "")

        # Frontier Model Designation
        self.frontier_provider = "antigravity"
        self.frontier_model = "gemini-3.8-flash"

        # Registered node fleet: built from StoneSage's config.json (engine URLs, harness_instances) and filled
        # in with real hardware/model names by refresh_hardware(). Nothing about the user's devices is hardcoded.
        class NodeDict(dict):
            """Dictionary that resolves legacy node ids to their current names."""
            ALIASES = {
                "node2_ally_x": "node2_edge",
                "node2_ally_extreme": "node2_edge",
            }

            def __getitem__(self, key):
                return super().__getitem__(self.ALIASES.get(key, key))

            def get(self, key, default=None):
                return super().get(self.ALIASES.get(key, key), default)

            def __contains__(self, key):
                return super().__contains__(self.ALIASES.get(key, key))

        # host that runs the engines (for SSH: unit files, model files, probes)
        self.inference_host = urllib.parse.urlparse(self.coordinator_url).hostname or "127.0.0.1"
        self.inference_ssh = ep.ssh_target(cfg, host_name=self.inference_host)

        self.nodes: Dict[str, NodeEndpoint] = NodeDict({
            "node1_primary": NodeEndpoint(node_id="node1_primary", name="Coordinator", base_url=self.coordinator_url,
                                          role="coordinator", slots=1, device_name=""),
            "node1_secondary": NodeEndpoint(node_id="node1_secondary", name="Worker", base_url=self.worker_url,
                                            role="worker", slots=1, device_name=""),
        })
        edges = [i for i in self.stonesage_cfg.get("harness_instances", []) if _is_engine_instance(i)]
        for n, inst in enumerate(edges):
            hw = inst.get("hardware") or {}
            nid = "node2_edge" if n == 0 else f"node2_edge_{n + 1}"
            url = inst.get("url", "").rstrip("/")
            self.nodes[nid] = NodeEndpoint(
                node_id=nid, name=inst.get("name", nid), base_url=url if url.endswith("/v1") else url + "/v1",
                api_type="lm-studio" if url.endswith(":1234") else "openai", role="edge_pool", slots=int(hw.get("slots", 1)),
                is_roaming=True, total_memory_mb=int(hw.get("ram_gb", 0)) * 1024,
                device_name=", ".join(str(x) for x in (hw.get("device"), hw.get("cpu"), f"{hw['ram_gb']} GB RAM" if hw.get("ram_gb") else None) if x))
        if edges:
            self.edge_node_url = self.nodes["node2_edge"].base_url
            self.ally_extreme_url = self.ally_x_url = self.edge_node_url  # backward-compatible aliases
        ws = next((i for i in self.stonesage_cfg.get("harness_instances", []) if i.get("id") == "workstation_primary"), None)
        if ws:
            self.nodes["local_workstation"] = NodeEndpoint(
                node_id="local_workstation", name=ws.get("name", "Workstation"),
                base_url=os.environ.get("WORKSTATION_URL", ws.get("url", "").rstrip("/") + "/v1"),
                role="workstation", slots=1, device_name=(ws.get("hardware") or {}).get("device", ""))
        # Boost: free cloud sources behind StoneSage's OpenAI-compatible proxy (keys stay on StoneSage).
        # `/node use boost` in the CLI; model id boost:loops so it counts against the background share.
        boost_cfg = self.stonesage_cfg.get("boost") or {}
        if boost_cfg.get("enabled") and (boost_cfg.get("surfaces") or {}).get("loops"):
            ss_url = (os.environ.get("STONESAGE_URL") or boost_cfg.get("stonesage_url") or ep.url("stonesage", cfg)).rstrip("/")
            self.nodes["boost"] = NodeEndpoint(node_id="boost", name="Boost (free cloud pool)", base_url=ss_url + "/api/boost/v1",
                                               role="boost", slots=1, device_name="free cloud pool", active_model="boost:loops")
        self.active_node_id: str = "node1_primary"

    def operator_name(self) -> str:
        """Who the user is (config.json "operator"); "the operator" when not set."""
        return (self.stonesage_cfg.get("operator") or {}).get("name") or "the operator"

    def obsidian_vault(self) -> str:
        """The user's Obsidian vault (StoneSage lodestone/site.py); "" when none is configured."""
        _endpoints()
        from lodestone import site
        return site.obsidian_vault(self.stonesage_cfg)

    def local_tz(self):
        """The installation's time zone (StoneSage lodestone/site.py)."""
        _endpoints()                       # puts StoneSage's backend on the path
        from lodestone import site
        return site.local_tz(self.stonesage_cfg)

    def ssh_options(self, batch: bool = True) -> List[str]:
        return _endpoints().ssh_options(self.stonesage_cfg, batch=batch)

    def ssh_cmd(self, *remote: str, target: str = "") -> List[str]:
        """argv for running `remote` on the engines' host (or `target`, "user@host"), host keys per config."""
        return _endpoints().ssh_cmd(self.stonesage_cfg, *remote, target=target or self.inference_ssh)

    def stonesage_profile(self) -> Dict[str, Any]:
        """The full live system profile (GPUs, engines, host); {} when unavailable."""
        return _live_profile(self.stonesage_cfg) or {}

    def engine(self, role: str) -> Dict[str, Any]:
        """Live engine facts for 'coordinator'/'worker'/'embedder'/'vision' ({} if unknown)."""
        return ((_live_profile(self.stonesage_cfg) or {}).get("engines") or {}).get(role) or {}

    def label(self, role: str) -> str:
        """'Qwen3 14B · RX 6750 XT' for display, or the role name when the engine is unknown."""
        return self.engine(role).get("label") or role

    def model(self, role: str) -> str:
        return self.engine(role).get("model") or role

    def gpu(self, role: str) -> str:
        """'RX 6750 XT' / 'CPU' / '' for the GPU an engine actually runs on."""
        e = self.engine(role)
        return (e.get("gpu") or {}).get("short") or ("CPU" if e.get("offloaded") else "")

    def refresh_hardware(self) -> None:
        """Fill coordinator/worker names, GPUs, memory and slots from StoneSage's live system profile."""
        prof = _live_profile(self.stonesage_cfg)
        engines = (prof or {}).get("engines") or {}
        for nid, role in (("node1_primary", "coordinator"), ("node1_secondary", "worker")):
            e, node = engines.get(role), self.nodes.get(nid)
            if not e or not node:
                continue
            node.name = f"{role.title()}: {e.get('label') or role} (:{e.get('port')})"
            node.device_name = (e.get("gpu") or {}).get("name") or ("CPU" if e.get("offloaded") else node.device_name)
            node.total_memory_mb = e.get("vram_mb") or node.total_memory_mb
            node.slots = e.get("slots") or node.slots
            node.active_context = e.get("ctx_per_slot") or node.active_context

    def set_active_node(self, node_id: str) -> bool:
        if node_id in self.nodes:
            self.active_node_id = node_id
            return True
        return False

    def get_active_node(self) -> NodeEndpoint:
        return self.nodes.get(self.active_node_id, self.nodes["node1_primary"])

    def get_active_node_id(self) -> str:
        return self.active_node_id

    def poll_node_models(self) -> Dict[str, Any]:
        """Dynamically queries each node's endpoint in parallel to retrieve loaded models and health."""
        import concurrent.futures
        import socket

        def _probe_tcp(host: str, port: int, timeout: float = 0.5) -> bool:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except Exception:
                return False

        def _poll_single(item):
            nid, node = item
            parsed_u = urllib.parse.urlparse(node.base_url)
            host = parsed_u.hostname or "127.0.0.1"
            port = parsed_u.port or (443 if parsed_u.scheme == "https" else 80)

            # Pre-probe port to avoid hanging on offline hosts
            if not _probe_tcp(host, port, timeout=0.6):
                node.status = "offline"
                node.active_model = None
                return nid, {"status": "offline", "error": f"Port {port} unreachable on {host}"}

            # 1. First attempt LM Studio native API (/api/v1/models)
            lm_studio_api = f"{parsed_u.scheme}://{host}:{port}/api/v1/models"
            try:
                req = urllib.request.Request(lm_studio_api, headers={"User-Agent": "Harness-Poller"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        raw_models = data.get("models", [])
                        loaded = [m for m in raw_models if m.get("loaded_instances")]
                        if loaded:
                            m = loaded[0]
                            m_name = m.get("key") or m.get("display_name", "Unknown")
                            inst = m.get("loaded_instances", [])[0]
                            ctx = inst.get("config", {}).get("context_length", m.get("max_context_length", 32768))
                            node.active_model = m_name
                            node.active_context = ctx
                            node.status = "online"
                            return nid, {"status": "online", "model": m_name, "context": ctx, "models": raw_models, "api": "lm-studio"}
                        elif raw_models:
                            node.status = "online (no model loaded)"
                            node.active_model = None
                            return nid, {"status": "idle", "model": None, "available_count": len(raw_models), "api": "lm-studio"}
            except Exception:
                pass

            # 2. Attempt llama-server /props endpoint
            props_endpoint = f"{parsed_u.scheme}://{host}:{port}/props"
            try:
                req = urllib.request.Request(props_endpoint, headers={"User-Agent": "Harness-Poller"})
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        m_path = data.get("model_path") or ""
                        m_name = data.get("model_alias") or (m_path.split("/")[-1] if m_path else "Unknown")
                        ctx = data.get("default_generation_settings", {}).get("n_ctx", 8192)
                        node.active_model = m_name
                        node.active_context = ctx
                        node.slots = data.get("total_slots", node.slots)
                        node.status = "online"
                        return nid, {"status": "online", "model": m_name, "context": ctx, "slots": node.slots, "path": m_path, "api": "llama-server"}
            except Exception:
                pass

            # 3. Standard OpenAI /v1/models fallback
            models_endpoint = f"{node.base_url.rstrip('/')}/models"
            try:
                req = urllib.request.Request(models_endpoint, headers={"User-Agent": "Harness-Poller"})
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        model_list = data.get("data", [])
                        if model_list:
                            first_model = model_list[0].get("id", "Unknown")
                            node.active_model = first_model
                            node.status = "online"
                            return nid, {"status": "online", "model": first_model, "models": model_list, "api": "openai"}
                        else:
                            node.status = "online (no models loaded)"
                            node.active_model = None
                            return nid, {"status": "empty", "model": None}
            except Exception as e:
                node.status = "offline"
                return nid, {"status": "offline", "error": str(e)}

            node.status = "online"
            return nid, {"status": "online", "model": node.active_model}

        self.refresh_hardware()
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(self.nodes), 1)) as executor:
            future_to_node = {executor.submit(_poll_single, item): item[0] for item in self.nodes.items()}
            for future in concurrent.futures.as_completed(future_to_node):
                try:
                    nid, res = future.result()
                    results[nid] = res
                except Exception as ex:
                    nid = future_to_node[future]
                    results[nid] = {"status": "offline", "error": str(ex)}

        return results

fleet_config = FleetConfig()

