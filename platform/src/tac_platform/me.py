"""The signed-in artist's own account: profile, unpublish, delete. Cookie (site) or Bearer (plugin)."""

import re
import unicodedata
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator

from .auth import current_user
from .sessions import clear_session_cookie
from .web import ApiError, take_rate_token

router = APIRouter()
PROFILE_UPDATES_PER_HOUR = 30  # each one by a published artist regenerates community.json

# C0/C1 controls, zero-width, bidi marks/overrides/isolates, invisible operators, BOM and tag chars
# (spoofing); \t and \n are handled per field
_INVISIBLE = (
    "\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f\\u00ad\\u061c\\u180e\\u200b\\u200e\\u200f\\u2028-\\u202e"
    "\\u2060-\\u2064\\u2066-\\u2069\\ufeff\\U000e0000-\\U000e007f"
)
# display_name/bio: stripped. ZWNJ/ZWJ (U+200C/D) kept: they shape Persian/Indic text and join emoji (👩‍💻)
_CTRL = re.compile(f"[{_INVISIBLE}]")
_LINK_CTRL = re.compile(f"[{_INVISIBLE}\\u200c\\u200d]")  # link: refused, ZWNJ/ZWJ included


def _clean(v: str, allow_newlines: bool) -> str:
    v = unicodedata.normalize("NFC", v)
    v = v.replace("\r\n", "\n")
    v = _CTRL.sub("", v if allow_newlines else v.replace("\n", " ").replace("\t", " "))
    return v.strip()


# Instagram's own username rules: 1-30 of A-Z a-z 0-9 . _, no "..", no leading or trailing ".".
IG_RE = re.compile(r"^(?!\.)(?!.*\.\.)(?!.*\.$)[A-Za-z0-9._]{1,30}$")


class ProfileIn(BaseModel):
    """PATCH semantics: omitted = unchanged, "" or null = cleared."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    bio: str | None = None
    link: str | None = None
    instagram: str | None = None

    @field_validator("display_name")
    @classmethod
    def v_name(cls, v):
        if v is None:
            return v
        v = _clean(v, allow_newlines=False)
        if len(v) > 40:
            raise ValueError("display_name is at most 40 characters")
        return v

    @field_validator("bio")
    @classmethod
    def v_bio(cls, v):
        if v is None:
            return v
        v = _clean(v, allow_newlines=True)
        if len(v) > 280:
            raise ValueError("bio is at most 280 characters")
        return v

    @field_validator("link")
    @classmethod
    def v_link(cls, v):
        if v is None or v.strip() == "":
            return "" if v is not None else None
        v = v.strip()
        if len(v) > 200 or any(c.isspace() for c in v) or _LINK_CTRL.search(v):  # reject, never silently rewrite a URL
            raise ValueError("link must be a single https URL of at most 200 characters")
        parts = urlsplit(v)
        if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname or "@" in parts.netloc:
            raise ValueError("link must be an https:// URL with a host and no credentials")
        return v


    @field_validator("instagram")
    @classmethod
    def v_instagram(cls, v):
        if v is None or v.strip() == "":
            return "" if v is not None else None
        v = v.strip()
        if "/" in v or ":" in v or "instagram.com" in v.lower():  # refuse, never parse a handle out of a URL
            raise ValueError("instagram must be a bare handle such as @name, not a URL")
        v = v.removeprefix("@")
        if not IG_RE.fullmatch(v):
            raise ValueError("instagram must be 1-30 letters, digits, '.' or '_', with no '..' and no leading "
                             "or trailing '.'")
        return v


class DeleteIn(BaseModel):
    confirm: str


def profile_out(row) -> dict:
    return {
        "handle": row["handle"],
        "display_name": row["display_name"] or "",
        "bio": row["bio"] or "",
        "link": row["link"] or "",
        "instagram": row["instagram"] or "",
        "instagram_confirmed": bool(row["instagram"]) and bool(row["instagram_confirmed"]),
        "created": row["created_at"][:10],
    }


async def _load(request: Request, user_id: int):
    return await request.app.state.db.fetchone(
        "SELECT handle, display_name, bio, link, instagram, instagram_confirmed, created_at FROM users WHERE id = ?", (user_id,)
    )


@router.get("/v1/me")
async def get_me(request: Request) -> dict:
    user = await current_user(request)
    return profile_out(await _load(request, user["id"]))


@router.patch("/v1/me")
async def patch_me(body: ProfileIn, request: Request) -> dict:
    st = request.app.state
    user = await current_user(request)
    if not await take_rate_token(st.db, f"profile:{user['id']}", PROFILE_UPDATES_PER_HOUR, 3600):
        raise ApiError(429, "rate_limited", detail=f"at most {PROFILE_UPDATES_PER_HOUR} profile updates per hour")
    changes = body.model_dump(exclude_unset=True)
    if changes:
        cols = ", ".join(f"{k} = ?" for k in changes)  # keys are ProfileIn's field names only (extra="forbid")
        params = [v or None for v in changes.values()]
        if "instagram" in changes:
            # A different handle drops the admin confirmation. SQLite evaluates every SET expression
            # against the old row, so this compares old vs new atomically in the same statement.
            cols += ", instagram_confirmed = CASE WHEN instagram IS ? THEN instagram_confirmed ELSE 0 END"
            params.append(changes["instagram"] or None)
        async with st.db.tx() as tx:
            await tx.execute(f"UPDATE users SET {cols} WHERE id = ?", (*params, user["id"]))
            await tx.audit(f"user:{user['handle']}", "profile_update", detail=",".join(sorted(changes)))
        if await st.db.fetchone(
            "SELECT 1 FROM submissions WHERE user_id = ? AND status = 'published' AND hidden = 0 LIMIT 1", (user["id"],)
        ):
            await st.publisher.regenerate()  # artist block in community.json
    return profile_out(await _load(request, user["id"]))


@router.post("/v1/me/pieces/{piece_id:path}/unpublish")
async def unpublish(piece_id: str, request: Request) -> dict:
    """piece_id = the submission id, or "handle/slug" as listed by GET /v1/me/pieces. Owner only."""
    st = request.app.state
    user = await current_user(request)
    if "/" in piece_id:
        handle, _, slug = piece_id.partition("/")
        row = await st.db.fetchone(
            "SELECT s.id FROM submissions s WHERE s.user_id = ? AND s.slug = ? AND ? = ?",
            (user["id"], slug, handle, user["handle"]),
        )
    else:
        row = await st.db.fetchone("SELECT id FROM submissions WHERE id = ? AND user_id = ?", (piece_id, user["id"]))
    if row is None:  # not yours == not found
        raise ApiError(404, "not_found")
    result = await st.publisher.unpublish(row["id"], f"user:{user['handle']}")
    if result is None:
        raise ApiError(409, "not_unpublishable", detail="only published or in-review pieces can be unpublished")
    return result


@router.delete("/v1/me", status_code=204)
async def delete_me(body: DeleteIn, request: Request) -> Response:
    st = request.app.state
    user = await current_user(request)
    if body.confirm != user["handle"]:
        raise ApiError(400, "confirm_mismatch", detail="send {\"confirm\": \"<your handle>\"}")
    if not await st.publisher.delete_account(user["id"], user["handle"]):
        raise ApiError(409, "busy", detail="a submission is rendering; try again in a few minutes")
    resp = Response(status_code=204)
    if user["via"] == "cookie":
        clear_session_cookie(request, resp)
    return resp

