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
    # decoded keys: what the app parses as `code` is redacted however it was spelled on the wire
    ("/v1/auth/web/github/callback?%63ode=50aa1b2c3d&state=S", "/v1/auth/web/github/callback?%63ode=REDACTED&state=REDACTED"),
    ("/v1/auth/web/github/callback?c%6Fde=50aa1b2c3d", "/v1/auth/web/github/callback?c%6Fde=REDACTED"),
    ("/device/github/callback?code=A&code=50aa1b2c3d", "/device/github/callback?code=REDACTED&code=REDACTED"),
    ("/device/github/callback?CODE=50aa1b2c3d", "/device/github/callback?CODE=REDACTED"),
    ("/device/github/callback?code=50aa%261b2c3d&state=x", "/device/github/callback?code=REDACTED&state=REDACTED"),
    # a secret hidden inside another pair: the whole pair goes (over-redacting a harmless return= is fine)
    ("/device/github/callback?x=1;code=50aa1b2c3d", "/device/github/callback?REDACTED"),
    ("/device/github/callback?code%3D50aa1b2c3d", "/device/github/callback?REDACTED"),
    ("/v1/auth/web/login?return=/me?state=keepme", "/v1/auth/web/login?REDACTED"),
    # sig: the 7-day signed preview link and render-io presigned URLs
    ("/v1/submissions/abc/preview.webp?exp=1&sig=50aa1b2c3d", "/v1/submissions/abc/preview.webp?exp=1&sig=REDACTED"),
    ("/v1/render-io/submissions/x/render/og.jpg?m=GET&exp=2&sig=50aa1b2c3d",
     "/v1/render-io/submissions/x/render/og.jpg?m=GET&exp=2&sig=REDACTED"),
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
    error = logging.getLogger("uvicorn.error")
    before_error = list(error.filters)
    try:
        main.run()
        assert any(isinstance(f, RedactTokens) for f in logger.filters)
        assert any(isinstance(f, RedactTokens) for f in error.filters)  # WebSocket request lines go here
    finally:
        logger.filters[:] = before
        error.filters[:] = before_error


def test_websocket_line_shape_is_redacted_too():
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" %s',
                               ("1.2.3.4:5000", "/device?code=50aa1b2c3d", 403), None)
    RedactTokens().filter(record)
    assert record.getMessage() == '1.2.3.4:5000 - "WebSocket /device?code=REDACTED" 403'
