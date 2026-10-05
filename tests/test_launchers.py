"""The plugin's uv launchers pin exact versions and never sync the user's own project."""

import re

import render_piece
from conftest import ROOT

BIN = ROOT / "plugins" / "tac-studio" / "bin"
DEPS = ("rich", "Pillow", "fonttools")


def pins(argv: str) -> dict[str, str]:
    found = dict(re.findall(r"--with[ =]'?([A-Za-z]+)==([0-9][0-9A-Za-z.]*)'?", argv))
    assert set(found) == set(DEPS), argv
    assert len(re.findall(r"--with", argv)) == len(DEPS), argv  # no unpinned extra --with
    return found


def test_bin_scripts_pin_exact_versions_and_pass_no_project() -> None:
    seen = []
    for name in ("tac", "tacctl"):
        text = (BIN / name).read_text()
        exec_line = " ".join(text.split("exec ", 1)[1].replace("\\\n", " ").split())
        assert exec_line.startswith('"$UV" run -q --no-project --python \'>=3.10\' '), exec_line
        seen.append(pins(exec_line))
    fallback = " ".join(render_piece.UV_DEPS)
    assert all(d.count("==") == 1 for d in render_piece.UV_DEPS)
    seen.append(pins(" ".join(f"--with {d}" for d in render_piece.UV_DEPS)))
    assert seen[0] == seen[1] == seen[2], (seen, fallback)


def test_render_fallback_passes_no_project(monkeypatch) -> None:
    monkeypatch.setattr(render_piece, "_deps_ok", lambda: False)
    monkeypatch.setattr("uvfind.find_uv", lambda: "/opt/uv")
    cmd = render_piece._worker_cmd()
    assert cmd[:6] == ["/opt/uv", "run", "-q", "--no-project", "--python", ">=3.10"]
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--with"] == list(render_piece.UV_DEPS)


# ── a missing uv is impossible to miss ──────────────────────────────────────────────────────────

import json
import os
import shutil
import subprocess
import sys

import pytest

import uvfind


def _no_uv_env(tmp_path, home=None):
    """A PATH with bash and coreutils but no uv, and a HOME with none of uv's install dirs."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("bash", "dirname", "env"):
        src = shutil.which(tool)
        if src and not (bindir / tool).exists():
            (bindir / tool).symlink_to(src)
    return {"PATH": str(bindir), "HOME": str(home or tmp_path / "home")}


@pytest.mark.parametrize("launcher,args", [("tacctl", ["fit"]), ("tacctl", ["play", "kettle"]), ("tac", ["play", "x.py"])])
def test_a_launcher_without_uv_says_so_and_exits_127(tmp_path, launcher, args):
    r = subprocess.run([str(BIN / launcher), *args], env=_no_uv_env(tmp_path), capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 127
    assert r.stderr.strip() == uvfind.MISSING and r.stdout == ""


@pytest.mark.parametrize("where", [".local/bin", ".cargo/bin"])
def test_a_launcher_finds_uv_off_path_where_its_installer_puts_it(tmp_path, where):
    home = tmp_path / "home"
    fake = home / where / "uv"
    fake.parent.mkdir(parents=True)
    fake.write_text('#!/bin/sh\necho "fake uv: $*"\n')
    fake.chmod(0o755)
    r = subprocess.run([str(BIN / "tacctl"), "fit"], env=_no_uv_env(tmp_path, home), capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("fake uv: run -q --no-project --python >=3.10 ")


def test_render_without_uv_or_deps_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(render_piece, "_deps_ok", lambda: False)
    monkeypatch.setattr("uvfind.find_uv", lambda: None)
    assert render_piece.run_with_timeout(tmp_path, tmp_path / "out", 5, 80, 66) == 127
    assert capsys.readouterr().err.strip() == uvfind.MISSING


def test_session_start_says_so_once_and_runs_nothing(tmp_path, monkeypatch):
    import nudge

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("the hook ran a subprocess"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("the hook ran a subprocess"))
    assert nudge.uv_line() == uvfind.MISSING
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    monkeypatch.setattr(nudge, "sync_helper", lambda: None)
    monkeypatch.setattr(nudge, "message", lambda *a: None)
    with redirect_stdout(out):
        assert nudge.main() == 0
    assert json.loads(out.getvalue())["systemMessage"].splitlines()[0] == uvfind.MISSING


def test_session_start_is_silent_about_uv_when_it_is_there(tmp_path, monkeypatch):
    import nudge

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "uv").write_text("#!/bin/sh\n")
    (home / ".local" / "bin" / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(home))
    assert nudge.uv_line() is None


def test_create_tells_claude_to_stop_on_a_missing_uv():
    create = (ROOT / "plugins" / "tac-studio" / "commands" / "create.md").read_text()
    assert "Exit 127" in create and "don't retry" in create
