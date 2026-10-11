"""
The agent API (Roadmap Phase 11 step 5, 2026-10-11): StoneSage's agents over HTTP, on its own port, for any OpenAI-compatible
client (Hermes, opencode, Open WebUI, a script), and the routes that move souls between machines.

    GET  /v1/models                    one entry per agent (id = agent id, owned_by "stonesage")
    POST /v1/chat/completions          model = an agent id (or alias: "computer"); runs that agent's loop on the last user
                                       message, with the earlier messages as history. "stream": true sends OpenAI SSE chunks.
    GET  /api/souls                    the souls on this machine (manifests, and whether the agent is running)
    GET  /api/souls/<id>/export        one soul as a .tar.gz download
    POST /api/souls/import?as=<id>&overwrite=1     the raw .tar.gz as the request body

An agent's tool calls happen inside its loop: the client sees only the answer (a question that needs a yes is the answer, and
"yes" in the next request, same session, approves it). The session for approvals is the request's `user` field, else the
X-Session-Id header, else the caller's address. A client's own system message is not used: the persona is the agent's soul.

Auth: these routes sit behind the server's one gate (stonesage/auth.py): `Authorization: Bearer <key>` (env STONESAGE_API_KEY
or config api.key, constant-time compare) or a login cookie; no key at all means nothing is let in. `_denied` below repeats the
check for a route reached without the gate in front of it. The approval session is bound to the caller (auth.approval_key).

Routes are registered on the app route table by install(), so server.py needs no per-route code.
"""

import io
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from . import auth
from .agents import AgentHub, AgentSpec
from .courage.agent import streamed
from .soul import AGENT_ID, SoulError, SoulExists, export_soul, import_soul, list_souls

MAX_IMPORT_BYTES = 60 * 1024 * 1024        # the archive as sent (the unpacked caps are in soul.py)
OWNER = "stonesage"


def api_key(config: Optional[Dict[str, Any]]) -> str:
    return auth.configured_key(config)


def authorised(header: str, key: str) -> bool:
    """`Bearer <key>` against the server key (constant-time). No key configured = nobody is authorised (fail closed)."""
    return auth.bearer_ok(header, key)


# ---- small response helpers (the handler's send_json cannot add headers) -------------------------------------
def _send(h, status: int, payload: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(payload)))
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(payload)


def _json(h, status: int, data: Any, extra: Optional[Dict[str, str]] = None) -> None:
    _send(h, status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)


def _oai_error(h, status: int, message: str, kind: str = "invalid_request_error", code: Optional[str] = None,
               extra: Optional[Dict[str, str]] = None) -> None:
    _json(h, status, {"error": {"message": message, "type": kind, "param": None, "code": code}}, extra)


def _denied(h, config: Dict[str, Any], openai_shape: bool) -> bool:
    """True (after answering 401) when the request is not authenticated. Behind the gate this is one attribute read."""
    return auth.require_principal(h, config, openai_shape)


# ---- chat -----------------------------------------------------------------------------------------------------
def _text(content: Any) -> str:
    """A message's text: a string, or the text parts of a list-of-parts content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text"
                       and isinstance(p.get("text"), str))
    return ""


def history_from(messages: Any) -> List[Dict[str, str]]:
    """The user/assistant messages of an OpenAI request as the loop's history (system and tool messages are not used)."""
    out = []
    for m in messages if isinstance(messages, list) else []:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            out.append({"role": m["role"], "content": _text(m.get("content"))})
    return out


def session_id(h, body: Dict[str, Any]) -> str:
    """The approval session: the caller (auth principal) + the id it chose (`user`, else X-Session-Id, else its address).
    A chosen id that starts with a prefix the server uses itself (ha:, alexa:, api:...) is ignored."""
    who = body.get("user") if isinstance(body.get("user"), str) and body.get("user") else h.headers.get("X-Session-Id", "")
    return "api:" + auth.approval_key(h, who)


def _chunk(cid: str, created: int, model: str, delta: Dict[str, Any], finish: Optional[str] = None) -> bytes:
    obj = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def chat_completions(hub: AgentHub, config: Dict[str, Any]):
    def route(h, parsed, body: Dict[str, Any]) -> None:
        if _denied(h, config, True):
            return
        if not isinstance(body, dict) or not body:
            return _oai_error(h, 400, "the request body must be a JSON object", code="invalid_json")
        spec = hub.resolve(body.get("model"))
        if spec is None:
            return _oai_error(h, 404, f"The model '{body.get('model')}' does not exist (GET /v1/models lists the agents)",
                              code="model_not_found")
        history = history_from(body.get("messages"))
        if not history or history[-1]["role"] != "user" or not history[-1]["content"].strip():
            return _oai_error(h, 400, "messages must end with a user message that has text", code="invalid_messages")
        try:
            agent = hub.agent(spec)
        except Exception as e:                    # e.g. Computer without the Home app
            return _oai_error(h, 503, f"agent '{spec.id}' is not available: {e}", "server_error", "agent_unavailable")
        sid, cid, created = session_id(h, body), "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time())
        if body.get("stream") is True:
            return _stream(h, agent, spec, history, sid, cid, created)
        text, error = "", None
        try:
            for ev in agent.run(history, sid):
                if ev["type"] == "final":
                    text = ev.get("content") or ""
        except Exception as e:
            error = e
        if error is not None:
            return _oai_error(h, 500, f"the agent failed: {type(error).__name__}", "server_error", "agent_error")
        _json(h, 200, {"id": cid, "object": "chat.completion", "created": created, "model": spec.id,
                       "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})
    return route


def _stream(h, agent, spec: AgentSpec, history, sid: str, cid: str, created: int) -> None:
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Connection", "close")
    h.end_headers()
    gone = []

    def write(data: bytes) -> None:
        if gone:
            return                          # the client left; the turn still finishes, so approvals stay consistent
        try:
            h.wfile.write(data)
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            gone.append(True)

    write(_chunk(cid, created, spec.id, {"role": "assistant", "content": ""}))
    try:
        for ev in streamed(agent.run(history, sid)):
            if ev["type"] in ("partial", "final") and ev.get("content"):
                write(_chunk(cid, created, spec.id, {"content": ev["content"]}))
        write(_chunk(cid, created, spec.id, {}, "stop"))
    except Exception as e:
        write(f"data: {json.dumps({'error': {'message': 'the agent failed: ' + type(e).__name__, 'type': 'server_error'}})}\n\n".encode())
    write(b"data: [DONE]\n\n")


def models(hub: AgentHub, config: Dict[str, Any]):
    def route(h, parsed, body) -> None:
        if _denied(h, config, True):
            return
        created = {m["id"]: int(m.get("created") or 0) for m in list_souls(hub.data_dir())}
        _json(h, 200, {"object": "list", "data": [
            {"id": s.id, "object": "model", "created": created.get(s.id, 0), "owned_by": "stonesage",
             "name": s.name, "description": s.description} for s in hub.specs()]})
    return route


# ---- souls ----------------------------------------------------------------------------------------------------
def souls_list(hub: AgentHub, config: Dict[str, Any]):
    def route(h, parsed, body) -> None:
        if _denied(h, config, False):
            return
        builtin = {s.id for s in hub.specs()}
        _json(h, 200, {"ok": True, "souls": [
            dict(m, running=hub.is_running(m["id"]), agent=m["id"] in builtin) for m in list_souls(hub.data_dir())]})
    return route


def soul_export(hub: AgentHub, config: Dict[str, Any]):
    def route(h, parsed, body) -> None:
        if _denied(h, config, False):
            return
        parts = parsed.path[len("/api/souls/"):].split("/")
        if len(parts) != 2 or parts[1] != "export" or not AGENT_ID.match(parts[0]):
            return _json(h, 404, {"ok": False, "error": "not found"})
        buf = io.BytesIO()
        try:
            export_soul(hub.data_dir(), parts[0], buf)
        except SoulError as e:
            return _json(h, 404, {"ok": False, "error": str(e)})
        _send(h, 200, buf.getvalue(), "application/gzip",
              {"Content-Disposition": f'attachment; filename="{parts[0]}.soul.tar.gz"'})
    return route


def soul_import(hub: AgentHub, config: Dict[str, Any]):
    def route(h, parsed, body) -> None:
        if _denied(h, config, False):
            return
        raw = getattr(h, "_raw_body", b"") or b""
        if not raw:
            return _json(h, 400, {"ok": False, "error": "send the .tar.gz as the request body"})
        if len(raw) > MAX_IMPORT_BYTES:
            return _json(h, 413, {"ok": False, "error": "the archive is too large"})
        q = parse_qs(parsed.query)
        as_id = (q.get("as") or [None])[0] or None
        overwrite = (q.get("overwrite") or ["0"])[0].lower() in ("1", "true", "yes")
        try:
            manifest = import_soul(hub.data_dir(), io.BytesIO(raw), agent_id=as_id, overwrite=overwrite)
        except SoulExists as e:
            running = hub.is_running(e.agent_id)
            return _json(h, 409, {"ok": False, "needs": "overwrite", "running": running,
                                  "error": str(e) + (" Its agent is running." if running else "")})
        except SoulError as e:
            return _json(h, 400, {"ok": False, "error": str(e)})
        hub.reload(manifest["id"])            # the running agent re-reads its notes; persona is read from the file each turn
        _json(h, 200, {"ok": True, "soul": manifest})
    return route


def install(ctx, hub: AgentHub) -> None:
    """Register the routes on the app route table (apps.py), under the platform's own name."""
    def add(method: str, path: str, make, prefix: bool = False) -> None:
        ctx.routes.add(method, path, make(hub, ctx.config), prefix=prefix, owner=OWNER)

    add("GET", "/v1/models", models)
    add("POST", "/v1/chat/completions", chat_completions)
    add("GET", "/api/souls", souls_list)
    add("GET", "/api/souls/", soul_export, prefix=True)
    add("POST", "/api/souls/import", soul_import)
