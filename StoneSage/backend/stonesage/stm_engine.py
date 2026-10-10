"""
Short-Term Memory (STM) RAM Engine for StoneSage
Maintains an ultra-fast in-memory working context buffer (RAM cache)
and leverages the local worker (80+ tok/s) on Secondary Accelerator (:8002) for rapid
context compression, active scratchpad management, and low-context prompt injection.
"""

import time
import json
import urllib.request
from typing import Dict, Any, List, Optional

from lodestone import endpoints

class ShortTermMemoryEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.worker_url = endpoints.url("worker", config).removesuffix("/v1")
        # In-RAM storage
        self.working_items: List[Dict[str, Any]] = []
        self.active_plan: Optional[Dict[str, Any]] = None
        self.pinned_context: str = ""
        self.max_items = 20
        self.last_updated = time.time()
        
        # Seed initial system context
        self.add_item("system", "Topology", "Engines and GPUs: see /api/system/profile (live); Proxmox nodes: see /api/proxmox/nodes")

    def add_item(self, category: str, key: str, value: str) -> Dict[str, Any]:
        """Add or update an item in short-term RAM memory."""
        # Check if key already exists
        for item in self.working_items:
            if item["key"].lower() == key.lower():
                item["value"] = value
                item["category"] = category
                item["updated_at"] = time.time()
                self.last_updated = time.time()
                return item

        item = {
            "id": f"stm_{int(time.time()*1000)}_{len(self.working_items)}",
            "category": category,
            "key": key,
            "value": value,
            "created_at": time.time(),
            "updated_at": time.time()
        }
        self.working_items.insert(0, item)
        if len(self.working_items) > self.max_items:
            self.working_items = self.working_items[:self.max_items]
        
        self.last_updated = time.time()
        return item

    def remove_item(self, item_id: str) -> bool:
        initial_len = len(self.working_items)
        self.working_items = [i for i in self.working_items if i.get("id") != item_id and i.get("key") != item_id]
        return len(self.working_items) < initial_len

    def clear(self):
        self.working_items = []
        self.pinned_context = ""
        self.active_plan = None
        self.last_updated = time.time()

    def set_active_plan(self, plan: Dict[str, Any]):
        self.active_plan = plan
        self.last_updated = time.time()

    def get_active_plan(self) -> Optional[Dict[str, Any]]:
        return self.active_plan

    def estimate_tokens(self) -> int:
        """Rough token estimate (~4 chars per token)."""
        total_chars = len(self.pinned_context)
        for i in self.working_items:
            total_chars += len(i.get("key", "")) + len(i.get("value", ""))
        return round(total_chars / 4)

    def get_summary(self) -> Dict[str, Any]:
        """Return full STM snapshot for UI display."""
        return {
            "ok": True,
            "item_count": len(self.working_items),
            "estimated_tokens": self.estimate_tokens(),
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_updated)),
            "items": self.working_items,
            "pinned_context": self.pinned_context,
            "has_active_plan": self.active_plan is not None,
            "active_plan": self.active_plan
        }

    def format_for_prompt(self, max_tokens: int = 150) -> str:
        """
        Format active RAM context as an ultra-compact atomic header (< 150 tokens total)
        to prevent context oversaturation and eliminate model lag.
        """
        if not self.working_items and not self.pinned_context and not self.active_plan:
            return ""

        lines = ["[WORKING MEMORY (A-MEM / RAM)]:"]
        if self.pinned_context:
            pinned = self.pinned_context.strip()
            if len(pinned) > 140:
                pinned = pinned[:140] + "..."
            lines.append(f"• Pinned: {pinned}")
        
        for item in self.working_items[:5]:
            val = str(item.get('value', '')).strip()
            # Bounded to ~35 tokens (approx 140 chars)
            if len(val) > 140:
                val = val[:140] + "..."
            lines.append(f"• [{item['category'].upper()}] {item['key']}: {val}")

        if self.active_plan:
            title = self.active_plan.get("title", "Active Task")
            steps = self.active_plan.get("steps", [])
            current_step = next((s for s in steps if s.get("status") == "running"), None)
            step_desc = current_step.get("desc", "Executing") if current_step else "Ready"
            lines.append(f"• [PLAN]: {title} -> {step_desc}")

        out = "\n".join(lines)
        if len(out) > max_tokens * 4:
            out = out[:max_tokens * 4] + "..."
        return out

    def compress_with_worker(self, raw_text: str) -> str:
        """
        Call the worker engine to compress raw text
        into a concise 1-2 sentence short-term memory entry.
        """
        prompt = (
            "Summarize the following notes/context into 1 or 2 dense, factual bullet points "
            "for immediate short-term memory retention. Output ONLY the summary bullets:\n\n"
            f"{raw_text[:1500]}"
        )
        base_url = self.worker_url.rstrip("/").removesuffix("/v1")
        url = f"{base_url}/v1/chat/completions"
        payload = json.dumps({
            "model": "worker",
            "messages": [
                {"role": "system", "content": "You are a fast, dense memory compression engine."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 120,
            "temperature": 0.2,
            "stream": False
        }).encode("utf-8")
        
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    compressed = choices[0].get("message", {}).get("content", "").strip()
                    self.add_item("compressed", f"Brief_{int(time.time())}", compressed)
                    return compressed
        except Exception as e:
            fallback = raw_text[:140].replace("\n", " ").strip()
            self.add_item("raw", f"Clip_{int(time.time())}", fallback)
            return fallback
        return ""
