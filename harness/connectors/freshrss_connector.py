"""
FreshRSS Connector (LXC 108 :80).
Allows background agents to pull unread tech/AI preprints and articles for ambient intelligence.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.FreshRSS")

class FreshRSSConnector:
    def __init__(self, base_url: str = fleet_config.freshrss_url):
        self.base_url = base_url.rstrip("/")

    def get_unread_articles(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Queries Google Reader API / Fever API endpoint on FreshRSS for unread items."""
        endpoint = f"{self.base_url}/api/greader.php/reader/api/0/stream/contents/reading-list?n={limit}&xt=user/-/state/com.google/read"
        req = urllib.request.Request(endpoint, headers={"User-Agent": "Harness-RSS"})
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                results = []
                for it in items[:limit]:
                    results.append({
                        "id": it.get("id"),
                        "title": it.get("title"),
                        "author": it.get("author"),
                        "published": it.get("published"),
                        "summary": it.get("summary", {}).get("content", "")[:300],
                        "canonical_url": it.get("canonical", [{}])[0].get("href", ""),
                    })
                return results
        except Exception as e:
            logger.warning(f"FreshRSS read failed on {endpoint}: {e}")
            return []

freshrss_connector = FreshRSSConnector()
