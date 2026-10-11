"""The agent API (stonesage/agent_api.py): /v1/models, /v1/chat/completions (plain and streamed), the soul routes, the optional
bearer key. Routes are driven through the app route table with a fake handler; a few checks go through the real server."""

import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from agent_fixture import ScriptedLLM, reply, tool_call  # noqa: E402
from test_courage_stream import ScriptedStream, STORY, text_chunks  # noqa: E402
from stonesage import agent_api, auth  # noqa: E402
from stonesage.agents import AgentHub, AgentServices  # noqa: E402
from stonesage.apps import AppContext  # noqa: E402
from stonesage.courage.memory import EXTRACT, ConversationMemory  # noqa: E402
from stonesage.soul import Soul  # noqa: E402

KEY = "test-key-0123456789"


class FakeHeaders(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


class FakeHandler:
    """Just what the routes use of StoneSageHandler."""

    def __init__(self, headers=None, raw=b"", addr="10.0.0.9", fail_after=None, principal="owner"):
        self.headers = FakeHeaders(headers or {})
        self.principal = principal          # what the server's gate (stonesage/auth.py) decided before the route ran
        self._raw_body = raw
        self.client_address = (addr, 50000)
        self.wfile = self
        self.status, self.sent_headers, self.out, self.writes, self.fail_after = 0, {}, b"", 0, fail_after

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        pass

    def write(self, data):
        self.writes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise BrokenPipeError
        self.out += data

    def flush(self):
        pass

    def json(self):
        return json.loads(self.out.decode("utf-8"))

    def sse(self):
        """The data: payloads of a streamed answer ('[DONE]' kept as a string)."""
        events = []
        for block in self.out.decode("utf-8").split("\n\n"):
            if block.startswith("data: "):
                events.append(block[6:] if block[6:] == "[DONE]" else json.loads(block[6:]))
        return events


class StubAgent:
    """A stand-in for Computer's loop: records what it was asked."""

    def __init__(self, *events):
        self.events, self.calls = list(events), []

    def run(self, history, session):
        self.calls.append((history, session))
        for ev in self.events:
            if isinstance(ev, Exception):
                raise ev
            yield ev


def final(text):
    return {"type": "final", "content": text}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.config = {}
        self.llm = ScriptedLLM()
        self.computer = None
        self.stream = None

    def build(self, computer=None):
        def memory(path, url, extract):
            return ConversationMemory(path, lambda texts: [[0.0, 1.0] for _ in texts], lambda p: '{"notes": []}',
                                      extract=extract or EXTRACT)

        services = AgentServices(llm_url=lambda role: "http://fake/v1", timeout=lambda: 5.0, memory=memory,
                                 post=self.llm if self.stream is None else None, stream_post=self.stream)
        self.hub = AgentHub(lambda: self.d, services, computer=(lambda: computer) if computer else None,
                            computer_built=(lambda: computer) if computer else None)
        self.ctx = AppContext(self.config, ROOT, lambda: self.d)
        agent_api.install(self.ctx, self.hub)
        return self.hub

    def call(self, method, path, body=None, headers=None, raw=b"", **kw):
        """principal="owner" (the default) = the request has passed the gate; principal=None = it reached the route without it."""
        h = FakeHandler(headers, raw, **kw)
        parsed = urlparse(path)
        fn = self.ctx.routes.find(method, parsed.path)
        self.assertIsNotNone(fn, f"{method} {parsed.path} is not a route")
        fn(h, parsed, body or {})
        return h

    def chat(self, body, **kw):
        return self.call("POST", "/v1/chat/completions", body, **kw)


class TestModels(Base):
    def test_one_entry_per_agent(self):
        self.build(computer=StubAgent())
        h = self.call("GET", "/v1/models")
        self.assertEqual(h.status, 200)
        data = h.json()
        self.assertEqual(data["object"], "list")
        self.assertEqual([m["id"] for m in data["data"]], ["courage-computer", "atlas"])
        for m in data["data"]:
            self.assertEqual((m["object"], m["owned_by"]), ("model", "stonesage"))
            self.assertTrue(m["name"])
        self.assertEqual(data["data"][0]["name"], "Computer")

    def test_without_the_home_app_only_atlas_is_listed(self):
        self.build()
        self.assertEqual([m["id"] for m in self.call("GET", "/v1/models").json()["data"]], ["atlas"])


class TestChat(Base):
    def test_a_completion_through_atlas(self):
        self.llm.responses = [reply("Start with a failing test.")]
        self.build()
        h = self.chat({"model": "atlas", "messages": [{"role": "system", "content": "Be a pirate."},
                                                     {"role": "user", "content": "How do I fix this bug?"}]})
        self.assertEqual(h.status, 200)
        out = h.json()
        self.assertEqual((out["object"], out["model"]), ("chat.completion", "atlas"))
        self.assertTrue(out["id"].startswith("chatcmpl-"))
        self.assertEqual(out["choices"][0]["message"], {"role": "assistant", "content": "Start with a failing test."})
        self.assertEqual(out["choices"][0]["finish_reason"], "stop")
        msgs = self.llm.requests[0]["messages"]
        self.assertNotIn("pirate", json.dumps(msgs))                    # the persona is the soul's, not the client's
        self.assertEqual(msgs[-1], {"role": "user", "content": "How do I fix this bug?"})

    def test_earlier_messages_are_the_history(self):
        self.llm.responses = [reply("Four.")]
        self.build()
        self.chat({"model": "atlas", "messages": [
            {"role": "user", "content": "Pick a number."}, {"role": "assistant", "content": "Two."},
            {"role": "user", "content": [{"type": "text", "text": "Double "}, {"type": "text", "text": "it."}]}]})
        sent = self.llm.requests[0]["messages"]
        self.assertEqual([(m["role"], m["content"]) for m in sent[1:]],
                         [("user", "Pick a number."), ("assistant", "Two."), ("user", "Double it.")])

    def test_computer_is_reached_by_its_id_and_by_the_name_computer(self):
        stub = StubAgent(final("Done."))
        self.build(computer=stub)
        for name in ("courage-computer", "computer", "Computer"):
            h = self.chat({"model": name, "messages": [{"role": "user", "content": "Hello"}]})
            self.assertEqual(h.json()["choices"][0]["message"]["content"], "Done.", name)
            self.assertEqual(h.json()["model"], "courage-computer")
        self.assertEqual(stub.calls[0][0], [{"role": "user", "content": "Hello"}])

    def test_the_full_answer_is_returned_even_when_the_loop_streamed_parts(self):
        stub = StubAgent({"type": "partial", "content": "One. "}, {"type": "usage", "completion_tokens": 3, "tps": 1},
                         final("One. Two."))
        self.build(computer=stub)
        out = self.chat({"model": "computer", "messages": [{"role": "user", "content": "x"}]}).json()
        self.assertEqual(out["choices"][0]["message"]["content"], "One. Two.")

    def test_session_for_approvals_is_user_then_header_then_address(self):
        stub = StubAgent(final("Shall I?"))
        self.build(computer=stub)
        msgs = [{"role": "user", "content": "x"}]
        self.chat({"model": "computer", "messages": msgs, "user": "alex"}, headers={"X-Session-Id": "ignored"})
        self.chat({"model": "computer", "messages": msgs}, headers={"X-Session-Id": "tab-2"})
        self.chat({"model": "computer", "messages": msgs}, addr="10.0.0.77")
        self.chat({"model": "computer", "messages": msgs, "user": "alex"})
        self.assertEqual([c[1] for c in stub.calls], ["api:owner:alex", "api:owner:tab-2", "api:owner:ip:10.0.0.77", "api:owner:alex"])
        # the id a client chooses can never be one the server uses itself, nor another caller's slot
        stub.calls.clear()
        self.chat({"model": "computer", "messages": msgs, "user": "ha:10.0.0.9"}, addr="10.0.0.5")
        self.chat({"model": "computer", "messages": msgs, "user": "alexa:abc"}, addr="10.0.0.5")
        self.chat({"model": "computer", "messages": msgs, "user": "owner:x"}, addr="10.0.0.5")
        self.chat({"model": "computer", "messages": msgs, "user": "alex"}, principal="trusted:10.0.0.5")
        self.assertEqual([c[1] for c in stub.calls], ["api:owner:ip:10.0.0.5"] * 3 + ["api:trusted:10.0.0.5:alex"])

    def test_errors_use_openais_shape(self):
        self.build(computer=StubAgent(final("x")))
        cases = [({"model": "nobody", "messages": [{"role": "user", "content": "x"}]}, 404, "model_not_found"),
                 ({"model": "atlas", "messages": []}, 400, "invalid_messages"),
                 ({"model": "atlas", "messages": [{"role": "assistant", "content": "x"}]}, 400, "invalid_messages"),
                 ({"model": "atlas", "messages": [{"role": "user", "content": "  "}]}, 400, "invalid_messages"),
                 ({"model": "atlas"}, 400, "invalid_messages"),
                 ({}, 400, "invalid_json")]
        for body, status, code in cases:
            h = self.chat(body)
            self.assertEqual((h.status, h.json()["error"]["code"]), (status, code), body)
            self.assertIn("message", h.json()["error"])

    def test_a_failing_agent_is_a_500_without_details(self):
        self.build(computer=StubAgent(RuntimeError("secret detail")))
        h = self.chat({"model": "computer", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(h.status, 500)
        self.assertNotIn("secret detail", json.dumps(h.json()))

    def test_stream_sends_openai_chunks_with_the_loops_partial_sentences(self):
        self.stream = ScriptedStream(text_chunks(STORY))
        self.build()
        h = self.chat({"model": "atlas", "stream": True, "messages": [{"role": "user", "content": "Tell me a story"}]})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.sent_headers["Content-Type"].startswith("text/event-stream"))
        events = h.sse()
        self.assertEqual(events[-1], "[DONE]")
        chunks = events[:-1]
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": ""})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        pieces = [c["choices"][0]["delta"]["content"] for c in chunks if c["choices"][0]["delta"].get("content")]
        self.assertGreater(len(pieces), 2, "a long reply goes out sentence by sentence")
        self.assertEqual("".join(pieces), STORY)
        self.assertTrue(all(c["object"] == "chat.completion.chunk" and c["model"] == "atlas" for c in chunks))
        self.assertEqual(len({c["id"] for c in chunks}), 1)

    def test_stream_of_a_short_answer_is_one_piece(self):
        self.stream = ScriptedStream(text_chunks("Use a transaction."))
        self.build()
        h = self.chat({"model": "atlas", "stream": True, "messages": [{"role": "user", "content": "Safe update?"}]})
        pieces = [c["choices"][0]["delta"]["content"] for c in h.sse()[1:-2]]
        self.assertEqual(pieces, ["Use a transaction."])

    def test_stream_through_computers_loop_events(self):
        self.build(computer=StubAgent({"type": "tool_call", "name": "presence_now", "arguments": {}},
                                      {"type": "partial", "content": "One. "}, final("One. Two.")))
        h = self.chat({"model": "computer", "stream": True, "messages": [{"role": "user", "content": "x"}]})
        text = "".join(c["choices"][0]["delta"].get("content", "") for c in h.sse()[:-1])
        self.assertEqual(text, "One. Two.")                              # tool events are not part of an OpenAI stream
        self.assertNotIn("presence_now", h.out.decode())

    def test_a_stream_that_fails_ends_cleanly(self):
        self.build(computer=StubAgent({"type": "partial", "content": "One. "}, RuntimeError("secret detail")))
        h = self.chat({"model": "computer", "stream": True, "messages": [{"role": "user", "content": "x"}]})
        events = h.sse()
        self.assertEqual(events[-1], "[DONE]")
        self.assertIn("error", events[-2])
        self.assertNotIn("secret detail", h.out.decode())

    def test_a_client_that_leaves_does_not_stop_the_turn(self):
        stub = StubAgent({"type": "partial", "content": "One. "}, {"type": "partial", "content": "Two. "}, final("Three."))
        finished = []
        original = stub.run

        def run(history, session):
            yield from original(history, session)
            finished.append(True)

        stub.run = run
        self.build(computer=stub)
        self.chat({"model": "computer", "stream": True, "messages": [{"role": "user", "content": "x"}]}, fail_after=1)
        self.assertEqual(finished, [True])

    def test_atlas_cannot_be_talked_into_a_home_tool(self):
        self.llm.responses = [tool_call("ha_call", {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}),
                              reply("That is not something I can do.")]
        self.build()
        h = self.chat({"model": "atlas", "messages": [{"role": "user", "content": "Unlock the front door"}]})
        self.assertEqual(h.json()["choices"][0]["message"]["content"], "That is not something I can do.")
        self.assertIn("unknown tool 'ha_call'", json.dumps(self.llm.requests[1]["messages"]))


class TestAuth(Base):
    def routes(self):
        return [("GET", "/v1/models", None), ("POST", "/v1/chat/completions", {"model": "atlas", "messages": [{"role": "user", "content": "x"}]}),
                ("GET", "/api/souls", None), ("GET", "/api/souls/atlas/export", None), ("POST", "/api/souls/import", None)]

    def test_closed_when_no_key_is_set(self):
        """No key anywhere = nothing is let in (the old behaviour was: no key, no check)."""
        self.llm.responses = [reply("ok")]
        self.build()
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            for method, path, body in self.routes():
                self.assertEqual(self.call(method, path, body, principal=None).status, 401, path)
                self.assertEqual(self.call(method, path, body, principal=None, headers={"Authorization": "Bearer "}).status, 401, path)
                self.assertEqual(self.call(method, path, body, principal=None, headers={"Authorization": "Bearer x"}).status, 401, path)

    def test_a_request_that_passed_the_gate_is_served(self):
        self.build()
        self.assertEqual(self.call("GET", "/api/souls").status, 200)

    def test_every_route_wants_the_key_when_one_is_set(self):
        self.llm.responses = [reply("ok")] * 3
        self.build()
        self.hub.agent(self.hub.resolve("atlas"))
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": KEY}):
            for method, path, body in self.routes():
                for headers in ({}, {"Authorization": "Bearer wrong-key"}, {"Authorization": KEY}, {"Authorization": "Basic " + KEY},
                                {"Authorization": "Bearer "}):
                    h = self.call(method, path, body, headers=headers, principal=None)
                    self.assertEqual(h.status, 401, (path, headers))
                    self.assertEqual(h.sent_headers.get("WWW-Authenticate"), "Bearer")
                    self.assertNotIn(KEY, h.out.decode())
            for path in ("/v1/models", "/v1/chat/completions"):
                h = self.call("GET" if path == "/v1/models" else "POST", path,
                              None if path == "/v1/models" else self.routes()[1][2], headers={"Authorization": "Bearer " + KEY},
                              principal=None)
                self.assertEqual(h.status, 200, path)
            self.assertEqual(self.call("GET", "/api/souls", headers={"authorization": "bearer " + KEY}, principal=None).status, 200)
            self.assertEqual(self.call("GET", "/api/souls/atlas/export", headers={"Authorization": "Bearer " + KEY},
                                       principal=None).status, 200)

    def test_v1_errors_are_openai_shaped_and_soul_errors_are_plain(self):
        self.build()
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": KEY}):
            self.assertEqual(self.call("GET", "/v1/models", principal=None).json()["error"]["code"], "invalid_api_key")
            self.assertEqual(self.call("GET", "/api/souls", principal=None).json(), {"ok": False, "error": "unauthorized"})

    def test_the_key_can_come_from_config(self):
        self.config["api"] = {"key": KEY}
        self.build()
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            self.assertEqual(self.call("GET", "/v1/models", principal=None).status, 401)
            self.assertEqual(self.call("GET", "/v1/models", headers={"Authorization": "Bearer " + KEY}, principal=None).status, 200)

    def test_the_comparison_is_constant_time(self):
        with mock.patch("stonesage.auth.hmac.compare_digest", wraps=auth.hmac.compare_digest) as cmp:
            self.assertTrue(agent_api.authorised("Bearer " + KEY, KEY))
            self.assertFalse(agent_api.authorised("Bearer nope", KEY))
            self.assertEqual(cmp.call_count, 2)


class TestSoulRoutes(Base):
    def exported_atlas(self):
        Soul.open(self.d, "atlas", display_name="Atlas", default_persona="You are Atlas.")
        return self.call("GET", "/api/souls/atlas/export")

    def test_list_and_export(self):
        self.build()
        self.assertEqual(self.call("GET", "/api/souls").json()["souls"], [])
        h = self.exported_atlas()
        self.assertEqual(h.status, 200)
        self.assertEqual(h.sent_headers["Content-Type"], "application/gzip")
        self.assertIn('filename="atlas.soul.tar.gz"', h.sent_headers["Content-Disposition"])
        with tarfile.open(fileobj=io.BytesIO(h.out), mode="r:gz") as tf:
            self.assertIn("persona.md", tf.getnames())
        souls = self.call("GET", "/api/souls").json()["souls"]
        self.assertEqual([(s["id"], s["running"], s["agent"]) for s in souls], [("atlas", False, True)])
        self.hub.agent(self.hub.resolve("atlas"))
        self.assertTrue(self.call("GET", "/api/souls").json()["souls"][0]["running"])

    def test_export_of_nothing_or_of_a_bad_id_is_a_404(self):
        self.build()
        for path in ("/api/souls/nobody/export", "/api/souls/../x/export", "/api/souls/atlas", "/api/souls/atlas/other",
                     "/api/souls/UPPER/export", "/api/souls/a/b/export"):
            self.assertEqual(self.call("GET", path).status, 404, path)

    def test_import_installs_a_soul_that_becomes_an_agent(self):
        self.build()
        blob = self.exported_atlas().out
        other = tempfile.mkdtemp()
        self.d = other                                                   # a second machine
        self.hub.data_dir = lambda: other
        h = self.call("POST", "/api/souls/import?as=reviewer", raw=blob)
        self.assertEqual(h.status, 200)
        self.assertEqual(h.json()["soul"]["id"], "reviewer")
        self.assertTrue(os.path.isfile(os.path.join(other, "souls", "reviewer", "persona.md")))
        self.assertIn("reviewer", [m["id"] for m in self.call("GET", "/v1/models").json()["data"]])

    def test_import_refuses_an_existing_soul_unless_overwrite_and_reloads_the_running_agent(self):
        self.llm.responses = [reply("ok")]
        self.build()
        blob = self.exported_atlas().out
        running = self.hub.agent(self.hub.resolve("atlas"))
        h = self.call("POST", "/api/souls/import", raw=blob)
        self.assertEqual(h.status, 409)
        self.assertTrue(h.json()["running"])
        self.assertIn("running", h.json()["error"])
        self.assertIs(self.hub.agent(self.hub.resolve("atlas")), running)          # untouched
        with open(os.path.join(self.d, "souls", "atlas", "persona.md"), "w", encoding="utf-8") as f:
            f.write("Edited.")
        h = self.call("POST", "/api/souls/import?overwrite=1", raw=blob)
        self.assertEqual(h.status, 200)
        self.assertEqual(open(os.path.join(self.d, "souls", "atlas", "persona.md"), encoding="utf-8").read(), "You are Atlas.")
        self.assertTrue([n for n in os.listdir(os.path.join(self.d, "souls")) if n.startswith("atlas.bak-")])
        self.assertIsNot(self.hub.agent(self.hub.resolve("atlas")), running)       # rebuilt from the new soul

    def test_import_refuses_bad_bodies(self):
        self.build()
        self.assertEqual(self.call("POST", "/api/souls/import").status, 400)                     # empty
        h = self.call("POST", "/api/souls/import", raw=b"not an archive")
        self.assertEqual(h.status, 400)
        self.assertFalse(h.json()["ok"])
        blob = self.exported_atlas().out
        self.assertEqual(self.call("POST", "/api/souls/import?as=Bad%20Id", raw=blob).status, 400)
        with mock.patch.object(agent_api, "MAX_IMPORT_BYTES", 10):
            self.assertEqual(self.call("POST", "/api/souls/import", raw=blob).status, 413)
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, "souls"))), ["atlas"])


class TestThroughTheRealServer(unittest.TestCase):
    """The routes are wired into StoneSage's own handler (data dir: the run's scratch folder)."""

    def call(self, method, path, body=None, headers=None, auth=True):
        from route_harness import call
        status, ctype, payload = call(method, path, body, headers, auth=auth)
        return status, (json.loads(payload) if "json" in ctype else payload)

    def test_models_and_auth(self):
        status, data = self.call("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertIn("atlas", [m["id"] for m in data["data"]])
        if importlib.util.find_spec("hearth"):                                  # Computer needs the Home app
            self.assertIn("courage-computer", [m["id"] for m in data["data"]])
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": KEY}):
            self.assertEqual(self.call("GET", "/v1/models", auth=False)[0], 401)
            self.assertEqual(self.call("GET", "/v1/models", headers={"Authorization": "Bearer " + KEY}, auth=False)[0], 200)
        self.assertEqual(self.call("POST", "/v1/chat/completions", {"model": "nobody", "messages": [{"role": "user", "content": "x"}]})[0], 404)

    def test_import_takes_a_raw_body(self):
        from stonesage import server
        src = tempfile.mkdtemp()
        s = Soul.open(src, "wire-test", display_name="Wire Test", default_persona="You are a test.")
        buf = io.BytesIO()
        s.export(buf)
        target = os.path.join(server._stonesage_data_dir(), "souls", "wire-test")
        try:
            status, data = self.call("POST", "/api/souls/import", buf.getvalue())
            self.assertEqual((status, data["soul"]["id"]), (200, "wire-test"))
            self.assertIn("wire-test", [m["id"] for m in self.call("GET", "/api/souls")[1]["souls"]])
            self.assertEqual(self.call("POST", "/api/souls/import", buf.getvalue())[0], 409)
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def test_cluster_chat_routes_atlas_through_the_loop_and_not_coder_agent(self):
        from stonesage import server
        llm = ScriptedLLM(reply("Mind the migration order."))
        services = AgentServices(llm_url=lambda role: "http://fake/v1", timeout=lambda: 5.0, post=llm,
                                 memory=lambda path, url, extract: ConversationMemory(None, lambda t: [[0.0, 1.0]] * len(t), lambda p: ""))
        with mock.patch.object(server.agent_hub, "services", services):
            server.agent_hub.reload("atlas")
            try:
                status, payload = self.call("POST", "/api/cluster/chat",
                                            {"agent_id": "atlas", "messages": [{"role": "user", "content": "Review my schema change"}]})
            finally:
                server.agent_hub.reload("atlas")
        self.assertEqual(status, 200)
        self.assertIn(b"Mind the migration order.", payload)
        self.assertEqual([t["function"]["name"] for t in llm.requests[0]["tools"]], ["memory_search"])


if __name__ == "__main__":
    unittest.main()
