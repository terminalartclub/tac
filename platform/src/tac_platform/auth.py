"""Device-code login (RFC 8628 shape) and bearer tokens.

dev mode:    /device is a form: enter the user_code, pick a handle.
github mode: /device takes the user_code, then GitHub OAuth names the handle (TAC_AUTH=github).
Tokens are 32 random bytes; the DB stores only sha256(token).
"""

import hmac
import html
import logging
import secrets
import sqlite3
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .db import now_iso
from .models import HANDLE_RE, DeviceCodeOut, TokenIn, TokenOut
from .sessions import cookie_secure, require_csrf, session_row
from .web import ApiError, ip_key, page, sha256_hex, take_rate_token

router = APIRouter()
log = logging.getLogger("tac.auth")

USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # RFC 8628 §6.1: no vowels, no look-alikes
DEVICE_TTL_S = 600
RESERVED_HANDLES = {"admin", "api", "media", "device", "v1", "tac", "terminal-art-club", "root", "system"}


def new_user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(value: str) -> str:
    raw = "".join(ch for ch in value.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) == 8 else ""


async def current_user(request: Request) -> dict:
    """Bearer token (plugin) or session cookie (site, sessions.cookie_name) -> {id, handle, trusted, via}. Raises 401.

    A Bearer header, when present, wins and is never CSRF-checked (browsers can't attach it
    cross-site). Cookie auth on a state-changing method must pass require_csrf.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if not header:
        row = await session_row(request)
        if row is None:
            raise ApiError(401, "missing_token")
        require_csrf(request, row["session_sha256"])
        return {"id": row["id"], "handle": row["handle"], "trusted": row["trusted"], "via": "cookie"}
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "missing_token")
    row = await request.app.state.db.fetchone(
        "SELECT u.id, u.handle, u.trusted FROM access_tokens t JOIN users u ON u.id = t.user_id"
        " WHERE t.token_sha256 = ?",
        (sha256_hex(token.strip()),),
    )
    if row is None:
        raise ApiError(401, "invalid_token")
    return {**dict(row), "via": "bearer"}


@router.post("/v1/auth/device", response_model=DeviceCodeOut)
async def start_device(request: Request) -> DeviceCodeOut:
    st = request.app.state
    if not await take_rate_token(st.db, f"device:{ip_key(request)}", 30, 3600):
        raise ApiError(429, "rate_limited")
    device_code = secrets.token_urlsafe(32)
    now = time.time()
    await st.db.execute("DELETE FROM device_codes WHERE expires_at < ?", (now - 3600,))
    for _ in range(5):  # user_code collisions: 20^8 space, practically never
        user_code = new_user_code()
        try:
            await st.db.execute(
                "INSERT INTO device_codes (device_code_sha256, user_code, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (sha256_hex(device_code), user_code, now + DEVICE_TTL_S, now_iso()),
            )
            break
        except sqlite3.IntegrityError:  # user_code collision
            continue
    else:
        raise ApiError(503, "try_again")
    base = st.settings.public_base_url
    return DeviceCodeOut(
        device_code=device_code,
        user_code=user_code,
        verification_uri=f"{base}/device",
        verification_uri_complete=f"{base}/device?code={user_code}",
        interval=3,
        expires_in=DEVICE_TTL_S,
    )


@router.post("/v1/auth/token", response_model=TokenOut)
async def poll_token(body: TokenIn, request: Request):
    db = request.app.state.db
    key = sha256_hex(body.device_code)
    row = await db.fetchone(
        "SELECT d.status, d.expires_at, d.user_id, u.handle FROM device_codes d"
        " LEFT JOIN users u ON u.id = d.user_id WHERE d.device_code_sha256 = ?",
        (key,),
    )
    if row is None:
        raise ApiError(400, "invalid_device_code")
    if row["status"] == "consumed":
        raise ApiError(410, "expired_token")
    if row["status"] == "pending":
        if time.time() > row["expires_at"]:
            raise ApiError(410, "expired_token")
        return JSONResponse({"error": "authorization_pending"}, status_code=428)
    # approved: consume exactly once (compare-and-swap), then mint the token
    token = secrets.token_urlsafe(32)
    async with db.tx() as tx:
        if not await tx.execute(
            "UPDATE device_codes SET status = 'consumed' WHERE device_code_sha256 = ? AND status = 'approved'",
            (key,),
        ):
            raise ApiError(410, "expired_token")
        await tx.execute(
            "INSERT INTO access_tokens (token_sha256, user_id, created_at) VALUES (?, ?, ?)",
            (sha256_hex(token), row["user_id"], now_iso()),
        )
        await tx.audit(f"user:{row['handle']}", "login")
    return TokenOut(access_token=token, handle=row["handle"])


# ---------------------------------------------------------------- verification page


def _device_form(code: str = "", handle: str = "", error: str = "", github: bool = False) -> str:
    err = f"<p class=err>{html.escape(error)}</p>" if error else ""
    handle_field = (
        ""
        if github
        else "<label for=handle>pick a handle (a-z, 0-9, dash; 2-24)</label>"
        f"<input id=handle name=handle type=text required pattern='[a-z0-9-]{{2,24}}' value='{html.escape(handle)}'"
        " autocomplete=off>"
    )
    action = "/device/github" if github else "/device"
    button = "continue with GitHub" if github else "connect"
    return page(
        "spare cycles · connect",
        "<h1>spare cycles</h1><p class=muted>Connect your terminal to the Terminal Art Club gallery.</p>"
        f"<div class=card><form method=post action='{action}'>{err}"
        "<label for=user_code>code shown in your terminal</label>"
        f"<input id=user_code name=user_code type=text required value='{html.escape(code)}' autocomplete=off"
        " placeholder='XXXX-XXXX'>"
        f"{handle_field}<p><button class=primary type=submit>{button}</button></p></form></div>",
    )


@router.get("/device", response_class=HTMLResponse)
async def device_page(request: Request, code: str = "") -> str:
    github = request.app.state.settings.auth_mode == "github"
    return _device_form(code=normalize_user_code(code) or "", github=github)


async def _approve(request: Request, user_code: str, user_id: int, handle: str) -> bool:
    async with request.app.state.db.tx() as tx:
        n = await tx.execute(
            "UPDATE device_codes SET status = 'approved', user_id = ?"
            " WHERE user_code = ? AND status = 'pending' AND expires_at > ?",
            (user_id, user_code, time.time()),
        )
        if n:
            await tx.audit(f"user:{handle}", "device_approved")
        return bool(n)


@router.post("/device", response_class=HTMLResponse)
async def device_submit(request: Request, user_code: str = Form(""), handle: str = Form("")):
    st = request.app.state
    if st.settings.auth_mode != "dev":
        raise ApiError(404, "not_found")
    if not await take_rate_token(st.db, f"device-form:{ip_key(request)}", 10, 600):
        return HTMLResponse(_device_form(user_code, handle, "Too many attempts. Wait ten minutes."), 429)
    code = normalize_user_code(user_code)
    handle = handle.strip().lower()
    if not code:
        return HTMLResponse(_device_form(user_code, handle, "That code is not XXXX-XXXX."), 400)
    if not HANDLE_RE.match(handle) or handle in RESERVED_HANDLES:
        return HTMLResponse(_device_form(code, handle, "Handles are 2-24 of a-z, 0-9 and dash."), 400)
    # One transaction: code still pending + handle free -> create user + approve code.
    # Dev mode: a handle belongs to the first device that claims it; no re-login as an existing handle.
    error, status = "", 200
    async with st.db.tx() as tx:
        if not await tx.fetchone(
            "SELECT 1 FROM device_codes WHERE user_code = ? AND status = 'pending' AND expires_at > ?",
            (code, time.time()),
        ):
            error, status = "Unknown or expired code. Start again in your terminal.", 400
        elif await tx.fetchone("SELECT 1 FROM users WHERE handle = ?", (handle,)):
            error, status = "That handle is taken.", 409
        else:
            await tx.execute("INSERT INTO users (handle, created_at) VALUES (?, ?)", (handle, now_iso()))
            user = await tx.fetchone("SELECT id FROM users WHERE handle = ?", (handle,))
            await tx.execute(
                "UPDATE device_codes SET status = 'approved', user_id = ? WHERE user_code = ?", (user["id"], code)
            )
            await tx.audit(f"user:{handle}", "user_created")
            await tx.audit(f"user:{handle}", "device_approved")
    if error:
        return HTMLResponse(_device_form(code, handle, error), status)
    return page(
        "spare cycles · connected",
        f"<h1>connected</h1><div class=card><p class=ok>You are <b>{html.escape(handle)}</b>.</p>"
        "<p class=muted>Go back to your terminal; it picks this up within a few seconds.</p></div>",
    )


# ---------------------------------------------------------------- prod: GitHub OAuth
# TODO(prod): register a GitHub OAuth app; callback = {TAC_PUBLIC_BASE_URL}/device/github/callback.
# TODO(prod): when the GitHub login is not a valid/free handle, show a pick-a-handle step
#             instead of failing (currently: error page).
# TODO(prod): exercise this path against GitHub; it is written to the documented OAuth web flow
#             (https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
#             but has only been tested up to the redirect.


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), "sha256").hexdigest()[:32]


def _github_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def github_identity(st, code: str) -> tuple[int, str] | None:
    """OAuth code -> (github user id, lowercased login), or None if GitHub refused, failed or answered garbage."""
    try:
        async with _github_client() as client:
            tok = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": st.settings.github_client_id,
                    "client_secret": st.settings.github_client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            if tok.status_code != 200:
                return None
            body = tok.json()
            gh_token = body.get("access_token") if isinstance(body, dict) else None
            if not isinstance(gh_token, str) or not gh_token:
                return None
            resp = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"},
            )
            if resp.status_code != 200:
                return None
            prof = resp.json()
            gh_id, login = prof["id"], prof["login"]
            if isinstance(gh_id, bool) or not isinstance(gh_id, int) or not isinstance(login, str):
                return None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:  # ValueError covers JSONDecodeError
        log.warning("github identity lookup failed: %s", type(e).__name__)
        return None
    return gh_id, login.lower()


async def user_for_github(st, gh_id: int, login: str):
    """The user bound to this GitHub id; created on first login when the login is a free, valid handle."""
    async with st.db.tx() as tx:
        row = await tx.fetchone("SELECT id, handle FROM users WHERE github_id = ?", (gh_id,))
        if row is not None:
            return row
        if not HANDLE_RE.match(login) or login in RESERVED_HANDLES or await tx.fetchone(
            "SELECT 1 FROM users WHERE handle = ?", (login,)
        ):
            return None
        await tx.execute("INSERT INTO users (handle, github_id, created_at) VALUES (?, ?, ?)", (login, gh_id, now_iso()))
        await tx.audit(f"user:{login}", "user_created", detail=f"github:{gh_id}")
        return await tx.fetchone("SELECT id, handle FROM users WHERE github_id = ?", (gh_id,))


@router.post("/device/github")
async def github_start(request: Request, user_code: str = Form("")):
    st = request.app.state
    if st.settings.auth_mode != "github":
        raise ApiError(404, "not_found")
    code = normalize_user_code(user_code)
    if not code or not await st.db.fetchone(
        "SELECT 1 FROM device_codes WHERE user_code = ? AND status = 'pending' AND expires_at > ?",
        (code, time.time()),
    ):
        return HTMLResponse(_device_form(user_code, error="Unknown or expired code.", github=True), 400)
    nonce = secrets.token_urlsafe(16)
    state = f"{code}.{nonce}.{_sign(code + nonce, st.secret)}"
    query = urlencode(
        {
            "client_id": st.settings.github_client_id,
            "redirect_uri": f"{st.settings.public_base_url}/device/github/callback",
            "state": state,
            "scope": "",  # public profile only
            "allow_signup": "true",
        }
    )
    resp = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}", status_code=303)
    resp.set_cookie("tac_gh_nonce", nonce, max_age=600, httponly=True, samesite="lax", secure=cookie_secure(request))
    return resp


@router.get("/device/github/callback", response_class=HTMLResponse)
async def github_callback(request: Request, code: str = "", state: str = ""):
    st = request.app.state
    if st.settings.auth_mode != "github":
        raise ApiError(404, "not_found")
    user_code, _, rest = state.partition(".")
    nonce, _, sig = rest.partition(".")
    if (
        not hmac.compare_digest(sig.encode(), _sign(user_code + nonce, st.secret).encode())
        or request.cookies.get("tac_gh_nonce") != nonce
    ):
        return HTMLResponse(_device_form(error="Login state mismatch. Start again.", github=True), 400)
    ident = await github_identity(st, code)
    if ident is None:
        return HTMLResponse(_device_form(error="GitHub did not authorize.", github=True), 400)
    gh_id, login = ident
    row = await user_for_github(st, gh_id, login)
    if row is None:
        return HTMLResponse(_device_form(error=f"Handle '{login}' is unavailable (TODO: pick one).", github=True), 409)
    if not await _approve(request, user_code, row["id"], row["handle"]):
        return HTMLResponse(_device_form(error="That code expired. Start again.", github=True), 400)
    return page("spare cycles · connected", f"<h1>connected as {html.escape(row['handle'])}</h1>")
