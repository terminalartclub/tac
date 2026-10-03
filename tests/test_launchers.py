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
        assert exec_line.startswith("uv run -q --no-project "), exec_line
        seen.append(pins(exec_line))
    fallback = " ".join(render_piece.UV_DEPS)
    assert all(d.count("==") == 1 for d in render_piece.UV_DEPS)
    seen.append(pins(" ".join(f"--with {d}" for d in render_piece.UV_DEPS)))
    assert seen[0] == seen[1] == seen[2], (seen, fallback)


def test_render_fallback_passes_no_project(monkeypatch) -> None:
    monkeypatch.setattr(render_piece, "_deps_ok", lambda: False)
    cmd = render_piece._worker_cmd()
    assert cmd[:4] == ["uv", "run", "-q", "--no-project"]
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--with"] == list(render_piece.UV_DEPS)
