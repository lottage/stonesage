"""Apps on the StoneSage platform (docs/LAYERS.md, layer 1 -> 2).

The platform never imports an app. An app is a package with an `app.py` whose `install(ctx)` registers what it brings:
HTTP routes, hooks the platform calls (a phone notifier, extra prompt context, privacy terms, the agent it contributes),
and things to start once the server is up. Which apps run is configuration (`config.json "apps"`), like Django's
INSTALLED_APPS, so the same platform runs with Home, with another app, or with none.

    def install(ctx):
        ctx.routes.add("GET", "/api/patrol/status", patrol_status)          # fn(handler, parsed, body)
        ctx.hooks["notify_phone"] = send_push                               # the platform calls ctx.hook("notify_phone")
        ctx.on_start(start_background_services)                             # threads / services, once the server is up
        ctx.add_metrics(lambda: ["my_app_gauge 1"])                         # lines appended to GET /metrics
        ctx.add_scoped_key("my-app", [("POST", "/api/my/v1/x")], key_fn)    # a key that opens exactly these routes
        ctx.add_static(os.path.join(HERE, "web"))                           # served (login required) at /apps/<app>/<file>
        ctx.add_tab("my-app", "MY APP", "board.js", open="openMyApp")       # a tab the cockpit builds from GET /api/apps

Where an app comes from. `config.json "apps"` lists them by name. A name is looked up first in the Python entry points of the
group `stonesage.apps` (an installed package declares `[project.entry-points."stonesage.apps"]` with `grove = "grove.app"`),
then as a package `<name>` with an `app.py`. Installing a package never turns it on: only the names in `apps` are loaded.
With none listed (or none installed) nothing is added to the server or the page.
"""

import importlib
import logging
import os
import sys
import urllib.parse
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from lodestone import compat
from stonesage import API_VERSION, auth

logger = logging.getLogger("StoneSage.apps")

RouteFn = Callable[[Any, Any, Dict[str, Any]], None]      # (handler, parsed url, json body or {}) -> writes the response
ENTRY_POINT_GROUP = "stonesage.apps"
# What an app's static folder may serve (with the type the page needs: a module script must not arrive as text/plain)
STATIC_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
                ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".txt": "text/plain", ".map": "application/json"}
TEXT_TYPES = (".js", ".mjs", ".css", ".json", ".svg", ".txt", ".map")


class Routes:
    """Exact paths first, then the longest matching prefix ('/api/frigate/snapshot/' + id)."""

    def __init__(self):
        self._exact: Dict[Tuple[str, str], Tuple[str, RouteFn]] = {}
        self._prefix: List[Tuple[str, str, str, RouteFn]] = []
        self._features: Dict[Tuple[str, str], str] = {}        # (method, path) -> config features.<name> that must be true

    def feature(self, method: str, path: str) -> str:
        """The feature flag a route sits behind ('' when none). The gate (auth.py) answers 404 while it is off."""
        method = method.upper()
        flag = self._features.get((method, path))
        if flag:
            return flag
        for (m, p), f in self._features.items():
            if m == method and p.endswith("*") and path.startswith(p[:-1]):
                return f
        return ""

    def add(self, method: str, path: str, fn: RouteFn, prefix: bool = False, owner: str = "", feature: str = "") -> None:
        method = method.upper()
        if feature:
            self._features[(method, path + "*" if prefix else path)] = feature
        if prefix:
            self._prefix = [p for p in self._prefix if (p[0], p[1]) != (method, path)] + [(method, path, owner, fn)]
            self._prefix.sort(key=lambda p: -len(p[1]))
        else:
            if (method, path) in self._exact and self._exact[(method, path)][0] != owner:
                raise ValueError(f"route {method} {path} is already owned by {self._exact[(method, path)][0]!r}")
            self._exact[(method, path)] = (owner, fn)

    def find(self, method: str, path: str) -> Optional[RouteFn]:
        hit = self._exact.get((method.upper(), path))
        if hit:
            return hit[1]
        for m, p, _owner, fn in self._prefix:
            if m == method.upper() and path.startswith(p):
                return fn
        return None

    def table(self) -> List[Dict[str, str]]:
        rows = [{"method": m, "path": p, "app": o} for (m, p), (o, _f) in self._exact.items()]
        rows += [{"method": m, "path": p + "*", "app": o} for m, p, o, _f in self._prefix]
        return sorted(rows, key=lambda r: (r["path"], r["method"]))


class LiveRef:
    """Stands in for an object the platform may REPLACE at runtime (e.g. the Home Assistant client is rebuilt when its
    settings are saved): every attribute is looked up on the current object, so an app never keeps a stale copy."""

    def __init__(self, get: Callable[[], Any]):
        object.__setattr__(self, "_get", get)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_get")(), name)

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_get")())

    def __repr__(self) -> str:
        return f"LiveRef({object.__getattribute__(self, '_get')()!r})"


def discover() -> Dict[str, str]:
    """{app name: module path} of the installed packages that declare an entry point in the group `stonesage.apps`."""
    try:
        from importlib import metadata
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as e:
        logger.warning(f"could not list the {ENTRY_POINT_GROUP} entry points: {e}")
        return {}
    return {ep.name: ep.value.split(":")[0].strip() for ep in eps}


def _static_route(app: str, folders: Dict[str, str]) -> RouteFn:
    """GET /apps/<app>/<file>: a file of the app's static folder, realpath-contained, by extension allowlist. The gate has already
    asked for the login (this path is not part of the public web UI)."""
    prefix = f"/apps/{app}/"

    def serve(h, parsed, _body):
        rel = urllib.parse.unquote(parsed.path[len(prefix):]).replace("\\", "/")
        root = folders.get(app, "")
        target = ""
        if root and "\0" not in rel:
            target = os.path.realpath(os.path.join(root, *[p for p in rel.split("/") if p not in ("", ".")]))
        ext = os.path.splitext(target)[1].lower()
        if not target or not target.startswith(root + os.sep) or ext not in STATIC_TYPES or not os.path.isfile(target):
            auth.reply(h, 404, {"ok": False, "error": "not found"})
            return
        with open(target, "rb") as f:
            data = f.read()
        h.send_response(200)
        h.send_header("Content-Type", STATIC_TYPES[ext] + ("; charset=utf-8" if ext in TEXT_TYPES else ""))
        h.send_header("Content-Length", str(len(data)))
        h.send_header("Cache-Control", "no-cache")
        h.end_headers()
        if getattr(h, "command", "") != "HEAD":
            h.wfile.write(data)
    return serve


class AppContext:
    """What an app gets from the platform. `services` are the platform's own (agent, trace log, lease...); `hooks` are
    filled by apps and called by the platform, which must work when a hook is missing (no app installed)."""

    def __init__(self, config: Dict[str, Any], root_dir: str, data_dir: Callable[[], str], tool_registry: Any = None,
                 services: Optional[Dict[str, Any]] = None):
        self.config = config                      # the platform's live config object (mutated in place, never replaced)
        self.root_dir = root_dir
        self.data_dir = data_dir
        self.tool_registry = tool_registry
        self.services: Dict[str, Any] = dict(services or {})
        self.hooks: Dict[str, Callable[..., Any]] = {}
        self.routes = Routes()
        self.apps: List[str] = []
        self.versions: Dict[str, str] = {}
        self._starters: List[Tuple[str, Callable[[], None]]] = []
        self._metrics: List[Tuple[str, Callable[[], Iterable[str]]]] = []
        self._tabs: List[Dict[str, Any]] = []
        self._static: Dict[str, str] = {}                  # app -> the folder served at /apps/<app>/
        self._scoped: List[str] = []
        self._current = ""

    def hook(self, name: str) -> Optional[Callable[..., Any]]:
        return self.hooks.get(name)

    def on_start(self, fn: Callable[[], None]) -> None:
        self._starters.append((self._current, fn))

    def add_route(self, method: str, path: str, fn: RouteFn, prefix: bool = False, feature: str = "") -> None:
        """`feature`: a route that is off unless config features.<name> is true (and is behind the login either way)."""
        self.routes.add(method, path, fn, prefix=prefix, owner=self._current, feature=feature)

    # ---- what an app can bring besides routes and hooks ------------------------------------------------------------
    def add_metrics(self, fn: Callable[[], Iterable[str]]) -> None:
        """`fn()` returns Prometheus text lines; they are appended to GET /metrics (a failing one is skipped and logged)."""
        self._metrics.append((self._current, fn))

    def metrics_lines(self) -> List[str]:
        out: List[str] = []
        for app, fn in self._metrics:
            try:
                out += [str(x) for x in (fn() or [])]
            except Exception as e:
                logger.warning(f"app {app!r}: metrics failed: {e}")
        return out

    def add_scoped_key(self, name: str, routes: Iterable[Tuple[str, str]], key_fn: Callable[[Dict[str, Any]], str]) -> None:
        """A key of the app's own that authenticates exactly `routes` (method, path) and nothing else (auth.SCOPED_KEYS); a key
        equal to the server key counts as not set. The routes themselves are registered with add_route as usual."""
        auth.register_scoped_key(name, routes, key_fn)
        self._scoped.append(name)

    def add_static(self, directory: str) -> None:
        """Serve the files of `directory` (JS, CSS, JSON, images) at /apps/<app>/<file>, behind the login like every API route."""
        if not self._current:
            raise ValueError("add_static is only for an app being installed")
        self._static[self._current] = os.path.realpath(directory)
        self.routes.add("GET", f"/apps/{self._current}/", _static_route(self._current, self._static), prefix=True, owner=self._current)

    def add_tab(self, tab_id: str, label: str, script: str, open: str = "", target: str = "engine_console",
                hide_engine_tabs: bool = True) -> None:
        """A tab for the cockpit. The page asks GET /api/apps, makes the button and an empty pane (`<div id="<tab_id>-root">`), and
        when the tab is first opened imports `script` (a file of the app's static folder) and calls `window[open]()`."""
        if not self._current:
            raise ValueError("add_tab is only for an app being installed")
        self._tabs.append({"app": self._current, "id": str(tab_id), "label": str(label), "script": f"/apps/{self._current}/{script}",
                           "open": str(open), "target": target, "hide_engine_tabs": bool(hide_engine_tabs)})

    def manifest(self) -> Dict[str, Any]:
        """What GET /api/apps answers: the loaded apps with their tabs, and the installed ones that are not switched on."""
        rows = [{"id": a, "name": a.replace("_", " ").replace("-", " ").title(), "version": self.versions.get(a, ""),
                 "tabs": [{k: v for k, v in t.items() if k != "app"} for t in self._tabs if t["app"] == a]} for a in self.apps]
        return {"ok": True, "apps": rows, "available": sorted(n for n in discover() if n not in self.apps)}

    def start(self) -> None:
        for app, fn in self._starters:
            try:
                fn()
            except Exception as e:                # one app's background service must not stop the server
                logger.error(f"app {app!r}: a start hook failed: {e}")


def install_platform_routes(ctx: AppContext) -> None:
    """The platform's own route for apps: GET /api/apps (login required), the manifest the page builds its app tabs from."""
    ctx.routes.add("GET", "/api/apps", lambda h, parsed, body: auth.reply(h, 200, ctx.manifest()), owner="stonesage")


# Old names that existing installs still have in config.json "apps". The Home app's package was renamed `home` -> `hearth`
# (Hearth is the name of the layer-2 repo); an install whose config says ["home"] keeps working unchanged.
APP_ALIASES = {"home": "hearth"}


def load_apps(names: List[str], ctx: AppContext) -> List[str]:
    """Import each app's module (entry point `stonesage.apps`, else `<name>.app`) and call its install(ctx), in order. A broken app
    is logged loudly and skipped. An old name (APP_ALIASES) loads the app that replaced it, and is reported under its new name."""
    loaded = list(ctx.apps)
    found = discover()
    for name in names:
        name = APP_ALIASES.get(name, name)
        if name in loaded:
            continue
        ctx._current = name
        try:
            target = found.get(name) or f"{name}.app"
            package = target.split(".")[0]
            wanted = getattr(importlib.import_module(package), "STONESAGE_API", None)     # the API the app was written for
            version = str(getattr(sys.modules[package], "__version__", "?"))
            if wanted is not None:
                compat.require(f"app {name}", version, "StoneSage", API_VERSION, str(wanted))
            module = importlib.import_module(target)
            module.install(ctx)
            ctx.versions[name] = version
            loaded.append(name)
        except Exception as e:
            logger.error(f"app {name!r} did not load: {type(e).__name__}: {e}")
        finally:
            ctx._current = ""
    ctx.apps = loaded
    return loaded
