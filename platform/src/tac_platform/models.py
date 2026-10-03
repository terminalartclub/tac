"""Pydantic v2 models for the wire contract."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HANDLE_RE = re.compile(r"^[a-z0-9-]{2,24}$")
# Self-reported tokens feed the site's public "N tokens of spare Claude usage" counter. The largest
# house piece used ~530k; 2M is a hard ceiling, and anything over HIGH_TOKENS is flagged for review.
MAX_TOKENS = 2_000_000
HIGH_TOKENS = 1_000_000
# What submissions.slugify() emits: ≤40 chars of [a-z0-9] runs joined by "-", plus an optional "-N"
# collision suffix. tac-studio's `tacctl gallery` mirrors both patterns (a test pins them equal).
SLUG_RE = re.compile(r"^(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
Status = Literal["queued", "rendering", "rejected", "in_review", "published"]


class SubmissionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    model: str = Field(pattern=r"^claude-[a-z0-9.-]{1,60}$")
    tokens: int | None = None  # validated strictly below: whole number 0..MAX_TOKENS, or null (unknown)
    iterations: int | None = Field(default=None, ge=0, le=10_000)
    loop_s: float | None = Field(default=None, gt=0, le=3600)
    license: str = Field(default="CC-BY-4.0 art / MIT code", max_length=80)
    process_notes: list[str] = Field(default_factory=list, max_length=4)
    human_role: Literal["none", "seeded", "directed"] = "none"  # declared; the server recomputes it from notes.md
    size: Literal["sketch", "full"] = "full"
    tokens_source: Literal["user", "transcript-estimate", "subagent-total", "unknown"] = "unknown"

    @field_validator("tokens", mode="before")
    @classmethod
    def tokens_in_range(cls, v: object) -> int | None:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):  # no 1.0, "12" or true: the counter sums these
            raise ValueError("tokens must be a whole number or null (unknown)")
        if v < 0:
            raise ValueError("tokens must be 0 or more")
        if v > MAX_TOKENS:
            raise ValueError(f"tokens must be at most {MAX_TOKENS:,} (got {v:,}); the largest house piece used ~530,000")
        return v

    @field_validator("title", "description")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("process_notes")
    @classmethod
    def short_notes(cls, v: list[str]) -> list[str]:
        return [n.strip()[:280] for n in v]


class DeviceCodeOut(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int = 3
    expires_in: int = 600


class TokenIn(BaseModel):
    device_code: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    handle: str


class SubmissionAccepted(BaseModel):
    id: str
    status: Status
    url: str  # owner-only status URL (Bearer)
    piece_url: str | None = None  # TAC_SITE_URL/@<handle>/<slug> once published; None without TAC_SITE_URL


class SubmissionOut(BaseModel):
    id: str
    status: Status
    reasons: list[str]
    preview_url: str | None
    critique: str | None


class ReportIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RejectIn(BaseModel):
    reason: str = Field(default="rejected by moderator", min_length=1, max_length=500)


class AutomodVerdict(BaseModel):
    safe: bool
    flags: list[str]
    on_brief: bool
    critique: str

    @field_validator("critique")
    @classmethod
    def cap(cls, v: str) -> str:
        return v.strip()[:600]
