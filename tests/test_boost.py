"""Offline tests for Boost (backend/boost): providers, quotas, egress scan, router fallback, proxy helpers,
Courage's think_harder tool, config masking, and the frontier worker's command building. No network."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))

from lodestone import boost  # noqa: E402
from lodestone import config_mask  # noqa: E402
from lodestone.boost import egress  # noqa: E402
from lodestone.boost.providers import load_providers  # noqa: E402
from lodestone.boost.quota import QuotaBook  # noqa: E402
from lodestone.boost.router import BoostRouter  # noqa: E402
from stonesage.courage import CourageDeps, CourageTools  # noqa: E402

ENV = {"GROQ_API_KEY": "gsk_test", "GEMINI_API_KEY": "g_test", "OPENCODE_API_KEY": "oc_test"}


def reference_policy(tier, content_class, images=False):
    """The rule the owner chose (tiered), used to test the router independently of egress_allowed's implementation."""
    if content_class == "secret":
        return tier == "local"
    if images:
        return tier == "local"
    if content_class == "home":
        return tier in ("local", "no_training")
    return True


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeHttp:
    """Scripted provider responses keyed by URL substring; records every call."""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []

    def _answer(self, url):
        for key, resp in self.script.items():
            if key in url:
                return resp(url) if callable(resp) else resp
        return 200, {}, {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 10}}

    def request(self, method, url, headers, body=None, timeout=60):
        self.calls.append((method, url, headers, body))
        if url.endswith("/models"):
            return 404, {}, {"error": "no list"}
        return self._answer(url)

    def open_stream(self, url, headers, body, timeout=60):
        self.calls.append(("STREAM", url, headers, body))
        status, h, data = self._answer(url)
        if status == 200:
            return 200, h, iter([b'data: {"choices":[{"delta":{"content":"hi"}}]}\n', b"data: [DONE]\n"])
        return status, h, data


def cfg(**boost_over):
    b = {"enabled": True, "surfaces": {"chat": True, "courage": True, "loops": True, "workspaces": True}}
    b.update(boost_over)
    return {"boost": b, "cluster": {"coordinator_url": "http://127.0.0.1:8001/v1"}}


class TestProviders(unittest.TestCase):
    def test_keys_from_env_and_cloudflare_needs_account(self):
        provs = load_providers(cfg(), ENV)
        self.assertTrue(provs["groq"].has_key)
        self.assertFalse(provs["openrouter"].has_key)
        self.assertFalse(provs["cloudflare"].enabled)          # no CLOUDFLARE_ACCOUNT_ID
        env = dict(ENV, CLOUDFLARE_ACCOUNT_ID="abc", CLOUDFLARE_API_TOKEN="t")
        cf = load_providers(cfg(), env)["cloudflare"]
        self.assertTrue(cf.enabled)
        self.assertIn("/accounts/abc/ai/v1", cf.base_url)

    def test_config_overrides_and_edge_nodes(self):
        c = cfg(providers={"groq": {"limits": {"rpd": 5}, "models": ["x"]}})
        c["harness_instances"] = [{"id": "workstation", "name": "Workstation", "url": "http://192.0.2.132:1234", "boost": True},
                                  {"id": "ally", "url": "http://192.0.2.213:1234"}]
        provs = load_providers(c, ENV)
        self.assertEqual(provs["groq"].limits, {"rpd": 5})
        self.assertEqual(provs["groq"].models, ["x"])
        self.assertIn("edge:workstation", provs)
        self.assertNotIn("edge:ally", provs)                  # not opted in
        self.assertEqual(provs["edge:workstation"].tier, "local")
        self.assertEqual(provs["edge:workstation"].base_url, "http://192.0.2.132:1234/v1")

    def test_public_view_has_no_key(self):
        pub = load_providers(cfg(), ENV)["groq"].public()
        self.assertNotIn("gsk_test", json.dumps(pub))
        self.assertTrue(pub["has_key"])


class TestQuota(unittest.TestCase):
    def test_rpm_rpd_tpd_and_cooldown(self):
        clk = Clock()
        q = QuotaBook(None, clock=clk)
        lim = {"rpm": 2, "rpd": 3, "tpd": 100}
        self.assertIsNone(q.check("p", lim, "chat"))
        q.record("p", 10, "chat")
        q.record("p", 10, "chat")
        self.assertEqual(q.check("p", lim, "chat"), "per-minute limit")
        clk.t += 61
        self.assertIsNone(q.check("p", lim, "chat"))
        self.assertEqual(q.check("p", lim, "chat", est_tokens=90), "daily token limit")
        q.record("p", 10, "chat")
        self.assertEqual(q.check("p", lim, "chat"), "daily request limit")
        clk.t += 86401
        self.assertIsNone(q.check("p", lim, "chat"))
        q.cool_down("p", 30, "HTTP 429")
        self.assertIn("cooling down", q.check("p", lim, "chat"))
        self.assertEqual(q.usage("p", lim)["last_error"], "HTTP 429")

    def test_background_share_keeps_headroom_for_courage(self):
        q = QuotaBook(None, loop_share=0.5, clock=Clock())
        lim = {"rpd": 4}
        q.record("p", 1, "loops")
        q.record("p", 1, "workspaces")
        self.assertIn("background share", q.check("p", lim, "loops"))
        self.assertIsNone(q.check("p", lim, "courage"))

    def test_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "q.json")
            q = QuotaBook(path, clock=Clock())
            q.record("p", 5, "chat")
            q.flush()
            self.assertEqual(QuotaBook(path, clock=Clock()).usage("p", {})["requests_today"], 1)


class TestEgressScan(unittest.TestCase):
    def test_scan_finds_home_and_secrets(self):
        self.assertIn("home:ha_entity", egress.scan("turn on light.kitchen_ceiling"))
        self.assertIn("home:lan_ip", egress.scan("the NAS at 10.0.0.230"))
        self.assertIn("secret:jwt", egress.scan("token eyJhbGciOiJIUzI1.eyJpc3MiOiJhYmMx.c2lnbmF0dXJlMTIz"))
        self.assertIn("secret:assignment", egress.scan("HASS_TOKEN=abcdef1234567890"))
        self.assertIn("home:term:bella", egress.scan("Is Bella asleep?", ["bella"]))
        self.assertEqual(egress.scan("Prove there are infinitely many primes."), [])

    def test_classify_only_raises(self):
        msgs = [{"role": "user", "content": "why is 10.0.0.5 unreachable"}]
        self.assertEqual(egress.classify("general", msgs)["class"], "home")
        self.assertEqual(egress.classify("home", [{"role": "user", "content": "hello"}])["class"], "home")
        self.assertEqual(egress.classify("bogus", [{"role": "user", "content": "hello"}])["class"], "general")

    def test_images_detected(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                             {"type": "image_url", "image_url": {"url": "data:..."}}]}]
        self.assertTrue(egress.classify("general", msgs)["images"])


class TestEgressPolicy(unittest.TestCase):
    """The owner's egress_allowed() (Learn-by-Doing): must match the tiered rule."""

    def test_matrix(self):
        for tier in ("local", "no_training", "training"):
            for cls in ("general", "code", "home", "secret"):
                for images in (False, True):
                    with self.subTest(tier=tier, cls=cls, images=images):
                        self.assertEqual(egress.egress_allowed(tier, cls, images), reference_policy(tier, cls, images))


@mock.patch("lodestone.boost.egress.egress_allowed", reference_policy)
class TestRouter(unittest.TestCase):
    def make(self, http=None, c=None, env=ENV):
        return BoostRouter(lambda: c or cfg(), None, http=http or FakeHttp(), env=env, clock=Clock(),
                           home_terms=("bella",))

    def test_home_terms_are_read_per_call_and_survive_a_failed_lookup(self):
        names = ["bella"]

        def terms():
            if names is None:
                raise OSError("the engine host unreachable")
            return names
        r = BoostRouter(lambda: cfg(), None, http=FakeHttp(), env=ENV, clock=Clock(), home_terms=terms)
        self.assertIn("bella", r.home_terms())
        names.append("Aunt May")                          # profile added while running
        self.assertIn("Aunt May", r.home_terms())
        names = None
        self.assertIn("Aunt May", r.home_terms())         # lookup failed: last good list, never empty

    def test_priority_order_skips_missing_keys(self):
        r = self.make()
        res = r.complete([{"role": "user", "content": "hi"}], "chat")
        self.assertTrue(res["ok"])
        self.assertEqual(res["provider"], "groq")
        self.assertIn("cloudflare: disabled", res["skipped"])

    def test_429_cools_down_and_falls_through(self):
        http = FakeHttp({"groq.com": (429, {"retry-after": "30"}, {"error": {"message": "rate limit"}})})
        r = self.make(http)
        res = r.complete([{"role": "user", "content": "hi"}], "chat")
        self.assertEqual(res["provider"], "gemini")
        self.assertTrue(res["tried"][0].startswith("groq: HTTP 429"))
        again = r.complete([{"role": "user", "content": "hi"}], "chat")
        self.assertTrue(any(s.startswith("groq: cooling down") for s in again["skipped"]))

    def test_home_content_never_reaches_training_tiers(self):
        http = FakeHttp({"groq.com": (500, {}, "down")})
        r = self.make(http)
        res = r.complete([{"role": "user", "content": "where is bella"}], "courage", allow_local=False)
        self.assertFalse(res["ok"])
        self.assertTrue(all("gemini" not in c[1] and "opencode" not in c[1] for c in http.calls))
        self.assertTrue(any(s.startswith("gemini: home content") for s in res["skipped"]))

    def test_secret_goes_nowhere_but_local(self):
        http = FakeHttp()
        r = self.make(http)
        res = r.complete([{"role": "user", "content": "api_key: sk-abcdefghijklmnopqrstuvwxyz"}], "chat")
        self.assertEqual(res["provider"], "local")
        self.assertTrue(all("127.0.0.1" in c[1] for c in http.calls if c[0] == "POST"))

    def test_pinned_and_local_fallback(self):
        r = self.make()
        self.assertEqual(r.complete([{"role": "user", "content": "hi"}], "chat", pinned="zen/big-pickle")["model"], "big-pickle")
        http = FakeHttp({"groq.com": (500, {}, "x"), "googleapis": (500, {}, "x"), "opencode.ai": (500, {}, "x")})
        res = self.make(http).complete([{"role": "user", "content": "hi"}], "chat")
        self.assertEqual(res["provider"], "local")

    def test_edge_node_first_for_home(self):
        c = cfg()
        c["harness_instances"] = [{"id": "ws", "url": "http://192.0.2.132:1234", "boost": True, "boost_models": ["qwen3-4b"]}]
        r = self.make(c=c)
        self.assertEqual(r.complete([{"role": "user", "content": "is bella home"}], "courage")["provider"], "edge:ws")
        self.assertEqual(r.complete([{"role": "user", "content": "hi"}], "chat")["provider"], "groq")

    def test_stream_records_usage(self):
        r = self.make()
        res = r.open_stream([{"role": "user", "content": "hi"}], "loops")
        self.assertTrue(res["ok"])
        lines = list(boost.iter_sse(res["response"]))
        self.assertEqual(lines[-1].strip(), "data: [DONE]")
        self.assertEqual(r.quota.usage("groq", {})["background_today"], 1)

    def test_live_models_filtered_to_free(self):
        class ListHttp(FakeHttp):
            def request(self, method, url, headers, body=None, timeout=60):
                if url.endswith("/models"):
                    return 200, {}, {"data": [{"id": "a:free", "context_length": 8000, "supported_parameters": ["tools"]},
                                              {"id": "b-paid"}, {"id": "c:free", "context_length": 128000}]}
                return super().request(method, url, headers, body, timeout)
        env = dict(ENV, OPENROUTER_API_KEY="or")
        r = self.make(ListHttp(), env=env)
        p = r.providers()["openrouter"]
        self.assertEqual([m["id"] for m in r.live_models(p)], ["c:free", "a:free"])
        self.assertEqual(r.choose_model(p, need_tools=False), "c:free")


class TestConfigMask(unittest.TestCase):
    def test_mask_and_drop(self):
        c = {"homeassistant": {"token": "eyJabcdefghijklmnop"}, "boost": {"providers": {"groq": {"api_key": "gsk_123456789abc"}}},
             "proxmox": {"pve_node": {"token_secret": "short"}}, "name": "x"}
        m = config_mask.mask(c)
        self.assertEqual(m["homeassistant"]["token"], "••••mnop")
        self.assertEqual(m["proxmox"]["pve_node"]["token_secret"], "••••")
        self.assertEqual(m["name"], "x")
        self.assertNotIn("gsk_123456789abc", json.dumps(m))
        self.assertEqual(config_mask.drop_masked(m), {"boost": {"providers": {"groq": {}}}, "homeassistant": {},
                                                       "proxmox": {"pve_node": {}}, "name": "x"})


class TestCourageThinkHarder(unittest.TestCase):
    def deps(self, **kw):
        return CourageDeps(ha_states=None, ha_call=None, presence=None, camera_look=None, camera_scan=None, **kw)

    def test_offered_only_when_wired_and_on(self):
        self.assertNotIn("think_harder", [t["function"]["name"] for t in CourageTools(self.deps()).openai_tools()])
        on = {"v": False}
        tools = CourageTools(self.deps(think_harder=lambda q, k: {"ok": True, "answer": q.upper()},
                                       think_harder_available=lambda: on["v"]))
        self.assertNotIn("think_harder", [t["function"]["name"] for t in tools.openai_tools()])
        self.assertIn("unknown tool", tools.execute("think_harder", {"question": "x"}))
        on["v"] = True
        self.assertIn("think_harder", [t["function"]["name"] for t in tools.openai_tools()])
        self.assertIn("WHY", tools.execute("think_harder", {"question": "why", "kind": "weird"}))
        self.assertEqual(tools.status_text("think_harder", {}), "Borrowing a bigger brain…")


def _load_frontier_worker():
    from lodestone import boost                  # the worker ships in lodestone/boost/, however installed
    path = os.path.join(os.path.dirname(boost.__file__), "frontier_worker.py")
    spec = importlib.util.spec_from_file_location("frontier_worker", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFrontierWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fw = _load_frontier_worker()

    def test_claude_command_uses_only_listed_flags(self):
        help_text = "  -p, --print\n  --output-format <format>\n  --allowedTools <tools...>\n  --permission-mode <mode>\n"
        cmd = self.fw.build_command("claude", "fix the bug", help_text)
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertIn("fix the bug", cmd[2])
        self.assertIn("--allowedTools", cmd)
        self.assertNotIn("--strict-mcp-config", cmd)   # not in this help text
        tools = cmd[cmd.index("--allowedTools") + 1]
        self.assertNotIn("WebFetch", tools)
        self.assertIn("Bash(git diff:*)", tools)

    def test_gemini_command(self):
        cmd = self.fw.build_command("gemini", "t", "--approval-mode\n--output-format\n")
        self.assertIn("auto_edit", cmd)
        with self.assertRaises(ValueError):
            self.fw.build_command("gpt", "t", "")

    def test_child_env_has_no_secrets(self):
        with mock.patch.dict(os.environ, {"HASS_TOKEN": "x", "FRONTIER_WORKER_TOKEN": "y", "HOME": "/home/frontier"}):
            env = self.fw.child_env()
        self.assertNotIn("HASS_TOKEN", env)
        self.assertNotIn("FRONTIER_WORKER_TOKEN", env)
        self.assertEqual(env.get("HOME"), "/home/frontier")

    def test_daily_cap(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(self.fw, "STATE_FILE", os.path.join(d, "s.json")):
                self.assertTrue(self.fw.take_slot("claude", cap=2))
                self.assertTrue(self.fw.take_slot("claude", cap=2))
                self.assertFalse(self.fw.take_slot("claude", cap=2))
                self.assertEqual(self.fw.jobs_today("gemini"), 0)

    def test_parse_answer(self):
        self.assertEqual(self.fw.parse_answer("claude", json.dumps({"result": "done"})), "done")
        self.assertEqual(self.fw.parse_answer("gemini", "plain"), "plain")


class TestClusterStreamBoost(unittest.TestCase):
    """ClusterClient.stream_chat("boost"): provider stream passed through; off or refused -> local coordinator."""

    def client(self):
        from stonesage.cluster_client import ClusterClient
        c = ClusterClient({"cluster": {}})
        c.local_calls = []

        def fake_local(target, messages, params):
            c.local_calls.append(target)
            yield "data: [DONE]\n\n"
        return c, fake_local

    def test_off_goes_local(self):
        c, fake_local = self.client()
        with mock.patch.object(boost, "get_router", return_value=None):
            out = list(c._stream_boost_with(fake_local, "boost", [{"role": "user", "content": "hi"}], {}))
        self.assertEqual(c.local_calls, ["coordinator"])
        self.assertIn("Boost", out[0])

    @mock.patch("lodestone.boost.egress.egress_allowed", reference_policy)
    def test_stream_passthrough(self):
        c, fake_local = self.client()
        r = BoostRouter(lambda: cfg(), None, http=FakeHttp(), env=ENV, clock=Clock())
        with mock.patch.object(boost, "get_router", return_value=r):
            out = list(c._stream_boost_with(fake_local, "boost", [{"role": "user", "content": "hi"}], {}))
        self.assertIn("Groq", out[0])
        self.assertTrue(any('"hi"' in o for o in out))
        self.assertEqual(out[-1], "data: [DONE]\n\n")
        self.assertEqual(c.local_calls, [])

    def test_images_stay_home(self):
        c, fake_local = self.client()
        r = BoostRouter(lambda: cfg(), None, http=FakeHttp(), env=ENV, clock=Clock())
        with mock.patch.object(boost, "get_router", return_value=r):
            list(c._stream_boost_with(fake_local, "boost", [{"role": "user", "content": "x", "images": ["data:"]}], {}))
        self.assertEqual(c.local_calls, ["coordinator"])


if __name__ == "__main__":
    unittest.main()
