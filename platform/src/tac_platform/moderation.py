"""Public reports on published pieces. N distinct reporters (by salted IP hash) auto-hide."""

from fastapi import APIRouter, Request

from .db import now_iso
from .models import ReportIn
from .web import ApiError, ip_key, take_rate_token

router = APIRouter()


@router.post("/v1/pieces/{handle}/{slug}/report", status_code=202)
async def report_piece(handle: str, slug: str, body: ReportIn, request: Request) -> dict:
    st = request.app.state
    reporter = ip_key(request)
    if not await take_rate_token(st.db, f"report:{reporter}", st.settings.reports_per_hour, 3600):
        raise ApiError(429, "rate_limited", detail=f"{st.settings.reports_per_hour} reports per hour")
    row = await st.db.fetchone(
        "SELECT s.id FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE u.handle = ? AND s.slug = ? AND s.status = 'published' AND s.hidden = 0",
        (handle, slug),
    )
    if row is None:
        raise ApiError(404, "not_found")
    async with st.db.tx() as tx:
        # UNIQUE(submission_id, reporter): the same reporter counts once
        await tx.execute(
            "INSERT OR IGNORE INTO reports (submission_id, reporter, reason, created_at) VALUES (?, ?, ?, ?)",
            (row["id"], reporter, body.reason.strip(), now_iso()),
        )
        count = (await tx.fetchone("SELECT COUNT(*) AS n FROM reports WHERE submission_id = ?", (row["id"],)))["n"]
        await tx.audit("public", "report", row["id"], detail=f"{count} distinct")
    if count >= st.settings.reports_to_hide:
        await st.publisher.hide(handle, slug, "reports", detail=f"{count} distinct reports")
    return {"status": "received"}
