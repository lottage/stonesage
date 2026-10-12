"""
Real service health for StoneSage (/api/health/all).

Probes every service the stack depends on, in parallel, with short timeouts, and reports
what each one actually says: llama.cpp engines report their loaded model, per-slot context
and slot count from /props, not from config or docs. Nothing here is hardcoded topology
beyond defaults for hosts that are not in config.json.
"""

import base64
import json
import os
import socket
import time
from lodestone import endpoints
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

PROBE_TIMEOUT = 3.0
CACHE_SECONDS = 5.0

_cache: Dict[str, Any] = {"at": 0.0, "result": None}


def _http(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = PROBE_TIMEOUT):
    """GET url. Returns (status_code or None, parsed JSON or text or error string, latency_ms)."""
    req = urllib.request.Request(url, headers=headers or {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read(2000).decode("utf-8", "replace")
        code = e.code
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", round((time.time() - t0) * 1000)
    ms = round((time.time() - t0) * 1000)
    try:
        return code, json.loads(raw), ms
    except ValueError:
        return code, raw.strip()[:200], ms


def _tcp(host: str, port: int, timeout: float = PROBE_TIMEOUT):
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.time() - t0) * 1000)
    except OSError as e:
        return False, str(e)


def _base(url: str) -> str:
    """http://host:8001/v1 -> http://host:8001"""
    url = (url or "").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def _result(name: str, group: str, target: str, ok: bool, status: str, latency_ms=None, **detail) -> Dict[str, Any]:
    return {"name": name, "group": group, "target": target, "ok": ok, "status": status,
            "latency_ms": latency_ms, "detail": {k: v for k, v in detail.items() if v is not None}}


def probe_llama(name: str, url: str) -> Dict[str, Any]:
    base = _base(url)
    code, body, ms = _http(f"{base}/health")
    if code is None:
        return _result(name, "engines", base, False, "down", error=body)
    if code == 503:
        return _result(name, "engines", base, False, "loading", ms)
    if code != 200:
        return _result(name, "engines", base, False, f"http {code}", ms, error=str(body)[:200])
    pcode, props, _ = _http(f"{base}/props")
    if pcode != 200 or not isinstance(props, dict):
        return _result(name, "engines", base, True, "ok", ms, note="/props unavailable")
    model = os.path.basename(props.get("model_path", "") or "")
    gen = props.get("default_generation_settings", {}) or {}
    return _result(name, "engines", base, True, "ok", ms,
                   model=model or None,
                   ctx_per_slot=gen.get("n_ctx"),
                   slots=props.get("total_slots"))


def probe_http(name: str, group: str, url: str, headers: Optional[Dict[str, str]] = None,
               expect=(200,)) -> Dict[str, Any]:
    code, body, ms = _http(url, headers)
    if code is None:
        return _result(name, group, url, False, "down", error=body)
    ok = code in expect
    return _result(name, group, url, ok, "ok" if ok else f"http {code}", ms,
                   error=None if ok else str(body)[:200])


def probe_tcp(name: str, group: str, host: str, port: int) -> Dict[str, Any]:
    ok, info = _tcp(host, port)
    target = f"{host}:{port}"
    if ok:
        return _result(name, group, target, True, "ok", info, check="tcp")
    return _result(name, group, target, False, "down", check="tcp", error=info)


def _candidates(cfg: Dict[str, Any]) -> List:
    """Every probe this stack knows, each as (needs, probe tuple). `needs` is the service config.json (or the environment)
    must name for the probe to make sense; None = always (the coordinator: without a brain nothing works; the probes that are
    only listed at all when their host or token is configured: Proxmox, the old voice host, Home Assistant)."""
    ha = cfg.get("homeassistant", {})
    voice_host = endpoints.host("voice_legacy", cfg)          # "" = not configured: those probes are left out
    pve_host = endpoints.host("proxmox", cfg)
    ha_token = ha.get("token") or os.environ.get("HASS_TOKEN", "")
    ha_url = endpoints.url("homeassistant", cfg)
    couch = cfg.get("couchdb", {})
    couch_url = endpoints.url("couchdb", cfg)
    couch_pw = couch.get("password", os.environ.get("COUCHDB_PASSWORD", ""))
    couch_auth = base64.b64encode(f"{couch.get('username', '')}:{couch_pw}".encode()).decode("ascii")
    qdrant_url = endpoints.url("qdrant", cfg)
    mcp_url = endpoints.url("mcp", cfg)
    voice_server_url = endpoints.url("voice", cfg)   # voice_client.py
    voice_server_host = endpoints.host("voice", cfg)

    probes = [
        (None, (probe_llama, "Coordinator", endpoints.url("coordinator", cfg))),
        ("worker", (probe_llama, "Worker", endpoints.url("worker", cfg))),
        ("embedder", (probe_llama, "Embedder", endpoints.url("embedder", cfg))),
        ("vision", (probe_llama, "Vision", endpoints.url("vision", cfg))),
        ("mcp", (probe_http, "Cluster MCP bridge", "services", f"{mcp_url}/health")),
        ("qdrant", (probe_http, "Qdrant", "services", f"{qdrant_url}/readyz")),
        ("couchdb", (probe_http, "CouchDB", "services", f"{couch_url}/_up", {"Authorization": f"Basic {couch_auth}"})),
        ("voice", (probe_http, "Voice server", "voice", f"{voice_server_url}/health")),
        ("voice", (probe_tcp, "Wyoming STT", "voice", voice_server_host, 10301)),
        ("voice", (probe_tcp, "Wyoming TTS", "voice", voice_server_host, 10201)),
        ("voice", (probe_tcp, "Wake word", "voice", voice_server_host, 10400)),
    ]
    if pve_host:
        probes.insert(7, (None, (probe_tcp, "Proxmox API", "infra", pve_host, endpoints.port("proxmox", cfg))))
    if voice_host:
        probes += [
            (None, (probe_tcp, "Whisper STT", "voice", voice_host, 8200)),
            (None, (probe_tcp, "Kokoro TTS", "voice", voice_host, 8300)),
            (None, (probe_tcp, "Wyoming Whisper", "voice", voice_host, 10300)),
            (None, (probe_tcp, "Wyoming Piper", "voice", voice_host, 10200)),
        ]
    if ha_token:
        probes.append((None, (probe_http, "Home Assistant", "services", f"{ha_url}/api/",
                                         {"Authorization": f"Bearer {ha_token}"})))
    elif endpoints.needed("homeassistant", cfg):       # an address with no token is a half-finished setup (the Hearth wizard asks for both)
        probes.append((HA_NEEDS_TOKEN, (probe_http, "Home Assistant", "services", f"{ha_url}/manifest.json")))
    return probes


HA_NEEDS_TOKEN = "homeassistant:token"      # never "configured": Home Assistant is probed once a token is stored


def _is_set_up(needs: Optional[str], cfg: Dict[str, Any]) -> bool:
    if needs is None:
        return True
    if needs == HA_NEEDS_TOKEN:
        return False
    if needs == "couchdb":      # a database section with a login is as good as an address
        return endpoints.configured("couchdb", cfg) or bool((cfg.get("couchdb") or {}).get("username"))
    return endpoints.configured(needs, cfg)


def build_probes(cfg: Dict[str, Any]) -> List:
    """The probes of what this install has set up: the coordinator always, every other service only once config.json (or
    the environment) names it. The rest are in not_set_up() and are shown as "not set up", never as down."""
    return [p for needs, p in _candidates(cfg) if _is_set_up(needs, cfg)]


def not_set_up(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """The services this install has not set up (name + group), for the status panel."""
    return [{"name": p[1], "group": "engines" if p[0] is probe_llama else p[2]}
            for needs, p in _candidates(cfg) if not _is_set_up(needs, cfg)]


def check_all(cfg: Dict[str, Any], use_cache: bool = True) -> Dict[str, Any]:
    now = time.time()
    if use_cache and _cache["result"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["result"]
    probes = build_probes(cfg)
    with ThreadPoolExecutor(max_workers=len(probes)) as ex:
        services = list(ex.map(lambda p: p[0](*p[1:]), probes))
    down = [s["name"] for s in services if not s["ok"]]
    idle = not_set_up(cfg)
    result = {
        "ok": not down,
        "checked_at": now,
        "summary": f"{len(services) - len(down)}/{len(services)} up" + (f"; down: {', '.join(down)}" if down else "")
                   + (f"; not set up: {', '.join(i['name'] for i in idle)}" if idle else ""),
        "services": services,
        "not_set_up": idle,
    }
    _cache.update(at=now, result=result)
    return result
