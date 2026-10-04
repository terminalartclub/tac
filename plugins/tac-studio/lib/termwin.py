"""Open a new terminal window that runs `tac play <piece>` (the person's own local piece only).

Claude Code's Bash tool is not a TTY, so a piece can't animate there; a real terminal window can.
Everything that touches the OS is injected (run, popen, which, exists), so tests never open a window.

macOS: iTerm2 when installed, else Terminal.app, both via osascript. The command reaches the window's
shell as one AppleScript string literal (quote_applescript) holding a POSIX-quoted command line
(shlex.join): no piece name or path can break out of either layer. Linux: $TERMINAL, then common
emulators, launched with an argv (no shell string at all). Anything else, or no GUI: None, and the
caller prints the paste command instead.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

COLS, ROWS = 80, 66  # the reel canvas every piece is composed for
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
LINUX_TERMINALS = ("x-terminal-emulator", "gnome-terminal", "konsole", "kitty", "alacritty", "wezterm")
ITERM_PATHS = ("/Applications/iTerm.app", "~/Applications/iTerm.app")


@dataclass
class Opened:
    app: str  # "iTerm", "Terminal", or the Linux emulator's name
    size: tuple[int, int] | None  # the window's real (cols, rows) when the app reports it


def play_argv(tac: Path, piece: Path) -> list[str]:
    return [str(tac), "play", str(piece)]


def quote_applescript(s: str) -> str:
    """An AppleScript string literal: only backslash and double quote are special inside one."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def iterm_script(cmd: str, cols: int = COLS, rows: int = ROWS) -> str:
    return "\n".join([
        'tell application "iTerm"',
        "  activate",
        "  set w to (create window with default profile)",
        "  tell current session of w",
        f"    set columns to {int(cols)}",
        f"    set rows to {int(rows)}",
        f"    write text {quote_applescript(cmd)}",
        '    return ((columns as text) & "x" & (rows as text))',
        "  end tell",
        "end tell",
    ])


def terminal_script(cmd: str, cols: int = COLS, rows: int = ROWS) -> str:
    return "\n".join([
        'tell application "Terminal"',
        "  activate",
        f"  set t to do script {quote_applescript(cmd)}",
        f"  set number of columns of t to {int(cols)}",
        f"  set number of rows of t to {int(rows)}",
        '  return ((number of columns of t as text) & "x" & (number of rows of t as text))',
        "end tell",
    ])


def _size(out: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d+)x(\d+)\s*", out or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def linux_argv(term: str, argv: list[str], cols: int = COLS, rows: int = ROWS) -> list[str]:
    """How each emulator takes a command (and a size where it has a flag for one). The command runs
    through `sh -c` with the argv as positional parameters, never interpolated, and waits for Enter
    after it exits so an error stays readable."""
    hold = ["sh", "-c", '"$0" "$@"; printf "\\n[done: press Enter to close] "; read _', *argv]
    base = Path(term).name
    if base == "gnome-terminal":
        return [term, f"--geometry={int(cols)}x{int(rows)}", "--", *hold]
    if base == "kitty":
        return [term, "-o", f"initial_window_width={int(cols)}c", "-o", f"initial_window_height={int(rows)}c", *hold]
    if base == "wezterm":
        return [term, "start", "--", *hold]
    if base in ("xterm", "uxterm"):
        return [term, "-geometry", f"{int(cols)}x{int(rows)}", "-e", *hold]
    return [term, "-e", *hold]  # x-terminal-emulator, konsole, alacritty, $TERMINAL


def no_gui_reason(platform: str, env: Mapping[str, str]) -> str | None:
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return "this is an SSH session"
    if platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return "no graphical display"
    if not (platform == "darwin" or platform.startswith("linux")):
        return f"opening a terminal isn't supported on {platform}"
    return None


def open_play_window(
    argv: list[str], *,
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = lambda p: Path(p).expanduser().exists(),
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    cols: int = COLS, rows: int = ROWS,
) -> tuple[Opened | None, str]:
    """(Opened, "") when a window is running the piece, else (None, why not)."""
    env = os.environ if env is None else env
    if any(_CONTROL.search(a) for a in argv):
        return None, "the piece path has control characters"
    if (why := no_gui_reason(platform, env)) is not None:
        return None, why
    if platform == "darwin":
        if not which("osascript"):
            return None, "osascript not found"
        cmd = shlex.join(argv)
        apps = ([("iTerm", iterm_script)] if any(exists(p) for p in ITERM_PATHS) else []) + [("Terminal", terminal_script)]
        errors = []
        for app, script in apps:
            try:
                r = run(["osascript", "-e", script(cmd, cols, rows)], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                errors.append(f"{app}: {type(e).__name__}")
                continue
            if r.returncode == 0:
                return Opened(app, _size(r.stdout)), ""
            errors.append(f"{app}: {(r.stderr or '').strip()[:120] or f'exit {r.returncode}'}")
        return None, "; ".join(errors) or "no terminal app"
    candidates = []
    if env.get("TERMINAL"):
        candidates.append(env["TERMINAL"])
    candidates += [t for t in LINUX_TERMINALS if t not in candidates]
    for term in candidates:
        path = which(term)
        if not path:
            continue
        try:
            p = popen(linux_argv(path, argv, cols, rows), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            continue
        try:
            rc = p.wait(timeout=1.5)  # most launchers return 0 at once or keep running; non-zero = failed
        except subprocess.TimeoutExpired:
            rc = 0
        if rc == 0:
            return Opened(Path(term).name, None), ""
    return None, "no terminal emulator found ($TERMINAL, x-terminal-emulator, gnome-terminal, konsole, kitty, alacritty, wezterm)"
