"""Terms acceptance: the checkbox on every sign-in path, version storage, re-acceptance, submit gating."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import META, make_ctx
from tac_platform import auth, terms, web_auth
from tac_platform.config import Settings

SITE = "https://terminalart.club"
AGREE_TEXT = "I agree to the <a href='https://terminalart.club/terms'"


def bump(ctx, version: int) -> None:
    object.__setattr__(ctx.app.state.settings, "terms_version", version)  # Settings is frozen


async def user_terms(ctx, handle: str) -> tuple:
    row = await ctx.app.state.db.fetchone("SELECT terms_version, terms_accepted_at FROM users WHERE handle = ?",
                                          (handle,))
    return (row["terms_version"], row["terms_accepted_at"]) if row else (None, None)


async def audits(ctx, handle: str) -> list[str]:
    rows = await ctx.app.state.db.fetchall("SELECT detail FROM audit_log WHERE action = 'terms_accepted' AND actor = ?",
                                           (f"user:{handle}",))
    return [r["detail"] for r in rows]


def token_of(page: httpx.Response) -> str:
    m = re.search(r"name=token value='([^']+)'", page.text)
    assert m, page.text
    return m.group(1)


@pytest.fixture
async def site_ctx(tmp_path):
    async with make_ctx(tmp_path, site_url="http://localhost:5181") as c:
        yield c


# ── the box itself ─────────────────────────────────────────────────────────


def test_checkbox_is_required_unticked_and_links_from_site_url():
    box = terms.checkbox(Settings.from_env(site_url="https://gallery.example", auth_mode="dev"))
    assert "type=checkbox name=agree value=1 required" in box and "checked" not in box
    assert "href='https://gallery.example/terms'" in box and "href='https://gallery.example/content-policy'" in box
    default = terms.checkbox(Settings.from_env(site_url="", auth_mode="dev"))
    assert AGREE_TEXT in default  # no TAC_SITE_URL: the public site


@pytest.mark.parametrize("bad", ["0", "-1", "x"])
def test_terms_version_setting_must_be_positive_int(monkeypatch, bad):
    monkeypatch.setenv("TAC_TERMS_VERSION", bad)
    with pytest.raises((RuntimeError, ValueError)):
        Settings.from_env()


# ── dev /device ────────────────────────────────────────────────────────────


async def test_dev_device_refused_without_box_and_stores_version_with_it(ctx):
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        page = await c.get("/device")
        assert "name=agree" in page.text and "label class=agree" in page.text
        r = await c.post("/device", data={"user_code": d["user_code"], "handle": "alex"})
        assert r.status_code == 400 and "Tick the box" in r.text and "name=agree" in r.text  # re-rendered inline
        assert await user_terms(ctx, "alex") == (None, None)  # nothing created
        r = await c.post("/device", data={"user_code": d["user_code"], "handle": "alex", "agree": "1"})
        assert r.status_code == 200
    version, at = await user_terms(ctx, "alex")
    assert version == 1 and at and await audits(ctx, "alex") == ["v1"]


# ── dev web login ──────────────────────────────────────────────────────────


async def test_dev_web_login_box_and_reacceptance_after_bump(ctx):
    async with ctx.client(ip="10.8.8.8") as c:
        form = await c.get("/v1/auth/web/login")
        assert "name=agree" in form.text
        r = await c.post("/v1/auth/web/login", data={"handle": "alex", "return": "/"})
        assert r.status_code == 400 and "Tick the box" in r.text and not c.cookies.get("tac_session")
        assert (await c.post("/v1/auth/web/login", data={"handle": "alex", "agree": "1"})).status_code == 303
        assert (await user_terms(ctx, "alex"))[0] == 1
        # current: signs in without the box (the dev form still shows it; it's just not required)
        assert (await c.post("/v1/auth/web/login", data={"handle": "alex"})).status_code == 303
        bump(ctx, 2)  # site published v2: the next sign-in must accept again
        r = await c.post("/v1/auth/web/login", data={"handle": "alex"})
        assert r.status_code == 400 and "Tick the box" in r.text
        assert (await c.post("/v1/auth/web/login", data={"handle": "alex", "agree": "1"})).status_code == 303
    assert (await user_terms(ctx, "alex"))[0] == 2 and await audits(ctx, "alex") == ["v1", "v2"]


# ── GitHub device ──────────────────────────────────────────────────────────


async def _gh_device_callback(ctx, c, monkeypatch, gh=(4242, "alexgh")):
    async def fake_identity(st, code):
        return gh

    monkeypatch.setattr(auth, "github_identity", fake_identity)
    d = (await c.post("/v1/auth/device", json={})).json()
    start = await c.post("/device/github", data={"user_code": d["user_code"]})
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    c.cookies.set("tac_gh_nonce", state.split(".")[1])
    return d, await c.get("/device/github/callback", params={"code": "gh", "state": state})


async def test_github_device_interstitial_only_when_behind(tmp_path, monkeypatch):
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            assert "name=agree" not in (await c.get("/device")).text  # identity unknown yet: no box here
            d, step = await _gh_device_callback(ctx, c, monkeypatch)
            assert step.status_code == 200 and "name=agree" in step.text and "alexgh" in step.text
            pending = await ctx.app.state.db.fetchone("SELECT status FROM device_codes WHERE user_code = ?",
                                                      (d["user_code"],))
            assert pending["status"] == "pending"  # not approved before the box is ticked
            r = await c.post("/device/github/terms", data={"token": token_of(step)})
            assert r.status_code == 400 and "Tick the box" in r.text
            r = await c.post("/device/github/terms", data={"token": token_of(step), "agree": "1"})
            assert r.status_code == 200 and "connected as alexgh" in r.text
            assert (await c.post("/v1/auth/token", json={"device_code": d["device_code"]})).status_code == 200
            assert (await user_terms(ctx, "alexgh"))[0] == 1
            # current user: straight through, no box
            d2, again = await _gh_device_callback(ctx, c, monkeypatch)
            assert again.status_code == 200 and "connected as alexgh" in again.text and "name=agree" not in again.text
            bump(ctx, 2)
            d3, behind = await _gh_device_callback(ctx, c, monkeypatch)
            assert "name=agree" in behind.text  # version bump: asked again
        assert await audits(ctx, "alexgh") == ["v1"]


async def test_github_device_terms_token_needs_its_cookie_and_signature(tmp_path, monkeypatch):
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            _, step = await _gh_device_callback(ctx, c, monkeypatch)
            token = token_of(step)
        async with ctx.client() as other:  # a leaked form token without the nonce cookie
            r = await other.post("/device/github/terms", data={"token": token, "agree": "1"})
            assert r.status_code == 400 and "expired" in r.text
        async with ctx.client() as c:
            _, step = await _gh_device_callback(ctx, c, monkeypatch)
            tok = token_of(step)
            forged = tok.replace(f".{tok.split('.')[1]}.", ".999.", 1)  # another user id, old signature
            assert forged != tok
            assert (await c.post("/device/github/terms", data={"token": forged, "agree": "1"})).status_code == 400
            web_kind = await c.post("/v1/auth/web/terms", data={"token": tok, "agree": "1"})
            assert web_kind.status_code == 400  # a device token can't finish a web sign-in
        assert (await user_terms(ctx, "alexgh"))[0] is None


# ── GitHub web login ───────────────────────────────────────────────────────


async def test_github_web_login_interstitial_and_reacceptance(tmp_path, monkeypatch):
    async def fake_identity(st, code):
        return 4242, "alexgh"

    monkeypatch.setattr(web_auth, "github_identity", fake_identity)
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            async def callback():
                r = await c.get("/v1/auth/web/login", params={"return": "/me"})
                state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
                return await c.get("/v1/auth/web/github/callback", params={"code": "gh", "state": state})

            step = await callback()
            assert step.status_code == 200 and "name=agree" in step.text and not c.cookies.get("tac_session")
            r = await c.post("/v1/auth/web/terms", data={"token": token_of(step)})
            assert r.status_code == 400 and "Tick the box" in r.text and not c.cookies.get("tac_session")
            r = await c.post("/v1/auth/web/terms", data={"token": token_of(step), "agree": "1"})
            assert r.status_code == 303 and r.headers["location"] == "/me" and c.cookies.get("tac_session")
            assert (await callback()).status_code == 303  # current: no interstitial
            bump(ctx, 3)
            assert "name=agree" in (await callback()).text
            cross = await c.post("/v1/auth/web/terms", data={"token": token_of(step), "agree": "1"},
                                 headers={"sec-fetch-site": "cross-site"})
            assert cross.status_code == 403  # login CSRF guard, as on the dev form
        assert (await user_terms(ctx, "alexgh"))[0] == 1


# ── submit gating + /v1/me ─────────────────────────────────────────────────


async def test_submit_403_until_current_terms_accepted(site_ctx):
    ctx = site_ctx
    token = await ctx.login("alex")
    async with ctx.client(authorization=f"Bearer {token}") as b:
        me = (await b.get("/v1/me")).json()
        assert (me["terms_version"], me["terms_current"]) == (1, True)
    bump(ctx, 2)
    r = await ctx.submit(token)
    assert r.status_code == 403
    assert r.json() == {"error": "terms_not_accepted", "terms_url": "http://localhost:5181/terms", "terms_version": 2,
                        "detail": "accept the current terms by signing in again (/tac:login)"}
    async with ctx.client(authorization=f"Bearer {token}") as b:
        me = (await b.get("/v1/me")).json()
        assert (me["terms_version"], me["terms_current"]) == (1, False)
    await ctx.app.state.db.execute("UPDATE users SET terms_version = NULL WHERE handle = 'alex'")
    bump(ctx, 1)
    assert (await ctx.submit(token)).status_code == 403  # never accepted
    assert await ctx.app.state.db.fetchone("SELECT 1 FROM submissions") is None


@pytest.mark.parametrize("value", [None, False, "true", 1, "yes"])
async def test_submit_400_without_rights_confirmed(ctx, value):
    token = await ctx.login("alex")
    meta = {k: v for k, v in META.items() if k != "rights_confirmed"}
    if value is not None:
        meta["rights_confirmed"] = value
    r = await ctx.submit(token, meta=meta)
    assert r.status_code == 400 and r.json()["error"] == "rights_not_confirmed"
    assert "characters, brands or logos" in r.json()["detail"]
    ok = await ctx.submit(token, meta={**META, "rights_confirmed": True})
    assert ok.status_code == 202
    stored = json.loads((await ctx.app.state.db.fetchone("SELECT meta_json FROM submissions"))["meta_json"])
    assert stored["rights_confirmed"] is True  # the attestation is kept with the piece


@pytest.mark.parametrize("cookie", ["нонс", "été", "\U0001f600"])
async def test_non_ascii_nonce_cookie_is_a_400_not_a_500(tmp_path, monkeypatch, cookie):
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            _, step = await _gh_device_callback(ctx, c, monkeypatch)
            token = token_of(step)
        # raw UTF-8 bytes in the Cookie header: httpx's jar refuses non-ASCII, a hostile client won't
        async with ctx.client() as other:
            r = await other.post("/device/github/terms", data={"token": token, "agree": "1"},
                                 headers={"cookie": f"{terms.NONCE_COOKIE}={cookie}".encode()})
            assert r.status_code == 400 and "expired" in r.text


async def test_pending_terms_token_expires_after_ttl(tmp_path, monkeypatch):
    import time as real_time

    clock = [real_time.time()]
    monkeypatch.setattr(terms.time, "time", lambda: clock[0])
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            d, step = await _gh_device_callback(ctx, c, monkeypatch)
            token = token_of(step)
            assert int(token.split(".")[3]) == int(clock[0]) + terms.PENDING_TTL_S  # exp = issue + 10 min
            clock[0] += terms.PENDING_TTL_S + 1  # just past the deadline
            r = await c.post("/device/github/terms", data={"token": token, "agree": "1"})
            assert r.status_code == 400 and "expired" in r.text
            assert (await user_terms(ctx, "alexgh"))[0] is None  # nothing recorded
            clock[0] -= 2  # one second before the deadline: still good
            r = await c.post("/device/github/terms", data={"token": token, "agree": "1"})
            assert r.status_code == 200 and "connected as alexgh" in r.text
