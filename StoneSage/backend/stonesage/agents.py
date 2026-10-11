"""
Agents on the platform (Roadmap Phase 11 step 5, 2026-10-11): an agent is a soul + a tool allowlist + a brain.

    soul    data/souls/<id>/ (soul.py): persona.md, memories.json, manifest.json
    tools   the names it may use, out of the loop's tool set (courage/tools.py). Never offered to the model otherwise,
            and refused as unknown if it asks anyway.
    brain   the endpoint role that answers for it (lodestone.endpoints): "coordinator" (default) or "worker"

All agents run through the same loop (courage/agent.py CourageAgent). Two are built in:

    Computer (courage-computer)  the house agent. Its tools, persona, presence card and reflexes come from the Home app
                                 (hearth), through the server's get_courage_agent(): this module only names it.
    Atlas (atlas)                a mentoring senior engineer. No house: no presence card, no home tools. Its one tool,
                                 memory_search, searches Atlas's own memories.json.

A soul on disk with no built-in agent behind it (an imported one, `python -m stonesage.soul import`) is an agent too: its
persona is its persona.md, its brain and tools come from its manifest, and it can only ever have the platform's safe tools
(SAFE_TOOLS). A manifest's `tools` can only narrow what the platform grants; it can never add a tool.

Nothing here imports the Home app: a house agent is wired by the server through the `computer` callables of AgentHub.
"""

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .soul import Soul, list_souls

BRAINS = ("coordinator", "worker")             # endpoint roles that can chat
DEFAULT_BRAIN = "coordinator"
SAFE_TOOLS = ("memory_search",)                # all an agent with no built-in spec ever gets: reads only its own memory
COMPUTER_ID = "courage-computer"

ATLAS_PERSONA = (
    "You are Atlas, a calm senior engineer who manages a coding department. Your mission is the long-term health of the "
    "codebase: keep it simple and readable, protect the data in its databases, and teach junior developers by explaining "
    "the reasoning behind each choice. Read before you judge. Give a short answer first, then the detail that matters: the "
    "tradeoff, the risk, the test that would prove it. When a request is ambiguous, ask one clear question. Prefer small, "
    "reversible changes, say what you are unsure of, and say when something needs measuring before anyone decides. "
    "Use memory_search to recall what the people you work with told you earlier. Be warm, direct and patient, and praise "
    "good work specifically."
)

ATLAS_EXTRACT = (
    "You keep a senior engineer's long-term memory. From the exchange below, write down only what is worth remembering "
    "for weeks about the person talking to Atlas and the work they do: their projects, conventions and decisions, "
    "preferences, deadlines, what they are learning, people they mention, things they asked Atlas to remember. "
    "Write only what the user said; Atlas's reply is context, never a source. Skip small talk and one-off questions. "
    "Write each note to the person, in the second person (\"Your team deploys on Fridays...\").\n"
    'Reply with JSON only: {"notes": ["one short sentence", ...]} with at most 2 notes, or {"notes": []}. '
    "Most exchanges have nothing worth keeping.\n\nThe user said: {user}\nAtlas replied: {reply}"
)


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    persona: str = ""                       # seeded into the soul (follows code until edited); "" = the soul's own / the app's
    tools: Optional[Tuple[str, ...]] = None  # the most this agent may use; None = every tool the app wired (Computer)
    brain: str = DEFAULT_BRAIN
    aliases: Tuple[str, ...] = ()
    house: bool = False                      # a house agent: presence card, house tools; its pieces come from the Home app
    description: str = ""
    extract: str = ""                        # memory extraction prompt; "" = courage/memory.py's own

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "description": self.description, "brain": self.brain,
                "tools": list(self.tools) if self.tools is not None else "all", "house": self.house}


COMPUTER = AgentSpec(COMPUTER_ID, "Computer", tools=None, house=True, aliases=("computer", "courage"),
                     description="The house computer: Home Assistant, cameras, presence, voice. Needs the Home app.")
ATLAS = AgentSpec("atlas", "Atlas", persona=ATLAS_PERSONA, tools=("memory_search",), extract=ATLAS_EXTRACT,
                  description="A senior engineer who mentors: code review, design, long-term codebase health. No house tools.")
BUILTIN: Tuple[AgentSpec, ...] = (COMPUTER, ATLAS)


def _legacy(agent_id: str) -> str:
    """An old agent id -> the current one; the lookup lives with the other renamed things (harness/legacy_names.py)."""
    try:
        from harness.legacy_names import canonical_agent_id
        return canonical_agent_id(agent_id)
    except ImportError:
        return (agent_id or "").strip().lower()


def effective_tools(spec: AgentSpec, manifest: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, ...]]:
    """spec.tools, narrowed (never widened) by the soul manifest's `tools` list. None = everything the app wired."""
    mt = (manifest or {}).get("tools")
    if not isinstance(mt, list):
        return spec.tools
    names = tuple(dict.fromkeys(t for t in mt if isinstance(t, str)))
    return names if spec.tools is None else tuple(t for t in names if t in spec.tools)


def effective_brain(spec: AgentSpec, manifest: Optional[Dict[str, Any]] = None) -> str:
    """The manifest's `brain` when it names a chat role this install has; otherwise the spec's."""
    b = (manifest or {}).get("brain")
    return b if isinstance(b, str) and b in BRAINS else spec.brain


def spec_from_manifest(manifest: Dict[str, Any]) -> AgentSpec:
    """The agent a soul with no built-in spec describes: its own persona.md, the safe tools, its manifest's brain."""
    agent_id = str(manifest.get("id", ""))
    spec = AgentSpec(agent_id, str(manifest.get("name") or agent_id)[:60], tools=SAFE_TOOLS,
                     description="An imported soul.")
    return replace(spec, tools=effective_tools(spec, manifest), brain=effective_brain(spec, manifest))


@dataclass
class AgentServices:
    """What the server lends to build an agent without a house (the loop itself is platform code)."""
    llm_url: Callable[[str], str]                                   # endpoint role -> chat URL
    timeout: Callable[[], float]                                    # seconds of silence the model may keep
    memory: Callable[[str, str, str], Any]                          # (memories path, chat URL, extract prompt) -> ConversationMemory
    lease_brain: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None   # engine lease: who answers for a role now
    post: Optional[Callable[..., Any]] = None                       # tests: a fake model call
    stream_post: Optional[Callable[..., Any]] = None                # tests: a fake streaming model call


def _no_home(*_a, **_k) -> Dict[str, Any]:
    return {"ok": False, "error": "this agent has no home"}


def build_agent(spec: AgentSpec, soul: Soul, services: AgentServices, manifest: Optional[Dict[str, Any]] = None):
    """A loop for an agent without a house: persona from its soul, memory in its soul, only its allowed tools, and no
    presence card or house reminder in the prompt."""
    from .courage import CourageAgent, CourageDeps, CourageTools
    manifest = manifest if manifest is not None else soul.manifest
    allowed = effective_tools(spec, manifest)
    brain = effective_brain(spec, manifest)
    url = services.llm_url(brain)
    holder: List[Any] = []

    def memory_search(query: str) -> List[Dict[str, Any]]:
        mem = holder[0].memory if holder else None
        if not mem:
            return []
        return [{"text": f"{time.strftime('%b %d', time.localtime(n['at']))}, from a conversation: {n['text']}"}
                for n in mem.recall(query)]

    deps = CourageDeps(ha_states=_no_home, ha_call=_no_home, presence=lambda: {}, camera_look=_no_home,
                       camera_scan=_no_home, memory_search=memory_search)
    kw = {k: v for k, v in (("post", services.post), ("stream_post", services.stream_post)) if v}
    agent = CourageAgent(CourageTools(deps, allowed=allowed if allowed is not None else SAFE_TOOLS), url,
                         timeout=services.timeout(), model=brain, **kw)
    holder.append(agent)
    agent.home_context = False
    agent.persona_fn = soul.persona
    agent.memory = services.memory(soul.path("memories.json"), url, spec.extract)
    if services.lease_brain:
        agent.brain = lambda: services.lease_brain(brain)
    return agent


class AgentHub:
    """The registered agents and their running loops. Computer's loop is the server's own (it carries phone approvals and
    the escalation ladder, and there must be exactly one); every other agent is built here on first use."""

    def __init__(self, data_dir: Callable[[], str], services: AgentServices,
                 computer: Optional[Callable[[], Any]] = None, computer_built: Optional[Callable[[], Any]] = None):
        """computer(): the shared Computer loop, or None/raises when no app provides a house. computer_built(): that loop
        if it already exists (never builds it)."""
        self.data_dir = data_dir
        self.services = services
        self._computer, self._computer_built = computer, computer_built
        self._agents: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---- who exists ---------------------------------------------------------------------------
    def specs(self) -> List[AgentSpec]:
        out = [s for s in BUILTIN if not s.house or self._computer is not None]
        have = {s.id for s in BUILTIN}
        out += [spec_from_manifest(m) for m in list_souls(self.data_dir()) if m["id"] not in have]
        return out

    def resolve(self, name: Any) -> Optional[AgentSpec]:
        """An agent by id, by an alias ("computer"), or by an old id (harness/legacy_names.py); None if there is no such agent."""
        if not isinstance(name, str) or not name.strip():
            return None
        key = _legacy(name)
        for s in self.specs():
            if key == s.id or key in s.aliases:
                return s
        return None

    # ---- running them ---------------------------------------------------------------------------
    def agent(self, spec: AgentSpec):
        if spec.house:
            if self._computer is None:
                raise RuntimeError("no app provides Computer's home")
            return self._computer()
        with self._lock:
            if spec.id not in self._agents:
                soul = Soul.open(self.data_dir(), spec.id, display_name=spec.name, default_persona=spec.persona)
                self._agents[spec.id] = build_agent(spec, soul, self.services)
            return self._agents[spec.id]

    def is_running(self, agent_id: str) -> bool:
        if agent_id == COMPUTER_ID:
            return bool(self._computer_built and self._computer_built())
        with self._lock:
            return agent_id in self._agents

    def reload(self, agent_id: str) -> None:
        """A soul was replaced on disk: a loop built from the old one is dropped (rebuilt on next use); Computer's loop
        stays (it holds approvals) and re-reads its memory notes. Its persona is read from the file every turn."""
        if agent_id == COMPUTER_ID:
            agent = self._computer_built() if self._computer_built else None
            mem = getattr(agent, "memory", None)
            if mem is not None:
                mem.reload()
            return
        with self._lock:
            self._agents.pop(agent_id, None)

    def chat_agent(self, agent_id: Any):
        """The loop for /api/cluster/chat when it names one of the soul agents that are not house agents (Atlas, or an
        old id of it); None leaves the request to the older paths."""
        spec = self.resolve(agent_id)
        return None if spec is None or spec.house else self.agent(spec)
