"""uvicorn's access log must not carry secret query values (admin token, OAuth code/state)."""

import logging

import pytest

from tac_platform.main import RedactTokens

FMT = '%s - "%s %s HTTP/%s" %d'  # uvicorn 0.5x access line


def _line(path: str) -> str:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, FMT,
                               ("1.2.3.4:5000", "GET", path, "1.1", 303), None)
    assert RedactTokens().filter(record) is True  # redacts, never drops the line
    return record.getMessage()


@pytest.mark.parametrize("path,want", [
    ("/v1/auth/web/github/callback?code=50aa1b2c3d&state=cmV0.nonce.sig",
     "/v1/auth/web/github/callback?code=REDACTED&state=REDACTED"),
    ("/device/github/callback?state=ABCD-EFGH.n.s&code=50aa1b2c3d",
     "/device/github/callback?state=REDACTED&code=REDACTED"),
    ("/admin/login?token=s3cret", "/admin/login?token=REDACTED"),
    ("/device?code=BCDF-GHJK", "/device?code=REDACTED"),
    ("/v1/auth/web/github/callback?code=&state=x", "/v1/auth/web/github/callback?code=REDACTED&state=REDACTED"),
    ("/v1/auth/web/login?return=/me", "/v1/auth/web/login?return=/me"),  # everything else untouched
    ("/admin?page=2&find=alex", "/admin?page=2&find=alex"),
    ("/v1/x?barcode=1&statement=2", "/v1/x?barcode=1&statement=2"),  # names match exactly, not as suffixes
])
def test_access_log_redacts_secret_query_values(path, want):
    line = _line(path)
    assert line == f'1.2.3.4:5000 - "GET {want} HTTP/1.1" 303'
    assert "50aa1b2c3d" not in line and "s3cret" not in line


def test_filter_is_installed_on_the_access_logger(monkeypatch):
    from tac_platform import main

    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: None)
    logger = logging.getLogger("uvicorn.access")
    before = list(logger.filters)
    try:
        main.run()
        assert any(isinstance(f, RedactTokens) for f in logger.filters)
    finally:
        logger.filters[:] = before
