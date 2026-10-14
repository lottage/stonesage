"""Login and the gate: who may talk to this server (docs/LAYERS.md, "M5: Login and hardening").

One server key (config `api.key`, or env STONESAGE_API_KEY, which wins) and one chokepoint. `gate()` is called first in every
do_* method of the HTTP handler, before any route table (platform, apps, agent API), and either lets the request through
or has already answered it. A request is let through when it is

  - for the login page or the static web UI (files in the frontend folder), `POST /api/auth/login`, `POST /api/auth/logout`,
    the minimal `GET /api/health`, or `POST /api/watch/reply` (which checks its own bearer);
  - an OPTIONS request (nothing to read: it answers 204 with no CORS headers, so a browser page of another origin gets nothing);
  - carrying `Authorization: Bearer <key>` (scripts, Prometheus, the CLI, OpenAI-style clients), or a valid `ss_session` cookie
    (the browser UI, from `POST /api/auth/login {key}`); or
  - from an address in config `api.trusted_hosts`, on the few routes in TRUSTED_ROUTES only (the Ollama-style routes Home
    Assistant's integration uses, which cannot send a header, and the Prometheus scrape).

Everything else is 401. The cookie is HMAC-signed with a secret derived from the key (so changing the key logs everyone out),
HttpOnly, SameSite=Strict, and Secure when the request came over TLS. A request authenticated by the cookie that changes
something (POST/PUT/DELETE/PATCH) must also be same-origin (Origin or Sec-Fetch-Site); a bearer request is not ambient, so it
needs no such check. The Host header must be an IP address, `localhost`, or a name in `api.allowed_hosts` (DNS rebinding).
Failed logins and wrong bearer keys are counted per client address and the address is locked out for a while.

    python -m stonesage.auth show-key      # print the key (config file given by --config, else the server's own)
"""

import argparse
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import ssl
import sys
import tempfile
import threading
import time
import urllib.parse
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger("StoneSage.auth")

COOKIE = "ss_session"
SESSION_TTL = 30 * 24 * 3600
UNSAFE_METHODS = ("POST", "PUT", "DELETE", "PATCH")
OWNER = "owner"                                          # the principal of a key or cookie holder
# Scoped keys (docs/LAYERS.md, apps): an app can declare a key of its own that opens exactly the routes it names and nothing else
# (Grove's model key: two routes, no cookie, no other path). The owner's key is never one of them (a scoped key equal to the server
# key is refused, so a leaked scoped key is never the master). Declared through AppContext.add_scoped_key (stonesage/apps.py).
class ScopedKey:
    def __init__(self, name: str, routes, key_fn):
        self.name = name                                       # the principal a request with this key becomes
        self.routes = frozenset((m.upper(), p) for m, p in routes)
        self.key_fn = key_fn                                   # (config) -> the key text, '' when none is configured


SCOPED_KEYS: Dict[str, ScopedKey] = {}


def register_scoped_key(name: str, routes, key_fn) -> ScopedKey:
    """Declare (or replace, by name) a scoped key. `routes` = [(method, path), ...] exactly; `key_fn(config)` reads the key."""
    if not name or name == OWNER or name.startswith(("trusted:", "ip:")):
        raise ValueError(f"{name!r} cannot be the principal of a scoped key")
    SCOPED_KEYS[name] = ScopedKey(name, routes, key_fn)
    return SCOPED_KEYS[name]


def unregister_scoped_key(name: str) -> None:
    SCOPED_KEYS.pop(name, None)


def scoped_key_value(config: Optional[Dict[str, Any]], name: str) -> str:
    """The configured key of a declared scoped key, or '' (none, unknown name, or equal to the server key)."""
    sk = SCOPED_KEYS.get(name)
    key = str(sk.key_fn(config) or "").strip() if sk else ""
    return "" if key == configured_key(config) else key



# Session ids a client may not choose: the server builds these itself (an approval a person is asked in Home Assistant's voice
# chat, an Alexa session, the API, a trusted machine, an address), and a client-chosen id must never land on someone else's slot.
RESERVED_SESSION_PREFIXES = ("ha:", "alexa:", "api:", "trusted:", "ip:", "owner:")

PUBLIC_ROUTES = {("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("GET", "/api/health"), ("HEAD", "/api/health"),
                 ("POST", "/api/watch/reply")}

# What a machine in `api.trusted_hosts` may call without a key: the Ollama-compatible routes Home Assistant's Ollama
# integration uses (model list in its setup, the chat itself) and the Prometheus scrape. Nothing else.
TRUSTED_ROUTES = {("GET", "/api/tags"), ("GET", "/api/version"), ("GET", "/api/ps"), ("POST", "/api/show"),
                  ("POST", "/api/chat"), ("GET", "/metrics")}

# Platform routes that stay off unless config `features.<name>` is true (and are behind the login either way). App routes
# declare theirs when they register (apps.py, `feature=`).
PLATFORM_FEATURES = {
    ("POST", "/api/terminal/exec"): "web_terminal",
    ("POST", "/api/harness/tools/register"): "code_tools",
    ("POST", "/api/harness/tools/ingest_url"): "code_tools",
    ("POST", "/api/git/repo/select"): "file_editor",
    ("POST", "/api/git/clone"): "file_editor",
    ("POST", "/api/git/commit"): "file_editor",
    ("POST", "/api/workspaces/roots/add"): "file_editor",
    ("POST", "/api/workspaces/roots/delete"): "file_editor",
    ("POST", "/api/harness/tools/execute"): "code_tools",
    ("POST", "/api/3d/execute"): "code_tools",
    ("GET", "/api/workspace/file"): "file_editor",
    ("GET", "/api/workspace/tree"): "file_editor",
    ("POST", "/api/workspace/save"): "file_editor",
    ("POST", "/api/workspace/diff"): "file_editor",
}
# The shelved features (Assembly Hall, the autonomous thinking loop, agent reproduction, Citadel 3D / Blender, trainer runs, and the
# older subagent and planner routes): still in the code, off unless config `features.shelved` is true. Their behaviour is
# unchanged when on; nothing is being extended. A route here answers 404 while the flag is off, after the login check.
SHELVED_PREFIXES = ("/api/trainer/", "/api/dataset/", "/api/hivemind/", "/api/agent/", "/api/3d/", "/api/subagents/", "/api/planner/")
FEATURE_NAMES = ("web_terminal", "code_tools", "raw_ha_call", "file_editor", "shelved")


def is_shelved(path: str) -> bool:
    return any(path.startswith(p) for p in SHELVED_PREFIXES)


# ---- the key ----------------------------------------------------------------------------------------------------
def configured_key(config: Optional[Dict[str, Any]]) -> str:
    """The server key: env STONESAGE_API_KEY, else config api.key; '' when there is none."""
    env = os.environ.get("STONESAGE_API_KEY", "").strip()
    if env:
        return env
    return str(((config or {}).get("api") or {}).get("key") or "").strip()


def default_config_file() -> str:
    """Where the server reads its config (the same rule as stonesage/server.py: env, else backend/config.json)."""
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("STONESAGE_CONFIG_FILE") or os.path.join(backend, "config.json")


def write_private_json(path: str, data: Dict[str, Any]) -> None:
    """Atomic write, owner-only permissions where the OS has them (a config holds secrets)."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=folder)     # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_key(config: Dict[str, Any], config_file: str) -> Tuple[str, bool]:
    """Make sure a key exists. Returns (key, generated). When neither the env nor the config has one, a new random key is
    saved into the config file (0600) and into the live `config`. It is never logged: the log says where it is."""
    key = configured_key(config)
    if key:
        return key, False
    key = secrets.token_urlsafe(32)
    on_disk: Dict[str, Any] = {}
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8-sig") as f:
                on_disk = json.load(f)
        except Exception as e:
            raise RuntimeError(f"cannot read {config_file} to save the API key: {e}") from e
    else:
        on_disk = json.loads(json.dumps(config or {}))        # no file yet: keep what the server runs on (the example's defaults)
        from stonesage import config_migrate                    # (imported here: config_migrate imports this module)
        on_disk.setdefault("config_version", config_migrate.LATEST)    # a file born now is current: nothing to migrate
        config.setdefault("config_version", config_migrate.LATEST)
    on_disk.setdefault("api", {})["key"] = key
    write_private_json(config_file, on_disk)
    config.setdefault("api", {})["key"] = key
    logger.warning("No API key was set, so one was generated and saved in %s. Read it with: python -m stonesage.auth show-key",
                   config_file)
    return key, True


# ---- sessions (the cookie) ---------------------------------------------------------------------------------------
def _secret(key: str) -> bytes:
    return hmac.new(key.encode("utf-8"), b"stonesage-session-v1", hashlib.sha256).digest()


def make_session(key: str, now: Optional[float] = None, ttl: int = SESSION_TTL) -> str:
    expiry = int((time.time() if now is None else now) + ttl)
    sig = hmac.new(_secret(key), f"v1.{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{sig}"


def verify_session(key: str, value: str, now: Optional[float] = None) -> bool:
    if not key or not value:
        return False
    parts = str(value).split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdigit():
        return False
    now = time.time() if now is None else now
    expiry = int(parts[1])
    if expiry <= now or expiry > now + SESSION_TTL + 120:      # expired, or claims to outlive any session we issue
        return False
    want = hmac.new(_secret(key), f"v1.{expiry}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want.encode(), parts[2].encode("utf-8", "replace"))


def cookie_value(cookie_header: str, name: str = COOKIE) -> str:
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip()
    return ""


def set_cookie_header(value: str, secure: bool, max_age: int = SESSION_TTL) -> str:
    return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={int(max_age)}" + ("; Secure" if secure else "")


def bearer_ok(header: str, key: str) -> bool:
    """True when the header is `Bearer <key>` (constant-time; bytes, so a non-ASCII value is a 'no', not an error)."""
    if not key:
        return False
    scheme, _, token = (header or "").strip().partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.strip().encode("utf-8", "replace"), key.encode("utf-8"))


def has_bearer(header: str) -> bool:
    return (header or "").strip().lower().startswith("bearer ")


# ---- failed-attempt throttle ---------------------------------------------------------------------------------------
class Throttle:
    """Counts failures per client address; `blocked` once `limit` fall inside `window` seconds, until the oldest ages out."""

    def __init__(self, limit: int = 10, window: float = 300.0, clock=time.time):
        self.limit, self.window, self.clock = limit, window, clock
        self._fails: Dict[str, list] = {}
        self._lock = threading.Lock()

    def _recent(self, who: str) -> list:
        now = self.clock()
        recent = [t for t in self._fails.get(who, []) if now - t < self.window]
        if recent:
            self._fails[who] = recent
        else:
            self._fails.pop(who, None)
        return recent

    def blocked(self, who: str) -> float:
        """Seconds until this address may try again; 0 when it may."""
        with self._lock:
            recent = self._recent(who)
            if len(recent) < self.limit:
                return 0.0
            return max(1.0, self.window - (self.clock() - recent[0]))

    def fail(self, who: str) -> None:
        with self._lock:
            self._recent(who)
            self._fails.setdefault(who, []).append(self.clock())
            if len(self._fails) > 2000:                      # a flood from many addresses must not grow this without end
                for k in list(self._fails)[:500]:
                    self._fails.pop(k, None)

    def reset(self, who: str) -> None:
        with self._lock:
            self._fails.pop(who, None)


THROTTLE = Throttle()


# ---- host, origin ---------------------------------------------------------------------------------------------------
def _split_host(value: str) -> Tuple[str, Optional[int]]:
    """'Name:8888' / '[::1]:8888' / '10.0.0.5' -> (host lower-cased without a trailing dot, port or None)."""
    v = (value or "").strip().lower()
    port: Optional[int] = None
    if v.startswith("["):
        end = v.find("]")
        host = v[1:end] if end > 0 else v[1:]
        rest = v[end + 1:] if end > 0 else ""
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    elif v.count(":") == 1:
        host, _, p = v.partition(":")
        port = int(p) if p.isdigit() else None
    else:
        host = v
    return host.rstrip("."), port


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def allowed_hosts(config: Optional[Dict[str, Any]]) -> set:
    return {str(h).strip().lower().rstrip(".") for h in (((config or {}).get("api") or {}).get("allowed_hosts") or []) if str(h).strip()}


def host_allowed(host_header: Optional[str], config: Optional[Dict[str, Any]]) -> bool:
    """DNS-rebinding defence: the name the client used must be one of ours. IP literals and localhost always; other names
    only when listed in config api.allowed_hosts. A request with no Host header at all is not a browser, so it passes."""
    if host_header is None or not str(host_header).strip():
        return True
    host, _ = _split_host(host_header)
    if not host:
        return False
    if _is_ip(host) or host == "localhost" or host.endswith(".localhost"):
        return True
    return host in allowed_hosts(config)


def _default_port(scheme: str) -> int:
    return 443 if scheme in ("https", "wss") else 80


def same_origin(headers) -> bool:
    """For a request that changes something: did the browser say it came from this very site? Sec-Fetch-Site (when sent) must
    be 'same-origin'; Origin (when sent) must be this server's own scheme+host+port; one of the two must be present."""
    site = headers.get("Sec-Fetch-Site")
    origin = headers.get("Origin")
    if site is not None and str(site).strip().lower() != "same-origin":
        return False
    if origin is None:
        return site is not None
    try:
        o = urllib.parse.urlparse(str(origin).strip())
        ohost, oport = (o.hostname or "").lower(), o.port or _default_port(o.scheme)
    except ValueError:
        return False
    if not ohost or o.scheme not in ("http", "https"):
        return False
    hhost, hport = _split_host(headers.get("Host") or "")
    return ohost == hhost and oport == (hport or _default_port(o.scheme))


def same_site_host(headers) -> bool:
    """For the PTY WebSocket (another port of the same machine): the Origin's host name must equal the Host header's."""
    origin = headers.get("Origin")
    if origin is None:
        return False
    try:
        ohost = (urllib.parse.urlparse(str(origin).strip()).hostname or "").lower()
    except ValueError:
        return False
    return bool(ohost) and ohost == _split_host(headers.get("Host") or "")[0]


def is_tls(h) -> bool:
    return isinstance(getattr(h, "connection", None), ssl.SSLSocket)


def client_ip(h) -> str:
    return str((getattr(h, "client_address", None) or ("", 0))[0])


def trusted(h, config: Optional[Dict[str, Any]]) -> bool:
    """Is the connecting address in config api.trusted_hosts (IPs or CIDR blocks)? Never X-Forwarded-For."""
    try:
        ip = ipaddress.ip_address(client_ip(h).split("%", 1)[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    for entry in ((config or {}).get("api") or {}).get("trusted_hosts") or []:
        try:
            net = ipaddress.ip_network(str(entry).strip(), strict=False)
        except ValueError:
            continue
        if ip.version == net.version and ip in net:
            return True
    return False


# ---- features -------------------------------------------------------------------------------------------------------
def feature_enabled(config: Optional[Dict[str, Any]], name: str) -> bool:
    return (((config or {}).get("features") or {}).get(name)) is True


def feature_for(method: str, path: str, routes=None) -> str:
    flag = PLATFORM_FEATURES.get((method.upper(), path), "")
    if not flag and routes is not None and hasattr(routes, "feature"):
        flag = routes.feature(method, path)
    return flag


# ---- static files ---------------------------------------------------------------------------------------------------
def _api_like(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/v1/") or path == "/metrics" or path in ("/api", "/v1")


def static_file(frontend_dir: Optional[str], path: str) -> bool:
    """Is `path` a file of the web UI (or a folder with an index.html)? Realpath-contained in the frontend folder."""
    if not frontend_dir:
        return False
    rel = urllib.parse.unquote(path).replace("\\", "/")
    if "\0" in rel:
        return False
    root = os.path.realpath(frontend_dir)
    target = os.path.realpath(os.path.join(root, *[p for p in rel.split("/") if p not in ("", ".")]))
    if target != root and not target.startswith(root + os.sep):
        return False
    if os.path.isdir(target):
        return os.path.isfile(os.path.join(target, "index.html"))
    return os.path.isfile(target)


def is_public(method: str, path: str, frontend_dir: Optional[str] = None, is_apk=None) -> bool:
    method = method.upper()
    if (method, path) in PUBLIC_ROUTES:
        return True
    if method in ("GET", "HEAD") and not _api_like(path):
        if is_apk and is_apk(path):
            return False                                  # the app package is for the signed-in owner
        return static_file(frontend_dir, path)
    return False


# ---- answers --------------------------------------------------------------------------------------------------------
def reply(h, status: int, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> None:
    """A JSON answer without CORS headers (works for the real handler and for the test fakes)."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    if getattr(h, "command", "") != "HEAD":
        h.wfile.write(body)


def _deny(h, path: str, status: int, error: str, headers: Optional[Dict[str, str]] = None) -> None:
    if status == 401:
        headers = dict(headers or {}, **{"X-StoneSage-Auth": "login"})      # the web page sends the person to the login page on this
    if path.startswith("/v1/"):                       # OpenAI-shaped, like the agent API's own errors
        code = {401: "invalid_api_key", 429: "rate_limit_exceeded"}.get(status, "forbidden")
        reply(h, status, {"error": {"message": error, "type": "authentication_error" if status == 401 else "invalid_request_error",
                                    "param": None, "code": code}}, headers)
    else:
        reply(h, status, {"ok": False, "error": error}, headers)


# ---- the gate -------------------------------------------------------------------------------------------------------
def authenticate(h, config: Dict[str, Any], method: str, path: str) -> Tuple[Optional[str], str]:
    """(principal, how): who this request is, or (None, reason). reason: 'no-key' | 'none' | 'bad-key' | 'cookie-origin' | 'locked'."""
    key = configured_key(config)
    who = client_ip(h)
    auth_header = h.headers.get("Authorization", "")
    if has_bearer(auth_header):
        wait = THROTTLE.blocked(who)
        if wait:
            return None, "locked"
        if bearer_ok(auth_header, key):
            return OWNER, "bearer"
        for sk in list(SCOPED_KEYS.values()):
            if (method.upper(), path) in sk.routes and bearer_ok(auth_header, scoped_key_value(config, sk.name)):
                return sk.name, sk.name + "-bearer"         # an app's scoped key: only the routes it declared, no cookie
        THROTTLE.fail(who)
        return None, "bad-key"
    cookie = cookie_value(h.headers.get("Cookie", ""))
    if cookie and verify_session(key, cookie):
        if method.upper() in UNSAFE_METHODS and not same_origin(h.headers):
            return None, "cookie-origin"
        return OWNER, "cookie"
    if (method.upper(), path) in TRUSTED_ROUTES and trusted(h, config):
        return f"trusted:{who}", "trusted"
    return None, "none" if key else "no-key"


def gate(h, method: str, config: Dict[str, Any], routes=None, frontend_dir: Optional[str] = None, is_apk=None) -> bool:
    """Decide the request. True = go on (h.principal is set; it may be None for a public route). False = already answered."""
    h.principal = None
    path = urllib.parse.urlparse(h.path).path
    if not host_allowed(h.headers.get("Host"), config):
        _deny(h, path, 421, "unknown host name (add it to api.allowed_hosts)")
        return False
    method = method.upper()
    if method == "OPTIONS":                                        # no CORS: a cross-origin page gets no permission from us
        h.send_response(204)
        h.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        h.send_header("Content-Length", "0")
        h.end_headers()
        return False
    if is_public(method, path, frontend_dir, is_apk):
        if method == "POST" and path in ("/api/auth/login", "/api/auth/logout") and h.headers.get("Origin") is not None \
                and not same_origin(h.headers):
            _deny(h, path, 403, "cross-origin request refused")
            return False
        return True
    principal, how = authenticate(h, config, method, path)
    if principal is None:
        if how == "locked":
            _deny(h, path, 429, "too many wrong keys from this address; wait a few minutes",
                  {"Retry-After": str(int(THROTTLE.blocked(client_ip(h))) or 60)})
        elif how == "cookie-origin":
            _deny(h, path, 403, "cross-origin request refused")
        else:
            _deny(h, path, 401, "unauthorized", {"WWW-Authenticate": "Bearer"})
        return False
    h.principal = principal
    flag = feature_for(method, path, routes)
    if flag and not feature_enabled(config, flag):
        reply(h, 404, {"ok": False, "error": f"this feature is off (set features.{flag} to true in config.json to turn it on)",
                       "feature": flag})
        return False
    if is_shelved(path) and not feature_enabled(config, "shelved"):
        reply(h, 404, {"ok": False, "error": "this part of StoneSage is shelved and off (set features.shelved to true in config.json "
                                             "to turn it on)", "feature": "shelved"})
        return False
    return True


def require_principal(h, config: Dict[str, Any], openai_shape: bool = False) -> bool:
    """For routes that can be called without the gate in front (a test, an embedding of the route table): True after answering
    401 when the request is not authenticated. Behind the gate this is one attribute read."""
    if getattr(h, "principal", None):
        return False
    key = configured_key(config)
    if bearer_ok(h.headers.get("Authorization", ""), key):
        h.principal = OWNER
        return False
    path = "/v1/x" if openai_shape else "/api/x"
    _deny(h, path, 401, "Invalid or missing API key" if openai_shape else "unauthorized", {"WWW-Authenticate": "Bearer"})
    return True


# ---- login / logout / whoami routes ------------------------------------------------------------------------------------
def login(h, config: Dict[str, Any], body: Dict[str, Any]) -> None:
    who = client_ip(h)
    wait = THROTTLE.blocked(who)
    if wait:
        reply(h, 429, {"ok": False, "error": "too many wrong keys from this address; wait a few minutes"},
              {"Retry-After": str(int(wait))})
        return
    key = configured_key(config)
    given = str(body.get("key") or "") if isinstance(body, dict) else ""
    if not key or not hmac.compare_digest(given.strip().encode("utf-8", "replace"), key.encode("utf-8")):
        THROTTLE.fail(who)
        reply(h, 401, {"ok": False, "error": "that is not the key"})
        return
    THROTTLE.reset(who)
    reply(h, 200, {"ok": True}, {"Set-Cookie": set_cookie_header(make_session(key), is_tls(h)),
                                 "Cache-Control": "no-store"})


def logout(h) -> None:
    reply(h, 200, {"ok": True}, {"Set-Cookie": set_cookie_header("", is_tls(h), 0), "Cache-Control": "no-store"})


def whoami(h) -> None:
    reply(h, 200, {"ok": True, "principal": getattr(h, "principal", None)})


# ---- approvals: sessions belong to the person who asked --------------------------------------------------------------
def client_session_id(raw: Any) -> Optional[str]:
    """A client-chosen conversation id, or None when it is empty or tries to be one of the server's own (see RESERVED...)."""
    s = str(raw or "").strip()[:120]
    if any(s.lower().startswith(p) for p in RESERVED_SESSION_PREFIXES):
        return None
    return s or ""


def approval_key(h, client_id: Any = "") -> str:
    """The key an approval is stored under: the authenticated principal + the client's own id (or its address when it sent
    none). A client id with a reserved prefix is ignored, so nobody can name another session's slot."""
    principal = getattr(h, "principal", None) or "anon"
    cid = client_session_id(client_id)
    return f"{principal}:{cid or 'ip:' + client_ip(h)}"


# ---- websocket (the PTY daemon on its own port) --------------------------------------------------------------------------
def websocket_allowed(headers, config: Dict[str, Any]) -> bool:
    """The PTY daemon's handshake: Host allowed, then the key as a bearer (no Origin needed: a browser cannot send it), or a
    valid session cookie together with an Origin from this same host name (cross-site WebSocket hijacking)."""
    if not host_allowed(headers.get("Host"), config):
        return False
    key = configured_key(config)
    if bearer_ok(headers.get("Authorization", ""), key):
        return True
    return verify_session(key, cookie_value(headers.get("Cookie", ""))) and same_site_host(headers)


# ---- command line -----------------------------------------------------------------------------------------------------
def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m stonesage.auth", description="StoneSage server key")
    p.add_argument("command", choices=["show-key"])
    p.add_argument("--config", default="", help="config.json to read (default: the server's own)")
    args = p.parse_args(list(argv) if argv is not None else None)
    path = args.config or default_config_file()
    cfg: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"cannot read {path}: {e}", file=sys.stderr)
            return 2
    key = configured_key(cfg)
    if not key:
        print(f"No key yet. Start the server once (it creates one and saves it in {path}).", file=sys.stderr)
        return 1
    where = "STONESAGE_API_KEY in the environment" if os.environ.get("STONESAGE_API_KEY", "").strip() else path
    print(key)
    print(f"(from {where})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
