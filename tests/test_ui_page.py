"""The platform's side of "a fresh install shows only what is real" (docs/LAYERS.md, M6 rc.2), offline: the page as shipped
(index.html starts bare; the Node checks of net.js / features.js / the panels) and GET /api/ui/features without any app.
The Home app's block and the appliance discovery are in test_ui_features.py (they need Hearth)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from stonesage import ui_features  # noqa: E402

FRESH = {"cluster": {"coordinator_url": "http://127.0.0.1:8001/v1"}}


def build(cfg, apps=(), home=None, **kw):
    with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.endswith(("_URL", "_KEY", "_HOST", "_VAULT", "_PATH"))},
                         clear=True), tempfile.TemporaryDirectory() as tmp:
        return ui_features.build(cfg, "9.9.9", list(apps), tmp, home, **kw)


class TestPlatformFeatures(unittest.TestCase):
    def test_nothing_optional_is_on_without_any_app(self):
        f = build(FRESH)
        self.assertEqual((f["product"], f["version"], f["apps"], f["home"]), ("StoneSage", "9.9.9", [], {}))
        self.assertEqual(f["agent"], {"id": "courage-computer", "name": "Computer"})
        self.assertFalse(any(f["features"].values()))
        self.assertFalse(f["obsidian"] or any(f["configured"].values()) or f["providers"]["gemini"])
        self.assertIsNone(f["usage"])

    def test_the_agent_is_named_by_its_soul(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "souls", "courage-computer"))
            with open(os.path.join(tmp, "souls", "courage-computer", "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"id": "courage-computer", "name": "Atlas"}, fh)
            self.assertEqual(ui_features.agent_name(tmp), "Atlas")

    def test_a_broken_app_hook_does_not_blank_the_page(self):
        def boom():
            raise RuntimeError("no")
        f = build(FRESH, apps=("hearth",), home=boom)
        self.assertTrue(f["ok"])
        self.assertIn("RuntimeError", f["home"]["error"])

    def test_what_is_set_shows(self):
        f = build({"features": {"file_editor": True, "shelved": True}, "obsidian": {"user_vault_path": "/notes"},
                   "cluster": {"worker_url": "http://10.0.0.5:8002/v1"}, "external_providers": {"gemini": {"api_key": "k"}}})
        self.assertTrue(f["features"]["file_editor"] and f["features"]["shelved"] and not f["features"]["web_terminal"])
        self.assertTrue(f["obsidian"] and f["providers"]["gemini"] and f["configured"]["worker"])
        self.assertFalse(f["configured"]["qdrant"])

    def test_the_obsidian_tab_also_follows_the_couchdb_it_syncs_through(self):
        self.assertTrue(build({"couchdb": {"url": "http://10.0.0.7:5984"}})["obsidian"])

    def test_a_flag_must_be_literally_true(self):
        self.assertFalse(build({"features": {"file_editor": "yes"}})["features"]["file_editor"])


class TestTokenTally(unittest.TestCase):
    class Router:
        def __init__(self, enabled, rows):
            self.enabled, self.rows = enabled, rows

        def status(self):
            return {"enabled": self.enabled, "providers": self.rows}

    def test_only_cloud_tokens_from_the_boost_quota_book(self):
        r = self.Router(True, [{"tier": "no_training", "usage": {"tokens_today": 1200}},
                               {"tier": "training", "usage": {"tokens_today": 300}},
                               {"tier": "local", "usage": {"tokens_today": 9000}}])
        self.assertEqual(ui_features.cloud_tokens_today(r), 1500)
        self.assertEqual(build(FRESH, boost_router=r)["usage"], {"cloud_tokens_today": 1500})

    def test_no_count_is_better_than_a_made_up_one(self):
        self.assertIsNone(ui_features.cloud_tokens_today(None))
        self.assertIsNone(ui_features.cloud_tokens_today(self.Router(False, [{"tier": "no_training", "usage": {"tokens_today": 5}}])))
        self.assertIsNone(ui_features.cloud_tokens_today(self.Router(True, [{"tier": "no_training", "usage": {"tokens_today": 0}}])))
        self.assertIsNone(ui_features.cloud_tokens_today(object()))


class TestTheRoute(unittest.TestCase):
    def test_it_is_registered_and_behind_the_login(self):
        from route_harness import call
        from stonesage import server
        table = {(r["method"], r["path"]): r["app"] for r in server.app_ctx.routes.table()}
        self.assertEqual(table.get(("GET", "/api/ui/features")), "stonesage")
        self.assertEqual(call("GET", "/api/ui/features", auth=False)[0], 401)
        status, ctype, payload = call("GET", "/api/ui/features")
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertEqual(data["product"], "StoneSage")
        self.assertEqual(data["version"], server.APP_VERSION)
        for key in ("apps", "agent", "features", "configured", "obsidian", "providers", "usage", "home"):
            self.assertIn(key, data)


FRONTEND = os.path.join(ROOT, "StoneSage", "frontend")


def tag(html, element_id):
    m = re.search(r'<[a-z0-9]+\s[^>]*id="%s"[^>]*>' % re.escape(element_id), html)
    assert m, f"no element with id {element_id}"
    return m.group(0)


class TestTheShippedPage(unittest.TestCase):
    """index.html starts bare: parts only some installs have are hidden in the markup and shown from data."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FRONTEND, "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_everything_that_needs_a_feature_starts_hidden(self):
        needs = re.findall(r'<[a-z0-9]+\s[^>]*data-needs="[^"]+"[^>]*>', self.html)
        self.assertGreaterEqual(len(needs), 7)
        for t in needs:
            self.assertRegex(t, r"hidden", t)

    def test_the_tabs_a_fresh_install_lacks_are_tied_to_their_feature(self):
        for tab, need in (("workstation", "file_editor"), ("terminal", "web_terminal"), ("obsidian", "obsidian"), ("ha", "app:hearth")):
            t = re.search(r'<button[^>]*data-tab="%s"[^>]*>' % tab, self.html).group(0)
            self.assertIn(f'data-needs="{need}"', t)

    def test_the_citadel_and_nudge_buttons_follow_the_shelved_flag(self):
        for i in ("topbar-citadel-link", "mobile-citadel-link", "topbar-nudge-btn"):
            self.assertIn('data-needs="shelved"', tag(self.html, i))

    def test_panels_with_a_source_start_hidden_and_readings_start_empty(self):
        for i in ("presence-appliances-wrap", "irrigation-card", "sightings-card", "topbar-tokens-badge", "brand-version", "brand-app",
                  "irrigation-battery-badge", "irrigation-rain-delay-btn"):
            self.assertRegex(tag(self.html, i), r"hidden", i)
        self.assertNotIn("thermostat-current-temp", self.html)            # the thermostat cards are made from Home Assistant's entities
        self.assertIn('id="thermostat-list"', self.html)
        self.assertNotRegex(self.html, r"RAIN DELAY: OFF|Battery: \d|HAOS \(ONLINE\)")

    def test_the_header_is_the_product_not_a_version_made_up_in_markup(self):
        self.assertIn("<title>StoneSage</title>", self.html)
        self.assertNotRegex(self.html, r"v4\.0|Enterprise|AGENT HARNESS")
        self.assertEqual(tag(self.html, "brand-version").count("hidden"), 1)
        self.assertNotRegex(re.search(r'<span id="brand-version"[^>]*>(.*?)</span>', self.html, re.S).group(1), r"\d")

    def test_the_page_loads_the_request_sharing_script_first(self):
        scripts = re.findall(r'<script[^>]+src="(/js/[^"?]+)', self.html)
        self.assertEqual(scripts[:2], ["/js/auth.js", "/js/net.js"])

    def test_hidden_wins_over_a_class_that_sets_display(self):
        with open(os.path.join(FRONTEND, "style.css"), encoding="utf-8") as fh:
            self.assertRegex(fh.read(), r"\[hidden\]\s*\{\s*display:\s*none\s*!important")


class TestPageScripts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_headless_checks_pass(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "fresh_install.test.mjs"), FRONTEND],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
