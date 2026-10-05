"""The wall, for `/tac:wall`: this week's pieces as pre-rendered terminal frames. Data only, never code.

  GET /v1/wall.json[?picks=1]           the playlist, newest first, only pieces with frames: this ISO week's
                                        ("week"); under TOP_UP_BELOW of them -> topped up with the RECENT newest
                                        from before this week ("week+recent"); none this week -> the picks
                                        ("picks"); no picks either -> the RECENT newest ("recent"). ?picks=1 ->
                                        the picks only (empty if there are none)
  GET /v1/pieces/{handle}/{slug}/frames the piece's frames.cells.gz (wallframes.py), as the render VM made it

Both read-only, rate-limited per client, with a strong ETag (304 on If-None-Match). Takedowns stick: the frames
live only with the submission (never under public/, so no /media URL and no CDN copy), are served only while
the DB says published, not hidden, owner not suspended, and are cached privately for 5 minutes at most (no
stale-while-revalidate). The playlist is public but short-lived. Frames go out as stored bytes with Content-Type
application/gzip and no Content-Encoding: the client inflates them itself, with its own size cap.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response

from .publish import model_label
from .web import ApiError, client_key, take_rate_token

router = APIRouter()

HANDLE_RE = re.compile(r"[a-z0-9-]{2,24}")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ENTRIES = 200
RECENT = 20  # the fallback / top-up: newest visible pieces from before this week
TOP_UP_BELOW = 5  # a week with fewer pieces than this is topped up with RECENT
PLAYLIST_PER_HOUR = 120
FRAMES_PER_HOUR = 600
PLAYLIST_CACHE = "public, max-age=300, stale-while-revalidate=600"
FRAMES_CACHE = "private, max-age=300"  # a hidden piece is gone from every client within 5 minutes


def week_start(now: datetime | None = None) -> str:
    """Monday 00:00 UTC of this ISO week, as the DB's timestamps are written (ISO 8601, sortable)."""
    now = now or datetime.now(UTC)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.isoformat()


def _etag(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def _not_modified(request: Request, etag: str) -> bool:
    tags = {t.strip() for t in request.headers.get("if-none-match", "").split(",")}
    return etag in tags or f"W/{etag}" in tags or "*" in tags


async def _limited(request: Request, kind: str, per_hour: int) -> None:
    st = request.app.state
    if not await take_rate_token(st.db, f"{kind}:{client_key(request, st.settings)}", per_hour, 3600):
        raise ApiError(429, "rate_limited", detail=f"at most {per_hour} {kind} requests an hour")


VISIBLE = ("s.status = 'published' AND s.hidden = 0 AND u.suspended_at IS NULL AND s.frames_json IS NOT NULL")


async def playlist(request: Request, picks: bool) -> dict:
    db = request.app.state.db
    now = datetime.now(UTC)
    since = week_start(now)  # once per request: the week and "before the week" never overlap, even at midnight
    cols = ("SELECT u.handle, s.slug, s.title, s.meta_json, s.frames_json FROM submissions s"
            " JOIN users u ON u.id = s.user_id WHERE " + VISIBLE)
    newest = " ORDER BY s.published_at DESC, s.rowid DESC LIMIT ?"
    if picks:
        rows, source = await db.fetchall(cols + " AND s.pick = 1" + newest, (MAX_ENTRIES,)), "picks"
    else:
        rows = await db.fetchall(cols + " AND s.published_at >= ?" + newest, (since, MAX_ENTRIES))
        source = "week"
        if not rows:
            rows = await db.fetchall(cols + " AND s.pick = 1" + newest, (MAX_ENTRIES,))
            source = "picks"
        if (source == "week" and len(rows) < TOP_UP_BELOW) or not rows:
            # before this week: disjoint from the week's rows by construction, so nothing to dedupe
            recent = await db.fetchall(cols + " AND s.published_at < ?" + newest, (since, RECENT))
            if recent:
                rows, source = rows + recent, ("week+recent" if rows else "recent")
    pieces = []
    for r in rows:
        meta, frames = json.loads(r["meta_json"]), json.loads(r["frames_json"])
        pieces.append({
            "handle": r["handle"], "slug": r["slug"], "title": r["title"],
            "model": meta.get("model", ""), "model_label": model_label(meta.get("model", "")),
            "frames": {k: frames[k] for k in ("bytes", "cols", "rows", "fps", "frames", "loop_ms")},
            "etag": '"' + frames["sha256"][:32] + '"',
        })
    iso = now.isocalendar()
    return {"week": f"{iso[0]}-W{iso[1]:02d}", "source": source, "pieces": pieces}


@router.get("/v1/wall.json")
async def wall_json(request: Request, picks: int = 0) -> Response:
    await _limited(request, "wall", PLAYLIST_PER_HOUR)
    body = json.dumps(await playlist(request, bool(picks)), separators=(",", ":")).encode()
    etag = _etag(body)
    headers = {"Cache-Control": PLAYLIST_CACHE, "ETag": etag}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@router.get("/v1/pieces/{handle}/{slug}/frames")
async def frames(handle: str, slug: str, request: Request) -> Response:
    if not HANDLE_RE.fullmatch(handle) or not SLUG_RE.fullmatch(slug) or len(slug) > 48:
        raise ApiError(404, "not_found")
    await _limited(request, "frames", FRAMES_PER_HOUR)
    st = request.app.state
    row = await st.db.fetchone(
        "SELECT s.id, s.frames_json FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE u.handle = ? AND s.slug = ? AND " + VISIBLE, (handle, slug))
    if row is None:
        raise ApiError(404, "not_found")
    etag = '"' + json.loads(row["frames_json"])["sha256"][:32] + '"'
    headers = {"Cache-Control": FRAMES_CACHE, "ETag": etag}
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    data = await st.store.get(f"submissions/{row['id']}/render/frames.cells.gz")
    if data is None:  # in the DB but gone from the store (an unpublish in flight)
        raise ApiError(404, "not_found")
    if '"' + hashlib.sha256(data).hexdigest()[:32] + '"' != etag:  # a re-render landed: serve what is there
        headers["ETag"] = _etag(data)
    return Response(data, media_type="application/gzip", headers=headers)
