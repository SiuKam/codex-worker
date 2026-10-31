"""Tests for codex_task.py with a fake Codex CLI (no network, no quota).

Run:  python -m unittest discover -s tests -v     (from the project folder)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WRAPPER = os.path.join(os.path.dirname(HERE), "codex_task.py")
IS_WIN = os.name == "nt"

sys.path.insert(0, os.path.dirname(HERE))
import codex_task  # noqa: E402


class CmdlineTest(unittest.TestCase):
    def test_quoting(self):
        self.assertEqual(codex_task._cmdline(["plain", "with space", r"C:\R&D\proj"]),
                         'plain "with space" "C:\\R&D\\proj"')
        for arg in ("with\ttab", "a|b", "a<b", "a>b", "a^b", "a(b", "a)b"):
            with self.subTest(arg=arg):
                self.assertEqual(codex_task._cmdline([arg]), '"' + arg + '"')

    def test_rejected_characters(self):
        for arg in ("path%name", "path!name", 'with"quote'):
            with self.subTest(arg=arg), self.assertRaises(SystemExit) as exc:
                codex_task._cmdline([arg])
            self.assertEqual(exc.exception.code, 2)


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="codexworker_")
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.home, ".codex-worker"))
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
                       cwd=self.repo, check=True)
        # launcher for the fake CLI
        fake = os.path.join(HERE, "fake_codex.py")
        if IS_WIN:
            self.codex = os.path.join(self.tmp, "codex.cmd")
            with open(self.codex, "w") as f:
                f.write(f'@"{sys.executable}" "{fake}" %*\n')
        else:
            self.codex = os.path.join(self.tmp, "codex")
            with open(self.codex, "w") as f:
                f.write(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
            os.chmod(self.codex, 0o755)
        self.config({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, extra):
        cfg = {"codex_bin": self.codex, "lock_dir": os.path.join(self.tmp, "locks")}
        cfg.update(extra)
        with open(os.path.join(self.home, ".codex-worker", "config.json"), "w") as f:
            json.dump(cfg, f)

    def run_wrapper(self, *args, prompt="do the thing", env=None, background=False):
        e = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        for k in ("CODEX_BIN", "CODEX_API_KEY", "OPENAI_API_KEY"):
            e.pop(k, None)
        e.update(env or {})
        cmd = [sys.executable, WRAPPER, *args]
        if background:
            p = subprocess.Popen(cmd, cwd=self.repo, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            p.stdin.write(prompt)
            p.stdin.close()
            return p
        return subprocess.run(cmd, cwd=self.repo, env=e, input=prompt, capture_output=True, text=True)

    def meta(self, out):
        rd = [l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("RUN_DIR: ")][0]
        with open(os.path.join(rd, "meta.json"), encoding="utf-8") as f:
            return rd, json.load(f)

    def test_basic_run(self):
        r = self.run_wrapper("--name", "t1", "--effort", "low", prompt="hello WRITE_FILE=out.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FAKE DONE", r.stdout)
        rd, m = self.meta(r.stdout)
        self.assertEqual(m["model"], "gpt-6.1-sol")
        self.assertEqual(m["session_id"], "fake-thread-0001")
        self.assertEqual(m["fast_mode"], "off (service_tier=default)")
        self.assertEqual(m["changed_files"], ["out.txt"])
        self.assertFalse(m["git_head_changed"])
        with open(os.path.join(rd, "prompt.md"), encoding="utf-8") as f:
            p = f.read()
        self.assertIn("ASSIGNED TASK BELOW", p)
        self.assertTrue(p.rstrip().endswith("WRITE_FILE=out.txt"))
        self.assertTrue(os.path.realpath(rd).startswith(os.path.join(os.path.realpath(self.repo), ".codex_runs")))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "locks")), [])

    def test_model_policy(self):
        r = self.run_wrapper("--name", "t2", "--effort", "low", "--model", "other-model")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not permitted", r.stderr)

    def test_fast_switch(self):
        r = self.run_wrapper("--name", "t3", "--effort", "low", "--fast")
        _, m = self.meta(r.stdout)
        self.assertEqual(m["fast_mode"], "on (service_tier=priority)")
        self.assertIn('service_tier=priority' if IS_WIN else 'service_tier="priority"', m["command"])
        self.config({"allow_fast": False})
        r = self.run_wrapper("--name", "t3b", "--effort", "low", "--fast")
        _, m = self.meta(r.stdout)
        self.assertEqual(m["fast_mode"], "off (service_tier=default)")

    def test_resume(self):
        r = self.run_wrapper("--name", "t4", "--effort", "medium", "--resume", "abc-123")
        self.assertEqual(r.returncode, 0, r.stderr)
        _, m = self.meta(r.stdout)
        self.assertEqual(m["session_id"], "abc-123")
        self.assertIn(" resume ", " " + m["command"] + " ")
        self.assertNotIn(" -C ", m["command"])

    def test_exit_code_passthrough(self):
        r = self.run_wrapper("--name", "t5", "--effort", "low", env={"FAKE_RC": "3"})
        self.assertEqual(r.returncode, 3)
        _, m = self.meta(r.stdout)
        self.assertEqual(m["exit_code"], 3)

    def test_concurrency_and_stale_lock(self):
        self.config({"max_concurrent": 1})
        # a stale slot left by a dead process is reclaimed
        stale = os.path.join(self.tmp, "locks", "slot1.lock")
        os.makedirs(stale)
        with open(os.path.join(stale, "owner.json"), "w") as f:
            json.dump({"pid": 999999}, f)
        busy = self.run_wrapper("--name", "t6", "--effort", "low", env={"FAKE_SLEEP": "4"}, background=True)
        time.sleep(1.5)
        r = self.run_wrapper("--name", "t6b", "--effort", "low")
        self.assertEqual(r.returncode, 75, r.stderr)
        busy.wait(timeout=30)
        err = busy.stderr.read()
        busy.stdout.close()
        busy.stderr.close()
        self.assertEqual(busy.returncode, 0, err)
        r = self.run_wrapper("--name", "t6c", "--effort", "low")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_empty_prompt(self):
        r = self.run_wrapper("--name", "t7", "--effort", "low", prompt="   ")
        self.assertEqual(r.returncode, 2)

    def test_auth_api(self):
        r = self.run_wrapper("--name", "t8", "--effort", "low", "--auth", "api")
        self.assertEqual(r.returncode, 2)
        self.assertIn("OPENAI_API_KEY is not set", r.stderr)
        r = self.run_wrapper("--name", "t8b", "--effort", "low", "--auth", "api",
                             env={"OPENAI_API_KEY": "sk-test-secret"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auth=api", r.stdout)
        rd, m = self.meta(r.stdout)
        self.assertEqual(m["auth"], "api key (OPENAI_API_KEY)")
        self.assertEqual(m["usage"], {"input_tokens": 100, "output_tokens": 7})
        for fn in os.listdir(rd):
            with open(os.path.join(rd, fn), encoding="utf-8", errors="replace") as f:
                self.assertNotIn("sk-test-secret", f.read(), fn)

    def test_auth_login_strips_api_key(self):
        r = self.run_wrapper("--name", "t9", "--effort", "low", env={"CODEX_API_KEY": "sk-stray"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auth=login", r.stdout)
        _, m = self.meta(r.stdout)
        self.assertEqual(m["auth"], "codex login")
        self.config({"auth": "api", "api_key_env": "MY_KEY"})
        r = self.run_wrapper("--name", "t9b", "--effort", "low", env={"MY_KEY": "k"})
        self.assertIn("auth=api", r.stdout)
        r = self.run_wrapper("--name", "t9c", "--effort", "low", "--auth", "login", env={"MY_KEY": "k"})
        self.assertIn("auth=login", r.stdout)


if __name__ == "__main__":
    unittest.main()
