"""Agents on the platform (stonesage/agents.py): Atlas runs through the same loop as Computer with its own soul and a read-only
tool allowlist, and Computer's prompt is byte-for-byte what it was. Offline: scripted model, no Home app."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import agent_fixture  # noqa: E402
import prompt_capture  # noqa: E402
from agent_fixture import ScriptedLLM, reply, run, tool_call  # noqa: E402
from stonesage import agents  # noqa: E402
from stonesage.agents import ATLAS, ATLAS_PERSONA, AgentHub, AgentServices  # noqa: E402
from stonesage.courage import prompt  # noqa: E402
from stonesage.courage.agent import FRESH_FACTS  # noqa: E402
from stonesage.courage.memory import EXTRACT, ConversationMemory  # noqa: E402
from stonesage.soul import Soul, import_soul  # noqa: E402

from harness.legacy_names import LEGACY_AGENT_IDS  # noqa: E402

OLD_ATLAS_ID = next(old for old, new in LEGACY_AGENT_IDS.items() if new == "atlas")
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "computer_prompt_golden.json")
HOME_TOOLS = ["presence_now", "ha_get_states", "camera_look", "camera_scan", "ha_call", "notify", "speak",
              "correct_sighting", "think_harder"]


def embed(texts):
    """Notes and questions about deploys point one way, everything else the other."""
    return [[1.0, 0.0] if "deploy" in t.lower() else [0.0, 1.0] for t in texts]


def make_hub(llm, data_dir, computer=None, **kw):
    made = []

    def memory(path, url, extract):
        mem = ConversationMemory(path, embed, lambda text: '{"notes": []}', extract=extract or EXTRACT)
        made.append(mem)
        return mem

    services = AgentServices(llm_url=lambda role: f"http://fake/{role}/v1", timeout=lambda: 5.0, memory=memory, post=llm, **kw)
    hub = AgentHub(lambda: data_dir, services, computer=computer)
    hub.made_memories = made
    return hub


class TestComputerPromptUnchanged(unittest.TestCase):
    def test_every_request_computer_sends_is_what_it_was(self):
        """The golden was captured from the code before agents existed (messages, tool schemas, every body field)."""
        with open(GOLDEN, encoding="utf-8") as f:
            golden = json.load(f)
        now = json.loads(json.dumps(prompt_capture.capture(), sort_keys=True))
        self.assertEqual(len(now), len(golden))
        for got, want in zip(now, golden):
            self.assertEqual(got, want)

    def test_the_golden_has_the_house_in_it(self):
        with open(GOLDEN, encoding="utf-8") as f:
            golden = json.load(f)
        text = json.dumps(golden)
        self.assertIn("Who is where", text)
        self.assertIn(FRESH_FACTS[:40], text)
        self.assertEqual(len(golden[0]["tools"]), 9)


class TestAtlas(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def atlas(self, *replies, **kw):
        llm = ScriptedLLM(*replies)
        hub = make_hub(llm, self.d, **kw)
        return hub.agent(hub.resolve("atlas")), llm, hub

    def test_the_model_is_offered_memory_search_and_nothing_from_the_house(self):
        agent, llm, _ = self.atlas(reply("Hello."))
        run(agent, "hi")
        names = [t["function"]["name"] for t in llm.requests[0]["tools"]]
        self.assertEqual(names, ["memory_search"])
        for home in HOME_TOOLS:
            self.assertNotIn(home, json.dumps(llm.requests[0]["tools"]))

    def test_persona_comes_from_its_soul_and_nothing_of_the_house_is_injected(self):
        agent, llm, _ = self.atlas(reply("Hello."), reply("Hi again."))
        run(agent, "hi")
        msgs = llm.requests[0]["messages"]
        self.assertEqual(msgs, [{"role": "system", "content": ATLAS_PERSONA}, {"role": "user", "content": "hi"}])
        # mid-conversation: still no presence card and no "facts come from a tool" reminder
        run(agent, "and now?", history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."}])
        blob = json.dumps(llm.requests[1]["messages"])
        for house in ("Who is where", "presence", FRESH_FACTS[:30], "in the attic", "camera"):
            self.assertNotIn(house, blob)
        self.assertEqual(llm.requests[1]["model"], "coordinator")

    def test_persona_is_the_souls_file_and_follows_edits_per_turn(self):
        agent, llm, _ = self.atlas(reply("One."), reply("Two."))
        run(agent, "a")
        with open(os.path.join(self.d, "souls", "atlas", "persona.md"), "w", encoding="utf-8") as f:
            f.write("You are Atlas, edited.")
        run(agent, "b")
        self.assertEqual(llm.requests[0]["messages"][0]["content"], ATLAS_PERSONA)
        self.assertEqual(llm.requests[1]["messages"][0]["content"], "You are Atlas, edited.")

    def test_persona_is_lean_and_has_no_house_or_negative_identity(self):
        self.assertLess(len(ATLAS_PERSONA), 1200)
        low = ATLAS_PERSONA.lower()
        for word in ("house", "home", "camera", "you are not", "never", "don't"):
            self.assertNotIn(word, low)
        self.assertTrue(ATLAS_PERSONA.startswith("You are Atlas"))

    def test_home_tools_are_refused_even_when_the_model_asks(self):
        call = tool_call("ha_call", {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"})
        agent, llm, _ = self.atlas(call, reply("I cannot reach that."))
        for name in HOME_TOOLS:
            err = agent.tools.validate(name, {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen", "camera": "x"})
            self.assertIn("unknown tool", err, name)
        events = run(agent, "turn on the kitchen light")
        self.assertEqual([e["type"] for e in events], ["tool_result", "final"])      # no approval, nothing run
        self.assertIn("unknown tool 'ha_call'", events[0]["result"])
        self.assertEqual(agent.pending.count(), 0)

    def test_memory_search_reads_its_own_memory_file(self):
        agent, llm, _ = self.atlas(tool_call("memory_search", {"query": "when do we deploy"}), reply("Fridays."))
        path = os.path.join(self.d, "souls", "atlas", "memories.json")
        agent.memory.add("Your team deploys on Fridays.")
        events = run(agent, "when do we deploy")
        self.assertEqual([e["type"] for e in events[:2]], ["tool_call", "tool_result"])
        self.assertIn("deploys on Fridays", events[1]["result"])
        self.assertTrue(json.load(open(path, encoding="utf-8"))["notes"])
        # a different agent's notes are a different file: Computer's memories.json is never touched
        self.assertFalse(os.path.exists(os.path.join(self.d, "souls", "courage-computer")))

    def test_recalled_notes_go_in_a_second_system_message(self):
        agent, llm, _ = self.atlas(reply("Fridays."))
        agent.memory.add("Your team deploys on Fridays.")
        run(agent, "when do we deploy")
        msgs = llm.requests[0]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["system", "system", "user"])
        self.assertIn("deploys on Fridays", msgs[1]["content"])
        self.assertNotIn("Who is where", msgs[1]["content"])

    def test_a_promise_is_not_nudged_when_there_is_nothing_to_call(self):
        # a soul whose manifest allows no tools: "Let me check" is just an answer, not a described tool call
        Soul.open(self.d, "atlas", display_name="Atlas", default_persona=ATLAS_PERSONA)
        path = os.path.join(self.d, "souls", "atlas", "manifest.json")
        manifest = json.load(open(path, encoding="utf-8"))
        manifest["tools"] = []
        json.dump(manifest, open(path, "w", encoding="utf-8"))
        agent, llm, _ = self.atlas(reply("Let me check that for you."))
        events = run(agent, "review this")
        self.assertEqual(len(llm.requests), 1)
        self.assertNotIn("tools", llm.requests[0])
        self.assertEqual(events[-1]["content"], "Let me check that for you.")

    def test_manifest_tools_can_narrow_but_never_widen(self):
        self.assertEqual(agents.effective_tools(ATLAS, {"tools": ["memory_search", "ha_call"]}), ("memory_search",))
        self.assertEqual(agents.effective_tools(ATLAS, {"tools": []}), ())
        self.assertEqual(agents.effective_tools(ATLAS, {}), ("memory_search",))
        self.assertEqual(agents.effective_tools(ATLAS, {"tools": "ha_call"}), ("memory_search",))
        self.assertIsNone(agents.effective_tools(agents.COMPUTER, {}))
        self.assertEqual(agents.effective_tools(agents.COMPUTER, {"tools": ["presence_now"]}), ("presence_now",))

    def test_brain_comes_from_the_manifest_when_it_names_a_chat_role(self):
        self.assertEqual(agents.effective_brain(ATLAS, {"brain": "worker"}), "worker")
        for bad in ("embedder", "vision", "../x", 3, None):
            self.assertEqual(agents.effective_brain(ATLAS, {"brain": bad}), "coordinator")
        Soul.open(self.d, "atlas", display_name="Atlas", default_persona=ATLAS_PERSONA)
        path = os.path.join(self.d, "souls", "atlas", "manifest.json")
        manifest = json.load(open(path, encoding="utf-8"))
        manifest["brain"] = "worker"
        json.dump(manifest, open(path, "w", encoding="utf-8"))
        agent, llm, _ = self.atlas(reply("ok"))
        run(agent, "hi")
        self.assertEqual(llm.requests[0]["model"], "worker")
        self.assertIn("/worker/", agent.url)

    def test_the_spare_brain_note_follows_the_engine_lease_for_its_role(self):
        asked = []
        agent, llm, _ = self.atlas(reply("ok"), lease_brain=lambda role: asked.append(role))
        run(agent, "hi")
        self.assertEqual(asked, ["coordinator"])


class TestHub(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_registry_lists_atlas_and_computer_only_when_an_app_gives_a_house(self):
        hub = make_hub(ScriptedLLM(), self.d)
        self.assertEqual([s.id for s in hub.specs()], ["atlas"])
        hub = make_hub(ScriptedLLM(), self.d, computer=lambda: object())
        self.assertEqual([s.id for s in hub.specs()], ["courage-computer", "atlas"])

    def test_names_and_old_names_resolve(self):
        hub = make_hub(ScriptedLLM(), self.d, computer=lambda: object())
        for name, want in (("computer", "courage-computer"), ("Courage-Computer", "courage-computer"), ("courage", "courage-computer"),
                           ("atlas", "atlas"), ("ATLAS", "atlas"), (OLD_ATLAS_ID, "atlas")):
            self.assertEqual(hub.resolve(name).id, want, name)
        for name in ("coder-agent", "nobody", "", None, 5):
            self.assertIsNone(hub.resolve(name), name)

    def test_chat_agent_serves_atlas_by_id_only(self):
        hub = make_hub(ScriptedLLM(), self.d, computer=lambda: object())
        self.assertIsNotNone(hub.chat_agent("atlas"))
        self.assertIsNone(hub.chat_agent("courage-computer"))     # Computer keeps its own path (approvals, phone)
        self.assertIsNone(hub.chat_agent("coder-agent"))
        self.assertIsNone(hub.chat_agent("coordinator"))

    def test_an_imported_soul_is_an_agent_with_only_safe_tools(self):
        src = tempfile.mkdtemp()
        s = Soul.open(src, "tutor", display_name="Tutor", default_persona="You are Tutor, a patient maths tutor.")
        s.manifest.update(tools=["memory_search", "ha_call", "notify"], brain="worker")
        s._save_manifest()
        out = os.path.join(src, "tutor.tar.gz")
        s.export(out)
        import_soul(self.d, out)
        llm = ScriptedLLM(reply("Two."))
        hub = make_hub(llm, self.d)
        spec = hub.resolve("tutor")
        self.assertEqual((spec.id, spec.name, spec.tools, spec.brain), ("tutor", "Tutor", ("memory_search",), "worker"))
        run(hub.agent(spec), "1+1?")
        self.assertEqual(llm.requests[0]["messages"][0]["content"], "You are Tutor, a patient maths tutor.")
        self.assertEqual([t["function"]["name"] for t in llm.requests[0]["tools"]], ["memory_search"])
        self.assertEqual([x.id for x in hub.specs()], ["atlas", "tutor"])

    def test_reload_drops_the_loop_so_the_new_soul_is_read(self):
        hub = make_hub(ScriptedLLM(), self.d)
        first = hub.agent(hub.resolve("atlas"))
        self.assertTrue(hub.is_running("atlas"))
        self.assertIs(hub.agent(hub.resolve("atlas")), first)
        hub.reload("atlas")
        self.assertFalse(hub.is_running("atlas"))
        self.assertIsNot(hub.agent(hub.resolve("atlas")), first)

    def test_reloading_computer_rereads_its_notes_and_keeps_the_loop(self):
        class Mem:
            reloaded = 0

            def reload(self):
                self.reloaded += 1

        class Loop:
            memory = Mem()

        loop = Loop()
        hub = AgentHub(lambda: self.d, AgentServices(lambda r: "", lambda: 1.0, lambda *a: None),
                       computer=lambda: loop, computer_built=lambda: loop)
        self.assertTrue(hub.is_running("courage-computer"))
        hub.reload("courage-computer")
        self.assertEqual(loop.memory.reloaded, 1)


class TestMemoryReload(unittest.TestCase):
    def test_reload_reads_the_file_again(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "m.json")
        mem = ConversationMemory(path, embed, lambda p: "")
        self.assertEqual(mem.notes, [])
        json.dump({"notes": [{"id": "a", "text": "x", "at": 1.0, "seen": 1.0, "vec": [1, 0]}]}, open(path, "w"))
        mem.reload()
        self.assertEqual(len(mem.notes), 1)
        open(path, "w").write("not json")
        mem.reload()                                    # a broken file keeps what is already loaded
        self.assertEqual(len(mem.notes), 1)


if __name__ == "__main__":
    unittest.main()
