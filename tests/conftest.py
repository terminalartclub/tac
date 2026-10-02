import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "plugins" / "tac-studio" / "lib"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT / "plugins" / "tac-studio" / "scripts"))


@pytest.fixture
def good(tmp_path: Path) -> Path:
    d = tmp_path / "good"
    shutil.copytree(FIXTURES / "good", d)
    return d
