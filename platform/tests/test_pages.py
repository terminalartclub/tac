"""Every server-rendered HTML page uses the one shared stylesheet (terminalart.club look) and current naming."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import make_ctx
from tac_platform import auth
from tac_platform.web import CSS, FONTS

STYLE_TAG = f"<style id=tac-style>{CSS}</style>"
WORDMARK = re.compile(r"<a class=wordmark href='([^']*)'><span class=accent-cyan>terminal</span> "
                      r"<span class=accent-purple>art</span> <span class=accent-pink>club</span></a>")


def assert_styled(name: str, r, home: str = "/") -> None:
    assert r.headers["content-type"].startswith("text/html"), name
    text = r.text
    assert text.count(STYLE_TAG) == 1 and text.count("<style") == 1, name  # shared sheet only, no per-page styles
    assert f"<link rel=stylesheet href='{FONTS}'>" in text, name
    assert " style=" not in text, name  # no inline style attributes either
    m = WORDMARK.search(text)
    assert m and m.group(1) == home, name
    assert "spare cycles" not in text.lower(), name


async def test_dev_pages_share_style(ctx):
    async with ctx.client() as c:
        d = (await c.post("/v1/auth/device", json={})).json()
        pages = {
            "web login": await c.get("/v1/auth/web/login", params={"return": "/me"}),
            "web login error": await c.post("/v1/auth/web/login", data={"handle": "X!", "return": "/"}),
            "device": await c.get("/device", params={"code": d["user_code"]}),
            "device error": await c.post("/device", data={"user_code": "nope", "handle": "alex"}),
            "device connected": await c.post("/device", data={"user_code": d["user_code"], "handle": "alex"}),
            "admin login bad": await c.get("/admin/login", params={"token": "wrong"}),
            "admin signed out": await c.get("/admin"),
        }
    async with ctx.admin() as a:
        pages["admin"] = await a.get("/admin")
    h1 = re.search(r"<h1>(.*?)</h1>", pages["web login"].text).group(1)
    assert re.sub(r"<[^>]+>", "", h1) == "sign in · local dev: pick an existing handle or a new one"
    assert "<main class=wide>" in pages["admin"].text and "<main>" in pages["web login"].text
    for name, r in pages.items():
        assert_styled(name, r)


async def test_github_pages_share_style_and_link_site(tmp_path, monkeypatch):
    async def no_identity(st, code):
        return None

    monkeypatch.setattr(auth, "github_identity", no_identity)
    from tac_platform import web_auth

    monkeypatch.setattr(web_auth, "github_identity", no_identity)
    site = "http://localhost:5181"
    async with make_ctx(tmp_path, worker_enabled=False, auth_mode="github", github_client_id="cid",
                        site_url=site) as ctx:
        async with ctx.client() as c:
            d = (await c.post("/v1/auth/device", json={})).json()
            start = await c.post("/device/github", data={"user_code": d["user_code"]})
            dstate = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            w = await c.get("/v1/auth/web/login", params={"return": "/me"})
            wstate = parse_qs(urlsplit(w.headers["location"]).query)["state"][0]
            pages = {
                "device github": await c.get("/device"),
                "device github bad code": await c.post("/device/github", data={"user_code": "nope"}),
                "device callback mismatch": await c.get("/device/github/callback", params={"state": "x.y.z"}),
                "device callback refused": await c.get("/device/github/callback", params={"code": "x", "state": dstate}),
                "web callback mismatch": await c.get("/v1/auth/web/github/callback", params={"state": "x.y.z"}),
                "web callback refused": await c.get("/v1/auth/web/github/callback",
                                                    params={"code": "x", "state": wstate}),
            }
    for name, r in pages.items():
        assert r.status_code in (200, 400), name
        assert_styled(name, r, home=site)


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TOKENS = dict(re.findall(r"--([a-z-]+):(#[0-9a-f]{6})", CSS))


def test_contrast_helper_matches_wcag_reference():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_primary_button_contrast():
    rule = re.search(r"button\.primary \{([^}]*)\}", CSS).group(1)
    assert "background:var(--accent-cyan)" in rule and "color:var(--bg)" in rule and "font-weight:600" in rule
    ratio = contrast(TOKENS["accent-cyan"], TOKENS["bg"])
    assert ratio >= 4.5 and ratio == pytest.approx(12.2, abs=0.3)
    assert "button.primary:hover { filter:brightness(1.08); }" in CSS and ":focus-visible" in CSS


@pytest.mark.parametrize(("fg", "bg"), [
    ("text", "bg"), ("text", "bg-card"), ("text-bright", "bg-elevated"), ("text-dim", "bg"), ("text-dim", "bg-card"),
    ("text-dim", "bg-elevated"), ("accent-pink", "bg-card"), ("accent-cyan", "bg-card"), ("accent-pink", "bg"),
])
def test_text_pairs_meet_wcag_aa(fg, bg):
    assert contrast(TOKENS[fg], TOKENS[bg]) >= 4.5, (fg, bg)
