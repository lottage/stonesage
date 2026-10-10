"""
Kavita Connector (LXC 100 :5000).
Enables querying technical books, programming documentation, and PDFs.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.Kavita")

class KavitaConnector:
    def __init__(self, base_url: str = fleet_config.kavita_url, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""

    def search_books(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches technical books and manga titles in Kavita."""
        url = f"{self.base_url}/api/Search/search?queryString={urllib.request.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "Harness-Kavita"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                books = data.get("series", [])
                return [
                    {
                        "id": b.get("id"),
                        "name": b.get("name"),
                        "format": b.get("format"),
                        "pages": b.get("pages"),
                        "summary": b.get("summary", "")[:250],
                    }
                    for b in books[:limit]
                ]
        except Exception as e:
            logger.warning(f"Kavita search error on {url}: {e}")
            return []

kavita_connector = KavitaConnector()
