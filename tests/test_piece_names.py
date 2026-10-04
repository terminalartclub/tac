"""Every command that takes a piece name resolves it through piece_dir(): a bad name is refused before
anything touches the filesystem, and nothing is ever created outside the work root."""

import os
from pathlib import Path

import pytest

import tacctl

BAD = ["../escaped-dir", "../../etc", "/abs/path", "a/b", "", "kéttle", "kettle\n", "Kettle", "a..b", ".", ".."]


def _commands(name: str) -> list[list[str]]:
    return [
        ["start", name],
        ["direct", name, "note", "warmer"],
        ["prepare", name, "--model", "claude-opus-5-5"],
        ["submit", name, "--model", "claude-opus-5-5", "--confirm-rights"],
        ["style", "--log", name],
        ["play", name, "--no-window", "--no-page"],
    ]


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    root = outer / "work" / "tac-work"
    root.mkdir(parents=True)
    monkeypatch.setenv("TAC_WORK", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(outer / "config"))
    (outer / "config" / "tac").mkdir(parents=True)
    (outer / "config" / "tac" / "style.md").write_text("I like: rain\n")  # so `style --log` has something to log
    tacctl.write_private(tacctl.cred_path(), {"access_token": "tok-123", "handle": "alex"})
    monkeypatch.chdir(outer / "work")
    return outer, root


@pytest.mark.parametrize("name", BAD)
def test_every_command_refuses_a_bad_name_and_touches_nothing(sandbox, name, capsys):
    outer, root = sandbox
    before = _tree(outer)
    for argv in _commands(name):
        rc = tacctl.main(argv)
        assert rc == 1, argv
        assert tacctl.NAME_RULE in capsys.readouterr().err, argv
    assert _tree(outer) == before  # nothing created, inside the root or out
    assert not (outer / "escaped-dir").exists() and not Path("/abs/path").exists()


def test_a_good_name_still_works_everywhere(sandbox, capsys):
    outer, root = sandbox
    assert tacctl.main(["start", "kettle"]) == 0
    assert tacctl.main(["direct", "kettle", "note", "warmer"]) == 0
    assert tacctl.main(["style", "--log", "kettle"]) == 0
    assert sorted(os.listdir(root)) == ["kettle"]
    assert "warmer" in (root / "kettle" / "notes.md").read_text()


def test_piece_dir_is_the_only_join():
    src = (Path(tacctl.__file__)).read_text()
    assert "work_root() /" not in src and "home_root() / a." not in src  # no command joins a raw name itself
    body = src[src.index("def piece_dir("):src.index("def all_piece_dirs(")]
    assert body.index("if not valid_name(name):") < body.index("home_root() / name")  # validated first
    assert "SLUG_RE.match(" not in src  # fullmatch only: match() lets a trailing newline through
