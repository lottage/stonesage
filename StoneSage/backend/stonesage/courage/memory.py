"""
Courage's conversational memory (2026-09-26): short notes about the people he talks to, learned from conversation
and recalled by meaning in later ones. Everything stays local: the notes live in data/courage_memories.json on the StoneSage host,
the coordinator (:8001) decides what is worth keeping, the BGE embedder (:8003) finds what is relevant.

Write: after a conversational turn, a background call asks the coordinator for 0-2 lasting notes (most turns give
none). A note within DUPLICATE of an existing one only refreshes it.
Read: each turn, the user's message is compared with every note; up to RECALL_MAX notes scoring >= RECALL_MIN go into
the system prompt with the date they were learned. Calibrated on the live embedder 2026-09-26 (with BGE's query
prefix): relevant 0.40-0.65 with the right note always first; home commands and arithmetic 0.33-0.35.
The owner sees and deletes notes in Engine Console -> 🧠 MEMORIES (GET /api/courage/memories, POST .../forget).
"""

import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # BGE v1.5, for short queries
RECALL_MIN = 0.40
RECALL_MAX = 3
DUPLICATE = 0.90
MAX_NOTES = 500
NOTE_CHARS = 240

EXTRACT = (
    "You keep a companion's long-term memory. From the exchange below, write down only what is worth remembering "
    "for weeks about the person talking to Computer and their household: events in their life, plans, people they "
    "mention, preferences, feelings that matter, things they asked Computer to remember. "
    "Write only what the user said; Computer's reply is context, never a source (not what he checked, inferred or "
    "invented, and never his stories). Skip small talk, jokes, requests for stories and house device states. "
    "Write each note to the person, in the second person (\"Your sister Emma...\", \"You had a rough day...\").\n"
    'Reply with JSON only: {"notes": ["one short sentence", ...]} with at most 2 notes, or {"notes": []}. '
    "Most exchanges have nothing worth keeping.\n\nThe user said: {user}\nComputer replied: {reply}"
)


def cosine(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return num / den if den else 0.0


def parse_notes(text: str) -> List[str]:
    """The extractor's JSON -> clean note sentences (tolerant of code fences and chatter)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        notes = json.loads(m.group(0)).get("notes") or []
    except (ValueError, AttributeError):
        return []
    out = []
    for n in notes[:2]:
        if isinstance(n, str) and 8 <= len(n.strip()) <= 400:
            out.append(n.strip()[:NOTE_CHARS])
    return out


class ConversationMemory:
    def __init__(self, path: Optional[str], embed: Callable[[List[str]], List[List[float]]],
                 llm: Callable[[str], str], clock: Callable[[], float] = time.time,
                 on_write: Optional[Callable[[Dict[str, Any]], None]] = None, extract: str = EXTRACT):
        """embed: texts -> vectors (BGE on :8003). llm: prompt -> text (the coordinator, no tools).
        on_write: gets one trace record per extraction (kind 'memory'). extract: the extraction prompt ({user} and
        {reply} are filled in); another agent brings its own."""
        self.path, self.embed, self.llm, self.clock, self.on_write = path, embed, llm, clock, on_write
        self.extract = extract
        self._lock = threading.Lock()
        self.notes: List[Dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        """Read the notes file again (a soul was imported over it, or a person edited it)."""
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                notes = json.load(f).get("notes", [])
        except (OSError, ValueError, AttributeError):
            return
        with self._lock:
            self.notes = notes if isinstance(notes, list) else []

    # ---- storage ------------------------------------------------------------------
    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"notes": self.notes}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def items(self) -> List[Dict[str, Any]]:
        """Newest first, without the vectors (for the UI)."""
        with self._lock:
            return [{k: v for k, v in n.items() if k != "vec"} for n in sorted(self.notes, key=lambda n: -n["at"])]

    def forget(self, note_id: str) -> bool:
        with self._lock:
            before = len(self.notes)
            self.notes = [n for n in self.notes if n["id"] != note_id]
            changed = len(self.notes) != before
            if changed:
                self._save()
        return changed

    def add(self, text: str, session: str = "", vec: Optional[List[float]] = None) -> Dict[str, Any]:
        """Store a note, or refresh the near-identical one already there. Returns {id, stored|refreshed}."""
        vec = vec or self.embed([text[:900]])[0]
        now = self.clock()
        with self._lock:
            best = max(self.notes, key=lambda n: cosine(vec, n["vec"]), default=None)
            if best is not None and cosine(vec, best["vec"]) >= DUPLICATE:
                best["seen"] = now
                best["times"] = best.get("times", 1) + 1
                self._save()
                return {"id": best["id"], "refreshed": True}
            note = {"id": uuid.uuid4().hex[:10], "text": text, "at": now, "seen": now, "times": 1,
                    "session": session, "vec": [round(x, 5) for x in vec]}
            self.notes.append(note)
            if len(self.notes) > MAX_NOTES:                    # drop the least recently confirmed
                self.notes.sort(key=lambda n: n["seen"])
                self.notes = self.notes[-MAX_NOTES:]
            self._save()
            return {"id": note["id"], "stored": True}

    # ---- read ------------------------------------------------------------------------
    def recall(self, query: str) -> List[Dict[str, Any]]:
        """Up to RECALL_MAX notes relevant to `query` (score >= RECALL_MIN), best first."""
        with self._lock:
            if not self.notes or not (query or "").strip():
                return []
            notes = list(self.notes)
        qv = self.embed([QUERY_PREFIX + query[:800]])[0]
        scored = sorted(((cosine(qv, n["vec"]), n) for n in notes), key=lambda x: -x[0])
        return [dict(n, score=round(s, 3)) for s, n in scored[:RECALL_MAX] if s >= RECALL_MIN]

    @staticmethod
    def card(recalled: List[Dict[str, Any]]) -> str:
        """The prompt block: a few dated lines, never a dump."""
        if not recalled:
            return ""
        lines = [f"- {time.strftime('%b %d', time.localtime(n['at']))}: {n['text']}" for n in recalled]
        return ("[From earlier conversations with the person you are talking to (the date is when you learned it). "
                "Use this naturally when it fits, as a friend would; don't recite it, and don't add details they "
                "didn't tell you.]\n" + "\n".join(lines))

    # ---- write -------------------------------------------------------------------------
    def learn(self, user: str, reply: str, session: str = "") -> Dict[str, Any]:
        """Ask the coordinator what is worth keeping from one exchange and store it. Returns a trace record."""
        t0 = self.clock()
        rec: Dict[str, Any] = {"kind": "memory", "at": round(t0, 3), "session": session, "outcome": "nothing",
                               "stored": [], "refreshed": 0, "triggers": []}
        try:
            prompt = self.extract.replace("{user}", user[:1500]).replace("{reply}", reply[:1500])
            notes = parse_notes(self.llm(prompt))
            for text in notes:
                res = self.add(text, session)
                if res.get("stored"):
                    rec["stored"].append(text)
                else:
                    rec["refreshed"] += 1
            if rec["stored"]:
                rec["outcome"] = "stored"
            elif rec["refreshed"]:
                rec["outcome"] = "refreshed"
        except Exception as e:
            rec["outcome"] = "error"
            rec["error"] = f"{type(e).__name__}: {e}"[:200]
            rec["triggers"].append("memory_error")
        rec["ms"] = round((self.clock() - t0) * 1000)
        if self.on_write:
            try:
                self.on_write(rec)
            except Exception:
                pass
        return rec

    def learn_later(self, user: str, reply: str, session: str = "") -> None:
        """learn() in a background thread, after the reply has reached the user."""
        threading.Thread(target=self.learn, args=(user, reply, session), name="courage-memory", daemon=True).start()
