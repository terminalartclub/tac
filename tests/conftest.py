import shutil
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "plugins" / "tac-studio" / "lib"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT / "plugins" / "tac-studio" / "scripts"))


PROD_HOSTS = ("api.terminalart.club", "terminalart.club")


@pytest.fixture(autouse=True)
def no_prod_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin defaults to the public platform. Tests start from a clean env (no TAC_API / TAC_SITE_URL
    leaking in from a dev shell), and any test that would actually reach a prod host fails loudly instead:
    a test that needs a platform pins TAC_API to its own fake."""
    for var in ("TAC_API", "TAC_SITE_URL"):
        monkeypatch.delenv(var, raising=False)
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if isinstance(host, str) and host.lower().rstrip(".") in PROD_HOSTS:
            raise AssertionError(f"test tried to reach the prod platform ({host}): pin TAC_API to a fake")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture
def good(tmp_path: Path) -> Path:
    d = tmp_path / "good"
    shutil.copytree(FIXTURES / "good", d)
    return d
