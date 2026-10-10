"""Golden answers of the platform's own GET routes (docs/LAYERS.md M1): the next steps move route code by layer (foundation
routes out of server.py, then separate repos), and each route must answer as before. Offline, under the network guard:
status, content type, top-level JSON keys. The route list is read from server.py itself, so a new GET route that is not
in the golden fails until the golden is regenerated on purpose:

    STONESAGE_WRITE_GOLDEN=1 python tests/run_tests.py
"""

import ast
import json
import os
import shutil
import time
import unittest

from route_harness import ROOT, shape

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
GOLDEN = os.path.join(FIXTURES, "platform_routes_golden.json")
# The config the routes run on: platform sections only, so the answer is the same in the full repo and in the platform's
# own project (an installed package has no config.example.json next to it, and the example holds Home's sections).
CONFIG = os.path.join(FIXTURES, "platform_routes_config.json")
# Routes left out, each with its reason. Their answer depends on the machine, so no golden could hold everywhere:
#   /api/dataset/download serves StoneSage/datasets/*, which is private (not in the public snapshot CI runs on):
#   200 with a file on the operator's machine, 404 in CI (found 2026-10-10 by running this test on the public tree).
SKIP = {"/api/dataset/download"}     # (/api/profiles moved to the Home app, 2026-10-10)


def platform_get_routes():
    """Every exact /api/ path the platform's do_GET compares against (app routes are covered by their own golden)."""
    import importlib.util
    src = open(importlib.util.find_spec("stonesage.server").origin, encoding="utf-8").read()   # however installed
    handler = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "StoneSageHandler")
    get = next(f for f in handler.body if isinstance(f, ast.FunctionDef) and f.name == "_do_get")
    paths = set()
    for x in ast.walk(get):
        if isinstance(x, ast.Compare) and isinstance(x.left, ast.Name) and x.left.id in ("path", "clean_path"):
            for c in x.comparators:
                elts = c.elts if isinstance(c, (ast.Tuple, ast.List)) else [c]
                paths |= {e.value for e in elts if isinstance(e, ast.Constant) and str(e.value).startswith("/api/")}
    return sorted(paths - SKIP)


class TestPlatformRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest import mock
        from stonesage import server
        cls.routes = platform_get_routes()
        # a fresh config of its own: other tests save into the run's shared scratch config (workspace roots...), and a
        # golden must not depend on which tests ran first (found 2026-10-10: /api/config had an extra key locally)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "config.json")
            shutil.copyfile(CONFIG, cfg)                 # a copy: a route that saves must not touch the fixture
            with mock.patch.object(server, "CONFIG_FILE", cfg):
                t = time.time()
                cls.now = {p: shape("GET", p) for p in cls.routes}
                cls.elapsed = time.time() - t
        if os.environ.get("STONESAGE_WRITE_GOLDEN") == "1":
            with open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cls.now, f, indent=1, sort_keys=True)
        with open(GOLDEN, encoding="utf-8") as f:
            cls.golden = json.load(f)

    def test_every_platform_get_route_answers_as_before(self):
        diffs = {p: {"was": self.golden.get(p), "now": v} for p, v in self.now.items() if self.golden.get(p) != v}
        self.assertEqual(diffs, {}, "a platform route answers differently (fix it, or regenerate the golden on purpose)")

    def test_the_golden_covers_every_route_and_no_ghosts(self):
        self.assertEqual(sorted(self.golden), self.routes)

    def test_every_get_answers_something(self):
        silent = [p for p, v in self.now.items() if not v.get("status")]
        self.assertEqual(silent, [], "these GET routes answered nothing (a dropped connection)")

    def test_no_route_hangs(self):
        self.assertLess(self.elapsed, 90, "offline, every platform GET must fail fast")


if __name__ == "__main__":
    unittest.main()
