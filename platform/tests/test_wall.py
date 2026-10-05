"""/tac:wall: frames made at render time, served as data to the plugin. Never code."""

import hashlib
import json

import pytest

from tac_platform import wallframes
from tac_platform.wall import week_start

from conftest import META, PIECE, handle_slug

CSRF = {"x-tac-admin-csrf": "1", "sec-fetch-site": "same-origin"}


async def _published(ctx, handle="alex", piece=PIECE, title="First Light", token=None):
    token = token or await ctx.login(handle)
    sub = (await ctx.submit(token, piece=piece, meta={**META, "title": title})).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return token, sub["id"], *handle_slug(st["preview_url"])


async def test_published_pieces_are_on_this_weeks_wall_with_their_frames(ctx):
    _, sub_id, h, s = await _published(ctx)
    async with ctx.client() as c:
        r = await c.get("/v1/wall.json")
        assert r.status_code == 200 and r.headers["cache-control"].startswith("public, max-age=300")
        wall = r.json()
        assert wall["source"] == "week" and [(p["handle"], p["slug"]) for p in wall["pieces"]] == [(h, s)]
        p = wall["pieces"][0]
        assert p["frames"] == {"bytes": p["frames"]["bytes"], "cols": 8, "rows": 4, "fps": 10, "frames": 3,
                               "loop_ms": 300}
        assert p["model"] == "claude-opus-5-5" and p["title"] == "First Light"
        f = await c.get(f"/v1/pieces/{h}/{s}/frames")
        assert f.status_code == 200 and f.headers["content-type"] == "application/gzip"
        assert "content-encoding" not in f.headers  # the client inflates, with its own cap
        assert f.headers["etag"] == p["etag"] == '"' + hashlib.sha256(f.content).hexdigest()[:32] + '"'
        head, words = wallframes.load(f.content)
        firsts = [chr(fr[0]) for fr in wallframes.frames(head, words)]
        assert firsts == ["0", "1", "2"]
        again = await c.get(f"/v1/pieces/{h}/{s}/frames", headers={"if-none-match": p["etag"]})
        assert again.status_code == 304 and again.content == b""
        w2 = await c.get("/v1/wall.json", headers={"if-none-match": r.headers["etag"]})
        assert w2.status_code == 304


@pytest.mark.parametrize("marker", ["TEST:no-frames", "TEST:bad-frames", "TEST:bomb-frames"])
async def test_a_piece_without_good_frames_still_publishes_but_is_not_on_the_wall(ctx, marker):
    _, sub_id, h, s = await _published(ctx, piece=PIECE + f"# {marker}\n".encode())
    row = await ctx.app.state.db.fetchone("SELECT frames_json FROM submissions WHERE id = ?", (sub_id,))
    assert row["frames_json"] is None
    async with ctx.client() as c:
        assert (await c.get("/v1/wall.json")).json()["pieces"] == []
        assert (await c.get(f"/v1/pieces/{h}/{s}/frames")).status_code == 404
    assert await ctx.app.state.store.get(f"public/{h}/{s}/frames.cells.gz") is None


async def test_hidden_and_suspended_pieces_leave_the_wall_and_their_frames_404(ctx):
    _, _, h, s = await _published(ctx)
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/pieces/{h}/{s}/hide", json={"reason": "DMCA"})).status_code == 200
    async with ctx.client() as c:
        assert (await c.get("/v1/wall.json")).json()["pieces"] == []
        assert (await c.get(f"/v1/pieces/{h}/{s}/frames")).status_code == 404
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/pieces/{h}/{s}/unhide")).status_code == 200
    async with ctx.client() as c:
        assert (await c.get(f"/v1/pieces/{h}/{s}/frames")).status_code == 200  # back, frames copied again
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "spam"})).status_code == 200
    async with ctx.client() as c:
        assert (await c.get("/v1/wall.json")).json()["pieces"] == []
        assert (await c.get(f"/v1/pieces/{h}/{s}/frames")).status_code == 404


async def test_no_pieces_this_week_means_the_picks(ctx):
    _, sub_old, h1, s1 = await _published(ctx, title="Old One")
    token = await ctx.login("bea")
    _, sub_new, h2, s2 = await _published(ctx, handle="bea", token=token, title="New One")
    db = ctx.app.state.db
    await db.execute("UPDATE submissions SET published_at = '2026-01-05T10:00:00+00:00'")  # nothing this week
    await db.execute("UPDATE submissions SET pick = 1 WHERE id = ?", (sub_old,))
    async with ctx.client() as c:
        wall = (await c.get("/v1/wall.json")).json()
        assert wall["source"] == "picks" and [p["slug"] for p in wall["pieces"]] == [s1]
    await db.execute("UPDATE submissions SET published_at = ? WHERE id = ?", (week_start(), sub_new))
    async with ctx.client() as c:
        assert [p["slug"] for p in (await c.get("/v1/wall.json")).json()["pieces"]] == [s2]  # the week
        forced = (await c.get("/v1/wall.json?picks=1")).json()
        assert forced["source"] == "picks" and [p["slug"] for p in forced["pieces"]] == [s1]


async def test_the_wall_orders_newest_first(ctx):
    token = await ctx.login("alex")
    slugs = []
    for t in ("One", "Two", "Three"):
        slugs.append((await _published(ctx, token=token, title=t))[3])
    async with ctx.client() as c:
        assert [p["slug"] for p in (await c.get("/v1/wall.json")).json()["pieces"]] == slugs[::-1]


@pytest.mark.parametrize("path", ["/v1/pieces/Alex/first-light/frames", "/v1/pieces/alex/..%2Fx/frames",
                                  "/v1/pieces/alex/a--b/frames", "/v1/pieces/a/first-light/frames"])
async def test_frames_refuse_odd_names(ctx, path):
    async with ctx.client() as c:
        assert (await c.get(path)).status_code == 404


async def test_frames_and_playlist_are_rate_limited_per_client(ctx, monkeypatch):
    from tac_platform import wall

    monkeypatch.setattr(wall, "PLAYLIST_PER_HOUR", 3)
    async with ctx.client(ip="10.1.1.1") as c:
        codes = [(await c.get("/v1/wall.json")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    async with ctx.client(ip="10.1.1.2") as c:  # another client: its own budget
        assert (await c.get("/v1/wall.json")).status_code == 200


async def test_backfill_needs_admin_and_csrf_is_audited_and_makes_frames(ctx):
    _, sub_id, h, s = await _published(ctx)
    db = ctx.app.state.db
    await db.execute("UPDATE submissions SET frames_json = NULL WHERE id = ?", (sub_id,))
    await ctx.app.state.store.delete_prefix(f"public/{h}/{s}/frames.cells.gz")
    async with ctx.client() as c:  # no admin
        assert (await c.post("/v1/admin/wall/backfill", json={})).status_code in (401, 403)
        c.cookies.set("tac_admin", "test-admin-token")  # a cookie without the CSRF header
        assert (await c.post("/v1/admin/wall/backfill", json={})).status_code == 403
    async with ctx.admin() as a:
        r = await a.post("/v1/admin/wall/backfill", json={})
        assert r.status_code == 202 and r.json() == {"queued": 1, "force": False}
    await ctx.app.state.wall_backfill
    rows = await db.fetchall("SELECT actor, action, detail FROM audit_log WHERE action LIKE 'wall_backfill%' ORDER BY id")
    assert [(r["actor"], r["action"]) for r in rows] == [("admin", "wall_backfill"), ("system", "wall_backfill_done")]
    assert rows[1]["detail"] == "1 rendered, 0 failed, of 1"
    info = json.loads((await db.fetchone("SELECT frames_json FROM submissions WHERE id = ?", (sub_id,)))["frames_json"])
    assert info["frames"] == 3
    async with ctx.client() as c:
        assert (await c.get(f"/v1/pieces/{h}/{s}/frames")).status_code == 200


async def test_the_platform_and_plugin_copies_of_the_format_are_identical():
    from pathlib import Path

    here = Path(__file__).resolve().parents[1] / "src" / "tac_platform" / "wallframes.py"
    plugin = Path(__file__).resolve().parents[2] / "plugins" / "tac-studio" / "lib" / "wallframes.py"
    assert here.read_bytes() == plugin.read_bytes()
