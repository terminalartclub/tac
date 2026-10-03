"""POST /v1/submissions (multipart) and the owner's status view."""

import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from .auth import current_user
from .db import now_iso
from .models import SubmissionAccepted, SubmissionMeta, SubmissionOut
from .notes import human_role
from .web import ApiError

router = APIRouter()

MAX_REQUEST = 3 * 1024 * 1024
MAX_PIECE = 200 * 1024
MAX_PNG = 600 * 1024
MAX_PROCESS = 4
MAX_NOTES = 64 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PREVIEW_URL_TTL_S = 7 * 86400
PREVIEWABLE = ("queued", "rendering", "in_review")  # signed preview only before a decision


def slugify(title: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:40].strip("-")
    return slug or "untitled"


async def _read_capped(upload: UploadFile, cap: int, what: str) -> bytes:
    data = await upload.read(cap + 1)
    if len(data) > cap:
        raise ApiError(413, "too_large", detail=f"{what} is over {cap // 1024} KB")
    return data


def _sign_preview(secret: str, sub_id: str, exp: int) -> str:
    return hmac.new(secret.encode(), f"preview|{sub_id}|{exp}".encode(), "sha256").hexdigest()[:32]


@router.post("/v1/submissions", status_code=202, response_model=SubmissionAccepted)
async def create_submission(request: Request):
    st = request.app.state
    user = await current_user(request)

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    limit = st.settings.submissions_per_day
    recent = await st.db.fetchone(
        "SELECT COUNT(*) AS n FROM submissions WHERE user_id = ? AND created_at > ?", (user["id"], since)
    )
    if recent["n"] >= limit:  # fast path; the INSERT below re-checks atomically
        raise ApiError(429, "rate_limited", detail=f"{limit} submissions per 24 h")

    # max_part_size bounds non-file fields only (Starlette); the per-field caps are checked in _accept.
    form = await request.form(max_files=MAX_PROCESS + 2, max_fields=8, max_part_size=MAX_PNG)
    try:
        return await _accept(request, user, form, since, limit)
    finally:
        await form.close()  # drop spooled temp files


async def _accept(request: Request, user: dict, form, since: str, limit: int) -> SubmissionAccepted:
    st = request.app.state
    piece = form.get("piece")
    if not isinstance(piece, UploadFile):
        raise ApiError(400, "invalid_request", detail="piece (piece.py) file is required")
    piece_bytes = await _read_capped(piece, MAX_PIECE, "piece.py")
    try:
        piece_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ApiError(400, "invalid_request", detail="piece.py must be UTF-8 text") from None

    raw_meta = form.get("meta")
    if isinstance(raw_meta, UploadFile):
        raw_meta = (await _read_capped(raw_meta, MAX_NOTES, "meta")).decode("utf-8", "replace")
    if not raw_meta:
        raise ApiError(400, "invalid_request", detail="meta (JSON string) is required")
    if len(raw_meta) > MAX_NOTES:
        raise ApiError(413, "too_large", detail=f"meta is over {MAX_NOTES // 1024} KB")
    try:
        meta = SubmissionMeta.model_validate_json(raw_meta)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        raise ApiError(400, "invalid_meta", detail=errors) from None

    notes = form.get("notes")
    if isinstance(notes, UploadFile):
        notes = (await _read_capped(notes, MAX_NOTES, "notes")).decode("utf-8", "replace")
    notes = notes or None
    if notes and len(notes.encode()) > MAX_NOTES:
        raise ApiError(413, "too_large", detail=f"notes is over {MAX_NOTES // 1024} KB")
    # human_role is computed from notes.md, never trusted from meta. check_piece rejects a
    # declared role the notes don't back; the stored value is always the computed one.
    declared_role = meta.human_role
    meta = meta.model_copy(update={"human_role": human_role(notes)})

    process = [f for f in form.getlist("process") if isinstance(f, UploadFile)]
    if len(process) > MAX_PROCESS:
        raise ApiError(400, "invalid_request", detail=f"at most {MAX_PROCESS} process images")
    process_bytes = []
    for f in process:
        data = await _read_capped(f, MAX_PNG, "a process image")
        if not data.startswith(PNG_MAGIC):
            raise ApiError(400, "invalid_request", detail="process images must be PNG")
        process_bytes.append(data)

    # Store files first; the row only becomes visible to the worker once they exist.
    sub_id = secrets.token_urlsafe(9)
    prefix = f"submissions/{sub_id}"
    meta_doc = {**meta.model_dump(), "human_role": declared_role}  # piece_dir copy: what the client claimed
    await st.store.put(f"{prefix}/piece.py", piece_bytes)
    await st.store.put(f"{prefix}/meta.json", json.dumps(meta_doc, indent=1).encode())
    if notes:
        await st.store.put(f"{prefix}/notes.md", notes.encode())
    for i, data in enumerate(process_bytes, 1):
        await st.store.put(f"{prefix}/process/{i:02d}.png", data)

    # Rate limit and insert are ONE statement: the count check can't race another upload.
    base_slug = slugify(meta.title)
    inserted = 0
    for attempt in range(1, 50):
        slug = base_slug if attempt == 1 else f"{base_slug}-{attempt}"
        now = now_iso()
        try:
            inserted = await st.db.execute(
                "INSERT INTO submissions (id, user_id, slug, title, meta_json, status, created_at, updated_at)"
                " SELECT ?, ?, ?, ?, ?, 'queued', ?, ?"
                " WHERE (SELECT COUNT(*) FROM submissions WHERE user_id = ? AND created_at > ?) < ?",
                (sub_id, user["id"], slug, meta.title, meta.model_dump_json(), now, now,
                 user["id"], since, limit),
            )
            break
        except sqlite3.IntegrityError:  # slug taken by this handle: try the next suffix
            continue
    if not inserted:
        await st.store.delete_prefix(prefix)
        raise ApiError(429, "rate_limited", detail=f"{limit} submissions per 24 h")
    async with st.db.tx() as tx:
        await tx.audit(f"user:{user['handle']}", "submit", sub_id, None, "queued", slug)
    st.pipeline.wake()
    site = st.settings.site_url
    return SubmissionAccepted(id=sub_id, status="queued", url=f"{st.settings.public_base_url}/v1/submissions/{sub_id}",
                              piece_url=f"{site}/@{user['handle']}/{slug}" if site else None)


@router.get("/v1/submissions/{sub_id}", response_model=SubmissionOut)
async def get_submission(sub_id: str, request: Request):
    st = request.app.state
    user = await current_user(request)
    row = await st.db.fetchone(
        "SELECT s.*, u.handle FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ? AND s.user_id = ?",
        (sub_id, user["id"]),
    )
    if row is None:  # not yours == not found: no id probing
        raise ApiError(404, "not_found")
    preview_url = None
    base = st.settings.public_base_url
    if row["status"] == "published" and not row["hidden"]:
        preview_url = f"{base}/media/{row['handle']}/{row['slug']}/preview.webp"
    elif row["status"] in PREVIEWABLE and row["stats_json"]:
        exp = int(time.time()) + PREVIEW_URL_TTL_S
        preview_url = f"{base}/v1/submissions/{sub_id}/preview.webp?exp={exp}&sig={_sign_preview(st.secret, sub_id, exp)}"
    return SubmissionOut(
        id=row["id"],
        status=row["status"],
        reasons=json.loads(row["reasons_json"]),
        preview_url=preview_url,
        critique=row["critique"],
    )


@router.get("/v1/submissions/{sub_id}/preview.webp")
async def submission_preview(sub_id: str, request: Request, exp: int = 0, sig: str = ""):
    """Capability URL for the owner's preview before publish (HMAC-signed, expires)."""
    st = request.app.state
    if exp < time.time() or not hmac.compare_digest(sig.encode(), _sign_preview(st.secret, sub_id, exp).encode()):
        return JSONResponse({"error": "invalid_signature"}, status_code=403)
    row = await st.db.fetchone("SELECT status, hidden FROM submissions WHERE id = ?", (sub_id,))
    # re-checked on every fetch: a rejected/deleted/hidden piece stops serving even with a valid link
    if row is None or not (row["status"] in PREVIEWABLE or (row["status"] == "published" and not row["hidden"])):
        raise ApiError(404, "not_found")
    data = await st.store.get(f"submissions/{sub_id}/render/preview.webp")
    if data is None:
        raise ApiError(404, "not_found")
    return Response(data, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})
