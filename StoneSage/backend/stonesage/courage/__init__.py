"""
Courage: the house computer's tool-calling agent (Phase 2).

    prompt.py  persona + live presence card (small, stable system prompt)
    tools.py   the curated tool set, JSON schemas and approval rules
    agent.py   the OpenAI-style tool loop against the coordinator (:8001)

Dependencies (Home Assistant, cameras, presence, memory, voice) are injected through
`CourageDeps`, so the loop runs offline in tests with fakes.
"""

from .agent import CourageAgent, PendingActions
from .tools import CourageDeps, CourageTools

__all__ = ["CourageAgent", "CourageDeps", "CourageTools", "PendingActions"]
