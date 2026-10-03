"""SessionStart hook (every source): expose this session's id to tacctl as $TAC_SESSION_ID.

Claude Code passes session_id only on hook stdin; a SessionStart hook can persist env vars into later
Bash commands by appending `export` lines to $CLAUDE_ENV_FILE. tacctl records the id in
tac-work/<name>/.sessions so a piece's token estimate counts only the sessions that built it.
Reads stdin, writes one line to $CLAUDE_ENV_FILE, prints nothing. No network calls.
"""

from __future__ import annotations

import json
import os
import re
import sys

SESSION_RE = re.compile(r"[A-Za-z0-9-]{1,64}")  # the id ends up in a sourced shell file: no quoting games


def main() -> int:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    try:
        sid = json.loads(sys.stdin.read() or "{}").get("session_id")
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not env_file or not isinstance(sid, str) or not SESSION_RE.fullmatch(sid):
        return 0
    try:
        with open(env_file, "a", encoding="utf-8") as fh:
            fh.write(f"export TAC_SESSION_ID={sid}\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
