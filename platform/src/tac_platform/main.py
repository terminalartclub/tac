"""`uv run tac-platform`: serve the API with uvicorn."""

import logging
import re

import uvicorn

from .config import Settings

TOKEN_RE = re.compile(r"(token=)[^&\s]+")


class RedactTokens(logging.Filter):
    """Keep /admin/login?token=... out of the access log."""

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
