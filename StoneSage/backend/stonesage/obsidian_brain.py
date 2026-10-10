#!/usr/bin/env python3
"""
StoneSage Obsidian Brain & Graph-Vector Memory Fabric
Provides a dedicated Qdrant layer ('obsidian_brain') with:
- 1024-dim dense BGE embeddings + BM25 sparse vectors for hybrid retrieval
- Wiki-link [[Note]] extraction and 1-hop graph relationship traversal
- Frontmatter tag and category extraction
- Strict cosine score filtering (default >= 0.72) to prevent irrelevant context bleeding
"""

import os
import re
import json
import time
import uuid
import hashlib
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Set, Tuple
from collections import Counter

from lodestone import endpoints, site

logger = logging.getLogger("StoneSage.ObsidianBrain")

COLLECTION_NAME = "obsidian_brain"
DEFAULT_VAULT_PATH = site.obsidian_vault()      # config obsidian.user_vault_path / OBSIDIAN_VAULT; "" = none


class ObsidianBrain:
    def __init__(
        self,
        vault_path: str = DEFAULT_VAULT_PATH,
        qdrant_url: str = "",
        embedder_url: str = ""
    ):
        self.vault_path = vault_path
        self.qdrant_url = (qdrant_url or endpoints.url("qdrant")).rstrip("/")
        self.embedder_url = (embedder_url or endpoints.url("embedder")).rstrip("/")
        self.note_graph: Dict[str, Set[str]] = {}  # source_note -> set of linked target_notes
        self.backlinks: Dict[str, Set[str]] = {}   # target_note -> set of source_notes
        self.ensure_collection()

    def ensure_collection(self):
        """Creates the hybrid dense+sparse obsidian_brain collection if it does not exist."""
        try:
            req = urllib.request.Request(f"{self.qdrant_url}/collections/{COLLECTION_NAME}")
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            body = {
                "vectors": {
                    "dense": {"size": 1024, "distance": "Cosine"}
                },
                "sparse_vectors": {
                    "sparse": {}
                }
            }
            try:
                req = urllib.request.Request(
                    f"{self.qdrant_url}/collections/{COLLECTION_NAME}",
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT"
                )
                urllib.request.urlopen(req, timeout=5)
                # Create payload indexes for fast filtering
                for field in ["note_title", "folder", "tags", "outgoing_links"]:
                    idx_req = urllib.request.Request(
                        f"{self.qdrant_url}/collections/{COLLECTION_NAME}/index",
                        data=json.dumps({"field_name": field, "field_schema": "keyword"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="PUT"
                    )
                    urllib.request.urlopen(idx_req, timeout=3)
                logger.info(f"✔ Initialized Qdrant collection '{COLLECTION_NAME}' with hybrid dense+sparse vectors.")
            except Exception as e:
                logger.warning(f"Failed creating collection {COLLECTION_NAME}: {e}")

    def tokenize(self, text: str) -> List[str]:
        return re.findall(r"\b[a-zA-Z0-9_\.:-]+\b", text.lower())

    def compute_sparse_vector(self, text: str, k1: float = 1.2, b: float = 0.75, avg_len: float = 100.0) -> Dict[str, Any]:
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
        safe_text = text[:800].strip() if text else ""
        req = urllib.request.Request(
            f"{self.embedder_url}/embeddings",
            data=json.dumps({"input": safe_text, "model": "embedder"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["data"][0]["embedding"]

    def parse_markdown_note(self, rel_path: str, content: str) -> Dict[str, Any]:
        """Extracts title, tags, wiki-links [[...]], and clean sections from a note."""
        note_title = os.path.splitext(os.path.basename(rel_path))[0]
        folder = os.path.dirname(rel_path).replace("\\", "/")

        # 1. Frontmatter extraction
        frontmatter = {}
        tags = set()
        clean_content = content
        fm_match = re.match(r"^---\s*\n([\s\S]*?)\n---\s*\n", content)
        if fm_match:
            clean_content = content[fm_match.end():]
            fm_text = fm_match.group(1)
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip()
                    if k.strip() in ("tags", "tag"):
                        for t in re.findall(r"\b[\w-]+\b", v):
                            tags.add(t.lower())

        # 2. Inline #tags extraction (avoiding markdown headers # Header)
        inline_tags = re.findall(r"(?:^|\s)#([a-zA-Z0-9_\/-]+)", clean_content)
        for t in inline_tags:
            tags.add(t.lower())

        # 3. Wiki-link [[Note Name|Alias]] extraction
        raw_links = re.findall(r"\[\[(.*?)\]\]", clean_content)
        outgoing_links = set()
        for link in raw_links:
            target = link.split("|")[0].split("#")[0].strip()
            if target:
                outgoing_links.add(target)

        # 4. Chunk into semantic passages bounded to < 800 chars
        sections = re.split(r"\n(?=#{1,4}\s+)", clean_content)
        chunks = []
        for sec in sections:
            sec_text = sec.strip()
            if not sec_text or len(sec_text) < 15:
                continue

            header_match = re.match(r"^(#{1,4})\s+(.+)", sec_text)
            header = header_match.group(2).strip() if header_match else ""

            if len(sec_text) > 750:
                paragraphs = re.split(r"\n{1,2}", sec_text)
                cur_buf = []
                cur_len = 0
                for p in paragraphs:
                    p = p.strip()
                    if not p:
                        continue
                    if cur_len + len(p) > 700 and cur_buf:
                        chunks.append({
                            "header": header,
                            "text": f"[{note_title}] {header}\n" + "\n".join(cur_buf)
                        })
                        cur_buf = [p]
                        cur_len = len(p)
                    else:
                        cur_buf.append(p)
                        cur_len += len(p)
                if cur_buf:
                    chunks.append({
                        "header": header,
                        "text": f"[{note_title}] {header}\n" + "\n".join(cur_buf)
                    })
            else:
                chunks.append({
                    "header": header,
                    "text": f"[{note_title}] {header}\n{sec_text}"
                })

        return {
            "note_title": note_title,
            "folder": folder,
            "rel_path": rel_path,
            "tags": list(tags),
            "outgoing_links": list(outgoing_links),
            "chunks": chunks
        }

    def sync_vault(self, max_notes: int = 500) -> Dict[str, Any]:
        """Scans the Obsidian vault and indexes notes into obsidian_brain."""
        if not os.path.exists(self.vault_path):
            return {"status": "error", "message": f"Vault path does not exist: {self.vault_path}"}

        notes_indexed = 0
        chunks_indexed = 0
        points = []

        # Reset in-memory graph
        self.note_graph.clear()
        self.backlinks.clear()

        for root, dirs, files in os.walk(self.vault_path):
            # Ignore hidden or system directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["templates", "node_modules"]]
            for file in files:
                if not file.endswith(".md"):
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.vault_path)

                # Skip raw skill clones or junk that pollutes personal notes
                if any(bad in rel_path.lower() for bad in ["skills/", ".agents/", "junk/"]):
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                        content = f.read()

                    note_data = self.parse_markdown_note(rel_path, content)
                    title = note_data["note_title"]
                    links = note_data["outgoing_links"]
                    self.note_graph[title] = set(links)
                    for lk in links:
                        self.backlinks.setdefault(lk, set()).add(title)

                    for idx, chunk in enumerate(note_data["chunks"]):
                        chunk_text = chunk["text"][:900]
                        dense_vec = self.get_embedding(chunk_text)
                        sparse_vec = self.compute_sparse_vector(chunk_text)

                        # Deterministic UUID per chunk
                        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{rel_path}:{idx}"))
                        point = {
                            "id": chunk_id,
                            "vector": {
                                "dense": dense_vec,
                                "sparse": sparse_vec
                            },
                            "payload": {
                                "note_title": title,
                                "folder": note_data["folder"],
                                "rel_path": rel_path,
                                "header": chunk["header"],
                                "tags": note_data["tags"],
                                "outgoing_links": links,
                                "content": chunk_text,
                                "chunk_idx": idx,
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                            }
                        }
                        points.append(point)
                        chunks_indexed += 1

                    notes_indexed += 1
                    if notes_indexed >= max_notes:
                        break
                except Exception as e:
                    logger.debug(f"Error indexing {rel_path}: {e}")

            if notes_indexed >= max_notes:
                break

        # Batch upsert to Qdrant
        batch_size = 32
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            req = urllib.request.Request(
                f"{self.qdrant_url}/collections/{COLLECTION_NAME}/points",
                data=json.dumps({"points": batch}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT"
            )
            try:
                urllib.request.urlopen(req, timeout=15)
            except Exception as e:
                logger.error(f"Failed upserting batch to {COLLECTION_NAME}: {e}")

        logger.info(f"✔ Obsidian Brain synced: {notes_indexed} notes, {chunks_indexed} passages.")
        return {
            "status": "success",
            "notes_indexed": notes_indexed,
            "chunks_indexed": chunks_indexed
        }

    def search(
        self,
        query: str,
        limit: int = 4,
        score_threshold: float = 0.72,
        expand_graph: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Performs hybrid semantic search with strict score thresholding.
        Optionally traverses wiki-link graph to retrieve 1-hop connected notes.
        """
        if not query or len(query.strip()) < 3:
            return []

        dense_vec = self.get_embedding(query)
        sparse_vec = self.compute_sparse_vector(query)

        prefetch = [
            {"query": dense_vec, "using": "dense", "limit": limit * 2}
        ]
        if sparse_vec["indices"]:
            prefetch.append({
                "query": {"indices": sparse_vec["indices"], "values": sparse_vec["values"]},
                "using": "sparse",
                "limit": limit * 2
            })

        body = {
            "prefetch": prefetch,
            "query": {"rrf": {}},
            "limit": limit,
            "with_payload": True,
            "params": {"hnsw_ef": 128}
        }

        results = []
        try:
            req = urllib.request.Request(
                f"{self.qdrant_url}/collections/{COLLECTION_NAME}/points/query",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                points = data.get("result", {}).get("points", [])
        except Exception:
            # Fallback to standard dense search
            url = f"{self.qdrant_url}/collections/{COLLECTION_NAME}/points/search"
            req = urllib.request.Request(
                url,
                data=json.dumps({"vector": dense_vec, "limit": limit, "with_payload": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                points = data.get("result", [])

        # Filter strictly by score threshold
        for pt in points:
            score = float(pt.get("score", 0.0))
            if score >= score_threshold:
                results.append(pt)

        # Graph link traversal: if high-confidence match found, enrich with connected note headers
        if expand_graph and results:
            top_pt = results[0]
            top_title = top_pt.get("payload", {}).get("note_title", "")
            outgoing = list(self.note_graph.get(top_title, set()))[:3]
            back = list(self.backlinks.get(top_title, set()))[:3]
            if outgoing or back:
                top_pt["payload"]["graph_relations"] = {
                    "links_to": outgoing,
                    "referenced_by": back
                }

        return results


# Global singleton
obsidian_brain = ObsidianBrain()
