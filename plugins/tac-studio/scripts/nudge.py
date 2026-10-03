"""SessionStart hook: one line when the weekly window resets within 24h and <80% is used,
saying what fits in the spare capacity (it expires at reset, so it's about fit, not cost).

Reads ~/.cache/tac/usage.json, written by the opt-in statusline helper (statusline_cache.py).
Silent (no output) when the cache is missing/stale or the condition doesn't hold.
Also puts a copy of statusline_cache.py in ${CLAUDE_PLUGIN_DATA}, a path that survives plugin
updates, so the user's statusline setting can point at it. The user's statusLine runs that copy on
every render, so it is written only when missing and never overwritten: a plugin update can't
silently swap the code it runs. When the plugin's version differs, the hook says so in one line.
No network calls.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

USED_MAX = 80.0
WINDOW_S = 24 * 3600
STALE_S = 7 * 24 * 3600
# Share of the weekly window one full piece costs (house pieces: ~0.4M tokens, 6–14 iterations).
# Conservative default; calibrate per plan in ~/.config/tac/config.json {"piece_pct": N} or TAC_PIECE_PCT.
DEFAULT_PIECE_PCT = 15.0
SKETCH_SHARE = 0.4  # a sketch is ≤3 iterations, ~40% of a full piece


def piece_pct() -> float:
    env = os.environ.get("TAC_PIECE_PCT")
    if env:
        try:
            return max(0.1, float(env))
        except ValueError:
            pass
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tac" / "config.json"
    try:
        return max(0.1, float(json.loads(cfg.read_text())["piece_pct"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return DEFAULT_PIECE_PCT


def fit(used_pct: float, pct: float) -> tuple[int, bool]:
    """(full pieces that fit in what's left, whether a sketch fits)."""
    spare = max(0.0, 100.0 - used_pct)
    return int(spare // pct), spare >= pct * SKETCH_SHARE


def window(data: dict, now: float) -> tuple[float, float] | None:
    """(used %, seconds to reset) from a fresh cache, else None."""
    week = data.get("seven_day") or {}
    pct, reset = week.get("used_percentage"), week.get("resets_at")
    if not isinstance(pct, (int, float)) or not isinstance(reset, (int, float)):
        return None
    if now - float(data.get("updated_at", 0)) > STALE_S or reset <= now:
        return None
    return float(pct), reset - now


def load_cache() -> dict:
    try:
        return json.loads(cache_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def cache_path() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tac" / "usage.json"


def message(data: dict, now: float, pct_per_piece: float = DEFAULT_PIECE_PCT) -> str | None:
    w = window(data, now)
    if w is None:
        return None
    used, left = w
    if used >= USED_MAX or left > WINDOW_S:
        return None
    when = f"{int(left // 3600)}h" if left >= 3600 else f"{max(1, int(left // 60))}m"
    full, sketch = fit(used, pct_per_piece)
    hint = f"~{used:.0f}% used, resets in {when}"
    if full >= 1:
        what, hint, cmd = "a piece", f"{hint}, fits ~{full} full piece{'s' if full > 1 else ''}", "/tac:create"
    elif sketch:
        what, hint, cmd = "a sketch", f"{hint}, fits a sketch", "/tac:create --sketch"
    else:
        return None
    return f"tac: room for {what} before your weekly reset ({hint}). Make something for the wall: {cmd}"


def sync_helper() -> str | None:
    """Copy the statusline helper into ${CLAUDE_PLUGIN_DATA} if it is missing. An existing copy is
    left as is; returns a one-line notice when it differs from this plugin's version."""
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not data_dir:
        return None
    src = Path(__file__).with_name("statusline_cache.py")
    dst = Path(data_dir) / "statusline_cache.py"
    try:
        if not dst.exists() and not dst.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "xb") as fh:  # exclusive create: never clobbers a copy made meanwhile
                fh.write(src.read_bytes())
            return None
        if dst.read_bytes() != src.read_bytes():
            return (f"tac: your statusline helper {dst} differs from this plugin's version; "
                    f"to update, review and copy {src} over it")
    except OSError:
        pass
    return None


def main() -> int:
    lines = [m for m in (sync_helper(), message(load_cache(), time.time(), piece_pct())) if m]
    if lines:
        print(json.dumps({"systemMessage": "\n".join(lines)}))  # shown to the user, not added to Claude's context
    return 0


if __name__ == "__main__":
    sys.exit(main())
