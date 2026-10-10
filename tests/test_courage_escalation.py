"""Offline tests for the escalation ladder (courage/escalation.py, Phase 6): which turns count as failed, what each
rung gets and does, the human rung's policy and limits, the repeated-tool-failure alert, and the loop's hook."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stonesage.courage import escalation as es  # noqa: E402
from agent_fixture import ScriptedLLM, make_agent, tool_call, reply  # noqa: E402

MESSAGES = [
    {"role": "system", "content": "persona + presence card"},
    {"role": "user", "content": "an earlier question"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "old_tool", "arguments": "{}"}}]},
    {"role": "tool", "content": "old result"},
    {"role": "user", "content": "Where is Rex?"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "presence_now", "arguments": '{"who": "rex"}'}}]},
    {"role": "tool", "content": '{"ok": false, "error": "camera timeout"}'},
]
STEPS = [{"tool": "presence_now", "args": '{"who": "rex"}', "ok": False, "error": "camera timeout"}]


class Rig:
    def __init__(self, boost_answer=None, boost_on=False, **cfg):
        self.now = 1_800_000_000.0
        self.pushes, self.watch, self.traces, self.boost_msgs = [], [], [], []
        self.cfg = {"courage": {"escalation": cfg}}
        self.boost_answer = boost_answer
        self.e = es.Escalation(lambda: self.cfg, boost=self._boost, boost_available=lambda: boost_on,
                               push=lambda t, m: (self.pushes.append((t, m)), {"ok": True})[1],
                               watch=self.watch.append, trace=self.traces.append, clock=lambda: self.now)

    def _boost(self, msgs):
        self.boost_msgs.append(msgs)
        return {"ok": True, "answer": self.boost_answer, "source": "Groq (gpt-oss-120b)"} if self.boost_answer \
            else {"ok": False, "error": "all sources failed"}

    def fail(self, session="ha:kitchen", question="Where is Rex?", reason="step_cap"):
        return self.e.handle(session, question, "I've gone round in circles on that one.", reason, STEPS, MESSAGES, self.now)


class TestFailed(unittest.TestCase):
    def test_only_objective_failures(self):
        self.assertEqual(es.failed("step_cap", []), "step_cap")
        self.assertEqual(es.failed("llm_error", ["llm_error"]), "llm_error")
        self.assertEqual(es.failed(None, ["empty_answer"]), "empty_answer")
        self.assertIsNone(es.failed("answered", ["tool_error"]))      # told the user the tool failed: a right answer
        self.assertIsNone(es.failed("asked_approval", []))

    def test_attempt_history_is_this_turn_only(self):
        h = es.attempt_history(MESSAGES)
        self.assertIn('called presence_now({"who": "rex"})', h)
        self.assertIn("camera timeout", h)
        self.assertNotIn("old_tool", h)
        self.assertNotIn("persona", h)


class TestLadder(unittest.TestCase):
    def test_boost_answers_and_nobody_is_bothered(self):
        r = Rig(boost_answer="Rex's last sighting isn't available: the kitchen camera timed out.", boost_on=True)
        out = r.fail()
        self.assertTrue(out["content"].startswith("Rex's last sighting"))
        self.assertIn("bigger brain", out["content"])
        self.assertEqual((out["record"]["answered_by"], r.pushes), ("boost", []))
        user = r.boost_msgs[0][1]["content"]
        self.assertIn("Where is Rex?", user)
        self.assertIn("camera timeout", user)
        self.assertEqual(out["record"]["local"], "I've gone round in circles on that one.")   # the verdict pair

    def test_boost_off_goes_to_the_human_on_a_voice_turn(self):
        r = Rig()
        out = r.fail()
        self.assertEqual([x["rung"] for x in out["record"]["rungs"]], ["boost", "frontier", "human"])
        self.assertEqual(out["record"]["rungs"][0]["outcome"], "unavailable")
        self.assertEqual(out["record"]["rungs"][1]["outcome"], "unavailable")
        self.assertEqual(r.pushes[0][0], "Computer got stuck")
        self.assertIn("presence_now failed: camera timeout", r.pushes[0][1])
        self.assertEqual(len(r.watch), 1)
        self.assertTrue(out["content"].endswith("I've sent the details to the phone."))
        self.assertEqual(r.traces[-1]["kind"], "escalation")

    def test_a_failing_boost_still_reaches_the_human(self):
        r = Rig(boost_on=True)
        out = r.fail()
        self.assertEqual(out["record"]["rungs"][0]["outcome"], "failed")
        self.assertEqual(len(r.pushes), 1)

    def test_web_chat_is_not_pushed_under_the_voice_policy(self):
        r = Rig()
        out = r.fail(session="web-123")
        self.assertEqual(r.pushes, [])
        self.assertEqual(out["record"]["rungs"][-1]["why"], "not a voice turn (policy voice)")
        self.assertEqual(out["content"], "I've gone round in circles on that one.")
        self.assertEqual(len(Rig(human="always").fail(session="web-1")["record"]["rungs"]), 3)

    def test_rate_limit_daily_limit_and_same_question(self):
        r = Rig(human_every_min=30, human_daily=2)
        r.fail(question="Where is Rex?")
        r.now += 60
        self.assertEqual(r.fail(question="Is the stove on?")["record"]["rungs"][-1]["why"], "rate limit")
        r.now += 3600
        self.assertEqual(r.fail(question="Where is Rex?")["record"]["rungs"][-1]["why"],
                         "already told about this question today")
        r.fail(question="Is the stove on?")
        r.now += 3600
        self.assertEqual(r.fail(question="What's on the driveway?")["record"]["rungs"][-1]["why"], "daily limit")
        self.assertEqual(len(r.pushes), 2)

    def test_never(self):
        r = Rig(human="never")
        r.fail()
        self.assertEqual(r.pushes, [])


class TestToolAlert(unittest.TestCase):
    def test_a_tool_failing_three_times_in_an_hour_is_told_once(self):
        r = Rig()
        for _ in range(2):
            self.assertIsNone(r.e.tool_errors(STEPS))
            r.now += 600
        rec = r.e.tool_errors(STEPS)
        self.assertEqual((rec["reason"], rec["tool"], rec["errors_1h"]), ("tool_failing", "presence_now", 3))
        self.assertIn("presence_now failed 3 times", r.pushes[0][1])
        r.now += 600
        self.assertIsNone(r.e.tool_errors(STEPS))                  # once per 6 h
        r.now += 7 * 3600
        for _ in range(3):
            r.e.tool_errors(STEPS)
        self.assertEqual(len(r.pushes), 2)

    def test_refused_arguments_are_not_a_broken_tool(self):
        r = Rig()
        for _ in range(5):
            r.e.tool_errors([{"tool": "ha_call", "ok": False, "error": "bad entity", "refused": True}])
        self.assertEqual(r.pushes, [])


class TestLoopHook(unittest.TestCase):
    def test_step_cap_climbs_the_ladder(self):
        llm = ScriptedLLM(*[tool_call("ha_get_states", {"domain": "light"}, f"c{i}") for i in range(5)])
        agent, _ = make_agent(llm)
        r = Rig(boost_answer="The kitchen light is on and the bedroom lamp is off.", boost_on=True)
        agent.escalation = r.e
        events = list(agent.run([{"role": "user", "content": "Which lights are on?"}], "web-9"))
        final = [e for e in events if e["type"] == "final"][-1]["content"]
        self.assertTrue(final.startswith("The kitchen light is on"))
        self.assertIn("Kitchen Light", r.boost_msgs[0][1]["content"])      # tool results reached the higher rung
        self.assertEqual(r.traces[-1]["reason"], "step_cap")

    def test_a_good_answer_never_escalates(self):
        agent, _ = make_agent(ScriptedLLM(reply("Rex is asleep on the rug.")))
        r = Rig(boost_on=True, boost_answer="x")
        agent.escalation = r.e
        list(agent.run([{"role": "user", "content": "Where is Rex?"}], "ha:1"))
        self.assertEqual((r.boost_msgs, r.pushes, r.traces), ([], [], []))


if __name__ == "__main__":
    unittest.main()
