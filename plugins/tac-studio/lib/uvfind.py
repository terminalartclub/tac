"""Where uv is, and the one line that says what to do when it isn't. Shared by render_piece.py and the
SessionStart hook (scripts/nudge.py); bin/tac and bin/tacctl do the same lookup in shell. A PATH lookup and
two file checks: no subprocess, no network."""

from __future__ import annotations

import os
import re
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


FIRST_RUN = "tac: the first /tac: command may download Python 3.10+ via uv (one-time, about 30 MB)."
PY_NAME = re.compile(r"python3\.(1\d|[2-9]\d)")  # python3.10 and later


def has_python310(path: str | None = None, home: Path | None = None) -> bool:
    """A Python 3.10+ uv can use without downloading one: a python3.1x on PATH (or python3 linking to one), or
    one uv already installed. Names and links only, nothing run."""
    dirs = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
    for d in filter(None, dirs):
        for name in ("python3", *(f"python3.{m}" for m in range(10, 20))):
            exe = Path(d) / name
            if exe.is_file() and os.access(exe, os.X_OK):
                if name != "python3" or PY_NAME.fullmatch(Path(os.path.realpath(exe)).name):
                    return True
    managed = Path(os.environ.get("UV_PYTHON_INSTALL_DIR") or (home or Path.home()) / ".local" / "share" / "uv" / "python")
    try:
        return any(re.match(r"cpython-3\.(1\d|[2-9]\d)\.", p.name) for p in managed.iterdir())
    except OSError:
        return False
