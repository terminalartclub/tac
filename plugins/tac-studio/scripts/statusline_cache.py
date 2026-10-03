"""Opt-in statusline helper: caches rate_limits.seven_day to ~/.cache/tac/usage.json.

It does NOT replace your statusline. Wrap your existing one:

    "statusLine": {"type": "command",
                   "command": "python3 ~/.claude/plugins/data/tac-terminalartclub/statusline_cache.py -- <your existing statusline command>"}

It reads the session JSON from stdin, writes the cache (atomically, only when the values change),
then runs your command with the same stdin and prints its output. With nothing after `--` it prints
nothing. `rate_limits` only exists for claude.ai Pro/Max subscribers after the first API response,
so the cache stays untouched otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def cache_path() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tac" / "usage.json"


def update_cache(payload: dict) -> None:
    week = (payload.get("rate_limits") or {}).get("seven_day") or {}
    pct, reset = week.get("used_percentage"), week.get("resets_at")
    if not isinstance(pct, (int, float)) or not isinstance(reset, (int, float)):
        return
    path = cache_path()
    try:
        old = json.loads(path.read_text()).get("seven_day")
    except (OSError, json.JSONDecodeError, AttributeError):
        old = None
    new = {"used_percentage": pct, "resets_at": reset}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if old == new and time.time() - path.stat().st_mtime < 3600:
            return
        tmp = path.with_name(f".usage.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"seven_day": new, "updated_at": int(time.time())}))
        os.replace(tmp, path)  # atomic: parallel sessions never leave a torn file
    except OSError:
        pass


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        update_cache(json.loads(raw or b"{}"))
    except json.JSONDecodeError:
        pass
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        return 0
    # shell=True on purpose: argv is the user's own statusline command from their settings.json, which
    # Claude Code itself runs through a shell, so it may hold pipes, `~`, `$VAR` or `&&`. Only that
    # string is executed. The session JSON (raw) goes to its stdin as data, and nothing from the usage
    # cache, the plugin's data dir or the TAC platform is ever put into the command.
    cmd = argv[0] if len(argv) == 1 else " ".join(argv)
    r = subprocess.run(cmd, shell=True, input=raw, stdout=subprocess.PIPE)
    sys.stdout.buffer.write(r.stdout)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
