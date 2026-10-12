"""What the cockpit page may show: `GET /api/ui/features` (docs/LAYERS.md, "M6 rc.2: a fresh install shows only what is real").

The page used to carry one homelab's panels in its HTML. Now it starts bare and asks this route which of its parts exist on
THIS install: the product's version, the apps that are loaded (and what each one says about itself through the `ui_features`
hook), which feature flags are on, which optional services config.json names, which cloud providers have a key, and a real
token tally if there is one. Nothing here is a reading: every field is a fact about the configuration, so the route is cheap
and never reaches a device. A part whose source is missing is absent (or false), and the page hides it.

    {"ok": true, "product": "StoneSage", "version": "0.1.0",
     "apps": [{"id": "hearth", "name": "Hearth"}],
     "agent": {"id": "courage-computer", "name": "Computer"},
     "features": {"web_terminal": false, "file_editor": false, "code_tools": false, "raw_ha_call": false, "shelved": false},
     "configured": {"worker": false, "vision": false, "mcp": false, "qdrant": false, "couchdb": false, "voice": false, ...},
     "obsidian": false,
     "providers": {"gemini": false},
     "usage": null,
     "home": {...}}        <- whatever the loaded app's `ui_features` hook returns (Hearth: people, cameras, zones, questions)
"""

import os
from typing import Any, Callable, Dict, Optional

from lodestone import endpoints, site
from stonesage import auth

OWNER = "stonesage"
AGENT_ID = "courage-computer"          # the product's default agent (its code name stays; the person sees `name`)
DEFAULT_AGENT_NAME = "Computer"
# Services the page cares about showing or hiding (endpoints.SERVICES names)
SHOWN_SERVICES = ("worker", "vision", "mcp", "qdrant", "couchdb", "voice", "frontier", "gemini_web")


def app_name(app_id: str) -> str:
    return str(app_id).replace("_", " ").replace("-", " ").title()


def agent_name(data_dir: str) -> str:
    """The default agent's display name, from its soul (stonesage/soul.py); the product's own name for it until it exists."""
    try:
        from stonesage import soul
        for manifest in soul.list_souls(data_dir):
            if manifest.get("id") == AGENT_ID and str(manifest.get("name") or "").strip():
                return str(manifest["name"]).strip()
    except Exception:
        pass
    return DEFAULT_AGENT_NAME


def _has_key(cfg: Dict[str, Any], provider: str, env: str) -> bool:
    key = str(((cfg.get("external_providers") or {}).get(provider) or {}).get("api_key") or "").strip()
    return bool(key) or bool(os.environ.get(env, "").strip())


def cloud_tokens_today(router: Any) -> Optional[int]:
    """Tokens sent to cloud sources today, from Boost's own quota book; None when Boost is off or has sent nothing (no
    counter is better than a made-up one)."""
    try:
        status = router.status() if router is not None else None
        if not status or not status.get("enabled"):
            return None
        total = sum(int((p.get("usage") or {}).get("tokens_today") or 0) for p in status.get("providers", [])
                    if p.get("tier") != "local")
        return total or None
    except Exception:
        return None


def build(config: Dict[str, Any], version: str, apps, data_dir: str, home: Optional[Callable[[], Dict[str, Any]]] = None,
          boost_router: Any = None) -> Dict[str, Any]:
    home_block: Dict[str, Any] = {}
    if home is not None:
        try:
            home_block = home() or {}
        except Exception as e:                       # one app's trouble must not blank the whole page
            home_block = {"error": f"{type(e).__name__}: {e}"}
    tokens = cloud_tokens_today(boost_router)
    return {
        "ok": True,
        "product": "StoneSage",
        "version": version,
        "apps": [{"id": a, "name": app_name(a)} for a in apps],
        "agent": {"id": AGENT_ID, "name": agent_name(data_dir)},
        "features": {name: auth.feature_enabled(config, name) for name in auth.FEATURE_NAMES},
        "configured": {name: endpoints.configured(name, config) for name in SHOWN_SERVICES},
        # the Obsidian tab reads a vault folder or the CouchDB it syncs through: no vault, no tab
        "obsidian": bool(site.obsidian_vault(config)) or endpoints.configured("couchdb", config),
        # a cloud provider is "configured" when a key is stored; it is not probed (no call is made for a page load)
        "providers": {"gemini": _has_key(config, "gemini", "GEMINI_API_KEY")},
        "usage": {"cloud_tokens_today": tokens} if tokens else None,
        "home": home_block,
    }


def install(ctx, version: str, data_dir: Callable[[], str], boost_router: Optional[Callable[[], Any]] = None) -> None:
    """Register GET /api/ui/features on the app route table (behind the login like every other route)."""
    def route(h, parsed, body):
        h.send_json(build(ctx.config, version, ctx.apps, data_dir(), ctx.hook("ui_features"),
                          boost_router() if boost_router else None))
    ctx.routes.add("GET", "/api/ui/features", route, owner=OWNER)
