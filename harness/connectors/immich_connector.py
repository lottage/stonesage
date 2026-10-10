"""
Immich Photo & Vision Grounding Connector (LXC 107 :9000).
Enables agents to query photo EXIF metadata, smart search tags, faces, and timestamps.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.Immich")

class ImmichConnector:
    def __init__(self, base_url: str = fleet_config.immich_url, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""

    def search_photos(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Smart-searches Immich photo library using visual semantic tags and descriptions."""
        url = f"{self.base_url}/api/search/smart"
        payload = {"query": query, "size": limit}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "User-Agent": "Harness-Immich"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("assets", {}).get("items", [])
                results = []
                for item in items[:limit]:
                    results.append({
                        "id": item.get("id"),
                        "originalFileName": item.get("originalFileName"),
                        "createdAt": item.get("fileCreatedAt"),
                        "tags": item.get("tags", []),
                        "exif": {
                            "city": item.get("exifInfo", {}).get("city"),
                            "dateTimeOriginal": item.get("exifInfo", {}).get("dateTimeOriginal"),
                            "model": item.get("exifInfo", {}).get("model"),
                        }
                    })
                return results
        except Exception as e:
            logger.warning(f"Immich query failed on {url}: {e}")
            return []

immich_connector = ImmichConnector()
