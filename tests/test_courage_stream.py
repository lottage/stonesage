"""Offline tests for Computer's streamed replies (Phase 4 voice): long answers go out sentence by sentence as the
model writes them, so a speaker can start before the reply is finished. Fake streaming llama server, no network."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from stonesage.courage import CourageAgent, CourageTools, PendingActions  # noqa: E402
from stonesage.courage.agent import PROMISE_MAX_CHARS, _release_point, streamed  # noqa: E402
from agent_fixture import PRESENCE, FakeHA, make_agent  # noqa: E402

STORY = ("Old Elias kept the lamp on the northern rock for forty years. Every night he climbed the hundred and twelve "
         "steps with a can of oil and a flask of tea. Ships passed, and none of them ever knew his name. One winter the "
         "gale took the glass clean out of the lantern room. He stood in the wind with a hand lantern until dawn. "
         "The fishing boat that came home that morning carried his daughter. She had never told him she worked the "
         "boats. He never asked why she cried when she saw him on the rock.")


def text_chunks(text, size=7):
    """The reply as the model streams it: a few characters per chunk, timings on the last one."""
    out = [{"choices": [{"delta": {"role": "assistant", "content": None}}]}]
    out += [{"choices": [{"delta": {"content": text[i:i + size]}}]} for i in range(0, len(text), size)]
    out.append({"choices": [{"delta": {}, "finish_reason": "stop"}], "timings": {"predicted_n": 90, "predicted_ms": 2000}})
    return out


def call_chunks(name, args, cid="c1"):
    """A tool call as llama.cpp streams it: the name first, the arguments in pieces."""
    raw = json.dumps(args)
    out = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": cid, "type": "function",
                                                   "function": {"name": name, "arguments": raw[:3]}}]}}]}]
    out += [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": raw[i:i + 4]}}]}}]}
            for i in range(3, len(raw), 4)]
    out.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "timings": {"predicted_n": 20, "predicted_ms": 480}})
    return out


class ScriptedStream:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, url, body, timeout):
        self.bodies.append(body)
        if not self.replies:
            raise AssertionError("LLM called more times than scripted")
        yield from self.replies.pop(0)


def streaming_agent(*replies, ha=None):
    agent, ha = make_agent(lambda *a: (_ for _ in ()).throw(AssertionError("one-shot call used")), ha=ha)
    agent.stream_post = ScriptedStream(*replies)
    return agent, ha


def run(agent, text, session="s1"):
    return list(agent.run([{"role": "user", "content": text}], session))


class TestReleasePoint(unittest.TestCase):
    def test_only_sentences_followed_by_more_text_are_released(self):
        self.assertEqual(_release_point("One. Two. Thr", 0), len("One. Two."))
        self.assertEqual(_release_point("One. Two.", 0), len("One."))   # the last sentence waits for the end
        self.assertEqual(_release_point("It costs 3.50 today", 0), 0)   # a decimal point is not a sentence end
        self.assertEqual(_release_point("A line\nand the next", 0), len("A line"))   # a poem's lines count

    def test_releases_nothing_before_the_start(self):
        self.assertEqual(_release_point("One. Two. Three", len("One. Two.")), len("One. Two."))


class TestStreamedReplies(unittest.TestCase):
    def test_a_long_reply_goes_out_sentence_by_sentence_before_it_ends(self):
        agent, _ = streaming_agent(text_chunks(STORY))
        events = run(agent, "Tell me a story about a lighthouse keeper.")
        partials = [e["content"] for e in events if e["type"] == "partial"]
        final = events[-1]
        self.assertEqual(final["type"], "final")
        self.assertEqual(final["content"], STORY)
        self.assertGreater(len(partials), 1)
        said = "".join(partials)
        self.assertTrue(STORY.startswith(said))
        self.assertGreater(len(said), PROMISE_MAX_CHARS)
        self.assertNotIn("He never asked", said)     # the last sentence is held until the reply ends
        for p in partials[1:]:
            self.assertTrue(p[0].isspace() and p.rstrip()[-1] in ".!?", p)   # whole sentences

    def test_usage_comes_from_the_streamed_timings(self):
        agent, _ = streaming_agent(text_chunks(STORY))
        usage = [e for e in run(agent, "Tell me a story.") if e["type"] == "usage"][0]
        self.assertEqual(usage["completion_tokens"], 90)
        self.assertEqual(usage["tps"], 45.0)

    def test_a_short_reply_is_held_whole(self):
        agent, _ = streaming_agent(text_chunks("It's 21 degrees inside. Perfectly civilised."))
        events = run(agent, "How warm is it?")
        self.assertFalse([e for e in events if e["type"] == "partial"])
        self.assertEqual(events[-1]["content"], "It's 21 degrees inside. Perfectly civilised.")

    def test_a_trailing_offer_is_never_spoken(self):
        agent, _ = streaming_agent(text_chunks(STORY + " Let me know if you want another one."))
        events = run(agent, "Tell me a story.")
        said = "".join(e["content"] for e in events if e["type"] == "partial")
        self.assertNotIn("Let me know", said)
        self.assertEqual(events[-1]["content"], STORY)

    def test_a_short_promise_is_still_nudged_into_a_tool_call(self):
        agent, _ = streaming_agent(text_chunks("Let me check the cameras for Bella."),
                                   call_chunks("presence_now", {"who": "bella"}),
                                   text_chunks("Bella was on the kitchen couch 12 minutes ago."))
        events = run(agent, "Where's Bella?")
        self.assertIn("presence_now", [e.get("name") for e in events if e["type"] == "tool_call"])
        self.assertFalse([e for e in events if e["type"] == "partial"])
        self.assertIn("kitchen couch", events[-1]["content"])

    def test_a_streamed_tool_call_is_put_back_together_and_runs(self):
        # (not an on/off order: those take the reflex path and never reach the model)
        agent, _ = streaming_agent(call_chunks("presence_now", {"who": "bella"}),
                                   text_chunks("Bella was on the kitchen couch 12 minutes ago."))
        events = run(agent, "Where's Bella got to?")
        self.assertEqual([e["arguments"] for e in events if e["type"] == "tool_call"], [{"who": "bella"}])
        self.assertEqual(events[-1]["content"], "Bella was on the kitchen couch 12 minutes ago.")
        # the assistant message sent back to the model carries the call's id and the whole arguments
        sent_back = agent.stream_post.bodies[1]["messages"]
        call = [m for m in sent_back if m.get("tool_calls")][-1]["tool_calls"][0]
        self.assertEqual(call["id"], "c1")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"who": "bella"})

    def test_a_streamed_preamble_before_a_tool_call_stays_in_front_of_the_answer(self):
        preamble = STORY[:STORY.index("She had never")].strip()  # > PROMISE_MAX_CHARS, ends with a full sentence
        self.assertGreater(len(preamble), PROMISE_MAX_CHARS)
        chunks = text_chunks(preamble + " Now, the kitchen.")[:-1] + call_chunks("presence_now", {"who": "bella"})
        agent, _ = streaming_agent(chunks, text_chunks("Bella is on the couch."))
        events = run(agent, "Tell me about Elias, then where's Bella?")
        said = "".join(e["content"] for e in events if e["type"] == "partial")
        self.assertTrue(said and preamble.startswith(said))
        self.assertTrue(events[-1]["content"].startswith(said))
        self.assertTrue(events[-1]["content"].endswith("\n\nBella is on the couch."))


class TestStreamedForClients(unittest.TestCase):
    def test_the_final_event_carries_only_what_was_not_streamed(self):
        evs = list(streamed(iter([{"type": "partial", "content": "One."}, {"type": "partial", "content": " Two."},
                                  {"type": "final", "content": "One. Two. Three."}])))
        self.assertEqual(evs[-1]["content"], " Three.")
        self.assertEqual(evs[-1]["full"], "One. Two. Three.")

    def test_an_answer_that_does_not_continue_the_stream_follows_as_a_new_paragraph(self):
        evs = list(streamed(iter([{"type": "partial", "content": "Once upon a time."},
                                  {"type": "final", "content": "I've gone round in circles on that one."}])))
        self.assertEqual(evs[-1]["content"], "\n\nI've gone round in circles on that one.")

    def test_without_partials_the_final_is_untouched(self):
        ev = {"type": "final", "content": "Done."}
        self.assertEqual(list(streamed(iter([ev]))), [ev])

    def test_sse_deltas_add_up_to_the_whole_answer(self):
        agent, _ = streaming_agent(text_chunks(STORY + " Let me know if you want another."))
        deltas = []
        for chunk in agent.sse([{"role": "user", "content": "Tell me a story."}], "s1"):
            if chunk.startswith("data: {"):
                d = (json.loads(chunk[6:]).get("choices") or [{}])[0].get("delta") or {}
                if d.get("content"):
                    deltas.append(d["content"])
        self.assertGreater(len(deltas), 2)
        self.assertEqual("".join(deltas), STORY)


class TestOneShotFallback(unittest.TestCase):
    def test_an_agent_with_a_custom_post_and_no_stream_post_does_not_stream(self):
        agent = CourageAgent(CourageTools(make_agent(lambda *a: None)[0].tools.deps), "http://fake:8001/v1",
                             presence_fn=lambda: PRESENCE, pending=PendingActions(),
                             post=lambda url, body, timeout: {"choices": [{"message": {"content": "Hello."}}]})
        self.assertIsNone(agent.stream_post)
        self.assertEqual(run(agent, "Hi there")[-1]["content"], "Hello.")


CODE_REPLY = ("Here is a small function that reverses a string, using slicing so it works for any length of input and "
              "needs no loop at all. It is the idiomatic way to do it in Python, and it returns a new string. "
              "Slicing with a step of minus one walks the characters from the end to the start, which is exactly "
              "what reversing means, and it is quick even for long strings because the work happens in C.\n\n"
              "```python\ndef reverse(text):\n    return text[::-1]\n```\n\n"
              "Call it as reverse('stone') to get 'enots'.")


class TestMarkdownBySpeaker(unittest.TestCase):
    """plain_text is for replies that are spoken (Computer). An agent without a home (Atlas) keeps its code fences."""

    def test_an_agent_without_a_home_keeps_the_code_fence_in_partials_and_final(self):
        agent, _ = streaming_agent(text_chunks(CODE_REPLY))
        agent.home_context = False
        events = run(agent, "Show me a Python function that reverses a string.")
        partials = [e["content"] for e in events if e["type"] == "partial"]
        self.assertGreater(len(partials), 0)
        self.assertTrue(CODE_REPLY.startswith("".join(partials)))
        final = events[-1]
        self.assertEqual(final["type"], "final")
        self.assertEqual(final["content"], CODE_REPLY)
        self.assertIn("```python\ndef reverse(text):\n    return text[::-1]\n```", final["content"])

    def test_a_home_agent_still_strips_the_markdown(self):
        # Computer's spoken replies: bold, italics, headings, bullets and inline code lose their markers (no fences here)
        spoken = ("It's currently **8:32 AM** and the *living room* is at 21 degrees. " * 4
                  + "\n## Lights\n- kitchen on\n- hall off\nCheck `ha_call` if you want more.")
        agent, _ = streaming_agent(text_chunks(spoken))
        self.assertTrue(agent.home_context)
        final = run(agent, "What's the house like?")[-1]
        self.assertEqual(final["content"].count("*"), 0)
        self.assertNotIn("#", final["content"])
        self.assertNotIn("`", final["content"])
        self.assertIn("It's currently 8:32 AM", final["content"])
        self.assertIn("Check ha_call if you want more.", final["content"])


if __name__ == "__main__":
    unittest.main()
