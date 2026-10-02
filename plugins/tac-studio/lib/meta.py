"""meta.yaml / meta.json for a TAC piece. Stdlib only.

meta.yaml is deliberately flat: one `key: value` per line. Values are YAML scalars
(null, true/false, int, float, bare or double-quoted string) or a JSON flow list/object,
so every file this module writes is also valid YAML for any other reader.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FIELDS = ("title", "description", "handle", "model", "tokens", "iterations", "loop_s",
          "license", "created")
OPTIONAL = ("size", "human_role", "house_artist", "tokens_source", "process_notes")
DEFAULT_LICENSE = "CC-BY-4.0 art / MIT code"
MODEL_LABELS = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable"}

_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*))?$")
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


class MetaError(ValueError):
    pass


def _scalar(raw: str, lineno: int) -> Any:
    v = raw.strip()
    if v in ("", "~", "null", "Null", "NULL"):
        return None
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v[0] in "\"[{":
        try:
            return json.loads(v)
        except json.JSONDecodeError as e:
            raise MetaError(f"meta.yaml line {lineno}: bad quoted/flow value ({e.msg})") from None
    if v[0] in "'|>&*!":
        raise MetaError(f"meta.yaml line {lineno}: use double quotes; block/anchor syntax isn't supported")
    if _INT.match(v):
        return int(v)
    if _FLOAT.match(v):
        return float(v)
    return v.split(" #", 1)[0].rstrip()


def loads_yaml(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            raise MetaError(f"meta.yaml line {n}: nested/indented values aren't supported (keep it flat)")
        m = _KEY.match(line.rstrip())
        if not m:
            raise MetaError(f"meta.yaml line {n}: expected `key: value`")
        key, raw = m.group(1), m.group(2) or ""
        if key in out:
            raise MetaError(f"meta.yaml line {n}: duplicate key {key!r}")
        out[key] = _scalar(raw, n)
    return out


def dumps_yaml(meta: dict[str, Any]) -> str:
    order = [k for k in (*FIELDS, *OPTIONAL) if k in meta] + sorted(k for k in meta if k not in FIELDS + OPTIONAL)
    lines = []
    for k in order:
        v = meta[k]
        if v is None:
            s = "null"
        elif isinstance(v, bool):
            s = "true" if v else "false"
        elif isinstance(v, (int, float)):
            s = repr(v)
        else:
            s = json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {s}")
    return "\n".join(lines) + "\n"


def load(piece_dir: Path) -> dict[str, Any]:
    """meta.yaml wins over meta.json when both exist."""
    y, j = piece_dir / "meta.yaml", piece_dir / "meta.json"
    if y.exists():
        return loads_yaml(y.read_text(encoding="utf-8"))
    if j.exists():
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise MetaError(f"meta.json: {e}") from None
        if not isinstance(data, dict):
            raise MetaError("meta.json: top level must be an object")
        return data
    raise MetaError("missing meta.yaml (or meta.json)")


def model_label(model: str) -> str:
    """claude-opus-5-5 → Claude Opus 5.5; unknown shapes pass through."""
    m = re.match(r"^claude-([a-z]+)-(\d+)(?:-(\d+))?", model or "")
    if not m:
        return model
    fam = MODEL_LABELS.get(m.group(1), m.group(1).capitalize())
    ver = m.group(2) + (f".{m.group(3)}" if m.group(3) and len(m.group(3)) <= 2 else "")
    return f"Claude {fam} {ver}"
