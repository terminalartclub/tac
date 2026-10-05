"""Where pieces live. New pieces: $TAC_WORK, else ~/tac-work. An existing piece by name: $TAC_WORK only when
set; else ./tac-work/<name> if it exists, else ~/tac-work/<name>. A stray ./tac-work never hides ~/tac-work."""

from pathlib import Path

import pytest

import review
import tacctl
import termwin


@pytest.fixture
def clean(monkeypatch, tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "Desktop"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.delenv("TAC_WORK")  # conftest's private default
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(tacctl, "_BOTH_NOTED", set())
    return home, cwd


def _piece(root: Path, name: str) -> Path:
    (root / name).mkdir(parents=True)
    (root / name / f"{name}.py").write_text(f"# {name}\n")
    return root / name


@pytest.fixture
def no_window(monkeypatch):
    opened = []
    monkeypatch.setattr(termwin, "open_play_window",
                        lambda argv, **kw: opened.append(argv) or (termwin.Opened("Terminal", None), ""))
    monkeypatch.setattr(review, "build", lambda root, **kw: root / "index.html")
    return opened


def test_a_stray_local_tac_work_does_not_hide_the_home_piece(clean, no_window, capsys):
    """The operator's bug: /tac:play kettle from ~/Desktop, which has an old ./tac-work without kettle."""
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    kettle = _piece(home / "tac-work", "kettle")
    assert tacctl.piece_dir("kettle") == kettle
    assert tacctl.main(["play", "kettle", "--no-page"]) == 0
    assert no_window[0][-1] == str((kettle / "kettle.py").resolve())
    assert "note:" not in capsys.readouterr().err  # nothing to say: it is only in one place


def test_start_from_such_a_directory_creates_in_home(clean, capsys):
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    assert tacctl.main(["start", "ember"]) == 0
    assert (home / "tac-work" / "ember" / "meta.yaml").is_file()
    assert not (cwd / "tac-work" / "ember").exists()
    assert tacctl.main(["direct", "fresh", "seed", "rain"]) == 0  # any first write lands in ~/tac-work too
    assert (home / "tac-work" / "fresh" / "notes.md").is_file() and not (cwd / "tac-work" / "fresh").exists()


def test_a_piece_in_the_local_folder_is_still_found(clean, no_window):
    home, cwd = clean
    old = _piece(cwd / "tac-work", "old-thing")
    assert tacctl.piece_dir("old-thing") == old
    assert tacctl.main(["direct", "old-thing", "note", "warmer"]) == 0  # an existing local piece stays put
    assert "warmer" in (old / "notes.md").read_text() and not (home / "tac-work" / "old-thing").exists()


def test_the_same_name_in_both_uses_the_local_one_and_says_so_once(clean, no_window, capsys):
    home, cwd = clean
    local = _piece(cwd / "tac-work", "kettle")
    _piece(home / "tac-work", "kettle")
    assert tacctl.piece_dir("kettle") == local
    tacctl.piece_dir("kettle")
    err = capsys.readouterr().err
    assert err.count("note: kettle is in both") == 1
    assert str(local) in err and str(home / "tac-work" / "kettle") in err


def test_listing_shows_both_roots_labelled_once_per_name(clean, no_window, capsys):
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    _piece(cwd / "tac-work", "kettle")
    _piece(home / "tac-work", "kettle")
    _piece(home / "tac-work", "ember")
    (home / "tac-work" / "not a piece").mkdir()
    assert tacctl.main(["play"]) == 0
    out = capsys.readouterr().out.splitlines()[0]
    items = sorted(out.split(": ", 1)[1].split(", "))
    assert items == ["ember (~/tac-work)", "kettle (./tac-work)", "old-thing (./tac-work)"]


def test_review_page_shows_pieces_from_both_roots(clean, monkeypatch):
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    _piece(home / "tac-work", "ember")
    page = review.build(tacctl.home_root(), render=False, dirs=tacctl.list_pieces())
    text = page.read_text()
    assert page == home / "tac-work" / "index.html"
    assert (cwd / "tac-work" / "old-thing").resolve().as_uri() in text  # absolute links: either root works
    assert (home / "tac-work" / "ember").resolve().as_uri() in text


def test_tac_work_overrides_both(clean, monkeypatch, tmp_path, capsys):
    home, cwd = clean
    _piece(cwd / "tac-work", "kettle")
    _piece(home / "tac-work", "kettle")
    chosen = tmp_path / "chosen"
    monkeypatch.setenv("TAC_WORK", str(chosen))
    assert tacctl.piece_dir("kettle") == chosen / "kettle"  # only that root, even though it isn't there
    assert tacctl.search_roots() == [chosen]
    assert tacctl.main(["start", "ember"]) == 0 and (chosen / "ember").is_dir()
    assert "note:" not in capsys.readouterr().err


def test_cwd_is_home_means_one_root_and_no_note(clean, monkeypatch, capsys):
    home, cwd = clean
    _piece(home / "tac-work", "kettle")
    monkeypatch.chdir(home)  # ./tac-work IS ~/tac-work
    assert tacctl.local_root() is None and tacctl.search_roots() == [home / "tac-work"]
    assert tacctl.piece_dir("kettle") == home / "tac-work" / "kettle"
    assert capsys.readouterr().err == ""


def test_root_prints_the_creation_root_and_a_piece_folder(clean, capsys):
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    assert tacctl.main(["root"]) == 0
    cap = capsys.readouterr()
    assert cap.out.strip() == str(home / "tac-work")  # one line on stdout: where new pieces go
    assert "also searched by name" in cap.err and str(cwd / "tac-work") in cap.err
    assert tacctl.main(["root", "old-thing"]) == 0
    assert capsys.readouterr().out.strip() == str(cwd / "tac-work" / "old-thing")


def test_session_attribution_sees_pieces_in_either_root(clean, monkeypatch):
    home, cwd = clean
    _piece(cwd / "tac-work", "old-thing")
    _piece(home / "tac-work", "kettle")
    monkeypatch.setenv("TAC_SESSION_ID", "s-1")
    tacctl.record_session("old-thing")
    tacctl.record_session("kettle")
    tacctl.record_session("old-thing")  # back to the first piece: a new line, found across roots
    assert len(tacctl.read_sessions(cwd / "tac-work" / "old-thing")) == 2
    assert len(tacctl.read_sessions(home / "tac-work" / "kettle")) == 1
    assert {d.name for d in tacctl.all_piece_dirs()} == {"old-thing", "kettle"}
