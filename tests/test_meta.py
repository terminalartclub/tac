import pytest

import meta


def test_roundtrip() -> None:
    m = {"title": "ember", "description": 'says "hi" — ünïcode: yes', "handle": "a-b", "model": "claude-opus-5-5",
         "tokens": None, "iterations": 3, "loop_s": 30.0, "license": meta.DEFAULT_LICENSE,
         "created": "2026-10-02", "house_artist": True, "process_notes": ["a: b", ""]}
    assert meta.loads_yaml(meta.dumps_yaml(m)) == m


def test_plain_yaml_scalars() -> None:
    got = meta.loads_yaml("# c\ntitle: ember glow\ntokens: 12\nloop_s: 2.5\nok: false\nx: ~\n")
    assert got == {"title": "ember glow", "tokens": 12, "loop_s": 2.5, "ok": False, "x": None}


@pytest.mark.parametrize("text", ["a: 1\na: 2\n", "  a: 1\n", "a 1\n", "a: |\n", "a: 'single'\n", 'a: "unterminated\n'])
def test_rejects(text: str) -> None:
    with pytest.raises(meta.MetaError):
        meta.loads_yaml(text)


@pytest.mark.parametrize("model,label", [
    ("claude-opus-5-5", "Claude Opus 5.5"), ("claude-fable-5-1", "Claude Fable 5.1"),
    ("claude-sonnet-4-20250514", "Claude Sonnet 4"), ("other", "other"),
])
def test_model_label(model: str, label: str) -> None:
    assert meta.model_label(model) == label
