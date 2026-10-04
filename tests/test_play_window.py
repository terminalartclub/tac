"""/tac:play opens a terminal window running `tac play <the person's own piece>`. No window is ever opened
here: every OS call is a fake."""

import shlex
import subprocess
from pathlib import Path

import pytest

import review
import tacctl
import termwin

NASTY = "/Users/o'brien/tac work/\"quoted\" \\back$HOME`id`/kettle/kettle.py"


def _unquote_applescript(lit: str) -> str:
    """Parse one AppleScript string literal back (the inverse of quote_applescript); fails if it leaks."""
    assert lit[0] == lit[-1] == '"'
    out, i, body = [], 0, lit[1:-1]
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            out.append(body[i + 1])
            i += 2
            continue
        assert ch != '"', "an unescaped quote would end the literal early"
        out.append(ch)
        i += 1
    return "".join(out)


def test_command_quoting_survives_spaces_quotes_and_shell_metacharacters():
    """Defence in depth: even for a path the SAFE_ARG gate would refuse, the two quoting layers hold."""
    argv = termwin.play_argv(Path("/plug ins/bin/tac"), Path(NASTY))
    cmd = shlex.join(argv)
    assert shlex.split(cmd) == argv  # the window's shell gets exactly these words, nothing expanded
    lit = termwin.quote_applescript(cmd)
    assert _unquote_applescript(lit) == cmd
    for script in (termwin.iterm_script(cmd), termwin.terminal_script(cmd)):
        assert lit in script and script.count(lit) == 1
        assert "80" in script and "66" in script


class FakeRun:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out, err = self.results.pop(0)
        return subprocess.CompletedProcess(argv, rc, out, err)


MAC = dict(platform="darwin", env={}, which=lambda n: f"/usr/bin/{n}")


def test_macos_prefers_iterm_and_reports_the_real_size():
    run = FakeRun([(0, "80x52\n", "")])
    opened, why = termwin.open_play_window(["tac", "play", "/p.py"], exists=lambda p: "iTerm" in p, run=run, **MAC)
    assert opened == termwin.Opened("iTerm", (80, 52)) and why == ""
    assert run.calls[0][:2] == ["osascript", "-e"] and 'tell application "iTerm"' in run.calls[0][2]


def test_macos_falls_back_to_terminal_when_iterm_is_refused_then_to_the_paste_command():
    run = FakeRun([(1, "", "Not authorised to send Apple events to iTerm. (-1743)"), (0, "80x66", "")])
    opened, _ = termwin.open_play_window(["tac", "play", "/p.py"], exists=lambda p: True, run=run, **MAC)
    assert opened == termwin.Opened("Terminal", (80, 66))
    assert 'tell application "Terminal"' in run.calls[1][2]
    run = FakeRun([(1, "", "denied (-1743)"), (1, "", "denied (-1743)")])
    opened, why = termwin.open_play_window(["tac", "play", "/p.py"], exists=lambda p: True, run=run, **MAC)
    assert opened is None and "iTerm" in why and "Terminal" in why


def test_macos_without_iterm_goes_straight_to_terminal():
    run = FakeRun([(0, "80x66", "")])
    opened, _ = termwin.open_play_window(["tac", "play", "/p.py"], exists=lambda p: False, run=run, **MAC)
    assert opened.app == "Terminal" and len(run.calls) == 1


@pytest.mark.parametrize("platform,env,reason", [
    ("darwin", {"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, "SSH"),
    ("linux", {"SSH_TTY": "/dev/pts/1", "DISPLAY": ":0"}, "SSH"),
    ("linux", {}, "no graphical display"),
    ("win32", {}, "isn't supported"),
])
def test_no_gui_means_the_paste_command(platform, env, reason):
    run = FakeRun([])
    opened, why = termwin.open_play_window(["tac", "play", "/p.py"], platform=platform, env=env,
                                           which=lambda n: "/bin/x", run=run, popen=None)
    assert opened is None and reason in why and run.calls == []


@pytest.mark.parametrize("path", [
    "/p\nrm.py",                                  # control character
    "/Users/a/tac work/x\\'$(touch pwned)'.py",  # fish: \' ends the quote, then $(...) runs
    "/tmp/`id`.py", "/tmp/$HOME.py", "/tmp/a;b.py", "/tmp/a\"b.py", "/tmp/a'b.py", "/tmp/a*b.py",
    "/Users/josé/tac-work/k/k.py",                # non-ASCII: refused rather than trusted to every shell
])
def test_unsafe_path_characters_never_reach_a_terminal(path):
    run, popen = FakeRun([]), FakePopen({})
    for platform, env in (("darwin", {}), ("linux", {"DISPLAY": ":0"})):
        opened, why = termwin.open_play_window(["/plug/bin/tac", "play", path], platform=platform, env=env,
                                               which=lambda n: f"/usr/bin/{n}", exists=lambda p: True,
                                               run=run, popen=popen)
        assert opened is None and "characters a terminal could misread" in why
    assert run.calls == [] and popen.calls == []


def test_ordinary_paths_pass_the_gate():
    for path in ("/Users/alex/tac-work/kettle/kettle.py", "/home/a.b+c@d/tac work/last-light/last-light.py",
                 "/Users/alex/.claude/plugins/cache/terminalartclub/tac/0.1.2/bin/tac"):
        assert termwin.SAFE_ARG.fullmatch(path)


class FakePopen:
    def __init__(self, rcs, start_marker=True):
        self.rcs, self.calls, self.start_marker = dict(rcs), [], start_marker

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = self.rcs.get(Path(argv[0]).name, 0)
        if self.start_marker:  # simulate the window's sh touching its marker (the path follows the script)
            Path(argv[argv.index("sh") + 3]).touch()

        class P:
            def wait(self, timeout=None):
                if rc is None:
                    raise subprocess.TimeoutExpired(argv, timeout)
                return rc
        return P()


def test_linux_tries_terminal_env_first_then_the_common_emulators():
    which = {"myterm": "/opt/myterm", "gnome-terminal": "/usr/bin/gnome-terminal"}.get
    popen = FakePopen({"myterm": 1, "gnome-terminal": None})  # $TERMINAL fails; gnome-terminal keeps running
    piece = "/home/alex/tac work/kettle/kettle.py"
    opened, _ = termwin.open_play_window(["tac", "play", piece], platform="linux", env={"DISPLAY": ":0", "TERMINAL": "myterm"},
                                         which=which, popen=popen, sleep=lambda s: None)
    assert opened == termwin.Opened("gnome-terminal", None, confirmed=True)
    first, second = popen.calls
    assert first[:2] == ["/opt/myterm", "-e"]
    assert second[:3] == ["/usr/bin/gnome-terminal", "--geometry=80x66", "--"]
    sh = second[3:]
    assert sh[:2] == ["sh", "-c"] and sh[4:] == ["tac", "play", piece]  # marker, then argv as positional params
    assert piece not in sh[2]  # the shell script itself never contains the path


def test_linux_window_that_never_starts_the_piece_is_unconfirmed():
    slept = []
    opened, _ = termwin.open_play_window(["tac", "play", "/p.py"], platform="linux", env={"DISPLAY": ":0"},
                                         which={"konsole": "/usr/bin/konsole"}.get,
                                         popen=FakePopen({"konsole": None}, start_marker=False), sleep=slept.append)
    assert opened == termwin.Opened("konsole", None, confirmed=False)
    assert 2.5 <= sum(slept) <= 3.5  # waited ~LAUNCH_CONFIRM_S for the marker, no longer


@pytest.mark.parametrize("term,head", [
    ("xfce4-terminal", ["/x/xfce4-terminal", "-x", "sh"]), ("mate-terminal", ["/x/mate-terminal", "-x", "sh"]),
    ("terminator", ["/x/terminator", "-x", "sh"]), ("tilix", ["/x/tilix", "--", "sh"]),
    ("kitty", ["/x/kitty", "-o", "initial_window_width=80c"]), ("wezterm", ["/x/wezterm", "start", "--"]),
    ("konsole", ["/x/konsole", "-e"]), ("alacritty", ["/x/alacritty", "-e"]), ("xterm", ["/x/xterm", "-geometry", "80x66"]),
])
def test_linux_argv_shapes(term, head):
    argv = termwin.linux_argv(f"/x/{term}", ["tac", "play", "/p.py"])
    assert argv[:len(head)] == head and argv[-3:] == ["tac", "play", "/p.py"]


def test_linux_with_no_emulator_falls_back():
    opened, why = termwin.open_play_window(["tac", "play", "/p.py"], platform="linux", env={"DISPLAY": ":0"},
                                           which=lambda n: None, popen=FakePopen({}))
    assert opened is None and "no terminal emulator" in why


# ── tacctl play ──────────────────────────────────────────────────────────


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "tac work"  # a space in the root on purpose
    (root / "kettle").mkdir(parents=True)
    (root / "kettle" / "kettle.py").write_text("# kettle\n")
    monkeypatch.setenv("TAC_WORK", str(root))
    calls = {"open": [], "local": [], "build": []}

    def fake_open(argv):
        calls["open"].append(argv)
        return termwin.Opened("Terminal", (80, 66)), ""

    monkeypatch.setattr(termwin, "open_play_window", fake_open)
    monkeypatch.setattr(tacctl, "open_local", lambda p: calls["local"].append(p))
    monkeypatch.setattr(review, "build", lambda root, render, only: calls["build"].append(only) or root / "index.html")
    return root, calls


def test_play_opens_a_window_and_not_the_browser(work, capsys):
    root, calls = work
    assert tacctl.main(["play", "kettle"]) == 0
    out = capsys.readouterr().out
    assert "playing kettle in a new Terminal window. Ctrl-C there stops it." in out
    assert calls["open"][0][1:] == ["play", str((root / "kettle" / "kettle.py").resolve())]
    assert calls["local"] == []  # no browser by default
    assert "review page" in out and calls["build"] == ["kettle"]
    assert tacctl.main(["play", "kettle", "--page"]) == 0 and len(calls["local"]) == 1


def test_play_falls_back_to_the_paste_command(work, monkeypatch, capsys):
    root, calls = work
    monkeypatch.setattr(termwin, "open_play_window", lambda argv: (None, "this is an SSH session"))
    assert tacctl.main(["play", "kettle", "--no-page"]) == 0
    out = capsys.readouterr().out
    assert "couldn't open a terminal window (this is an SSH session)" in out
    line = out.splitlines()[-1].strip()
    assert shlex.split(line)[1:] == ["play", str((root / "kettle" / "kettle.py").resolve())]


def test_play_unconfirmed_window_also_prints_the_paste_command(work, monkeypatch, capsys):
    monkeypatch.setattr(termwin, "open_play_window",
                        lambda argv: (termwin.Opened("konsole", None, confirmed=False), ""))
    assert tacctl.main(["play", "kettle", "--no-page"]) == 0
    out = capsys.readouterr().out
    assert "couldn't confirm the piece started" in out and "playing kettle" not in out
    assert shlex.split(out.splitlines()[-1].strip())[1] == "play"


def test_play_reports_a_window_that_could_not_be_sized(work, monkeypatch, capsys):
    monkeypatch.setattr(termwin, "open_play_window", lambda argv: (termwin.Opened("iTerm", (80, 48)), ""))
    assert tacctl.main(["play", "kettle", "--no-page"]) == 0
    assert "the window is 80x48, not 80x66" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["../kettle", "Kettle", "ket tle", 'ket"tle', "ket'tle", "a--b", "kettle/", "-",
                                 "kettle\n", "kéttle"])
def test_play_refuses_anything_but_a_slug_name(work, bad, capsys):
    root, calls = work
    assert tacctl.main(["play", bad]) == 1
    assert calls["open"] == [] and "lowercase letters" in capsys.readouterr().err


def test_a_leading_hyphen_is_refused_by_argparse_before_anything_runs(work):
    root, calls = work
    with pytest.raises(SystemExit):
        tacctl.main(["play", "-x"])
    assert calls["open"] == []


def test_play_refuses_a_piece_that_points_outside_the_work_root(work, tmp_path, capsys):
    root, calls = work
    elsewhere = tmp_path / "someone-else"
    elsewhere.mkdir()
    (elsewhere / "stranger.py").write_text("# not yours\n")
    (root / "stranger").mkdir()
    (root / "stranger" / "stranger.py").symlink_to(elsewhere / "stranger.py")
    assert tacctl.main(["play", "stranger"]) == 1
    assert calls["open"] == [] and "outside" in capsys.readouterr().err


def test_play_without_a_name_lists_pieces(work, capsys):
    root, calls = work
    (root / "empty-dir").mkdir()
    assert tacctl.main(["play"]) == 0
    out = capsys.readouterr().out
    assert "kettle" in out and "empty-dir" not in out and calls["open"] == []
