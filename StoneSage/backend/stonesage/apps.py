"""Apps on the StoneSage platform (docs/LAYERS.md, layer 1 -> 2).

The platform never imports an app. An app is a package with an `app.py` whose `install(ctx)` registers what it brings:
HTTP routes, hooks the platform calls (a phone notifier, extra prompt context, privacy terms, the agent it contributes),
and things to start once the server is up. Which apps run is configuration (`config.json "apps"`), like Django's
INSTALLED_APPS, so the same platform runs with Home, with another app, or with none.

    def install(ctx):
        ctx.routes.add("GET", "/api/patrol/status", patrol_status)          # fn(handler, parsed, body)
        ctx.hooks["notify_phone"] = send_push                               # the platform calls ctx.hook("notify_phone")
        ctx.on_start(start_background_services)
"""

import importlib
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("StoneSage.apps")

RouteFn = Callable[[Any, Any, Dict[str, Any]], None]      # (handler, parsed url, json body or {}) -> writes the response


class Routes:
    """Exact paths first, then the longest matching prefix ('/api/frigate/snapshot/' + id)."""

    def __init__(self):
        self._exact: Dict[Tuple[str, str], Tuple[str, RouteFn]] = {}
        self._prefix: List[Tuple[str, str, str, RouteFn]] = []

    def add(self, method: str, path: str, fn: RouteFn, prefix: bool = False, owner: str = "") -> None:
        method = method.upper()
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
        self._starters: List[Tuple[str, Callable[[], None]]] = []
        self._current = ""

    def hook(self, name: str) -> Optional[Callable[..., Any]]:
        return self.hooks.get(name)

    def on_start(self, fn: Callable[[], None]) -> None:
        self._starters.append((self._current, fn))

    def add_route(self, method: str, path: str, fn: RouteFn, prefix: bool = False) -> None:
        self.routes.add(method, path, fn, prefix=prefix, owner=self._current)

    def start(self) -> None:
        for app, fn in self._starters:
            try:
                fn()
            except Exception as e:                # one app's background service must not stop the server
                logger.error(f"app {app!r}: a start hook failed: {e}")


# Old names that existing installs still have in config.json "apps". The Home app's package was renamed `home` ->
# `hearth` (Hearth is the name of the layer-2 repo); an install whose config says ["home"] keeps working unchanged.
APP_ALIASES = {"home": "hearth"}


def load_apps(names: List[str], ctx: AppContext) -> List[str]:
    """Import `<name>.app` and call its install(ctx), in order. A broken app is logged loudly and skipped.
    An old name (APP_ALIASES) loads the app that replaced it, and is reported under its new name."""
    loaded = []
    for name in names:
        name = APP_ALIASES.get(name, name)
        if name in loaded:
            continue
        ctx._current = name
        try:
            module = importlib.import_module(f"{name}.app")
            module.install(ctx)
            loaded.append(name)
        except Exception as e:
            logger.error(f"app {name!r} did not load: {type(e).__name__}: {e}")
        finally:
            ctx._current = ""
    ctx.apps = loaded
    return loaded
