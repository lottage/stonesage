"""How long Computer waits for the coordinator: the choice (config, CPU hint, GPU default) and the idle-not-total behaviour
of the streamed call, against a fake slow server on 127.0.0.1."""

import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from lodestone import endpoints  # noqa: E402
from stonesage.courage import agent as agent_mod  # noqa: E402
from stonesage.courage.agent import llm_timeout_s  # noqa: E402

CPU_PROFILE = {"engines": {"coordinator": {"online": True, "offloaded": True}}}
GPU_PROFILE = {"engines": {"coordinator": {"online": True, "offloaded": False, "gpu": {"short": "RTX 4090"}}}}


class SlowSSE(BaseHTTPRequestHandler):
    """POST -> an event stream: `head_delay` s of silence, then `chunks` data lines `gap` s apart, then (optionally)
    `stall` s of silence before the end. Parameters come from the server object."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        p = self.server.params
        time.sleep(p.get("head_delay", 0))
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(p.get("chunks", 0)):
                if i:
                    time.sleep(p.get("gap", 0))
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": "w%d " % i}}]}).encode() + b"\n\n")
                self.wfile.flush()
            time.sleep(p.get("stall", 0))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass


class Server:
    def __init__(self, **params):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), SlowSSE)
        self.srv.daemon_threads = True
        self.srv.params = params
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestTimeoutChoice(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if k != "COORDINATOR_CPU"}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_gpu_keeps_todays_value(self):
        self.assertEqual(llm_timeout_s({}), 90.0)
        self.assertEqual(llm_timeout_s({}, GPU_PROFILE), 90.0)

    def test_cpu_hint_in_config_gives_the_long_wait(self):
        self.assertEqual(llm_timeout_s({"cluster": {"coordinator_cpu": True}}), 600.0)

    def test_cpu_from_the_environment_the_docker_stack_sets(self):
        with mock.patch.dict(os.environ, {"COORDINATOR_CPU": "1"}):
            self.assertEqual(llm_timeout_s({}), 600.0)
        with mock.patch.dict(os.environ, {"COORDINATOR_CPU": "0"}):
            self.assertEqual(llm_timeout_s({}, CPU_PROFILE), 90.0)

    def test_cpu_from_the_live_profile(self):
        self.assertEqual(llm_timeout_s({}, CPU_PROFILE), 600.0)
        offline = {"engines": {"coordinator": {"online": False, "offloaded": True}}}
        self.assertEqual(llm_timeout_s({}, offline), 90.0)            # an engine that is down says nothing about its speed

    def test_an_explicit_hint_beats_the_profile(self):
        self.assertEqual(llm_timeout_s({"cluster": {"coordinator_cpu": False}}, CPU_PROFILE), 90.0)
        self.assertEqual(llm_timeout_s({"cluster": {"coordinator_cpu": "true"}}, GPU_PROFILE), 600.0)

    def test_configured_seconds_win_over_everything(self):
        cfg = {"courage": {"llm_timeout_s": 45}, "cluster": {"coordinator_cpu": True}}
        self.assertEqual(llm_timeout_s(cfg, CPU_PROFILE), 45.0)
        self.assertEqual(llm_timeout_s({"courage": {"llm_timeout_s": "1200"}}), 1200.0)

    def test_nonsense_in_config_falls_back(self):
        for bad in ("soon", 0, -5, None, []):
            self.assertEqual(llm_timeout_s({"courage": {"llm_timeout_s": bad}}), 90.0, bad)

    def test_constants_are_what_the_docs_say(self):
        self.assertEqual((endpoints.CPU_LLM_TIMEOUT_S, endpoints.GPU_LLM_TIMEOUT_S), (600.0, 90.0))

    def test_the_agent_uses_the_timeout_it_is_given(self):
        a = agent_mod.CourageAgent(mock.MagicMock(), "http://127.0.0.1:1/v1", timeout=llm_timeout_s({"cluster": {"coordinator_cpu": True}}))
        self.assertEqual(a.timeout, 600.0)


class TestIdleNotTotal(unittest.TestCase):
    def stream(self, srv, timeout):
        return [c["choices"][0]["delta"]["content"] for c in agent_mod._http_stream_json(srv.url, {"messages": []}, timeout)]

    def test_a_slow_stream_that_keeps_going_is_not_cut_by_a_total_deadline(self):
        srv = Server(chunks=6, gap=0.25)           # 1.25 s in all, no gap longer than 0.25 s
        self.addCleanup(srv.close)
        t0 = time.time()
        got = self.stream(srv, timeout=0.7)        # shorter than the whole reply, longer than any silence
        self.assertEqual(got, ["w%d " % i for i in range(6)])
        self.assertGreater(time.time() - t0, 1.0)

    def test_a_stream_that_goes_silent_is_cut(self):
        srv = Server(chunks=2, gap=0.05, stall=2.0)
        self.addCleanup(srv.close)
        got = []
        with self.assertRaises((socket.timeout, TimeoutError)):
            for c in agent_mod._http_stream_json(srv.url, {"messages": []}, 0.5):
                got.append(c)
        self.assertEqual(len(got), 2)               # what arrived before the silence was delivered

    def test_the_wait_for_the_first_token_counts_as_silence(self):
        srv = Server(chunks=1, head_delay=1.5)      # a long prompt being read
        self.addCleanup(srv.close)
        with self.assertRaises((socket.timeout, TimeoutError)):
            self.stream(srv, timeout=0.5)
        self.assertEqual(self.stream(srv, timeout=5), ["w0 "])    # the long CPU wait lets it through

    def test_the_agents_own_call_uses_its_timeout(self):
        srv = Server(chunks=0, head_delay=1.5)
        self.addCleanup(srv.close)
        from stonesage.courage.tools import CourageTools
        tools = mock.MagicMock(spec=CourageTools)
        tools.openai_tools.return_value = []
        a = agent_mod.CourageAgent(tools, srv.url.replace("/chat/completions", ""), timeout=0.4)
        step = a._step([{"role": "user", "content": "hi"}])
        with self.assertRaises((socket.timeout, TimeoutError)):
            list(step)


if __name__ == "__main__":
    unittest.main()
