"""Automod's monthly budget: cost from usage, the atomic ledger, the cutoff, and the human-review fallback."""

import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from conftest import fake_claude, make_ctx
from tac_platform import automod_budget as budget
from tac_platform.automod import CODE_LIMIT, Automod, extract_frames
from tac_platform.config import Settings

SAFE = {"safe": True, "flags": [], "on_brief": True}


def usage_client(input_tokens: int, output_tokens: int, cache_w: int = 0, cache_r: int = 0):
    c = fake_claude(SAFE)
    c.messages.response.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                                cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    return c


def test_cost_math_sonnet_5_5_prices():
    s = Settings.from_env(auth_mode="dev")
    assert s.automod_model == "claude-sonnet-5-5" and s.automod_budget_usd == 10.0
    p = budget.price(s, "claude-sonnet-5-5")
    assert p == (2.0, 10.0)
    # 4,000 in + 120 out = 0.008 + 0.0012
    assert budget.cost_usd({"input_tokens": 4000, "output_tokens": 120}, p) == pytest.approx(0.0092)
    # cache write 1.25x input, cache read 0.1x input; null fields are 0
    u = {"input_tokens": 0, "output_tokens": None, "cache_creation_input_tokens": 1_000_000,
         "cache_read_input_tokens": 1_000_000}
    assert budget.cost_usd(u, p) == pytest.approx(2.5 + 0.2)
    assert budget.price(s, "claude-unknown-9") is None


def test_unpriced_model_fails_closed():
    s = Settings.from_env(auth_mode="dev", automod_model="claude-unknown-9")
    with pytest.raises(RuntimeError, match="no price"):
        Automod(s, client=fake_claude(SAFE))


@pytest.mark.parametrize("bad", ["-1", "nan", "x"])
def test_budget_setting_validated(monkeypatch, bad):
    monkeypatch.setenv("TAC_AUTOMOD_BUDGET_USD", bad)
    with pytest.raises((RuntimeError, ValueError)):
        Settings.from_env()


async def test_ledger_charge_is_atomic_under_concurrency(ctx):
    db = ctx.app.state.db
    await asyncio.gather(*(budget.charge(db, 0.01, month="2026-10") for _ in range(200)))
    assert await budget.month_spend(db, "2026-10") == pytest.approx(2.00)
    assert await budget.month_spend(db, "2026-11") == 0.0  # months are separate rows
    await budget.charge(db, 0.0, month="2026-11")
    assert await db.fetchone("SELECT 1 FROM automod_spend WHERE month = '2026-11'") is None


async def test_each_call_is_charged_and_logged(tmp_path, caplog):
    caplog.set_level("INFO", logger="tac.automod")
    client = usage_client(4000, 120)
    async with make_ctx(tmp_path, automod_client=client) as ctx:
        token = await ctx.login("alex")
        for _ in range(2):
            sub = (await ctx.submit(token)).json()
            assert (await ctx.wait(token, sub["id"]))["status"] == "in_review"
        assert await budget.month_spend(ctx.app.state.db) == pytest.approx(2 * 0.0092)
    assert sum("automod call: model=claude-sonnet-5-5 in=4000 out=120" in r.message for r in caplog.records) == 2
    assert any("cost=$0.00920" in r.message for r in caplog.records)


async def test_refusal_is_charged_too(tmp_path):
    client = fake_claude(None, "refusal", "cyber")
    async with make_ctx(tmp_path, automod_client=client) as ctx:
        token = await ctx.login("alex")
        sub = (await ctx.submit(token)).json()
        await ctx.wait(token, sub["id"])
        assert await budget.month_spend(ctx.app.state.db) == pytest.approx((10 * 2 + 5 * 10) / 1e6)


async def test_budget_reached_skips_call_and_sends_piece_to_human(tmp_path):
    client = usage_client(4000, 120)
    async with make_ctx(tmp_path, automod_client=client, automod_budget_usd=1.0) as ctx:
        db = ctx.app.state.db
        await budget.charge(db, 1.0)  # exactly at the budget: "at or above" stops it
        token = await ctx.login("alex")
        await db.execute("UPDATE users SET trusted = 1 WHERE handle = 'alex'")  # trusted: still a human
        sub = (await ctx.submit(token)).json()
        st = await ctx.wait(token, sub["id"])
        assert st["status"] == "in_review" and st["reasons"] == ["automod budget reached"]
        assert client.messages.calls == []  # no API call, no spend
        assert await budget.month_spend(db) == pytest.approx(1.0)
        async with ctx.admin() as a:
            page = (await a.get("/admin")).text
        assert "automod: $1.00 of $1 this month" in page and "budget reached" in page


async def test_just_under_budget_still_calls(tmp_path):
    client = usage_client(4000, 120)
    async with make_ctx(tmp_path, automod_client=client, automod_budget_usd=1.0) as ctx:
        await budget.charge(ctx.app.state.db, 0.999)
        token = await ctx.login("alex")
        sub = (await ctx.submit(token)).json()
        await ctx.wait(token, sub["id"])
        assert len(client.messages.calls) == 1
        assert await budget.month_spend(ctx.app.state.db) == pytest.approx(0.999 + 0.0092)  # may overshoot once


async def test_admin_shows_spend(ctx):
    await budget.charge(ctx.app.state.db, 3.456)
    async with ctx.admin() as a:
        page = (await a.get("/admin")).text
    assert "automod: $3.46 of $10 this month" in page


def test_frames_downscaled_and_code_truncated():
    frames = [Image.new("RGB", (540, 960), (i * 40, 0, 0)) for i in range(5)]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:])
    for png in extract_frames(buf.getvalue()):
        assert max(Image.open(io.BytesIO(png)).size) <= 512
    assert CODE_LIMIT == 20_000


async def test_code_sent_is_truncated_to_20k(tmp_path):
    client = fake_claude(SAFE)
    am = Automod(Settings.from_env(auth_mode="dev"), client=client)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, "WEBP")
    res = await am.review("x" * 50_000, buf.getvalue(), "t", "d")
    text = client.messages.calls[0]["messages"][0]["content"][-1]["text"]
    assert res.verdict is not None and text.count("x") == 20_000 and 'truncated="true"' in text
    assert client.messages.calls[0]["output_config"]["format"]["schema"]["required"] == ["safe", "flags", "on_brief"]
