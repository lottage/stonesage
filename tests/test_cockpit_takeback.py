"""Computer takes the GPU back from background work (2026-10-13), platform side: the cockpit activity signal
(stonesage/activity.py), the takeback (stonesage/courage/takeback.py) and Computer's loop answering again on the full brain.
No LAN: fake lease / engines / clock. The garden scheduler's side is tests/test_grove_takeback.py; the idle gate's and the
engine lease's are in tests/test_idle_gate.py and tests/test_engine_lease.py."""

import json
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stonesage import activity, auth  # noqa: E402
from stonesage.courage.agent import CANNED_LINES, streamed  # noqa: E402
from stonesage.courage.takeback import FILLER, STILL_COMING_BACK, Takeback  # noqa: E402
from lodestone import engine_lease as el  # noqa: E402
from agent_fixture import ScriptedLLM, make_agent, reply, tool_call  # noqa: E402

LITE_URL = "http://vm:8006/v1/chat/completions"
FULL_URL = "http://fake:8001/v1/chat/completions"


class Clock:
    """A clock that moves when something sleeps on it, and also lets real threads run a little."""

    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        time.sleep(0.003)
        self.t += s


# ---- the cockpit signal ------------------------------------------------------------------------------------------------------
class FakeHandler:
    def __init__(self, path="/api/x", cookie="ss_session=abc", bearer="", principal=auth.OWNER):
        self.path, self.principal = path, principal
        self.headers = {k: v for k, v in (("Cookie", cookie), ("Authorization", bearer)) if v}


class TestCockpitActivity(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cfg = {}
        self.act = activity.CockpitActivity(lambda: self.cfg, self.clock)

    def test_a_heartbeat_counts_and_the_window_expires(self):
        self.assertIsNone(self.act.reason())
        self.act.beat("heartbeat")
        self.assertTrue(self.act.active())
        self.clock.t += 30
        self.assertEqual(self.act.reason(), "someone is using StoneSage (30 s ago)")
        self.clock.t += 569
        self.assertTrue(self.act.active())               # 599 s: inside the default 10 min
        self.clock.t += 2
        self.assertFalse(self.act.active())              # 601 s: over
        self.assertIsNone(self.act.reason())

    def test_the_window_is_configurable(self):
        self.cfg["cockpit_activity"] = {"window_s": 60}
        self.act.beat()
        self.clock.t += 59
        self.assertTrue(self.act.active())
        self.clock.t += 2
        self.assertFalse(self.act.active())

    def test_polling_does_not_count_and_a_signed_in_post_does(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertFalse(self.act.observe(FakeHandler("/api/health/all"), method), method)
        self.assertFalse(self.act.active())
        self.assertTrue(self.act.observe(FakeHandler("/api/cluster/chat"), "POST"))
        self.assertTrue(self.act.active())

    def test_a_bearer_key_or_an_unsigned_request_is_not_a_person_at_the_page(self):
        self.assertFalse(self.act.observe(FakeHandler("/v1/chat/completions", cookie="", bearer="Bearer k"), "POST"))
        self.assertFalse(self.act.observe(FakeHandler("/api/ollama", cookie="ss_session=abc", bearer="Bearer k"), "POST"))
        self.assertFalse(self.act.observe(FakeHandler("/api/x", principal="trusted:1.2.3.4"), "POST"))
        self.assertFalse(self.act.observe(FakeHandler("/api/x", principal=None), "POST"))
        self.assertFalse(self.act.active())

    def test_ignored_paths_never_count(self):
        for path in ("/api/auth/login", "/api/cameras/webrtc-report", "/api/activity/status"):
            self.assertFalse(self.act.observe(FakeHandler(path), "POST"), path)
        self.cfg["cockpit_activity"] = {"ignore": ["/api/cluster/"]}
        self.assertFalse(self.act.observe(FakeHandler("/api/cluster/chat"), "POST"))
        self.assertTrue(self.act.observe(FakeHandler("/api/auth/login"), "POST"))      # the config replaced the list

    def test_it_can_be_switched_off(self):
        self.cfg["cockpit_activity"] = {"enabled": False}
        self.act.beat()
        self.assertFalse(self.act.active())
        self.assertFalse(self.act.observe(FakeHandler("/api/cluster/chat"), "POST"))

    def test_listeners_hear_every_beat_and_a_broken_one_is_skipped(self):
        heard = []
        self.act.listeners += [lambda s: 1 / 0, heard.append]
        self.act.beat("heartbeat")
        self.assertEqual(heard, ["heartbeat"])

    def test_the_heartbeat_route(self):
        class Ctx:
            config = {}

            class routes:
                table = {}

                @classmethod
                def add(cls, method, path, fn, **kw):
                    cls.table[(method, path)] = fn
        activity.install(Ctx, self.act)
        sent = []

        class H(FakeHandler):
            command = "POST"

            def send_response(self, c): sent.append(c)
            def send_header(self, *a): pass
            def end_headers(self): pass

            class wfile:
                @staticmethod
                def write(b): sent.append(json.loads(b))
        Ctx.routes.table[("POST", "/api/activity/heartbeat")](H(), None, {})
        self.assertEqual(sent[0], 200)
        self.assertTrue(sent[1]["active"])
        self.assertTrue(self.act.active())


# ---- the takeback ------------------------------------------------------------------------------------------------------------
class FakeLeaseSvc:
    """What Takeback needs of the lease, and what Computer's loop needs of brain(): a held lease that comes back when released."""

    def __init__(self, clock, load_s=30.0, mode="lite", yield_flag=True, roles=("coordinator", "worker")):
        self.clock, self.load_s, self.mode = clock, load_s, mode
        self.lease = {"id": "L1", "layout": "longctx_6750", "state": "active", "yield_to_computer": yield_flag,
                      "evicted_roles": list(roles), "expires": clock() + 3000}
        self.released, self.released_at, self.release_delay = [], None, 0.0
        self.coordinator_down = False

    def _settle(self):
        """The revert's tail: the lease record goes (the spare brain stops) a few seconds after the coordinator answers."""
        if self.lease and self.released_at is not None and not self.coordinator_down                 and self.clock() >= self.released_at + self.load_s + 5:
            self.lease = None

    def yielding(self, role="coordinator"):
        self._settle()
        l = self.lease
        return dict(l) if l and l.get("yield_to_computer") and l.get("state") in ("starting", "active")             and role in l["evicted_roles"] else None

    def brain(self, role="coordinator"):
        self._settle()
        if not self.lease or role not in self.lease["evicted_roles"]:
            return None
        if self.mode == "lite":
            return {"mode": "lite", "url": "http://vm:8006", "until": self.lease["expires"], "yield_to_computer": True}
        return {"mode": "on_loan", "until": self.lease["expires"]}

    def release(self, lease_id=None, why="released", approver=""):
        self.released.append((lease_id, why, approver))
        self.released_at = self.clock()
        time.sleep(self.release_delay)
        return {"ok": not self.coordinator_down}

    def ready(self):
        return (not self.coordinator_down and self.released_at is not None and self.clock() >= self.released_at + self.load_s)


class TakebackRig(unittest.TestCase):
    def build(self, lite=(), full=(), mode="lite", cfg=None, load_s=30.0, yield_flag=True):
        self.clock = Clock()
        self.svc = FakeLeaseSvc(self.clock, load_s, mode, yield_flag)
        self.traces, self.pushes, self.yielded = [], [], []
        self.config = {"courage": {"takeback": dict(cfg or {})}}
        self.tb = Takeback(lambda: self.config, lambda: self.svc, self.svc.ready, trace=self.traces.append,
                           push=lambda t, m: self.pushes.append((t, m)), clock=self.clock, sleep=self.clock.sleep)
        self.tb.register_yielder(lambda why, cooldown: self.yielded.append((why, cooldown)) or {"ok": True})
        self.lite_llm, self.full_llm = ScriptedLLM(*lite), ScriptedLLM(*full)
        self.urls = []

        def post(url, body, timeout):
            self.urls.append(url)
            llm = self.lite_llm if url == LITE_URL else self.full_llm
            r = llm(url, body, timeout)
            if isinstance(r, Exception):
                raise r
            return r

        self.looks = []
        self.agent, self.ha = make_agent(post, looks=self.looks)
        self.agent.brain = self.svc.brain
        self.agent.takeback = self.tb
        self.escalated = []

        class Esc:
            def tool_errors(_s, *a, **k):
                pass

            def handle(_s, *a, **k):
                self.escalated.append(a)
                return {"content": "escalated", "record": {"outcome": "x"}}
        self.agent.escalation = Esc()
        return self

    def ask(self, text="what is on the driveway camera?", agent=None, stream=False):
        events = list((agent or self.agent).run([{"role": "user", "content": text}], "web-1"))
        return events

    def types(self, events):
        return [e["type"] for e in events]

    def trace(self):
        return [t for t in self.traces if t.get("kind") == "takeback"]

    def agent_traces(self):
        return self.agent.trace_log.recent(10) if self.agent.trace_log else []


class TestTakebackTriggers(TakebackRig):
    # (a) the spare brain fails on the objective triggers
    def test_a_step_cap_on_the_spare_brain_is_taken_back_and_rerun_on_the_coordinator(self):
        self.build(lite=[tool_call("presence_now", {}, "c%d" % i) for i in range(5)], full=[reply("Rex is by the door.")])
        events = self.ask("Where is Rex?")
        self.assertEqual(self.types(events)[0:1], ["tool_call"])               # what the spare brain did stays visible
        i = self.types(events).index("holding")
        self.assertEqual(events[i]["content"], FILLER)
        self.assertEqual(events[-1], {"type": "final", "content": "Rex is by the door."})
        self.assertNotIn("circles", events[-1]["content"])                     # the spare brain's failure text is not shown
        self.assertEqual(self.urls[-1], FULL_URL)
        self.assertEqual(set(self.urls[:-1]), {LITE_URL})
        self.assertEqual(self.svc.released[0][1:], ("takeback: step_cap", "takeback"))
        self.assertEqual(self.yielded, [("takeback: step_cap", 3600.0)])
        self.assertEqual(self.escalated, [])                                    # the lite attempt did not climb the ladder
        t = self.trace()[0]
        self.assertEqual((t["why"], t["outcome"], t["source"]), ("step_cap", "ok", "request"))
        self.assertAlmostEqual(t["coordinator_ms"], 30000, delta=2500)
        self.assertGreaterEqual(t["user_wait_ms"], t["coordinator_ms"])

    def test_the_stand_in_not_answering_is_taken_back(self):
        self.build(lite=[OSError("connection refused")], full=[reply("It is 9:41.")])
        events = self.ask("What time is it?")
        self.assertEqual(events[-1]["content"], "It is 9:41.")
        self.assertEqual(self.trace()[0]["why"], "llm_error")
        self.assertEqual(self.escalated, [])

    def test_an_empty_answer_is_taken_back(self):
        self.build(lite=[reply("")], full=[reply("Nothing to report.")])
        events = self.ask("anything new?")
        self.assertEqual(events[-1]["content"], "Nothing to report.")
        self.assertEqual(self.trace()[0]["why"], "empty_answer")

    def test_nothing_answering_at_all_on_a_garden_lease_is_taken_back(self):
        self.build(full=[reply("Back with you.")], mode="on_loan")
        events = self.ask("hello there")
        self.assertEqual(events[-1]["content"], "Back with you.")
        self.assertEqual(self.trace()[0]["why"], "on_loan")
        self.assertEqual(self.urls, [FULL_URL])

    def test_a_tool_error_the_spare_brain_reported_honestly_is_not_a_failure(self):
        self.build(lite=[tool_call("ha_get_states", {"domain": "climate"}), reply("Home Assistant would not say.")])
        self.agent.tools.execute = lambda name, args: json.dumps({"ok": False, "error": "HA timed out"})
        events = self.ask("what is the thermostat at?")
        self.assertEqual(events[-1]["content"], "Home Assistant would not say.")
        self.assertEqual(self.svc.released, [])
        self.assertEqual(self.trace(), [])
        self.assertNotIn("holding", self.types(events))

    def test_a_plain_answer_on_the_spare_brain_stays_there(self):
        self.build(lite=[reply("Hello.")])
        events = self.ask("hi")
        self.assertEqual(events[-1]["content"], "Hello.")
        self.assertEqual((self.svc.released, self.trace(), self.urls), ([], [], [LITE_URL]))

    # (b) the request needs the full Computer: decided before the tool runs
    def test_think_harder_is_taken_back_before_it_runs(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("Rayleigh scattering.")])
        events = self.ask("why is the sky blue, really?")
        self.assertEqual(len(self.lite_llm.requests), 1)
        self.assertEqual(self.urls, [LITE_URL, FULL_URL])
        self.assertEqual(events[-1]["content"], "Rayleigh scattering.")
        t = self.trace()[0]
        self.assertEqual((t["why"], t["tool"], t["card"]), ("tool", "think_harder", "coordinator"))
        self.assertEqual(self.yielded[0][0], "takeback: tool")
        self.assertEqual(self.svc.released[0][1], "takeback: tool")

    def test_a_camera_look_on_the_spare_brain_does_not_take_the_coordinator_back(self):
        # The owner, 2026-10-14: vision lives on the other card; a camera question while the garden holds the 6750 leaves it alone
        self.build(lite=[tool_call("camera_look", {"camera": "living_room"}), reply("A cat on the couch.")])
        events = self.ask("what is on the living room camera?")
        self.assertEqual(self.looks, ["camera.living_room_hd_stream"])         # the look ran, on the spare brain's turn
        self.assertEqual((self.svc.released, self.yielded, self.trace()), ([], [], []))
        self.assertNotIn("holding", self.types(events))
        self.assertEqual(set(self.urls), {LITE_URL})

    def test_the_default_coordinator_tool_and_a_configured_list(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")])
        self.ask("do the thing")
        self.assertEqual(len(self.svc.released), 1)
        # a configured list replaces the default: presence_now now needs the full brain, think_harder does not
        self.build(lite=[tool_call("presence_now", {})], full=[reply("ok")], cfg={"tools": ["presence_now"]})
        self.ask("where is everyone")
        self.assertEqual(len(self.svc.released), 1)
        self.build(lite=[tool_call("think_harder", {"question": "x"}), reply("a cat")], cfg={"tools": ["presence_now"]})
        self.ask("look")
        self.assertEqual(self.svc.released, [])

    def test_a_tool_after_a_harmless_one_in_the_same_message_is_still_caught_before_either_runs(self):
        two = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "presence_now", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "camera_look", "arguments": '{"camera": "living_room"}'}}]}}]}
        self.build(lite=[two], full=[reply("done")])
        self.ask("where is everyone and show me the room")
        self.assertEqual(len(self.svc.released), 1)
        self.assertFalse(any(isinstance(s, dict) and s.get("tool") for s in self.agent_traces()[:0]))   # nothing executed on the lite pass

    # not a garden lease, not enabled
    def test_a_lease_that_is_not_background_work_is_never_taken_back(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"}), reply("Looking is on hold.")], yield_flag=False)
        events = self.ask("what is on the camera?")
        self.assertEqual(self.svc.released, [])
        self.assertNotIn("holding", self.types(events))

    def test_a_failed_turn_on_a_lease_that_is_not_background_work_climbs_the_ladder_as_before(self):
        self.build(lite=[tool_call("presence_now", {}, "c%d" % i) for i in range(5)], yield_flag=False)
        events = self.ask("Where is Rex?")
        self.assertEqual(events[-1]["content"], "escalated")
        self.assertEqual(len(self.escalated), 1)

    def test_switched_off_means_the_old_behaviour(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"}), reply("no")], cfg={"enabled": False})
        self.ask("look")
        self.assertEqual(self.svc.released, [])

    def test_no_lease_at_all_is_a_plain_turn(self):
        self.build(full=[reply("Hi.")])
        self.svc.lease = None
        self.agent.brain = lambda: None
        events = self.ask("hi")
        self.assertEqual(events[-1]["content"], "Hi.")
        self.assertEqual(self.urls, [FULL_URL])

    # (c) the cockpit
    def test_a_person_at_the_cockpit_gives_the_gpu_back_with_no_request_waiting(self):
        self.build()
        self.tb.on_cockpit_activity("heartbeat")
        for _ in range(500):
            if self.trace():
                break
            time.sleep(0.01)
        self.assertEqual(self.svc.released[0][1], "takeback: cockpit")
        self.assertEqual(self.yielded[0][0], "takeback: cockpit")
        t = self.trace()[0]
        self.assertEqual((t["why"], t["source"], t["outcome"], t["user_wait_ms"]), ("cockpit", "cockpit", "ok", None))

    def test_the_cockpit_does_nothing_without_a_background_lease(self):
        for how in ("not background work", "no lease", "switched off"):
            if how == "not background work":
                self.build(yield_flag=False)
            elif how == "no lease":
                self.build()
                self.svc.lease = None
            else:
                self.build(cfg={"enabled": False})
            self.tb.on_cockpit_activity()
            time.sleep(0.1)
            self.assertEqual((self.svc.released, self.yielded, self.trace()), ([], [], []), how)

    def test_many_beats_make_one_takeback(self):
        self.build()
        self.svc.release_delay = 0.05
        for _ in range(20):
            self.tb.on_cockpit_activity()
        for _ in range(500):
            if self.trace():
                break
            time.sleep(0.01)
        time.sleep(0.1)
        self.assertEqual(len(self.svc.released), 1)
        self.assertEqual(len(self.trace()), 1)


class TestTakebackFlow(TakebackRig):
    def test_streams_get_one_moment_first_and_the_answer_follows_as_a_new_paragraph(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("Nothing moving.")])
        out = list(streamed(self.agent.run([{"role": "user", "content": "look at the room"}], "web-1"), holding=True))
        parts = [e for e in out if e["type"] in ("partial", "final")]
        self.assertEqual(parts[0], {"type": "partial", "content": FILLER})
        self.assertEqual(parts[-1]["content"], "\n\nNothing moving.")
        self.assertEqual(parts[-1]["full"], "Nothing moving.")

    def test_one_shot_callers_never_see_the_filler(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("Nothing moving.")])
        out = list(streamed(self.agent.run([{"role": "user", "content": "look at the room"}], "alexa:1")))
        self.assertEqual("".join(e["content"] for e in out if e["type"] in ("partial", "final")), "Nothing moving.")
        self.assertNotIn("holding", [e["type"] for e in out])

    def test_the_web_chat_sse_carries_it(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("Nothing moving.")])
        chunks = list(self.agent.sse([{"role": "user", "content": "look at the room"}], "web-1"))
        text = "".join(json.loads(c[6:])["choices"][0]["delta"].get("content", "") for c in chunks
                       if c.startswith("data: {") and json.loads(c[6:]).get("choices"))
        self.assertTrue(text.startswith(FILLER), text)
        self.assertTrue(text.endswith("Nothing moving."))

    def test_the_filler_is_a_line_whose_audio_is_already_cached(self):
        self.assertIn(FILLER, CANNED_LINES)

    def test_the_coordinator_not_back_in_time_gives_the_spare_brain_s_answer_and_tells_the_phone(self):
        self.build(lite=[tool_call("presence_now", {}, "c%d" % i) for i in range(5)], full=[reply("never asked")], load_s=500,
                   cfg={"wait_s": 90})
        events = self.ask("Where is Rex?")
        self.assertIn("circles", events[-1]["content"])                       # the spare brain's own, honest failure text
        self.assertEqual(self.full_llm.requests, [])
        t = self.trace()[0]
        self.assertEqual((t["outcome"], t["triggers"], t["coordinator_ms"]), ("takeback_failed", ["takeback_failed"], None))
        self.assertAlmostEqual(t["waited_ms"], 90000, delta=1500)              # the bound held
        self.assertEqual(len(self.pushes), 1)
        self.assertIn("slow to come back", self.pushes[0][0])
        self.assertGreater(self.tb.cooldown_left(), 0)

    def test_when_the_spare_brain_never_answered_the_user_gets_the_honest_message(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("never asked")], load_s=500)
        events = self.ask("look")
        self.assertEqual(events[-1], {"type": "final", "content": STILL_COMING_BACK})
        self.assertEqual(self.trace()[0]["outcome"], "takeback_failed")

    def test_the_wait_is_configurable(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], load_s=500, cfg={"wait_s": 10})
        self.ask("look")
        self.assertAlmostEqual(self.trace()[0]["waited_ms"], 10000, delta=1500)

    def test_a_yielder_that_raises_does_not_keep_the_gpu(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")])
        self.tb.yielders.insert(0, lambda why, cd: 1 / 0)
        events = self.ask("look")
        self.assertEqual(events[-1]["content"], "ok")
        self.assertEqual(len(self.svc.released), 1)

    def test_a_request_that_arrives_during_a_takeback_waits_for_it_and_runs_on_the_full_brain(self):
        self.build(full=[reply("Here I am.")])
        self.svc.release_delay = 0.05
        self.tb.on_cockpit_activity()
        for _ in range(200):
            if self.tb._current is not None:
                break
            time.sleep(0.005)
        events = self.ask("hello")
        self.assertEqual(events[-1]["content"], "Here I am.")
        self.assertEqual(self.urls, [FULL_URL])
        self.assertIn("holding", self.types(events))
        self.assertEqual(len(self.svc.released), 1)

    def test_a_request_while_the_lease_winds_down_goes_to_the_coordinator_and_does_not_take_back_again(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("one"), reply("two")])
        self.ask("look")
        self.assertIsNotNone(self.svc.lease)                              # the lease record is still there (the tail of the revert)
        self.assertEqual(self.svc.brain()["mode"], "lite")                # ... and it still says "spare brain"
        events = self.ask("and now?")
        self.assertEqual(events[-1]["content"], "two")
        self.assertEqual(self.urls[-1], FULL_URL)
        self.assertEqual(len(self.svc.released), 1)
        self.assertEqual(len(self.trace()), 1)
        self.assertNotIn("holding", self.types(events))

    def test_a_request_while_the_lease_winds_down_and_the_coordinator_is_not_up_stays_on_the_spare_brain(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"}), reply("Short answer.")], load_s=500, cfg={"wait_s": 5})
        self.ask("look")                                                   # timed out: takeback_failed
        events = self.ask("and now?")
        self.assertEqual(events[-1]["content"], "Short answer.")
        self.assertEqual(self.urls[-1], LITE_URL)
        self.assertEqual(len(self.svc.released), 1)

    def test_cooldown_state_survives_a_restart(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "takeback.json")
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")])
        self.tb.path = path
        self.ask("look")
        again = Takeback(lambda: self.config, lambda: None, lambda: True, path=path, clock=self.clock)
        self.assertGreater(again.cooldown_left(), 3500)
        self.assertEqual(again.last["why"], "tool")

    def test_the_cooldown_starts_from_the_first_moment_and_follows_the_config(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")], cfg={"cooldown_min": 5})
        self.ask("look")
        self.assertEqual(self.yielded[0][1], 300.0)
        self.assertAlmostEqual(self.tb.cooldown_left(), 300 - 30, delta=15)

    def test_metrics_count_by_why_and_outcome_and_keep_a_wait_histogram(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")])
        self.ask("look")
        text = "\n".join(self.tb.metrics())
        self.assertIn('stonesage_takeback_total{card="coordinator",why="tool",outcome="ok"} 1', text)
        self.assertIn("stonesage_takeback_user_wait_seconds_count 1", text)
        self.assertIn('stonesage_takeback_user_wait_seconds_bucket{le="60"} 1', text)
        self.assertIn('stonesage_takeback_user_wait_seconds_bucket{le="10"} 0', text)

    def test_status_reports_the_last_takeback(self):
        self.build(lite=[tool_call("think_harder", {"question": "why is the sky blue"})], full=[reply("ok")])
        self.ask("look")
        st = self.tb.status()
        self.assertEqual(st["last"]["why"], "tool")
        self.assertGreater(st["cooldown_left_s"], 0)


# ---- the real lease -----------------------------------------------------------------------------------------------------------
class MiniVM:
    """systemd with Conflicts= between the garden layout and what it evicts; :8001 answers while the coordinator runs."""

    def __init__(self):
        self.running = {"coord", "worker"}
        self.log = []

    def systemctl(self, verb, unit):
        self.log.append((verb, unit))
        if verb == "start":
            if unit == "layout":
                self.running -= {"coord", "worker"}
            elif unit in ("coord", "worker"):
                self.running.discard("layout")
            self.running.add(unit)
        else:
            self.running.discard(unit)
        return {"ok": True}

    def http_ok(self, url):
        port = url.split(":")[2].split("/")[0]
        return {"8001": "coord", "8002": "worker", "8005": "layout", "8006": "lite"}.get(port) in self.running


class TestTakebackWithTheRealLease(unittest.TestCase):
    """The real EngineLease and a fake VM: the lease is released on the takeback's own thread, the coordinator comes back, the turn runs again."""

    def test_end_to_end(self):
        vm = MiniVM()
        cfg = {"engine_layouts": {"garden": {"unit": "layout", "evicts": ["coord", "worker"], "url": "http://vm:8005", "when": "any",
                                              "stand_in": "lite"}},
               "engine_stand_ins": {"lite": {"unit": "lite", "url": "http://vm:8006", "replaces": "coordinator",
                                             "eval_passed": {"single": "44/46"}}}}
        roles = {"coord": {"role": "coordinator", "gpu": "G1", "url": "http://vm:8001"},
                 "worker": {"role": "worker", "gpu": "G1", "url": "http://vm:8002"}}
        now = [1_800_000_000.0]
        lease = el.EngineLease(lambda: cfg, vm.systemctl, vm.http_ok, lambda: roles, lambda: {"gpus": {}}, lambda r: None,
                               clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        res = lease.acquire("garden", 3600, "garden", "garden-scheduler", yield_to_computer=True)
        self.assertTrue(res["ok"], res)
        self.assertNotIn("coord", vm.running)
        clock = Clock()
        stopped = []
        tb = Takeback(lambda: {}, lambda: lease, lambda: vm.http_ok("http://vm:8001"), clock=clock, sleep=clock.sleep)
        tb.register_yielder(lambda why, cd: stopped.append(why))
        urls = []
        lite, full = ScriptedLLM(tool_call("think_harder", {"question": "why is the sky blue"})), ScriptedLLM(reply("A cat."))

        def post(url, body, timeout):
            urls.append(url)
            return (lite if url == LITE_URL else full)(url, body, timeout)
        agent, _ = make_agent(post)
        agent.brain = lambda: lease.brain("coordinator")
        agent.takeback = tb
        events = list(agent.run([{"role": "user", "content": "look"}], "s"))
        self.assertEqual(events[-1]["content"], "A cat.")
        self.assertEqual(stopped, ["takeback: tool"])
        for _ in range(300):
            if lease.lease is None:
                break
            time.sleep(0.01)
        self.assertIsNone(lease.lease)                                          # released through the lease's own revert path
        self.assertIn("coord", vm.running)
        self.assertNotIn("lite", vm.running)                                    # the spare one went once the real one answered
        self.assertEqual(urls[-1], FULL_URL)


if __name__ == "__main__":
    unittest.main()
