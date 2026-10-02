"""Pydantic v2 models for the wire contract."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HANDLE_RE = re.compile(r"^[a-z0-9-]{2,24}$")
Status = Literal["queued", "rendering", "rejected", "in_review", "published"]


class SubmissionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    model: str = Field(pattern=r"^claude-[a-z0-9.-]{1,60}$")
    tokens: int | None = Field(default=None, ge=0)
    iterations: int | None = Field(default=None, ge=0, le=10_000)
    loop_s: float | None = Field(default=None, gt=0, le=3600)
    license: str = Field(default="CC-BY-4.0 art / MIT code", max_length=80)
    process_notes: list[str] = Field(default_factory=list, max_length=4)
    human_role: Literal["none", "seeded", "directed"] = "none"  # declared; the server recomputes it from notes.md
    size: Literal["sketch", "full"] = "full"
    tokens_source: Literal["user", "transcript-estimate", "subagent-total", "unknown"] = "unknown"

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
    url: str


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
