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
    """The plugin defaults to the public platform. Tests start from a clean env: TAC_API is a dead loopback
    port (subprocesses inherit it, so a launcher test can't reach prod either) and TAC_SITE_URL is unset. Any
    test that would still reach a prod host (terminalart.club or any *.terminalart.club) fails loudly: a test
    that needs a platform pins TAC_API to its own fake, and one that checks the default deletes TAC_API."""
    monkeypatch.setenv("TAC_API", "http://127.0.0.1:9")  # discard port: nothing listens
    monkeypatch.delenv("TAC_SITE_URL", raising=False)
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        h = host.lower().rstrip(".") if isinstance(host, str) else ""
        if h in PROD_HOSTS or h.endswith(".terminalart.club"):
            raise AssertionError(f"test tried to reach the prod platform ({host}): pin TAC_API to a fake")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture
def good(tmp_path: Path) -> Path:
    d = tmp_path / "good"
    shutil.copytree(FIXTURES / "good", d)
    return d
