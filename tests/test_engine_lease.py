"""Offline tests for the engine lease (backend/engine_lease.py): gates, start + health, revert on failure / release /
expiry / restart, the human-only check-in, and Computer answering "on loan" instead of failing."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lodestone import engine_lease as el  # noqa: E402

LAYOUTS = {
    "longctx_6750": {"unit": "llama-longctx.service", "evicts": ["llama-coordinator.service", "llama-worker.service"],
                     "url": "http://vm:8005", "when": "idle", "stand_in": "courage_lite"},
    "longctx_6600": {"unit": "llama-longctx-6600.service", "evicts": ["vision-server.service"], "url": "http://vm:8005",
                     "when": "any"},
    "longctx_max": {"unit": "llama-longctx-max.service", "evicts": [], "disabled": "crashed at 245K"},
}
ROLES = {"llama-coordinator.service": {"role": "coordinator", "gpu": "RX 6750 XT", "url": "http://vm:8001"},
         "llama-worker.service": {"role": "worker", "gpu": "RX 6750 XT", "url": "http://vm:8002"},
         "vision-server.service": {"role": "vision", "gpu": "RX 6600", "url": "http://vm:8004"}}


class FakeVM:
    """systemd with Conflicts= between each layout and what it evicts."""

    def __init__(self):
        self.running = {"llama-coordinator.service", "llama-worker.service", "vision-server.service"}
        self.log, self.broken = [], set()

    def systemctl(self, verb, unit):
        self.log.append((verb, unit))
        if verb == "is-active":
            return {"ok": unit in self.running, "error": None}
        if unit in self.broken:
            return {"ok": False, "error": "Job failed"}
        if verb == "start":
            for spec in LAYOUTS.values():
                if unit == spec["unit"]:
                    self.running -= set(spec["evicts"])
                elif unit in spec["evicts"]:
                    self.running.discard(spec["unit"])
            self.running.add(unit)
        elif verb == "stop":
            self.running.discard(unit)
        return {"ok": True}

    def http_ok(self, url):
        port = url.split(":")[2].split("/")[0]
        by_port = {"8005": ("llama-longctx.service", "llama-longctx-6600.service"), "8001": ("llama-coordinator.service",),
                   "8006": ("llama-courage-lite.service",),
                   "8002": ("llama-worker.service",), "8004": ("vision-server.service",)}
        return any(u in self.running for u in by_port.get(port, ()))


class Rig:
    def __init__(self, gate_open=True, busy=None, stand_ins=None):
        self.now = 1_800_000_000.0
        self.stand_ins = stand_ins or {}
        self.vm, self.pushes, self.traces = FakeVM(), [], []
        self.gate_open, self.busy = gate_open, busy
        self.path = os.path.join(tempfile.mkdtemp(), "lease.json")
        self.lease = self.make()

    def make(self):
        return el.EngineLease(lambda: {"engine_layouts": LAYOUTS, "engine_stand_ins": self.stand_ins}, self.vm.systemctl, self.vm.http_ok, lambda: ROLES,
                              self.gate, lambda roles: self.busy, push=lambda *a: self.pushes.append(a),
                              trace=self.traces.append, path=self.path, clock=lambda: self.now,
                              sleep=lambda s: setattr(self, "now", self.now + s))

    def gate(self):
        g = {"open": self.gate_open, "reasons": [] if self.gate_open else ["coordinator served a request"]}
        return {"gpus": {"RX 6750 XT": g, "RX 6600": g}, "shared_reasons": []}


class TestAcquire(unittest.TestCase):
    def test_acquire_evicts_and_release_brings_them_back(self):
        r = Rig()
        res = r.lease.acquire("longctx_6750", 3600, "loop job 3f2a", "owner")
        self.assertTrue(res["ok"], res)
        self.assertEqual(r.vm.running, {"llama-longctx.service", "vision-server.service"})
        self.assertIsNotNone(r.lease.on_loan("coordinator"))
        self.assertIsNone(r.lease.on_loan("vision"))
        self.assertEqual(json.load(open(r.path))["layout"], "longctx_6750")
        out = r.lease.release(res["lease_id"], approver="owner")
        self.assertTrue(out["ok"], out)
        self.assertEqual(r.vm.running, {"llama-coordinator.service", "llama-worker.service", "vision-server.service"})
        self.assertIsNone(r.lease.on_loan("coordinator"))
        self.assertEqual([t["event"] for t in r.traces], ["acquire", "release"])

    def test_gates(self):
        r = Rig(gate_open=False)
        self.assertIn("idle gate closed", r.lease.acquire("longctx_6750")["error"])
        self.assertTrue(r.lease.acquire("longctx_6600")["ok"])            # when: any (Courage keeps working)
        self.assertIn("already held", r.lease.acquire("longctx_6750")["error"])
        self.assertIn("disabled", Rig().lease.acquire("longctx_max")["error"])
        self.assertIn("unknown layout", Rig().lease.acquire("nope")["error"])

    def test_in_use_refuses_unless_forced(self):
        r = Rig(busy="Computer answered 12 s ago")
        self.assertIn("in use: Computer answered 12 s ago", r.lease.acquire("longctx_6750")["error"])
        self.assertTrue(r.lease.acquire("longctx_6750", force=True)["ok"])

    def test_a_layout_that_never_answers_is_reverted(self):
        r = Rig()
        r.lease.http_ok = lambda url: ":8005" not in url and r.vm.http_ok(url)   # the layout never answers
        res = r.lease.acquire("longctx_6750")
        self.assertFalse(res["ok"])
        self.assertIn("reverted", res["error"])
        self.assertIn("llama-coordinator.service", r.vm.running)
        self.assertIsNone(r.lease.lease)


class TestClock(unittest.TestCase):
    def test_checkin_then_expiry_without_an_answer(self):
        r = Rig()
        lid = r.lease.acquire("longctx_6600", 3600)["lease_id"]
        r.now += 3600 - 599
        r.lease.tick()
        title, message, buttons, tag = r.pushes[0]
        self.assertEqual([b["title"] for b in buttons], ["Another hour", "Stop"])
        self.assertEqual(buttons[0]["action"], f"LEASE_RENEW_{lid}")
        r.lease.tick()
        self.assertEqual(len(r.pushes), 1)                                   # once
        r.now += 600
        r.lease.tick()
        self.assertIsNone(r.lease.lease)
        self.assertIn("vision-server.service", r.vm.running)
        self.assertEqual(r.traces[-1]["event"], "expire")

    def test_another_hour_from_the_phone(self):
        r = Rig()
        lid = r.lease.acquire("longctx_6600", 3600)["lease_id"]
        r.now += 3100
        r.lease.tick()
        self.assertIn("renewed until", r.lease.handle_action(f"LEASE_RENEW_{lid}"))
        r.now += 1800
        r.lease.tick()
        self.assertIsNotNone(r.lease.lease)                                  # 30 min into the new hour
        self.assertEqual(r.traces[-1]["surface"], "phone")
        self.assertIsNone(r.lease.handle_action("COURAGE_YES_abc"))          # not ours

    def test_stop_from_the_phone(self):
        r = Rig()
        lid = r.lease.acquire("longctx_6600")["lease_id"]
        self.assertEqual(r.lease.handle_action(f"LEASE_STOP_{lid}"), "lease stopped, engines back")
        self.assertIsNone(r.lease.lease)

    def test_restart_reverts_an_orphan(self):
        r = Rig()
        r.lease.acquire("longctx_6750")
        fresh = r.make()                                                      # StoneSage restarted
        out = fresh.recover()
        self.assertTrue(out["ok"])
        self.assertIn("llama-coordinator.service", r.vm.running)
        self.assertNotIn("llama-longctx.service", r.vm.running)
        self.assertEqual(r.traces[-1]["why"], "orphaned by a StoneSage restart")

    def test_a_failed_revert_is_pushed(self):
        r = Rig()
        lid = r.lease.acquire("longctx_6600")["lease_id"]
        r.vm.broken.add("vision-server.service")
        out = r.lease.release(lid)
        self.assertFalse(out["ok"])
        self.assertEqual(r.traces[-1]["event"], "revert_failed")
        self.assertEqual(r.pushes[-1][0], "Computer: engines did not come back")


NORMAL = {"llama-coordinator.service", "llama-worker.service", "vision-server.service"}


class TestCancelWhileStarting(unittest.TestCase):
    """2026-10-14: a takeback released a lease that was still `starting`; the start sequence went on, evicted the coordinator and
    worker, and nothing reverted them. release() now cancels the start; whichever step it hits, the end state is the normal one."""

    def rig(self):
        r = Rig(stand_ins=LITE)
        r.fired, r.out, r.thread, r.polls_after = False, {}, None, 0
        return r

    @staticmethod
    def fire(r):
        """release() the way takeback does (another thread), once; returns when the cancel flag is set."""
        if r.fired:
            return
        r.fired = True

        def go():
            r.out.update(r.lease.release(why="takeback: on_loan", approver="takeback"))
            r.out["running_at_return"] = set(r.vm.running)

        ctl = r.lease._starting
        r.thread = threading.Thread(target=go)
        r.thread.start()
        deadline = time.time() + 5
        while not ctl.cancel.is_set() and time.time() < deadline:
            time.sleep(0.001)
        assert ctl.cancel.is_set(), "release() did not set the cancel flag"

    def at(self, r, trigger):
        """Fire release() at one point of the start sequence."""
        vm_ctl, vm_ok, real_save, real_sleep = r.vm.systemctl, r.vm.http_ok, r.lease._save, r.lease.sleep

        def ctl(verb, unit):
            res = vm_ctl(verb, unit)
            if (trigger, verb, unit) == ("after_eviction", "start", "llama-longctx.service"):
                self.fire(r)
            return res

        def ok(url):
            res = vm_ok(url)
            if r.fired and (":8005" in url or ":8006" in url):
                r.polls_after += 1
            if trigger == "after_stand_in" and res and ":8006" in url:
                self.fire(r)
            if trigger == "just_before_active" and res and ":8005" in url:
                self.fire(r)                                                         # the layout answered; the lease is about to turn active
            return res

        def sleep(sec):
            if trigger == "during_stand_in_wait" and not r.fired:
                self.fire(r)
            if trigger == "during_health_wait" and not r.fired and r.vm.running >= {"llama-courage-lite.service", "llama-longctx.service"}:
                self.fire(r)
            real_sleep(sec)

        def save():
            real_save()
            if trigger == "before_stand_in":
                self.fire(r)

        r.lease.systemctl, r.lease.http_ok, r.lease.sleep, r.lease._save = ctl, ok, sleep, save
        if trigger == "during_stand_in_wait":
            r.lease.http_ok = lambda url: False if ":8006" in url else ok(url)       # the stand-in is slow
        if trigger == "during_health_wait":
            slow = {"n": 0}

            def slow_ok(url):
                if ":8005" in url:
                    slow["n"] += 1
                    r.polls_after += 1 if r.fired else 0
                    return slow["n"] > 1 and ok(url)                                  # the layout is slow: first poll fails
                return ok(url)
            r.lease.http_ok = slow_ok

    def check(self, trigger, stage, evicted):
        r = self.rig()
        self.at(r, trigger)
        res = r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler", yield_to_computer=True)
        r.thread.join(10)
        self.assertFalse(r.thread.is_alive())
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("stage"), stage, res)
        self.assertEqual(r.vm.running, NORMAL)                                       # coordinator + worker + vision, no layout, no stand-in
        self.assertIsNone(r.lease.lease)
        self.assertIsNone(r.lease._starting)
        with open(r.path) as f:
            self.assertIsNone(json.load(f))
        self.assertEqual(r.out["running_at_return"], NORMAL)                         # release() did not return before the engines were back
        self.assertTrue(r.out["ok"], r.out)
        self.assertTrue(r.out["cancelled_while_starting"])
        rec = [t for t in r.traces if t["event"] == "cancelled_while_starting"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["stage"], stage)
        self.assertNotIn("acquire", [t["event"] for t in r.traces])
        started = [u for v, u in r.vm.log if v == "start"]
        if evicted:
            self.assertIn("llama-longctx.service", started)
        else:
            self.assertNotIn("llama-longctx.service", started)                       # cancelled before anything was evicted
            self.assertEqual(r.vm.log.count(("start", "llama-coordinator.service")), 0)
        return r

    def test_release_before_the_stand_in_starts(self):
        r = self.check("before_stand_in", "before_stand_in", evicted=False)
        self.assertNotIn(("start", "llama-courage-lite.service"), r.vm.log)

    def test_release_while_waiting_for_the_stand_in(self):
        r = self.check("during_stand_in_wait", "after_stand_in", evicted=False)
        self.assertIn(("stop", "llama-courage-lite.service"), r.vm.log)
        self.assertEqual(r.polls_after, 0)                                           # the wait gave up at once

    def test_release_after_the_stand_in_is_up_before_anything_is_evicted(self):
        r = self.check("after_stand_in", "after_stand_in", evicted=False)
        self.assertIn(("stop", "llama-courage-lite.service"), r.vm.log)

    def test_release_right_after_the_eviction(self):
        r = self.check("after_eviction", "after_eviction", evicted=True)
        self.assertLess(r.vm.log.index(("start", "llama-coordinator.service")), r.vm.log.index(("stop", "llama-courage-lite.service")))

    def test_release_during_the_layouts_health_wait(self):
        r = self.check("during_health_wait", "during_health_wait", evicted=True)
        self.assertEqual(r.polls_after, 0)                                           # no further polling of the layout after the cancel

    def test_release_the_moment_the_layout_answers(self):
        self.check("just_before_active", "during_health_wait", evicted=True)

    def test_a_cancelled_lease_can_be_acquired_again(self):
        r = self.check("after_eviction", "after_eviction", evicted=True)
        r.lease.systemctl, r.lease.http_ok, r.lease.sleep = r.vm.systemctl, r.vm.http_ok, lambda s: setattr(r, "now", r.now + s)
        self.assertTrue(r.lease.acquire("longctx_6750", 3600, "again", "owner")["ok"])

    def test_release_reports_in_progress_instead_of_released_when_the_cancel_has_not_finished(self):
        r = self.rig()
        r.lease.cancel_wait_s = 0.05
        self.at(r, "after_eviction")
        real = r.lease.systemctl

        def slow(verb, unit):
            if r.fired and verb == "start" and unit == "llama-coordinator.service":
                time.sleep(0.4)                                                      # the revert is still running when release gives up waiting
            return real(verb, unit)
        r.lease.systemctl = slow
        r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler")
        r.thread.join(10)
        self.assertFalse(r.out["ok"])
        self.assertTrue(r.out["in_progress"])
        self.assertIn("being cancelled", r.out["error"])
        self.assertNotIn("llama-coordinator.service", r.out["running_at_return"])    # and it did not claim otherwise
        self.assertEqual(r.vm.running, NORMAL)                                       # the start sequence finished the job anyway

    def test_a_second_acquire_during_a_start_is_refused(self):
        r = self.rig()
        seen = {}
        real = r.vm.systemctl

        def ctl(verb, unit):
            if (verb, unit) == ("start", "llama-longctx.service") and "res" not in seen:
                seen["res"] = r.lease.acquire("longctx_6600", 3600, "second", "owner")
            return real(verb, unit)
        r.lease.systemctl = ctl
        self.assertTrue(r.lease.acquire("longctx_6750", 3600, "first", "owner")["ok"])
        self.assertFalse(seen["res"]["ok"])
        self.assertIn("already held", seen["res"]["error"])

    def test_release_of_an_active_lease_is_unchanged(self):
        r = self.rig()
        lid = r.lease.acquire("longctx_6750", 3600, "x", "owner")["lease_id"]
        out = r.lease.release(lid)
        self.assertTrue(out["ok"])
        self.assertNotIn("cancelled_while_starting", out)
        self.assertEqual(r.vm.running, NORMAL)


class TestReconcile(unittest.TestCase):
    def stray(self, r):
        """The live 2026-10-14 end state: layout + stand-in running, coordinator and worker down, no lease."""
        r.vm.running = {"vision-server.service", "llama-longctx.service", "llama-courage-lite.service"}

    def test_a_stray_layout_with_no_lease_is_put_back(self):
        r = Rig(stand_ins=LITE)
        self.stray(r)
        out = r.lease.reconcile(immediate=True)
        self.assertEqual(r.vm.running, NORMAL)
        self.assertEqual(out["stray_layouts"], ["llama-longctx.service"])
        self.assertEqual(sorted(out["engines_down"]), ["llama-coordinator.service", "llama-worker.service"])
        self.assertEqual([t["event"] for t in r.traces], ["reconcile"])
        self.assertEqual(len(r.pushes), 1)
        self.assertIsNone(r.lease.lease)
        self.assertIsNone(r.lease.reconcile(immediate=True))                          # clean now: nothing to do
        self.assertEqual(len(r.pushes), 1)

    def test_a_stopped_engine_alone_is_started(self):
        r = Rig()
        r.vm.running.discard("vision-server.service")
        r.lease.reconcile(immediate=True)
        self.assertEqual(r.vm.running, NORMAL)

    def test_a_stray_must_be_seen_twice_unless_immediate(self):
        r = Rig(stand_ins=LITE)
        self.stray(r)
        self.assertIsNone(r.lease.reconcile())
        self.assertNotEqual(r.vm.running, NORMAL)
        r.now += 5
        self.assertIsNone(r.lease.reconcile())
        r.now += 30
        self.assertIsNotNone(r.lease.reconcile())
        self.assertEqual(r.vm.running, NORMAL)

    def test_a_held_lease_is_never_touched(self):
        r = Rig(stand_ins=LITE)
        r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler")
        before, log = set(r.vm.running), len(r.vm.log)
        self.assertIsNone(r.lease.reconcile(immediate=True))
        self.assertEqual(r.vm.running, before)
        self.assertEqual(len(r.vm.log), log)                                          # not even a status query

    def test_a_lease_that_is_starting_is_never_touched(self):
        r = Rig(stand_ins=LITE)
        seen, real = {}, r.vm.systemctl

        def ctl(verb, unit):
            if (verb, unit) == ("start", "llama-longctx.service") and not seen:
                seen["out"] = r.lease.reconcile(immediate=True)
            return real(verb, unit)
        r.lease.systemctl = ctl
        self.assertTrue(r.lease.acquire("longctx_6750", 3600, "g", "owner")["ok"])
        self.assertIsNone(seen["out"])
        self.assertIn("llama-longctx.service", r.vm.running)
        self.assertNotIn("reconcile", [t["event"] for t in r.traces])

    def test_one_phone_note_per_incident_even_when_it_cannot_be_fixed(self):
        r = Rig(stand_ins=LITE)
        self.stray(r)
        r.vm.broken.add("llama-coordinator.service")
        r.lease.reconcile(immediate=True)
        r.lease.reconcile(immediate=True)
        self.assertEqual(len(r.pushes), 1)
        self.assertEqual(r.traces[-1]["triggers"], ["revert_failed"])
        self.assertIn("llama-courage-lite.service", r.vm.running)                      # the spare brain stays while the real one is down

    def test_an_unreachable_host_is_not_a_stray(self):
        r = Rig()
        r.lease.systemctl = lambda verb, unit: {"ok": False, "error": "ssh: connect to host: timed out"}
        self.assertIsNone(r.lease.reconcile(immediate=True))
        self.assertEqual(r.traces, [])

    def test_recover_then_reconcile_at_start(self):
        r = Rig(stand_ins=LITE)
        r.lease.acquire("longctx_6750")
        fresh = r.make()
        fresh.recover()
        self.stray(r)                                                                  # something else left the layout running
        fresh.reconcile(immediate=True)
        self.assertEqual(r.vm.running, NORMAL)


class TestComputerOnLoan(unittest.TestCase):
    def test_on_loan_answer_and_no_escalation(self):
        from agent_fixture import ScriptedLLM, make_agent
        from stonesage.courage.escalation import Escalation
        agent, _ = make_agent(ScriptedLLM())                                  # any LLM call would fail the test
        agent.brain = lambda: {"mode": "on_loan", "until": 1_800_003_600.0}
        escalations = []
        agent.escalation = Escalation(lambda: {}, push=lambda t, m: escalations.append(m), trace=escalations.append)
        events = list(agent.run([{"role": "user", "content": "What's the weather like?"}], "ha:1"))
        final = events[-1]["content"]
        self.assertIn("on loan until", final)
        self.assertEqual(escalations, [])


LITE = {"courage_lite": {"unit": "llama-courage-lite.service", "url": "http://vm:8006", "replaces": "coordinator",
                         "eval_passed": {"single": "44/46", "mid": "42/46", "wrong_actions": 0}}}


class TestStandIn(unittest.TestCase):
    def test_stand_in_starts_first_and_computer_goes_lite(self):
        r = Rig(stand_ins=LITE)
        res = r.lease.acquire("longctx_6750", 3600, "loop", "owner")
        self.assertTrue(res["ok"], res)
        starts = [u for v, u in r.vm.log if v == "start"]
        self.assertEqual(starts[:2], ["llama-courage-lite.service", "llama-longctx.service"])   # stand-in first
        b = r.lease.brain("coordinator")
        self.assertEqual((b["mode"], b["url"]), ("lite", "http://vm:8006"))
        self.assertIsNone(r.lease.brain("vision"))                            # not lent out by this layout
        r.lease.release(res["lease_id"])
        self.assertNotIn("llama-courage-lite.service", r.vm.running)
        stops = [u for v, u in r.vm.log if v == "stop"]
        self.assertIn("llama-courage-lite.service", stops)
        self.assertLess(r.vm.log.index(("start", "llama-coordinator.service")),
                        r.vm.log.index(("stop", "llama-courage-lite.service")))  # full brain back before the spare goes
        self.assertIsNone(r.lease.brain("coordinator"))

    def test_no_eval_no_stand_in(self):
        r = Rig(stand_ins={"courage_lite": dict(LITE["courage_lite"], eval_passed=None)})
        r.lease.acquire("longctx_6750")
        self.assertEqual(r.lease.brain("coordinator")["mode"], "on_loan")
        self.assertIn("stand_in_skipped", [t["event"] for t in r.traces])

    def test_a_stand_in_that_doesnt_start_means_on_loan(self):
        r = Rig(stand_ins=LITE)
        r.vm.broken.add("llama-courage-lite.service")
        self.assertTrue(r.lease.acquire("longctx_6750")["ok"])
        self.assertEqual(r.lease.brain("coordinator")["mode"], "on_loan")
        self.assertIn("stand_in_failed", [t["event"] for t in r.traces])

    def test_failed_revert_keeps_the_spare_brain_answering(self):
        r = Rig(stand_ins=LITE)
        lid = r.lease.acquire("longctx_6750")["lease_id"]
        r.vm.broken.add("llama-coordinator.service")
        out = r.lease.release(lid)
        self.assertFalse(out["ok"])
        self.assertEqual(r.lease.lease["state"], "revert_failed")
        self.assertEqual(r.lease.brain("coordinator")["mode"], "lite")
        self.assertIn("llama-courage-lite.service", r.vm.running)
        self.assertIn("already held", r.lease.acquire("longctx_6600")["error"])   # nothing new until a person fixes it
        r.vm.broken.clear()
        self.assertTrue(r.lease.release(lid)["ok"])                            # a person retries
        self.assertIsNone(r.lease.lease)


class TestComputerLite(unittest.TestCase):
    def setUp(self):
        from agent_fixture import ScriptedLLM, make_agent, tool_call, reply
        self.tool_call, self.reply = tool_call, reply
        self.urls = []
        self.ScriptedLLM, self.make_agent = ScriptedLLM, make_agent

    def agent(self, *responses):
        llm = self.ScriptedLLM(*responses)

        def post(url, body, timeout):
            self.urls.append(url)
            return llm(url, body, timeout)

        agent, ha = self.make_agent(post)
        agent.brain = lambda: {"mode": "lite", "url": "http://vm:8006", "until": 1_800_003_600.0}
        return agent, ha, llm

    def test_lite_turn_goes_to_the_stand_in_short_and_labelled(self):
        agent, _, llm = self.agent(self.reply("Rex was on the kitchen camera an hour ago."))
        events = list(agent.run([{"role": "user", "content": "Where is Rex?"}], "web-1"))
        self.assertEqual(self.urls, ["http://vm:8006/v1/chat/completions"])
        body = llm.requests[0]
        self.assertEqual(body["max_tokens"], 400)
        self.assertIn("spare brain until", body["messages"][0]["content"])
        self.assertEqual(events[-1]["content"], "Rex was on the kitchen camera an hour ago.")

    def test_an_outright_order_still_asks_in_lite(self):
        order = "Text Alex that dinner is ready."    # a direct order (runs at once on the full brain), not a reflex
        call = self.tool_call("notify", {"message": "Dinner is ready."})
        agent, _, _ = self.agent(call)
        self.assertTrue(agent.tools.is_direct_command("notify", {"message": "Dinner is ready."}, order))
        events = list(agent.run([{"role": "user", "content": order}], "web-2"))
        self.assertTrue(any(e["type"] == "approval_required" for e in events))   # lite: asks anyway
        full, _, _ = self.agent(call, self.reply("Sent."))
        full.brain = None
        events = list(full.run([{"role": "user", "content": order}], "web-3"))
        self.assertFalse(any(e["type"] == "approval_required" for e in events))  # control: the full brain just does it

    def test_trace_says_lite(self):
        from stonesage.courage.trace import TraceLog
        agent, _, _ = self.agent(self.reply("Right."))
        agent.trace_log = TraceLog(os.path.join(tempfile.mkdtemp(), "t.jsonl"))
        list(agent.run([{"role": "user", "content": "How are you?"}], "web-3"))
        self.assertEqual(agent.trace_log.recent(1)[0]["brain"], "lite")

    def test_a_dead_stand_in_is_an_honest_answer(self):
        from stonesage.courage.escalation import Escalation
        agent, _, _ = self.agent()                                             # no scripted replies: the call fails
        told = []
        agent.escalation = Escalation(lambda: {}, push=lambda t, m: told.append(m), trace=told.append)
        events = list(agent.run([{"role": "user", "content": "What's the temperature?"}], "ha:9"))
        self.assertIn("spare one isn't answering", events[-1]["content"])
        self.assertEqual(told, [])



class TestLeaseAndCockpit(unittest.TestCase):
    def rig(self, cockpit=True):
        r = Rig(stand_ins=LITE)
        reason = "someone is using StoneSage (3 s ago)"

        def gate():
            g = {"open": not cockpit, "reasons": [], "open_ignoring_cockpit": True}
            return {"gpus": {"RX 6750 XT": g, "RX 6600": dict(g)}, "shared_reasons": [reason] if cockpit else [],
                    "cockpit": reason if cockpit else None}
        r.lease.idle_check = gate
        return r

    def test_background_work_is_refused_while_a_person_is_on_the_page(self):
        r = self.rig()
        res = r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler", yield_to_computer=True)
        self.assertFalse(res["ok"])
        self.assertIn("someone is using StoneSage", res["error"])

    def test_a_lease_a_person_asks_for_ignores_that_he_is_on_the_page(self):
        r = self.rig()
        self.assertTrue(r.lease.acquire("longctx_6750", 3600, "long context", "owner")["ok"])

    def demand_rig(self, household_awake=True, cockpit=False):
        r = Rig(stand_ins=LITE)
        reason = "someone is using StoneSage (3 s ago)"
        house = ["Alex is awake (using their phone)"] if household_awake else []

        def gate():
            g = {"open": not house and not cockpit, "reasons": [], "open_ignoring_cockpit": not house,
                 "demand_open": True, "demand_open_with_cockpit": not cockpit}
            return {"gpus": {"RX 6750 XT": g, "RX 6600": dict(g)}, "shared_reasons": house + ([reason] if cockpit else []),
                    "demand_shared_reasons": [], "cockpit": reason if cockpit else None}
        r.lease.idle_check = gate
        return r

    def test_background_work_ignores_the_household_signal(self):
        r = self.demand_rig()
        res = r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler", yield_to_computer=True)
        self.assertTrue(res["ok"], res)

    def test_a_lease_a_person_asks_for_keeps_the_household_rule(self):
        r = self.demand_rig()
        res = r.lease.acquire("longctx_6750", 3600, "long context", "owner")
        self.assertFalse(res["ok"])
        self.assertIn("idle gate closed", res["error"])

    def test_the_cockpit_stops_any_background_lease(self):
        r = self.demand_rig(household_awake=False, cockpit=True)
        self.assertFalse(r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler", yield_to_computer=True)["ok"])

    def test_the_flag_is_kept_and_reported_by_brain_and_yielding(self):
        r = self.rig(cockpit=False)
        res = r.lease.acquire("longctx_6750", 3600, "garden", "garden-scheduler", yield_to_computer=True)
        self.assertTrue(res["ok"], res)
        self.assertTrue(r.lease.brain("coordinator")["yield_to_computer"])
        self.assertEqual(r.lease.yielding("coordinator")["id"], res["lease_id"])
        self.assertIsNone(r.lease.yielding("vision"))             # this layout leaves vision alone
        r.lease.lease["state"] = "revert_failed"
        self.assertIsNone(r.lease.yielding("coordinator"))        # nothing left to take back
        r.lease.lease["state"] = "active"
        r.lease.release(res["lease_id"])
        self.assertIsNone(r.lease.yielding("coordinator"))

    def test_a_person_s_own_lease_is_not_yielding(self):
        r = self.rig(cockpit=False)
        r.lease.acquire("longctx_6750", 3600, "long context", "owner")
        self.assertIsNone(r.lease.yielding("coordinator"))
        self.assertFalse(r.lease.brain("coordinator")["yield_to_computer"])

    def test_layout_info_says_whether_the_stand_in_is_ready(self):
        r = Rig(stand_ins=LITE)
        self.assertTrue(r.lease.layout_info("longctx_6750")["stand_in_ready"])
        self.assertFalse(r.lease.layout_info("longctx_6600")["stand_in_ready"])
        r2 = Rig(stand_ins={"courage_lite": dict(LITE["courage_lite"], eval_passed=None)})
        self.assertFalse(r2.lease.layout_info("longctx_6750")["stand_in_ready"])
        self.assertFalse(Rig().lease.layout_info("longctx_6750")["stand_in_ready"])


if __name__ == "__main__":
    unittest.main()
