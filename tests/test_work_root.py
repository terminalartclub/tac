"""One home for pieces: $TAC_WORK > ./tac-work if it exists here > ~/tac-work. Always absolute."""

from pathlib import Path

import pytest

import review
import tacctl
import termwin


@pytest.fixture
def clean(monkeypatch, tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "somewhere"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.delenv("TAC_WORK")  # conftest's private default
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(tacctl, "_LOCAL_NOTE_SHOWN", False)
    return home, cwd


def test_tac_work_env_wins_even_over_a_local_folder(clean, monkeypatch, tmp_path):
    home, cwd = clean
    (cwd / "tac-work").mkdir()
    monkeypatch.setenv("TAC_WORK", str(tmp_path / "chosen"))
    assert tacctl.work_root() == tmp_path / "chosen"
    monkeypatch.setenv("TAC_WORK", "~/elsewhere")
    assert tacctl.work_root() == home / "elsewhere"


def test_an_existing_local_tac_work_wins_and_is_announced_once(clean, capsys):
    home, cwd = clean
    (cwd / "tac-work").mkdir()
    assert tacctl.work_root() == cwd / "tac-work" and tacctl.work_root().is_absolute()
    tacctl.work_root()
    err = capsys.readouterr().err
    assert err.count("using ./tac-work") == 1


def test_no_local_folder_means_the_home_folder_created_on_demand(clean, capsys):
    home, cwd = clean
    assert tacctl.work_root() == home / "tac-work"
    assert not (home / "tac-work").exists()  # a read doesn't create it
    assert "using ./tac-work" not in capsys.readouterr().err
    assert tacctl.main(["start", "kettle"]) == 0
    assert (home / "tac-work" / "kettle" / "meta.yaml").is_file()
    assert not (cwd / "tac-work").exists()
    assert tacctl.main(["root"]) == 0
    assert capsys.readouterr().out.strip().endswith(str(home / "tac-work"))


def test_play_finds_a_piece_made_from_another_directory(clean, monkeypatch, tmp_path, capsys):
    """The bug: kettle was made in one project, /tac:play kettle run from ~/Desktop found nothing."""
    home, cwd = clean
    (home / "tac-work" / "kettle").mkdir(parents=True)
    (home / "tac-work" / "kettle" / "kettle.py").write_text("# kettle\n")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.chdir(desktop)
    opened = []
    monkeypatch.setattr(termwin, "open_play_window", lambda argv: opened.append(argv) or (termwin.Opened("Terminal", None), ""))
    monkeypatch.setattr(review, "build", lambda root, render, only: root / "index.html")
    assert tacctl.main(["play", "kettle"]) == 0
    assert opened[0][-1] == str((home / "tac-work" / "kettle" / "kettle.py").resolve())
    assert tacctl.main(["play"]) == 0
    assert "kettle" in capsys.readouterr().out
