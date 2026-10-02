import re

from tac_platform.web import sha256_hex


async def test_device_flow_dev_mode(ctx):
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        assert re.fullmatch(r"[A-Z]{4}-[A-Z]{4}", d["user_code"])
        assert d["interval"] == 3 and d["expires_in"] == 600
        assert d["verification_uri"].endswith("/device")

        r = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
        assert r.status_code == 428 and r.json() == {"error": "authorization_pending"}

        page = await c.get("/device", params={"code": d["user_code"].lower()})
        assert d["user_code"] in page.text

        # lowercase + no dash is normalised
        r = await c.post("/device", data={"user_code": d["user_code"].replace("-", "").lower(), "handle": "alex"})
        assert r.status_code == 200 and "alex" in r.text

        r = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
        assert r.status_code == 200
        token = r.json()["access_token"]
        assert r.json()["handle"] == "alex"

        # consumed exactly once
        r = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
        assert r.status_code == 410

        me = await c.get("/v1/me", headers={"authorization": f"Bearer {token}"})
        assert me.json() == {"handle": "alex"}
        assert (await c.get("/v1/me", headers={"authorization": "Bearer nope"})).status_code == 401
        assert (await c.get("/v1/me")).status_code == 401

    # only the hash is stored
    rows = await ctx.app.state.db.fetchall("SELECT token_sha256 FROM access_tokens")
    assert [r["token_sha256"] for r in rows] == [sha256_hex(token)]
    dev = await ctx.app.state.db.fetchall("SELECT device_code_sha256 FROM device_codes")
    assert dev[0]["device_code_sha256"] == sha256_hex(d["device_code"])


async def test_device_expired_and_bad_input(ctx):
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        bad = await c.post("/device", data={"user_code": d["user_code"], "handle": "No Caps!"})
        assert bad.status_code == 400
        reserved = await c.post("/device", data={"user_code": d["user_code"], "handle": "admin"})
        assert reserved.status_code == 400

        await ctx.app.state.db.execute("UPDATE device_codes SET expires_at = 0")
        r = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
        assert r.status_code == 410
        late = await c.post("/device", data={"user_code": d["user_code"], "handle": "late"})
        assert late.status_code == 400
        assert (await c.post("/v1/auth/token", json={"device_code": "unknown"})).status_code == 400


async def test_handle_unique(ctx):
    await ctx.login("taken")
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        r = await c.post("/device", data={"user_code": d["user_code"], "handle": "taken"})
        assert r.status_code == 409
        # the code stays usable with another handle
        r = await c.post("/device", data={"user_code": d["user_code"], "handle": "other"})
        assert r.status_code == 200


async def test_device_form_rate_limited(ctx):
    async with ctx.client(ip="10.1.1.1") as c:
        codes = [r.status_code for r in [await c.post("/device", data={"user_code": "BBBB-BBBB", "handle": "xx"})
                                         for _ in range(11)]]
    assert codes[:10] == [400] * 10 and codes[10] == 429
