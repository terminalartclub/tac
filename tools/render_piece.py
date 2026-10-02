"""Stable CLI entry point: runs plugins/tac-studio/lib/render_piece.py (single source shared with the plugin).

See that file's docstring for usage; the README documents the contract the platform relies on.
"""

import runpy
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "plugins" / "tac-studio" / "lib"
sys.path.insert(0, str(LIB))
runpy.run_path(str(LIB / "render_piece.py"), run_name="__main__")
