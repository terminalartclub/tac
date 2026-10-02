"""Stand-in for builder-community's tools/check_piece.py (same CLI contract).

python check_piece.py <piece_dir> -> stdout {"ok": bool, "reasons": [...]}; exit 0 ok, 1 rejected, 2 usage/IO.
Only used by the platform tests when tools/check_piece.py does not exist yet.
"""

import ast
import json
import sys
from pathlib import Path

BANNED = {"socket", "subprocess", "urllib", "requests", "httpx", "ctypes", "shutil"}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "reasons": ["usage: check_piece.py <piece_dir>"]}))
        return 2
    d = Path(sys.argv[1])
    try:
        src = (d / "piece.py").read_text()
        meta = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "reasons": [f"io: {exc}"]}))
        return 2
    reasons = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        reasons.append(f"piece.py does not parse: line {exc.lineno}")
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            reasons += [f"banned import: {n}" for n in names if n in BANNED]
    if not meta.get("title"):
        reasons.append("meta.title missing")
    if not str(meta.get("model", "")).startswith("claude-"):
        reasons.append("meta.model must be a claude-… id")
    notes_path = d / "notes.md"
    notes = notes_path.read_text() if notes_path.exists() else ""
    declared = meta.get("human_role", "none")
    if declared != "none" and "## direction" not in notes.lower():
        reasons.append(f"human_role {declared!r} is not backed by a ## direction section in notes.md")
    print(json.dumps({"ok": not reasons, "reasons": reasons}))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())
