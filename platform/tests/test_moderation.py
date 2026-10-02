import json
import os

from conftest import handle_slug
from tac_platform.publish import model_label
from tac_platform.sandbox import run_limited


async def _published(ctx, handle="alex"):
    token = await ctx.login(handle)
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return token, sub["id"], *handle_slug(st["preview_url"])


async def test_three_distinct_reports_hide_then_unhide_then_delete(ctx):
    token, sub_id, h, s = await _published(ctx)
    url = f"/v1/pieces/{h}/{s}/report"
    async with ctx.client(ip="1.1.1.1") as c:
        assert (await c.post(url, json={"reason": "spam"})).status_code == 202
        assert (await c.post(url, json={"reason": "spam again"})).status_code == 202  # same IP counts once
    async with ctx.client(ip="2.2.2.2") as c:
        assert (await c.post(url, json={"reason": "nsfw"})).status_code == 202
    async with ctx.client() as c:
        assert len((await c.get("/v1/community.json")).json()["pieces"]) == 1
    async with ctx.client(ip="3.3.3.3") as c:
        assert (await c.post(url, json={"reason": "ip"})).status_code == 202
        doc = (await c.get("/v1/community.json")).json()
        assert doc["pieces"] == [] and doc["totals"]["pieces"] == 0
        assert (await c.get(f"/media/{h}/{s}/preview.webp")).status_code == 404
        assert (await c.post(url, json={"reason": "x"})).status_code == 404  # already hidden

    async with ctx.admin() as a:
        q = (await a.get("/v1/admin/queue")).json()
        assert [len(i["reports"]) for i in q["hidden"]] == [3]
        assert (await a.post(f"/v1/admin/pieces/{h}/{s}/unhide")).status_code == 200
    async with ctx.client() as c:
        assert len((await c.get("/v1/community.json")).json()["pieces"]) == 1
        assert (await c.get(f"/media/{h}/{s}/preview.webp")).status_code == 200

    async with ctx.admin() as a:
        r = await a.post(f"/v1/admin/pieces/{h}/{s}/delete", json={"reason": "franchise IP"})
        assert r.status_code == 200
    async with ctx.client(authorization=f"Bearer {token}") as c:
        st = (await c.get(f"/v1/submissions/{sub_id}")).json()
        assert st["status"] == "rejected" and st["reasons"] == ["removed by moderator: franchise IP"]
        assert (await c.get(f"/media/{h}/{s}/preview.webp")).status_code == 404

    actions = [r["action"] for r in await ctx.app.state.db.fetchall(
        "SELECT action FROM audit_log WHERE submission_id = ? ORDER BY id", (sub_id,))]
    assert actions == ["submit", "claim", "automod", "pipeline_result", "publish",
                       "report", "report", "report", "report", "hide", "unhide", "delete"]


async def test_report_rate_limit_per_ip(ctx):
    _, _, h, s = await _published(ctx)
    async with ctx.client(ip="4.4.4.4") as c:
        codes = [(await c.post(f"/v1/pieces/{h}/{s}/report", json={"reason": "r"})).status_code for _ in range(6)]
    assert codes == [202] * 5 + [429]


async def test_admin_auth(ctx):
    async with ctx.client() as c:
        assert (await c.get("/v1/admin/queue")).status_code == 401
        assert (await c.get("/admin")).status_code == 401
        assert (await c.post("/v1/admin/submissions/x/approve")).status_code == 401
        assert (await c.get("/admin/login", params={"token": "wrong"})).status_code == 401
        r = await c.get("/admin/login", params={"token": "test-admin-token"})
        assert r.status_code == 303 and "samesite=strict" in r.headers["set-cookie"].lower()
        c.cookies.set("tac_admin", "test-admin-token")
        assert (await c.get("/admin")).status_code == 200


async def test_admin_reject_and_trust(ctx):
    token = await ctx.login("alex")
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/reject", json={"reason": "off brief"})).status_code == 200
        assert (await a.post("/v1/admin/users/alex/trust", json={"trusted": True})).json()["trusted"] is True
        assert (await a.post("/v1/admin/users/nobody/trust", json={"trusted": True})).status_code == 404
    st = await ctx.wait(token, sub["id"])
    assert st["status"] == "rejected" and st["reasons"] == ["off brief"]


async def test_sandbox_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("TAC_ADMIN_TOKEN", "x")
    monkeypatch.setenv("UV_CACHE_DIR", "/tmp/uvc")
    monkeypatch.setenv("UV_INDEX_URL", "https://user:pass@example.invalid/simple")
    res = await run_limited(
        ["python3", "-c", "import json, os, resource; print(json.dumps({'env': sorted(os.environ),"
         " 'nofile': resource.getrlimit(resource.RLIMIT_NOFILE)[0], 'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0],"
         " 'cwd': os.getcwd()}))"],
        tmp_path,
        10,
    )
    import json

    out = json.loads(res.stdout)
    assert "ANTHROPIC_API_KEY" not in out["env"] and "TAC_ADMIN_TOKEN" not in out["env"]
    assert not any(k.startswith("UV_") for k in out["env"])
    # LC_CTYPE (PEP 538 locale coercion) and __CF_USER_TEXT_ENCODING (macOS CoreFoundation) are added
    # by the child itself, not inherited
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
               "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert set(out["env"]) <= allowed
    assert out["nofile"] == 256 and out["cpu"] == 180
    assert os.path.realpath(out["cwd"]) == os.path.realpath(tmp_path)


def test_model_label():
    assert model_label("claude-opus-5-5") == "Claude Opus 5.5"
    assert model_label("claude-sonnet-5") == "Claude Sonnet 5"
    assert model_label("claude-fable-5-1") == "Claude Fable 5.1"
    assert model_label("something") == "something"


async def test_cancel_kills_child(tmp_path):
    import asyncio

    task = asyncio.create_task(run_limited(["python3", "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(60)"],
                                           tmp_path, 120))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    out = await asyncio.create_subprocess_exec("pgrep", "-f", "time.sleep\\(60\\)", stdout=asyncio.subprocess.PIPE)
    pids, _ = await out.communicate()
    assert pids.strip() == b""


async def test_admin_cookie_post_needs_csrf_header(ctx):
    token = await ctx.login("alex")
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])
    url = f"/v1/admin/submissions/{sub['id']}/reject"
    async with ctx.client() as c:
        c.cookies.set("tac_admin", "test-admin-token")
        # a cross-origin form/fetch from a sibling subdomain carries the cookie but not our header
        assert (await c.post(url, json={"reason": "x"})).status_code == 403
        r = await c.post(url, json={"reason": "x"}, headers={"x-tac-admin-csrf": "1", "sec-fetch-site": "same-site"})
        assert r.status_code == 403
        r = await c.post(url, json={"reason": "x"}, headers={"x-tac-admin-csrf": "1", "sec-fetch-site": "same-origin"})
        assert r.status_code == 200
        assert "x-tac-admin-csrf" in (await c.get("/admin")).text  # act() sends it


async def test_hide_never_wipes_concurrent_unhide(ctx):
    import asyncio

    _, sub_id, h, s = await _published(ctx)
    pub, store = ctx.app.state.publisher, ctx.app.state.store
    real_delete = store.delete_prefix

    async def slow_delete(prefix):  # widen the window between hide's DB write and its media delete
        await asyncio.sleep(0.2)
        await real_delete(prefix)

    store.delete_prefix = slow_delete
    hide = asyncio.create_task(pub.hide(h, s, "reports"))
    await asyncio.sleep(0.05)  # hide has committed hidden=1 and is now deleting media
    assert await pub.unhide(h, s, "admin")
    assert await hide
    row = await ctx.app.state.db.fetchone("SELECT hidden FROM submissions WHERE id = ?", (sub_id,))
    listed = [p["id"] for p in json.loads(await store.get("public/community.json"))["pieces"]]
    media = await store.list(f"public/{h}/{s}")
    assert row["hidden"] == 0 and listed == [f"{h}/{s}"]
    assert f"public/{h}/{s}/preview.webp" in media  # unhide's copy survived


async def test_house_artist_admin_only(ctx):
    token = await ctx.login("alex")
    from conftest import META

    sub = (await ctx.submit(token, meta={**META, "house_artist": True})).json()  # client claim: ignored
    await ctx.wait(token, sub["id"])
    assert "house_artist" not in json.loads(
        (await ctx.app.state.db.fetchone("SELECT meta_json FROM submissions"))["meta_json"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    async with ctx.client() as c:
        assert (await c.get("/v1/community.json")).json()["pieces"][0]["house_artist"] is False
        assert (await c.post("/v1/admin/users/alex/house", json={"house": True})).status_code == 401
    async with ctx.admin() as a:
        r = await a.post("/v1/admin/users/alex/house", json={"house": True})
        assert r.json() == {"handle": "alex", "house_artist": True}
        assert (await a.post("/v1/admin/users/nobody/house", json={"house": True})).status_code == 404
        assert "Unmark alex house artist" in (await a.get("/admin")).text
    async with ctx.client() as c:  # regenerated immediately for already-published pieces
        assert (await c.get("/v1/community.json")).json()["pieces"][0]["house_artist"] is True
    async with ctx.admin() as a:
        await a.post("/v1/admin/users/alex/house", json={"house": False})
    async with ctx.client() as c:
        assert (await c.get("/v1/community.json")).json()["pieces"][0]["house_artist"] is False


async def test_house_artist_migration_on_old_db(tmp_path):
    import aiosqlite

    from tac_platform.db import Database

    path = tmp_path / "old.sqlite3"
    async with aiosqlite.connect(path) as c:  # users table as created before the column existed
        await c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, handle TEXT NOT NULL UNIQUE, github_id INTEGER UNIQUE,"
                        " trusted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
        await c.execute("INSERT INTO users (handle, created_at) VALUES ('old', 'x')")
        await c.commit()
    db = Database(path)
    await db.open()
    try:
        row = await db.fetchone("SELECT house_artist FROM users WHERE handle = 'old'")
        assert row["house_artist"] == 0
    finally:
        await db.close()
