import importlib.util
from pathlib import Path

import pytest

from tac_platform.notes import human_role

CASES = [
    (None, "none"),
    ("# x\n\n## concept\n- seed: nope, wrong section\n", "none"),
    ("## direction\n- seed: first light\n\n## concept\nx\n", "seeded"),
    ("## Direction\n- seed: a\n- pick: concept 2\n", "directed"),
    ("## direction\n- note (iter-3): slower fish\n## iteration log\n", "directed"),
    ("## direction\n- seed:\n", "none"),  # empty value does not count
]

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "tac-studio" / "lib" / "notes.py"


@pytest.mark.parametrize(("text", "role"), CASES)
def test_human_role(text, role):
    assert human_role(text) == role


@pytest.mark.skipif(not PLUGIN.exists(), reason="plugin tree not present")
@pytest.mark.parametrize(("text", "role"), CASES)
def test_matches_plugin(text, role):
    spec = importlib.util.spec_from_file_location("plugin_notes", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.human_role(text or "") == human_role(text) == role
