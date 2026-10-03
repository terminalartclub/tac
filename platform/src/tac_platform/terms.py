"""Terms acceptance: one required checkbox at sign-in, recorded per user with the version accepted.

settings.terms_version mirrors TERMS_VERSION in the site's legal.js. A user whose stored
terms_version is NULL or lower must tick the box before a sign-in completes; uploads are refused
(403 terms_not_accepted) until they do.

Where the box appears:
  dev  /device form, dev web login form   identity is only known on submit, so the box is always shown
                                          (new handle, or the box is required only if the handle is behind)
  github device + github web login        identity first (OAuth callback), then an interstitial with the
                                          box only if that user is behind, so current users never see it

The interstitial carries a signed, 10-minute token (user id + what to finish) bound to an httpOnly
nonce cookie set on the same response, the same pattern as the OAuth state. A leaked form token
alone can't complete a sign-in.
"""

import base64
import hmac
import html
import secrets
import time

from fastapi import Request

from .db import now_iso

DEFAULT_SITE = "https://terminalart.club"
PENDING_TTL_S = 600
NONCE_COOKIE = "tac_terms_nonce"
FIELD = "agree"


def links(settings) -> tuple[str, str]:
    site = settings.site_url or DEFAULT_SITE
    return f"{site}/terms", f"{site}/content-policy"


def terms_url(settings) -> str:
    return links(settings)[0]


def checkbox(settings) -> str:
    """The required, unticked agreement box (shared stylesheet: label.agree)."""
    terms, policy = (html.escape(u, quote=True) for u in links(settings))
    return (
        f"<label class=agree><input type=checkbox name={FIELD} value=1 required> "
        f"I agree to the <a href='{terms}' target=_blank rel=noopener>Terms</a> and the "
        f"<a href='{policy}' target=_blank rel=noopener>Content policy</a></label>"
    )


def ticked(value: str | None) -> bool:
    return value == "1"


def is_current(row, settings) -> bool:
    v = row["terms_version"] if row is not None else None
    return isinstance(v, int) and v >= settings.terms_version


async def record(tx, user_id: int, handle: str, version: int) -> None:
    """Store the accepted version + time, with an audit row. Caller owns the transaction."""
    await tx.execute(
        "UPDATE users SET terms_version = ?, terms_accepted_at = ? WHERE id = ?", (version, now_iso(), user_id)
    )
    await tx.audit(f"user:{handle}", "terms_accepted", detail=f"v{version}")


# ── signed pending sign-in (GitHub interstitials) ───────────────────────────


def _mac(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), f"terms|{payload}".encode(), "sha256").hexdigest()[:32]


def make_pending(secret: str, kind: str, user_id: int, extra: str) -> tuple[str, str]:
    """(form token, cookie nonce). kind = "device" (extra = user_code) or "web" (extra = return path)."""
    nonce = secrets.token_urlsafe(16)
    extra_b64 = base64.urlsafe_b64encode(extra.encode()).decode().rstrip("=")
    payload = f"{kind}.{user_id}.{extra_b64}.{int(time.time()) + PENDING_TTL_S}.{nonce}"
    return f"{payload}.{_mac(secret, payload)}", nonce


def read_pending(secret: str, kind: str, token: str, request: Request) -> tuple[int, str] | None:
    """(user_id, extra) if the token is ours, unexpired, of this kind and matches the nonce cookie."""
    payload, _, sig = token.rpartition(".")
    if not payload or not hmac.compare_digest(sig.encode(), _mac(secret, payload).encode()):
        return None
    parts = payload.split(".")
    if len(parts) != 5:
        return None
    t_kind, uid, extra_b64, exp, nonce = parts
    cookie = request.cookies.get(NONCE_COOKIE) or ""
    if t_kind != kind or not exp.isdigit() or int(exp) < time.time() or not hmac.compare_digest(nonce, cookie):
        return None
    if not uid.isdigit():
        return None
    try:
        extra = base64.urlsafe_b64decode(extra_b64 + "=" * (-len(extra_b64) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return int(uid), extra


def interstitial_body(settings, action: str, token: str, handle: str, error: str = "") -> str:
    err = f"<p class=err>{html.escape(error)}</p>" if error else ""
    return (
        f"<h1>one more step <span class=sub>signing in as {html.escape(handle)}</span></h1>"
        f"<div class=card><form method=post action='{html.escape(action, quote=True)}'>{err}"
        f"<input type=hidden name=token value='{html.escape(token, quote=True)}'>"
        f"{checkbox(settings)}<p><button class=primary type=submit>continue</button></p></form></div>"
    )
