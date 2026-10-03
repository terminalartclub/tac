"""Automod's hard monthly budget: a per-call cost from the response usage, summed in automod_spend.

before a call   month_spend(db) >= settings.automod_budget_usd ? skip automod -> in_review, "automod budget reached"
after a call    cost = usage x settings.automod_prices[model]   -> one atomic UPSERT into automod_spend(month)

Never blocks a submission: an over-budget piece waits for a human like any other. The check is a read
before the call and the charge an atomic add after it, so concurrent reviews can overshoot by at most
(render_concurrency - 1) calls, each at most worst_case_call_usd(): about $0.03 at the defaults
(Sonnet 5.5, 20k code chars, 3 frames <= 512 px, 300 output tokens). A typical call is about $0.01.
"""

import logging
from datetime import UTC, datetime

from .db import Database

log = logging.getLogger("tac.automod")

CACHE_WRITE_X = 1.25  # 5-minute cache writes bill at 1.25x input (prompt caching docs)
CACHE_READ_X = 0.10


# Upper-bound token estimates for worst_case_call_usd (estimates, not tokenizer output):
WORST_CHARS_PER_TOKEN = 2.0  # dense or minified code; ordinary Python is ~3-4 chars/token
IMAGE_PX_PER_TOKEN = 750  # image tokens ~= width x height / 750 (Anthropic vision docs)
FRAMES = 3
FIXED_TEXT_TOKENS = 1_000  # system prompt (~1.2k chars) + title (<=80) + description (<=400) + labels, rounded up


def worst_case_call_usd(settings) -> float:
    """Most one automod call can cost under the request limits in automod.py, at the configured model."""
    from .automod import CODE_LIMIT, FRAME_MAX_SIDE, MAX_TOKENS  # automod imports this module

    per_mtok = price(settings, settings.automod_model)
    if per_mtok is None:
        return float("nan")
    tokens_in = (CODE_LIMIT / WORST_CHARS_PER_TOKEN + FRAMES * FRAME_MAX_SIDE * FRAME_MAX_SIDE / IMAGE_PX_PER_TOKEN
                 + FIXED_TEXT_TOKENS)
    return cost_usd({"input_tokens": round(tokens_in), "output_tokens": MAX_TOKENS}, per_mtok)


def this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def price(settings, model: str) -> tuple[float, float] | None:
    p = settings.automod_prices.get(model)
    return (float(p[0]), float(p[1])) if p else None


def cost_usd(usage: dict, per_mtok: tuple[float, float]) -> float:
    """USD for one response's usage. Missing or null fields count as 0."""
    pin, pout = per_mtok
    n = {k: int(usage.get(k) or 0) for k in
         ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
    return (n["input_tokens"] * pin + n["cache_creation_input_tokens"] * pin * CACHE_WRITE_X
            + n["cache_read_input_tokens"] * pin * CACHE_READ_X + n["output_tokens"] * pout) / 1_000_000


async def month_spend(db: Database, month: str | None = None) -> float:
    row = await db.fetchone("SELECT usd FROM automod_spend WHERE month = ?", (month or this_month(),))
    return float(row["usd"]) if row else 0.0


async def over_budget(db: Database, settings) -> bool:
    return await month_spend(db) >= settings.automod_budget_usd


async def charge(db: Database, usd: float, month: str | None = None) -> None:
    """Add to this month's spend in one statement (atomic under concurrent writers)."""
    if usd <= 0:
        return
    await db.execute(
        "INSERT INTO automod_spend (month, usd) VALUES (?, ?)"
        " ON CONFLICT(month) DO UPDATE SET usd = usd + excluded.usd",
        (month or this_month(), usd),
    )
