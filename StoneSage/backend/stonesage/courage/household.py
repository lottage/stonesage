"""Who and what an agent's home has: people, pets, cameras, phones and speakers.

Supplied by an app (the Home app builds it from config.json "household", or discovers it: hearth/household.py) and handed to
the agent's tools through CourageDeps. The platform holds no household data of its own (docs/LAYERS.md M2): an agent
without a home gets the empty default, so its tools simply offer no cameras, people or phones.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

EXAMPLE_NAMES = ("Bella", "Rex")     # for tool examples when the household has fewer than two names


@dataclass
class Household:
    people: List[str] = field(default_factory=list)                     # display names; the first is the owner (approves)
    owner_pronouns: Tuple[str, str] = ("they", "them")                  # subject, object, for prompt text about the owner
    pets: Dict[str, str] = field(default_factory=dict)                  # display name -> species, in order
    cameras: Dict[str, Dict[str, str]] = field(default_factory=dict)    # key -> {"entity": "camera.x", "name": "Kitchen"}
    ptz_cameras: List[str] = field(default_factory=list)                # keys of cameras that can pan (camera_scan)
    default_camera: str = ""                                            # where to look when a sighting names no camera
    phones: List[str] = field(default_factory=list)                     # notify targets (lower-case person keys)
    speakers: List[str] = field(default_factory=list)                   # announce rooms; "everywhere" = every speaker
    descriptions: Dict[str, str] = field(default_factory=dict)          # display name -> how they look, for vision
    infra_words: List[str] = field(default_factory=list)                # words in a plug's entity id that mark the site's own servers

    @property
    def owner(self) -> str:
        return self.people[0] if self.people else "the owner"

    @property
    def who(self) -> List[str]:
        """Everyone presence can report on, people first, as displayed."""
        return list(self.people) + list(self.pets)

    @property
    def keys(self) -> List[str]:
        return [w.lower() for w in self.who]

    def pets_text(self) -> str:
        return ", ".join(f"{name} the {species}" for name, species in self.pets.items())

    def example_pair(self) -> Tuple[str, str]:
        """Two names for "that wasn't X, it was Y" examples: pets if there are two, else anyone, else stand-ins."""
        names = list(self.pets) if len(self.pets) >= 2 else self.who if len(self.who) >= 2 else list(EXAMPLE_NAMES)
        return names[-1], names[0]

    def camera_key(self, label: Optional[str]) -> str:
        """A sighting's camera label ('Living', 'Driveway') -> camera key; the default camera if none."""
        first = (label or "").lower().split("/")[0].split()[0] if (label or "").strip() else ""
        fallback = self.default_camera or next(iter(self.cameras), "")
        return next((k for k in self.cameras if first and k.startswith(first)), fallback)

    def lookout_text(self) -> str:
        """"Alex (dark hair, male), Bella (grey tabby cat)": who a camera description should name, in `who` order."""
        return ", ".join(f"{n} ({self.descriptions[n]})" if self.descriptions.get(n) else n for n in self.who)

    def speakers_text(self) -> str:
        rooms = [r for r in self.speakers if r != "everywhere"]
        return ", ".join(rooms) + (" or every Echo" if "everywhere" in self.speakers else "")
