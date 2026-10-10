"""Offline tests for Courage's conversational memory: what the extractor's answer becomes, storing vs refreshing a
near-duplicate, recall by meaning (fake embedder: word overlap), forgetting, and the agent's rules (learn from
conversation, not from home tasks; recalled notes reach the prompt and the trace)."""

import json
import math
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stonesage.courage import memory as cm  # noqa: E402
from stonesage.courage.trace import TraceLog  # noqa: E402
from agent_fixture import ScriptedLLM, make_agent, reply, run, tool_call  # noqa: E402

VOCAB = {}


def fake_embed(texts):
    """Bag of words over a growing vocabulary, normalised: similar wording -> high cosine, like a real embedder."""
    out = []
    for t in texts:
        words = re.findall(r"[a-z]+", t.replace(cm.QUERY_PREFIX, "").lower())
        vec = [0.0] * 256
        for w in words:
            vec[VOCAB.setdefault(w, len(VOCAB) % 256)] += 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / n for x in vec])
    return out


def memory(llm_answers=(), path=None):
    answers = list(llm_answers)
    return cm.ConversationMemory(path, fake_embed, lambda prompt: answers.pop(0) if answers else '{"notes": []}',
                                 clock=lambda: 1_790_400_000.0)


class TestNotes(unittest.TestCase):
    def test_parse_extractor_answers(self):
        self.assertEqual(cm.parse_notes('```json\n{"notes": ["The user has a sister called Emma."]}\n```'),
                         ["The user has a sister called Emma."])
        self.assertEqual(cm.parse_notes('{"notes": []}'), [])
        self.assertEqual(cm.parse_notes("Nothing to remember."), [])
        self.assertEqual(len(cm.parse_notes('{"notes": ["one note here", "two note here", "three note here"]}')), 2)

    def test_learn_stores_then_refreshes_a_duplicate(self):
        path = os.path.join(tempfile.mkdtemp(), "mem.json")
        m = memory(['{"notes": ["Emma is visiting next weekend and is allergic to cats."]}',
                    '{"notes": ["Emma is visiting next weekend and is allergic to cats."]}'], path)
        self.assertEqual(m.learn("My sister Emma visits next weekend, she's allergic to cats", "Noted.")["outcome"], "stored")
        self.assertEqual(m.learn("Remember Emma's cat allergy", "I do.")["outcome"], "refreshed")
        again = memory(path=path)                                          # persisted
        self.assertEqual(len(again.items()), 1)
        self.assertEqual(again.items()[0]["times"], 2)
        self.assertNotIn("vec", again.items()[0])                           # the UI never gets vectors

    def test_recall_threshold_limit_and_forget(self):
        m = memory()
        for t in ("Emma is visiting next weekend and is allergic to cats.", "The user is training for a half marathon.",
                  "The user loves Doctor Who.", "Emma likes tea.", "Emma drives a red car."):
            m.add(t)
        hits = m.recall("Is Emma visiting next weekend?")
        self.assertLessEqual(len(hits), cm.RECALL_MAX)
        self.assertIn("visiting", hits[0]["text"])
        self.assertEqual(m.recall("turn off the kitchen light"), [])
        self.assertTrue(m.forget(hits[0]["id"]))
        self.assertNotIn(hits[0]["id"], [n["id"] for n in m.items()])
        card = m.card(hits)
        self.assertTrue(card.startswith("[From earlier conversations"))


class TestAgentMemory(unittest.TestCase):
    def agent(self, llm):
        agent, ha = make_agent(llm)
        agent.memory = memory(['{"notes": ["The user had a rough day at work: a deadline moved up."]}'])
        agent.memory.learn_later = lambda user, text, session: agent.memory.learn(user, text, session)   # run inline
        agent.trace_log = TraceLog(os.path.join(tempfile.mkdtemp(), "trace.jsonl"))
        return agent

    def test_conversation_is_learned_and_recalled_later(self):
        llm = ScriptedLLM(reply("Deadlines. The natural predator of weekends. Tell me about it."),
                          reply("Better than yesterday, I hope."))
        agent = self.agent(llm)
        run(agent, "I had a rough day at work, my deadline moved up a week")
        self.assertEqual(len(agent.memory.items()), 1)
        run(agent, "Another rough day at work, the deadline moved up again", session="s2")   # a new conversation
        msgs = llm.requests[1]["messages"]
        self.assertIn("rough day at work", msgs[1]["content"])   # the memory card rides with the presence card, 2nd
        self.assertNotIn("rough day at work", msgs[0]["content"])  # never in the cached persona prefix
        self.assertTrue(agent.trace_log.recent(1, "courage")[0].get("recalled"))

    def test_commands_are_not_learned_but_a_looked_up_fact_is(self):
        llm = ScriptedLLM(tool_call("ha_call", {"domain": "light", "service": "turn_off", "entity_id": "light.bedroom"}),
                          reply("Done."))
        agent = self.agent(llm)
        run(agent, "Please switch the bedroom lamp off for me, it's too bright")   # a command (ha_call): not learned
        self.assertEqual(agent.memory.items(), [])
        agent.post = ScriptedLLM(tool_call("presence_now", {"who": "bella"}), reply("Bella is on the couch; keep her away."))
        run(agent, "My sister is allergic to cats and visiting next weekend")      # checked the cat: still learned
        self.assertEqual(len(agent.memory.items()), 1)

    def test_a_broken_embedder_never_breaks_a_turn(self):
        llm = ScriptedLLM(reply("Hello."))
        agent = self.agent(llm)
        agent.memory.add("The user likes tea.")
        agent.memory.embed = lambda texts: (_ for _ in ()).throw(OSError("embedder down"))
        self.assertEqual(run(agent, "hello there, how are you")[-1]["content"], "Hello.")


if __name__ == "__main__":
    unittest.main()
