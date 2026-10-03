"""`uv run tac-platform`: serve the API with uvicorn."""

import logging
import re

from urllib.parse import unquote_plus

import uvicorn

from .config import Settings

# Query values that must never reach the logs: the admin token (/admin/login?token=), the OAuth code + state
# (/v1/auth/web/github/callback, /device/github/callback), and sig (signed preview and render-io URLs, which
# are capabilities for days). Matched on the DECODED, case-folded key, so %63ode= or CODE= are caught too.
SECRET_KEYS = frozenset({"token", "code", "state", "sig"})
# A secret key=value hiding inside a pair (x=1;code=..., code%3D..., return=/me?state=...): redact the pair.
SECRET_INSIDE = re.compile(r"(?:^|[^a-z0-9_])(?:token|code|state|sig)=", re.I)


def redact_query(target: str) -> str:
    """'/path?k=v&...' with every secret pair's value replaced by REDACTED. Pairs are split on '&' as the app
    does (Starlette uses parse_qsl); the original encoding of everything kept is left as sent."""
    path, sep, query = target.partition("?")
    if not sep:
        return target
    out = []
    for pair in query.split("&"):
        key, eq, _ = pair.partition("=")
        decoded = unquote_plus(pair)
        if unquote_plus(key).strip().lower() in SECRET_KEYS or SECRET_INSIDE.search(decoded):
            out.append(f"{key}=REDACTED" if eq and unquote_plus(key).strip().lower() in SECRET_KEYS else "REDACTED")
        else:
            out.append(pair)
    return f"{path}?{'&'.join(out)}"


class RedactTokens(logging.Filter):
    """Redact secret query values (redact_query) in request-target args of uvicorn log lines; the rest of
    the line is kept. On uvicorn.access (HTTP) and uvicorn.error (where uvicorn logs WebSocket requests)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_query(a) if isinstance(a, str) and a.startswith("/") and "?" in a else a
                                for a in record.args)
        return True


def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(RedactTokens())
    uvicorn.run(
        "tac_platform.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        log_config=None,
    )
