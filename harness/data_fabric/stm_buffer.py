"""
Tier 1: Short-Term Session Working Memory (STM RAM Buffer).
Maintains active scratchpad items, in-progress execution plans, and rolling summaries.
Enforces compact header formatting (< 300 tokens) to preserve model context.
"""

import time
from typing import Dict, Any, List, Optional

class STMBuffer:
    def __init__(self, max_items: int = 15):
        self.max_items = max_items
        self.working_items: List[Dict[str, Any]] = []
        self.active_plan: Optional[Dict[str, Any]] = None
        self.pinned_context: str = ""
        self.last_updated: float = time.time()

    def add_item(self, category: str, key: str, value: str) -> Dict[str, Any]:
        """Add or update an item in short-term RAM memory."""
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
            "updated_at": time.time(),
        }
        self.working_items.insert(0, item)
        if len(self.working_items) > self.max_items:
            self.working_items = self.working_items[:self.max_items]
        self.last_updated = time.time()
        return item

    def set_pinned_context(self, context_str: str):
        self.pinned_context = context_str.strip()
        self.last_updated = time.time()

    def set_active_plan(self, plan: Dict[str, Any]):
        self.active_plan = plan
        self.last_updated = time.time()

    def clear(self):
        self.working_items = []
        self.pinned_context = ""
        self.active_plan = None
        self.last_updated = time.time()

    def format_header(self, max_tokens: int = 300) -> str:
        """Formats active RAM context into a compact prompt header."""
        if not self.working_items and not self.pinned_context and not self.active_plan:
            return ""

        lines = ["[SHORT-TERM WORKING MEMORY]:"]
        if self.pinned_context:
            lines.append(f"• Pinned: {self.pinned_context}")

        if self.active_plan:
            goal = self.active_plan.get("goal", "Active Mission")
            step = self.active_plan.get("current_step", 1)
            lines.append(f"• Active Plan: {goal} (Step {step})")

        for item in self.working_items[:5]:
            lines.append(f"• {item['key']}: {item['value']}")

        return "\n".join(lines)

stm_buffer = STMBuffer()
