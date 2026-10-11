"""
The 4 Pillar Founder Personas & Weighted Collective Governance.
Defines the 4 immortal pillars of the homelab AI society:
- Atlas (Software Architecture & Systems Code)
- FaunaSentinel (Perimeter Vigilance & Physical Security)
- Mnemosyne (Memory, Truth & Obsidian Vault Curation)
- Hearth (Energy, Presence & Smart Home Orchestration)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from ..legacy_names import LEGACY_AGENT_IDS, canonical_agent_id


@dataclass
class Founder:
    id: str
    name: str
    title: str
    domain: str
    specialized_domains: List[str]
    weight: float = 1.5
    mission: str = ""
    invariants: List[str] = field(default_factory=list)


FOUNDERS: Dict[str, Founder] = {
    "aule": Founder(
        id="aule",
        name="Aulë",
        title="Lead Systems Architect & Algorithms Engineer",
        domain="software",
        specialized_domains=["software", "code", "architecture", "refactor", "tests", "databases", "concurrency", "atlas", "forge"],
        weight=1.5,
        mission="Ensure the long-term integrity, mathematical rigor, and architectural supremacy of homelab codebases.",
        invariants=[
            "Never commit unverified code or broken tests.",
            "Preserve database state and schema migrations without data loss.",
            "Minimize token overhead through lean personas and atomic memory.",
            "Enforce strict algorithmic correctness and O(n) computational efficiency.",
        ],
    ),
    "radagast": Founder(
        id="radagast",
        name="Radagast",
        title="Property Perimeter, Wildlife & Hardware Sentinel",
        domain="security",
        specialized_domains=["security", "vision", "cameras", "wildlife", "hardware", "perimeter", "sentry", "sentinel", "faunasentinel"],
        weight=1.5,
        mission="Maintain 24/7 autonomous vigilance over the physical property, camera feeds, and wildlife population.",
        invariants=[
            "Pre-resize camera frames to 640px Lanczos before VLM evaluation.",
            "Backoff polling on battery-powered cameras (<10%) to prevent lockup.",
            "Protect server hardware against thermal runaway and power hazards.",
            "Maintain unique animal biometric identification records without duplicates.",
        ],
    ),
    "mandos": Founder(
        id="mandos",
        name="Mandos",
        title="Archivist, Vector Memory & Knowledge Curator",
        domain="memory",
        specialized_domains=["memory", "obsidian", "truth", "qdrant", "valkey", "dossiers", "archive", "reflection", "mnemosyne"],
        weight=1.5,
        mission="Synthesize and preserve the collective memory, research dossiers, and discoveries in eternal storage.",
        invariants=[
            "Never fabricate or hallucinate empirical evidence or URLs.",
            "Enforce semantic novelty threshold (< 0.85 cosine) on research dossiers.",
            "Synchronize verified insights to Obsidian with append-only integrity.",
            "If you don't know: ask a human.",
        ],
    ),
    "varda": Founder(
        id="varda",
        name="Varda",
        title="Home & Climate Concierge",
        domain="environment",
        specialized_domains=["environment", "energy", "presence", "smarthome", "hvac", "climate", "power", "plugs", "hearth", "concierge", "voice"],
        weight=1.5,
        mission="Orchestrate the physical living environment, execute instant smart home intents, and provide multimodal vision assistance.",
        invariants=[
            "Yield background compute during interactive workstation sessions.",
            "Respect shift work schedules via multi-signal presence fusion.",
            "Maintain climate comfort and energy efficiency through Home Assistant.",
            "Identify user action and held objects via Lanczos-resized camera perception.",
        ],
    ),
}

# Backward compatibility aliases
FOUNDERS["atlas"] = FOUNDERS["aule"]
FOUNDERS["sentinel"] = FOUNDERS["radagast"]
FOUNDERS["mnemosyne"] = FOUNDERS["mandos"]
FOUNDERS["hearth"] = FOUNDERS["varda"]
# ids a founder had before it was renamed (stored data still uses them)
for _old, _new in LEGACY_AGENT_IDS.items():
    if _new in FOUNDERS:
        FOUNDERS[_old] = FOUNDERS[_new]


def get_founder(identifier: str) -> Optional[Founder]:
    """Retrieves a Founder by ID or matching domain keyword."""
    clean = canonical_agent_id(identifier)
    if clean in FOUNDERS:
        return FOUNDERS[clean]
    for f in FOUNDERS.values():
        if clean in f.specialized_domains:
            return f
    return None


def is_founder(agent_id: str) -> bool:
    """Returns True if the agent ID belongs to one of the 4 Pillars."""
    return canonical_agent_id(agent_id) in FOUNDERS


def get_all_founders() -> List[Founder]:
    """Returns the 4 unique Valar Pantheon Founder instances."""
    unique = []
    seen = set()
    for f in FOUNDERS.values():
        if f.id not in seen:
            seen.add(f.id)
            unique.append(f)
    return unique


def evaluate_collective_vote(
    proposal: str,
    domain: str,
    votes: Dict[str, bool],
    veto_threshold: float = 0.50
) -> Dict[str, Any]:
    """
    Evaluates a collective vote across participating agents and Founders.
    Founders carry 1.5x weight in general, and 2.0x weight in their specialized domain.
    Standard agents carry 1.0x weight.
    """
    lead_founder = get_founder(domain) or FOUNDERS["atlas"]

    total_weight = 0.0
    approved_weight = 0.0
    breakdown = []

    for agent_id, approves in votes.items():
        aid = agent_id.strip().lower()
        if aid in FOUNDERS:
            f = FOUNDERS[aid]
            # Extra domain weight if voting in their primary pillar
            weight = 2.0 if lead_founder.id == f.id else f.weight
            is_founder_agent = True
        else:
            weight = 1.0
            is_founder_agent = False

        total_weight += weight
        if approves:
            approved_weight += weight

        breakdown.append({
            "agent_id": aid,
            "is_founder": is_founder_agent,
            "weight": weight,
            "vote": "YES" if approves else "NO",
        })

    approval_ratio = (approved_weight / total_weight) if total_weight > 0 else 0.0
    passed = approval_ratio >= veto_threshold

    return {
        "proposal": proposal,
        "domain": domain,
        "lead_founder": lead_founder.name,
        "passed": passed,
        "approval_ratio": round(approval_ratio, 3),
        "total_weight": round(total_weight, 2),
        "approved_weight": round(approved_weight, 2),
        "breakdown": breakdown,
    }
