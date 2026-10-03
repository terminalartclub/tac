import json
from datetime import timedelta

from conftest import handle_slug
from tac_platform import views


async def _published(ctx, handle="alex"):
    token = await ctx.login(handle)
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return token, sub["id"], *handle_slug(st["preview_url"])


async def _mine(ctx, token):
    async with ctx.client(authorization=f"Bearer {token}") as c:
        r = await c.get("/v1/me/pieces")
    assert r.status_code == 200, r.text
    return r.json()


async def test_views_dedupe_per_ip_day_and_private(ctx):
    token, sub_id, h, s = await _published(ctx)
    url = f"/v1/pieces/{h}/{s}/view"
    for ip in ("1.1.1.1", "1.1.1.1", "2.2.2.2", "3.3.3.3", "3.3.3.3"):
        async with ctx.client(ip=ip) as c:
            r = await c.post(url)
            assert r.status_code == 204 and r.content == b"" and "set-cookie" not in r.headers
    mine = await _mine(ctx, token)
    p = mine["pieces"][0]
    assert (p["id"], p["slug"], p["status"]) == (f"{h}/{s}", s, "published")
    assert p["views_total"] == 3 and p["views_7d"] == 3
    assert len(p["views_28d"]) == 28 and p["views_28d"][-1] == 3 and sum(p["views_28d"][:-1]) == 0
    assert p["url"].endswith(f"/v1/submissions/{sub_id}")

    # no raw IPs anywhere; hashes differ per IP
    rows = await ctx.app.state.db.fetchall("SELECT * FROM views")
    assert len(rows) == 3 and not any("1.1.1.1" in json.dumps(dict(r)) for r in rows)

    async with ctx.client() as c:  # never public
        doc = (await c.get("/v1/community.json")).text
    assert "views" not in doc
    async with ctx.admin() as a:
        q = (await a.get("/v1/admin/queue")).json()
        assert q["published"][0]["views_7d"] == 3
        assert "3 views 7d" in (await a.get("/admin")).text


async def test_view_always_204_and_uncounted_when_hidden_or_unknown(ctx):
    token, sub_id, h, s = await _published(ctx)
    async with ctx.client(ip="5.5.5.5") as c:
        assert (await c.post("/v1/pieces/nobody/nothing/view")).status_code == 204
        await ctx.app.state.publisher.hide(h, s, "test")
        assert (await c.post(f"/v1/pieces/{h}/{s}/view")).status_code == 204
    assert (await _mine(ctx, token))["pieces"][0]["views_total"] == 0


async def test_view_rate_limit_silent(ctx):
    token, _, h, s = await _published(ctx)
    async with ctx.client(ip="6.6.6.6") as c:
        codes = {(await c.post(f"/v1/pieces/{h}/{s}/view")).status_code for _ in range(125)}
    assert codes == {204}
    events = await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM rate_events WHERE key LIKE 'view:%'")
    assert events["n"] == 120


async def test_view_cors_only_site_origins(ctx):
    _, _, h, s = await _published(ctx)
    url = f"/v1/pieces/{h}/{s}/view"
    async with ctx.client() as c:
        ok = await c.post(url, headers={"origin": "http://localhost:5181"})
        assert ok.headers["access-control-allow-origin"] == "http://localhost:5181"
        prod_site = await c.post(url, headers={"origin": "https://terminalart.club"})
        assert "access-control-allow-origin" not in prod_site.headers  # dev default: the local site only
        evil = await c.post(url, headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers
        pre = await c.options(url, headers={"origin": "http://localhost:5181", "access-control-request-method": "POST"})
        assert pre.status_code == 204 and "POST" in pre.headers["access-control-allow-methods"]


async def test_me_pieces_owner_only_and_series(ctx):
    token, sub_id, h, s = await _published(ctx)
    other = await ctx.login("bea")
    assert (await _mine(ctx, other))["pieces"] == []
    async with ctx.client() as c:
        assert (await c.get("/v1/me/pieces")).status_code == 401
    today = views.utc_today()
    db = ctx.app.state.db
    for back, n in ((0, 2), (6, 5), (7, 11), (27, 1), (40, 100)):
        await db.execute("INSERT INTO view_days (submission_id, day, views) VALUES (?, ?, ?)",
                         (sub_id, (today - timedelta(days=back)).isoformat(), n))
    p = (await _mine(ctx, token))["pieces"][0]
    assert p["views_total"] == 119 and p["views_7d"] == 7
    assert p["views_28d"][0] == 1 and p["views_28d"][-7] == 5 and p["views_28d"][-8] == 11 and p["views_28d"][-1] == 2


async def test_purge_keeps_rollups_and_drops_old_salts(ctx):
    db = ctx.app.state.db
    today = views.utc_today()
    old, recent = (today - timedelta(days=31)).isoformat(), (today - timedelta(days=29)).isoformat()
    for day in (old, recent):
        await db.execute("INSERT INTO views VALUES ('x', ?, 'h')", (day,))
        await db.execute("INSERT INTO view_days VALUES ('x', ?, 1)", (day,))
        await views.day_salt(db, day)
    await views.day_salt(db, today.isoformat())
    await views.purge(db)
    assert [r["day"] for r in await db.fetchall("SELECT day FROM views")] == [recent]
    assert len(await db.fetchall("SELECT * FROM view_days")) == 2
    salts = [r["key"] for r in await db.fetchall("SELECT key FROM kv WHERE key LIKE 'view_salt:%'")]
    assert salts == [f"view_salt:{today.isoformat()}"]


async def test_me_pieces_contract_shape(ctx):
    """Pinned with the plugin client (tac-studio f5bacdf). Change only together with the plugin."""
    token, sub_id, h, s = await _published(ctx)
    body = await _mine(ctx, token)
    assert set(body) == {"pieces"}
    (p,) = body["pieces"]
    assert set(p) == {"id", "slug", "title", "status", "views_total", "views_7d", "views_28d", "url",
                      "critique", "reasons"}
    assert p["critique"] is None or isinstance(p["critique"], str)
    assert isinstance(p["reasons"], list) and all(isinstance(x, str) for x in p["reasons"])
    assert p["id"] == f"{h}/{s}" and p["slug"] == s and isinstance(p["title"], str)
    assert p["status"] in {"queued", "rendering", "rejected", "in_review", "published", "hidden"}
    assert type(p["views_total"]) is int and type(p["views_7d"]) is int
    assert len(p["views_28d"]) == 28 and all(type(v) is int for v in p["views_28d"])
    assert p["url"].startswith("http")
    await ctx.app.state.publisher.hide(h, s, "test")
    assert (await _mine(ctx, token))["pieces"][0]["status"] == "hidden"
