"""Takedown tools: hide now, suspend/unsuspend, the takedown log, and the Instagram reminder."""

import html as htmlmod
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import handle_slug, make_ctx
from tac_platform import auth, web_auth
from tac_platform.web import sha256_hex

CSRF = {"x-tac-admin-csrf": "1", "sec-fetch-site": "same-origin"}


async def _published(ctx, handle="alex", token=None, title="First Light"):
    from conftest import META

    token = token or await ctx.login(handle)
    sub = (await ctx.submit(token, meta={**META, "title": title})).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return token, sub["id"], *handle_slug(st["preview_url"])


async def _public_ids(ctx) -> list[str]:
    async with ctx.client() as c:
        return [p["id"] for p in (await c.get("/v1/community.json")).json()["pieces"]]


async def _audit(ctx, action):
    return await ctx.app.state.db.fetchall(
        "SELECT actor, detail, target, submission_id FROM audit_log WHERE action = ? ORDER BY id", (action,))


# ---------------------------------------------------------------- hide now


async def test_hide_now_takes_piece_down_everywhere_and_unhide_restores(ctx):
    _, sub_id, h, s = await _published(ctx)
    async with ctx.client() as c:
        assert (await c.get(f"/media/{h}/{s}/share.jpg")).status_code == 200
    async with ctx.admin() as a:
        url = f"/v1/admin/pieces/{h}/{s}/hide"
        assert (await a.post(url)).status_code == 400  # reason is required
        assert (await a.post(url, json={"reason": "   "})).status_code == 400
        assert (await a.post(url, json={"reason": "x" * 201})).status_code == 400
        r = await a.post(url, json={"reason": " DMCA notice 2026-10-03 "})
        assert r.status_code == 200 and r.json() == {"id": f"{h}/{s}", "hidden": True, "reminder": None}
        assert (await a.post(url, json={"reason": "again"})).status_code == 409  # already hidden
        assert (await a.post(f"/v1/admin/pieces/{h}/nope/hide", json={"reason": "x"})).status_code == 409
    assert await _public_ids(ctx) == []
    async with ctx.client() as c:
        for name in ("preview.webp", "og.jpg", "share.jpg", "card.jpg", "piece.py"):
            assert (await c.get(f"/media/{h}/{s}/{name}")).status_code == 404, name
        assert (await c.post(f"/v1/pieces/{h}/{s}/report", json={"reason": "x"})).status_code == 404
    hides = await _audit(ctx, "hide")
    assert [(r["actor"], r["detail"], r["target"], r["submission_id"]) for r in hides] == [
        ("admin", "DMCA notice 2026-10-03", f"{h}/{s}", sub_id)]
    async with ctx.admin() as a:
        q = (await a.get("/v1/admin/queue")).json()
        assert [i["hidden_by"] for i in q["hidden"]] == ["admin: DMCA notice 2026-10-03"]
        assert (await a.post(f"/v1/admin/pieces/{h}/{s}/unhide")).status_code == 200
    assert await _public_ids(ctx) == [f"{h}/{s}"]


async def test_hide_needs_admin_and_cookie_csrf(ctx):
    _, _, h, s = await _published(ctx)
    url = f"/v1/admin/pieces/{h}/{s}/hide"
    async with ctx.client() as c:
        assert (await c.post(url, json={"reason": "x"})).status_code == 401
        c.cookies.set("tac_admin", "test-admin-token")
        assert (await c.post(url, json={"reason": "x"})).status_code == 403  # no CSRF header
        r = await c.post(url, json={"reason": "x"}, headers={**CSRF, "sec-fetch-site": "same-site"})
        assert r.status_code == 403
        assert (await c.post(url, json={"reason": "x"}, headers=CSRF)).status_code == 200
    for path in ("/v1/admin/users/alex/suspend", "/v1/admin/users/alex/unsuspend",
                 f"/v1/admin/pieces/{h}/{s}/instagram-posted", "/v1/admin/takedowns"):
        async with ctx.client() as c:
            r = await (c.get(path) if path.endswith("takedowns") else c.post(path, json={"reason": "x", "posted": True}))
            assert r.status_code == 401, path


async def test_delete_reason_is_validated_like_hide(ctx):
    token, sub_id, h, s = await _published(ctx)
    url = f"/v1/admin/pieces/{h}/{s}/delete"
    async with ctx.admin() as a:
        for bad in (None, {}, {"reason": "  "}, {"reason": "x" * 201}):
            r = await (a.post(url) if bad is None else a.post(url, json=bad))
            assert r.status_code == 400, bad
        page = (await a.get("/admin")).text
        assert re.search(rf"data-act='/v1/admin/pieces/{h}/{s}/delete' data-prompt='[^']*SHOWN TO THE ARTIST", page)
        assert (await a.post(url, json={"reason": " DMCA-2026-002 "})).status_code == 200
    async with ctx.client(authorization=f"Bearer {token}") as c:
        assert (await c.get(f"/v1/submissions/{sub_id}")).json()["reasons"] == ["removed by moderator: DMCA-2026-002"]


# ---------------------------------------------------------------- suspend


async def _web_session(ctx, handle) -> dict:
    async with ctx.client() as c:
        r = await c.post("/v1/auth/web/login", data={"handle": handle, "agree": "1"})
        assert r.status_code == 303, r.text
        return dict(c.cookies)


async def test_suspend_revokes_everything_hides_everything_and_blocks_sign_in(ctx):
    token, sub_a, h, s1 = await _published(ctx)
    _, _, _, s2 = await _published(ctx, token=token, title="Second Light")
    cookies = await _web_session(ctx, "alex")
    other, _, oh, os_ = await _published(ctx, handle="sam")
    # a pending submission must not publish while suspended
    from conftest import META

    pending = (await ctx.submit(token, meta={**META, "title": "Third"})).json()
    await ctx.wait(token, pending["id"])
    assert sorted(await _public_ids(ctx)) == sorted([f"{h}/{s1}", f"{h}/{s2}", f"{oh}/{os_}"])

    async with ctx.admin() as a:
        assert (await a.post("/v1/admin/users/alex/suspend", json={})).status_code == 400  # reason required
        assert (await a.post("/v1/admin/users/nobody/suspend", json={"reason": "x"})).status_code == 404
        r = await a.post("/v1/admin/users/alex/suspend", json={"reason": "third copyright strike"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["suspended"] is True and body["revoked_tokens"] == 1 and body["revoked_sessions"] == 1
        assert sorted(body["hidden"]) == sorted([f"{h}/{s1}", f"{h}/{s2}"]) and body["reminder"] is None
        assert body["already_suspended"] is False and body["media_failed"] == []
        assert sorted(body["media_removed"]) == sorted([f"{h}/{s1}", f"{h}/{s2}"])
        again = (await a.post("/v1/admin/users/alex/suspend", json={"reason": "x"})).json()  # idempotent re-sweep
        assert again["already_suspended"] is True and again["hidden"] == [] and again["media_removed"] == []

    assert await _public_ids(ctx) == [f"{oh}/{os_}"]  # other artists untouched
    db = ctx.app.state.db
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM access_tokens t JOIN users u ON u.id = t.user_id"
                              " WHERE u.handle = 'alex'"))["n"] == 0
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM web_sessions"))["n"] == 0
    assert [(r["detail"], r["target"]) for r in await _audit(ctx, "suspend")] == [("third copyright strike", "alex")]
    assert sorted(r["target"] for r in await _audit(ctx, "hide") if r["detail"] == "account suspended") == sorted(
        [f"{h}/{s1}", f"{h}/{s2}"])

    # old credentials are dead
    async with ctx.client(authorization=f"Bearer {token}") as b:
        assert (await b.get("/v1/me")).status_code == 401
    async with ctx.client() as c:
        c.cookies.update(cookies)
        assert (await c.get("/v1/me")).status_code == 401
    # a credential that slipped through anyway is refused as suspended, on the API and on submit
    leaked = secrets.token_urlsafe(32)
    uid = (await db.fetchone("SELECT id FROM users WHERE handle = 'alex'"))["id"]
    await db.execute("INSERT INTO access_tokens (token_sha256, user_id, created_at) VALUES (?, ?, 'x')",
                     (sha256_hex(leaked), uid))
    r = await ctx.submit(leaked)
    assert r.status_code == 403 and r.json()["error"] == "suspended" and "suspended" in r.json()["detail"]
    async with ctx.client(authorization=f"Bearer {leaked}") as b:
        assert (await b.get("/v1/me")).json()["error"] == "suspended"
    # web sign-in refuses with a clear message; the moderator's reason is not shown
    async with ctx.client() as c:
        r = await c.post("/v1/auth/web/login", data={"handle": "alex", "agree": "1"})
        assert r.status_code == 403 and "This account is suspended" in r.text and "strike" not in r.text
        assert not c.cookies.get("tac_session")
    # an approved-but-unpolled device code can't mint a token
    dc = secrets.token_urlsafe(32)
    await db.execute("INSERT INTO device_codes (device_code_sha256, user_code, status, user_id, expires_at, created_at)"
                     " VALUES (?, 'BBBB-CCCC', 'approved', ?, ?, 'x')", (sha256_hex(dc), uid, time.time() + 600))
    async with ctx.client() as c:
        r = await c.post("/v1/auth/token", json={"device_code": dc})
        assert r.status_code == 403 and r.json()["error"] == "suspended"
    # nothing of theirs goes back up: approve and unhide refuse, trusted auto-publish refuses
    async with ctx.admin() as a:
        r = await a.post(f"/v1/admin/submissions/{pending['id']}/approve")
        assert r.status_code == 409 and r.json()["error"] == "user_suspended"
        r = await a.post(f"/v1/admin/pieces/{h}/{s1}/unhide")
        assert r.status_code == 409 and r.json()["error"] == "user_suspended"
    assert not await ctx.app.state.publisher.publish(pending["id"], "system:trusted")
    assert await _public_ids(ctx) == [f"{oh}/{os_}"]

    async with ctx.admin() as a:
        page = (await a.get("/admin")).text
        assert "<span class=chip>suspended</span>" in page and "third copyright strike" in page
        assert "Unsuspend alex" in page and "unsuspend to unhide" in page
        assert (await a.post("/v1/admin/users/alex/unsuspend", json={})).status_code == 400
        r = await a.post("/v1/admin/users/alex/unsuspend", json={"reason": "appeal accepted"})
        assert r.json() == {"handle": "alex", "suspended": False}
        assert (await a.post("/v1/admin/users/alex/unsuspend", json={"reason": "x"})).status_code == 409
    assert [(r["detail"], r["target"]) for r in await _audit(ctx, "unsuspend")] == [("appeal accepted", "alex")]
    assert await _public_ids(ctx) == [f"{oh}/{os_}"]  # unsuspend does NOT unhide
    await _web_session(ctx, "alex")  # sign-in works again
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/pieces/{h}/{s1}/unhide")).status_code == 200  # one by one
    assert sorted(await _public_ids(ctx)) == sorted([f"{h}/{s1}", f"{oh}/{os_}"])


async def test_sign_in_refusal_holds_inside_the_write_transaction(tmp_path, monkeypatch):
    """A suspend that lands between a sign-in's early check and its write still wins: create_session and
    the device approval re-check suspended_at in the same transaction as their INSERT/UPDATE."""
    async def fake_identity(st, code):
        return 4242, "alexgh"

    async def never_suspended(db, user_id):  # the early check is stale (the suspend landed right after it)
        return False

    monkeypatch.setattr(web_auth, "github_identity", fake_identity)
    monkeypatch.setattr(auth, "github_identity", fake_identity)
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:  # first sign-in creates the user (and accepts the terms)
            r = await c.get("/v1/auth/web/login")
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            step = await c.get("/v1/auth/web/github/callback", params={"code": "gh", "state": state})
            tok = re.search(r"name=token value='([^']+)'", step.text).group(1)
            assert (await c.post("/v1/auth/web/terms", data={"token": tok, "agree": "1"})).status_code == 303
        async with ctx.admin() as a:
            assert (await a.post("/v1/admin/users/alexgh/suspend", json={"reason": "x"})).status_code == 200

        async def web_callback(c):
            r = await c.get("/v1/auth/web/login")
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            return await c.get("/v1/auth/web/github/callback", params={"code": "gh", "state": state})

        async def device_callback(c):
            d = (await c.post("/v1/auth/device", json={})).json()
            start = await c.post("/device/github", data={"user_code": d["user_code"]})
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            c.cookies.set("tac_gh_nonce", state.split(".")[1])
            return d, await c.get("/device/github/callback", params={"code": "gh", "state": state})

        async with ctx.client() as c:  # the early checks refuse with the message
            r = await web_callback(c)
            assert r.status_code == 403 and "This account is suspended" in r.text and not c.cookies.get("tac_session")
            d, r = await device_callback(c)
            assert r.status_code == 403 and "This account is suspended" in r.text
        monkeypatch.setattr(web_auth, "is_suspended", never_suspended)
        monkeypatch.setattr(auth, "is_suspended", never_suspended)
        async with ctx.client() as c:  # the in-transaction guards refuse even when the early check passed
            r = await web_callback(c)
            assert r.status_code == 403 and not c.cookies.get("tac_session")
            d, _ = await device_callback(c)
            row = await ctx.app.state.db.fetchone("SELECT status FROM device_codes WHERE user_code = ?",
                                                  (d["user_code"],))
            assert row["status"] == "pending"  # never approved
        assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM web_sessions"))["n"] == 0


async def test_suspend_is_one_transaction_and_a_retry_finishes_a_failed_media_sweep(ctx):
    token, _, h, s1 = await _published(ctx)
    _, _, _, s2 = await _published(ctx, token=token, title="Second Light")
    pub, store, db = ctx.app.state.publisher, ctx.app.state.store, ctx.app.state.db
    real_delete, real_regen = store.delete_prefix, pub.regenerate
    regens = []

    async def flaky_delete(prefix):  # the store dies after the first piece
        if prefix == f"public/{h}/{s2}":
            raise OSError("store unavailable")
        await real_delete(prefix)

    async def counting_regen():
        regens.append(1)
        return await real_regen()

    store.delete_prefix, pub.regenerate = flaky_delete, counting_regen
    async with ctx.admin() as a:
        r = (await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "DMCA-2026-001"})).json()
    assert r["media_removed"] == [f"{h}/{s1}"] and r["media_failed"] == [f"{h}/{s2}"]
    assert len(regens) == 1  # once for the whole sweep, not per piece
    # the DB side is complete despite the media failure: both hidden, each with its own audit row
    rows = await db.fetchall("SELECT hidden FROM submissions WHERE status = 'published'")
    assert [x["hidden"] for x in rows] == [1, 1]
    assert sorted(x["target"] for x in await _audit(ctx, "hide")) == sorted([f"{h}/{s1}", f"{h}/{s2}"])
    assert await _public_ids(ctx) == []
    async with ctx.client() as c:
        assert (await c.get(f"/media/{h}/{s2}/preview.webp")).status_code == 200  # the leftover

    store.delete_prefix = real_delete  # store is back; the moderator clicks Re-sweep
    async with ctx.admin() as a:
        page = (await a.get("/admin")).text
        btn = re.search(r"<button type=button data-act='([^']*)'>Re-sweep</button>", page)
        assert btn and btn.group(1) == f"/v1/admin/users/{h}/resweep"
        r = (await a.post(btn.group(1))).json()
    assert r["already_suspended"] is True and r["hidden"] == []
    assert r["media_removed"] == [f"{h}/{s2}"] and r["media_failed"] == []
    async with ctx.client() as c:
        assert (await c.get(f"/media/{h}/{s2}/preview.webp")).status_code == 404
    assert [x["detail"] for x in await _audit(ctx, "suspend")] == ["DMCA-2026-001"]  # one suspend row, first reason
    assert (await db.fetchone("SELECT suspended_reason FROM users WHERE handle = ?", (h,)))[0] == "DMCA-2026-001"
    async with ctx.admin() as a:  # a repeated /suspend with another reason also keeps the first
        assert (await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "OTHER-1"})).json()["already_suspended"]
    assert (await db.fetchone("SELECT suspended_reason FROM users WHERE handle = ?", (h,)))[0] == "DMCA-2026-001"
    assert [x["detail"] for x in await _audit(ctx, "suspend")] == ["DMCA-2026-001"]


async def test_resweep_from_a_stale_page_never_re_suspends(ctx):
    _, _, h, s = await _published(ctx)
    db = ctx.app.state.db
    async with ctx.admin() as a:
        await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "DMCA-2026-006"})
        stale = (await a.get("/admin")).text  # the moderator's tab, before someone else unsuspends
        assert f"data-act='/v1/admin/users/{h}/resweep'>Re-sweep<" in stale
        await a.post(f"/v1/admin/users/{h}/unsuspend", json={"reason": "APPEAL-2026-002"})
        r = await a.post(f"/v1/admin/users/{h}/resweep")
        assert r.status_code == 409 and r.json()["error"] == "not_suspended"
        assert (await a.post("/v1/admin/users/nobody/resweep")).status_code == 404
    row = await db.fetchone("SELECT suspended_at, suspended_reason FROM users WHERE handle = ?", (h,))
    assert tuple(row) == (None, None)
    assert [x["detail"] for x in await _audit(ctx, "suspend")] == ["DMCA-2026-006"]  # no second suspend
    async with ctx.client() as c:
        assert (await c.post(f"/v1/admin/users/{h}/resweep")).status_code == 401


async def test_suspend_rejects_queued_work_and_the_worker_skips_suspended_owners(tmp_path):
    async with make_ctx(tmp_path, worker_enabled=False) as ctx:  # nothing claims: rows stay queued
        alex, sam = await ctx.login("alex"), await ctx.login("sam")
        a1 = (await ctx.submit(alex)).json()
        s1 = (await ctx.submit(sam)).json()
        db, pipe = ctx.app.state.db, ctx.app.state.pipeline
        async with ctx.admin() as a:
            r = (await a.post("/v1/admin/users/alex/suspend", json={"reason": "DMCA-2026-003"})).json()
        assert r["rejected_queued"] == ["alex/first-light"]
        row = await db.fetchone("SELECT status, reasons_json FROM submissions WHERE id = ?", (a1["id"],))
        assert (row["status"], json.loads(row["reasons_json"])) == ("rejected", ["account suspended"])
        # a queued row of a suspended owner (e.g. one that slipped in) is never claimed; others still are
        await db.execute("UPDATE submissions SET status = 'queued' WHERE id = ?", (a1["id"],))
        assert await pipe.claim() == s1["id"]
        assert await pipe.claim() is None
        assert (await db.fetchone("SELECT status FROM submissions WHERE id = ?", (a1["id"],)))["status"] == "queued"


# Race tests: the early "is the owner suspended?" checks are stubbed to say no (a suspend landed right after
# them), so only the guard inside the write statement stands between a suspended user and the wall.


async def test_submission_insert_guard_refuses_when_the_early_check_is_stale(ctx, monkeypatch):
    from tac_platform import submissions

    token = await ctx.login("alex")
    uid = (await ctx.app.state.db.fetchone("SELECT id FROM users WHERE handle = 'alex'"))["id"]

    async def stale_current_user(request):  # authenticated before the suspend committed
        return {"id": uid, "handle": "alex", "trusted": 0, "via": "bearer"}

    async with ctx.admin() as a:
        assert (await a.post("/v1/admin/users/alex/suspend", json={"reason": "x"})).status_code == 200
    monkeypatch.setattr(submissions, "current_user", stale_current_user)
    before = await ctx.app.state.store.list("submissions/")
    r = await ctx.submit(token)
    assert r.status_code == 403 and r.json()["error"] == "suspended"
    assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM submissions"))["n"] == 0
    assert await ctx.app.state.store.list("submissions/") == before  # uploaded files cleaned up


def _stale(orig):
    async def row(*args):
        r = await orig(*args)
        return {**dict(r), "suspended_at": None} if r is not None else r
    return row


async def test_publish_cas_refuses_a_suspended_owner_when_the_precheck_is_stale(ctx, monkeypatch):
    token = await ctx.login("alex")
    sub = (await ctx.submit(token)).json()
    await ctx.wait(token, sub["id"])  # in_review
    pub = ctx.app.state.publisher
    async with ctx.admin() as a:
        await a.post("/v1/admin/users/alex/suspend", json={"reason": "x"})
    monkeypatch.setattr(pub, "_row", _stale(pub._row))
    assert not await pub.publish(sub["id"], "system:trusted")
    assert (await ctx.app.state.db.fetchone("SELECT status FROM submissions"))["status"] == "in_review"
    assert await ctx.app.state.store.list("public/alex/") == []  # media copied before the CAS is removed again
    assert await _public_ids(ctx) == []


async def test_unhide_cas_refuses_a_suspended_owner_when_the_precheck_is_stale(ctx, monkeypatch):
    _, sub_id, h, s = await _published(ctx)
    pub = ctx.app.state.publisher
    async with ctx.admin() as a:
        await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "x"})
    monkeypatch.setattr(pub, "_piece_row", _stale(pub._piece_row))
    assert not await pub.unhide(h, s, "admin")
    assert (await ctx.app.state.db.fetchone("SELECT hidden FROM submissions WHERE id = ?", (sub_id,)))["hidden"] == 1
    assert await ctx.app.state.store.list(f"public/{h}/") == []
    assert await _public_ids(ctx) == []


async def test_admin_deletes_a_suspended_account_into_the_takedown_log(ctx):
    token, sub_id, h, s = await _published(ctx)
    db = ctx.app.state.db
    async with ctx.admin() as a:
        await a.post(f"/v1/admin/pieces/{h}/{s}/instagram-posted", json={"posted": True})
        await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "DMCA-2026-004"})
        page = (await a.get("/admin")).text
        btn = re.search(rf"<button type=button class=bad data-act='/v1/admin/users/{h}/delete'([^>]*)>", page)
        assert btn and "data-confirm='Permanently delete" in btn.group(1) and "data-prompt=" in btn.group(1)
        assert (await a.post(f"/v1/admin/users/{h}/delete", json={})).status_code == 400  # reason required
        assert (await a.post("/v1/admin/users/nobody/delete", json={"reason": "x"})).status_code == 404
        await db.execute("UPDATE submissions SET status = 'rendering' WHERE id = ?", (sub_id,))
        r = await a.post(f"/v1/admin/users/{h}/delete", json={"reason": "ERASE-2026-001"})
        assert r.status_code == 409 and r.json()["error"] == "busy"
        await db.execute("UPDATE submissions SET status = 'published' WHERE id = ?", (sub_id,))
        r = await a.post(f"/v1/admin/users/{h}/delete", json={"reason": "ERASE-2026-001"})
        assert r.json() == {"handle": h, "deleted": True, "reminder": f"also remove from Instagram: {h}/{s}"}
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
    assert (log[0]["action"], log[0]["target"], log[0]["actor"]) == ("delete_account", h, "admin")
    assert (log[0]["reason"], log[0]["ig"], log[0]["reminder"]) == (  # structured, durable: the rows are gone
        "ERASE-2026-001", [f"{h}/{s}"], "also remove from Instagram")
    async with ctx.admin() as a:
        row = re.search(r"<tr><td>[^<]*</td><td>delete_account</td>.*?</tr>", (await a.get("/admin/takedowns")).text)
    assert "chip warn'>also remove from Instagram</span> <span class=muted>" + f"{h}/{s}" in row.group(0)
    for table in ("users", "submissions", "access_tokens", "web_sessions"):
        assert (await db.fetchone(f"SELECT COUNT(*) AS n FROM {table}"))["n"] == 0, table
    assert await ctx.app.state.store.list(f"public/{h}/") == []
    async with ctx.client() as c:
        assert (await c.post("/v1/admin/users/x/delete", json={"reason": "x"})).status_code == 401


# ---------------------------------------------------------------- blocked identities


async def _dev_device_signup(ctx, handle):
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        return await c.post("/device", data={"user_code": d["user_code"], "handle": handle, "agree": "1"})


async def test_deleting_a_suspended_account_blocks_the_identity_until_unblocked(ctx):
    await ctx.login("alex")
    async with ctx.admin() as a:
        await a.post("/v1/admin/users/alex/suspend", json={"reason": "DMCA-2026-005"})
        assert (await a.post("/v1/admin/users/alex/delete", json={"reason": "ERASE-2026-002"})).status_code == 200
        page = (await a.get("/admin")).text
        assert "Blocked identities <span class=count>1</span>" in page
    row = await ctx.app.state.db.fetchone("SELECT * FROM blocked_identities")
    bid = row["id"]
    assert f"data-act='/v1/admin/blocked/{bid}/unblock'" in page and f">Unblock alex #{bid}<" in page
    assert row["ref"] == "alex" and len(row["github_id_hash"]) == 64 and "alex" not in row["github_id_hash"]
    # the freed handle can't be taken back: web sign-up and device sign-up both refuse, with the fixed message
    async with ctx.client() as c:
        r = await c.post("/v1/auth/web/login", data={"handle": "alex", "agree": "1"})
        assert r.status_code == 403 and "This account is suspended" in r.text and not c.cookies.get("tac_session")
    r = await _dev_device_signup(ctx, "alex")
    assert r.status_code == 403 and "This account is suspended" in r.text
    assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM users"))["n"] == 0
    async with ctx.admin() as a:
        assert (await a.post("/v1/admin/blocked/999/unblock", json={"reason": "x"})).status_code == 404
        assert (await a.post("/v1/admin/blocked/alex/unblock", json={"reason": "x"})).status_code == 400  # ids only
        assert (await a.post(f"/v1/admin/blocked/{bid}/unblock", json={})).status_code == 400  # reason required
        r = await a.post(f"/v1/admin/blocked/{bid}/unblock", json={"reason": "APPEAL-2026-001"})
        assert r.json() == {"id": bid, "ref": "alex", "unblocked": 1}
        assert (await a.post(f"/v1/admin/blocked/{bid}/unblock", json={"reason": "x"})).status_code == 404
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
    assert [(x["action"], x["target"]) for x in log[:3]] == [("unblock", "alex"), ("delete_account", "alex"),
                                                              ("block", "alex")]
    assert (await _dev_device_signup(ctx, "alex")).status_code == 200  # a mistake undone


async def test_deleting_an_unsuspended_account_blocks_nothing(ctx):
    token = await ctx.login("alex")
    await ctx.login("sam")
    async with ctx.admin() as a:
        assert (await a.post("/v1/admin/users/sam/delete", json={"reason": "ERASE-2026-003"})).status_code == 200
    async with ctx.client(authorization=f"Bearer {token}") as c:  # and the artist's own deletion
        assert (await c.request("DELETE", "/v1/me", json={"confirm": "alex"})).status_code == 204
    assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM blocked_identities"))["n"] == 0
    assert (await _dev_device_signup(ctx, "sam")).status_code == 200
    async with ctx.client() as c:
        assert (await c.post("/v1/auth/web/login", data={"handle": "alex", "agree": "1"})).status_code == 303


async def test_github_identity_of_a_deleted_suspended_account_cant_re_register(tmp_path, monkeypatch):
    gh = {"id": 4242, "login": "alexgh"}

    async def fake_identity(st, code):
        return gh["id"], gh["login"]

    monkeypatch.setattr(web_auth, "github_identity", fake_identity)
    monkeypatch.setattr(auth, "github_identity", fake_identity)
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async def web(c):
            r = await c.get("/v1/auth/web/login")
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            return await c.get("/v1/auth/web/github/callback", params={"code": "gh", "state": state})

        async def device(c):
            d = (await c.post("/v1/auth/device", json={})).json()
            start = await c.post("/device/github", data={"user_code": d["user_code"]})
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            c.cookies.set("tac_gh_nonce", state.split(".")[1])
            return await c.get("/device/github/callback", params={"code": "gh", "state": state})

        async with ctx.client() as c:
            assert (await web(c)).status_code == 200  # created; terms interstitial
        async with ctx.admin() as a:
            await a.post("/v1/admin/users/alexgh/suspend", json={"reason": "x"})
            await a.post("/v1/admin/users/alexgh/delete", json={"reason": "x"})
        gh["login"] = "alexgh-new"  # renamed on GitHub: the numeric id is what's blocked
        async with ctx.client() as c:
            r = await web(c)
            assert r.status_code == 403 and "This account is suspended" in r.text
            r = await device(c)
            assert r.status_code == 403 and "This account is suspended" in r.text
        assert (await ctx.app.state.db.fetchone("SELECT COUNT(*) AS n FROM users"))["n"] == 0
        gh.update(id=5555, login="alexgh")  # a different GitHub account may take the freed handle
        async with ctx.client() as c:
            assert (await web(c)).status_code == 200
        gh.update(id=4242, login="alexgh-new")
        async with ctx.admin() as a:
            bid = (await ctx.app.state.db.fetchone("SELECT id FROM blocked_identities WHERE ref = 'alexgh'"))["id"]
            assert (await a.post(f"/v1/admin/blocked/{bid}/unblock", json={"reason": "x"})).status_code == 200
        async with ctx.client() as c:
            assert (await web(c)).status_code == 200  # unblocked: signs up again


async def test_unblock_needs_admin_and_cookie_csrf(ctx):
    await ctx.login("alex")
    async with ctx.admin() as a:
        await a.post("/v1/admin/users/alex/suspend", json={"reason": "x"})
        await a.post("/v1/admin/users/alex/delete", json={"reason": "x"})
    db = ctx.app.state.db
    bid = (await db.fetchone("SELECT id FROM blocked_identities"))["id"]
    url = f"/v1/admin/blocked/{bid}/unblock"
    async with ctx.client() as c:
        r = await c.post(url, json={"reason": "x"})
        assert r.status_code == 401 and r.json()["error"] == "admin_auth_required"
        c.cookies.set("tac_admin", "wrong-token")
        assert (await c.post(url, json={"reason": "x"}, headers=CSRF)).status_code == 401
        c.cookies.set("tac_admin", "test-admin-token")
        r = await c.post(url, json={"reason": "x"})  # cookie, no CSRF header
        assert r.status_code == 403 and r.json()["error"] == "csrf_check_failed"
        r = await c.post(url, json={"reason": "x"}, headers={**CSRF, "sec-fetch-site": "same-site"})
        assert r.status_code == 403 and r.json()["error"] == "csrf_check_failed"
        assert (await db.fetchone("SELECT COUNT(*) AS n FROM blocked_identities"))["n"] == 1  # nothing lifted
        assert (await c.post(url, json={"reason": "x"}, headers=CSRF)).status_code == 200


async def test_unblock_lifts_exactly_one_block_when_two_share_a_handle(tmp_path, monkeypatch):
    """Two GitHub people held the handle "alex" in turn and were each deleted while suspended: unblocking one
    must leave the other barred, and the audit row must say which block went."""
    gh = {"id": 1001, "login": "alex"}

    async def fake_identity(st, code):
        return gh["id"], gh["login"]

    monkeypatch.setattr(web_auth, "github_identity", fake_identity)
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async def web(c):
            r = await c.get("/v1/auth/web/login")
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            return await c.get("/v1/auth/web/github/callback", params={"code": "gh", "state": state})

        for gid in (1001, 2002):
            gh["id"] = gid
            async with ctx.client() as c:
                assert (await web(c)).status_code == 200
            async with ctx.admin() as a:
                await a.post("/v1/admin/users/alex/suspend", json={"reason": "x"})
                assert (await a.post("/v1/admin/users/alex/delete", json={"reason": "x"})).status_code == 200
        db = ctx.app.state.db
        first, second = [r["id"] for r in await db.fetchall("SELECT id FROM blocked_identities WHERE ref = 'alex'"
                                                            " ORDER BY id")]
        async with ctx.admin() as a:
            page = (await a.get("/admin")).text
            assert f">Unblock alex #{first}<" in page and f">Unblock alex #{second}<" in page
            r = await a.post(f"/v1/admin/blocked/{first}/unblock", json={"reason": "APPEAL-1001"})
            assert r.json() == {"id": first, "ref": "alex", "unblocked": 1}
        assert [r["id"] for r in await db.fetchall("SELECT id FROM blocked_identities")] == [second]
        rows = await db.fetchall("SELECT target, detail, data_json FROM audit_log WHERE action = 'unblock'")
        assert [(x["target"], x["detail"], json.loads(x["data_json"])) for x in rows] == [
            ("alex", "APPEAL-1001", {"block_id": first})]
        blocks = await db.fetchall("SELECT data_json FROM audit_log WHERE action = 'block' ORDER BY id")
        assert [json.loads(x["data_json"])["block_id"] for x in blocks] == [first, second]
        gh["id"] = 2002  # never appealed: still refused
        async with ctx.client() as c:
            r = await web(c)
            assert r.status_code == 403 and "This account is suspended" in r.text
        gh["id"] = 1001  # appealed: signs up again
        async with ctx.client() as c:
            assert (await web(c)).status_code == 200


async def test_blocked_identities_hash_keyed_table_migrates_to_ids(tmp_path):
    import aiosqlite

    from tac_platform.db import Database

    path = tmp_path / "blk.sqlite3"
    async with aiosqlite.connect(path) as c:  # the first shape, keyed by the hash
        await c.execute("CREATE TABLE blocked_identities (github_id_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL,"
                        " ref TEXT NOT NULL)")
        await c.executemany("INSERT INTO blocked_identities VALUES (?, ?, ?)",
                            [("h1", "2026-10-01", "alex"), ("h2", "2026-10-02", "alex")])
        await c.commit()
    db = Database(path)
    await db.open()
    try:
        rows = await db.fetchall("SELECT id, github_id_hash, ref FROM blocked_identities ORDER BY id")
        assert [tuple(r) for r in rows] == [(1, "h1", "alex"), (2, "h2", "alex")]
    finally:
        await db.close()
    db = Database(path)  # second open: already migrated, untouched
    await db.open()
    try:
        assert len(await db.fetchall("SELECT id FROM blocked_identities")) == 2
    finally:
        await db.close()


def test_publisher_refuses_an_empty_secret():
    from tac_platform.publish import Publisher

    with pytest.raises(TypeError):
        Publisher(None, None)  # type: ignore[call-arg]  # no default to fall back on
    with pytest.raises(ValueError):
        Publisher(None, None, secret="")  # type: ignore[arg-type]


# ---------------------------------------------------------------- Instagram


async def test_ig_posted_mark_drives_the_reminder(ctx):
    _, sub_id, h, s = await _published(ctx)
    path = f"/v1/admin/pieces/{h}/{s}/instagram-posted"
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/pieces/{h}/nope/instagram-posted", json={"posted": True})).status_code == 404
        r = await a.post(path, json={"posted": True})
        assert r.status_code == 200 and r.json()["ig_posted_at"]
        page = (await a.get("/admin")).text
        assert "Clear IG mark" in page and "posted to IG" in page
        r = await a.post(f"/v1/admin/pieces/{h}/{s}/hide", json={"reason": "hate symbol"})
        assert r.json()["reminder"] == "also remove from Instagram"
        page = (await a.get("/admin")).text
        assert "posted to IG: also remove from Instagram" in page  # on the hidden card
        assert (await a.get("/v1/admin/queue")).json()["instagram_cleanup"][0]["state"] == "hidden"
        await a.post(f"/v1/admin/pieces/{h}/{s}/unhide")
        r = await a.post(f"/v1/admin/pieces/{h}/{s}/delete", json={"reason": "hate symbol"})
        assert r.json()["reminder"] == "also remove from Instagram"
        q = (await a.get("/v1/admin/queue")).json()
        assert [(c["id"], c["state"]) for c in q["instagram_cleanup"]] == [(f"{h}/{s}", "deleted")]
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
        assert [(x["action"], x["reminder"]) for x in log[:2]] == [("delete", "also remove from Instagram"),
                                                                  ("unhide", None)]
        assert (await a.post(path, json={"posted": False})).json()["ig_posted_at"] is None  # "Removed from IG"
        assert (await a.get("/v1/admin/queue")).json()["instagram_cleanup"] == []
        assert (await a.get("/v1/admin/takedowns")).json()["actions"][0]["reminder"] is None
    assert [r["target"] for r in await _audit(ctx, "ig_posted")] == [f"{h}/{s}"]
    assert [r["target"] for r in await _audit(ctx, "ig_removed")] == [f"{h}/{s}"]


async def test_suspend_reminds_about_ig_posted_pieces(ctx):
    _, _, h, s = await _published(ctx)
    async with ctx.admin() as a:
        await a.post(f"/v1/admin/pieces/{h}/{s}/instagram-posted", json={"posted": True})
        r = await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "repeat infringer"})
        assert r.json()["reminder"] == f"also remove from Instagram: {h}/{s}"


# ---------------------------------------------------------------- takedown log


async def test_takedown_log_lists_moderation_actions_with_reasons(ctx):
    _, _, h, s = await _published(ctx)
    async with ctx.client() as c:
        assert (await c.get("/admin/takedowns")).status_code == 401
    async with ctx.admin() as a:
        await a.post(f"/v1/admin/pieces/{h}/{s}/hide", json={"reason": "<b>copyright</b> claim #12"})
        await a.post(f"/v1/admin/pieces/{h}/{s}/unhide")
        await a.post(f"/v1/admin/pieces/{h}/{s}/delete", json={"reason": "claim upheld"})
        await a.post(f"/v1/admin/users/{h}/suspend", json={"reason": "repeat infringer"})
        await a.post(f"/v1/admin/users/{h}/unsuspend", json={"reason": "appeal"})
        await a.post(f"/v1/admin/users/{h}/trust", json={"trusted": True})  # not a takedown action
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
        page = (await a.get("/admin/takedowns")).text
    assert [(x["action"], x["target"], x["reason"]) for x in log] == [
        ("unsuspend", h, "appeal"), ("suspend", h, "repeat infringer"), ("delete", f"{h}/{s}", "claim upheld"),
        ("unhide", f"{h}/{s}", ""), ("hide", f"{h}/{s}", "<b>copyright</b> claim #12")]
    assert all(x["at"] and x["actor"] == "admin" for x in log)
    assert "&lt;b&gt;copyright&lt;/b&gt; claim #12" in page and "<b>copyright" not in page
    assert page.count("<tr>") == 1 + 5 and "trust" not in page.split("id=takedowns")[1]
    assert "<script" not in page  # read-only


async def test_takedown_log_shows_last_100(ctx):
    db = ctx.app.state.db
    async with db.tx() as tx:
        for i in range(120):
            await tx.audit("admin", "hide", detail=f"r{i}", target=f"a/p{i}")
    async with ctx.admin() as a:
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
    assert len(log) == 100 and log[0]["reason"] == "r119" and log[-1]["reason"] == "r20"


async def test_report_hide_is_in_the_takedown_log(ctx):
    _, _, h, s = await _published(ctx)
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        async with ctx.client(ip=ip) as c:
            await c.post(f"/v1/pieces/{h}/{s}/report", json={"reason": "spam"})
    async with ctx.admin() as a:
        log = (await a.get("/v1/admin/takedowns")).json()["actions"]
    assert [(x["action"], x["actor"], x["target"], x["reason"]) for x in log] == [
        ("hide", "reports", f"{h}/{s}", "3 distinct reports")]


# ---------------------------------------------------------------- admin UI


async def test_admin_ui_takedown_buttons_are_data_not_code(ctx):
    _, sub_id, h, s = await _published(ctx)
    async with ctx.admin() as a:
        page = (await a.get("/admin")).text
    assert "onclick=" not in page and "/admin/takedowns" in page
    buttons = {}
    for attrs in re.findall(r"<button type=button([^>]*)>([^<]*)</button>", page):
        act = htmlmod.unescape(re.search(r"data-act='([^']*)'", attrs[0]).group(1))
        assert act.startswith("/v1/admin/")
        prompt = re.search(r"data-prompt='([^']*)'", attrs[0])
        body = re.search(r"data-body='([^']*)'", attrs[0])
        buttons[attrs[1]] = (act, htmlmod.unescape(prompt.group(1)) if prompt else None,
                             json.loads(htmlmod.unescape(body.group(1))) if body else None)
    act, prompt, _ = buttons["Hide now"]
    assert act == f"/v1/admin/pieces/{h}/{s}/hide" and "reason" in prompt
    act, prompt, _ = buttons[f"Suspend {h}"]
    assert act == f"/v1/admin/users/{h}/suspend" and "reason" in prompt
    assert buttons["Mark posted to IG"] == (f"/v1/admin/pieces/{h}/{s}/instagram-posted", None, {"posted": True})


ADMIN_JS_HARNESS = r"""
const posts = [];
globalThis.location = { origin: 'https://api.terminalart.club', reload() {} };
let handler;
globalThis.document = { addEventListener: (t, f) => { handler = f; }, getElementById: () => ({ value: 'r' }) };
const alerts = [];
globalThis.alert = (m) => alerts.push(m);
const answers = JSON.parse(process.argv[2]);
globalThis.prompt = () => answers.shift();
globalThis.fetch = async (url, opts) => {
  posts.push([url, JSON.parse(opts.body)]);
  return { ok: true, json: async () => ({ reminder: 'also remove from Instagram' }) };
};
eval(process.argv[1]);
const n = answers.length;
for (let i = 0; i < n; i++)
  handler({ target: { closest: () => ({ dataset: { act: '/v1/admin/pieces/a/b/hide', prompt: 'why?' } }) } });
setTimeout(() => console.log(JSON.stringify({ posts, alerts })), 20);
"""


CONFIRM_HARNESS = r"""
const posts = [];
globalThis.location = { origin: 'https://api.terminalart.club', reload() {} };
let handler;
globalThis.document = { addEventListener: (t, f) => { handler = f; }, getElementById: () => ({ value: 'r' }) };
globalThis.alert = () => {};
const confirms = JSON.parse(process.argv[2]);
globalThis.confirm = () => confirms.shift();
globalThis.prompt = () => 'ERASE-1';
globalThis.fetch = async (url, opts) => { posts.push([url, JSON.parse(opts.body)]); return { ok: true }; };
eval(process.argv[1]);
for (let i = 0; i < 2; i++)
  handler({ target: { closest: () => ({ dataset: { act: '/v1/admin/users/a/delete', confirm: 'sure?', prompt: 'why?' } }) } });
setTimeout(() => console.log(JSON.stringify(posts)), 20);
"""


def test_admin_listener_confirm_comes_before_the_prompt():
    import shutil
    import subprocess

    from tac_platform.admin import JS

    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = subprocess.run(["node", "-e", CONFIRM_HARNESS, JS, json.dumps([False, True])], capture_output=True,
                         text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [["/v1/admin/users/a/delete", {"reason": "ERASE-1"}]]  # declined = nothing


def test_admin_listener_alerts_failed_media_deletes():
    import shutil
    import subprocess

    from tac_platform.admin import JS

    if not shutil.which("node"):
        pytest.skip("node not installed")
    harness = ADMIN_JS_HARNESS.replace(
        "json: async () => ({ reminder: 'also remove from Instagram' })",
        "json: async () => ({ reminder: null, media_failed: ['alex/a', 'alex/b'] })")
    out = subprocess.run(["node", "-e", harness, JS, json.dumps(["DMCA-1"])], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["alerts"] == [
        "Public media could not be deleted for: alex/a, alex/b. Click Re-sweep to retry."]


def test_admin_listener_prompt_requires_a_reason():
    import shutil
    import subprocess

    from tac_platform.admin import JS

    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = subprocess.run(["node", "-e", ADMIN_JS_HARNESS, JS, json.dumps([None, "   ", " DMCA #4 "])],
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["posts"] == [["/v1/admin/pieces/a/b/hide", {"reason": "DMCA #4"}]]  # cancel and blank send nothing
    assert res["alerts"] == ["also remove from Instagram"]


async def test_every_published_piece_is_reachable_by_page_or_find(ctx, monkeypatch):
    from tac_platform import admin

    monkeypatch.setattr(admin, "PER_PAGE", 1)
    token, _, h, s1 = await _published(ctx)
    _, _, _, s2 = await _published(ctx, token=token, title="Second Light")
    _, _, sh, ss = await _published(ctx, handle="sam", title="Harbour 100% Fog")
    for i, slug in enumerate((s1, s2, ss)):  # same second in a test run: pin the order
        await ctx.app.state.db.execute("UPDATE submissions SET published_at = ? WHERE slug = ?",
                                       (f"2026-10-0{i + 1}T00:00:00+00:00", slug))
    hide = lambda h_, s_: f"data-act='/v1/admin/pieces/{h_}/{s_}/hide'"  # noqa: E731
    async with ctx.admin() as a:
        p1, p3 = (await a.get("/admin")).text, (await a.get("/admin", params={"page": 3})).text
        assert hide(sh, ss) in p1 and hide(h, s1) not in p1  # newest first
        assert "page 1 of 3" in p1 and "href='/admin?page=2#published'>older →" in p1 and "← newer" not in p1
        assert hide(h, s1) in p3 and "page 3 of 3" in p3 and "older →" not in p3  # the oldest is reachable
        assert (await a.get("/v1/admin/queue", params={"page": 2})).json()["published_page"] == {
            "page": 2, "per_page": 1, "total": 3, "find": ""}

        async def found(q):
            j = (await a.get("/v1/admin/queue", params={"find": q})).json()
            return sorted(f"{i['handle']}/{i['slug']}" for i in j["published"]), j["published_page"]["total"]

        assert await found(f"{h}/{s1}") == ([f"{h}/{s1}"], 1)
        assert await found(f"https://terminalart.club/@{h}/{s2}") == ([f"{h}/{s2}"], 1)  # a complaint's URL
        assert await found(f"/media/{sh}/{ss}/preview.webp") == ([f"{sh}/{ss}"], 1)
        assert (await found(f"@{h}"))[1] == 2  # a handle: all their visible pieces (paged)
        assert await found("100%") == ([f"{sh}/{ss}"], 1)  # LIKE wildcards are literal
        assert await found("1_0") == ([], 0)
        page = (await a.get("/admin", params={"find": "x' autofocus onfocus='alert(1)"})).text
        assert "no match" in page and "onfocus='alert" not in page and "x&#x27; autofocus" in page
        page = (await a.get("/admin", params={"find": f"{h}/{s1}"})).text
        assert hide(h, s1) in page and "<form class=find method=get" in page


async def test_admin_page_number_is_clamped(ctx):
    async with ctx.admin() as a:
        for n, want in ((10**30, 10**6), (-5, 1), (0, 1)):
            r = await a.get("/v1/admin/queue", params={"page": n})
            assert r.status_code == 200 and r.json()["published_page"]["page"] == want, n
            assert (await a.get("/admin", params={"page": n})).status_code == 200


# ---------------------------------------------------------------- migration


async def test_takedown_columns_migrate_onto_an_old_db(tmp_path):
    import aiosqlite

    from tac_platform.db import Database

    path = tmp_path / "old.sqlite3"
    async with aiosqlite.connect(path) as c:  # the three tables as created before these columns existed
        await c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, handle TEXT NOT NULL UNIQUE, github_id INTEGER UNIQUE,"
                        " trusted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
        await c.execute("CREATE TABLE submissions (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, slug TEXT NOT NULL,"
                        " title TEXT NOT NULL, meta_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,"
                        " updated_at TEXT NOT NULL)")
        await c.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,"
                        " action TEXT NOT NULL, submission_id TEXT, from_status TEXT, to_status TEXT, detail TEXT)")
        await c.execute("INSERT INTO users (handle, created_at) VALUES ('old', 'x')")
        await c.commit()
    db = Database(path)
    await db.open()
    try:
        row = await db.fetchone("SELECT suspended_at, suspended_reason FROM users WHERE handle = 'old'")
        assert tuple(row) == (None, None)
        assert "ig_posted_at" in {r["name"] for r in await db.fetchall("PRAGMA table_info(submissions)")}
        async with db.tx() as tx:
            await tx.audit("admin", "suspend", target="old", detail="x", data={"ig": ["old/a"]})
        row = await db.fetchone("SELECT target, data_json FROM audit_log")
        assert (row["target"], json.loads(row["data_json"])) == ("old", {"ig": ["old/a"]})
    finally:
        await db.close()
