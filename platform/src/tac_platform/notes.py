"""human_role from notes.md, computed and never trusted from meta.

Mirror of plugins/tac-studio/lib/notes.py (direction_section / direction / human_role),
copied rather than imported so the API image doesn't depend on the plugin tree.
tests/test_notes.py asserts both give the same answers.
"""

import re

HUMAN_ROLES = ("none", "seeded", "directed")
_DIR_LINE = re.compile(r"^- (seed|pick|note)\b[^:]*:\s*(.*\S)", re.M)


def direction_section(text: str) -> str:
    m = re.search(r"^##\s+direction\s*$", text, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def human_role(text: str | None) -> str:
    """directed = picked a concept or gave an iteration note; seeded = only a seed; none = nothing recorded."""
    kinds = {kind for kind, _ in _DIR_LINE.findall(direction_section(text or ""))}
    if kinds & {"pick", "note"}:
        return "directed"
    return "seeded" if "seed" in kinds else "none"
