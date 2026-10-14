"""What an app can bring to the platform besides routes (stonesage/apps.py, auth.py): entry-point discovery, background starters,
Prometheus metrics, a scoped key, static files and cockpit tabs, all through `install(ctx)`. A neutral fake app stands in; the
platform names no app. No LAN."""

import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from stonesage import apps, auth  # noqa: E402

APP_KEY = "scoped-key-for-tests-0123456789abcdef"
SERVER_KEY = "server-key-for-tests-0123456789abcdef"
ROUTES = (("POST", "/api/fake/v1/chat"), ("POST", "/api/fake/v1/embed"))


class Handler:
    """Just enough of a request handler for auth.authenticate and a static route."""

    def __init__(self, bearer="", method="GET", path="/", ip="127.0.0.1"):
        self.headers = {"Authorization": "Bearer " + bearer} if bearer else {}
        self.client_address = (ip, 1)
        self.command, self.path = method, path
        self.sent, self.out = [], io.BytesIO()

    def send_response(self, code):
        self.sent.append(("status", code))

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self.out

    def header(self, name):
        return next((v for k, v in self.sent if k == name), None)


class FakeAppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        sys.path.insert(0, self.tmp)
        self.addCleanup(lambda: sys.path.remove(self.tmp))
        self.addCleanup(lambda: [sys.modules.pop(m) for m in [m for m in sys.modules if m.split(".")[0] in ("fakeapp", "entrypkg")]])
        self.addCleanup(auth.unregister_scoped_key, "fake")
        auth.THROTTLE._fails.clear()
        self.addCleanup(auth.THROTTLE._fails.clear)
        self.web = os.path.join(self.tmp, "fakeapp", "web")

    def make(self, pkg, body, module="app", extra=None):
        os.makedirs(os.path.join(self.tmp, pkg), exist_ok=True)
        with open(os.path.join(self.tmp, pkg, "__init__.py"), "w") as f:
            f.write('__version__ = "1.2.3"\n')
        with open(os.path.join(self.tmp, pkg, module + ".py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
        for rel, text in (extra or {}).items():
            path = os.path.join(self.tmp, pkg, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)

    def ctx(self, config=None):
        return apps.AppContext(config if config is not None else {"api": {"key": SERVER_KEY}}, ROOT, lambda: self.tmp)

    def fake_app(self):
        self.make("fakeapp", """
            import os
            HERE = os.path.dirname(os.path.abspath(__file__))
            def key(config):
                return str((config.get("fake") or {}).get("model_key") or "")
            def install(ctx):
                ctx.add_route("POST", "/api/fake/v1/chat", lambda h, p, b: None)
                ctx.add_route("POST", "/api/fake/v1/embed", lambda h, p, b: None)
                ctx.add_scoped_key("fake", [("POST", "/api/fake/v1/chat"), ("POST", "/api/fake/v1/embed")], key)
                ctx.add_metrics(lambda: ["fake_gauge 1"])
                ctx.add_static(os.path.join(HERE, "web"))
                ctx.add_tab("fake", "FAKE", "tab.js", open="openFake")
        """, extra={"web/tab.js": "export const x = 1;\n", "web/secret.py": "x = 1\n", "web/data.json": "{}\n"})


class EntryPoints(FakeAppCase):
    def test_an_entry_point_names_the_module_of_an_installed_app(self):
        self.make("entrypkg", "def install(ctx):\n    ctx.hooks['entry'] = 1\n", module="plugin")
        c = self.ctx()
        with mock.patch.object(apps, "discover", return_value={"entryapp": "entrypkg.plugin"}):
            self.assertEqual(apps.load_apps(["entryapp"], c), ["entryapp"])
            self.assertEqual(c.hooks, {"entry": 1})
            self.assertEqual(c.versions["entryapp"], "1.2.3")

    def test_installed_is_not_enabled(self):
        self.make("entrypkg", "def install(ctx):\n    ctx.hooks['entry'] = 1\n", module="plugin")
        c = self.ctx()
        with mock.patch.object(apps, "discover", return_value={"entryapp": "entrypkg.plugin"}):
            self.assertEqual(apps.load_apps([], c), [])
            self.assertEqual(c.hooks, {})
            m = c.manifest()
        self.assertEqual((m["apps"], m["available"]), ([], ["entryapp"]))

    def test_a_name_without_an_entry_point_is_still_a_package_with_an_app_module(self):
        self.fake_app()
        c = self.ctx()
        with mock.patch.object(apps, "discover", return_value={}):
            self.assertEqual(apps.load_apps(["fakeapp"], c), ["fakeapp"])

    def test_the_real_entry_point_group_is_read(self):
        """discover() asks importlib.metadata for the group `stonesage.apps` and tolerates a broken environment."""
        ep = mock.Mock()
        ep.name, ep.value = "x", "xpkg.app:install"
        with mock.patch("importlib.metadata.entry_points", return_value=[ep]) as eps:
            self.assertEqual(apps.discover(), {"x": "xpkg.app"})
        eps.assert_called_once_with(group="stonesage.apps")
        with mock.patch("importlib.metadata.entry_points", side_effect=RuntimeError("broken")):
            with self.assertLogs("StoneSage.apps", level="WARNING"):
                self.assertEqual(apps.discover(), {})


class Bringing(FakeAppCase):
    def test_nothing_appears_with_no_apps(self):
        c = self.ctx()
        self.assertEqual(c.manifest()["apps"], [])
        self.assertEqual(c.metrics_lines(), [])
        self.assertEqual(c.routes.table(), [])
        self.assertEqual(auth.SCOPED_KEYS.get("fake"), None)

    def test_metrics_lines_are_collected_and_a_failing_source_is_skipped(self):
        self.fake_app()
        c = self.ctx()
        apps.load_apps(["fakeapp"], c)
        c.add_metrics(lambda: 1 / 0)
        with self.assertLogs("StoneSage.apps", level="WARNING"):
            self.assertEqual(c.metrics_lines(), ["fake_gauge 1"])

    def test_the_manifest_lists_the_tab_with_its_script_under_the_apps_path(self):
        self.fake_app()
        c = self.ctx()
        apps.load_apps(["fakeapp"], c)
        m = c.manifest()
        self.assertEqual([a["id"] for a in m["apps"]], ["fakeapp"])
        tab = m["apps"][0]["tabs"][0]
        self.assertEqual((tab["id"], tab["label"], tab["script"], tab["open"], tab["target"]),
                         ("fake", "FAKE", "/apps/fakeapp/tab.js", "openFake", "engine_console"))
        self.assertNotIn("app", tab)

    def test_tabs_and_static_folders_belong_to_an_app_being_installed(self):
        c = self.ctx()
        with self.assertRaises(ValueError):
            c.add_tab("t", "T", "t.js")
        with self.assertRaises(ValueError):
            c.add_static(self.tmp)

    def test_the_apps_route_is_the_platforms_and_needs_the_login(self):
        c = self.ctx()
        apps.install_platform_routes(c)
        self.assertIsNotNone(c.routes.find("GET", "/api/apps"))
        self.assertFalse(auth.is_public("GET", "/api/apps"))
        self.assertFalse(auth.is_public("GET", "/apps/fakeapp/tab.js", frontend_dir=self.tmp))

    def test_the_manifest_answers_json(self):
        self.fake_app()
        c = self.ctx()
        apps.load_apps(["fakeapp"], c)
        apps.install_platform_routes(c)
        h = Handler()
        c.routes.find("GET", "/api/apps")(h, None, {})
        self.assertEqual(h.sent[0], ("status", 200))
        self.assertEqual(json.loads(h.out.getvalue())["apps"][0]["tabs"][0]["id"], "fake")


class Static(FakeAppCase):
    def serve(self, rel):
        self.fake_app()
        c = self.ctx()
        apps.load_apps(["fakeapp"], c)
        import urllib.parse
        h = Handler(path="/apps/fakeapp/" + rel)
        c.routes.find("GET", "/apps/fakeapp/" + rel)(h, urllib.parse.urlparse(h.path), {})
        return h

    def test_a_module_script_arrives_as_javascript_not_text(self):
        h = self.serve("tab.js")
        self.assertEqual(h.sent[0], ("status", 200))
        self.assertEqual(h.header("Content-Type"), "text/javascript; charset=utf-8")
        self.assertEqual(h.out.getvalue(), b"export const x = 1;\n")

    def test_json_is_served_and_python_is_not(self):
        self.assertEqual(self.serve("data.json").sent[0], ("status", 200))
        self.assertEqual(self.serve("secret.py").sent[0], ("status", 404))

    def test_nothing_outside_the_folder_and_no_listing(self):
        for rel in ("..%2f__init__.py", "../__init__.py", "%2e%2e/app.py", "..%5capp.py", "", "nope.js", "sub/../../app.py"):
            self.assertEqual(self.serve(rel).sent[0], ("status", 404), rel)

    def test_a_folder_that_shares_a_prefix_is_not_inside(self):
        os.makedirs(self.web + "-private", exist_ok=True)
        with open(os.path.join(self.web + "-private", "x.js"), "w") as f:
            f.write("1")
        self.assertEqual(self.serve("../web-private/x.js").sent[0], ("status", 404))


class CockpitPage(unittest.TestCase):
    """The page names no app. Its engine console asks GET /api/apps and builds a button, a pane (`<id>-root`) and, when the tab is first
    opened, imports the app's script and calls the function the app named."""
    FRONT = os.path.join(ROOT, "StoneSage", "frontend")

    def read(self, *parts):
        with open(os.path.join(self.FRONT, *parts), encoding="utf-8-sig") as f:
            return f.read()

    def test_the_page_carries_no_app_marker_and_no_app_tab(self):
        for page in (self.read("index.html"), self.read("js", "app.js"), self.read("js", "engine_console.js")):
            self.assertNotRegex(page, r"(?i)garden:(?:begin|end)|grove|openGarden|garden-root|econsole-layer-garden")
        self.assertNotIn('data-layer="garden"', self.read("index.html"))

    def test_the_engine_console_builds_app_tabs_from_the_manifest(self):
        js = self.read("js", "engine_console.js")
        for needle in ("fetch('/api/apps')", "tab.target !== 'engine_console'", "startsWith('/apps/')", "`${tab.id}-root`",
                       "await import(tab.script)", "window[tab.open]"):
            self.assertIn(needle, js)
        block = js[js.index("// ─── Tabs that apps bring"):js.index("// ─── Sub-Layer Switching")]
        self.assertNotRegex(block, r"innerHTML|insertAdjacentHTML|document\.write")      # a tab's label is text, never markup

    @unittest.skipUnless(__import__("shutil").which("node"), "node is not installed")
    def test_the_tab_loader_runs_in_a_fake_dom(self):
        import subprocess
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "app_tabs.test.mjs"), os.path.join(self.FRONT, "js", "engine_console.js")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ScopedKeys(FakeAppCase):
    def setUp(self):
        super().setUp()
        self.fake_app()
        self.c = self.ctx({"api": {"key": SERVER_KEY}, "fake": {"model_key": APP_KEY}})
        apps.load_apps(["fakeapp"], self.c)

    def who(self, bearer, method, path, config=None):
        return auth.authenticate(Handler(bearer), config or self.c.config, method, path)

    def test_the_scoped_key_opens_exactly_the_declared_routes(self):
        for method, path in ROUTES:
            self.assertEqual(self.who(APP_KEY, method, path), ("fake", "fake-bearer"))

    def test_it_is_refused_everywhere_else(self):
        elsewhere = [("GET", "/api/fake/v1/chat"), ("POST", "/api/fake/v1/chat/"), ("POST", "/api/fake/v1/chat/extra"),
                     ("POST", "/api/config"), ("GET", "/api/souls"), ("POST", "/v1/chat/completions"), ("GET", "/metrics"),
                     ("POST", "/api/apps"), ("GET", "/api/apps"), ("GET", "/apps/fakeapp/tab.js")]
        for method, path in elsewhere:
            auth.THROTTLE._fails.clear()
            self.assertEqual(self.who(APP_KEY, method, path)[0], None, (method, path))

    def test_the_gate_answers_401_for_the_key_anywhere_else_and_lets_it_through_on_its_routes(self):
        def gate(method, path):
            h = Handler(APP_KEY, method, path)
            h.headers["Host"] = "localhost:8888"
            return auth.gate(h, method, self.c.config, routes=self.c.routes, frontend_dir=self.tmp), h
        ok, h = gate("POST", "/api/fake/v1/chat")
        self.assertTrue(ok)
        self.assertEqual(h.principal, "fake")
        auth.THROTTLE._fails.clear()
        denied, h = gate("GET", "/api/apps")
        self.assertFalse(denied)
        self.assertEqual(h.sent[0], ("status", 401))

    def test_wrong_missing_and_unset_keys_are_refused(self):
        self.assertIsNone(self.who("nope", "POST", ROUTES[0][1])[0])
        self.assertIsNone(self.who("", "POST", ROUTES[0][1])[0])
        self.assertIsNone(self.who(APP_KEY, "POST", ROUTES[0][1], {"api": {"key": SERVER_KEY}})[0])        # nothing configured

    def test_the_servers_own_key_is_never_a_scoped_key(self):
        cfg = {"api": {"key": SERVER_KEY}, "fake": {"model_key": SERVER_KEY}}
        self.assertEqual(auth.scoped_key_value(cfg, "fake"), "")
        auth.THROTTLE._fails.clear()
        # the owner's key authenticates as the owner on those routes (the owner may call anything), not as the scoped principal
        self.assertEqual(self.who(SERVER_KEY, "POST", ROUTES[0][1], cfg), (auth.OWNER, "bearer"))

    def test_the_owner_key_is_unaffected(self):
        self.assertEqual(self.who(SERVER_KEY, "GET", "/api/souls"), (auth.OWNER, "bearer"))

    def test_a_scoped_key_cannot_take_the_owners_name(self):
        for bad in ("owner", "", "trusted:1.2.3.4", "ip:1.2.3.4"):
            with self.assertRaises(ValueError):
                auth.register_scoped_key(bad, [("GET", "/x")], lambda c: "k")

    def test_it_is_not_a_login(self):
        h = Handler(APP_KEY)
        self.assertEqual(auth.authenticate(h, self.c.config, "POST", "/api/auth/whoami"), (None, "bad-key"))


if __name__ == "__main__":
    unittest.main()
