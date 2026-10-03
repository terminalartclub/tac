import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_ctx
from tac_platform import auth
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
        assert me.json()["handle"] == "alex" and set(me.json()) == {"handle", "display_name", "bio", "link", "created"}
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


# ---------------------------------------------------------------- github_identity against a misbehaving GitHub

GH_TOKEN_URL = "https://github.com/login/oauth/access_token"


def _github(token_resp, user_resp=None):
    """MockTransport: token endpoint -> token_resp, /user -> user_resp. A callable raises/returns per request."""

    def handler(request: httpx.Request) -> httpx.Response:
        resp = token_resp if str(request.url) == GH_TOKEN_URL else user_resp
        return resp(request) if callable(resp) else resp

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10)


def _timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


def _json_resp(status, body):
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


OK_TOKEN = _json_resp(200, {"access_token": "gho_x"})
GITHUB_FAILURES = {
    "token_not_json": (httpx.Response(200, text="<html>rate limited</html>"), None),
    "token_5xx": (httpx.Response(502, text="bad gateway"), None),
    "token_error_body": (_json_resp(200, {"error": "bad_verification_code"}), None),
    "token_json_list": (_json_resp(200, ["x"]), None),
    "token_timeout": (_timeout, None),
    "user_401_no_id": (OK_TOKEN, _json_resp(401, {"message": "Bad credentials"})),
    "user_200_no_id": (OK_TOKEN, _json_resp(200, {"login": "alex"})),
    "user_not_json": (OK_TOKEN, httpx.Response(200, text="oops")),
    "user_id_not_int": (OK_TOKEN, _json_resp(200, {"id": "12", "login": "alex"})),
    "user_timeout": (OK_TOKEN, _timeout),
    "connect_error": (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)), None),
}
ST = SimpleNamespace(settings=SimpleNamespace(github_client_id="cid", github_client_secret="sec"))


@pytest.mark.parametrize("case", sorted(GITHUB_FAILURES))
async def test_github_identity_failures_return_none(case, monkeypatch):
    monkeypatch.setattr(auth, "_github_client", _github(*GITHUB_FAILURES[case]))
    assert await auth.github_identity(ST, "code") is None


async def test_github_identity_ok(monkeypatch):
    monkeypatch.setattr(auth, "_github_client", _github(OK_TOKEN, _json_resp(200, {"id": 7, "login": "Alex"})))
    assert await auth.github_identity(ST, "code") == (7, "alex")


@pytest.mark.parametrize("case", ["token_not_json", "user_200_no_id", "user_timeout"])
async def test_github_callbacks_clean_400_on_github_failure(case, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_github_client", _github(*GITHUB_FAILURES[case]))
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid") as ctx:
        async with ctx.client() as c:
            # web sign-in callback
            r = await c.get("/v1/auth/web/login", params={"return": "/me"})
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            cb = await c.get("/v1/auth/web/github/callback", params={"code": "x", "state": state})
            assert cb.status_code == 400 and "GitHub did not authorize" in cb.text
            assert "tac_session" not in cb.headers.get("set-cookie", "")
            # device-flow callback
            d = (await c.post("/v1/auth/device", json={})).json()
            r = await c.post("/device/github", data={"user_code": d["user_code"]})
            state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
            cb = await c.get("/device/github/callback", params={"code": "x", "state": state})
            assert cb.status_code == 400 and "GitHub did not authorize" in cb.text
            assert (await c.post("/v1/auth/token", json={"device_code": d["device_code"]})).status_code == 428
