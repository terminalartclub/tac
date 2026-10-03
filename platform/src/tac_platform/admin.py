"""Human review: /admin HTML queue + /v1/admin/* JSON. Auth = TAC_ADMIN_TOKEN (header or cookie)."""

import hmac
import html
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

from .models import RejectIn
from .views import views_summary
from .web import ApiError, page

router = APIRouter()

COOKIE = "tac_admin"
MEDIA_RE = re.compile(r"^(render/(preview\.webp|og\.jpg|process/\d{2}\.webp)|process/\d{2}\.png)$")
MEDIA_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".png": "image/png"}


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


async def _queue(request: Request) -> dict:
    db = request.app.state.db
    cols = (
        "s.id, s.slug, s.title, s.status, s.hidden, s.meta_json, s.reasons_json, s.critique, s.flags_json,"
        " s.stats_json, s.process_n, s.created_at, u.handle, u.trusted, u.house_artist, u.display_name, u.link"
    )
    review = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE s.status = 'in_review' ORDER BY s.created_at"
    )
    hidden = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE s.status = 'published' AND s.hidden = 1 ORDER BY s.updated_at DESC"
    )
    published = await db.fetchall(
        f"SELECT {cols} FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE s.status = 'published' AND s.hidden = 0 ORDER BY s.published_at DESC LIMIT 50"
    )

    async def item(r) -> dict:
        reports = await db.fetchall(
            "SELECT reason, created_at FROM reports WHERE submission_id = ? ORDER BY id", (r["id"],)
        )
        return {
            "id": r["id"],
            "handle": r["handle"],
            "slug": r["slug"],
            "title": r["title"],
            "status": r["status"],
            "hidden": bool(r["hidden"]),
            "trusted": bool(r["trusted"]),
            "house_artist": bool(r["house_artist"]),
            "display_name": r["display_name"] or "",
            "link": r["link"] or "",
            "meta": json.loads(r["meta_json"]),
            "reasons": json.loads(r["reasons_json"]),
            "critique": r["critique"],
            "flags": json.loads(r["flags_json"]),
            "stats": json.loads(r["stats_json"] or "{}"),
            "process_n": r["process_n"],
            "created_at": r["created_at"],
            "reports": [dict(x) for x in reports],
        }

    views = await views_summary(db, [r["id"] for r in (*review, *hidden, *published)])
    out = {
        "in_review": [await item(r) for r in review],
        "hidden": [await item(r) for r in hidden],
        "published": [await item(r) for r in published],
    }
    for items in out.values():
        for it in items:
            it["views_7d"] = views[it["id"]]["views_7d"]  # private; never in community.json
    return out


@router.get("/v1/admin/queue")
async def admin_queue(request: Request) -> dict:
    require_admin(request)
    return await _queue(request)


@router.post("/v1/admin/submissions/{sub_id}/approve")
async def approve(sub_id: str, request: Request) -> dict:
    require_admin(request)
    if not await request.app.state.publisher.publish(sub_id, "admin"):
        raise ApiError(409, "not_in_review")
    return {"id": sub_id, "status": "published"}


@router.post("/v1/admin/submissions/{sub_id}/reject")
async def reject(sub_id: str, request: Request, body: RejectIn | None = None) -> dict:
    require_admin(request)
    reason = (body or RejectIn()).reason
    if not await request.app.state.publisher.reject(sub_id, "admin", [reason]):
        raise ApiError(409, "not_in_review")
    return {"id": sub_id, "status": "rejected"}


@router.post("/v1/admin/pieces/{handle}/{slug}/unhide")
async def unhide(handle: str, slug: str, request: Request) -> dict:
    require_admin(request)
    if not await request.app.state.publisher.unhide(handle, slug, "admin"):
        raise ApiError(409, "not_hidden")
    return {"id": f"{handle}/{slug}", "hidden": False}


@router.post("/v1/admin/pieces/{handle}/{slug}/delete")
async def delete(handle: str, slug: str, request: Request, body: RejectIn | None = None) -> dict:
    require_admin(request)
    reason = (body or RejectIn(reason="removed")).reason
    if not await request.app.state.publisher.delete(handle, slug, "admin", reason):
        raise ApiError(409, "not_published")
    return {"id": f"{handle}/{slug}", "status": "rejected"}


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

JS = """
async function act(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'content-type': 'application/json', 'x-tac-admin-csrf': '1'},
                              body: JSON.stringify(body || {})});
  if (!r.ok) { alert(url + ' -> ' + r.status + ' ' + await r.text()); return; }
  location.reload();
}
function withReason(url, id) { act(url, {reason: document.getElementById(id).value || 'rejected by moderator'}); }
"""


def _house_button(it: dict) -> str:
    h, on = e(it["handle"]), it["house_artist"]
    return (f"<button onclick=\"act('/v1/admin/users/{h}/house',{{house:{'false' if on else 'true'}}})\">"
            f"{'Unmark' if on else 'Mark'} {h} house artist</button>")


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
    if mode == "review":
        actions = (
            f"<button class=primary onclick=\"act('/v1/admin/submissions/{e(sid)}/approve')\">Approve</button>"
            f"<input type=text id='{e(rid)}' placeholder='reject reason'>"
            f"<button class=bad onclick=\"withReason('/v1/admin/submissions/{e(sid)}/reject','{e(rid)}')\">Reject</button>"
            f"<button onclick=\"act('/v1/admin/users/{h}/trust',{{trusted:{'false' if it['trusted'] else 'true'}}})\">"
            f"{'Untrust' if it['trusted'] else 'Trust'} {h}</button>"
            + _house_button(it)
        )
    else:
        unhide = f"<button class=primary onclick=\"act('/v1/admin/pieces/{h}/{s}/unhide')\">Unhide</button>" if mode == "hidden" else ""
        actions = (
            f"{unhide}{_house_button(it)}<input type=text id='{e(rid)}' placeholder='delete reason'>"
            f"<button class=bad onclick=\"withReason('/v1/admin/pieces/{h}/{s}/delete','{e(rid)}')\">Delete</button>"
        )
    return (
        "<div class=card><div class=item><div>"
        f"<img class=preview src='/admin/media/{e(sid)}/render/preview.webp' alt='preview'>"
        f"<div class=procs>{procs}</div></div><div>"
        f"<h2>{e(it['title'])} <span class=count>by {h}"
        f"{' (' + e(it['display_name']) + ')' if it['display_name'] else ''}"
        f"{' · ' + e(it['link']) if it['link'] else ''} · {e(it['created_at'][:16])}"
        f"{' · trusted' if it['trusted'] else ''}{' · house artist' if it['house_artist'] else ''} · {it['views_7d']} views 7d · human: {e(it['meta'].get('human_role', 'none'))} · {e(it['meta'].get('size', 'full'))}</span></h2>"
        f"<p>{e(it['meta'].get('description', ''))}</p>"
        f"<div>{flags}{reasons}</div>"
        + (f"<p><b>critique</b> {e(it['critique'])}</p>" if it["critique"] else "<p class=muted>no critique</p>")
        + (f"<p><b>reports</b></p><ul>{reports}</ul>" if reports else "")
        + _kv({**meta, **{f"stats.{k}": v for k, v in it["stats"].items() if not isinstance(v, (dict, list))}})
        + f"<div class=actions>{actions}</div></div></div>"
        + (f"<p class=muted>notes.md</p><pre>{e(notes.decode('utf-8', 'replace'))}</pre>" if notes else "")
        + f"<p class=muted>piece.py · {len(code) // 1024} KB</p><pre>{e(code.decode('utf-8', 'replace'))}</pre></div>"
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    try:
        require_admin(request)
    except ApiError:
        return HTMLResponse(
            page("terminal art club · admin",
                 "<h1>admin</h1><p class=muted>Sign in with <code>/admin/login?token=…</code>.</p>"), 401
        )
    q = await _queue(request)
    audit = await request.app.state.db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT 40")
    parts = [f"<script>{JS}</script><h1>review</h1>"]
    for key, title, mode in (("in_review", "In review", "review"), ("hidden", "Hidden by reports", "hidden"),
                             ("published", "Published", "published")):
        parts.append(f"<section><h2>{title} <span class=count>{len(q[key])}</span></h2>")
        parts.extend([await _card(request, it, mode) for it in q[key]] or ["<p class=muted>empty</p>"])
        parts.append("</section>")
    rows = "".join(
        "<tr>" + "".join(f"<td>{e(str(a[c] or ''))}</td>" for c in
                         ("at", "actor", "action", "submission_id", "from_status", "to_status", "detail")) + "</tr>"
        for a in audit
    )
    parts.append(f"<section><h2>Audit log</h2><div class=card><table class=audit>{rows}</table></div></section>")
    return page("terminal art club · admin", "".join(parts), wide=True)
