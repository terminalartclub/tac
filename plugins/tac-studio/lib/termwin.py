"""Open a terminal pane, tab or window that runs `tac play <piece>` (the person's own local piece only).

Claude Code's Bash tool is not a TTY, so a piece can't animate there; a real terminal window can.
Everything that touches the OS is injected (run, popen, which, exists, sleep), so tests never open a window.

Only conservative characters ever reach a terminal (SAFE_ARG: letters, digits, / . _ ~ + @ - and space).
On macOS the command is TYPED into the window, so the person's login shell parses it: POSIX quoting
(shlex.join) is not enough for fish or tcsh, where `\'` and `$(...)` read differently. Any other
character means no window, and the caller prints the paste command instead.

macOS, by default beside Claude Code: in iTerm2 (TERM_PROGRAM=iTerm.app) a split to the RIGHT of the session
that ran tacctl (found by $ITERM_SESSION_ID), in Ghostty 1.3+ (TERM_PROGRAM=ghostty) a split right of the focused
terminal (its AppleScript API, a preview); `exec` makes the pane close when the piece exits. where="tab": a new
tab in that window instead. where="window", any other terminal, or a split that fails: a new window, iTerm2
when installed, else Terminal.app (tabs there need System Events and accessibility permission: not used). All
via osascript; the command is one AppleScript string literal (quote_applescript). Linux: $TERMINAL, then common emulators, launched with an argv (no shell
string) through each emulator's own "run this argv" flag. The launched `sh` touches a marker file first, so
a window that opened but never ran the piece is reported as unconfirmed. Anything else, or no GUI: None.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

COLS, ROWS = 80, 66  # the reel canvas every piece is composed for
SAFE_ARG = re.compile(r"[A-Za-z0-9/._~+@ -]+")
LAUNCH_CONFIRM_S = 3.0
LINUX_TERMINALS = ("x-terminal-emulator", "gnome-terminal", "konsole", "kitty", "alacritty", "wezterm")
ITERM_PATHS = ("/Applications/iTerm.app", "~/Applications/iTerm.app")
WHERE = ("split", "tab", "window")
# $ITERM_SESSION_ID is "w0t1p0:<UUID>"; only the UUID part, and only in exactly this shape, reaches a script
ITERM_SESSION_RE = re.compile(r"w\d+t\d+p\d+:([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})")


@dataclass
class Opened:
    app: str  # "iTerm", "Terminal", or the Linux emulator's name
    size: tuple[int, int] | None  # the window's real (cols, rows) when the app reports it
    confirmed: bool = True  # False: a window opened but the piece wasn't seen starting in it
    where: str = "window"  # "pane" (a split beside Claude Code), "tab" or "window"
    note: str = ""  # why it's a window when a pane or tab was asked for (the split's error)


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


def iterm_session(env: Mapping[str, str]) -> str | None:
    """The UUID of the iTerm2 session that ran tacctl, from $ITERM_SESSION_ID; None when absent or odd."""
    m = ITERM_SESSION_RE.fullmatch(env.get("ITERM_SESSION_ID", ""))
    return m.group(1).upper() if m else None


def iterm_beside_script(cmd: str, where: str, session: str | None) -> str:
    """A split to the right of `session` (else the current session of the current window), or a new tab in its
    window, typing `exec <cmd>` into the new session's own shell (its PATH, so uv is found); the pane closes
    when the piece exits. iTerm2 adds a new tab at the end of the window's tabs (no position in its API) and
    sizes a split by halving the session (setting columns/rows would resize the whole window: not done)."""
    lines = ['tell application "iTerm"', "  set s0 to missing value", "  set w0 to missing value"]
    if session:
        lines += [
            "  repeat with w in windows",
            "    repeat with t in tabs of w",
            "      repeat with s in sessions of t",
            f"        if (unique id of s) is {quote_applescript(session)} then",
            "          set s0 to contents of s",
            "          set w0 to contents of w",
            "        end if",
            "      end repeat",
            "    end repeat",
            "  end repeat",
        ]
    lines += [
        "  if s0 is missing value then",
        "    set w0 to current window",
        "    set s0 to current session of w0",
        "  end if",
    ]
    if where == "split":
        lines += ["  tell s0", "    set s1 to (split vertically with default profile)", "  end tell"]
    else:
        lines += ["  tell w0", "    set t1 to (create tab with default profile)", "  end tell",
                  "  set s1 to current session of t1"]
    lines += [
        "  tell s1",
        f"    write text {quote_applescript('exec ' + cmd)}",
        '    return ((columns as text) & "x" & (rows as text))',
        "  end tell",
        "end tell",
    ]
    return "\n".join(lines)


def ghostty_beside_script(cmd: str, where: str) -> str:
    """Ghostty 1.3+ (AppleScript, a preview API): a split right of the focused terminal of the front window, or
    a new tab in it, typing `exec <cmd>` and Enter into the new terminal's shell; it closes when the piece exits.
    Ghostty reports no size here."""
    lines = ['tell application "Ghostty"', "  set t0 to focused terminal of selected tab of front window"]
    if where == "split":
        lines += ["  set t1 to split t0 direction right"]
    else:
        lines += ["  new tab in front window", "  set t1 to focused terminal of selected tab of front window"]
    lines += [f"  input text {quote_applescript('exec ' + cmd)} to t1", '  send key "enter" to t1',
              '  return "ok"', "end tell"]
    return "\n".join(lines)


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


def linux_argv(term: str, argv: list[str], cols: int = COLS, rows: int = ROWS, marker: str | None = None) -> list[str]:
    """How each emulator takes a command (and a size where it has a flag for one). The command runs
    through `sh -c` with the argv as positional parameters, never interpolated; it first creates the
    `marker` DIRECTORY (proof the command started; mkdir fails on an existing name and never follows a
    planted symlink, unlike `: >`), then waits for Enter after the piece exits so an error stays readable."""
    hold = ["sh", "-c", 'mkdir "$0" 2>/dev/null; "$@"; printf "\\n[done: press Enter to close] "; read _',
            marker or "/nonexistent/tac-marker", *argv]
    base = Path(term).name
    if base in ("xfce4-terminal", "mate-terminal", "terminator"):  # their -e takes ONE string: use -x (rest = argv)
        return [term, "-x", *hold]
    if base == "tilix":
        return [term, "--", *hold]
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
    sleep: Callable[[float], None] = time.sleep,
    cols: int = COLS, rows: int = ROWS,
    where: str = "split",
) -> tuple[Opened | None, str]:
    """(Opened, "") when a pane, tab or window is running the piece, else (None, why not). `where` is a wish:
    a split or tab only where the terminal running Claude Code can make one (iTerm2, Ghostty 1.3+), else a
    window; a split or tab that fails falls back to a window too."""
    if where not in WHERE:
        raise ValueError(f"where is one of {WHERE}")
    env = os.environ if env is None else env
    if not all(SAFE_ARG.fullmatch(a) for a in argv):
        return None, ("the piece path has characters a terminal could misread (only letters, digits, "
                      "space and / . _ ~ + @ - are used)")
    if (why := no_gui_reason(platform, env)) is not None:
        return None, why
    if platform == "darwin":
        if not which("osascript"):
            return None, "osascript not found"
        cmd = shlex.join(argv)
        errors = []
        term = env.get("TERM_PROGRAM", "")
        if where != "window" and term in ("iTerm.app", "ghostty"):
            app = "iTerm" if term == "iTerm.app" else "Ghostty"
            script = (iterm_beside_script(cmd, where, iterm_session(env)) if app == "iTerm"
                      else ghostty_beside_script(cmd, where))
            try:
                r = run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                errors.append(f"{app} {where}: {type(e).__name__}")
            else:
                if r.returncode == 0:
                    return Opened(app, _size(r.stdout), where="pane" if where == "split" else "tab"), ""
                errors.append(f"{app} {where}: {(r.stderr or '').strip()[:120] or f'exit {r.returncode}'}")
        note = "; ".join(errors)  # why not a pane or tab, if one was tried
        apps = ([("iTerm", iterm_script)] if any(exists(p) for p in ITERM_PATHS) else []) + [("Terminal", terminal_script)]
        for app, script in apps:
            try:
                r = run(["osascript", "-e", script(cmd, cols, rows)], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                errors.append(f"{app}: {type(e).__name__}")
                continue
            if r.returncode == 0:
                return Opened(app, _size(r.stdout), note=note), ""
            errors.append(f"{app}: {(r.stderr or '').strip()[:120] or f'exit {r.returncode}'}")
        return None, "; ".join(errors) or "no terminal app"
    candidates = []
    if env.get("TERMINAL"):
        candidates.append(env["TERMINAL"])
    candidates += [t for t in LINUX_TERMINALS if t not in candidates]
    marker_dir = Path(tempfile.mkdtemp(prefix="tac-play-"))  # 0700: nobody else can plant a link in it
    unconfirmed = 0  # launches whose shell hasn't been seen starting (it may still run later)
    try:
        for term in candidates:
            path = which(term)
            if not path:
                continue
            marker = marker_dir / f"started-{Path(term).name}"
            try:
                p = popen(linux_argv(path, argv, cols, rows, str(marker)), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                continue
            unconfirmed += 1
            try:
                rc = p.wait(timeout=1.5)  # most launchers return 0 at once or keep running; non-zero = failed
            except subprocess.TimeoutExpired:
                rc = 0
            if rc != 0:
                continue
            waited = 0.0
            while not marker.is_dir() and waited < LAUNCH_CONFIRM_S:
                sleep(0.1)
                waited += 0.1
            confirmed = marker.is_dir()
            unconfirmed -= confirmed
            return Opened(Path(term).name, None, confirmed=confirmed), ""
    finally:
        # A launched-but-unconfirmed shell may still run `mkdir "$0"` later: keep the private dir (a few bytes
        # in the user's own temp dir) so its path can't be re-created by someone else in a shared /tmp.
        if unconfirmed == 0:
            shutil.rmtree(marker_dir, ignore_errors=True)
    return None, "no terminal emulator found ($TERMINAL, x-terminal-emulator, gnome-terminal, konsole, kitty, alacritty, wezterm)"
