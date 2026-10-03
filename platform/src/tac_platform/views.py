"""View counts. Public as aggregates only: community.json carries each piece's total, each artist's
total and the current ISO week's sum (refreshed hourly). The site decides what to show.

POST /v1/pieces/{handle}/{slug}/view  -> 204 always (no auth, no cookies, no body)
GET  /v1/me/pieces                    -> the caller's own pieces with views_total / views_7d / views_28d

Privacy: a view is keyed by sha256(day_salt | ip), where day_salt is 32 random bytes minted per
UTC day and deleted after the next day ends. Raw IPs are never stored, and hashes from different
days can't be joined (different salts) or brute-forced back to an IP once the salt is gone.
Retention: per-hash rows are purged after 30 days; the per-day rollup (view_days) is kept.
"""

import asyncio
import json
import logging
import time
import secrets
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Request, Response

from .auth import current_user
from .db import Database
from .web import client_key, sha256_hex, take_rate_token

log = logging.getLogger("tac.views")
router = APIRouter()

RETENTION_DAYS = 30
SERIES_DAYS = 28
VIEWS_PER_HOUR = 120


def utc_today() -> date:
    return datetime.now(UTC).date()


async def day_salt(db: Database, day: str) -> str:
    await db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (f"view_salt:{day}", secrets.token_hex(32)))
    return (await db.fetchone("SELECT value FROM kv WHERE key = ?", (f"view_salt:{day}",)))["value"]


@router.post("/v1/pieces/{handle}/{slug}/view", status_code=204)
async def record_view(handle: str, slug: str, request: Request) -> Response:
    """204 for every outcome (counted, duplicate, rate-limited, unknown, hidden): leaks nothing."""
    st = request.app.state
    resp = Response(status_code=204)  # CORS for TAC_SITE_ORIGINS is added by app.LimitsMiddleware
    day = utc_today().isoformat()
    ip_day_hash = sha256_hex(await day_salt(st.db, day) + "|" + client_key(request, st.settings))[:32]  # IPv6: /64
    if not await take_rate_token(st.db, f"view:{ip_day_hash}", VIEWS_PER_HOUR, 3600):
        return resp
    row = await st.db.fetchone(
        "SELECT s.id FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE u.handle = ? AND s.slug = ? AND s.status = 'published' AND s.hidden = 0",
        (handle, slug),
    )
    if row is None:
        return resp
    async with st.db.tx() as tx:
        if await tx.execute(
            "INSERT OR IGNORE INTO views (submission_id, day, ip_day_hash) VALUES (?, ?, ?)",
            (row["id"], day, ip_day_hash),
        ):
            await tx.execute(
                "INSERT INTO view_days (submission_id, day, views) VALUES (?, ?, 1)"
                " ON CONFLICT(submission_id, day) DO UPDATE SET views = views + 1",
                (row["id"], day),
            )
    return resp


async def views_summary(db: Database, sub_ids: list[str]) -> dict[str, dict]:
    """sub_id -> {views_total, views_7d, views_28d: [28 ints oldest->newest]}."""
    today = utc_today()
    days = [(today - timedelta(days=SERIES_DAYS - 1 - i)).isoformat() for i in range(SERIES_DAYS)]
    out = {sid: {"views_total": 0, "views_7d": 0, "views_28d": [0] * SERIES_DAYS} for sid in sub_ids}
    if not sub_ids:
        return out
    marks = ",".join("?" * len(sub_ids))
    for r in await db.fetchall(
        f"SELECT submission_id, SUM(views) AS n FROM view_days WHERE submission_id IN ({marks}) GROUP BY submission_id",
        tuple(sub_ids),
    ):
        out[r["submission_id"]]["views_total"] = r["n"]
    index = {d: i for i, d in enumerate(days)}
    for r in await db.fetchall(
        f"SELECT submission_id, day, views FROM view_days WHERE submission_id IN ({marks}) AND day >= ?",
        (*sub_ids, days[0]),
    ):
        i = index.get(r["day"])
        if i is not None:
            out[r["submission_id"]]["views_28d"][i] = r["views"]
    for v in out.values():
        v["views_7d"] = sum(v["views_28d"][-7:])
    return out


@router.get("/v1/me/pieces")
async def my_pieces(request: Request) -> dict:
    st = request.app.state
    user = await current_user(request)
    rows = await st.db.fetchall(
        "SELECT id, slug, title, status, hidden, created_at, critique, reasons_json FROM submissions"
        " WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    views = await views_summary(st.db, [r["id"] for r in rows])
    base = st.settings.public_base_url
    # Contract pinned with the plugin (tac-studio f5bacdf): exactly these keys. views_28d is
    # oldest -> newest, last entry = today (UTC). "hidden" = published but hidden by reports.
    return {
        "pieces": [
            {
                "id": f"{user['handle']}/{r['slug']}",
                "slug": r["slug"],
                "title": r["title"],
                "status": "hidden" if r["status"] == "published" and r["hidden"] else r["status"],
                **views[r["id"]],
                "url": f"{base}/v1/submissions/{r['id']}",  # status URL (no site piece-page route yet)
                "critique": r["critique"],
                "reasons": json.loads(r["reasons_json"]),
            }
            for r in rows
        ],
    }


async def purge(db: Database) -> None:
    today = utc_today()
    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
    keep_salts = {f"view_salt:{(today - timedelta(days=1)).isoformat()}", f"view_salt:{today.isoformat()}"}
    async with db.tx() as tx:
        n = await tx.execute("DELETE FROM views WHERE day < ?", (cutoff,))
        await tx.execute("DELETE FROM web_sessions WHERE expires_at < ?", (time.time(),))
        rows = await tx.conn.execute_fetchall("SELECT key FROM kv WHERE key LIKE 'view_salt:%'")
        for (key,) in rows:
            if key not in keep_salts:  # a deleted salt makes that day's hashes irreversible
                await tx.execute("DELETE FROM kv WHERE key = ?", (key,))
    if n:
        log.info("purged %d per-hash view rows older than %s", n, cutoff)


async def purge_loop(db: Database, every_s: float = 3600) -> None:
    while True:
        try:
            await purge(db)
        except Exception:  # noqa: BLE001
            log.exception("view purge failed")
        await asyncio.sleep(every_s)
