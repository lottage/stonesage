#!/usr/bin/env python3
"""
Obsidian Vault Vector Ingestor for StoneSage & Qdrant Brain
Scans the user's primary Obsidian vault and embeds personal notes, home info,
tech docs, and tasks into Qdrant collections via local BGE Embedder (:8003).
"""

import os
from lodestone import endpoints, site
import re
import json
import time
import uuid
import hashlib
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Tuple

class ObsidianIngestor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        obsidian_cfg = config.get("obsidian", {})
        self.user_vault = site.obsidian_vault(config)
        self.backup_vault = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "vault_backup"))
        self.embedder_url = endpoints.url("embedder", config)
        self.qdrant_url = endpoints.url("qdrant", config)
        self.manifest_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault_sync_manifest.json")
        op = ((config.get("operator") or {}).get("name") or "").lower()
        self.operator_words = [op] if op else []         # a note naming the user is personal
        self.last_sync_status = {
            "last_synced": None,
            "total_notes": 0,
            "total_chunks": 0,
            "collections_updated": {},
            "status": "idle"
        }
        self.load_manifest()

    def load_manifest(self) -> Dict[str, Any]:
        if os.path.exists(self.manifest_file):
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_sync_status["last_synced"] = data.get("last_synced")
                    self.last_sync_status["total_notes"] = data.get("total_notes", 0)
                    self.last_sync_status["total_chunks"] = data.get("total_chunks", 0)
                    return data
            except Exception:
                pass
        return {"notes": {}, "last_synced": None}

    def save_manifest(self, manifest: Dict[str, Any]):
        try:
            with open(self.manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            print(f"[Ingestor] Warning: Failed to save manifest: {e}")

    def tokenize(self, text: str) -> List[str]:
        return re.findall(r"\b[a-zA-Z0-9_\.:-]+\b", text.lower())

    def compute_sparse_vector(self, text: str, k1: float = 1.2, b: float = 0.75, avg_len: float = 100.0) -> Dict[str, Any]:
        from collections import Counter
        tokens = self.tokenize(text)
        if not tokens:
            return {"indices": [], "values": []}
        counts = Counter(tokens)
        doc_len = len(tokens)
        indices = []
        values = []
        for word, tf in sorted(counts.items()):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
            tf_weight = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (doc_len / avg_len)))
            indices.append(h)
            values.append(round(float(tf_weight), 4))
        return {"indices": indices, "values": values}

    def get_embedding(self, text: str) -> List[float]:
        res = self.get_embeddings_batch([text])
        return res[0] if res else []

    def get_embeddings_batch(self, texts: List[str], batch_size: int = 8) -> List[List[float]]:
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = [t[:950].strip() for t in texts[i:i + batch_size] if t.strip()]
            if not batch:
                continue
            url = f"{self.embedder_url}/embeddings"
            req = urllib.request.Request(
                url,
                data=json.dumps({"input": batch}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("data", []):
                    all_embeddings.append(item["embedding"])
        return all_embeddings

    def ensure_hybrid_collection(self, col_name: str = "obsidian_vault"):
        try:
            req = urllib.request.Request(f"{self.qdrant_url}/collections/{col_name}")
            urllib.request.urlopen(req)
        except Exception:
            create_body = {
                "vectors": {"dense": {"size": 1024, "distance": "Cosine"}},
                "sparse_vectors": {"sparse": {}}
            }
            req = urllib.request.Request(
                f"{self.qdrant_url}/collections/{col_name}",
                data=json.dumps(create_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT"
            )
            try:
                urllib.request.urlopen(req)
                for field in ["path", "vault", "category", "title"]:
                    idx_req = urllib.request.Request(
                        f"{self.qdrant_url}/collections/{col_name}/index",
                        data=json.dumps({"field_name": field, "field_schema": "keyword"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="PUT"
                    )
                    urllib.request.urlopen(idx_req)
            except Exception as e:
                print(f"[Ingestor] Warning creating hybrid collection {col_name}: {e}")

    def upsert_points(self, collection_name: str, points: List[Dict[str, Any]]) -> bool:
        if not points:
            return True
        url = f"{self.qdrant_url}/collections/{collection_name}/points"
        req = urllib.request.Request(
            url,
            data=json.dumps({"points": points}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"

    def determine_collection(self, rel_path: str, content: str) -> str:
        lower_path = rel_path.lower()
        lower_content = content.lower()

        # Personal profile, home info, vehicle, wedding, education
        if any(k in lower_path for k in ["home info", "bmw", "accounting", "wedding", "gifts", "continuing education"]):
            return "companion_profile"
        if any(k in lower_content for k in ["vin:", *self.operator_words, "f30", "328i", "wife", "marriage", "degree", "wgu"]):
            return "companion_profile"

        # Tasks, jots, todos
        if any(k in lower_path for k in ["to-do", "todo", "jot", "ideas", "junk"]):
            return "agent_memories"

        # Technical skills, LLM configurations, clippings, coursework details
        return "codebase_knowledge"

    def chunk_markdown(self, rel_path: str, content: str) -> List[Dict[str, Any]]:
        title = os.path.splitext(os.path.basename(rel_path))[0]
        # Strip frontmatter
        clean = re.sub(r"^---\n[\s\S]*?\n---\n", "", content)
        
        # Split by markdown headers (# or ## or ###)
        sections = re.split(r"\n(?=#{1,4}\s+)", clean)
        chunks = []
        chunk_idx = 0

        for section in sections:
            section = section.strip()
            if not section or len(section) < 20:
                continue

            # Extract header if present
            header_match = re.match(r"^(#{1,4})\s+(.+)", section)
            header = header_match.group(2).strip() if header_match else ""

            # If section is long, break into paragraphs or lines
            if len(section) > 800:
                paragraphs = re.split(r"\n{1,2}", section)
                current_buf = []
                current_len = 0
                for p in paragraphs:
                    p = p.strip()
                    if not p:
                        continue
                    if current_len + len(p) > 750 and current_buf:
                        chunk_text = f"[{title}] {header}\n" + "\n".join(current_buf)
                        chunks.append({
                            "title": title,
                            "header": header,
                            "chunk_idx": chunk_idx,
                            "text": chunk_text[:1000].strip(),
                            "rel_path": rel_path
                        })
                        chunk_idx += 1
                        current_buf = [p]
                        current_len = len(p)
                    else:
                        current_buf.append(p)
                        current_len += len(p)
                if current_buf:
                    chunk_text = f"[{title}] {header}\n" + "\n".join(current_buf)
                    chunks.append({
                        "title": title,
                        "header": header,
                        "chunk_idx": chunk_idx,
                        "text": chunk_text[:1000].strip(),
                        "rel_path": rel_path
                    })
                    chunk_idx += 1
            else:
                chunk_text = f"[{title}] {header}\n{section}" if header and not section.startswith(f"[{title}]") else f"[{title}]\n{section}"
                chunks.append({
                    "title": title,
                    "header": header,
                    "chunk_idx": chunk_idx,
                    "text": chunk_text[:1000].strip(),
                    "rel_path": rel_path
                })
                chunk_idx += 1

        return chunks

    def sync_vault(self, progress_callback=None) -> Dict[str, Any]:
        self.last_sync_status["status"] = "syncing"
        self.ensure_hybrid_collection("obsidian_vault")
        manifest = self.load_manifest()
        manifest_notes = manifest.get("notes", {})
        
        target_vaults = []
        if os.path.exists(self.user_vault):
            target_vaults.append(("user_vault", self.user_vault))
        if os.path.exists(self.backup_vault):
            target_vaults.append(("tertiary_vault", self.backup_vault))

        if not target_vaults:
            self.last_sync_status["status"] = "error"
            self.last_sync_status["error"] = "No Obsidian vaults found."
            return self.last_sync_status

        total_notes_scanned = 0
        total_chunks_indexed = 0
        collections_count = {"obsidian_vault": 0, "companion_profile": 0, "codebase_knowledge": 0, "agent_memories": 0}

        for vault_label, vpath in target_vaults:
            for root, dirs, files in os.walk(vpath):
                # Ignore hidden and plugin directories
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["node_modules", "smart_blocks", "smart_sources"]]
                for f in files:
                    if not f.endswith(".md"):
                        continue

                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, vpath).replace("\\", "/")
                    total_notes_scanned += 1

                    try:
                        mtime = os.path.getmtime(full_path)
                        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                            content = fh.read()

                        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

                        # Check if note has changed
                        existing = manifest_notes.get(f"{vault_label}:{rel_path}")
                        if existing and existing.get("hash") == content_hash:
                            continue

                        chunks = self.chunk_markdown(rel_path, content)
                        target_col = self.determine_collection(rel_path, content)

                        if not chunks:
                            continue

                        # Batch embed all chunk texts in groups of 8
                        chunk_texts = [c["text"] for c in chunks]
                        try:
                            dense_vectors = self.get_embeddings_batch(chunk_texts, batch_size=8)
                        except Exception as embed_err:
                            print(f"[Ingestor] Error in batch embedding for {rel_path}: {embed_err}")
                            continue

                        hybrid_points = []
                        legacy_points = []

                        for idx, c in enumerate(chunks):
                            if idx >= len(dense_vectors):
                                break
                            dense_vec = dense_vectors[idx]
                            sparse_vec = self.compute_sparse_vector(c["text"])
                            point_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{vault_label}:{rel_path}:{c['chunk_idx']}"))

                            payload = {
                                "source": "obsidian",
                                "vault": vault_label,
                                "path": rel_path,
                                "title": c["title"],
                                "header": c["header"],
                                "content": c["text"],
                                "chunk_idx": c["chunk_idx"],
                                "timestamp": time.time(),
                                "category": target_col
                            }

                            # 1. Native Hybrid Point for obsidian_vault
                            hybrid_points.append({
                                "id": point_uuid,
                                "vector": {
                                    "dense": dense_vec,
                                    "sparse": sparse_vec
                                },
                                "payload": payload
                            })

                            # 2. Legacy Dense Point for backward compatibility
                            legacy_points.append({
                                "id": point_uuid,
                                "vector": dense_vec,
                                "payload": payload
                            })

                        if hybrid_points:
                            self.upsert_points("obsidian_vault", hybrid_points)
                            collections_count["obsidian_vault"] = collections_count.get("obsidian_vault", 0) + len(hybrid_points)
                            total_chunks_indexed += len(hybrid_points)

                        if legacy_points:
                            self.upsert_points(target_col, legacy_points)
                            collections_count[target_col] = collections_count.get(target_col, 0) + len(legacy_points)

                        manifest_notes[f"{vault_label}:{rel_path}"] = {
                            "hash": content_hash,
                            "mtime": mtime,
                            "chunks": len(chunks),
                            "collection": target_col
                        }

                        if progress_callback:
                            progress_callback(rel_path, len(chunks), total_chunks_indexed)

                    except Exception as note_err:
                        print(f"[Ingestor] Error processing {rel_path}: {note_err}")

        manifest["notes"] = manifest_notes
        manifest["last_synced"] = time.strftime("%Y-%m-%d %H:%M:%S")
        manifest["total_notes"] = total_notes_scanned
        manifest["total_chunks"] = manifest.get("total_chunks", 0) + total_chunks_indexed
        self.save_manifest(manifest)

        self.last_sync_status = {
            "status": "completed",
            "last_synced": manifest["last_synced"],
            "total_notes": total_notes_scanned,
            "new_chunks_indexed": total_chunks_indexed,
            "total_vault_chunks": manifest["total_chunks"],
            "collections_updated": collections_count
        }
        return self.last_sync_status

    def get_status(self) -> Dict[str, Any]:
        manifest = self.load_manifest()
        return {
            "status": self.last_sync_status.get("status", "idle"),
            "last_synced": manifest.get("last_synced", "Never"),
            "total_notes": manifest.get("total_notes", 0),
            "total_chunks": manifest.get("total_chunks", 0),
            "user_vault_path": self.user_vault,
            "exists": os.path.exists(self.user_vault)
        }

if __name__ == "__main__":
    cfg_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    cfg = {}
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    ingestor = ObsidianIngestor(cfg)
    print("Starting initial vault vector sync...")
    res = ingestor.sync_vault(lambda path, n, tot: print(f"  + Indexed {path} ({n} chunks, total {tot})"))
    print("\nSync completed successfully:")
    print(json.dumps(res, indent=2))
