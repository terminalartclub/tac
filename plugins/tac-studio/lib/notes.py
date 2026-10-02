"""Pull short lines out of a studio notes.md (### iter-K sections, `- field:` bullets)."""

from __future__ import annotations

import re

_ITER = re.compile(r"^###\s+iter-(\d+)\b.*$", re.M)
_H = re.compile(r"^#{2,3}\s", re.M)


def iter_sections(text: str) -> dict[int, str]:
    """iter number → section body. A repeated iter-K (revision passes) keeps the last one,
    except iter-1, which keeps the first (it is the true starting point)."""
    out: dict[int, str] = {}
    for m in _ITER.finditer(text):
        k = int(m.group(1))
        nxt = _H.search(text, m.end())
        body = text[m.end(): nxt.start() if nxt else len(text)]
        if k == 1 and k in out:
            continue
        out[k] = body
    return out


def field(section: str, name: str) -> str:
    """Text of a `- name:` bullet including its indented continuation lines."""
    m = re.search(rf"^- {re.escape(name)}:\s*(.*)$", section, re.M)
    if not m:
        return ""
    parts = [m.group(1)]
    for line in section[m.end():].splitlines()[1:]:
        if not line.startswith((" ", "\t")) or not line.strip():
            break
        parts.append(line.strip())
    return " ".join(parts).strip()


def first_sentence(s: str, limit: int = 180) -> str:
    s = re.sub(r"\*\*|`", "", s).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    s = m.group(1) if m else s
    s = s.rstrip(" .;:—-") + "." if not s.endswith(("!", "?")) else s
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def critique(text: str, k: int, prefer: tuple[str, ...] = ("biggest problem", "works")) -> str:
    sec = iter_sections(text).get(k, "")
    for name in prefer:
        v = field(sec, name)
        if v:
            return first_sentence(v)
    return ""


def last_iter(text: str) -> int:
    secs = iter_sections(text)
    return max(secs) if secs else 0


def catalog_description(text: str) -> str:
    """The last non-empty '## catalog description' paragraph (revisions append newer ones)."""
    best = ""
    for m in re.finditer(r"^#{2,3}\s+catalog description.*$", text, re.M):
        nxt = _H.search(text, m.end())
        body = text[m.end(): nxt.start() if nxt else len(text)].strip()
        para = " ".join(body.split("\n\n")[0].split())
        if para and not para.lower().startswith(("unchanged", "(unchanged")):
            best = para
    return best


# ── human direction (## direction section) ─────────────────────────────────

HUMAN_ROLES = ("none", "seeded", "directed")
_DIR_LINE = re.compile(r"^- (seed|pick|note)\b[^:]*:\s*(.*\S)", re.M)


def direction_section(text: str) -> str:
    m = re.search(r"^##\s+direction\s*$", text, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


def direction(text: str) -> dict[str, list[str]]:
    """{'seed': [...], 'pick': [...], 'note': [...]} from `- seed: …`, `- pick: …`, `- note (iter-K): …`."""
    out: dict[str, list[str]] = {"seed": [], "pick": [], "note": []}
    for kind, value in _DIR_LINE.findall(direction_section(text)):
        out[kind].append(value)
    return out


def human_role(text: str) -> str:
    """Computed, never declared: directed = picked a concept or gave an iteration note;
    seeded = only a seed/theme; none = no human input recorded."""
    d = direction(text)
    if d["pick"] or d["note"]:
        return "directed"
    return "seeded" if d["seed"] else "none"
