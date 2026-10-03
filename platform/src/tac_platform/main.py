"""`uv run tac-platform`: serve the API with uvicorn."""

import logging
import re

import uvicorn

from .config import Settings

# Query values that must never reach the access log: the admin token (/admin/login?token=), and the OAuth
# code + state on /v1/auth/web/github/callback and /device/github/callback (single-use, short-lived, still
# secrets). Matched by parameter name anywhere in the query, so /device?code=<user code> is redacted too.
TOKEN_RE = re.compile(r"([?&](?:token|code|state)=)[^&\s]*")


class RedactTokens(logging.Filter):
    """Redact secret query values (TOKEN_RE) in uvicorn.access lines; the rest of the line is kept."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(TOKEN_RE.sub(r"\1REDACTED", a) if isinstance(a, str) else a for a in record.args)
        return True


def run() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").addFilter(RedactTokens())
    uvicorn.run(
        "tac_platform.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        log_config=None,
    )
