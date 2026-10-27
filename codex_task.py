#!/usr/bin/env python3
"""Delegate one bounded task from a Claude Code orchestrator to the local OpenAI Codex CLI.

Cross-platform (macOS, Linux, Windows), standard library only, Python 3.9+.
Every run gets an audit folder with the prompt, command, event stream, final message
and meta.json (model, effort, sandbox, network, Fast mode, session id, exit code,
git HEAD before/after, files changed during the run).

Usage:
  python codex_task.py --name <slug> --effort <low|medium|high|xhigh>
                       [--model <id>] [--network] [--fast|--no-fast]
                       [--resume <session_id>] [--wait <seconds>]
                       [--root <repo>] [--prompt-file <path>]   (prompt may also come on stdin)

Settings come from built-in defaults, then ~/.codex-worker/config.json, then
<repo>/.codex-worker.json (later wins). See README.md.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"

DEFAULTS = {
    # models the wrapper will accept; the first one is the default
    "allowed_models": ["gpt-6.1-sol", "gpt-6-astra"],
    "max_concurrent": 8,
    # Fast = Codex "priority" service tier; off unless --fast (or this is true)
    "fast_default": False,
    # set false to make --fast a no-op (a hard "Fast off" switch)
    "allow_fast": True,
    "codex_bin": "",                       # empty: auto-detect
    "runs_dir": ".codex_runs",             # relative to the repo root
    "preamble": "",                        # empty: preamble.md next to this script
    "extra_preambles": {},                 # flag name -> file, e.g. {"browser": "browser_preflight.md"}
    "lock_dir": "",                        # empty: ~/.codex-worker/locks (machine-wide slots)
    "require_git": True,
    "exclude_from_changes": [],            # extra path prefixes ignored in changed_files
}


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def load_config(root):
    cfg = dict(DEFAULTS)
    for p in (os.path.join(os.path.expanduser("~"), ".codex-worker", "config.json"),
              os.path.join(root, ".codex-worker.json")):
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    cfg.update(json.load(f))
            except Exception as e:
                die(f"Cannot read config {p}: {e}")
    return cfg


def find_root(start):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.abspath(out.stdout.strip())
    except FileNotFoundError:
        pass
    return os.path.abspath(start)


def find_codex(cfg):
    if cfg.get("codex_bin"):
        p = os.path.expanduser(cfg["codex_bin"])
        if os.path.isfile(p):
            return p
        die(f"Codex CLI not found at configured codex_bin: {p}")
    env = os.environ.get("CODEX_BIN")
    if env and os.path.isfile(env):
        return env
    for name in (["codex.cmd", "codex.exe", "codex"] if IS_WIN else ["codex"]):
        p = shutil.which(name)
        if p:
            return p
    home = os.path.expanduser("~")
    for p in ([os.path.join(os.environ.get("APPDATA", ""), "npm", "codex.cmd")] if IS_WIN
              else [os.path.join(home, ".local", "bin", "codex"), "/opt/homebrew/bin/codex", "/usr/local/bin/codex"]):
        if p and os.path.isfile(p):
            return p
    die("Codex CLI not found. Install it (npm install -g @openai/codex), run `codex login`, "
        "or set codex_bin in the config / CODEX_BIN.")


# ---- concurrency slots: a slot is a directory (mkdir is atomic everywhere) holding owner.json ----

def pid_alive(pid):
    if pid <= 0:
        return False
    if IS_WIN:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_slot(lock_dir, n, wait):
    os.makedirs(lock_dir, exist_ok=True)
    deadline = time.time() + wait
    while True:
        for i in range(1, n + 1):
            d = os.path.join(lock_dir, f"slot{i}.lock")
            try:
                os.mkdir(d)
            except FileExistsError:
                try:
                    with open(os.path.join(d, "owner.json"), encoding="utf-8") as f:
                        pid = int(json.load(f).get("pid", 0))
                except Exception:
                    # owner file not written yet or unreadable: treat as busy unless it is old
                    if time.time() - os.path.getmtime(d) < 30:
                        continue
                    pid = 0
                if pid_alive(pid):
                    continue
                stale = f"{d}.stale.{uuid.uuid4().hex}"
                try:
                    os.rename(d, stale)          # only one reclaimer wins the rename
                    shutil.rmtree(stale, ignore_errors=True)
                    os.mkdir(d)
                except OSError:
                    continue
            with open(os.path.join(d, "owner.json"), "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "since": utcnow()}, f)
            return d
        if time.time() >= deadline:
            return None
        time.sleep(3)


def utcnow(fmt="%Y-%m-%dT%H:%M:%SZ"):
    return datetime.datetime.now(datetime.timezone.utc).strftime(fmt)


# ---- dirty-state snapshot: path -> (status, content hash) for every dirty/untracked file ----

def snapshot(root, excludes):
    try:
        out = subprocess.run(["git", "status", "--porcelain", "-uall", "-z"], cwd=root,
                             capture_output=True)
    except FileNotFoundError:
        return {}
    if out.returncode != 0:
        return {}
    snap = {}
    parts = out.stdout.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        st, p = e[:2], e[3:]
        if st[0] in "RC":            # rename/copy: the source path follows; skip it
            i += 1
        if any(p.replace("\\", "/").startswith(x) for x in excludes):
            continue
        fp = os.path.join(root, p)
        h = "-"
        if os.path.isfile(fp):
            try:
                with open(fp, "rb") as f:
                    h = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                h = "?"
        snap[p] = [st, h]
    return snap


def git_head(root):
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else "none"
    except FileNotFoundError:
        return "none"


def scan_events(path):
    thread_id, service_tier = None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not thread_id and ev.get("type") == "thread.started":
                    thread_id = ev.get("thread_id")
                if service_tier is None and "service_tier" in ev:
                    service_tier = ev["service_tier"]
    except OSError:
        pass
    return thread_id, service_tier


def main():
    ap = argparse.ArgumentParser(description="Run one Codex CLI task with an audit trail.")
    ap.add_argument("--name", required=True)
    ap.add_argument("--effort", required=True, choices=["low", "medium", "high", "xhigh"])
    ap.add_argument("--model")
    ap.add_argument("--network", action="store_true")
    ap.add_argument("--fast", dest="fast", action="store_true", default=None)
    ap.add_argument("--no-fast", dest="fast", action="store_false")
    ap.add_argument("--resume", default="")
    ap.add_argument("--wait", type=int, default=0)
    ap.add_argument("--root", default="")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--with", dest="extras", action="append", default=[],
                    help="prepend a named extra preamble from the config (e.g. --with browser)")
    a = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", a.name):
        die("--name must be a slug [A-Za-z0-9._-], max 64 chars")
    if a.wait < 0:
        die("--wait must be >= 0")

    root = find_root(a.root or os.getcwd())
    cfg = load_config(root)
    if cfg["require_git"] and not os.path.isdir(os.path.join(root, ".git")) and git_head(root) == "none":
        die(f"Not a git repository: {root} (set require_git false to allow)")

    allowed = cfg["allowed_models"]
    model = a.model or allowed[0]
    if model not in allowed:
        die(f"Model '{model}' is not permitted (allowed: {', '.join(allowed)})")
    fast = cfg["fast_default"] if a.fast is None else a.fast
    if fast and not cfg["allow_fast"]:
        print("Fast is switched off in the config (allow_fast=false); running without it.", file=sys.stderr)
        fast = False

    codex = find_codex(cfg)
    preamble = os.path.expanduser(cfg["preamble"]) if cfg["preamble"] else os.path.join(HERE, "preamble.md")
    if not os.path.isabs(preamble):
        preamble = os.path.join(root, preamble)
    if not os.path.isfile(preamble):
        die(f"Missing preamble {preamble}")
    extra_files = []
    for x in a.extras:
        p = cfg["extra_preambles"].get(x)
        if not p:
            die(f"--with {x}: no such entry in extra_preambles")
        p = os.path.expanduser(p)
        p = p if os.path.isabs(p) else os.path.join(root, p)
        if not os.path.isfile(p):
            die(f"Missing extra preamble {p}")
        extra_files.append(p)

    if a.prompt_file:
        if not os.path.isfile(a.prompt_file):
            die(f"Prompt file not found: {a.prompt_file}")
        with open(a.prompt_file, encoding="utf-8") as f:
            user_prompt = f.read()
    elif not sys.stdin.isatty():
        user_prompt = sys.stdin.read()
    else:
        user_prompt = ""
    if not user_prompt.strip():
        die("Empty prompt (use --prompt-file or stdin)")

    lock_dir = os.path.expanduser(cfg["lock_dir"]) if cfg["lock_dir"] else os.path.join(os.path.expanduser("~"), ".codex-worker", "locks")
    slot = acquire_slot(lock_dir, int(cfg["max_concurrent"]), a.wait)
    if not slot:
        die(f"Concurrency limit reached ({cfg['max_concurrent']} Codex runs active). Retry later or use --wait <seconds>.", 75)

    try:
        runs_dir = cfg["runs_dir"] if os.path.isabs(cfg["runs_dir"]) else os.path.join(root, cfg["runs_dir"])
        os.makedirs(runs_dir, exist_ok=True)
        ts = utcnow("%Y%m%dT%H%M%SZ")
        run_dir = os.path.join(runs_dir, f"{ts}_{a.name}")
        n = 1
        while True:
            try:
                os.mkdir(run_dir)
                break
            except FileExistsError:
                n += 1
                run_dir = os.path.join(runs_dir, f"{ts}_{a.name}-{n}")

        with open(os.path.join(run_dir, "prompt.md"), "w", encoding="utf-8", newline="\n") as f:
            for p in [preamble] + extra_files:
                with open(p, encoding="utf-8") as g:
                    f.write(g.read().rstrip("\n") + "\n\n")
            f.write(user_prompt.rstrip("\n") + "\n")

        rel_runs = os.path.relpath(runs_dir, root).replace("\\", "/")
        excludes = [rel_runs.rstrip("/") + "/"] + list(cfg["exclude_from_changes"])
        before = snapshot(root, excludes)
        head_before = git_head(root)
        start = utcnow()
        is_batch = IS_WIN and codex.lower().endswith((".cmd", ".bat"))
        version_cmd = _batch([codex, "--version"]) if is_batch else [codex, "--version"]
        try:
            version = subprocess.run(version_cmd, capture_output=True, text=True).stdout.strip().splitlines()[0]
        except Exception:
            version = "unknown"

        quote = "" if IS_WIN else '"'
        cmd = [codex, "exec"] + (["resume"] if a.resume else []) + [
            "-m", model,
            "-c", f'model_reasoning_effort={quote}{a.effort}{quote}',
            "-c", f'sandbox_mode={quote}workspace-write{quote}',
            "-c", f'approval_policy={quote}never{quote}',
            "-c", f'service_tier={quote}{"priority" if fast else "default"}{quote}',
            "-c", f"sandbox_workspace_write.network_access={'true' if a.network else 'false'}",
            "--json", "-o", os.path.join(run_dir, "last_message.md"),
        ]
        if not a.resume:
            cmd += ["-C", root]       # `codex exec resume` does not accept -C
        else:
            cmd += [a.resume]
        cmd += ["-"]
        if is_batch:
            cmd = _batch(cmd)
            cmd_txt = cmd
        else:
            cmd_txt = subprocess.list2cmdline(cmd) if IS_WIN else " ".join(_q(c) for c in cmd)
        with open(os.path.join(run_dir, "command.txt"), "w", encoding="utf-8") as f:
            f.write(cmd_txt + " < prompt.md\n")

        with open(os.path.join(run_dir, "prompt.md"), "rb") as fin, \
                open(os.path.join(run_dir, "events.jsonl"), "wb") as fout, \
                open(os.path.join(run_dir, "stderr.log"), "wb") as ferr:
            try:
                rc = subprocess.run(cmd, cwd=root, stdin=fin, stdout=fout, stderr=ferr).returncode
            except KeyboardInterrupt:
                rc = 130

        end = utcnow()
        after = snapshot(root, excludes)
        head_after = git_head(root)
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        thread_id, tier = scan_events(os.path.join(run_dir, "events.jsonl"))
        meta = {
            "name": a.name, "run_dir": run_dir, "cwd": root,
            "start_utc": start, "end_utc": end,
            "codex_bin": codex, "codex_version": version, "model": model,
            "reasoning_effort": a.effort, "sandbox": "workspace-write", "approval_policy": "never",
            "network": a.network,
            "extra_preambles": a.extras,
            "fast_mode": "on (service_tier=priority)" if fast else "off (service_tier=default)",
            "service_tier_reported": tier,
            "resumed_session_id": a.resume or None,
            "session_id": thread_id or (a.resume or None),
            "exit_code": rc,
            "git_head_before": head_before, "git_head_after": head_after,
            "git_head_changed": head_before != head_after,
            "changed_files": changed,
            "command": cmd_txt,
            "wrapper": "codex-worker/codex_task.py",
        }
        with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
            f.write("\n")
    finally:
        shutil.rmtree(slot, ignore_errors=True)

    print(f"RUN_DIR: {run_dir}")
    print(f"EXIT_CODE: {rc}")
    print(f"SESSION_ID: {meta['session_id']}")
    print("----- LAST MESSAGE -----")
    lm = os.path.join(run_dir, "last_message.md")
    if os.path.isfile(lm) and os.path.getsize(lm):
        with open(lm, encoding="utf-8", errors="replace") as f:
            text = f.read()
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        print(f"(no last message; see {os.path.join(run_dir, 'stderr.log')})")
    sys.exit(rc)


def _q(s):
    return s if re.fullmatch(r"[A-Za-z0-9_./:=@%+-]+", s) else "'" + s.replace("'", "'\\''") + "'"


def _cmdline(args):
    parts = []
    for arg in args:
        if "%" in arg or "!" in arg:
            die("Arguments for codex.cmd/.bat cannot contain % or !; "
                "set codex_bin to codex.exe or the node entry point.")
        if '"' in arg:
            die('Arguments for codex.cmd/.bat cannot contain double quotes.')
        parts.append('"' + arg + '"' if not arg or re.search(r"[ \t&|<>^()]", arg) else arg)
    return " ".join(parts)


def _batch(args):
    # /d skips the cmd.exe AutoRun hook (e.g. conda/mamba init), which can swallow redirected stdin;
    # /s makes cmd strip only the outer quotes and run the rest verbatim
    return f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c "{_cmdline(args)}"'


if __name__ == "__main__":
    main()
