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
def private_work_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pieces default to ~/tac-work: never let a test touch the real one. Tests of the precedence unset this."""
    monkeypatch.setenv("TAC_WORK", str(tmp_path / "tac-work"))


@pytest.fixture(autouse=True)
def plain_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The terminal the tests run in (iTerm2, kitty, Ghostty) must not leak into a test: /tac:wall would probe it
    for pixel mode. Tests of pixel mode set these themselves."""
    for var in ("TERM_PROGRAM", "TERM_PROGRAM_VERSION", "KITTY_WINDOW_ID", "TMUX", "STY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture(autouse=True)
def private_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The wall's cache is ~/.cache/tac/wall: never the real one (a test that saves a playlist would replace the
    user's and drop their cached pieces). Tests that need their own set XDG_CACHE_HOME again."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


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
