"""Container entrypoint of the StoneSage / Hearth images (standard library only).

On the first start there is no config.json in the /config volume: this writes a small one from the environment the Compose
file (python -m lodestone.installer --docker) sets, then starts the server. After that config.json is yours and is never
rewritten here; edit it in the cockpit or in the volume.

    COORDINATOR_URL / WORKER_URL / EMBEDDER_URL / VISION_URL   -> cluster.<role>_url   (the engine services)
    STONESAGE_APPS   comma list of apps to load ("hearth"; empty = none)  -> apps
    HASS_URL / HASS_TOKEN                                         -> homeassistant.url / .token (only when set)
    COORDINATOR_CPU  1 when the coordinator engine runs on a CPU   -> cluster.coordinator_cpu (longer waits for its answers)
    TZ                                                            -> server.timezone (when set and not UTC)

The token is read from the environment only to be written into config.json inside the volume, where StoneSage keeps it
everywhere else; it is never printed.
"""

import json
import os
import sys
from typing import Any, Dict, Mapping

ROLE_ENV = {"coordinator": "COORDINATOR_URL", "worker": "WORKER_URL", "embedder": "EMBEDDER_URL", "vision": "VISION_URL"}
DEFAULT_CONFIG = "/config/config.json"
SERVER = "/app/StoneSage/backend/server.py"


def seed_config(env: Mapping[str, str]) -> Dict[str, Any]:
    """The first-start config.json, as a dict (only keys the environment actually gives)."""
    cfg: Dict[str, Any] = {"server": {"host": "0.0.0.0", "port": 8888}}
    cfg["apps"] = [a.strip() for a in env.get("STONESAGE_APPS", "").split(",") if a.strip()]
    cluster = {"%s_url" % role: env[var].strip() for role, var in ROLE_ENV.items() if env.get(var, "").strip()}
    if env.get("COORDINATOR_CPU", "").strip().lower() in ("1", "true", "yes", "on"):
        cluster["coordinator_cpu"] = True
    if cluster:
        cfg["cluster"] = cluster
    ha = {k: env[var].strip() for k, var in (("url", "HASS_URL"), ("token", "HASS_TOKEN")) if env.get(var, "").strip()}
    if ha:
        cfg["homeassistant"] = ha
    tz = env.get("TZ", "").strip()
    if tz and tz.upper() != "UTC":
        cfg["server"]["timezone"] = tz
    return cfg


def ensure_config(path: str, env: Mapping[str, str]) -> bool:
    """Write the seed config when `path` does not exist yet. True when it was written."""
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(seed_config(env), f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return True


def main() -> None:
    path = os.environ.get("STONESAGE_CONFIG_FILE") or DEFAULT_CONFIG
    os.environ["STONESAGE_CONFIG_FILE"] = path
    os.environ.setdefault("STONESAGE_DATA_DIR", "/data")
    if ensure_config(path, os.environ):
        print("[entrypoint] first start: wrote %s from the environment" % path, flush=True)
    os.execv(sys.executable, [sys.executable, "-u", SERVER])


if __name__ == "__main__":
    main()
