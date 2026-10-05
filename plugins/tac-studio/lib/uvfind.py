"""Where uv is, and the one line that says what to do when it isn't. Shared by render_piece.py and the
SessionStart hook (scripts/nudge.py); bin/tac and bin/tacctl do the same lookup in shell. A PATH lookup and
two file checks: no subprocess, no network."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MISSING = ("tac needs uv (a Python runner). Install it: brew install uv — or see "
           "https://docs.astral.sh/uv/getting-started/installation/ — then run the command again.")
PYTHON = ">=3.10"  # uv run --python: a managed Python when the machine's is older (macOS ships 3.9)


def fallbacks(home: Path | None = None) -> list[Path]:
    """Where uv's own installer puts it, often off PATH in a non-login shell."""
    home = home or Path.home()
    return [home / ".local" / "bin" / "uv", home / ".cargo" / "bin" / "uv"]


def find_uv(path: str | None = None, home: Path | None = None) -> str | None:
    found = shutil.which("uv", path=path if path is not None else os.environ.get("PATH"))
    if found:
        return found
    for p in fallbacks(home):
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None
