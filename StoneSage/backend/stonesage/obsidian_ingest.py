#!/usr/bin/env python3
"""
StoneSage Obsidian Multimodal Ingestion Engine
Captures URLs, files, code snippets, and photos directly into:
1. CouchDB dedicated database ('ai_obsidian' on the CouchDB host :5984)
2. Local Markdown vault mirrors on disk
3. Qdrant hybrid vector memory ('obsidian_brain' on the Qdrant host :6333)
4. Valkey A-MEM working memory (< 35 token atomic cards on the engine host :6379)
"""

import os
from lodestone import endpoints, site
import sys
import re
import time
import json
import uuid
import base64
import hashlib
import logging
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser
from typing import Dict, Any, List, Optional, Tuple

try:
    from PIL import Image
    import io
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger("StoneSage.ObsidianIngest")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # stonesage/ -> backend/
ROOT_DIR = os.path.dirname(BACKEND_DIR)
CONFIG_FILE = os.path.join(BACKEND_DIR, "config.json")
UPLOADS_CAPTURES_DIR = os.path.join(ROOT_DIR, "frontend", "uploads", "captures")
VAULT_BACKUP_AI = os.path.join(ROOT_DIR, "vault_backup", "ai_obsidian")

os.makedirs(UPLOADS_CAPTURES_DIR, exist_ok=True)
os.makedirs(VAULT_BACKUP_AI, exist_ok=True)


class SimpleHTMLTextExtractor(HTMLParser):
    """Strips tags, scripts, and stylesheets, converting HTML to clean readable text."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.in_script = False
        self.in_style = False
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in ("script", "noscript"):
            self.in_script = True
        elif tag_lower == "style":
            self.in_style = True
        elif tag_lower == "title":
            self.in_title = True
        elif tag_lower in ("p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr", "section", "article"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in ("script", "noscript"):
            self.in_script = False
        elif tag_lower == "style":
            self.in_style = False
        elif tag_lower == "title":
            self.in_title = False
        elif tag_lower in ("p", "div", "h1", "h2", "h3", "h4", "li", "tr", "section", "article"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self.in_script or self.in_style:
            return
        if self.in_title:
            self.title += data
        else:
            self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        cleaned = re.sub(r"\n\s*\n+", "\n\n", raw)
        return cleaned.strip()


class ObsidianIngestEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._load_config()
        self.couch_cfg = self.config.get("couchdb", {})
        self.couch_url = endpoints.url("couchdb", self.config)
        self.couch_user = self.couch_cfg.get("username", "")
        self.couch_pass = self.couch_cfg.get("password", os.environ.get("COUCHDB_PASSWORD", ""))
        self.default_db = "ai_obsidian"
        
        self.qdrant_url = endpoints.url("qdrant", self.config)
        self.embedder_url = endpoints.url("embedder", self.config)
        self.vision_url = endpoints.url("vision", self.config)
        self.worker_url = endpoints.url("worker", self.config)
        self.valkey_host = endpoints.host("valkey", self.config)
        self.valkey_port = endpoints.port("valkey", self.config)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _get_couch_headers(self) -> Dict[str, str]:
        auth_bytes = f"{self.couch_user}:{self.couch_pass}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("ascii")
        return {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def _sanitize_slug(self, text: str) -> str:
        s = re.sub(r"[^\w\s-]", "", text.strip())
        s = re.sub(r"[-\s]+", "-", s).strip("-")
        return s[:60] if s else f"note-{int(time.time())}"

    def chunk_text(self, text: str, max_chars: int = 750, overlap: int = 100) -> List[str]:
        """Strictly bounds chunks to < 800 characters per BGE-Large 512-token limit invariant."""
        if not text:
            return []
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(text_len, start + max_chars)
            if end < text_len:
                break_pos = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
                if break_pos > start + 200:
                    end = break_pos + 1
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= text_len:
                break
            start = max(start + 1, end - overlap)
        return chunks

    def compute_sparse_vector(self, text: str) -> Dict[str, Any]:
        """Computes BM25 term frequency sparse vector."""
        tokens = re.findall(r"\b[a-zA-Z0-9_\.:-]+\b", text.lower())
        if not tokens:
            return {"indices": [], "values": []}
        from collections import Counter
        counts = Counter(tokens)
        doc_len = len(tokens)
        avg_len = 100.0
        k1, b = 1.2, 0.75
        indices = []
        values = []
        for word, tf in sorted(counts.items()):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
            weight = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (doc_len / avg_len)))
            indices.append(h)
            values.append(round(float(weight), 4))
        return {"indices": indices, "values": values}

    def get_embedding(self, text: str) -> List[float]:
        """Retrieves 1024-dim dense vector from BGE-Large embedder at :8003."""
        safe_text = text[:750].strip()
        req = urllib.request.Request(
            f"{self.embedder_url}/embeddings",
            data=json.dumps({"input": safe_text, "model": "embedder"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedder error at {self.embedder_url}: {e}")
            return [0.0] * 1024

    def store_in_valkey_amem(self, card_id: str, atom_text: str, keywords: List[str], category: str = "knowledge"):
        """Stores sub-35 token atomic fact card directly in Valkey A-MEM at :6379."""
        try:
            from harness.data_fabric.valkey_amem import valkey_amem
            valkey_amem.store_atom(
                atom_id=card_id,
                atom_text=atom_text[:200],
                keywords=keywords[:10],
                category=category,
                confidence=1.0,
                is_core_memory=False
            )
        except Exception as ex:
            logger.debug(f"Valkey A-MEM direct store deferred: {ex}")

    def upsert_to_qdrant(self, rel_path: str, title: str, content: str, tags: List[str], metadata: Optional[Dict[str, Any]] = None):
        """Indexes note chunks into Qdrant 'obsidian_brain' collection."""
        chunks = self.chunk_text(content)
        points = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        for idx, ch in enumerate(chunks):
            dense_vec = self.get_embedding(ch)
            sparse_vec = self.compute_sparse_vector(ch)
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{rel_path}:{idx}"))
            points.append({
                "id": chunk_id,
                "vector": {
                    "dense": dense_vec,
                    "sparse": sparse_vec
                },
                "payload": {
                    "note_title": title,
                    "rel_path": rel_path,
                    "tags": tags,
                    "content": ch,
                    "chunk_idx": idx,
                    "total_chunks": len(chunks),
                    "timestamp": now_str,
                    "metadata": metadata or {}
                }
            })

        if points:
            req = urllib.request.Request(
                f"{self.qdrant_url}/collections/obsidian_brain/points",
                data=json.dumps({"points": points}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT"
            )
            try:
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    logger.info(f"Indexed {len(points)} chunks for '{title}' to Qdrant obsidian_brain.")
            except Exception as e:
                logger.warning(f"Failed indexing to Qdrant obsidian_brain: {e}")

    def save_note_to_couchdb(self, path: str, content: str, database: str = "ai_obsidian") -> Dict[str, Any]:
        """Saves note document into CouchDB database."""
        clean_path = path.strip().replace("\\", "/").lstrip("/")
        headers = self._get_couch_headers()

        # Check existing rev
        rev = None
        try:
            enc_id = urllib.parse.quote(clean_path, safe="")
            check_req = urllib.request.Request(f"{self.couch_url}/{urllib.parse.quote(database)}/{enc_id}", headers=headers)
            with urllib.request.urlopen(check_req, timeout=3.0) as resp:
                existing = json.loads(resp.read().decode("utf-8"))
                rev = existing.get("_rev")
        except Exception:
            pass

        now_ms = int(time.time() * 1000)
        doc = {
            "_id": clean_path,
            "path": clean_path,
            "type": "plain",
            "data": content,
            "mtime": now_ms,
            "ctime": now_ms,
            "size": len(content.encode("utf-8")),
            "children": []
        }
        if rev:
            doc["_rev"] = rev

        enc_id = urllib.parse.quote(clean_path, safe="")
        put_req = urllib.request.Request(
            f"{self.couch_url}/{urllib.parse.quote(database)}/{enc_id}",
            data=json.dumps(doc).encode("utf-8"),
            headers=headers,
            method="PUT"
        )
        try:
            with urllib.request.urlopen(put_req, timeout=5.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "path": clean_path, "rev": res.get("rev"), "database": database}
        except Exception as e:
            logger.warning(f"Failed CouchDB PUT to {database}/{clean_path}: {e}")
            return {"ok": False, "error": str(e), "path": clean_path}

    def save_note_to_disk(self, rel_path: str, content: str):
        """Mirrors note to local disk backup directory."""
        candidates = [
            os.path.join(VAULT_BACKUP_AI, rel_path),
            os.path.join(site.obsidian_vault(self.config), "AI Stack", rel_path) if site.obsidian_vault(self.config) else ""
        ]
        for dest in candidates:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass

    # ==========================================================================
    # 1. URL Ingestion
    # ==========================================================================
    def ingest_url(
        self,
        url: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        database: str = "ai_obsidian"
    ) -> Dict[str, Any]:
        """Fetches a URL, extracts readable markdown, saves to Obsidian & indexes to memory."""
        clean_url = (url or "").strip()
        if not clean_url.startswith(("http://", "https://")):
            return {"ok": False, "error": "Invalid URL. Must start with http:// or https://"}

        logger.info(f"Ingesting URL: {clean_url}")
        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 StoneSage/3.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                raw_html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return {"ok": False, "error": f"Failed fetching URL: {e}"}

        extractor = SimpleHTMLTextExtractor()
        try:
            extractor.feed(raw_html)
            text_body = extractor.get_text()
            extracted_title = extractor.title.strip()
        except Exception as e:
            text_body = re.sub(r"<[^>]+>", " ", raw_html)
            extracted_title = ""

        note_title = title or extracted_title or clean_url.split("//")[-1].split("/")[0]
        note_title = re.sub(r"[\r\n\t]+", " ", note_title).strip()
        slug = self._sanitize_slug(note_title)
        rel_path = f"Inbox/WebClips/{slug}.md"

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        all_tags = list(set(["webclip", "article", "research"] + [t.lstrip("#") for t in (tags or [])]))

        # Summarize main takeaways using fast worker (:8002)
        summary = ""
        try:
            prompt = (
                f"You are a concise research archivist. Provide a crisp 3-4 sentence technical summary "
                f"of the following web article:\n\n{text_body[:2500]}"
            )
            s_req = urllib.request.Request(
                f"{self.worker_url}/chat/completions",
                data=json.dumps({
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.3
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(s_req, timeout=10.0) as s_resp:
                s_data = json.loads(s_resp.read().decode("utf-8"))
                summary = s_data["choices"][0]["message"]["content"].strip()
        except Exception:
            summary = text_body[:300].strip() + "..."

        md_content = (
            f"---\n"
            f"title: \"{note_title}\"\n"
            f"source_url: \"{clean_url}\"\n"
            f"captured_at: \"{now_str}\"\n"
            f"tags: [{', '.join([chr(34) + str(t) + chr(34) for t in all_tags])}]\n"
            f"type: \"webclip\"\n"
            f"---\n\n"
            f"# {note_title}\n\n"
            f"> [!NOTE]\n"
            f"> **Source**: [{clean_url}]({clean_url})\n"
            f"> **Captured**: {now_str} | **Database**: `{database}`\n\n"
            f"## Summary\n"
            f"{summary}\n\n"
            f"## Full Article Text\n"
            f"{text_body[:10000]}\n"
        )

        couch_res = self.save_note_to_couchdb(rel_path, md_content, database=database)
        self.save_note_to_disk(rel_path, md_content)
        self.upsert_to_qdrant(rel_path, note_title, md_content, all_tags, {"url": clean_url, "type": "webclip"})
        
        amem_text = f"Webclip '{note_title}': {summary[:120]}"
        self.store_in_valkey_amem(
            card_id=f"clip_{slug[:20]}",
            atom_text=amem_text,
            keywords=[slug, "webclip", "article"] + all_tags[:4],
            category="webclips"
        )

        return {
            "ok": True,
            "title": note_title,
            "path": rel_path,
            "database": database,
            "summary": summary,
            "source_url": clean_url,
            "couchdb": couch_res
        }

    # ==========================================================================
    # 2. Photo Ingestion (Vision Stack on :8004)
    # ==========================================================================
    def ingest_photo(
        self,
        image_data: str,
        filename: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        prompt_hint: Optional[str] = None,
        database: str = "ai_obsidian"
    ) -> Dict[str, Any]:
        """
        Ingests a photo, runs the vision engine (cluster.vision_url) on it (resized to 640px),
        creates a rich Obsidian note, and indexes into Qdrant & Valkey.
        """
        try:
            if "," in image_data:
                b64_str = image_data.split(",", 1)[1]
            else:
                b64_str = image_data
            raw_bytes = base64.b64decode(b64_str)
        except Exception as e:
            return {"ok": False, "error": f"Invalid base64 image data: {e}"}

        timestamp = int(time.time())
        safe_fname = filename or f"photo_{timestamp}.jpg"
        safe_fname = re.sub(r"[^\w\.-]", "_", safe_fname)
        if not safe_fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            safe_fname += ".jpg"

        file_save_path = os.path.join(UPLOADS_CAPTURES_DIR, safe_fname)
        try:
            with open(file_save_path, "wb") as f:
                f.write(raw_bytes)
        except Exception as e:
            logger.warning(f"Could not save image to disk: {e}")

        # Pre-resize frame to 640px Lanczos (per Vision Server 640px invariant)
        resized_b64 = b64_str
        if HAS_PIL:
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                max_dim = 640
                w, h = img.size
                if w > max_dim or h > max_dim:
                    scale = min(max_dim / w, max_dim / h)
                    new_size = (int(w * scale), int(h * scale))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                resized_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception as e:
                logger.debug(f"Pillow resizing error: {e}")

        # Vision engine inspection (cluster.vision_url)
        vision_prompt = (
            "Analyze this image comprehensively for the homelab archive. Provide:\n"
            "1. Concise Title (3-6 words)\n"
            "2. Visual Scene Description (objects, environment, layout, condition)\n"
            "3. Any visible text / labels / OCR transcription\n"
            "4. Suggested Category & Tags"
        )
        if prompt_hint:
            vision_prompt += f"\nOperator Note / Focus Area: {prompt_hint}"

        vision_analysis = ""
        inferred_title = ""
        try:
            v_payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{resized_b64}"}}
                        ]
                    }
                ],
                "max_tokens": 400,
                "temperature": 0.4
            }
            v_req = urllib.request.Request(
                f"{self.vision_url}/chat/completions",
                data=json.dumps(v_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(v_req, timeout=20.0) as v_resp:
                v_data = json.loads(v_resp.read().decode("utf-8"))
                vision_analysis = v_data["choices"][0]["message"]["content"].strip()

            t_match = re.search(r"(?:1\.\s*(?:Concise\s*)?Title:?|Title:?)\s*([^\n\r]+)", vision_analysis, re.IGNORECASE)
            if t_match:
                inferred_title = t_match.group(1).strip().strip('"').strip("*")
        except Exception as e:
            logger.warning(f"Vision model inspection error at {self.vision_url}: {e}")
            vision_analysis = f"Photo captured at {time.strftime('%Y-%m-%d %H:%M:%S')}. (Vision model offline or timed out)."

        note_title = title or inferred_title or f"Photo Capture {timestamp}"
        slug = self._sanitize_slug(note_title)
        rel_path = f"Inbox/Captures/{slug}.md"

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        all_tags = list(set(["photo", "visual_memory", "capture"] + [t.lstrip("#") for t in (tags or [])]))

        md_content = (
            f"---\n"
            f"title: \"{note_title}\"\n"
            f"image_file: \"{safe_fname}\"\n"
            f"captured_at: \"{now_str}\"\n"
            f"tags: [{', '.join([chr(34) + str(t) + chr(34) for t in all_tags])}]\n"
            f"type: \"photo_capture\"\n"
            f"---\n\n"
            f"# {note_title}\n\n"
            f"> [!TIP]\n"
            f"> **Image Attachment**: `uploads/captures/{safe_fname}`\n"
            f"> **Captured**: {now_str} | **Database**: `{database}`\n\n"
            f"![[{safe_fname}]]\n\n"
            f"## Visual Analysis\n"
            f"{vision_analysis}\n"
        )

        couch_res = self.save_note_to_couchdb(rel_path, md_content, database=database)
        self.save_note_to_disk(rel_path, md_content)
        self.upsert_to_qdrant(rel_path, note_title, md_content, all_tags, {"image_file": safe_fname, "type": "photo"})
        
        amem_text = f"Photo '{note_title}': {vision_analysis[:120]}"
        self.store_in_valkey_amem(
            card_id=f"img_{slug[:20]}",
            atom_text=amem_text,
            keywords=[slug, "photo", "image"] + all_tags[:4],
            category="photos"
        )

        return {
            "ok": True,
            "title": note_title,
            "path": rel_path,
            "database": database,
            "image_file": safe_fname,
            "vision_analysis": vision_analysis,
            "couchdb": couch_res
        }

    # ==========================================================================
    # 3. Note & Snippet Ingestion
    # ==========================================================================
    def ingest_snippet(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        language: Optional[str] = None,
        database: str = "ai_obsidian"
    ) -> Dict[str, Any]:
        """Ingests a text note, memory card, or code snippet."""
        clean_content = (content or "").strip()
        if not clean_content:
            return {"ok": False, "error": "Content string cannot be empty"}

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        timestamp = int(time.time())

        if not title:
            first_line = clean_content.split("\n")[0].strip("# ")
            note_title = first_line[:50] if first_line else f"Note {timestamp}"
        else:
            note_title = title

        slug = self._sanitize_slug(note_title)
        rel_path = f"Inbox/Notes/{slug}.md"
        all_tags = list(set(["note", "snippet"] + [t.lstrip("#") for t in (tags or [])]))

        lang_fence = language or ""
        body = clean_content
        if lang_fence and not clean_content.startswith("```"):
            body = f"```{lang_fence}\n{clean_content}\n```"

        md_content = (
            f"---\n"
            f"title: \"{note_title}\"\n"
            f"captured_at: \"{now_str}\"\n"
            f"tags: [{', '.join([chr(34) + str(t) + chr(34) for t in all_tags])}]\n"
            f"type: \"snippet\"\n"
            f"---\n\n"
            f"# {note_title}\n\n"
            f"> **Captured**: {now_str} | **Database**: `{database}`\n\n"
            f"{body}\n"
        )

        couch_res = self.save_note_to_couchdb(rel_path, md_content, database=database)
        self.save_note_to_disk(rel_path, md_content)
        self.upsert_to_qdrant(rel_path, note_title, md_content, all_tags, {"type": "snippet"})
        
        amem_text = f"Note '{note_title}': {clean_content[:120]}"
        self.store_in_valkey_amem(
            card_id=f"not_{slug[:20]}",
            atom_text=amem_text,
            keywords=[slug, "note"] + all_tags[:4],
            category="notes"
        )

        return {
            "ok": True,
            "title": note_title,
            "path": rel_path,
            "database": database,
            "couchdb": couch_res
        }


# Global singleton
obsidian_ingest = ObsidianIngestEngine()
