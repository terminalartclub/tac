import json
import time
from contextlib import aclosing
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import ADMIN, handle_slug, make_ctx
from tac_platform.web import sha256_hex
from tac_platform.web_auth import safe_return

SITE = "http://localhost:5181"


async def web_login(ctx, handle: str, ret: str = "/me") -> httpx.AsyncClient:
    """A browser-like client signed in through the dev web form (cookie jar keeps tac_session)."""
    c = ctx.client(ip="10.7.7.7")
    form = await c.get("/v1/auth/web/login", params={"return": ret})
    assert form.status_code == 200 and "action='login'" in form.text
    r = await c.post("/v1/auth/web/login", data={"handle": handle, "return": ret})
    assert r.status_code == 303, r.text
    assert c.cookies.get("tac_session")
    return c


async def csrf(c: httpx.AsyncClient) -> dict:
    r = await c.get("/v1/auth/web/csrf")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return {"x-tac-csrf": r.json()["csrf"], "sec-fetch-site": "same-origin"}


async def published(ctx, token: str) -> tuple[str, str, str]:
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return (sub["id"], *handle_slug(st["preview_url"]))


@pytest.mark.parametrize(("value", "expected"), [
    ("/me", "/me"), ("/p/alex/laps?x=1#y", "/p/alex/laps?x=1#y"), (None, "/"), ("", "/"),
    ("//evil.example", "/"), ("/\\evil.example", "/"), ("https://evil.example/", "/"), ("evil", "/"),
    ("/\t/evil.example", "/"), ("/ok path", "/"), ("javascript:alert(1)", "/"), ("/" + "a" * 600, "/"),
])
def test_safe_return(value, expected):
    assert safe_return(value) == expected


async def test_login_logout_same_user_as_plugin(ctx):
    token = await ctx.login("alex")  # plugin device flow
    c = await web_login(ctx, "alex", "/p/alex")
    async with aclosing(c):
        me = (await c.get("/v1/me")).json()
        assert me["handle"] == "alex" and set(me) == {"handle", "display_name", "bio", "link", "created"}
        # rotation: logging in again replaces the session
        old = c.cookies.get("tac_session")
        r = await c.post("/v1/auth/web/login", data={"handle": "alex", "return": "/"})
        assert r.status_code == 303 and c.cookies.get("tac_session") != old
        assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM web_sessions"))["n"] == 1
        # logout needs CSRF; then the session is gone server-side and the cookie cleared
        assert (await c.post("/v1/auth/web/logout")).status_code == 403
        headers = await csrf(c)
        r = await c.post("/v1/auth/web/logout", headers=headers)
        assert r.status_code == 204 and "tac_session=" in r.headers["set-cookie"]
        assert (await c.get("/v1/me")).status_code == 401
    assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM web_sessions"))["n"] == 0
    async with ctx.client(authorization=f"Bearer {token}") as b:  # bearer unaffected
        assert (await b.get("/v1/me")).json()["handle"] == "alex"


async def test_redirect_and_cookie_flags_dev(ctx):
    async with ctx.client() as c:
        r = await c.post("/v1/auth/web/login", data={"handle": "alex", "return": "//evil.example/x"})
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
        assert "secure" not in cookie and "domain=" not in cookie
        assert "max-age=2592000" in cookie


PROD = dict(worker_enabled=False, env="prod", auth_mode="github", renderer="docker", github_client_id="cid",
            public_base_url="https://api.terminalart.club", site_url="https://terminalart.club",
            site_origins=("https://terminalart.club",))


async def github_login(ctx, monkeypatch, base: str, ret: str = "/p/alex") -> tuple[httpx.AsyncClient, httpx.Response]:
    """Browser-like client through the web GitHub flow (identity faked); returns the client and the callback."""
    from tac_platform import web_auth

    async def fake_identity(st, code):
        return 4242, "alexgh"

    monkeypatch.setattr(web_auth, "github_identity", fake_identity)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app, client=("10.0.0.1", 5000)), base_url=base)
    r = await c.get("/v1/auth/web/login", params={"return": ret})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    cb = await c.get("/v1/auth/web/github/callback", params={"code": "gh-code", "state": state})
    assert cb.status_code == 303, cb.text
    return c, cb


async def test_cookie_prod_is_host_prefixed_without_domain(tmp_path, monkeypatch):
    async with make_ctx(tmp_path, **PROD) as ctx:
        c, cb = await github_login(ctx, monkeypatch, "https://api.terminalart.club")
        async with aclosing(c):
            assert cb.headers["location"] == "https://terminalart.club/p/alex"
            cookie = cb.headers["set-cookie"].lower()
            assert cookie.startswith("__host-tac_session=") and "domain=" not in cookie
            assert "secure" in cookie and "path=/" in cookie and "samesite=lax" in cookie and "httponly" in cookie
            # every reader uses the configured name: current_user, csrf, logout
            assert c.cookies.get("__Host-tac_session") and not c.cookies.get("tac_session")
            assert (await c.get("/v1/me")).json()["handle"] == "alexgh"
            r = await c.post("/v1/auth/web/logout", headers=await csrf(c))
            assert r.status_code == 204 and r.headers["set-cookie"].startswith("__Host-tac_session=")
            assert (await c.get("/v1/me")).status_code == 401
        # the dev cookie name carries nothing in prod
        sid = "x" * 43
        await ctx.app.state.db.execute(
            "INSERT INTO web_sessions (session_sha256, user_id, created_at, expires_at) VALUES (?, 1, '', ?)",
            (sha256_hex(sid), time.time() + 60))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://api.terminalart.club",
                                     cookies={"tac_session": sid}) as dev:
            assert (await dev.get("/v1/me")).status_code == 401
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://api.terminalart.club",
                                     cookies={"__Host-tac_session": sid}) as ok:
            assert (await ok.get("/v1/me")).status_code == 200


def test_cookie_domain_knob_gone(monkeypatch, tmp_path):
    from tac_platform.config import Settings

    monkeypatch.setenv("TAC_COOKIE_DOMAIN", ".terminalart.club")
    assert not hasattr(Settings.from_env(data_dir=tmp_path), "cookie_domain")


async def test_session_stored_hashed_and_expires(ctx):
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        sid = c.cookies.get("tac_session")
        rows = await ctx.app.state.db.fetchall("SELECT session_sha256, expires_at FROM web_sessions")
        assert len(rows) == 1 and rows[0]["session_sha256"] != sid and sid not in json.dumps([dict(r) for r in rows])
        assert 29 * 86400 < rows[0]["expires_at"] - time.time() <= 30 * 86400
        await ctx.app.state.db.execute("UPDATE web_sessions SET expires_at = ?", (time.time() - 1,))
        assert (await c.get("/v1/me")).status_code == 401


async def test_csrf_required_for_cookie_state_changes(ctx):
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        good = await csrf(c)
        body = {"display_name": "Alex"}
        assert (await c.patch("/v1/me", json=body)).status_code == 403  # no header
        assert (await c.patch("/v1/me", json=body, headers={"x-tac-csrf": "0" * 64})).status_code == 403
        r = await c.patch("/v1/me", json=body, headers={**good, "sec-fetch-site": "cross-site"})
        assert r.status_code == 403  # right token, cross-site fetch
        assert (await c.patch("/v1/me", json=body, headers={**good, "sec-fetch-site": "same-site"})).status_code == 200
        # a token from another session doesn't work
        other = await web_login(ctx, "bea")
        async with aclosing(other):
            theirs = await csrf(other)
        assert (await c.patch("/v1/me", json=body, headers=theirs)).status_code == 403
    # bearer is exempt (not ambient: browsers can't attach it cross-site)
    token = await ctx.login("cara")
    async with ctx.client(authorization=f"Bearer {token}") as b:
        assert (await b.patch("/v1/me", json=body)).status_code == 200


async def test_profile_validation_and_escaping(ctx):
    token = await ctx.login("alex")
    async with ctx.client(authorization=f"Bearer {token}") as b:
        bad = [{"display_name": "x" * 41}, {"bio": "y" * 281}, {"link": "http://example.com"},
               {"link": "javascript:alert(1)"}, {"link": "https://user:pw@example.com"},
               {"link": "https://" + "a" * 200 + ".com"}, {"link": "https://exa mple.com"}, {"handle": "root"}]
        for body in bad:
            r = await b.patch("/v1/me", json=body)
            assert r.status_code == 400, body
        xss = {"display_name": "<script>alert(1)</script>‮", "bio": "line1\nline2 <b>x</b>\x07",
               "link": "https://example.com/a?b=<c>"}
        me = (await b.patch("/v1/me", json=xss)).json()
        assert me["display_name"] == "<script>alert(1)</script>" and me["bio"] == "line1\nline2 <b>x</b>"
        assert (await b.patch("/v1/me", json={"bio": ""})).json()["bio"] == ""  # "" clears
        assert (await b.patch("/v1/me", json={})).json()["display_name"] == xss["display_name"][:-1]  # omitted = kept
    await published(ctx, token)
    async with ctx.admin() as a:
        html = (await a.get("/admin")).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    async with ctx.client() as c:
        doc = (await c.get("/v1/community.json")).json()
    assert doc["pieces"][0]["artist"] == {"display_name": "<script>alert(1)</script>", "bio": "",
                                          "link": "https://example.com/a?b=<c>"}


async def test_artist_block_absent_without_profile(ctx):
    token = await ctx.login("alex")
    await published(ctx, token)
    async with ctx.client() as c:
        assert "artist" not in (await c.get("/v1/community.json")).json()["pieces"][0]


async def test_unpublish_owner_only(ctx):
    alex = await ctx.login("alex")
    sub_id, h, s = await published(ctx, alex)
    bea = await ctx.login("bea")
    async with ctx.client(authorization=f"Bearer {bea}") as b:
        assert (await b.post(f"/v1/me/pieces/{sub_id}/unpublish")).status_code == 404
        assert (await b.post(f"/v1/me/pieces/{h}/{s}/unpublish")).status_code == 404
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        r = await c.post(f"/v1/me/pieces/{h}/{s}/unpublish", headers=await csrf(c))
        assert r.status_code == 200 and r.json() == {"id": f"{h}/{s}", "status": "rejected"}
        assert (await c.post(f"/v1/me/pieces/{sub_id}/unpublish", headers=await csrf(c))).status_code == 409
        mine = (await c.get("/v1/me/pieces")).json()["pieces"][0]
        assert mine["status"] == "rejected" and mine["reasons"] == ["unpublished by the artist"]
        assert (await c.get(f"/media/{h}/{s}/preview.webp")).status_code == 404
        assert (await c.get("/v1/community.json")).json()["pieces"] == []
    assert await ctx.app.state.store.list(f"submissions/{sub_id}/render") == []
    audit = await ctx.app.state.db.fetchone("SELECT actor, to_status FROM audit_log WHERE action = 'unpublish'")
    assert dict(audit) == {"actor": "user:alex", "to_status": "rejected"}


async def test_delete_account_cascade(ctx):
    alex = await ctx.login("alex")
    sub_id, h, s = await published(ctx, alex)
    async with ctx.client(ip="9.9.9.9") as v:
        await v.post(f"/v1/pieces/{h}/{s}/view")
        await v.post(f"/v1/pieces/{h}/{s}/report", json={"reason": "x"})
    bea = await ctx.login("bea")
    await published(ctx, bea)
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        headers = await csrf(c)
        assert (await c.request("DELETE", "/v1/me", json={"confirm": "bea"}, headers=headers)).status_code == 400
        r = await c.request("DELETE", "/v1/me", json={"confirm": "alex"}, headers=headers)
        assert r.status_code == 204 and "tac_session=" in r.headers["set-cookie"]
        assert (await c.get("/v1/me")).status_code == 401
    async with ctx.client(authorization=f"Bearer {alex}") as b:
        assert (await b.get("/v1/me")).status_code == 401  # plugin token gone too
    db = ctx.app.state.db
    for table, where in (("users", "handle = 'alex'"), ("submissions", f"id = '{sub_id}'"),
                         ("reports", f"submission_id = '{sub_id}'"), ("views", f"submission_id = '{sub_id}'"),
                         ("view_days", f"submission_id = '{sub_id}'"), ("web_sessions", "1"),
                         ("access_tokens", "user_id NOT IN (SELECT id FROM users)")):
        n = (await db.fetchone(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}"))["n"]
        assert n == 0, table
    store = ctx.app.state.store
    assert await store.list(f"submissions/{sub_id}") == [] and await store.list(f"public/{h}") == []
    async with ctx.client() as c2:
        ids = [p["id"] for p in (await c2.get("/v1/community.json")).json()["pieces"]]
    assert ids == ["bea/first-light"]  # bea untouched
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'account_deleted'"))["n"] == 1


async def test_cors_credentials_only_for_site_origins(ctx):
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        ok = await c.get("/v1/me", headers={"origin": SITE})
        assert ok.headers["access-control-allow-origin"] == SITE
        assert ok.headers["access-control-allow-credentials"] == "true"
        evil = await c.get("/v1/me", headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers
        pre = await c.options("/v1/me", headers={"origin": SITE, "access-control-request-method": "PATCH",
                                                 "access-control-request-headers": "x-tac-csrf"})
        assert pre.status_code == 204 and "x-tac-csrf" in pre.headers["access-control-allow-headers"]
        assert "PATCH" in pre.headers["access-control-allow-methods"]
        adm = await c.options("/v1/admin/queue", headers={"origin": SITE, "access-control-request-method": "GET"})
        assert "access-control-allow-origin" not in adm.headers  # admin never cross-origin


async def test_login_rate_limited(ctx):
    async with ctx.client(ip="10.3.3.3") as c:
        codes = [(await c.post("/v1/auth/web/login", data={"handle": "X!", "return": "/"})).status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429


async def test_login_csrf_cross_site_form_rejected(ctx):
    async with ctx.client() as c:
        r = await c.post("/v1/auth/web/login", data={"handle": "alex"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


async def test_github_mode_login(tmp_path, monkeypatch):
    from tac_platform import web_auth

    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            assert (await c.post("/v1/auth/web/login", data={"handle": "alex"})).status_code == 404  # no dev form
            r = await c.get("/v1/auth/web/login", params={"return": "/p/x"})
            assert r.status_code == 303 and r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
            q = parse_qs(urlsplit(r.headers["location"]).query)
            assert q["redirect_uri"] == ["http://127.0.0.1:8790/v1/auth/web/github/callback"]
            state = q["state"][0]

            async def fake_identity(st, code):
                assert code == "gh-code"
                return 4242, "alexgh"

            monkeypatch.setattr(web_auth, "github_identity", fake_identity)
            bad = await c.get("/v1/auth/web/github/callback", params={"code": "gh-code", "state": state + "x"})
            assert bad.status_code == 400
            ok = await c.get("/v1/auth/web/github/callback", params={"code": "gh-code", "state": state})
            assert ok.status_code == 303 and ok.headers["location"] == "/p/x"
            assert "tac_session=" in ok.headers["set-cookie"]
        row = await ctx.app.state.db.fetchone("SELECT handle, github_id FROM users")
        assert dict(row) == {"handle": "alexgh", "github_id": 4242}


async def test_admin_token_unrelated_to_session(ctx):
    c = await web_login(ctx, "alex")
    async with aclosing(c):
        assert (await c.get("/v1/admin/queue")).status_code == 401
        c.cookies.set("tac_admin", ADMIN)
        assert (await c.get("/v1/admin/queue")).status_code == 200
