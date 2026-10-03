"""Web sign-in for the site: cookie sessions on the same user records as the plugin's device flow.

  GET  /v1/auth/web/login?return=/path   dev: pick/enter a handle form · github: redirect to OAuth
  POST /v1/auth/web/login                dev form submit -> session cookie -> 303 to return
  GET  /v1/auth/web/github/callback      github: code -> user -> session cookie -> 303 to return
  GET  /v1/auth/web/csrf                 {"csrf": ...} for the X-TAC-CSRF header (session required)
  POST /v1/auth/web/logout               revoke session, clear cookie
  POST /v1/auth/web/logout-all           revoke every web session of this user, clear cookie
"""

import base64
import hmac
import html
import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .auth import RESERVED_HANDLES, github_identity, user_for_github
from .db import now_iso
from .models import HANDLE_RE
from .sessions import (
    OK_FETCH_SITES,
    cookie_secure,
    clear_session_cookie,
    create_session,
    csrf_for,
    require_csrf,
    session_row,
    set_session_cookie,
)
from .web import ApiError, ip_key, page, site_home, take_rate_token

router = APIRouter()
LOGIN_PER_10MIN = 10


def safe_return(value: str | None) -> str:
    """Only same-site relative paths ("/..."); anything else becomes "/". Never an open redirect."""
    if not value or len(value) > 512 or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(c) < 0x21 or ord(c) == 0x7F for c in value):
        return "/"  # backslashes and whitespace/control chars get "fixed up" into hosts by browsers
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value


def _redirect(request: Request, ret: str) -> RedirectResponse:
    return RedirectResponse(request.app.state.settings.site_url + safe_return(ret), status_code=303)


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), f"web-login|{value}".encode(), "sha256").hexdigest()[:32]


def _login_form(request: Request, ret: str, handle: str = "", error: str = "") -> str:
    err = f"<p class=err>{html.escape(error)}</p>" if error else ""
    # relative action: works direct and behind the site's /api proxy
    return page(
        "terminal art club · sign in",
        "<h1>sign in <span class=sub>local dev: pick an existing handle or a new one</span></h1>"
        f"<div class=card><form method=post action='login'>{err}"
        f"<input type=hidden name=return value='{html.escape(safe_return(ret))}'>"
        "<label for=handle>handle (a-z, 0-9, dash; 2-24)</label>"
        f"<input id=handle name=handle type=text required pattern='[a-z0-9-]{{2,24}}' value='{html.escape(handle)}'"
        " autocomplete=username>"
        "<p><button class=primary type=submit>sign in</button></p></form></div>",
        home=site_home(request),
    )


@router.get("/v1/auth/web/login")
async def web_login(request: Request):
    st = request.app.state
    ret = safe_return(request.query_params.get("return"))
    if st.settings.auth_mode == "github":
        if not await take_rate_token(st.db, f"web-login:{ip_key(request)}", 20, 600):
            raise ApiError(429, "rate_limited")
        nonce = secrets.token_urlsafe(16)
        ret_b64 = base64.urlsafe_b64encode(ret.encode()).decode().rstrip("=")
        state = f"{ret_b64}.{nonce}.{_sign(st.secret, ret_b64 + nonce)}"
        query = urlencode({
            "client_id": st.settings.github_client_id,
            "redirect_uri": f"{st.settings.public_base_url}/v1/auth/web/github/callback",
            "state": state, "scope": "", "allow_signup": "true",
        })
        resp = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}", status_code=303)
        resp.set_cookie("tac_web_nonce", nonce, max_age=600, httponly=True, samesite="lax",
                        secure=cookie_secure(request), path="/v1/auth/web/")
        return resp
    return HTMLResponse(_login_form(request, ret))


@router.post("/v1/auth/web/login")
async def web_login_submit(request: Request, handle: str = Form(""), return_: str = Form("/", alias="return")):
    st = request.app.state
    if st.settings.auth_mode != "dev":
        raise ApiError(404, "not_found")
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in OK_FETCH_SITES and site != "none":
        raise ApiError(403, "csrf_check_failed", detail="cross-site login")  # login CSRF
    if not await take_rate_token(st.db, f"web-login:{ip_key(request)}", LOGIN_PER_10MIN, 600):
        return HTMLResponse(_login_form(request, return_, handle, "Too many attempts. Wait ten minutes."), 429)
    handle = handle.strip().lower()
    if not HANDLE_RE.match(handle) or handle in RESERVED_HANDLES:
        return HTMLResponse(_login_form(request, return_, handle, "Handles are 2-24 of a-z, 0-9 and dash."), 400)
    # dev only (prod refuses TAC_AUTH=dev): an existing handle signs in as that user, a new one is created
    async with st.db.tx() as tx:
        row = await tx.fetchone("SELECT id FROM users WHERE handle = ?", (handle,))
        if row is None:
            await tx.execute("INSERT INTO users (handle, created_at) VALUES (?, ?)", (handle, now_iso()))
            await tx.audit(f"user:{handle}", "user_created", detail="web-dev")
            row = await tx.fetchone("SELECT id FROM users WHERE handle = ?", (handle,))
    sid = await create_session(request, row["id"], handle)
    resp = _redirect(request, return_)
    set_session_cookie(request, resp, sid)
    return resp


@router.get("/v1/auth/web/github/callback")
async def web_github_callback(request: Request, code: str = "", state: str = ""):
    st = request.app.state
    if st.settings.auth_mode != "github":
        raise ApiError(404, "not_found")
    ret_b64, _, rest = state.partition(".")
    nonce, _, sig = rest.partition(".")
    if not hmac.compare_digest(sig.encode(), _sign(st.secret, ret_b64 + nonce).encode()) or request.cookies.get(
        "tac_web_nonce"
    ) != nonce:
        return HTMLResponse(page("terminal art club · sign in", "<h1>sign in</h1><p class=err>Login state mismatch. Start again.</p>",
                                 home=site_home(request)), 400)
    ret = base64.urlsafe_b64decode(ret_b64 + "=" * (-len(ret_b64) % 4)).decode("utf-8", "replace")
    ident = await github_identity(st, code)
    if ident is None:
        return HTMLResponse(page("terminal art club · sign in", "<h1>sign in</h1><p class=err>GitHub did not authorize.</p>",
                                 home=site_home(request)), 400)
    row = await user_for_github(st, *ident)
    if row is None:
        return HTMLResponse(page(
            "terminal art club · sign in",
            f"<h1>sign in</h1><p class=err>Handle '{html.escape(ident[1])}' is unavailable.</p>",
            home=site_home(request),
        ), 409)
    sid = await create_session(request, row["id"], row["handle"])
    resp = _redirect(request, ret)
    set_session_cookie(request, resp, sid)
    resp.delete_cookie("tac_web_nonce", path="/v1/auth/web/")
    return resp


@router.get("/v1/auth/web/csrf")
async def web_csrf(request: Request):
    row = await session_row(request)
    if row is None:
        raise ApiError(401, "no_session")
    return JSONResponse({"csrf": csrf_for(request.app.state.secret, row["session_sha256"]), "header": "X-TAC-CSRF"},
                        headers={"Cache-Control": "no-store"})


@router.post("/v1/auth/web/logout", status_code=204)
async def web_logout(request: Request):
    row = await session_row(request)
    if row is not None:
        require_csrf(request, row["session_sha256"])
        async with request.app.state.db.tx() as tx:
            await tx.execute("DELETE FROM web_sessions WHERE session_sha256 = ?", (row["session_sha256"],))
            await tx.audit(f"user:{row['handle']}", "web_logout")
    resp = Response(status_code=204)
    clear_session_cookie(request, resp)
    return resp


@router.post("/v1/auth/web/logout-all", status_code=204)
async def web_logout_all(request: Request):
    """Sign out everywhere: every web session of this user. Plugin Bearer tokens are separate and stay."""
    row = await session_row(request)
    if row is None:
        raise ApiError(401, "no_session")
    require_csrf(request, row["session_sha256"])
    async with request.app.state.db.tx() as tx:
        n = await tx.execute("DELETE FROM web_sessions WHERE user_id = ?", (row["id"],))
        await tx.audit(f"user:{row['handle']}", "web_logout_all", detail=f"{n} sessions")
    resp = Response(status_code=204)
    clear_session_cookie(request, resp)
    return resp
