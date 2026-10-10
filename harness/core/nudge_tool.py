"""
Harness integration for the standalone Agent Nudge subsystem.
Connects local agent sessions to NudgeEngine and WatchdogDetector.
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

# Add standalone agent-nudge package path if not already installed in site-packages
nudge_pkg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent-nudge"))
if os.path.exists(nudge_pkg_path) and nudge_pkg_path not in sys.path:
    sys.path.insert(0, nudge_pkg_path)

try:
    from agent_nudge import NudgeEngine, WatchdogDetector
    HAS_NUDGE = True
except ImportError:
    NudgeEngine = None
    WatchdogDetector = None
    HAS_NUDGE = False

logger = logging.getLogger("Harness.NudgeTool")

class HarnessNudgeBridge:
    def __init__(self):
        self.engine = NudgeEngine() if HAS_NUDGE else None
        self.watchdog = WatchdogDetector(self.engine) if self.engine else None

    def nudge(self, agent_id: str, directive: Optional[str] = None) -> Dict[str, Any]:
        """Triggers an out-of-band non-destructive intervention on the target agent."""
        if not self.engine:
            return {"ok": False, "error": "AgentNudge package not loaded"}
        res = self.engine.nudge(agent_id=agent_id, directive=directive)
        return res.to_dict()

    def record_tool_call(self, agent_id: str, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """Returns True if a circular loop was detected and interrupted by a nudge."""
        if self.watchdog:
            return self.watchdog.record_tool_call(agent_id, tool_name, arguments)
        return False

harness_nudge = HarnessNudgeBridge()
