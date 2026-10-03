"""Site engagement events: anonymous per-day counters (v0).

POST /v1/events  body {"name": "piece_share" | "install_copy" | "install_send"}  -> 204
    Any Content-Type: navigator.sendBeacon(url, JSON.stringify({name})) sends text/plain, which
    is a CORS "simple" request (no preflight). Unknown or missing name -> 400. Rate-limited per
    salted IP-day hash (same salt and hash as views); over the limit -> 204, dropped.

Stored: event_days(name, day, n) only. No IPs, no hashes, no piece ids, no cookies.
"""

import json
import logging

from fastapi import APIRouter, Request, Response

from .views import day_salt, utc_today
from .web import ApiError, client_ip, sha256_hex, take_rate_token

log = logging.getLogger("tac.events")
router = APIRouter()

EVENT_NAMES = frozenset({"piece_share", "install_copy", "install_send"})
EVENTS_PER_HOUR = 60


@router.post("/v1/events", status_code=204)
async def record_event(request: Request) -> Response:
    st = request.app.state
    try:
        body = json.loads(await request.body() or b"{}")
    except (ValueError, UnicodeDecodeError):
        body = None
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or name not in EVENT_NAMES:
        raise ApiError(400, "unknown_event", allowed=sorted(EVENT_NAMES))
    resp = Response(status_code=204)
    day = utc_today().isoformat()
    ip_day_hash = sha256_hex(await day_salt(st.db, day) + "|" + client_ip(request, st.settings))[:32]
    if not await take_rate_token(st.db, f"event:{ip_day_hash}", EVENTS_PER_HOUR, 3600):
        return resp
    await st.db.execute(
        "INSERT INTO event_days (name, day, n) VALUES (?, ?, 1)"
        " ON CONFLICT(name, day) DO UPDATE SET n = n + 1",
        (name, day),
    )
    return resp
