"""Login and the gate (stonesage/auth.py, docs/LAYERS.md "M5: Login and hardening"), driven through StoneSage's real handler.

The ratchet: every GET and POST route the server has (the platform's `elif path ==` branches, found by reading server.py, and
the app route table) answers 401 with no credentials, except a short public list. A new route is covered the day it is
added; a route that is meant to be public has to be added to PUBLIC here, on purpose.
"""

import ast
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from route_harness import TEST_KEY, call, call_full, header, server_key  # noqa: E402
from stonesage import auth, server  # noqa: E402

# the public list: static files and the login page are public by FILE (checked separately); these are the routes
PUBLIC = {("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("GET", "/api/health"), ("POST", "/api/watch/reply")}
TRUSTED = {("GET", "/api/tags"), ("GET", "/api/version"), ("GET", "/api/ps"), ("POST", "/api/show"), ("POST", "/api/chat"),
           ("GET", "/metrics")}
HA_HOST, OTHER_HOST = "10.0.0.7", "10.0.0.8"


def _handler_class():
    src = open(importlib.util.find_spec("stonesage.server").origin, encoding="utf-8").read()
    return next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "StoneSageHandler")


def platform_routes():
    """{(method, path)} for the exact /api/ paths in _do_get / do_POST, and {(method, prefix)} for startswith() routes."""
    handler = _handler_class()
    exact, prefixes = set(), set()
    for fname, method in (("_do_get", "GET"), ("do_POST", "POST")):
        fn = next(f for f in handler.body if isinstance(f, ast.FunctionDef) and f.name == fname)
        for x in ast.walk(fn):
            if isinstance(x, ast.Compare) and isinstance(x.left, ast.Name) and x.left.id in ("path", "clean_path"):
                for c in x.comparators:
                    for e in (c.elts if isinstance(c, (ast.Tuple, ast.List)) else [c]):
                        if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.startswith(("/api/", "/v1/", "/metrics")):
                            exact.add((method, e.value))
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "startswith" \
                    and isinstance(x.func.value, ast.Name) and x.func.value.id in ("path", "clean_path") \
                    and x.args and isinstance(x.args[0], ast.Constant) and str(x.args[0].value).startswith("/api/"):
                prefixes.add((method, x.args[0].value))
    return exact, prefixes


def app_routes():
    rows = []
    for r in server.app_ctx.routes.table():
        rows.append((r["method"], r["path"].rstrip("*") + ("x" if r["path"].endswith("*") else "")))
    return rows


class Fresh(unittest.TestCase):
    def setUp(self):
        self.key = server_key()
        auth.THROTTLE._fails.clear()
        patch = mock.patch.dict(server.config, {"api": {"key": self.key}, "features": {}})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(auth.THROTTLE._fails.clear)

    def bearer(self):
        return {"Authorization": "Bearer " + self.key}


class TestEveryRouteIsBehindTheGate(Fresh):
    def test_the_enumerators_see_the_routes(self):
        exact, prefixes = platform_routes()
        self.assertGreater(len(exact), 150, "the AST reader found too few platform routes: it went blind")
        self.assertTrue(any(m == "POST" for m, _ in exact) and any(m == "GET" for m, _ in exact))
        self.assertIn(("POST", "/api/terminal/exec"), exact)
        self.assertIn(("GET", "/api/config"), exact)
        self.assertGreaterEqual(len(app_routes()), 5, "at least the agent API's routes")

    def test_every_route_is_401_without_credentials(self):
        exact, prefixes = platform_routes()
        routes = sorted(exact | {(m, p + "x") for m, p in prefixes} | set(app_routes()))
        seen_open, bad = [], []
        for method, path in routes:
            if (method, path) in PUBLIC:
                continue
            status, _, payload = call(method, path, {} if method == "POST" else None, auth=False)
            if status != 401:
                seen_open.append((method, path, status))
            try:
                if status == 401 and json.loads(payload).get("ok") is not False and "error" not in json.loads(payload):
                    bad.append((method, path))
            except ValueError:
                bad.append((method, path))
        self.assertEqual(seen_open, [], "routes that answered without credentials: add the gate or, on purpose, PUBLIC")
        self.assertEqual(bad, [])

    def test_a_wrong_or_empty_key_is_401_everywhere_and_never_the_data(self):
        for headers in ({"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}, {"Authorization": self.key},
                        {"Authorization": "Basic " + self.key}, {"Cookie": "ss_session=junk"}, {"Cookie": f"ss_session={self.key}"}):
            for path in ("/api/config", "/api/health/all", "/api/courage/memories", "/v1/models"):
                status, _, payload = call("GET", path, headers=headers, auth=False)
                self.assertEqual(status, 401, (path, headers))
                self.assertNotIn(self.key.encode(), payload)

    def test_the_public_list_is_public_and_only_it(self):
        self.assertEqual(call("GET", "/api/health", auth=False)[0], 200)
        status, _, payload = call("GET", "/api/health", auth=False)
        self.assertEqual(set(json.loads(payload)), {"ok", "version"}, "the public health answer says nothing about this install")
        self.assertEqual(call("POST", "/api/auth/logout", {}, auth=False)[0], 200)
        # the watch route keeps its own bearer (no token configured = refused), and answers its own 401, not the gate's
        status, _, payload = call("POST", "/api/watch/reply", {}, auth=False)
        self.assertEqual((status, json.loads(payload)["error"]), (401, "unauthorized"))
        self.assertEqual(call("POST", "/api/auth/login", {"key": "no"}, auth=False)[0], 401)
        # the detailed health stays behind the login
        self.assertEqual(call("GET", "/api/health/all", auth=False)[0], 401)
        self.assertEqual(call("GET", "/api/health/all", headers=self.bearer(), auth=False)[0], 200)

    @unittest.skipUnless(os.path.isdir(server.FRONTEND_DIR), "the web UI is not next to this install")
    def test_static_files_and_the_login_page_are_public_but_not_the_apk_or_directories(self):
        for path in ("/", "/index.html", "/login.html", "/js/auth.js", "/style.css", "/icon.svg", "/sw.js", "/manifest.webmanifest"):
            self.assertEqual(call("GET", path, auth=False)[0], 200, path)
        for path in ("/stonesage.apk", "/download", "/js/", "/js", "/nothing-here.txt", "/../backend/config.json",
                     "/%2e%2e/backend/config.json", "/js/../../backend/config.example.json"):
            self.assertEqual(call("GET", path, auth=False)[0], 401, path)

    def test_the_watch_reply_with_a_non_ascii_header_is_a_401_not_a_crash(self):
        with mock.patch.dict(server.config, {"watch_bridge": {"token": "t0ken"}}):
            status, _, _ = call("POST", "/api/watch/reply", {}, headers={"Authorization": "Bearer t\u00f6ken"}, auth=False)
        self.assertEqual(status, 401)

    def test_nothing_sends_a_wildcard_cors_header(self):
        for method, path in (("GET", "/api/health"), ("OPTIONS", "/api/config"), ("GET", "/api/config"), ("POST", "/api/auth/login")):
            status, hdrs, _ = call_full(method, path, {"key": "x"} if method == "POST" else None,
                                        headers={"Origin": "http://evil.example"}, auth=(path == "/api/config"))
            self.assertFalse([k for k, _ in hdrs if k.lower().startswith("access-control-")], (method, path, hdrs))
        status, hdrs, _ = call_full("OPTIONS", "/api/config", headers={"Origin": "http://evil.example",
                                                                       "Access-Control-Request-Method": "POST"}, auth=False)
        self.assertEqual(status, 204)

    def test_security_headers_are_on_every_answer(self):
        _, hdrs, _ = call_full("GET", "/api/health", auth=False)
        self.assertEqual(header(hdrs, "X-Content-Type-Options"), "nosniff")
        self.assertEqual(header(hdrs, "X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors", header(hdrs, "Content-Security-Policy"))

    def test_no_source_file_sets_a_wildcard_cors_header(self):
        hits = []
        for base in ("StoneSage/backend", "harness", "server setup", "StoneSage/docker"):
            for dirpath, _d, files in os.walk(os.path.join(ROOT, base)):
                if any(p in dirpath for p in ("__pycache__", "node_modules", "wildlife", ".git")):
                    continue
                for f in files:
                    if f.endswith((".py", ".js")):
                        text = open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore").read()
                        if re.search(r"Access-Control-Allow-Origin", text, re.I) and not f.startswith("test_"):
                            hits.append(os.path.relpath(os.path.join(dirpath, f), ROOT))
        self.assertEqual(hits, [], "a wildcard (or any) CORS header is back; the cockpit is same-origin only")


class TestTrustedHosts(Fresh):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(server.config, {"api": {"key": self.key, "trusted_hosts": [HA_HOST]}})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_trusted_machine_reaches_only_its_routes(self):
        for method, path in sorted(TRUSTED):
            if (method, path) == ("POST", "/api/chat"):       # runs a model turn: the gate decision is tested below
                continue
            status, _, _ = call(method, path, {} if method == "POST" else None, auth=False, client=(HA_HOST, 40000))
            self.assertNotEqual(status, 401, (method, path))
        for method, path in (("GET", "/api/config"), ("GET", "/api/courage/memories"), ("POST", "/api/config"),
                             ("POST", "/api/cluster/chat"), ("GET", "/api/health/all"), ("POST", "/api/terminal/exec"),
                             ("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("GET", "/api/souls")):
            status, _, _ = call(method, path, {} if method == "POST" else None, auth=False, client=(HA_HOST, 40000))
            self.assertEqual(status, 401, (method, path))

    def test_other_machines_get_nothing_for_free(self):
        for method, path in sorted(TRUSTED):
            status, _, _ = call(method, path, {} if method == "POST" else None, auth=False, client=(OTHER_HOST, 40000))
            self.assertEqual(status, 401, (method, path))

    def test_the_principal_is_the_machine_and_a_forwarded_header_changes_nothing(self):
        class Req:
            def __init__(self, ip, headers):
                self.headers, self.client_address, self.path = headers, (ip, 1), "/api/tags"
        r = Req(HA_HOST, {})
        self.assertEqual(auth.authenticate(r, server.config, "GET", "/api/tags"), (f"trusted:{HA_HOST}", "trusted"))
        r = Req(OTHER_HOST, {"X-Forwarded-For": HA_HOST, "X-Real-IP": HA_HOST, "Forwarded": f"for={HA_HOST}"})
        self.assertEqual(auth.authenticate(r, server.config, "GET", "/api/tags")[0], None)

    def test_cidr_blocks_and_ipv4_mapped_addresses(self):
        with mock.patch.dict(server.config, {"api": {"key": self.key, "trusted_hosts": ["10.0.0.0/28"]}}):
            self.assertNotEqual(call("GET", "/api/version", auth=False, client=("10.0.0.5", 1))[0], 401)
            self.assertNotEqual(call("GET", "/api/version", auth=False, client=("::ffff:10.0.0.5", 1))[0], 401)
            self.assertEqual(call("GET", "/api/version", auth=False, client=("10.0.0.40", 1))[0], 401)

    def test_nothing_is_trusted_by_default(self):
        with mock.patch.dict(server.config, {"api": {"key": self.key}}):
            self.assertEqual(call("GET", "/api/version", auth=False, client=(HA_HOST, 1))[0], 401)


class TestLoginAndCookie(Fresh):
    def login(self, key=None, **kw):
        return call_full("POST", "/api/auth/login", {"key": key or self.key}, auth=False, **kw)

    def cookie_of(self, hdrs):
        sc = header(hdrs, "Set-Cookie")
        return sc.split(";")[0] if sc else None

    def test_a_good_key_gives_a_cookie_with_the_right_flags(self):
        status, hdrs, payload = self.login()
        self.assertEqual((status, json.loads(payload)), (200, {"ok": True}))
        sc = header(hdrs, "Set-Cookie")
        self.assertRegex(sc, r"^ss_session=v1\.\d+\.[0-9a-f]{64};")
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=2592000"):
            self.assertIn(flag, sc)
        self.assertNotIn("Secure", sc, "plain HTTP: a Secure cookie would never come back")
        self.assertNotIn(self.key, sc, "the key itself is never in the cookie")
        _, hdrs_tls, _ = self.login(tls=True)
        self.assertIn("Secure", header(hdrs_tls, "Set-Cookie"), "over TLS (the :8443 listener) the cookie is Secure")

    def test_a_bad_key_gives_no_cookie(self):
        status, hdrs, payload = self.login("not-the-key")
        self.assertEqual(status, 401)
        self.assertIsNone(header(hdrs, "Set-Cookie"))
        for body in ({}, {"key": ""}, {"key": 5}, {"key": None}):
            self.assertEqual(call("POST", "/api/auth/login", body, auth=False)[0], 401)

    def test_the_cookie_works_on_get_and_on_a_same_origin_post(self):
        cookie = self.cookie_of(self.login()[1])
        self.assertEqual(call("GET", "/api/config", headers={"Cookie": cookie}, auth=False)[0], 200)
        same = {"Cookie": cookie, "Origin": "http://localhost:8888"}
        status, _, payload = call("POST", "/api/harness/session/stop", {}, headers=same, auth=False)
        self.assertEqual(status, 200, payload)
        # the browser's own signal works as well, on its own
        self.assertEqual(call("POST", "/api/harness/session/stop", {}, headers={"Cookie": cookie, "Sec-Fetch-Site": "same-origin"},
                              auth=False)[0], 200)

    def test_a_cookie_post_from_another_origin_is_refused(self):
        cookie = self.cookie_of(self.login()[1])
        for extra in ({"Origin": "http://evil.example"}, {"Origin": "http://localhost:9999"}, {"Origin": "null"},
                      {"Origin": "http://localhost.evil.example:8888"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                      {"Sec-Fetch-Site": "none"}, {"Origin": "http://localhost:8888", "Sec-Fetch-Site": "cross-site"}, {}):
            status, _, _ = call("POST", "/api/harness/session/stop", {}, headers={"Cookie": cookie, **extra}, auth=False)
            self.assertEqual(status, 403, extra)

    def test_a_bearer_needs_no_origin(self):
        status, _, _ = call("POST", "/api/harness/session/stop", {}, headers=self.bearer(), auth=False)
        self.assertEqual(status, 200)
        status, _, _ = call("POST", "/api/harness/session/stop", {}, headers={**self.bearer(), "Origin": "http://evil.example"}, auth=False)
        self.assertEqual(status, 200, "a bearer is not ambient: a page cannot make the browser send it")

    def test_login_from_another_origin_is_refused(self):
        status, _, _ = call("POST", "/api/auth/login", {"key": self.key}, headers={"Origin": "http://evil.example"}, auth=False)
        self.assertEqual(status, 403)

    def test_tampered_and_expired_cookies_are_401(self):
        good = auth.make_session(self.key)
        v, exp, sig = good.split(".")
        forged = [f"v1.{int(exp) + 999999}.{sig}", f"v1.{exp}.{'0' * 64}", f"v1.{exp}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}",
                  f"v2.{exp}.{sig}", f"{exp}.{sig}", "", "v1..", good + "x", auth.make_session("another-key"),
                  auth.make_session(self.key, now=time.time() - auth.SESSION_TTL - 10),
                  auth.make_session(self.key, now=time.time() + 10 * auth.SESSION_TTL)]
        for c in forged:
            self.assertEqual(call("GET", "/api/config", headers={"Cookie": f"ss_session={c}"}, auth=False)[0], 401, c)
        self.assertEqual(call("GET", "/api/config", headers={"Cookie": f"other=1; ss_session={good}"}, auth=False)[0], 200)

    def test_changing_the_key_ends_every_session(self):
        cookie = self.cookie_of(self.login()[1])
        with mock.patch.dict(server.config, {"api": {"key": self.key + "-rotated"}}):
            self.assertEqual(call("GET", "/api/config", headers={"Cookie": cookie}, auth=False)[0], 401)

    def test_logout_clears_the_cookie(self):
        status, hdrs, _ = call_full("POST", "/api/auth/logout", {}, auth=False)
        sc = header(hdrs, "Set-Cookie")
        self.assertEqual(status, 200)
        self.assertIn("ss_session=;", sc)
        self.assertIn("Max-Age=0", sc)

    def test_failed_logins_are_rate_limited_per_address(self):
        for _ in range(auth.THROTTLE.limit):
            self.assertEqual(self.login("wrong", client=("10.0.0.50", 1))[0], 401)
        status, hdrs, _ = self.login("wrong", client=("10.0.0.50", 1))
        self.assertEqual(status, 429)
        self.assertTrue(int(header(hdrs, "Retry-After")) > 0)
        self.assertEqual(self.login(client=("10.0.0.50", 1))[0], 429, "the right key does not get through a lock-out either")
        self.assertEqual(call("GET", "/api/config", headers=self.bearer(), auth=False, client=("10.0.0.50", 1))[0], 429,
                         "while locked, a guess is a guess: even the right bearer waits (else guessing would never be slowed)")
        self.assertEqual(call("GET", "/api/config", auth=False, client=("10.0.0.50", 1))[0], 401, "no credentials is not counted or locked")
        self.assertEqual(self.login(client=("10.0.0.51", 1))[0], 200, "another address is not affected")

    def test_wrong_bearer_keys_count_too(self):
        for _ in range(auth.THROTTLE.limit):
            call("GET", "/api/config", headers={"Authorization": "Bearer guess"}, auth=False, client=("10.0.0.60", 1))
        self.assertEqual(call("GET", "/api/config", headers=self.bearer(), auth=False, client=("10.0.0.60", 1))[0], 429)

    def test_the_throttle_forgets(self):
        t = auth.Throttle(limit=2, window=10, clock=lambda: now[0])
        now = [100.0]
        t.fail("a"); t.fail("a")
        self.assertGreater(t.blocked("a"), 0)
        now[0] = 111.0
        self.assertEqual(t.blocked("a"), 0)

    def test_whoami_needs_a_login_and_reports_the_principal(self):
        self.assertEqual(call("GET", "/api/auth/whoami", auth=False)[0], 401)
        status, _, payload = call("GET", "/api/auth/whoami", headers=self.bearer(), auth=False)
        self.assertEqual((status, json.loads(payload)["principal"]), (200, "owner"))

    def test_the_401_tells_the_page_to_log_in(self):
        _, hdrs, _ = call_full("GET", "/api/config", auth=False)
        self.assertEqual(header(hdrs, "X-StoneSage-Auth"), "login")
        self.assertEqual(header(hdrs, "WWW-Authenticate"), "Bearer")


class TestHostNames(Fresh):
    def test_unknown_names_are_refused_everywhere(self):
        for path in ("/api/health", "/", "/login.html", "/api/config"):
            status, _, _ = call("GET", path, headers={"Host": "evil.example:8888", **self.bearer()}, auth=False)
            self.assertEqual(status, 421, path)

    def test_addresses_and_localhost_pass(self):
        for host in ("localhost:8888", "LOCALHOST", "127.0.0.1:8888", "192.0.2.10:8888", "10.0.0.5", "[::1]:8888", "[2001:db8::1]:8443",
                     "app.localhost:8888"):
            self.assertEqual(call("GET", "/api/health", headers={"Host": host}, auth=False)[0], 200, host)

    def test_listed_names_pass(self):
        with mock.patch.dict(server.config, {"api": {"key": self.key, "allowed_hosts": ["Cockpit.Example.net"]}}):
            self.assertEqual(call("GET", "/api/health", headers={"Host": "cockpit.example.net:8888"}, auth=False)[0], 200)
            self.assertEqual(call("GET", "/api/health", headers={"Host": "cockpit.example.net."}, auth=False)[0], 200)
            self.assertEqual(call("GET", "/api/health", headers={"Host": "other.example.net"}, auth=False)[0], 421)
            self.assertEqual(call("GET", "/api/health", headers={"Host": "evil.cockpit.example.net"}, auth=False)[0], 421)

    def test_rebinding_names_that_look_like_addresses_are_names(self):
        for host in ("127.0.0.1.evil.example", "10.0.0.5.nip.example", "localhost.evil.example", "evil.example:8888@127.0.0.1"):
            self.assertEqual(call("GET", "/api/health", headers={"Host": host}, auth=False)[0], 421, host)

    def test_an_options_request_for_an_unknown_name_is_refused_too(self):
        self.assertEqual(call("OPTIONS", "/api/config", headers={"Host": "evil.example"}, auth=False)[0], 421)


class TestFeatureFlags(Fresh):
    ROUTES = {"web_terminal": [("POST", "/api/terminal/exec", {"command": "ls"})],
              "code_tools": [("POST", "/api/harness/tools/register", {"name": "x", "description": "y", "python_code": "x=1"}),
                             ("POST", "/api/harness/tools/execute", {"name": "x"}), ("POST", "/api/3d/execute", {"code": "1"})],
              "file_editor": [("GET", "/api/workspace/file?path=x.txt", None), ("GET", "/api/workspace/tree", None),
                              ("POST", "/api/workspace/save", {"path": "x.txt", "content": "a"}),
                              ("POST", "/api/workspace/diff", {"path": "x.txt", "content": "a"}),
                              ("POST", "/api/git/clone", {"url": "https://example.invalid/r.git"}),
                              ("POST", "/api/workspaces/roots/add", {"path": "/"})],
              "raw_ha_call": [("POST", "/api/hass/call", {"domain": "lock", "service": "unlock", "service_data": {"entity_id": "lock.x"}})]}

    @unittest.skipUnless(importlib.util.find_spec("hearth"), "the Home app is not installed")
    def test_the_raw_ha_route_is_a_home_route_with_its_flag(self):
        self.assertEqual(server.app_ctx.routes.feature("POST", "/api/hass/call"), "raw_ha_call")

    def test_each_route_is_404_while_its_flag_is_off(self):
        for flag, routes in self.ROUTES.items():
            if flag == "raw_ha_call" and not importlib.util.find_spec("hearth"):
                continue
            for method, path, body in routes:
                with self.subTest(flag=flag, path=path):
                    status, _, payload = call(method, path, body)                       # a valid login
                    self.assertEqual(status, 404)
                    data = json.loads(payload)
                    self.assertEqual((data["ok"], data["feature"]), (False, flag))
                    self.assertIn("features." + flag, data["error"])
                    # and still behind the login when it is off or on
                    self.assertEqual(call(method, path, body, auth=False)[0], 401)

    def test_off_means_off_for_false_and_for_anything_but_true(self):
        for value in (False, None, 0, "", "true", "yes", 1, [], {}):
            with mock.patch.dict(server.config, {"features": {"web_terminal": value}}):
                self.assertEqual(call("POST", "/api/terminal/exec", {"command": "ls"})[0], 404, repr(value))

    def test_the_four_flags_are_independent(self):
        with mock.patch.dict(server.config, {"features": {"file_editor": True}}):
            self.assertEqual(call("POST", "/api/terminal/exec", {"command": "ls"})[0], 404)
            self.assertNotEqual(call("GET", "/api/workspace/tree")[0], 404)

    def test_the_terminal_when_on_still_refuses_shell_tricks_and_needs_the_login(self):
        with mock.patch.dict(server.config, {"features": {"web_terminal": True}}):
            self.assertEqual(call("POST", "/api/terminal/exec", {"command": "ls"}, auth=False)[0], 401)
            with tempfile.TemporaryDirectory() as tmp:
                marker = os.path.join(tmp, "pwned")
                roots = [{"id": "t", "path": tmp}]
                with mock.patch.object(server, "get_workspace_roots", return_value=roots), \
                        mock.patch.object(server, "ACTIVE_WORKSPACE_DIR", tmp):
                    for cmd in (f"echo hi; touch {marker}", f"echo hi && touch {marker}", f"echo $(touch {marker})",
                                f"echo `touch {marker}`", f"echo hi > {marker}", "python -c \"import os\"", "curl http://127.0.0.1/",
                                f"git -c core.sshCommand=x status", "ls | cat", "sh -c ls", "../../bin/ls"):
                        status, _, payload = call("POST", "/api/terminal/exec", {"command": cmd})
                        data = json.loads(payload)
                        self.assertEqual(data["exit_code"], 126, (cmd, data))
                        self.assertIn("Security Policy", data["stderr"])
                    self.assertFalse(os.path.exists(marker))
                    # a cwd outside the workspace roots is refused
                    status, _, _ = call("POST", "/api/terminal/exec", {"command": "ls", "cwd": os.path.dirname(tmp)})
                    self.assertEqual(status, 403)

    def test_the_file_editor_is_confined_to_the_workspace_roots(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(root, "sub"))
            open(os.path.join(root, "ok.txt"), "w").write("inside")
            open(os.path.join(outside, "secret.txt"), "w").write("TOP-SECRET-DATA")
            open(os.path.join(root, "config.json"), "w").write('{"api": {"key": "k"}}')
            roots = [{"id": "t", "path": root}]
            try:
                os.symlink(outside, os.path.join(root, "link"))
                linked = True
            except (OSError, NotImplementedError):
                linked = False
            with mock.patch.dict(server.config, {"features": {"file_editor": True}}), \
                    mock.patch.object(server, "get_workspace_roots", return_value=roots), \
                    mock.patch.object(server, "ACTIVE_WORKSPACE_DIR", root):
                def read(p):
                    return call("GET", "/api/workspace/file?path=" + p.replace(" ", "%20").replace("\\", "/"))
                self.assertEqual(read("ok.txt")[0], 200)
                self.assertEqual(json.loads(read(os.path.join(root, "ok.txt"))[2])["content"], "inside")
                bad = ["../secret.txt", os.path.join(outside, "secret.txt"), "sub/../../ws2/x", "..", "/etc/passwd",
                       "C:/Windows/win.ini", "sub/../../" + os.path.basename(outside) + "/secret.txt", "config.json"]
                if linked:
                    bad.append("link/secret.txt")
                for p in bad:
                    status, _, payload = read(p)
                    self.assertEqual(status, 403, (p, payload))
                    self.assertNotIn(b"TOP-SECRET-DATA", payload)
                self.assertEqual(call("GET", "/api/workspace/tree?dir=" + outside.replace("\\", "/"))[0], 403)
                self.assertEqual(call("GET", "/api/workspace/tree")[0], 200)
                # writing: inside works, outside does not, and the secret file is not writable either
                self.assertEqual(call("POST", "/api/workspace/save", {"path": "sub/new.txt", "content": "n"})[0], 200)
                self.assertEqual(open(os.path.join(root, "sub", "new.txt")).read(), "n")
                for p in ("../evil.txt", os.path.join(outside, "evil.txt"), "config.json", "sub/../../evil2.txt"):
                    status, _, _ = call("POST", "/api/workspace/save", {"path": p, "content": "x"})
                    self.assertEqual(status, 403, p)
                self.assertFalse(os.path.exists(os.path.join(outside, "evil.txt")))
                self.assertFalse(os.path.exists(os.path.join(tmp, "evil.txt")))
                self.assertEqual(call("POST", "/api/workspace/diff", {"path": os.path.join(outside, "secret.txt"), "content": "x"})[0], 403)
                self.assertEqual(open(os.path.join(root, "config.json")).read(), '{"api": {"key": "k"}}')
                # git: no option rides in as a URL, nothing local, only the destination inside the roots
                for url in ("--upload-pack=touch x", "-u x", "file:///etc", "ext::sh -c id", "/srv/repo", "http://example.invalid/r",
                            "https://example.invalid/r.git --foo", ""):
                    self.assertEqual(call("POST", "/api/git/clone", {"url": url})[0], 400, url)
                self.assertEqual(call("POST", "/api/git/clone", {"url": "https://example.invalid/r.git", "dest": outside})[0], 403)

    def test_the_pty_daemon_does_not_start_when_the_flag_is_off(self):
        with mock.patch("threading.Thread") as thread:
            self.assertIsNone(server.start_pty_daemon({"features": {}}))
            self.assertIsNone(server.start_pty_daemon({}))
            self.assertIsNone(server.start_pty_daemon({"features": {"web_terminal": "yes"}}))
            thread.assert_not_called()

    def test_the_pty_daemon_when_on_listens_on_loopback_and_wants_a_login(self):
        started = {}

        class FakeServer:
            def __init__(self, host, port, authorize=None):
                started.update(host=host, port=port, authorize=authorize)

            def run(self):
                pass
        import harness.server as hs
        cfg = {"api": {"key": self.key}, "features": {"web_terminal": True}}
        with mock.patch.object(hs, "HarnessServer", FakeServer):
            t = server.start_pty_daemon(cfg)
            t.join(5)
        self.assertEqual((started["host"], started["port"]), ("127.0.0.1", 8088))
        ok = started["authorize"]
        self.assertTrue(ok({"Authorization": "Bearer " + self.key, "Host": "localhost:8088"}))
        self.assertFalse(ok({"Host": "localhost:8088"}), "no credentials")
        cookie = "ss_session=" + auth.make_session(self.key)
        self.assertTrue(ok({"Cookie": cookie, "Origin": "http://localhost:8888", "Host": "localhost:8088"}), "the page on :8888, this host")
        self.assertFalse(ok({"Cookie": cookie, "Host": "localhost:8088"}), "a cookie without an Origin")
        self.assertFalse(ok({"Cookie": cookie, "Origin": "http://evil.example", "Host": "localhost:8088"}), "cross-site WebSocket")
        self.assertFalse(ok({"Authorization": "Bearer nope", "Host": "localhost:8088"}))
        self.assertFalse(ok({"Authorization": "Bearer " + self.key, "Host": "evil.example:8088"}), "rebinding")

    def test_the_pty_server_closes_a_connection_that_is_not_authorised(self):
        import asyncio
        import harness.server as hs

        class WS:
            request = type("R", (), {"headers": {"Host": "localhost:8088"}})()
            closed = None

            async def close(self, code=1000, reason=""):
                WS.closed = code

            def __aiter__(self):
                raise AssertionError("must not read from an unauthorised socket")
        srv = hs.HarnessServer(authorize=lambda headers: False)
        asyncio.run(srv.handle_websocket(WS()))
        self.assertEqual(WS.closed, 1008)
        self.assertEqual(srv.host, "127.0.0.1", "loopback unless someone says otherwise")
        self.assertEqual(len(srv.connected_clients), 0)

    def test_code_tools_refuse_python_even_when_called_directly(self):
        from stonesage.tool_harness import tool_registry
        res = tool_registry.register_custom_tool("evil", "x", {"type": "object", "properties": {}}, python_code="import os\ndef execute(): pass")
        self.assertFalse(res["ok"])
        self.assertNotIn("evil", tool_registry.tools)


class TestApprovalsBelongToTheCaller(Fresh):
    def test_the_key_includes_the_principal_and_reserved_ids_are_ignored(self):
        class H:
            def __init__(self, principal, ip="10.0.0.9"):
                self.principal, self.client_address = principal, (ip, 1)
        owner, ha = H("owner"), H("trusted:10.0.0.7", "10.0.0.7")
        self.assertEqual(auth.approval_key(owner, "session_123"), "owner:session_123")
        self.assertEqual(auth.approval_key(ha, "session_123"), "trusted:10.0.0.7:session_123")
        self.assertNotEqual(auth.approval_key(owner, "x"), auth.approval_key(ha, "x"))
        for reserved in ("ha:10.0.0.7", "alexa:abc123", "api:ip:10.0.0.9", "trusted:10.0.0.7", "ip:10.0.0.9", "owner:x", "HA:1", " ha:1"):
            self.assertEqual(auth.approval_key(owner, reserved), "owner:ip:10.0.0.9", reserved)
        self.assertEqual(auth.approval_key(owner, ""), "owner:ip:10.0.0.9")
        self.assertEqual(auth.approval_key(H(None), "x"), "anon:x", "an unauthenticated caller can never share an owner's slot")

    def test_a_chat_session_id_cannot_reach_a_voice_assistants_pending_action(self):
        """The old key was the client's own string, so 'ha:<address>' answered Home Assistant's pending 'unlock the door?'."""
        class H:
            principal, client_address = "owner", ("10.0.0.9", 1)
        self.assertNotEqual(auth.approval_key(H(), "ha:10.0.0.7"), "ha:10.0.0.7")
        self.assertFalse(auth.approval_key(H(), "ha:10.0.0.7").startswith(("ha:", "alexa:")))

    def test_an_unauthenticated_client_can_never_answer_an_approval(self):
        for body in ({"messages": [{"role": "user", "content": "yes"}], "session_id": "ha:10.0.0.7"},
                     {"messages": [{"role": "user", "content": "yes"}], "session_id": "session_1"}, {}):
            for path in ("/api/cluster/chat", "/api/chat", "/v1/chat/completions", "/api/courage/approve"):
                status, _, _ = call("POST", path, body, auth=False)
                self.assertEqual(status, 401, path)
        # and not from a trusted machine on the chat route (it is not on its list)
        with mock.patch.dict(server.config, {"api": {"key": self.key, "trusted_hosts": [HA_HOST]}}):
            self.assertEqual(call("POST", "/api/cluster/chat", {"messages": []}, auth=False, client=(HA_HOST, 1))[0], 401)

    def test_no_open_route_lists_session_ids(self):
        for path in ("/api/chat/sessions", "/api/chat/sessions/messages?session_id=x", "/api/alexa-relay/status", "/api/courage/trace",
                     "/api/courage/memories"):
            self.assertEqual(call("GET", path, auth=False)[0], 401, path)


class TestTheKey(unittest.TestCase):
    def test_env_wins_over_config(self):
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": "from-env"}):
            self.assertEqual(auth.configured_key({"api": {"key": "from-config"}}), "from-env")
        with mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            self.assertEqual(auth.configured_key({"api": {"key": "from-config"}}), "from-config")
            self.assertEqual(auth.configured_key({}), "")

    def test_a_missing_key_is_generated_saved_private_and_never_logged(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            path = os.path.join(tmp, "config.json")
            json.dump({"server": {"port": 1}, "homeassistant": {"url": "http://192.0.2.1"}}, open(path, "w"))
            cfg = {"server": {"port": 1}}
            with self.assertLogs("StoneSage.auth", level="WARNING") as logs:
                key, generated = auth.ensure_key(cfg, path)
            self.assertTrue(generated)
            self.assertGreaterEqual(len(key), 40)
            saved = json.load(open(path))
            self.assertEqual(saved["api"]["key"], key)
            self.assertEqual(saved["homeassistant"]["url"], "http://192.0.2.1", "the rest of the file is kept")
            self.assertEqual(cfg["api"]["key"], key, "the live config has it too")
            if os.name != "nt":
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertNotIn(key, "\n".join(logs.output))
            self.assertIn("show-key", "\n".join(logs.output))
            key2, generated2 = auth.ensure_key(cfg, path)
            self.assertEqual((key2, generated2), (key, False), "never replaced once it exists")
            self.assertEqual(os.listdir(tmp), ["config.json"], "no temp file left behind")

    def test_no_file_yet_keeps_what_the_server_runs_on(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            path = os.path.join(tmp, "sub", "config.json")
            cfg = {"server": {"port": 9}, "apps": ["hearth"]}
            auth.ensure_key(cfg, path)
            saved = json.load(open(path))
            self.assertEqual((saved["server"]["port"], saved["apps"]), (9, ["hearth"]))

    def test_show_key_prints_the_key_and_says_where_it_came_from(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"STONESAGE_API_KEY": ""}):
            path = os.path.join(tmp, "config.json")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(auth.main(["show-key", "--config", path]), 1, "no key yet")
            json.dump({"api": {"key": "abc-123"}}, open(path, "w"))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(auth.main(["show-key", "--config", path]), 0)
            self.assertEqual(out.getvalue().strip(), "abc-123")
            self.assertIn(path, err.getvalue())

    def test_comparisons_are_constant_time(self):
        with mock.patch("stonesage.auth.hmac.compare_digest", wraps=auth.hmac.compare_digest) as cmp:
            self.assertTrue(auth.bearer_ok("Bearer k", "k"))
            self.assertFalse(auth.bearer_ok("Bearer x", "k"))
            self.assertTrue(auth.verify_session("k", auth.make_session("k")))
            self.assertGreaterEqual(cmp.call_count, 3)
        self.assertFalse(auth.bearer_ok("Bearer \u00f6", "k"), "non-ASCII is a no, not an exception")
        self.assertFalse(auth.bearer_ok("Bearer k", ""), "no key configured never matches")


if __name__ == "__main__":
    unittest.main()
