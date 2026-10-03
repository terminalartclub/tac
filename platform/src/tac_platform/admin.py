"""Human review: /admin HTML queue + /v1/admin/* JSON. Auth = TAC_ADMIN_TOKEN (header or cookie)."""

import hmac
import html
import json
import re
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from .models import HIGH_TOKENS, RejectIn
from . import automod_budget
from .db import now_iso
from .views import views_summary
from .web import ApiError, page

router = APIRouter()

COOKIE = "tac_admin"
MEDIA_RE = re.compile(r"^(render/(preview\.webp|og\.jpg|process/\d{2}\.webp)|process/\d{2}\.png)$")
MEDIA_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".png": "image/png"}
IG_REMINDER = "also remove from Instagram"
IG_CHIP = f"<span class='chip warn'>{IG_REMINDER}</span>"
# The takedown log (/admin/takedowns): every action that takes something off the site or puts it back.
TAKEDOWN_ACTIONS = ("hide", "unhide", "delete", "suspend", "unsuspend", "delete_account", "block", "unblock")
TAKEDOWN_LOG_ROWS = 100


class ReasonIn(BaseModel):
    """A required moderator reason; it lands verbatim in the audit row (the takedown log)."""

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _token_ok(request: Request, token: str | None) -> bool:
    expected = request.app.state.settings.admin_token
    return bool(expected) and bool(token) and hmac.compare_digest(token.encode(), expected.encode())


CSRF_HEADER = "x-tac-admin-csrf"


def require_admin(request: Request) -> None:
    if not request.app.state.settings.admin_token:
        raise ApiError(403, "admin_disabled", detail="set TAC_ADMIN_TOKEN")
    if _token_ok(request, request.headers.get("x-admin-token")):
        return  # header auth: a browser can't attach it cross-site
    if not _token_ok(request, request.cookies.get(COOKIE)):
        raise ApiError(401, "admin_auth_required")
    # Cookie auth on a state change: SameSite=Strict alone trusts sibling subdomains (same-site).
    # Require the custom header act() sends (forces a CORS preflight we never answer) and, where the
    # browser reports it, a same-origin fetch.
    if request.method not in ("GET", "HEAD"):
        site = request.headers.get("sec-fetch-site")
        if request.headers.get(CSRF_HEADER) != "1" or (site is not None and site != "same-origin"):
            raise ApiError(403, "csrf_check_failed")


@router.get("/admin/login")
async def admin_login(request: Request, token: str = ""):
    if not _token_ok(request, token):
        return HTMLResponse(page("terminal art club · admin", "<h1>admin</h1><p class=err>Bad or missing token.</p>"), 401)
    resp = RedirectResponse("/admin", status_code=303)
    secure = request.app.state.settings.public_base_url.startswith("https://")
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
    return resp


@router.get("/admin/media/{sub_id}/{path:path}")
async def admin_media(sub_id: str, path: str, request: Request):
    require_admin(request)
    if not MEDIA_RE.match(path):
        raise ApiError(404, "not_found")
    data = await request.app.state.store.get(f"submissions/{sub_id}/{path}")
    if data is None:
        raise ApiError(404, "not_found")
    return Response(data, media_type=MEDIA_TYPES[path[path.rfind(".") :]], headers={"Cache-Control": "private"})


# ------------------------------------------------------------------ JSON


def _high_tokens(meta_json: str) -> bool:
    """Self-reported tokens over HIGH_TOKENS (1M): plausible but rare, so a human checks it before it
    lands in the public spare-tokens counter."""
    t = json.loads(meta_json).get("tokens")
    return isinstance(t, int) and not isinstance(t, bool) and t > HIGH_TOKENS


PER_PAGE = 50
MAX_PAGE = 10**6


def _find_terms(q: str) -> tuple[str, str]:
    """A find-box query as (exact "handle/slug" or handle, LIKE pattern). Takes a handle, handle/slug, @handle,
    a piece URL (https://terminalart.club/@alex/first-light, /media/alex/first-light/...) or title words."""
    q = q.strip()[:200]
    if "://" in q:
        q = urlsplit(q).path
    parts = [p.removeprefix("@") for p in q.strip("/").split("/") if p]
    if parts and parts[0] in ("media", "gallery", "night-shift"):
        parts = parts[1:]
    exact = "/".join(parts[:2]) if parts else ""
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return exact, like


async def _queue(request: Request, page: int = 1, find: str = "") -> dict:
    db = request.app.state.db
    cols = (
        "s.id, s.slug, s.title, s.status, s.hidden, s.meta_json, s.reasons_json, s.critique, s.flags_json,"
        " s.stats_json, s.process_n, s.created_at, u.handle, u.trusted, u.house_artist, u.display_name, u.link,"
        " u.instagram, u.instagram_confirmed, s.ig_posted_at, u.suspended_at, u.suspended_reason"
    )
    review = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE s.status = 'in_review' ORDER BY s.created_at"
    )
    hidden = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE s.status = 'published' AND s.hidden = 1 ORDER BY s.updated_at DESC"
    )
    # Published: every visible piece is reachable, by page (newest first) or through the find box.
    where, params = "s.status = 'published' AND s.hidden = 0", ()
    if find.strip():
        exact, like = _find_terms(find)
        where += (" AND (u.handle = ? OR u.handle || '/' || s.slug = ? OR s.slug LIKE ? ESCAPE '\\'"
                  " OR s.title LIKE ? ESCAPE '\\')")
        params = (exact, exact, like, like)
    page = min(max(1, page), MAX_PAGE)  # OFFSET must stay inside SQLite's 64-bit integer
    total = (await db.fetchone(
        f"SELECT COUNT(*) AS n FROM submissions s JOIN users u ON u.id = s.user_id WHERE {where}", params))["n"]
    published = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        f" WHERE {where} ORDER BY s.published_at DESC, s.id LIMIT ? OFFSET ?",
        (*params, PER_PAGE, (page - 1) * PER_PAGE),
    )

    async def item(r) -> dict:
        reports = await db.fetchall(
            "SELECT reason, created_at FROM reports WHERE submission_id = ? ORDER BY id", (r["id"],)
        )
        hid = await db.fetchone(
            "SELECT actor, detail FROM audit_log WHERE submission_id = ? AND action = 'hide' ORDER BY id DESC LIMIT 1",
            (r["id"],),
        ) if r["hidden"] else None
        return {
            "id": r["id"],
            "handle": r["handle"],
            "slug": r["slug"],
            "title": r["title"],
            "status": r["status"],
            "hidden": bool(r["hidden"]),
            "trusted": bool(r["trusted"]),
            "house_artist": bool(r["house_artist"]),
            "high_tokens": _high_tokens(r["meta_json"]),
            "display_name": r["display_name"] or "",
            "link": r["link"] or "",
            "instagram": r["instagram"] or "",
            "instagram_confirmed": bool(r["instagram"]) and bool(r["instagram_confirmed"]),
            "meta": json.loads(r["meta_json"]),
            "reasons": json.loads(r["reasons_json"]),
            "critique": r["critique"],
            "flags": json.loads(r["flags_json"]),
            "stats": json.loads(r["stats_json"] or "{}"),
            "process_n": r["process_n"],
            "created_at": r["created_at"],
            "reports": [dict(x) for x in reports],
            "ig_posted_at": r["ig_posted_at"],
            "suspended": r["suspended_at"] is not None,
            "suspended_reason": r["suspended_reason"] or "",
            "hidden_by": f"{hid['actor']}: {hid['detail'] or ''}" if hid else "",
        }

    views = await views_summary(db, [r["id"] for r in (*review, *hidden, *published)])
    out = {
        "in_review": [await item(r) for r in review],
        "hidden": [await item(r) for r in hidden],
        "published": [await item(r) for r in published],
    }
    suspended = await db.fetchall(
        "SELECT handle, suspended_at, suspended_reason FROM users WHERE suspended_at IS NOT NULL ORDER BY suspended_at DESC"
    )
    # Taken down (hidden or deleted) but still on our Instagram: the platform can't delete IG posts.
    ig_cleanup = await db.fetchall(
        "SELECT u.handle, s.slug, s.title, s.status, s.hidden, s.ig_posted_at FROM submissions s"
        " JOIN users u ON u.id = s.user_id WHERE s.ig_posted_at IS NOT NULL"
        " AND (s.status = 'rejected' OR (s.status = 'published' AND s.hidden = 1)) ORDER BY s.updated_at DESC"
    )
    for items in out.values():
        for it in items:
            it["views_7d"] = views[it["id"]]["views_7d"]  # private; never in community.json
    out["published_page"] = {"page": page, "per_page": PER_PAGE, "total": total, "find": find.strip()}
    out["suspended_users"] = [dict(r) for r in suspended]
    out["blocked_identities"] = [dict(r) for r in await db.fetchall(
        "SELECT id, ref, created_at FROM blocked_identities ORDER BY id DESC")]  # never the hash
    out["instagram_cleanup"] = [
        {"id": f"{r['handle']}/{r['slug']}", "handle": r["handle"], "slug": r["slug"], "title": r["title"],
         "state": "deleted" if r["status"] == "rejected" else "hidden", "ig_posted_at": r["ig_posted_at"]}
        for r in ig_cleanup
    ]
    return out


@router.get("/v1/admin/queue")
async def admin_queue(request: Request, page: int = 1, find: str = "") -> dict:
    require_admin(request)
    return await _queue(request, page, find)


@router.post("/v1/admin/submissions/{sub_id}/approve")
async def approve(sub_id: str, request: Request) -> dict:
    require_admin(request)
    if not await request.app.state.publisher.publish(sub_id, "admin"):
        if await request.app.state.db.fetchone(
            "SELECT 1 FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ? AND u.suspended_at IS NOT NULL",
            (sub_id,),
        ):
            raise ApiError(409, "user_suspended", detail="unsuspend the artist first")
        raise ApiError(409, "not_in_review")
    return {"id": sub_id, "status": "published"}


@router.post("/v1/admin/submissions/{sub_id}/reject")
async def reject(sub_id: str, request: Request, body: RejectIn | None = None) -> dict:
    require_admin(request)
    reason = (body or RejectIn()).reason
    if not await request.app.state.publisher.reject(sub_id, "admin", [reason]):
        raise ApiError(409, "not_in_review")
    return {"id": sub_id, "status": "rejected"}


async def _ig_reminder(request: Request, handle: str, slug: str) -> str | None:
    row = await request.app.state.db.fetchone(
        "SELECT s.ig_posted_at FROM submissions s JOIN users u ON u.id = s.user_id WHERE u.handle = ? AND s.slug = ?",
        (handle, slug),
    )
    return IG_REMINDER if row is not None and row["ig_posted_at"] else None


async def _user_suspended(request: Request, handle: str) -> bool:
    row = await request.app.state.db.fetchone("SELECT suspended_at FROM users WHERE handle = ?", (handle,))
    return row is not None and row["suspended_at"] is not None


@router.post("/v1/admin/pieces/{handle}/{slug}/hide")
async def hide(handle: str, slug: str, body: ReasonIn, request: Request) -> dict:
    """Takedown step one: off the wall, gallery, community.json, og/share and media now. Undo = unhide."""
    require_admin(request)
    if not await request.app.state.publisher.hide(handle, slug, "admin", detail=body.reason):
        raise ApiError(409, "not_visible", detail="only a published piece that isn't hidden can be hidden")
    return {"id": f"{handle}/{slug}", "hidden": True, "reminder": await _ig_reminder(request, handle, slug)}


@router.post("/v1/admin/pieces/{handle}/{slug}/unhide")
async def unhide(handle: str, slug: str, request: Request) -> dict:
    require_admin(request)
    if not await request.app.state.publisher.unhide(handle, slug, "admin"):
        if await _user_suspended(request, handle):
            raise ApiError(409, "user_suspended", detail="unsuspend the artist first")
        raise ApiError(409, "not_hidden")
    return {"id": f"{handle}/{slug}", "hidden": False}


@router.post("/v1/admin/pieces/{handle}/{slug}/delete")
async def delete(handle: str, slug: str, body: ReasonIn, request: Request) -> dict:
    """The reason is shown to the artist ("removed by moderator: <reason>"); hide/suspend reasons are not."""
    require_admin(request)
    if not await request.app.state.publisher.delete(handle, slug, "admin", body.reason):
        raise ApiError(409, "not_published")
    return {"id": f"{handle}/{slug}", "status": "rejected", "reminder": await _ig_reminder(request, handle, slug)}


class IgPostedIn(BaseModel):
    posted: bool


@router.post("/v1/admin/pieces/{handle}/{slug}/instagram-posted")
async def instagram_posted(handle: str, slug: str, body: IgPostedIn, request: Request) -> dict:
    """Record that we posted this piece on Instagram (or, after removing it there, clear that). Any status:
    a deleted piece's mark is cleared once the IG post is gone."""
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        row = await tx.fetchone(
            "SELECT s.id FROM submissions s JOIN users u ON u.id = s.user_id WHERE u.handle = ? AND s.slug = ?",
            (handle, slug),
        )
        if row is None:
            raise ApiError(404, "not_found")
        at = now_iso() if body.posted else None
        await tx.execute("UPDATE submissions SET ig_posted_at = ? WHERE id = ?", (at, row["id"]))
        await tx.audit("admin", "ig_posted" if body.posted else "ig_removed", row["id"], target=f"{handle}/{slug}")
    return {"id": f"{handle}/{slug}", "ig_posted_at": at}


@router.post("/v1/admin/users/{handle}/suspend")
async def suspend(handle: str, body: ReasonIn, request: Request) -> dict:
    """Repeat infringer: no sign-in, no submissions, every token and session revoked, every published piece
    hidden, all in one transaction (Publisher.suspend). Calling it again on a suspended user re-sweeps: any
    visible piece is hidden and any public media left by a failed delete is removed."""
    require_admin(request)
    res = await request.app.state.publisher.suspend(handle, body.reason, "admin")
    if res is None:
        raise ApiError(404, "not_found")
    on_ig = res.pop("ig_posted")
    return {"handle": handle, "suspended": True, **res,
            "reminder": f"{IG_REMINDER}: {', '.join(on_ig)}" if on_ig else None}


@router.post("/v1/admin/users/{handle}/unsuspend")
async def unsuspend(handle: str, body: ReasonIn, request: Request) -> dict:
    """Sign-in works again. Hidden pieces stay hidden: the moderator unhides them one by one."""
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        if not await tx.fetchone("SELECT 1 FROM users WHERE handle = ?", (handle,)):
            raise ApiError(404, "not_found")
        if not await tx.execute(
            "UPDATE users SET suspended_at = NULL, suspended_reason = NULL WHERE handle = ? AND suspended_at IS NOT NULL",
            (handle,),
        ):
            raise ApiError(409, "not_suspended")
        await tx.audit("admin", "unsuspend", target=handle, detail=body.reason)
    return {"handle": handle, "suspended": False}


@router.post("/v1/admin/users/{handle}/delete")
async def delete_user(handle: str, body: ReasonIn, request: Request) -> dict:
    """Delete an account and everything it owns (Publisher.delete_account), suspended or not: e.g. a suspended
    artist's erasure request, since they can't sign in to do it. Handle + reason go to the takedown log."""
    require_admin(request)
    st = request.app.state
    user = await st.db.fetchone("SELECT id FROM users WHERE handle = ?", (handle,))
    if user is None:
        raise ApiError(404, "not_found")
    on_ig = await st.publisher.delete_account(user["id"], handle, actor="admin", reason=body.reason)
    if on_ig is None:
        raise ApiError(409, "busy", detail="a submission is rendering; try again in a few minutes")
    return {"handle": handle, "deleted": True,
            "reminder": f"{IG_REMINDER}: {', '.join(on_ig)}" if on_ig else None}


@router.post("/v1/admin/blocked/{block_id}/unblock")
async def unblock(block_id: int, body: ReasonIn, request: Request) -> dict:
    """Undo ONE block, by its id (ref = a handle, which can belong to several blocked identities over time)."""
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        row = await tx.fetchone("DELETE FROM blocked_identities WHERE id = ? RETURNING ref", (block_id,))
        if row is None:
            raise ApiError(404, "not_found")
        await tx.audit("admin", "unblock", target=row["ref"], detail=body.reason, data={"block_id": block_id})
    return {"id": block_id, "ref": row["ref"], "unblocked": 1}


async def _takedowns(request: Request) -> list[dict]:
    marks = ",".join("?" * len(TAKEDOWN_ACTIONS))
    rows = await request.app.state.db.fetchall(
        "SELECT a.at, a.actor, a.action, a.target, a.detail, a.data_json, a.submission_id, u.handle, s.slug,"
        " s.status, s.hidden,"
        " s.ig_posted_at FROM audit_log a LEFT JOIN submissions s ON s.id = a.submission_id"
        f" LEFT JOIN users u ON u.id = s.user_id WHERE a.action IN ({marks}) ORDER BY a.id DESC LIMIT ?",
        (*TAKEDOWN_ACTIONS, TAKEDOWN_LOG_ROWS),
    )
    out = []
    for r in rows:
        down = r["status"] == "rejected" or (r["status"] == "published" and r["hidden"])
        ig = (json.loads(r["data_json"]).get("ig") or []) if r["data_json"] else []
        out.append({
            "at": r["at"], "actor": r["actor"], "action": r["action"],
            "target": r["target"] or (f"{r['handle']}/{r['slug']}" if r["handle"] else r["submission_id"] or ""),
            "reason": r["detail"] or "",
            # still on our Instagram while it's off the site
            "reminder": IG_REMINDER if (r["ig_posted_at"] and down and r["action"] in ("hide", "delete")) or ig
            else None,
            "ig": ig,  # delete_account: the IG-posted pieces of the deleted account (their rows are gone)
        })
    return out


@router.get("/v1/admin/takedowns")
async def admin_takedowns(request: Request) -> dict:
    require_admin(request)
    return {"actions": await _takedowns(request)}


class TrustIn(BaseModel):
    trusted: bool


@router.post("/v1/admin/users/{handle}/trust")
async def trust(handle: str, body: TrustIn, request: Request) -> dict:
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        if not await tx.execute("UPDATE users SET trusted = ? WHERE handle = ?", (int(body.trusted), handle)):
            raise ApiError(404, "not_found")
        await tx.audit("admin", "trust" if body.trusted else "untrust", detail=handle)
    return {"handle": handle, "trusted": body.trusted}


class InstagramConfirmIn(BaseModel):
    instagram: str  # the handle the admin looked at; the confirm only applies if it is still the current one


@router.post("/v1/admin/users/{handle}/instagram-confirm")
async def instagram_confirm(handle: str, body: InstagramConfirmIn, request: Request) -> dict:
    """Mark the user's CURRENT Instagram handle as verified (the admin checked the IG account links back).
    Compare-and-set on the handle: if the user changed it after the admin looked, nothing is confirmed (409)."""
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        if not await tx.fetchone("SELECT 1 FROM users WHERE handle = ?", (handle,)):
            raise ApiError(404, "not_found")
        if not await tx.execute(
            "UPDATE users SET instagram_confirmed = 1 WHERE handle = ? AND instagram = ?", (handle, body.instagram)
        ):
            raise ApiError(409, "instagram_changed")
        await tx.audit("admin", "instagram_confirm", detail=f"{handle}: {body.instagram}")
    await request.app.state.publisher.regenerate()  # the handle may now appear in community.json
    return {"handle": handle, "instagram": body.instagram, "instagram_confirmed": True}


class HouseIn(BaseModel):
    house: bool


@router.post("/v1/admin/users/{handle}/house")
async def house(handle: str, body: HouseIn, request: Request) -> dict:
    require_admin(request)
    async with request.app.state.db.tx() as tx:
        if not await tx.execute("UPDATE users SET house_artist = ? WHERE handle = ?", (int(body.house), handle)):
            raise ApiError(404, "not_found")
        await tx.audit("admin", "house" if body.house else "unhouse", detail=handle)
    await request.app.state.publisher.regenerate()  # flag shows on already-published pieces
    return {"handle": handle, "house_artist": body.house}


# ------------------------------------------------------------------ HTML

e = html.escape

# Buttons carry their action as data- attributes (html-escaped text, never code): data-act = the POST
# path, data-body = a JSON object, data-reason = the id of a reason input, data-prompt = the question of a
# required-reason prompt() (cancel or blank = no request), data-confirm = a confirm() asked first. One
# delegated listener reads
# them, so no value is ever interpolated into JavaScript and safety doesn't rest on the input validators.
JS = """
async function act(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'content-type': 'application/json', 'x-tac-admin-csrf': '1'},
                              body: JSON.stringify(body || {})});
  if (!r.ok) { alert(url + ' -> ' + r.status + ' ' + await r.text()); return; }
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (j && typeof j.reminder === 'string') alert(j.reminder);  // e.g. "also remove from Instagram"
  if (j && Array.isArray(j.media_failed) && j.media_failed.length)
    alert('Public media could not be deleted for: ' + j.media_failed.join(', ') + '. Click Re-sweep to retry.');
  location.reload();
}
function adminPath(raw) {
  // Only same-origin /v1/admin/... paths. Resolving first normalises "..", "//host" and "%2e%2e" tricks.
  let u;
  try { u = new URL(raw, location.origin); } catch (e) { return null; }
  return u.origin === location.origin && u.pathname.startsWith('/v1/admin/') ? u.pathname + u.search : null;
}
document.addEventListener('click', (ev) => {
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  const path = adminPath(b.dataset.act);
  if (!path) return;  // never POST anywhere else, whatever ended up in the attribute
  if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;  // irreversible actions ask first
  const body = b.dataset.body ? JSON.parse(b.dataset.body) : {};
  if (b.dataset.reason) body.reason = document.getElementById(b.dataset.reason).value || 'rejected by moderator';
  if (b.dataset.prompt) {  // required reason: cancel or blank sends nothing
    const r = prompt(b.dataset.prompt);
    if (r === null || !r.trim()) return;
    body.reason = r.trim();
  }
  act(path, body);
});
"""


NAV = "<p class=nav><a href='/admin'>review queue</a> · <a href='/admin/takedowns'>takedown log</a></p>"


def _find_box(pp: dict) -> str:
    """Plain GET form (no script): find any visible piece, however old, to hide it."""
    clear = " <a href='/admin#published'>clear</a>" if pp["find"] else ""
    return (
        "<form class=find method=get action='/admin#published'>"
        f"<input type=text name=find value='{e(pp['find'], quote=True)}' "
        "placeholder='find: handle, handle/slug, piece URL or title' aria-label='find a published piece'>"
        f"<button type=submit>Find</button>{clear}</form>"
    )


def _pager(pp: dict) -> str:
    last = max(1, -(-pp["total"] // pp["per_page"]))
    if last == 1:
        return ""

    def link(n: int, label: str) -> str:
        query = urlencode({"page": n, **({"find": pp["find"]} if pp["find"] else {})})
        return f"<a href='/admin?{e(query, quote=True)}#published'>{label}</a>"

    newer = link(pp["page"] - 1, "← newer") if pp["page"] > 1 else ""
    older = link(pp["page"] + 1, "older →") if pp["page"] < last else ""
    return f"<p class=pager>{newer} <span class=muted>page {pp['page']} of {last}</span> {older}</p>"


def _seg(v: str) -> str:
    """One URL path segment, percent-encoded (a '/' or '?' in a value can't change the route)."""
    return quote(str(v), safe="")


def _button(label: str, path: str, body: dict | None = None, reason_id: str | None = None, cls: str = "",
            prompt: str | None = None, confirm: str | None = None) -> str:
    attrs = f" class={cls}" if cls else ""
    attrs += f" data-act='{e(path, quote=True)}'"
    if body is not None:
        attrs += f" data-body='{e(json.dumps(body), quote=True)}'"
    if reason_id is not None:
        attrs += f" data-reason='{e(reason_id, quote=True)}'"
    if prompt is not None:
        attrs += f" data-prompt='{e(prompt, quote=True)}'"
    if confirm is not None:
        attrs += f" data-confirm='{e(confirm, quote=True)}'"
    return f"<button type=button{attrs}>{e(label)}</button>"


def _suspend_button(handle: str, suspended: bool) -> str:
    path = f"/v1/admin/users/{_seg(handle)}"
    if suspended:
        return _button(f"Unsuspend {handle}", f"{path}/unsuspend",
                       prompt=f"Unsuspend {handle}: reason (for the takedown log). Their pieces stay hidden.")
    return _button(f"Suspend {handle}", f"{path}/suspend", cls="bad",
                   prompt=f"Suspend {handle}: reason (for the takedown log). Revokes every sign-in and hides "
                          "every published piece.")


def _resweep_button(handle: str) -> str:
    """Suspend again: idempotent, it hides anything visible and retries any public media delete that failed."""
    return _button("Re-sweep", f"/v1/admin/users/{_seg(handle)}/suspend", {"reason": "re-sweep"})


def _delete_account_button(handle: str) -> str:
    return _button(f"Delete account {handle}", f"/v1/admin/users/{_seg(handle)}/delete", cls="bad",
                   confirm=f"Permanently delete {handle} and all their pieces, media and sign-ins? This can't be undone.",
                   prompt=f"Delete account {handle}: reason (reference ID only, for the takedown log)")


def _ig_posted_button(handle: str, slug: str, posted: bool, label: str | None = None) -> str:
    path = f"/v1/admin/pieces/{_seg(handle)}/{_seg(slug)}/instagram-posted"
    return _button(label or ("Clear IG mark" if posted else "Mark posted to IG"), path, {"posted": not posted})


def _ig_button(it: dict) -> str:
    """Confirm the artist's Instagram handle (shown only while unconfirmed). The handle is sent back, so a
    change by the user after this page loaded makes the confirm a 409 instead of confirming the new one."""
    if not it["instagram"] or it["instagram_confirmed"]:
        return ""
    return _button(f"confirm ig @{it['instagram']}", f"/v1/admin/users/{_seg(it['handle'])}/instagram-confirm",
                   {"instagram": it["instagram"]})


def _house_button(it: dict) -> str:
    on = it["house_artist"]
    return _button(f"{'Unmark' if on else 'Mark'} {it['handle']} house artist",
                   f"/v1/admin/users/{_seg(it['handle'])}/house", {"house": not on})


def _kv(d: dict) -> str:
    return "<dl class=kv>" + "".join(f"<dt>{e(str(k))}</dt><dd>{e(str(v))}</dd>" for k, v in d.items()) + "</dl>"


async def _card(request: Request, it: dict, mode: str) -> str:
    sid = it["id"]
    code = await request.app.state.store.get(f"submissions/{sid}/piece.py") or b""
    notes = await request.app.state.store.get(f"submissions/{sid}/notes.md")
    procs = "".join(
        f"<img src='/admin/media/{e(sid)}/render/process/{i:02d}.webp' alt='process {i}'>"
        for i in range(1, it["process_n"] + 1)
    )
    meta = {k: v for k, v in it["meta"].items() if k not in ("title", "description", "process_notes", "human_role", "size") and v}
    flags = "".join(f"<span class=chip>{e(f)}</span>" for f in it["flags"])
    reasons = "".join(f"<span class=chip>{e(r)}</span>" for r in it["reasons"])
    reports = "".join(f"<li>{e(r['reason'])} <span class=muted>{e(r['created_at'])}</span></li>" for r in it["reports"])
    rid = f"reason-{sid}"
    h, s = e(it["handle"]), e(it["slug"])
    sub_path = f"/v1/admin/submissions/{_seg(sid)}"
    piece_path = f"/v1/admin/pieces/{_seg(it['handle'])}/{_seg(it['slug'])}"
    suspend = _suspend_button(it["handle"], it["suspended"])
    if mode == "review":
        actions = (
            _button("Approve", f"{sub_path}/approve", cls="primary")
            + f"<input type=text id='{e(rid)}' placeholder='reject reason'>"
            + _button("Reject", f"{sub_path}/reject", reason_id=rid, cls="bad")
            + _button(f"{'Untrust' if it['trusted'] else 'Trust'} {it['handle']}",
                      f"/v1/admin/users/{_seg(it['handle'])}/trust", {"trusted": not it["trusted"]})
            + _house_button(it) + _ig_button(it) + suspend
        )
    else:
        if mode == "hidden":
            first = (_button("Unhide", f"{piece_path}/unhide", cls="primary") if not it["suspended"]
                     else "<span class=muted>unsuspend to unhide</span>")
        else:
            first = _button("Hide now", f"{piece_path}/hide", cls="bad",
                            prompt=f"Hide {it['handle']}/{it['slug']} now: reason (for the takedown log)")
        actions = (
            f"{first}{_house_button(it)}{_ig_button(it)}"
            + _ig_posted_button(it["handle"], it["slug"], bool(it["ig_posted_at"])) + suspend
            + _button("Delete", f"{piece_path}/delete", cls="bad",
                      prompt=f"Delete {it['handle']}/{it['slug']}: reason, SHOWN TO THE ARTIST (reference ID only)")
        )
    state = ""
    if it["suspended"]:
        state += f"<p><span class=chip>suspended</span> <span class=muted>{e(it['suspended_reason'])}</span></p>"
    if mode == "hidden":
        state += f"<p class=muted>hidden by {e(it['hidden_by'])}</p>" if it["hidden_by"] else ""
        if it["ig_posted_at"]:
            state += f"<p><span class='chip warn'>posted to IG: {IG_REMINDER}</span></p>"
    elif it["ig_posted_at"]:
        state += f"<p class=muted>posted to IG {e(it['ig_posted_at'][:16])}</p>"
    return (
        "<div class=card><div class=item><div>"
        f"<img class=preview src='/admin/media/{e(sid)}/render/preview.webp' alt='preview'>"
        f"<div class=procs>{procs}</div></div><div>"
        f"<h2>{e(it['title'])} <span class=count>by {h}"
        f"{' (' + e(it['display_name']) + ')' if it['display_name'] else ''}"
        f"{' · ' + e(it['link']) if it['link'] else ''}"
        f"{' · ig @' + e(it['instagram']) + (' ✓' if it['instagram_confirmed'] else ' (unconfirmed)') if it['instagram'] else ''}"
        f" · {e(it['created_at'][:16])}"
        f"{' · trusted' if it['trusted'] else ''}{' · house artist' if it['house_artist'] else ''}"
        f"{' <span class=chip>high_tokens</span>' if it['high_tokens'] else ''} · {it['views_7d']} views 7d · human: {e(it['meta'].get('human_role', 'none'))} · {e(it['meta'].get('size', 'full'))}</span></h2>"
        f"<p>{e(it['meta'].get('description', ''))}</p>{state}"
        f"<div>{flags}{reasons}</div>"
        + (f"<p><b>critique</b> {e(it['critique'])}</p>" if it["critique"] else "<p class=muted>no critique</p>")
        + (f"<p><b>reports</b></p><ul>{reports}</ul>" if reports else "")
        + _kv({**meta, **{f"stats.{k}": v for k, v in it["stats"].items() if not isinstance(v, (dict, list))}})
        + f"<div class=actions>{actions}</div></div></div>"
        + (f"<p class=muted>notes.md</p><pre>{e(notes.decode('utf-8', 'replace'))}</pre>" if notes else "")
        + f"<p class=muted>piece.py · {len(code) // 1024} KB</p><pre>{e(code.decode('utf-8', 'replace'))}</pre></div>"
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, page_n: int = Query(1, alias="page"), find: str = ""):
    try:
        require_admin(request)
    except ApiError:
        return HTMLResponse(
            page("terminal art club · admin",
                 "<h1>admin</h1><p class=muted>Sign in with <code>/admin/login?token=…</code>.</p>"), 401
        )
    q = await _queue(request, page_n, find)
    audit = await request.app.state.db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT 40")
    st = request.app.state
    spent = await automod_budget.month_spend(st.db)
    status = "" if st.pipeline.automod.enabled else " (off: no ANTHROPIC_API_KEY)"
    over = " <span class=chip>budget reached: pieces go to human review</span>" if (
        st.pipeline.automod.enabled and spent >= st.settings.automod_budget_usd) else ""
    parts = [f"<script>{JS}</script><h1>review</h1>{NAV}",
             f"<p class=muted id=automod-spend>automod: ${spent:.2f} of ${st.settings.automod_budget_usd:g} this month"
             f"{e(status)}{over}</p>"]
    for key, title, mode in (("in_review", "In review", "review"), ("hidden", "Hidden", "hidden"),
                             ("published", "Published", "published")):
        pp = q["published_page"]
        count = pp["total"] if key == "published" else len(q[key])
        parts.append(f"<section id={key}><h2>{title} <span class=count>{count}</span></h2>")
        if key == "published":
            parts.append(_find_box(pp))
        parts.extend([await _card(request, it, mode) for it in q[key]]
                     or [f"<p class=muted>{'no match' if key == 'published' and pp['find'] else 'empty'}</p>"])
        if key == "published":
            parts.append(_pager(pp))
        parts.append("</section>")
    ig = "".join(
        f"<tr><td>{e(c['id'])}</td><td>{e(c['title'])}</td><td>{e(c['state'])}</td>"
        f"<td>{IG_CHIP}</td>"
        f"<td>{_ig_posted_button(c['handle'], c['slug'], True, 'Removed from IG')}</td></tr>"
        for c in q["instagram_cleanup"]
    )
    parts.append("<section><h2>Instagram cleanup <span class=count>" + str(len(q["instagram_cleanup"])) + "</span></h2>"
                 + (f"<div class=card><table class=audit>{ig}</table></div>" if ig
                    else "<p class=muted>nothing taken down is still on Instagram</p>") + "</section>")
    sus = "".join(
        f"<tr><td>{e(u['handle'])}</td><td>{e(u['suspended_at'])}</td><td>{e(u['suspended_reason'] or '')}</td>"
        f"<td>{_resweep_button(u['handle'])}{_suspend_button(u['handle'], True)}"
        f"{_delete_account_button(u['handle'])}</td></tr>"
        for u in q["suspended_users"]
    )
    parts.append("<section><h2>Suspended users <span class=count>" + str(len(q["suspended_users"])) + "</span></h2>"
                 + (f"<div class=card><table class=audit>{sus}</table></div>" if sus else "<p class=muted>none</p>")
                 + "</section>")
    blk = "".join(
        f"<tr><td>#{int(b['id'])}</td><td>{e(b['ref'])}</td><td>{e(b['created_at'])}</td><td>"
        + _button(f"Unblock {b['ref']} #{b['id']}", f"/v1/admin/blocked/{int(b['id'])}/unblock",
                  prompt=f"Unblock {b['ref']} #{b['id']}: reason (reference ID only). They can sign up again.")
        + "</td></tr>"
        for b in q["blocked_identities"]
    )
    parts.append("<section><h2>Blocked identities <span class=count>" + str(len(q["blocked_identities"]))
                 + "</span></h2><p class=muted>deleted while suspended: can't sign up again</p>"
                 + (f"<div class=card><table class=audit>{blk}</table></div>" if blk else "") + "</section>")
    rows = "".join(
        "<tr>" + "".join(f"<td>{e(str(a[c] or ''))}</td>" for c in
                         ("at", "actor", "action", "submission_id", "target", "from_status", "to_status", "detail"))
        + "</tr>"
        for a in audit
    )
    parts.append(f"<section><h2>Audit log</h2><div class=card><table class=audit>{rows}</table></div></section>")
    return page("terminal art club · admin", "".join(parts), wide=True)


@router.get("/admin/takedowns", response_class=HTMLResponse)
async def takedowns_page(request: Request):
    """Read-only takedown log: the last 100 hides, unhides, deletes, suspends and unsuspends, with reasons."""
    try:
        require_admin(request)
    except ApiError:
        return HTMLResponse(
            page("terminal art club · takedowns",
                 "<h1>takedowns</h1><p class=muted>Sign in with <code>/admin/login?token=…</code>.</p>"), 401
        )
    rows = "".join(
        f"<tr><td>{e(r['at'])}</td><td>{e(r['action'])}</td><td>{e(r['target'])}</td><td>{e(r['reason'])}</td>"
        f"<td>{e(r['actor'])}</td>"
        f"<td>{IG_CHIP if r['reminder'] else ''}{' <span class=muted>' + e(', '.join(r['ig'])) + '</span>' if r['ig'] else ''}"
        "</td></tr>"
        for r in await _takedowns(request)
    )
    head = "<tr><th>time (UTC)</th><th>action</th><th>target</th><th>reason</th><th>by</th><th></th></tr>"
    body = (
        f"<h1>takedowns <span class=sub>last {TAKEDOWN_LOG_ROWS} moderation actions, newest first</span></h1>{NAV}"
        + (f"<div class=card><table class=audit id=takedowns>{head}{rows}</table></div>" if rows
           else "<p class=muted>no moderation actions yet</p>")
    )
    return page("terminal art club · takedowns", body, wide=True)
