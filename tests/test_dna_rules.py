"""The DNA's two layers (hard rules, house style) and the precedence between them and the person's taste.
Prompt surface: these pin the load-bearing lines so an edit can't silently drop one."""

import json
import re

from check_piece import check_dir, check_source
from conftest import ROOT

SKILL = ROOT / "plugins" / "tac-studio" / "skills" / "tac-studio"
DNA = (SKILL / "DNA.md").read_text()
SKILL_MD = (SKILL / "SKILL.md").read_text()


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def test_precedence_is_stated_once_with_insisting_as_the_only_override() -> None:
    assert DNA.count("## Precedence") == 1
    assert "hard rules > what the person insisted on > house style." in DNA
    prec = _section(DNA, "## Precedence", "## Hard rules")
    assert "read **within** house style" in prec  # a seed alone doesn't override
    assert "name that once, in one line" in prec and "Don't argue the" in prec
    assert "no, I want full" in prec and "contradicts house style" in prec  # what insisting means
    assert 'tacctl direct <name> note "<their words>"' in prec and "don't raise it again" in prec
    assert "Nobody directing" in prec and "full house look, unchanged" in prec


def test_hard_rules_are_all_still_there() -> None:
    hard = _section(DNA, "## Hard rules", "# House style")
    for rule in ("No franchise IP or brands", "Near-black ground", "Seamless loop", "Real-world scale",
                 "Renderable", "No strobe", "submission lint"):
        assert rule in hard, rule
    assert "a 60 s loop at 10–15 fps passes" in hard and "≤ 900" in hard


def test_palette_and_pacing_are_house_style_not_hard_rules() -> None:
    hard = _section(DNA, "## Hard rules", "# House style")
    house = DNA[DNA.index("# House style"):]
    assert "One dominant accent" in house and "One dominant accent" not in hard
    assert "80% of the frame" in house and "80%" not in hard
    assert "lineup" in house and "lineup" not in hard
    assert "Every piece is checked against it" not in DNA  # the old all-binding wording


def test_skill_defers_to_the_one_precedence_rule() -> None:
    assert "DNA.md's rules beat both" not in SKILL_MD
    assert "A per-run seed or note beats the style file" not in SKILL_MD
    assert SKILL_MD.count('DNA.md, "Precedence"') >= 2  # standing style + notes both point at it
    assert "the person insisting" in SKILL_MD


def test_render_budget_in_the_dna_matches_the_platform() -> None:
    cfg = (ROOT / "platform" / "src" / "tac_platform" / "config.py").read_text()
    total = float(re.search(r'render_timeout_s: float = ([\d.]+)', cfg).group(1))
    margin = float(re.search(r"RENDER_PIECE_MARGIN_S = ([\d.]+)", cfg).group(1))
    assert f"stops it at {total - margin:.0f} s" in DNA


def test_no_lint_enforces_palette_so_a_directed_full_colour_piece_passes(good) -> None:
    rainbow = (
        "# orchard\nfrom rich.style import Style\n"
        "HUES = ['red', 'orange', 'yellow', 'green', 'blue', 'purple', 'magenta', 'cyan']\n"
        "for frame in range(10):\n    canvas.clear()\n"
        "    canvas.write(Text(''.join('●' for _ in HUES), style=Style(color=HUES[frame % 8], bgcolor='rgb(8,8,15)')))\n"
        "    await sleep(0.1)\n"
    )
    assert check_source(rainbow) == []
    (good / "piece.py").write_text(rainbow)
    assert check_dir(good) == []


def test_plugin_version_is_0_1_5() -> None:
    meta = json.loads((ROOT / "plugins" / "tac-studio" / ".claude-plugin" / "plugin.json").read_text())
    assert meta["version"] == "0.1.5"
