"""Shared request helpers: errors, client IP, rate limits, hashing, HTML shell."""

import hashlib
import html
import random
import time

from fastapi import Request

from .config import Settings
from .db import Database


class ApiError(Exception):
    def __init__(self, status: int, error: str, **extra) -> None:
        self.status = status
        self.error = error
        self.extra = extra


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy:
        fly = request.headers.get("fly-client-ip")
        if fly:
            return fly.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_key(request: Request) -> str:
    """Stable, non-reversible id for an IP (salted, so the DB holds no raw IPs)."""
    state = request.app.state
    return sha256_hex(state.secret + "|" + client_ip(request, state.settings))[:32]


async def take_rate_token(db: Database, key: str, limit: int, window_s: float) -> bool:
    """Atomically record one event for `key` unless `limit` events already fall inside the window.

    A single INSERT ... SELECT ... WHERE count < limit, so two concurrent callers can never
    both squeeze in as the limit-th event.
    """
    now = time.time()
    inserted = await db.execute(
        "INSERT INTO rate_events (key, at) SELECT ?, ?"
        " WHERE (SELECT COUNT(*) FROM rate_events WHERE key = ? AND at > ?) < ?",
        (key, now, key, now - window_s, limit),
    )
    if inserted and random.random() < 0.02:  # occasional GC of old events
        await db.execute("DELETE FROM rate_events WHERE at < ?", (now - 2 * 86400,))
    return inserted == 1


CSS = """
:root { --bg:#14161a; --panel:#1d2026; --line:#2c3038; --text:#d9dce1; --muted:#8b919c;
  --accent:#e39a4c; --ok:#6cc08b; --bad:#e06c6c; }
@media (prefers-color-scheme: light) { :root { --bg:#f4f3ef; --panel:#fbfaf7; --line:#dcd9d1;
  --text:#25272b; --muted:#6b6f77; --accent:#b8661c; --ok:#2e8b57; --bad:#b83a3a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 Roboto, system-ui, sans-serif; }
main { max-width:1080px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-weight:500; font-size:22px; margin:0 0 4px; } h2 { font-weight:500; font-size:17px; margin:0 0 8px; }
.muted { color:var(--muted); } code, pre, .mono { font-family:"Roboto Mono", ui-monospace, monospace; font-size:13px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px; margin:16px 0; }
label { display:block; margin:12px 0 4px; color:var(--muted); font-size:13px; }
input[type=text] { width:100%; max-width:320px; padding:9px 11px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--text); font:15px "Roboto Mono", monospace; }
button { padding:8px 14px; border-radius:7px; border:1px solid var(--line); background:var(--panel);
  color:var(--text); font:500 14px Roboto, sans-serif; cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#14161a; }
button.bad { border-color:var(--bad); color:var(--bad); }
.err { color:var(--bad); } .ok { color:var(--ok); }
pre { background:var(--bg); border:1px solid var(--line); border-radius:7px; padding:12px; max-height:420px;
  overflow:auto; white-space:pre; }
"""


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Roboto:wght@400;500"
        "&family=Roboto+Mono&display=swap'>"
        f"<style>{CSS}</style></head><body><main>{body}</main></body></html>"
    )
