"""
Tier 3: Long-Term Semantic Vector Memory (Qdrant Vector Brain on LXC 117 :6333).
Uses the embedder engine (config cluster.embedder_url; BGE inputs < 950 chars).
Enforces semantic novelty gatekeeping (< 0.85 cosine similarity).
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from ..config import fleet_config

logger = logging.getLogger("Harness.QdrantBrain")

class QdrantBrain:
    def __init__(
        self,
        qdrant_url: str = fleet_config.qdrant_url,
        embedder_url: str = fleet_config.embedder_url
    ):
        self.qdrant_url = qdrant_url.rstrip("/")
        self.embedder_url = embedder_url.rstrip("/")

    def get_embedding(self, text: str) -> List[float]:
        """Generates 1024-d dense embedding via BGE-Large on :8003. Strictly slices < 950 chars."""
        safe_text = text[:900] if text else "homelab harness"
        url = f"{self.embedder_url}/embeddings"
        payload = {"input": safe_text, "model": "embedder"}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Harness-Qdrant"}
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedding generation error on {url}: {e}")
            return [0.0] * 1024

    def search_memory(
        self,
        collection_name: str,
        query: str,
        limit: int = 3,
        score_threshold: float = 0.60
    ) -> List[Dict[str, Any]]:
        """Searches Qdrant collection using BGE embedding."""
        vector = self.get_embedding(query)
        url = f"{self.qdrant_url}/collections/{collection_name}/points/search"
        payload = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
            "score_threshold": score_threshold
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("result", [])
        except Exception as e:
            logger.warning(f"Qdrant search error on collection '{collection_name}': {e}")
            return []

    def upsert_point(self, collection_name: str, point_id: str, vector: List[float], payload: Dict[str, Any]) -> bool:
        """Upserts a point into Qdrant collection."""
        url = f"{self.qdrant_url}/collections/{collection_name}/points"
        data = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": payload
                }
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning(f"Failed to upsert point {point_id} in {collection_name}: {e}")
            return False

    def scroll_points(self, collection_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Scrolls points from a Qdrant collection."""
        url = f"{self.qdrant_url}/collections/{collection_name}/points/scroll"
        data = {"limit": limit, "with_payload": True, "with_vector": False}
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                return d.get("result", {}).get("points", [])
        except Exception as e:
            logger.warning(f"Failed to scroll points from {collection_name}: {e}")
            return []

    def check_novelty(self, candidate_text: str, collection_name: str = "autonomous_thinking") -> bool:
        """
        Enforces semantic novelty threshold (< 0.85 cosine similarity against past explorations).
        Returns True if genuinely novel, False if too redundant.
        """
        results = self.search_memory(collection_name=collection_name, query=candidate_text, limit=1)
        if not results:
            return True
        top_score = results[0].get("score", 0.0)
        is_novel = top_score < 0.85
        if not is_novel:
            logger.info(f"[Novelty Gate] Candidate too redundant (cosine similarity: {top_score:.3f} >= 0.85).")
        return is_novel

qdrant_brain = QdrantBrain()
