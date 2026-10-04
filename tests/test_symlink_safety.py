"""A cloned repo's tac-work/ can hold symlinks. No tacctl write may follow one out of its piece folder, and
a launched terminal's marker can't be planted. Repros from the 0.1.2 review (p012s, p012m)."""

import subprocess
from pathlib import Path

import pytest

import tacctl
import termwin


@pytest.fixture
def clean(monkeypatch, tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    monkeypatch.delenv("TAC_WORK")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(tacctl, "_BOTH_NOTED", set())
    return home, repo, tmp_path


def _victim(tmp_path: Path, name: str) -> Path:
    v = tmp_path / name
    (v / "submission").mkdir(parents=True)
    (v / "submission" / "keep.txt").write_text("precious")
    (v / "piece.py").write_text("# not yours\n")
    (v / "meta.yaml").write_text("precious: true\n")
    return v


def _snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_prepare_through_a_symlinked_local_piece_folder_is_refused(clean, capsys):
    home, repo, tmp = clean
    victim2 = _victim(tmp, "victim2")
    (repo / "tac-work").mkdir()
    (repo / "tac-work" / "lamp").symlink_to(victim2)
    before = _snapshot(victim2)
    assert tacctl.main(["prepare", "lamp", "--model", "claude-opus-5-5"]) == 1
    assert "points outside" in capsys.readouterr().err
    assert _snapshot(victim2) == before  # submission/ not rmtree'd


def test_start_through_a_symlinked_home_piece_folder_is_refused(clean, capsys):
    home, repo, tmp = clean
    victim = _victim(tmp, "victim")
    (home / "tac-work").mkdir()
    (home / "tac-work" / "kettle").symlink_to(victim)
    before = _snapshot(victim)
    for argv in (["start", "kettle"], ["direct", "kettle", "note", "x"], ["play", "kettle", "--no-window"]):
        assert tacctl.main(argv) == 1, argv
        assert "points outside" in capsys.readouterr().err
    assert _snapshot(victim) == before  # meta.yaml not rewritten


def test_a_symlinked_file_inside_a_real_piece_folder_is_never_written_through(clean, capsys):
    home, repo, tmp = clean
    victim = _victim(tmp, "victim")
    wd = home / "tac-work" / "kettle"
    wd.mkdir(parents=True)
    (wd / "kettle.py").write_text("# kettle\n")
    (wd / "meta.yaml").symlink_to(victim / "meta.yaml")
    (wd / "notes.md").symlink_to(victim / "piece.py")
    before = _snapshot(victim)
    assert tacctl.main(["start", "kettle"]) == 1
    assert tacctl.main(["direct", "kettle", "seed", "rain"]) == 1
    err = capsys.readouterr().err
    assert err.count("refusing to write through it") == 2 and "Traceback" not in err
    assert _snapshot(victim) == before


def test_a_symlinked_submission_folder_is_a_clean_refusal_not_a_traceback(clean, capsys):
    home, repo, tmp = clean
    victim2 = _victim(tmp, "victim2")
    wd = home / "tac-work" / "lamp"
    wd.mkdir(parents=True)
    (wd / "lamp.py").write_text("# lamp\n")
    (wd / "submission").symlink_to(victim2 / "submission")
    before = _snapshot(victim2)
    assert tacctl.main(["prepare", "lamp", "--model", "claude-opus-5-5"]) == 1
    err = capsys.readouterr().err
    assert "submission is a symlink or points outside" in err and "Traceback" not in err
    assert _snapshot(victim2) == before


def test_a_symlink_to_another_piece_inside_the_root_is_fine(clean):
    home, repo, tmp = clean
    (home / "tac-work" / "real").mkdir(parents=True)
    (home / "tac-work" / "alias").symlink_to(home / "tac-work" / "real")
    assert tacctl.piece_dir("alias") == home / "tac-work" / "alias"


def test_a_symlinked_local_root_is_not_searched(clean):
    home, repo, tmp = clean
    victim = _victim(tmp, "victim")
    (repo / "tac-work").symlink_to(victim.parent)  # ./tac-work -> somewhere else entirely
    assert tacctl.local_root() is None
    assert tacctl.piece_dir("victim") == home / "tac-work" / "victim"


def test_a_cwd_inside_home_tac_work_is_not_a_second_root(clean, monkeypatch):
    home, repo, tmp = clean
    inner = home / "tac-work" / "kettle"
    (inner / "tac-work").mkdir(parents=True)  # a folder named tac-work inside a piece
    monkeypatch.chdir(inner)
    assert tacctl.local_root() is None
    assert tacctl.search_roots() == [home / "tac-work"]


@pytest.mark.parametrize("name", ["tac-work", "submission"])
def test_reserved_names_are_refused(clean, name, capsys):
    assert tacctl.main(["start", name]) == 1
    assert tacctl.NAME_RULE in capsys.readouterr().err


# ── the terminal launch marker ─────────────────────────────────────────────


def _fake_launch(start_marker: bool):
    calls = []

    def popen(argv, **kw):
        calls.append(argv)
        if start_marker:
            Path(argv[argv.index("sh") + 3]).mkdir()

        class P:
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(argv, timeout)
        return P()
    return popen, calls


def _marker_dirs(tmp_path):
    return sorted(tmp_path.glob("tac-play-*"))


def test_marker_is_made_with_mkdir_not_a_symlink_following_redirect():
    argv = termwin.linux_argv("/x/konsole", ["tac", "play", "/p.py"], marker="/m")
    script = argv[argv.index("sh") + 2]
    first = script.split(";")[0]
    assert first == 'mkdir "$0" 2>/dev/null'  # the marker: mkdir, never `: > "$0"` (which follows a symlink)
    assert '> "$0"' not in script


def test_confirmed_launch_cleans_up_its_private_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(termwin.tempfile, "tempdir", str(tmp_path))
    popen, _ = _fake_launch(start_marker=True)
    opened, _ = termwin.open_play_window(["tac", "play", "/p.py"], platform="linux", env={"DISPLAY": ":0"},
                                         which={"konsole": "/usr/bin/konsole"}.get, popen=popen, sleep=lambda s: None)
    assert opened.confirmed and _marker_dirs(tmp_path) == []


def test_unconfirmed_launch_keeps_its_private_dir(tmp_path, monkeypatch):
    """A late shell will still run `mkdir "$0"`: the 0700 dir stays, so nobody else can re-create its path."""
    monkeypatch.setattr(termwin.tempfile, "tempdir", str(tmp_path))
    popen, _ = _fake_launch(start_marker=False)
    opened, _ = termwin.open_play_window(["tac", "play", "/p.py"], platform="linux", env={"DISPLAY": ":0"},
                                         which={"konsole": "/usr/bin/konsole"}.get, popen=popen, sleep=lambda s: None)
    assert not opened.confirmed
    (kept,) = _marker_dirs(tmp_path)
    assert kept.stat().st_mode & 0o777 == 0o700


def test_a_real_sh_never_follows_a_planted_marker_symlink(tmp_path):
    """The script itself, under a real sh: a symlink planted at the marker path is left alone."""
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    marker = tmp_path / "marker"
    marker.symlink_to(victim)
    argv = termwin.linux_argv("/x/konsole", ["true"], marker=str(marker))
    sh = argv[argv.index("sh"):]
    script = sh[2].split("; printf")[0]  # just mkdir + the command, without the wait for Enter
    subprocess.run(["sh", "-c", script, *sh[3:]], check=False, timeout=10)
    assert victim.read_text() == "precious" and marker.is_symlink()
