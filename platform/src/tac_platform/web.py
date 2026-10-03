"""Shared request helpers: errors, client IP, rate limits, hashing, HTML shell."""

import hashlib
import html
import ipaddress
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


def ip_bucket(ip: str) -> str:
    """The unit one client is counted as. IPv4: the address. IPv6: its /64, because one subscriber
    usually gets a whole /64 and can rotate through 2^64 addresses to inflate views or dodge limits.
    An IPv4-mapped IPv6 address counts as its IPv4. Anything unparseable is used as-is."""
    try:
        addr = ipaddress.ip_address(ip.strip().split("%", 1)[0])
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def client_key(request: Request, settings: Settings) -> str:
    """client_ip folded to its counting bucket (ip_bucket); what every hash and rate key is built from."""
    return ip_bucket(client_ip(request, settings))


def ip_key(request: Request) -> str:
    """Stable, non-reversible id for a client (salted, so the DB holds no raw IPs). IPv6 = its /64."""
    state = request.app.state
    return sha256_hex(state.secret + "|" + client_key(request, state.settings))[:32]


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


# The one stylesheet for every server-rendered page (sign-in, device flow, admin): terminalart.club tokens,
# copied from the site's style.css :root. Inline is fine: the platform sends no CSP.
CSS = """
:root { --bg:#08080f; --bg-elevated:#0f0f1a; --bg-card:#111122; --text:#b0b0c0; --text-bright:#e0e0ee;
  --text-dim:#8a8aa6; --text-faint:#606078; --accent-cyan:#00e5c3; --accent-purple:#a78bfa; --accent-pink:#f472b6;
  --accent-amber:#fbbf24; --border:#1a1a2e; --border-light:#252540; --radius:8px;
  --mono:'JetBrains Mono','Fira Code','Cascadia Code','SF Mono',monospace; color-scheme:dark; }
*, *::before, *::after { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.7 var(--mono);
  -webkit-font-smoothing:antialiased; }
main { max-width:520px; margin:0 auto; padding:32px 16px 64px; }
main.wide { max-width:1080px; }
a { color:var(--accent-cyan); }
:focus-visible { outline:2px solid var(--accent-cyan); outline-offset:2px; }
.wordmark { display:inline-block; margin:0 0 28px; font-weight:600; font-size:17px; letter-spacing:-0.02em;
  text-decoration:none; }
.accent-cyan { color:var(--accent-cyan); } .accent-purple { color:var(--accent-purple); }
.accent-pink { color:var(--accent-pink); }
h1 { color:var(--text-bright); font-weight:600; font-size:20px; margin:0 0 4px; }
h2 { color:var(--text-bright); font-weight:600; font-size:16px; margin:0 0 8px; }
.muted { color:var(--text-dim); }
h1 .sub { display:block; font-size:14px; font-weight:400; color:var(--text-dim); }
code, pre, input, button { font-family:var(--mono); }
code, pre { font-size:13px; }
.card { background:var(--bg-card); border:1px solid var(--border-light); border-radius:var(--radius);
  padding:24px; margin:16px 0; }
label { display:block; margin:14px 0 6px; color:var(--text-dim); font-size:13px; }
input[type=text] { width:100%; min-height:44px; padding:10px 12px; border-radius:var(--radius);
  border:1px solid var(--border-light); background:var(--bg-elevated); color:var(--text-bright); font-size:15px; }
input[type=text]::placeholder { color:var(--text-dim); }
input[type=text]:focus { outline:none; border-color:var(--accent-cyan); }
input[type=text]:focus-visible { outline:none; border-color:var(--accent-cyan); box-shadow:0 0 0 1px var(--accent-cyan); }
button { display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:0 16px;
  border-radius:var(--radius); border:1px solid var(--border-light); background:none; color:var(--text-bright);
  font-size:14px; cursor:pointer; transition:filter .15s, border-color .15s; }
button:hover { border-color:var(--text-dim); }
button.primary { min-height:48px; padding:0 28px; border:0; background:var(--accent-cyan); color:var(--bg);
  font-weight:600; }
button.primary:hover { filter:brightness(1.08); }
button.bad { border-color:var(--accent-pink); color:var(--accent-pink); }
.err { color:var(--accent-pink); } .ok { color:var(--accent-cyan); }
label.agree { display:flex; gap:10px; align-items:flex-start; margin:18px 0 4px; color:var(--text); font-size:14px;
  cursor:pointer; }
label.agree input { width:18px; height:18px; margin:1px 0 0; flex:none; accent-color:var(--accent-cyan); }
label.agree a { color:var(--accent-cyan); }
pre { background:var(--bg-elevated); border:1px solid var(--border-light); border-radius:var(--radius);
  padding:12px; max-height:420px; overflow:auto; white-space:pre; color:var(--text); }
/* admin */
.item { display:grid; grid-template-columns:minmax(0,320px) minmax(0,1fr); gap:20px; }
@media (max-width:760px) { .item { grid-template-columns:1fr; } }
.item img.preview { width:100%; border-radius:var(--radius); background:var(--bg); image-rendering:pixelated; }
.procs { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; } .procs img { width:72px; border-radius:4px; }
.kv { display:grid; grid-template-columns:max-content 1fr; gap:2px 12px; font-size:13px; }
.kv dt { color:var(--text-dim); } .kv dd { margin:0; overflow-wrap:anywhere; }
.chip { display:inline-block; padding:1px 8px; border-radius:99px; border:1px solid var(--accent-pink);
  color:var(--accent-pink); font-size:12px; margin:0 4px 4px 0; }
.actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:12px; }
.actions input[type=text] { max-width:260px; }
section > h2 { margin-top:32px; } .count { color:var(--text-dim); font-weight:400; }
table.audit { width:100%; border-collapse:collapse; font-size:12px; }
table.audit td { padding:3px 6px; border-top:1px solid var(--border-light); vertical-align:top; }
table.audit th { padding:3px 6px; text-align:left; color:var(--text-dim); font-weight:400; }
.chip.warn { border-color:var(--accent-amber); color:var(--accent-amber); }
.nav { margin:0 0 8px; font-size:13px; }
"""
FONTS = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap"
WORDMARK = (
    "<span class=accent-cyan>terminal</span> <span class=accent-purple>art</span> <span class=accent-pink>club</span>"
)


def site_home(request: Request) -> str:
    """Where the wordmark links: TAC_SITE_URL, or "/" (the site root behind the dev /api proxy)."""
    return request.app.state.settings.site_url or "/"


def page(title: str, body: str, home: str = "/", wide: bool = False) -> str:
    """Every server-rendered HTML page: the shared stylesheet, the wordmark, then `body` (already escaped)."""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta name=color-scheme content=dark>"
        f"<title>{html.escape(title)}</title>"
        "<link rel=preconnect href='https://fonts.googleapis.com'>"
        "<link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
        f"<link rel=stylesheet href='{FONTS}'>"
        f"<style id=tac-style>{CSS}</style></head><body><main{' class=wide' if wide else ''}>"
        f"<a class=wordmark href='{html.escape(home, quote=True)}'>{WORDMARK}</a>{body}</main></body></html>"
    )
