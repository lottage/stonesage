"""Old names that stored data and old callers still use, mapped to the current ones.

The coding agent was called "Aevum" and is now "Atlas" (a placeholder name); the product was "Aevum" and is now
StoneSage. Persisted ids (agent profiles on disk, rows in the relational registry, the APK download link) were written
under the old names, so they must keep resolving. This is the ONLY place the old spelling lives: the personal-data scan
(tests/test_personal_data.py, server setup/build_public_repos.py) rejects it everywhere else and allowlists this file.

    canonical_agent_id("Aevum")       -> "atlas"
    legacy_ids_for("atlas")           -> ["aevum"]       (what an old profile directory or row may still be called)
"""

from typing import Dict, List

# agent id as it used to be written -> the id it has now. A child's id carried its parent's name, so the prefix counts too.
LEGACY_AGENT_IDS: Dict[str, str] = {"aevum": "atlas"}
# display names of those agents in old registry rows
LEGACY_AGENT_NAMES: Dict[str, str] = {"Aevum": "Atlas"}
# relational tables renamed from the product's old name
LEGACY_TABLES: Dict[str, str] = {
    "aevum_agent_registry": "stonesage_agent_registry",
    "aevum_project_registry": "stonesage_project_registry",
}
# the Android package used to be served under the old name; a phone with the old link or a host with the old file keeps working
LEGACY_APK_NAMES = ("aevum.apk",)
LEGACY_DOWNLOAD_PATHS = ("/download/aevum", "/aevum")


def canonical_agent_id(agent_id: str) -> str:
    """The current id for an agent id that may be an old one ("aevum", "aevum-steve-child-unit"); others come back cleaned."""
    clean = (agent_id or "").strip().lower()
    for old, new in LEGACY_AGENT_IDS.items():
        if clean == old or clean.startswith(old + "-"):
            return new + clean[len(old):]
    return clean


def canonical_agent_name(name: str) -> str:
    """The current display name for an old one ("Aevum", "Aevum-Steve-Child-Unit"); other names come back unchanged."""
    for old, new in LEGACY_AGENT_NAMES.items():
        if name == old or name.startswith(old + "-"):
            return new + name[len(old):]
    return name


def legacy_ids_for(agent_id: str) -> List[str]:
    """The old spellings an id may still be stored under (empty for an agent that was never renamed)."""
    clean = (agent_id or "").strip().lower()
    return [old + clean[len(new):] for old, new in LEGACY_AGENT_IDS.items()
            if clean == new or clean.startswith(new + "-")]
