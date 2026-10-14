"""
Courage's tool loop against the coordinator's OpenAI-compatible API (llama.cpp, jinja templates on).

run() yields plain event dicts; sse() turns them into the SSE chunks the StoneSage chat UI already
understands (tool_call / tool_result events and OpenAI-style content deltas).

Approval flow: an action the user ordered outright ("turn off the TV lights") runs at once. When Courage
inferred it ("it's cold in here" -> raise the heat) or it is an unlock/open, the loop stops, the action is
parked in PendingActions for that session, and Courage asks. A later "yes" in the same session runs it,
"no" drops it, anything else lets it expire.
"""

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from lodestone import endpoints
from lodestone.site import clock

from .escalation import failed
from .prompt import build_presence_card, build_static_prompt, build_system_prompt
from .tools import CourageTools, is_question
from .trace import TraceLog, TurnTrace

# A turn that switched something, notified or announced was a command: nothing to learn. Any other answered turn goes
# to the extractor, which skips device states itself (2026-09-26: "my sister Emma is allergic to cats" made Courage
# check where the cats were, and a "no home tools" rule then threw the fact away).
ACTION_TOOLS = {"ha_call", "notify", "speak"}
LEARN_MIN_CHARS = 12   # "hi", "thanks": nothing to remember

AFFIRM = re.compile(r"^\s*(yes|yeah|yep|yup|y|ok|okay|sure|do it|go ahead|go for it|approved?|confirm(ed)?|please do)\b", re.I)
DENY = re.compile(r"^\s*(no|nope|nah|cancel|don'?t|do not|stop|never ?mind|leave it)\b", re.I)
TOOL_MARKUP = re.compile(r":::(TOOL_CALL|TOOL_RESULT|APPROVAL):::.*?:::END_(TOOL_CALL|TOOL_RESULT|APPROVAL):::", re.S)
THINK = re.compile(r"<think>.*?</think>", re.S)
# llama.cpp answers HTTP 500 with this when it cannot parse the model's own tool-call JSON (a raw newline in a string, usually).
# The retry asks again with parse_tool_calls false and reads the calls out of the text. Mirrors grove/session.py (Grove must not
# be imported here, and this must not import Grove): keep the two in step.
PARSE_FAILURE = "Failed to parse tool call"
TEXT_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
# "I'll check the cameras", "let me look", "one moment": a promise to use a tool without calling it
PROMISE = re.compile(r"\b(i'?ll|i will|let me|going to|one moment|checking|(would you like|do you want|want) me to)\b.{0,40}"
                     r"\b(check|look|see|find|search|scan|increase|adjust|set|turn)"
                     r"|say yes to approve|\bshall i\b", re.I)  # an approval question only counts if a tool call made it
# "Would you like me to check anything else?", "Let me know if...": filler that makes every reply end in a question
TRAILING_OFFER = re.compile(r"\s*(?:(?:would you like|do you want|shall i|should i|want me to|can i|is there)[^.?!]*"
                            r"\banything else\b[^.?!]*[?.!]|(?:let me know|feel free)[^.?!]*[.!])\s*$", re.I)
# "The light is off. Would you like me to turn it on?" after a QUESTION: the answer is the state, not a sales pitch
ACTION_OFFER = re.compile(r"\s*(?:would you like|do you want|shall i|should i|want me to|can i|do you need me to)\b"
                          r"[^.?!]*\b(turn|switch|set|open|close|lock|unlock|raise|lower|dim|start|stop|activate|"
                          r"adjust|change|increase|decrease)\b[^.?!]*[?.!]\s*$", re.I)
NUDGE = "You described a tool call instead of making it. Call the right tool now; do not reply in text."

HISTORY_TURNS = 6  # 3 exchanges (was 2; longer history once made the model skip tools: re-check the live eval on changes)
HISTORY_CHARS = 1500  # per remembered message: a long story in the history must not crowd the 6k context
MAX_REPLY_TOKENS = 1200  # room for a short story; tool decisions stay short on their own (was 350)
PROMISE_MAX_CHARS = 300  # "Let me check the cameras." is a promise; a story that says "I'll look back..." is not
# The last sentence (2026-09-28): without it a greeting mid-conversation surveyed the house ("Hello?" -> 4-10 tool calls,
# 10-19 s). A/B: greetings with tools 7-14/24 -> 0/24, fact questions still 32/32. Softening "must come from a tool
# call" into an "if ..." instead fixed greetings too but lost car-notes lookups and made "Good morning" speak.
FRESH_FACTS = ("For the next message: device states, temperatures, who is where, camera views and household notes "
               "(cars, preferences, past events) must come from a tool call you make now, not from earlier replies or guesses. "
               "A greeting or small talk needs no tools.")
LITE_MAX_TOKENS = 400        # the stand-in on the CPU keeps replies short (stories wait for the full brain)
LITE_NOTE = ("\nRight now you run on the spare brain until {until} (the full one is lent out): keep replies short, and "
             "say so plainly if a question needs the full one.")


def _without_action_offer(text: str) -> str:
    """The reply minus a closing "Would you like me to turn it on?"; a reply that is only that offer stays."""
    cut = ACTION_OFFER.sub("", text).strip()
    return cut or text


def _chat_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


CONTEXT_POSITION = "second"  # presence card + memory: "first" (in the system prompt) | "second" | "late" (A/B 2026-09-28)
FRESH_FACTS_AS = "system"   # "system" (a late system message) | "user" (prefixed to the user's turn: every template takes it)


def already_text(name: str, state: str) -> str:
    return f"The {name} {'is' if not name.lower().endswith('s') else 'are'} already {state}."


def switched_text(name: str, state: Optional[str]) -> str:
    return f"{name} {state}." if state else f"{name} toggled."


# Short things Computer says over and over: their audio is made ahead of time (tts_cache.warm), so a reflex order or a
# yes/no answer is spoken with no synthesis wait. The same lines are the ones the code above produces.
CANNED_LINES = ["Right. Leaving it alone.", "One moment.", "Let me have a look.", "Done.", "Yes.", "No."]


def spoken(text: str) -> str:
    """Text for a speaker (HA voice, Echo): no markdown, no 'say yes to approve' UI wording."""
    text = re.sub(r"[*`#>]+", "", text or "").replace("_", " ")
    text = text.replace("Say yes to approve.", "Just say yes.")
    return re.sub(r"\s+", " ", text).strip()


def plain_text(text: str) -> str:
    """Reply text with no markdown (**bold**, *italic*, `code`, '#' headings, '-' and '*' bullets), which a speaker would
    read out, and with the typographic spaces (U+202F, U+00A0) made normal ones. Idempotent: plain_text(plain_text(t)) = plain_text(t)."""
    text = (text or "").replace(" ", " ").replace(" ", " ")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?![\s*])([^*\n]+?)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    text = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", text, flags=re.M)
    return re.sub(r"^[ \t]*[-*][ \t]+", "", text, flags=re.M)


def streamed(events: Iterator[Dict[str, Any]], holding: bool = False) -> Iterator[Dict[str, Any]]:
    """run()'s events for a client that shows or speaks 'partial' text as it comes: the final event then carries only
    the rest of the answer (its full text is under 'full'). An answer that does not continue what was streamed (a
    step cap or a lost model after a streamed preamble) follows it as a new paragraph.
    'holding' events (the takeback's "One moment." while the full brain comes back) become partials when `holding` is
    true (a stream a person watches or hears); otherwise they are dropped (Alexa's one-shot answer, a non-streaming reply)."""
    sent = ""
    for ev in events:
        if ev["type"] == "holding":
            if not holding:
                continue
            ev = {"type": "partial", "content": ev["content"]}
        if ev["type"] == "partial":
            sent += ev["content"]
        elif ev["type"] == "final" and sent:
            full = ev["content"] or ""
            rest = full[len(sent):] if full.startswith(sent) else "\n\n" + full
            ev = dict(ev, content=rest, full=full)
        yield ev


class PendingActions:
    """One pending approval per session, expiring after `ttl` seconds."""

    def __init__(self, ttl: float = 300.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def put(self, session_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
        action = dict(action, id=uuid.uuid4().hex[:8], created=time.time())
        with self._lock:
            self._items[session_id] = action
        return action

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            a = self._items.get(session_id)
            if a and time.time() - a["created"] > self.ttl:
                self._items.pop(session_id, None)
                return None
            return a

    def count(self) -> int:
        """Approvals still waiting for a yes/no (the idle gate won't lend a GPU while one is)."""
        now = time.time()
        with self._lock:
            return sum(1 for a in self._items.values() if now - a["created"] <= self.ttl)

    def pop_by_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Take a parked action by its id, whichever session parked it (phone approvals don't know the session)."""
        with self._lock:
            for sid, a in list(self._items.items()):
                if a.get("id") == action_id:
                    self._items.pop(sid, None)
                    return a if time.time() - a["created"] <= self.ttl else None
        return None

    def pop_by_watch_id(self, watch_id: str) -> Optional[Dict[str, Any]]:
        """Same as pop_by_id, keyed by the watch bridge's own ask id (a different id space than ours)."""
        with self._lock:
            for sid, a in list(self._items.items()):
                if a.get("watch_id") == watch_id:
                    self._items.pop(sid, None)
                    return a if time.time() - a["created"] <= self.ttl else None
        return None

    def pop(self, session_id: str) -> Optional[Dict[str, Any]]:
        a = self.get(session_id)
        with self._lock:
            self._items.pop(session_id, None)
        return a


def llm_timeout_s(cfg: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None) -> float:
    """Seconds one call to the coordinator may stay silent. config courage.llm_timeout_s wins; otherwise 600 s when the
    coordinator runs on a CPU (cluster.coordinator_cpu, COORDINATOR_CPU, or the live system profile: a few tokens a
    second, and minutes to read a long prompt) and 90 s when it runs on a GPU."""
    cfg = cfg if cfg is not None else {}
    try:
        configured = float((cfg.get("courage") or {}).get("llm_timeout_s") or 0)
    except (TypeError, ValueError):
        configured = 0.0
    if configured > 0:
        return configured
    return endpoints.CPU_LLM_TIMEOUT_S if endpoints.coordinator_on_cpu(cfg, profile) else endpoints.GPU_LLM_TIMEOUT_S


def _http_post_json(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_stream_json(url: str, body: Dict[str, Any], timeout: float) -> Iterator[Dict[str, Any]]:
    """The same call with stream=true: one parsed chunk per SSE line (llama.cpp puts `timings` on the last one).
    `timeout` is an IDLE limit, not a total deadline: urllib applies it to the connection and to every wait for the next
    bytes, so a slow reply that keeps producing tokens is never cut, and a model that goes silent for `timeout` seconds
    (including the wait for the first token while it reads the prompt) is."""
    req = urllib.request.Request(url, json.dumps(dict(body, stream=True)).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.strip()
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                yield json.loads(line[6:].decode("utf-8"))


# the end of a sentence (or a line of a list or poem) that is followed by more text: only whole sentences go out early,
# so the last one stays held back for TRAILING_OFFER to cut ("Let me know if...")
SENTENCE_END = re.compile(r"[.!?…:;][\"')\]*_]*(?=\s+\S)|\S(?=[ \t]*\n+\s*\S)")


def is_parse_failure(e: BaseException) -> bool:
    """An HTTP error whose body says the engine could not parse the model's tool-call JSON. The body is read here (once)."""
    if not isinstance(e, urllib.error.HTTPError) or e.code != 500:
        return False
    try:
        detail = e.read(4096).decode("utf-8", "replace")
    except (OSError, AttributeError):
        return False
    return PARSE_FAILURE in detail


def parse_text_calls(text: str, id_prefix: str = "text") -> Tuple[List[Dict[str, Any]], str]:
    """Tool calls written in a reply's text, for a model whose engine would not parse them: `<tool_call>{"name": ..., "arguments":
    {...}}</tool_call>` blocks, or else a whole reply that is one JSON object with a name. Raw newlines and control characters in
    strings are accepted (strict=False). -> (tool calls in the chat shape, the text without the blocks). Nothing parsed -> ([], text):
    the caller keeps the text as the answer."""
    blocks = [m.group(1) for m in TEXT_CALL.finditer(text or "")]
    if not blocks and (text or "").strip().startswith("{"):
        blocks = [text.strip()]
    calls: List[Dict[str, Any]] = []
    for raw in blocks:
        try:
            obj = json.loads(raw, strict=False)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str) or not obj["name"]:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args, strict=False)
            except ValueError:
                args = None
        args_text = json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)
        calls.append({"id": "%s-%d" % (id_prefix, len(calls) + 1), "type": "function",
                      "function": {"name": obj["name"], "arguments": args_text}})
    if not calls:
        return [], text
    rest = TEXT_CALL.sub("", text).strip() if TEXT_CALL.search(text) else ""
    return calls, rest


def json_args(raw: Any) -> str:
    """Tool-call arguments as a JSON object string the engine will accept when the call goes back to it in the history.
    A small model sometimes streams arguments that are empty or cut off (`{"camera":`); llama.cpp parses every assistant
    tool call in the REQUEST and answers HTTP 500 "Failed to parse tool call arguments as JSON" for the whole request, so
    one bad call would poison every later step of the turn (and the parse-failure retry, which resends the same history).
    Anything that is not a JSON object becomes {}."""
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    try:
        val = json.loads(raw, strict=False) if isinstance(raw, (str, bytes)) and raw.strip() else None
    except ValueError:
        val = None
    return json.dumps(val if isinstance(val, dict) else {}, ensure_ascii=False)


def clean_calls(calls: Any) -> List[Dict[str, Any]]:
    """Copies of tool calls with arguments that are valid JSON objects (see json_args)."""
    out = []
    for c in calls or []:
        if not isinstance(c, dict):
            continue
        fn = dict(c.get("function") or {})
        fn["arguments"] = json_args(fn.get("arguments"))
        out.append(dict(c, function=fn))
    return out


# A small model sometimes writes the tool's name as its reply instead of calling it: `presence_now`, `presence_now()`,
# `presence_now({"who": "bella"})`. Exactly that, nothing else, is a call.
BARE_CALL = re.compile(r"^[`\s]*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*(\{.*\})?\s*\)|(\{.*\}))?[`\s]*$", re.S)


def bare_tool_call(text: str, names: Any) -> Optional[Dict[str, Any]]:
    """The tool call a reply that is only a known tool's name (optionally with () or a JSON object) stands for, else None."""
    m = BARE_CALL.match(text or "")
    if not m or m.group(1) not in names:
        return None
    raw = m.group(2) or m.group(3)
    args: Dict[str, Any] = {}
    if raw:
        try:
            val = json.loads(raw, strict=False)
        except ValueError:
            return None            # a name followed by something broken is not a clean call: leave it to the nudge
        if not isinstance(val, dict):
            return None
        args = val
    return {"id": "bare-%s" % uuid.uuid4().hex[:8], "type": "function",
            "function": {"name": m.group(1), "arguments": json.dumps(args, ensure_ascii=False)}}


def describe_llm_failure(e: BaseException, timeout: float = 0) -> str:
    """What to tell the person when the model call failed: unreachable, silent too long, or answered with an error. No
    internal addresses or ports."""
    if isinstance(e, urllib.error.HTTPError):
        return (f"My language model answered with an error (HTTP {e.code}), so I could not work that one out. "
                "Try again; if it keeps happening, the model server's log has the details.")
    reason = getattr(e, "reason", None)
    if isinstance(e, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)):
        waited = f" within {timeout:.0f} s" if timeout else ""
        return f"My language model did not answer{waited}. It may be busy or still loading. Try again in a moment."
    if isinstance(e, (urllib.error.URLError, ConnectionError, OSError)):
        return "I cannot reach my language model right now. Try again in a moment."
    return f"Something went wrong while I was thinking ({type(e).__name__}). Try again in a moment."


def _release_point(text: str, start: int) -> int:
    """Where the finished sentences in text[start:] end (just after the punctuation), or start if none has."""
    cut = start
    for m in SENTENCE_END.finditer(text, start):
        cut = m.end()
    return cut


class CourageAgent:
    def __init__(self, tools: CourageTools, llm_url: str, presence_fn: Optional[Callable[[], Dict[str, Any]]] = None,
                 runs_on_fn: Optional[Callable[[], Optional[str]]] = None,
                 pending: Optional[PendingActions] = None, max_steps: int = 5, timeout: float = 90.0,
                 post: Callable[[str, Dict[str, Any], float], Dict[str, Any]] = _http_post_json,
                 stream_post: Optional[Callable[[str, Dict[str, Any], float], Iterator[Dict[str, Any]]]] = None,
                 model: str = "coordinator"):
        """model: the endpoint role (or model name) put in each request, the agent's brain (agents.py); the default is
        Computer's."""
        base = llm_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        self.url = base
        self.tools = tools
        self.presence_fn = presence_fn
        self.runs_on_fn = runs_on_fn
        self.on_approval: Optional[Callable[[str, Dict[str, Any]], bool]] = None  # e.g. push to the phone
        self.push = None   # PushApprovals, when the app gave a phone to approve on (server.get_courage_agent)
        self.on_resolved: Optional[Callable[[str], None]] = None  # a pending action's id, popped at the workstation
        self.learned = None  # home's LearnedReflexes: phrasings the loop resolved, replayed without the LLM
        # home's reflex (courage/reflex.resolve), injected so the loop never imports home code:
        # text, ha_states -> (entity, order) for a bare on/off order, or None. Unset = no reflex (a non-home agent).
        self.reflex_resolve: Optional[Callable[[str, Callable[[str], Dict[str, Any]]], Optional[Tuple[Dict[str, Any], Dict[str, str]]]]] = None
        self.trace_log: Optional[TraceLog] = None  # one record per turn (courage/trace.py); None = not recorded
        self.persona_fn: Optional[Callable[[], Optional[str]]] = None  # soul.py persona.md; None = prompt.PERSONA
        self.model = model
        # A house agent (Computer) gets the presence card and the "facts come from a tool" reminder in its prompt; an agent
        # without a home (agents.py: Atlas) gets only its persona and recalled memory notes.
        self.home_context = True
        self.memory = None  # courage/memory.py ConversationMemory: recalled into the prompt, learned from after a chat
        self.escalation = None  # courage/escalation.py Escalation: the ladder a failed turn climbs (Phase 6)
        # engine lease (engine_lease.EngineLease.brain): None | {"mode": "lite", url, until} | {"mode": "on_loan", until}
        self.brain: Optional[Callable[[], Optional[Dict[str, Any]]]] = None
        # courage/takeback.py Takeback: when a background lease (the garden) holds the coordinator's GPU and the spare brain
        # cannot do the job, give the GPU back and answer the same request on the full brain. None = never.
        self.takeback = None
        self.pending = pending or PendingActions()
        self.max_steps = max_steps
        self.timeout = timeout
        self.post = post
        # replies stream sentence by sentence (voice starts speaking sooner); a caller that fakes `post` (tests) and
        # passes no stream_post gets the old one-shot call
        self.stream_post = stream_post if stream_post is not None else (
            _http_stream_json if post is _http_post_json else None)

    def _resolved(self, action: Dict[str, Any]) -> None:
        """Tell on_resolved (e.g. the watch bridge) a pending action was settled here, so it clears elsewhere.
        Uses watch_id (the bridge's own ask id), not our id -- two different id spaces."""
        watch_id = action.get("watch_id")
        if self.on_resolved and watch_id:
            try:
                self.on_resolved(watch_id)
            except Exception:
                pass

    # ---- model call ----------------------------------------------------------
    def _complete(self, messages: List[Dict[str, Any]], url: Optional[str] = None,
                  max_tokens: Optional[int] = None, parse_tools: bool = True) -> Dict[str, Any]:
        """url / max_tokens: a lite turn sends these to the stand-in instead (engine lease). parse_tools False: the engine
        leaves the tool-call JSON alone and the caller reads it from the text (see _model_turn)."""
        data = self.post(url or self.url, self._body(messages, max_tokens, parse_tools), self.timeout)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        msg["_gen"] = self._gen(data.get("timings"), data.get("usage"))
        return msg

    def _body(self, messages: List[Dict[str, Any]], max_tokens: Optional[int], parse_tools: bool = True) -> Dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [dict(m, tool_calls=clean_calls(m["tool_calls"])) if m.get("tool_calls") else m for m in messages],
            "tools": self.tools.openai_tools(),
            "temperature": 0.3,
            "max_tokens": max_tokens or MAX_REPLY_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if not body["tools"]:
            del body["tools"]       # an agent with no tools offers none (an empty list is not sent)
        if not parse_tools:
            body["parse_tool_calls"] = False
        return body

    @staticmethod
    def _gen(timings: Optional[Dict[str, Any]], usage: Optional[Dict[str, Any]]):
        t = timings or {}  # llama.cpp: predicted_n tokens generated in predicted_ms
        return (t.get("predicted_n") or (usage or {}).get("completion_tokens") or 0, t.get("predicted_ms") or 0)

    def _step(self, messages: List[Dict[str, Any]], url: Optional[str] = None, max_tokens: Optional[int] = None,
              lead: str = ""):
        """One model call. Streamed when it can be: once the reply is past PROMISE_MAX_CHARS (shorter ones may be a
        promise that gets nudged, so they are held whole), each finished sentence goes out as a 'partial' event, the
        first one after `lead`. Returns the message like _complete, plus `_sent`: the reply text already sent."""
        if not self.stream_post:
            return self._complete(messages, url, max_tokens)
        raw, calls, timings, usage, sent = "", {}, None, None, 0
        for d in self.stream_post(url or self.url, self._body(messages, max_tokens), self.timeout):
            timings, usage = d.get("timings") or timings, d.get("usage") or usage
            delta = (d.get("choices") or [{}])[0].get("delta") or {}
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0),
                                        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            raw += delta.get("content") or ""
            if calls or "<think" in raw:
                continue
            clean = raw.lstrip()
            if len(clean) <= PROMISE_MAX_CHARS:
                continue
            cut = _release_point(clean, sent)
            if cut > sent:
                yield {"type": "partial", "content": ("" if sent else lead) + clean[sent:cut]}
                sent = cut
        msg = {"role": "assistant", "content": raw, "_gen": self._gen(timings, usage), "_sent": raw.lstrip()[:sent]}
        if calls:
            msg["tool_calls"] = [dict(c, id=c["id"] or f"call_{uuid.uuid4().hex[:8]}") for _, c in sorted(calls.items())]
        return msg

    def _model_turn(self, messages: List[Dict[str, Any]], url: Optional[str] = None, max_tokens: Optional[int] = None,
                    lead: str = ""):
        """One model call with its recoveries (_recovering_turn); the tool calls it returns carry valid JSON arguments."""
        msg = yield from self._recovering_turn(messages, url, max_tokens, lead=lead)
        if msg.get("tool_calls"):
            msg["tool_calls"] = clean_calls(msg["tool_calls"])
        return msg

    def _recovering_turn(self, messages: List[Dict[str, Any]], url: Optional[str] = None, max_tokens: Optional[int] = None,
                         lead: str = ""):
        """_step, with one recovery: when the engine answers 500 because it could not parse the model's tool-call JSON, the same
        request goes again with the engine's parsing off, and the tool calls are read from the reply's text. Nothing parseable
        there -> the text is the answer. Any other failure is raised as before."""
        try:
            return (yield from self._step(messages, url, max_tokens, lead=lead))
        except urllib.error.HTTPError as e:
            if not is_parse_failure(e):
                raise
        msg = self._complete(messages, url, max_tokens, parse_tools=False)
        calls, rest = parse_text_calls(msg.get("content") or "", "text-retry")
        if calls:
            msg = dict(msg, content=rest, tool_calls=calls)
        return msg

    # ---- conversation shaping ------------------------------------------------
    def _recall(self, text: str, tr: Optional[TurnTrace] = None) -> str:
        """The memory card for this message ('' when there is no memory, nothing relevant, or the embedder is down)."""
        if not self.memory or not isinstance(text, str):
            return ""
        try:
            notes = self.memory.recall(text)
        except Exception:
            return ""                                     # memory is a bonus; a turn never fails on it
        if tr is not None and notes:
            tr.recalled = [{"id": n["id"], "score": n["score"]} for n in notes]
        return self.memory.card(notes)

    @staticmethod
    def _history_messages(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        turns = [m for m in history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        for m in turns[-HISTORY_TURNS:]:
            text = THINK.sub("", TOOL_MARKUP.sub("", m["content"])).strip()
            if len(text) > HISTORY_CHARS:
                text = text[:HISTORY_CHARS] + " …"
            if text:
                out.append({"role": m["role"], "content": text})
        return out

    def _build_plain_messages(self, history: List[Dict[str, Any]], memory_card: str = "") -> List[Dict[str, Any]]:
        """The prompt of an agent without a home: persona, then recalled notes (if any), then the history."""
        persona = None
        if self.persona_fn:
            try:
                persona = self.persona_fn()
            except Exception:
                persona = None
        system = build_static_prompt(None, persona)
        if memory_card and FRESH_FACTS_AS == "user":      # a strict template takes one system message only
            return [{"role": "system", "content": system + "\n\n" + memory_card}] + self._history_messages(history)
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        if memory_card:
            msgs.append({"role": "system", "content": memory_card})
        return msgs + self._history_messages(history)

    def _build_messages(self, history: List[Dict[str, Any]], memory_card: str = "") -> List[Dict[str, Any]]:
        if not self.home_context:
            return self._build_plain_messages(history, memory_card)
        presence = None
        if self.presence_fn:
            try:
                presence = self.presence_fn()
            except Exception:
                presence = None
        runs_on = None
        if self.runs_on_fn:
            try:
                runs_on = self.runs_on_fn()
            except Exception:
                runs_on = None
        persona = None
        if self.persona_fn:
            try:
                persona = self.persona_fn()
            except Exception:
                persona = None
        # Where the changing context (presence card with its clock, recalled memory notes) goes; see CONTEXT_POSITION.
        # A strict template (FRESH_FACTS_AS == "user") takes one system message only, so it keeps "first".
        position = "first" if FRESH_FACTS_AS == "user" and CONTEXT_POSITION == "second" else CONTEXT_POSITION
        who = self.tools.deps.household.who          # the household's people and pets (supplied by the Home app)
        context = build_presence_card(presence, who=who) + (("\n\n" + memory_card) if memory_card else "")
        if position == "first":
            system = build_system_prompt(presence, runs_on=runs_on, persona=persona, who=who) + (("\n\n" + memory_card) if memory_card else "")
        else:
            system = build_static_prompt(runs_on, persona)
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        if position == "second":
            # right after the persona + the tool schemas the chat template appends to it: those ~1,300 tokens stay a
            # cacheable prefix, only this card (~250) is re-read, and it is still early, away from the user's words
            msgs.append({"role": "system", "content": context})
        msgs.extend(self._history_messages(history))
        base = 3 if position == "second" else 2           # messages before the first history turn, plus that turn
        if position == "late":
            # A/B 2026-09-28: tool choice mid-conversation fell to 43/46 twice (the card next to the user's words
            # pulled "Good morning" to presence_now and dropped memory lookups). Kept for comparison only.
            late = context
            if len(msgs) > base:
                late += "\n\n" + FRESH_FACTS
            if FRESH_FACTS_AS == "user" and msgs[-1]["role"] == "user":
                msgs[-1] = {"role": "user", "content": f"[{late}]\n\n{msgs[-1]['content']}"}
            elif msgs[-1]["role"] == "user":
                msgs.insert(len(msgs) - 1, {"role": "system", "content": late})
            else:
                msgs.append({"role": "system", "content": late})
            return msgs
        if len(msgs) > base:
            # With history in context the model stops calling tools and answers from thin air; a late reminder fixes it.
            # As a system message it works for Qwen3, but stricter templates (Qwen3.8 family: Bonsai 2, 2026-09-28)
            # refuse a system message that isn't first (HTTP 500), so FRESH_FACTS_AS can put it on the user's turn.
            if FRESH_FACTS_AS == "user" and msgs[-1]["role"] == "user":
                msgs[-1] = {"role": "user", "content": f"[{FRESH_FACTS}]\n\n{msgs[-1]['content']}"}
            else:
                msgs.insert(len(msgs) - 1, {"role": "system", "content": FRESH_FACTS})
        return msgs

    @staticmethod
    def _args(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            val = json.loads(raw or "{}")
            return val if isinstance(val, dict) else {}
        except (TypeError, ValueError):
            return {}

    # ---- reflex: bare on/off orders without the LLM ------------------------------------
    def _reflex(self, text: str, tr: TurnTrace):
        """Generator; returns True when it handled the message (events already yielded)."""
        learned = self.learned.lookup(text) if self.learned else None
        if learned:
            ent, _ = self.tools.resolve_entity(learned["domain"], learned["entity_id"])
            if ent is None:
                self.learned.forget(learned["key"])  # the device is gone: fall back to the loop
                return False
            args = {"domain": learned["domain"], "service": learned["service"], "entity_id": learned["entity_id"]}
            state = learned["service"].replace("turn_", "") if learned["service"] != "toggle" else None
            tr.path = "learned"
            handled = yield from self._switch(args, ent.get("friendly_name") or learned["name"], state, ent.get("state"), tr)
            if handled:
                self.learned.hit(learned["key"])
            else:
                tr.path = "loop"
            return handled
        found = self.reflex_resolve(text, self.tools.deps.ha_states) if self.reflex_resolve else None
        if not found:
            return False
        ent, order = found
        args = {"domain": ent["domain"], "service": f"turn_{order['state']}", "entity_id": ent["entity_id"]}
        tr.path = "reflex"
        handled = yield from self._switch(args, ent.get("friendly_name") or ent["entity_id"], order["state"], ent.get("state"), tr)
        if not handled:
            tr.path = "loop"
        return handled

    def _switch(self, args: Dict[str, Any], name: str, state: Optional[str], current: Optional[str], tr: TurnTrace):
        if self.tools.validate("ha_call", args):
            return False  # refused (e.g. server plug): let the loop explain
        if state and current == state and not self.tools.changed_just_now(args["entity_id"], state):
            yield {"type": "final", "content": already_text(name, state)}
            return True
        verb = f"Switching {state}" if state else "Toggling"
        yield {"type": "tool_call", "name": "ha_call", "arguments": args, "status": f"{verb} {name}…"}
        tr.tool_started()
        result = self.tools.execute("ha_call", args)
        tr.tool("ha_call", args, result)
        yield {"type": "tool_result", "name": "ha_call", "result": result}
        ok = json.loads(result).get("ok", False) if result.startswith("{") else False
        done = switched_text(name, state)
        yield {"type": "final", "content": done if ok else f"Home Assistant refused to change the {name}."}
        return True

    # ---- the loop ------------------------------------------------------------
    def run(self, history: List[Dict[str, Any]], session_id: str = "default") -> Iterator[Dict[str, Any]]:
        """Events: tool_call, tool_result, approval_required, partial (finished sentences of a long reply, as they are
        written), holding (the takeback's "One moment."; see streamed()), then usage (tokens generated, tok/s) and final
        (the whole answer; see streamed()). Each turn is recorded in self.trace_log (courage/trace.py) when it ends, however
        it ends. With a takeback set, a turn on the spare brain of a borrowed GPU may be answered again on the full one."""
        if self.takeback is not None:
            yield from self.takeback.run(self, history, session_id)
        else:
            yield from self._turn(history, session_id)

    def _turn(self, history: List[Dict[str, Any]], session_id: str = "default", full: bool = False,
              probe: bool = False) -> Iterator[Dict[str, Any]]:
        """One turn. full: answer on the real coordinator even if a lease has lent it out (the takeback has just given it back).
        probe: this turn runs on the spare brain of a lease that can be taken back; it never climbs the escalation ladder, and
        instead of answering a request the spare brain cannot do it yields 'takeback_request', or a final marked 'failed'."""
        gen = [0, 0.0]
        last_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        tr = TurnTrace(session_id, last_user if isinstance(last_user, str) else "")
        # only a house agent (Computer) speaks its replies: an agent without a home (Atlas) keeps markdown, code fences included
        speak = plain_text if self.home_context else (lambda t: t)
        try:
            for ev in self._run(history, session_id, gen, tr, full=full, probe=probe):
                if ev["type"] == "partial":
                    ev = dict(ev, content=speak(ev.get("content") or ""))
                if ev["type"] == "final":
                    why = (failed(tr.outcome, tr.triggers) or ("llm_error" if "lite_down" in tr.triggers else None)) if probe else None
                    if why:
                        ev = dict(ev, failed=why)          # the takeback decides what happens to a failed spare-brain turn
                    else:
                        ev = self._escalate(ev, tr, session_id)
                    ev = dict(ev, content=speak(ev.get("content") or ""))   # no markdown: it is spoken aloud too
                    tr.end("answered", ev.get("content") or "")
                    if gen[0]:
                        yield {"type": "usage", "completion_tokens": gen[0],
                               "tps": round(gen[0] / (gen[1] / 1000), 1) if gen[1] else 0}
                yield ev
        except Exception:
            tr.end("error")
            raise
        finally:
            if tr.outcome is None:
                tr.end("abandoned")          # the client went away mid-turn (GeneratorExit)
            if self.trace_log:
                self.trace_log.write(tr.record())
            if self.memory and self._worth_learning(tr):
                self.memory.learn_later(tr.user, tr.final, session_id)
            if self.escalation and any("tool" in s and not s.get("ok") for s in tr.steps):
                threading.Thread(target=self.escalation.tool_errors, args=(list(tr.steps),), daemon=True,
                                 name="escalation-tool-errors").start()   # a tool that keeps failing -> human

    def _escalate(self, ev: Dict[str, Any], tr: TurnTrace, session_id: str) -> Dict[str, Any]:
        """A failed turn (step cap, :8001 down, empty answer) climbs the ladder (courage/escalation.py); the user gets
        the best answer found, or the local text plus where the details went."""
        reason = failed(tr.outcome, tr.triggers) if self.escalation else None
        if not reason:
            return ev
        try:
            esc = self.escalation.handle(session_id, tr.user or "", ev.get("content") or "", reason, tr.steps,
                                         tr.context, round(tr.t0, 3))
        except Exception:
            return ev
        tr.escalated = {"outcome": esc["record"]["outcome"], "by": esc["record"].get("answered_by")}
        return dict(ev, content=esc["content"])

    @staticmethod
    def _worth_learning(tr: TurnTrace) -> bool:
        """An answered loop turn that did not act on the house, with more than a greeting in it."""
        tools = {s["tool"] for s in tr.steps if "tool" in s}
        return (tr.outcome == "answered" and tr.path == "loop" and not (tools & ACTION_TOOLS)
                and tr.brain != "lite"    # memory's own model is the lent-out coordinator
                and len((tr.user or "").strip()) >= LEARN_MIN_CHARS)

    def _run(self, history: List[Dict[str, Any]], session_id: str, gen: List[float],
             tr: TurnTrace, full: bool = False, probe: bool = False) -> Iterator[Dict[str, Any]]:
        if full:
            tr.trigger("takeback_rerun")
        last_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        messages = self._build_messages(history, self._recall(last_user, tr))
        tr.context = messages          # the escalation ladder reads this turn's tool calls and results from it

        pending = self.pending.get(session_id)
        if pending:
            if DENY.match(last_user):
                self.pending.pop(session_id)
                self._resolved(pending)
                tr.path = "approval_no"
                yield {"type": "final", "content": "Right. Leaving it alone."}
                return
            if AFFIRM.match(last_user):
                self.pending.pop(session_id)
                self._resolved(pending)
                tr.path = "approval_yes"
                call_id = f"call_{pending['id']}"
                yield {"type": "tool_call", "name": pending["name"], "arguments": pending["args"],
                       "status": self.tools.status_text(pending["name"], pending["args"]).replace("Asking to", "Going to")}
                tr.tool_started()
                result = self.tools.execute(pending["name"], pending["args"])
                tr.tool(pending["name"], pending["args"], result)
                yield {"type": "tool_result", "name": pending["name"], "result": result}
                messages.append({"role": "assistant", "content": "", "tool_calls": [
                    {"id": call_id, "type": "function",
                     "function": {"name": pending["name"], "arguments": json.dumps(pending["args"])}}]})
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            # anything else: a new request, the old action quietly expires on its own

        if not (pending and AFFIRM.match(last_user)):
            handled = yield from self._reflex(last_user, tr)
            if handled:
                return

        brain = self.brain() if (self.brain and not full) else None     # engine lease (engine_lease.py): who answers for :8001 now
        until = clock(datetime.fromtimestamp(brain["until"])) if brain else ""
        if probe and brain and brain.get("mode") == "on_loan":   # nothing answers on a lease the garden can give back: give it back
            tr.trigger("takeback_on_loan")
            tr.end("takeback")
            yield {"type": "takeback_request", "why": "on_loan"}
            return
        if brain and brain.get("mode") == "on_loan":   # lent out, no stand-in: say so plainly (not a failure)
            text = (f"Done. The big brain's on loan until {until}, so that's all I can say for now."
                    if tr.path == "approval_yes" else
                    f"The big brain's on loan until {until}. Plain on/off orders still work; anything else will have to wait.")
            tr.path = "on_loan" if tr.path == "loop" else tr.path
            tr.end("on_loan", text)
            yield {"type": "final", "content": text}
            return
        # Lite (DESIGN.md B2): the stand-in on the CPU answers. Every action asks, replies stay short, and it says so.
        lite = bool(brain and brain.get("mode") == "lite")
        llm: Dict[str, Any] = {}
        if lite:
            llm = {"url": _chat_url(brain["url"]), "max_tokens": LITE_MAX_TOKENS}
            messages[0] = dict(messages[0], content=messages[0]["content"] + LITE_NOTE.format(until=until))
            tr.brain = "lite"

        nudged = False
        switched = []  # successful direct ha_calls this turn; a phrasing is learned only if it caused exactly one
        said = ""      # reply text already streamed this turn ('partial' events); the final answer starts with it
        for _ in range(self.max_steps):
            t_llm = time.time()
            prior = said
            try:
                msg = yield from self._model_turn(messages, lead="\n\n" if prior else "", **llm)
                if msg.get("_sent"):
                    said = prior + ("\n\n" if prior else "") + msg["_sent"]
                gen[0] += msg["_gen"][0]
                gen[1] += msg["_gen"][1]
            except Exception as e:
                if lite:   # the stand-in failed too: an honest answer, not a ladder climb (the lease is the cause)
                    tr.trigger("lite_down")      # a takeback treats this as a failed turn (llm_error)
                    text = f"The big brain's on loan until {until} and the spare one isn't answering either. Try me then."
                    tr.end("on_loan", text)
                    yield {"type": "final", "content": text}
                    return
                text = describe_llm_failure(e, self.timeout)
                tr.end("llm_error", text)
                yield {"type": "final", "content": text}
                return
            tr.llm((time.time() - t_llm) * 1000, msg["_gen"][0], len(msg.get("tool_calls") or []), msg["_gen"][1])

            calls = msg.get("tool_calls") or []
            if not calls and not msg.get("_sent"):
                bare = bare_tool_call(THINK.sub("", msg.get("content") or ""), {t["function"]["name"] for t in self.tools.openai_tools()})
                if bare:                      # the model wrote the tool's name instead of calling it
                    tr.trigger("bare_tool_name")
                    calls, msg["content"] = [bare], ""
            sent = msg.get("_sent") or ""
            asked = is_question(last_user)
            if not calls and sent:   # only the part not yet streamed can still lose a trailing offer
                rest = TRAILING_OFFER.sub("", msg["content"].lstrip()[len(sent):])
                msg["content"] = (sent + (_without_action_offer(rest) if asked else rest)).rstrip()
            elif not calls:
                text = TRAILING_OFFER.sub("", THINK.sub("", msg.get("content") or "")).strip()
                msg["content"] = _without_action_offer(text) if asked else text
            if not calls and not nudged and not sent and len(msg.get("content") or "") <= PROMISE_MAX_CHARS \
                    and PROMISE.search(msg.get("content") or "") and self.tools.openai_tools():   # no tools: nothing to call
                nudged = True
                tr.trigger("nudged")
                messages.append({"role": "assistant", "content": msg.get("content") or ""})
                messages.append({"role": "user", "content": NUDGE})
                continue
            if not calls:
                # a streamed preamble from an earlier step (rare: it has to pass PROMISE_MAX_CHARS) stays in front
                text = prior + ("\n\n" if prior and msg["content"] else "") + msg["content"]
                if self.learned and len(switched) == 1:
                    self.learned.learn(last_user, *switched[0])
                if not text:
                    tr.trigger("empty_answer")
                yield {"type": "final", "content": text or "I have nothing useful to add, which is rare."}
                return

            if probe and lite and self.takeback is not None:
                # decide BEFORE running anything: a request the spare brain picked a camera / think_harder tool for needs the full one
                wanted = next(((call.get("function") or {}).get("name", "") for call in calls
                               if self.takeback.wants_full((call.get("function") or {}).get("name", ""))), None)
                if wanted:
                    tr.trigger("takeback_tool")
                    tr.end("takeback")
                    yield {"type": "takeback_request", "why": "tool", "tool": wanted}
                    return
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for i, call in enumerate(calls):
                fn = call.get("function") or {}
                name, args = fn.get("name", ""), self._args(fn.get("arguments"))
                call_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"

                if self.tools.needs_approval(name):
                    err = self.tools.validate(name, args)
                    if err:
                        tr.invalid(name, args, err)
                        result = json.dumps({"ok": False, "error": err})
                        yield {"type": "tool_result", "name": name, "result": result}
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                        continue
                if name in ACTION_TOOLS and is_question(last_user) \
                        and not self.tools.is_direct_command(name, args, last_user):
                    # "Is the kitchen light on?" read the state and then offered to turn something on (2026-09-28, real
                    # states: HA has no kitchen light, only the camera's floodlight). A question gets an answer.
                    err = "the user asked a question: answer it from what you read, and do not change or offer to change any device"
                    tr.invalid(name, args, err)
                    tr.trigger("question_action_blocked")
                    result = json.dumps({"ok": False, "error": err})
                    yield {"type": "tool_result", "name": name, "result": result}
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
                    continue
                if self.tools.needs_approval(name) and (lite or not self.tools.is_direct_command(name, args, last_user)):
                    action = self.pending.put(session_id, {"name": name, "args": args,
                                                           "summary": self.tools.describe_action(name, args)})
                    yield {"type": "approval_required", "id": action["id"], "name": name,
                           "arguments": args, "summary": action["summary"]}
                    pushed = False
                    if self.on_approval:
                        try:
                            pushed = bool(self.on_approval(session_id, action))
                        except Exception:
                            pushed = False
                    skipped = len(calls) - i - 1
                    extra = f" (I've held back {skipped} other request{'s' if skipped > 1 else ''} until then.)" if skipped else ""
                    phone = " Or tap Yes on your phone." if pushed else ""
                    text = f"Shall I {action['summary']}? Say yes to approve.{phone}{extra}"
                    tr.end("asked_approval", text)
                    yield {"type": "final", "content": text}
                    return

                yield {"type": "tool_call", "name": name, "arguments": args, "status": self.tools.status_text(name, args)}
                tr.tool_started()
                result = self.tools.execute(name, args)
                tr.tool(name, args, result)
                yield {"type": "tool_result", "name": name, "result": result}
                if name == "ha_call" and self.learned and result.startswith("{") and json.loads(result).get("ok"):
                    ent, _ = self.tools.resolve_entity(args.get("domain", ""), args.get("entity_id", ""))
                    switched.append((args, (ent or {}).get("friendly_name") or args.get("entity_id", "")))
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

        text = "I've gone round in circles on that one. Ask me again, more plainly?"
        tr.end("step_cap", text)
        yield {"type": "final", "content": text}

    # ---- SSE for the StoneSage chat route -----------------------------------
    def sse(self, history: List[Dict[str, Any]], session_id: str = "default") -> Iterator[str]:
        def chunk(obj: Dict[str, Any]) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def content(text: str) -> str:
            return chunk({"object": "chat.completion.chunk", "model": "courage",
                          "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})

        usage = None
        for ev in streamed(self.run(history, session_id), holding=True):
            if ev["type"] == "usage":
                usage = {"completion_tokens": ev["completion_tokens"], "tps": ev["tps"]}
            elif ev["type"] in ("partial", "final"):
                if ev["content"]:
                    yield content(ev["content"])
            else:
                yield chunk(ev)
        yield chunk({"object": "chat.completion.chunk", "model": "courage", "usage": usage,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield "data: [DONE]\n\n"
