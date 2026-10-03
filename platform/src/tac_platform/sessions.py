"""Web sessions (cookie `__Host-tac_session` in prod, `tac_session` in dev) and their CSRF token.

The cookie holds 32 random bytes; the DB stores only sha256(id) with a 30-day expiry.
CSRF token = HMAC(install secret, "csrf|" + sha256(id)): readable only via a credentialed GET
of /v1/auth/web/csrf, which CORS allows solely for TAC_SITE_ORIGINS.
"""

import hmac
import secrets
import time

from fastapi import Request, Response

from .db import now_iso
from .web import ApiError, sha256_hex

COOKIE_DEV = "tac_session"
# __Host- makes the browser enforce Secure, Path=/ and no Domain: the cookie never reaches a sibling
# subdomain and a sibling can't plant one. terminalart.club -> api.terminalart.club is same-site, so a
# host-only SameSite=Lax cookie on the API host already rides the site's credentialed fetches.
COOKIE_PROD = "__Host-tac_session"
TTL_S = 30 * 86400
CSRF_HEADER = "x-tac-csrf"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
OK_FETCH_SITES = ("same-origin", "same-site")


def csrf_for(secret: str, session_hash: str) -> str:
    return hmac.new(secret.encode(), f"csrf|{session_hash}".encode(), "sha256").hexdigest()


def cookie_name(request: Request) -> str:
    return COOKIE_PROD if request.app.state.settings.env == "prod" else COOKIE_DEV


def cookie_secure(request: Request) -> bool:
    s = request.app.state.settings
    return s.env == "prod" or s.public_base_url.startswith("https://")


def set_session_cookie(request: Request, response: Response, session_id: str) -> None:
    response.set_cookie(
        cookie_name(request), session_id, max_age=TTL_S, path="/", httponly=True, samesite="lax",
        secure=cookie_secure(request),
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(cookie_name(request), path="/", httponly=True, samesite="lax", secure=cookie_secure(request))


async def create_session(request: Request, user_id: int, handle: str) -> str:
    """New session id. Rotation: any session presented with this request is revoked first."""
    db = request.app.state.db
    old = request.cookies.get(cookie_name(request))
    sid = secrets.token_urlsafe(32)
    async with db.tx() as tx:
        if old:
            await tx.execute("DELETE FROM web_sessions WHERE session_sha256 = ?", (sha256_hex(old),))
        await tx.execute(
            "INSERT INTO web_sessions (session_sha256, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (sha256_hex(sid), user_id, now_iso(), time.time() + TTL_S),
        )
        await tx.audit(f"user:{handle}", "web_login")
    return sid


async def session_row(request: Request):
    sid = request.cookies.get(cookie_name(request))
    if not sid:
        return None
    return await request.app.state.db.fetchone(
        "SELECT s.session_sha256, u.id, u.handle, u.trusted FROM web_sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.session_sha256 = ? AND s.expires_at > ?",
        (sha256_hex(sid), time.time()),
    )


def require_csrf(request: Request, session_hash: str) -> None:
    """Cookie-authenticated state change: CSRF header must match, and the fetch must not be cross-site."""
    if request.method in SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in OK_FETCH_SITES:
        raise ApiError(403, "csrf_check_failed", detail="cross-site request")
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent.encode(), csrf_for(request.app.state.secret, session_hash).encode()):
        raise ApiError(403, "csrf_check_failed", detail=f"missing or wrong {CSRF_HEADER} header")
