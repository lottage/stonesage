#!/usr/bin/env python3
"""
Homelab Database to Obsidian Summary Generator
Queries the Qdrant vector database and the BGE embedder (config.json, lodestone/endpoints.py) and writes a comprehensive, live cyber-brutalist status
and directory note into the Obsidian vault:
'<vault>/Homelab Brain & Vector Database.md' (vault: config obsidian.user_vault_path or OBSIDIAN_VAULT)
"""

import os
from lodestone import endpoints, site
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

QDRANT_URL = endpoints.url("qdrant")
EMBEDDER_URL = endpoints.url("embedder") + "/models"
DEFAULT_VAULT = site.obsidian_vault() or "/opt/stonesage/vault_backup"
OBSIDIAN_VAULT = os.getenv("OBSIDIAN_VAULT", DEFAULT_VAULT)
NOTE_FILENAME = "Homelab Brain & Vector Database.md"
TARGET_PATH = os.path.join(OBSIDIAN_VAULT, NOTE_FILENAME)


def fetch_json(url: str, timeout: float = 5.0):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, str(e)


def scroll_collection_payloads(collection: str, limit: int = 250):
    """Scrolls points to extract unique skill and doc payloads."""
    url = f"{QDRANT_URL}/collections/{collection}/points/scroll"
    payload = json.dumps({
        "limit": limit,
        "with_payload": True,
        "with_vector": False
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("result", {}).get("points", [])
    except Exception:
        return []


def generate_summary():
    t0 = time.time()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Probe Embedder
    emb_ok, emb_data = fetch_json(EMBEDDER_URL)
    emb_status = "🟢 ONLINE (bge-large-en-v1.5 / 1024-d / 512 ctx)" if emb_ok else "🔴 OFFLINE"

    # 2. Probe Qdrant Collections
    qdrant_ok, col_data = fetch_json(f"{QDRANT_URL}/collections")
    if not qdrant_ok:
        print(f"[ERR] Failed connecting to Qdrant: {col_data}")
        return False

    collections = col_data.get("result", {}).get("collections", [])
    col_stats = []
    total_vectors = 0

    for c in collections:
        c_name = c["name"]
        ok, c_info = fetch_json(f"{QDRANT_URL}/collections/{c_name}")
        if ok:
            res = c_info.get("result", {})
            pts = res.get("points_count", 0)
            status = res.get("status", "green")
            total_vectors += pts
            col_stats.append({
                "name": c_name,
                "points": pts,
                "status": status
            })

    col_stats.sort(key=lambda x: x["name"])

    # 3. Extract Skills from codebase_knowledge
    points = scroll_collection_payloads("codebase_knowledge", limit=300)
    skills_map = {}
    docs_map = {}

    for pt in points:
        pl = pt.get("payload", {})
        pt_type = pl.get("type", "unknown")
        if pt_type == "skill":
            s_name = pl.get("skill_name", "unknown")
            desc = pl.get("description", "").strip()
            if not desc or desc in (">", ">-", "|", "|-", '""', "''"):
                # Extract first descriptive sentence from chunk text
                text = pl.get("text", "")
                for line in text.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("[") and not line.startswith("#") and not line.startswith("name:") and len(line) > 15:
                        desc = line
                        break
            if s_name not in skills_map:
                skills_map[s_name] = {
                    "name": s_name,
                    "description": desc or "Automated agent workflow & capability",
                    "chunks": 0
                }
            elif (not skills_map[s_name]["description"] or skills_map[s_name]["description"] in (">", ">-")) and desc:
                skills_map[s_name]["description"] = desc
            skills_map[s_name]["chunks"] += 1
        elif pt_type == "workspace_doc":
            d_title = pl.get("title", "unknown")
            if d_title not in docs_map:
                docs_map[d_title] = 0
            docs_map[d_title] += 1

    # 4. Extract Invariants from agent_memories
    mem_points = scroll_collection_payloads("agent_memories", limit=100)
    invariants = []
    for pt in mem_points:
        pl = pt.get("payload", {})
        if pl.get("type") == "decision":
            invariants.append({
                "title": pl.get("title", "Rule"),
                "text": pl.get("text", "")
            })

    # 5. Build Markdown Content
    lines = []
    lines.append("# 🧠 Homelab Vector Brain & Skill Knowledge Base")
    lines.append("")
    lines.append(f"> **Database Host**: `{QDRANT_URL}` | **Metric**: `Cosine (1024-d)`")
    lines.append(f"> **Embedding Host**: `{emb_status}`")
    lines.append(f"> **Total Knowledge Vectors**: `{total_vectors:,} points` across `{len(col_stats)} collections`")
    lines.append(f"> **Last Synced**: `{now_str}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📦 Qdrant Collection Registry")
    lines.append("")
    lines.append("| Collection Name | Points / Vectors | Status | Primary Content Domain |")
    lines.append("| :--- | :---: | :---: | :--- |")

    domain_desc = {
        "codebase_knowledge": "Skills repository, workspace architecture docs, device topologies",
        "agent_memories": "Operational invariants, sampling parameters, safety gates",
        "companion_profile": "System configuration, user preferences, hardware specifications",
        "home_automation_registry": "HA device entity map, voice trigger sentences, area routes",
        "session_transcripts": "Historical conversation summaries, trajectory milestones",
        "autonomous_thinking": "24/7 dual-GPU cognitive frontier dossiers & limit critiques",
        "obsidian_vault": "Hybrid indexed user notes, research dossiers, and clippings",
    }

    for cs in col_stats:
        desc = domain_desc.get(cs["name"], "General homelab vector storage")
        lines.append(f"| **`{cs['name']}`** | **{cs['points']}** | `🟢 {cs['status'].upper()}` | {desc} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## 🛠️ Indexed Usable Skills Directory ({len(skills_map)} Skills)")
    lines.append("")
    lines.append("Skills indexed in `codebase_knowledge` available for on-demand retrieval at minimal token cost:")
    lines.append("")
    lines.append("| Skill Identifier | Chunks | Summary / Capability |")
    lines.append("| :--- | :---: | :--- |")

    for s_name in sorted(skills_map.keys()):
        s_info = skills_map[s_name]
        d = s_info["description"]
        if len(d) > 90:
            d = d[:87] + "..."
        if not d:
            d = "Homelab automated workflow & procedural skill"
        lines.append(f"| **`{s_name}`** | `{s_info['chunks']}` | {d} |")

    if docs_map:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## 📄 Indexed Workspace Documentation ({len(docs_map)} Documents)")
        lines.append("")
        lines.append("| Document Name | Indexed Chunks | Target Collection |")
        lines.append("| :--- | :---: | :--- |")
        for d_title, d_chunks in sorted(docs_map.items()):
            lines.append(f"| **`{d_title}`** | `{d_chunks}` | `codebase_knowledge` |")

    if invariants:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## ⚡ Core Operational Invariants & Rules ({len(invariants)} Verified)")
        lines.append("")
        for inv in invariants[:8]:
            lines.append(f"- **{inv['title']}**: {inv['text']}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 🔍 Minimal-Token Retrieval Query Pattern")
    lines.append("")
    lines.append("To fetch a skill during execution without burning hundreds of tokens on manual file reads:")
    lines.append("```python")
    lines.append("# Query Qdrant directly or via the cluster MCP:")
    lines.append("search_memory(query=\"ha-voice-triggers movie night\", collection_name=\"codebase_knowledge\", limit=2)")
    lines.append("```")
    lines.append("")
    lines.append("> *Note generated automatically by `.agents/skills/database-memory-sync/sync_db_to_obsidian.py`.*")

    content = "\n".join(lines)

    # Write note
    os.makedirs(OBSIDIAN_VAULT, exist_ok=True)
    with open(TARGET_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    dt = time.time() - t0
    print(f"[OK] Wrote database summary to Obsidian: {TARGET_PATH} ({len(content)} bytes in {dt:.2f}s)")
    return True


if __name__ == "__main__":
    generate_summary()
