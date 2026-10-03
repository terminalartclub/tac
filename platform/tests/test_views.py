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

    async with ctx.client() as c:  # public only as aggregates, after the next regen
        stale = (await c.get("/v1/community.json")).json()
        assert stale["pieces"][0]["views"] == 0  # never regenerated per view
        await ctx.app.state.publisher.regenerate()
        doc = (await c.get("/v1/community.json")).json()
    assert doc["pieces"][0]["views"] == 3 and doc["artists"] == {h: {"views": 3}} and doc["week"]["views"] == 3
    assert "ip_day_hash" not in json.dumps(doc) and "views_28d" not in json.dumps(doc)
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


async def test_community_json_views_fields(ctx):
    token, sub_a, h, s_a = await _published(ctx, "alex")
    sub_b = (await ctx.submit(token)).json()["id"]  # same title -> slug "...-2"
    await ctx.wait(token, sub_b)
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub_b}/approve")).status_code == 200
    _, s_b = handle_slug((await ctx.wait(token, sub_b, until=("published",)))["preview_url"])
    _, sub_c, h2, s_c = await _published(ctx, "bea")
    db, today = ctx.app.state.db, views.utc_today()
    monday = today - timedelta(days=today.weekday())
    rows = [(sub_a, today, 5), (sub_a, monday, 2), (sub_a, monday - timedelta(days=1), 100),  # last week
            (sub_b, today - timedelta(days=60), 7), (sub_c, today, 1)]
    for sid, day, n in rows:
        await db.execute("INSERT INTO view_days (submission_id, day, views) VALUES (?, ?, ?)"
                         " ON CONFLICT(submission_id, day) DO UPDATE SET views = views + excluded.views",
                         (sid, day.isoformat(), n))
    doc = await ctx.app.state.publisher.regenerate()
    by_id = {p["id"]: p["views"] for p in doc["pieces"]}
    a_total = 107  # (on a Monday, today and monday are one row of 7)
    assert by_id == {f"{h}/{s_a}": a_total, f"{h}/{s_b}": 7, f"{h2}/{s_c}": 1}
    assert all(type(v) is int for v in by_id.values())
    assert doc["artists"] == {h: {"views": a_total + 7}, h2: {"views": 1}}
    # current ISO week (Mon 00:00 UTC -> now): today + Monday, never last Sunday or 60 days ago
    assert doc["week"]["views"] == 5 + 2 + 1
    assert doc["week"]["label"] == f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
    # a hidden piece drops out of every public aggregate
    await ctx.app.state.publisher.hide(h2, s_c, "test")
    doc = json.loads(await ctx.app.state.store.get("public/community.json"))
    assert h2 not in doc["artists"] and doc["week"]["views"] == 7


async def test_regen_loop_runs_hourly_under_the_lock(ctx, monkeypatch):
    import asyncio

    from tac_platform import publish

    pub = ctx.app.state.publisher
    held: list[bool] = []
    real_build = pub.build

    async def build():
        held.append(pub._regen_lock.locked())
        return await real_build()

    monkeypatch.setattr(pub, "build", build)
    assert publish.REGEN_EVERY_S == 3600
    task = asyncio.create_task(publish.regen_loop(pub, every_s=0.01))
    for _ in range(200):
        if len(held) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(held) >= 2 and all(held)
    assert any(t.get_name() == "tac-community-regen" for t in asyncio.all_tasks())  # started by the app


async def test_events_allowlist_and_counts(ctx):
    db = ctx.app.state.db
    async with ctx.client(ip="7.7.7.7") as c:
        for name in ("piece_share", "install_copy", "install_send", "install_copy"):
            r = await c.post("/v1/events", content=json.dumps({"name": name}),
                             headers={"content-type": "text/plain;charset=UTF-8"})  # sendBeacon shape
            assert r.status_code == 204 and r.content == b""
        for bad in (b'{"name": "page_view"}', b'{"name": ["piece_share"]}', b"[]", b"not json", b"",
                    b'{"name": "piece_share\\u0000"}'):
            r = await c.post("/v1/events", content=bad)
            assert r.status_code == 400 and r.json()["error"] == "unknown_event"
    rows = {r["name"]: r["n"] for r in await db.fetchall("SELECT name, n FROM event_days")}
    assert rows == {"piece_share": 1, "install_copy": 2, "install_send": 1}
    stored = json.dumps([dict(r) for r in await db.fetchall("SELECT * FROM event_days")])
    assert "7.7.7.7" not in stored


async def test_events_rate_limited_silently(ctx):
    from tac_platform import events

    async with ctx.client(ip="8.8.8.8") as c:
        codes = {(await c.post("/v1/events", json={"name": "piece_share"})).status_code
                 for _ in range(events.EVENTS_PER_HOUR + 5)}
    assert codes == {204}
    n = (await ctx.app.state.db.fetchone("SELECT n FROM event_days WHERE name = 'piece_share'"))["n"]
    assert n == events.EVENTS_PER_HOUR


async def test_events_cors_only_site_origins(ctx):
    async with ctx.client() as c:
        ok = await c.post("/v1/events", json={"name": "install_copy"}, headers={"origin": "http://localhost:5181"})
        assert ok.headers["access-control-allow-origin"] == "http://localhost:5181"
        evil = await c.post("/v1/events", json={"name": "install_copy"}, headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers


def test_ip_bucket_folds_ipv6_to_64_and_keeps_ipv4():
    from tac_platform.web import ip_bucket

    assert ip_bucket("203.0.113.7") == "203.0.113.7"
    assert ip_bucket("2001:db8:1:2:aaaa::1") == ip_bucket("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert ip_bucket("2001:db8:1:3::1") == "2001:db8:1:3::/64"  # next /64 is a different client
    assert ip_bucket("::ffff:203.0.113.7") == "203.0.113.7"
    assert ip_bucket("fe80::1%en0") == "fe80::/64"
    assert ip_bucket("unknown") == "unknown"


async def test_views_count_one_viewer_per_ipv6_64(ctx):
    token, _, h, s = await _published(ctx)
    for ip in ("2001:db8:1:2::1", "2001:db8:1:2::2", "2001:db8:1:2:dead:beef:0:9"):  # rotation inside one /64
        async with ctx.client(ip=ip) as c:
            assert (await c.post(f"/v1/pieces/{h}/{s}/view")).status_code == 204
    assert (await _mine(ctx, token))["pieces"][0]["views_total"] == 1
    for ip in ("2001:db8:1:3::1", "198.51.100.1", "198.51.100.2"):  # another /64, two IPv4s: each counts
        async with ctx.client(ip=ip) as c:
            await c.post(f"/v1/pieces/{h}/{s}/view")
    assert (await _mine(ctx, token))["pieces"][0]["views_total"] == 4


async def test_events_rate_limit_shared_across_ipv6_64(ctx):
    from tac_platform import events

    codes = set()
    for i in range(events.EVENTS_PER_HOUR + 5):
        async with ctx.client(ip=f"2001:db8:9:9::{i + 1:x}") as c:
            codes.add((await c.post("/v1/events", json={"name": "install_copy"})).status_code)
    assert codes == {204}
    n = (await ctx.app.state.db.fetchone("SELECT n FROM event_days WHERE name = 'install_copy'"))["n"]
    assert n == events.EVENTS_PER_HOUR  # one /64 = one rate bucket, however many addresses

