#!/usr/bin/env python3
"""Stand-in for the Codex CLI used by test_wrapper.py: no network, no quota.

`--version` prints a version. `exec ...` reads the prompt from stdin, prints JSON events,
writes the -o last-message file, and, if the prompt contains WRITE_FILE=<name>,
creates that file in the working directory so changed_files can be tested.
FAKE_SLEEP (seconds) delays the run; FAKE_RC sets the exit code.
"""
import json
import os
import re
import sys
import time

args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("codex-cli 0.0.0-fake")
    sys.exit(0)
assert args[:1] == ["exec"], args
prompt = sys.stdin.read()
out = args[args.index("-o") + 1]
resume = "resume" in args
tid = args[args.index("-") - 1] if resume else "fake-thread-0001"
print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
m = re.search(r"WRITE_FILE=(\S+)", prompt)
if m:
    with open(m.group(1), "w", encoding="utf-8") as f:
        f.write("written by fake codex\n")
with open(out, "w", encoding="utf-8") as f:
    f.write("FAKE DONE\nargs=" + json.dumps(args) + "\n")
print(json.dumps({"type": "turn.completed"}), flush=True)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
