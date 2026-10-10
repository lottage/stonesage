"""
CouchDB Client for StoneSage & Obsidian LiveSync
Interacts with the CouchDB instance (config couchdb.url; lodestone/endpoints.py)
managing the 'obsidiannotes' database.
"""

import os
from lodestone import endpoints
import time
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional

import logging

logger = logging.getLogger("StoneSage.couchdb")   # was used but never defined: its own error path crashed

class CouchDBClient:
    def __init__(self, config: Dict[str, Any]):
        self.url = (config.get("url") or endpoints.url("couchdb")).rstrip("/")
        self.username = config.get("username", "")
        self.password = config.get("password", os.environ.get("COUCHDB_PASSWORD", ""))
        self.database = config.get("database", "obsidiannotes")

    def _get_headers(self) -> Dict[str, str]:
        auth_bytes = f"{self.username}:{self.password}".encode("utf-8")
        auth_b64 = base64.b64encode(auth_bytes).decode("ascii")
        return {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def list_databases(self) -> List[Dict[str, Any]]:
        """List all non-system databases with stats and friendly descriptions."""
        headers = self._get_headers()
        try:
            req = urllib.request.Request(f"{self.url}/_all_dbs", headers=headers)
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                dbs = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to fetch databases from CouchDB: {e}")
            return [{"id": self.database, "name": self.database, "doc_count": 0}]

        results = []
        for db_name in dbs:
            if db_name.startswith("_"):
                continue
            meta = {
                "id": db_name,
                "name": "AI Stack Brain" if "ai" in db_name.lower() else ("Personal Vault" if "notes" in db_name.lower() else db_name),
                "doc_count": 0,
                "disk_size_mb": 0.0,
                "is_default": (db_name == self.database)
            }
            try:
                db_req = urllib.request.Request(f"{self.url}/{urllib.parse.quote(db_name)}", headers=headers)
                with urllib.request.urlopen(db_req, timeout=3.0) as db_resp:
                    db_data = json.loads(db_resp.read().decode("utf-8"))
                    meta["doc_count"] = db_data.get("doc_count", 0)
                    meta["disk_size_mb"] = round(db_data.get("sizes", {}).get("file", 0) / (1024 * 1024), 2)
            except Exception:
                pass
            results.append(meta)
        return results

    def get_status(self, database: Optional[str] = None) -> Dict[str, Any]:
        """Check CouchDB instance and database health."""
        start = time.perf_counter()
        headers = self._get_headers()
        target_db = database or self.database
        try:
            # 1. Probe root server
            root_req = urllib.request.Request(f"{self.url}/", headers=headers)
            with urllib.request.urlopen(root_req, timeout=3.0) as resp:
                root_data = json.loads(resp.read().decode("utf-8"))
            latency = round((time.perf_counter() - start) * 1000, 1)

            # 2. Probe database stats
            db_req = urllib.request.Request(f"{self.url}/{urllib.parse.quote(target_db)}", headers=headers)
            with urllib.request.urlopen(db_req, timeout=3.0) as resp:
                db_data = json.loads(resp.read().decode("utf-8"))

            return {
                "online": True,
                "latency_ms": latency,
                "version": root_data.get("version", "unknown"),
                "vendor": root_data.get("vendor", {}).get("name", "Apache CouchDB"),
                "database": target_db,
                "doc_count": db_data.get("doc_count", 0),
                "doc_del_count": db_data.get("doc_del_count", 0),
                "disk_size_mb": round(db_data.get("sizes", {}).get("file", 0) / (1024 * 1024), 2),
                "active_size_mb": round(db_data.get("sizes", {}).get("active", 0) / (1024 * 1024), 2),
                "update_seq": str(db_data.get("update_seq", ""))[:16]
            }
        except Exception as e:
            return {
                "online": False,
                "url": self.url,
                "database": target_db,
                "error": str(e)
            }

    def list_notes(self, database: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all note documents from the specified database.
        Excludes chunk leaves ('h:...') and internal metadata docs.
        """
        target_db = database or self.database
        url = f"{self.url}/{urllib.parse.quote(target_db)}/_all_docs?include_docs=true"
        req = urllib.request.Request(url, headers=self._get_headers())
        notes = []
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for row in data.get("rows", []):
                doc_id = row.get("id", "")
                # Skip chunks, designs, system or internal files
                if doc_id.startswith(("h:", "_design", "ix:", "t:", "$", "_local")) or "livesync_version" in doc_id.lower():
                    continue

                doc = row.get("doc", {})
                doc_type = doc.get("type", "")
                if doc_type == "leaf":
                    continue

                clean_path = doc.get("path") or doc_id
                # Skip non-markdown binary or internal index state files
                if clean_path.endswith((".bin", ".json", ".patch", ".zip")):
                    continue

                name = os.path.basename(clean_path)
                size = int(doc.get("size", 0)) if doc.get("size") else 0
                
                mtime_raw = doc.get("mtime")
                if mtime_raw:
                    try:
                        mtime_sec = float(mtime_raw) / 1000.0 if float(mtime_raw) > 1e11 else float(mtime_raw)
                        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_sec))
                    except Exception:
                        modified = str(mtime_raw)
                else:
                    modified = "Unknown"

                notes.append({
                    "id": doc_id,
                    "name": name,
                    "path": clean_path.replace("\\", "/"),
                    "size_bytes": size,
                    "modified": modified,
                    "source": "couchdb",
                    "database": target_db,
                    "type": doc_type or "plain",
                    "rev": doc.get("_rev", "")
                })

            return sorted(notes, key=lambda x: x["path"].lower())
        except Exception as e:
            print(f"[CouchDBClient] Error listing notes in {target_db}: {e}")
            return []

    def get_note_doc(self, note_id_or_path: str, database: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch raw note document from CouchDB with case fallback."""
        target_db = database or self.database
        headers = self._get_headers()
        candidates = [note_id_or_path, note_id_or_path.lower()]
        for cand in candidates:
            encoded_id = urllib.parse.quote(cand, safe="")
            url = f"{self.url}/{urllib.parse.quote(target_db)}/{encoded_id}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue
        return None

    def save_note_doc(
        self,
        path: str,
        content: str,
        database: Optional[str] = None,
        rev: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates or updates a note document in CouchDB.
        Compatible with Obsidian LiveSync schema.
        """
        target_db = database or self.database
        headers = self._get_headers()
        clean_path = path.strip().replace("\\", "/").lstrip("/")

        # If rev not provided, check if doc exists to retrieve current rev
        current_rev = rev
        if not current_rev:
            existing = self.get_note_doc(clean_path, database=target_db)
            if existing and "_rev" in existing:
                current_rev = existing["_rev"]

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
        if current_rev:
            doc["_rev"] = current_rev

        encoded_id = urllib.parse.quote(clean_path, safe="")
        url = f"{self.url}/{urllib.parse.quote(target_db)}/{encoded_id}"
        req = urllib.request.Request(
            url,
            data=json.dumps(doc).encode("utf-8"),
            headers=headers,
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return {
                    "ok": True,
                    "id": clean_path,
                    "rev": res.get("rev"),
                    "database": target_db
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            return {"ok": False, "error": f"HTTP {e.code}: {err_body}", "database": target_db}
        except Exception as e:
            return {"ok": False, "error": str(e), "database": target_db}

    def delete_note_doc(self, path: str, database: Optional[str] = None) -> Dict[str, Any]:
        """Deletes a note document from CouchDB."""
        target_db = database or self.database
        existing = self.get_note_doc(path, database=target_db)
        if not existing or "_rev" not in existing:
            return {"ok": False, "error": "Document not found or missing rev"}

        headers = self._get_headers()
        encoded_id = urllib.parse.quote(path, safe="")
        url = f"{self.url}/{urllib.parse.quote(target_db)}/{encoded_id}?rev={existing['_rev']}"
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "id": path, "deleted": res.get("ok", True)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
