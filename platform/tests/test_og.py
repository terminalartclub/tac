"""Share cards at publish time + the /v1/og link-preview shell."""

import html
import io
import json
import re

import httpx
import pytest
from PIL import Image

from conftest import META, handle_slug, make_ctx
from tac_platform import cards, og
from tac_platform.web import ApiError

SITE = "http://localhost:5181"
TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>terminal art club</title>
<meta name="description" content="site default description">
<meta property="og:title" content="terminal art club">
<meta content="https://terminalart.club/og-default.jpg" property="og:image">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="https://terminalart.club/">
<script type="module" src="/assets/app.js"></script>
</head><body><div id="app"></div></body></html>"""


@pytest.fixture
async def site(tmp_path):
    async with make_ctx(tmp_path, site_url=SITE) as c:
        fetches: list[str] = []

        async def fetch(url: str) -> str:
            fetches.append(url)
            return TEMPLATE

        c.app.state.og_template._fetch = fetch
        c.fetches = fetches
        yield c


async def publish(ctx, handle="alex", meta=None):
    token = await ctx.login(handle)
    sub = (await ctx.submit(token, meta=meta)).json()
    await ctx.wait(token, sub["id"])
    async with ctx.admin() as a:
        assert (await a.post(f"/v1/admin/submissions/{sub['id']}/approve")).status_code == 200
    st = await ctx.wait(token, sub["id"], until=("published",))
    return handle_slug(st["preview_url"])


def metas(page: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'<meta (?:property|name)="([^"]+)" content="([^"]*)">', page):
        out.setdefault(m.group(1), []).append(html.unescape(m.group(2)))
    return out


async def get_og(ctx, path: str):
    async with ctx.client() as c:
        return await c.get("/v1/og", params={"path": path})


# ── cards ──────────────────────────────────────────────────────────────────


async def test_publish_writes_share_and_card(site):
    h, s = await publish(site)
    store = site.app.state.store
    share, card = await store.get(f"public/{h}/{s}/share.jpg"), await store.get(f"public/{h}/{s}/card.jpg")
    assert share and card and len(share) < 200 * 1024 and len(card) < 200 * 1024
    assert Image.open(io.BytesIO(card)).size == (1200, 630)
    w, hgt = Image.open(io.BytesIO(share)).size
    assert 480 <= w <= 640 and Image.open(io.BytesIO(share)).format == "JPEG"
    doc = await site.app.state.publisher.regenerate()
    assert doc["pieces"][0]["share"] == f"{h}/{s}/share.jpg" and doc["pieces"][0]["card"] == f"{h}/{s}/card.jpg"
    async with site.client() as c:
        assert (await c.get(f"/media/{h}/{s}/card.jpg")).status_code == 200


async def test_card_renders_portrait_and_shrinks_long_text():
    buf = io.BytesIO()
    Image.new("RGB", (540, 960), (40, 80, 120)).save(buf, "JPEG")
    share, size = cards.share_jpg(buf.getvalue(), "a-very-long-handle-24chr", "Claude Haiku 4.5")
    assert size == (540, 960) and Image.open(io.BytesIO(share)).size == size
    card = cards.card_jpg(buf.getvalue(), "x" * 80, "a-very-long-handle-24chr", "Claude Haiku 4.5")
    assert Image.open(io.BytesIO(card)).size == (1200, 630) and len(card) < 200 * 1024
    runs, px = cards._fit([("x" * 80, cards.TITLE, True), *cards._byline("a-very-long-handle-24chr", "m")],
                          30, 20, 1104)
    assert cards._width(runs, px) <= 1104 and runs[0][0].endswith("…") and runs[1][0] == "@a-very-long-handle-24chr"


async def test_backfill_is_idempotent_and_follows_layout_version(site):
    h, s = await publish(site)
    store, pub, db = site.app.state.store, site.app.state.publisher, site.app.state.db
    store.path(f"public/{h}/{s}/card.jpg").unlink()
    assert await pub.backfill_cards() == 1 and await store.get(f"public/{h}/{s}/card.jpg")
    assert await pub.backfill_cards() == 0  # nothing missing, same version
    await db.execute("UPDATE kv SET value = 'old' WHERE key = 'cards_version'")
    assert await pub.backfill_cards() == 1  # layout changed: re-render all
    await pub.hide(h, s, "test")
    assert await pub.backfill_cards() == 0 and await store.get(f"public/{h}/{s}/share.jpg") is None


# ── /v1/og ─────────────────────────────────────────────────────────────────


async def test_og_piece_meta_has_author_and_card_images(site):
    h, s = await publish(site)
    r = await get_og(site, f"/night-shift/{h}/{s}")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=300"
    page, m = r.text, metas(r.text)
    assert m["og:title"] == ["First Light · @alex · Claude Opus 5.5"]  # AUTHOR ALWAYS, no site suffix
    assert "<title>First Light · @alex · Claude Opus 5.5</title>" in page and page.count("<title>") == 1
    assert m["og:description"] == ["animated terminal art made by @alex's Claude. terminal art club"]
    assert m["og:site_name"] == ["terminal art club"]
    assert m["og:url"] == [f"{SITE}/night-shift/{h}/{s}"]
    media = f"{site.settings.public_base_url}/media/{h}/{s}"
    share = Image.open(io.BytesIO(await site.app.state.store.get(f"public/{h}/{s}/share.jpg")))
    assert m["og:image"] == [f"{media}/share.jpg"]
    assert m["og:image:width"] == [str(share.width)] and m["og:image:height"] == [str(share.height)]
    assert m["twitter:card"] == ["summary_large_image"] and m["twitter:image"] == [f"{media}/card.jpg"]
    # the template's own copies are gone; the rest of the page (SPA boot) is intact
    assert "og-default.jpg" not in page and "site default description" not in page
    assert 'href="https://terminalart.club/"' not in page and f'<link rel="canonical" href="{SITE}/night-shift/{h}/{s}">' in page
    assert '<script type="module" src="/assets/app.js"></script>' in page and page.rstrip().endswith("</html>")


async def test_og_artist_and_feed(site):
    h, s = await publish(site)
    m = metas((await get_og(site, f"/night-shift/@{h}")).text)
    assert m["og:title"] == ["@alex on terminal art club · 1 piece"] and m["og:url"] == [f"{SITE}/night-shift/@{h}"]
    assert m["og:image"][0].endswith(f"/media/{h}/{s}/share.jpg")
    for path in ("/night-shift", "/night-shift/"):
        r = await get_og(site, path)
        m = metas(r.text)
        assert r.status_code == 200 and m["og:title"] == ["the wall · terminal art club"]
        assert m["og:url"] == [f"{SITE}/night-shift"]
        assert 'content="https://terminalart.club/og-default.jpg" property="og:image"' in r.text  # site image kept


async def test_og_escapes_every_value(site):
    evil = {**META, "title": '"><script>alert(1)</script><meta x="'}
    h, s = await publish(site, meta=evil)
    page = (await get_og(site, f"/night-shift/{h}/{s}")).text
    assert "<script>alert(1)" not in page and '"><script>' not in page
    assert metas(page)["og:title"] == [f'{evil["title"]} · @alex · Claude Opus 5.5']  # survives as text


@pytest.mark.parametrize("path", [
    "/night-shift/alex/nope", "/night-shift/@nobody", "/night-shift/Alex/first-light",
    "/night-shift/alex/first-light/extra", "/night-shift/alex/<script>", "/night-shift/@a",
    "/elsewhere", "", "/night-shift/alex/first-light?x=1", "//evil.example/night-shift",
    "/night-shift/../admin", "/night-shift/alex/first light",
])
async def test_og_404_on_unknown_or_invalid(site, path):
    await publish(site)
    r = await get_og(site, path)
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    assert "<script>" not in r.text and "evil" not in r.text


async def test_og_hidden_piece_is_404(site):
    h, s = await publish(site)
    await site.app.state.publisher.hide(h, s, "test")
    assert (await get_og(site, f"/night-shift/{h}/{s}")).status_code == 404
    assert (await get_og(site, f"/night-shift/@{h}")).status_code == 404


async def test_og_template_cached_and_refreshed(site, monkeypatch):
    h, s = await publish(site)
    clock = [1000.0]
    monkeypatch.setattr(og.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        assert (await get_og(site, f"/night-shift/{h}/{s}")).status_code == 200
    assert site.fetches == [f"{SITE}/index.html"]  # one fetch serves many requests
    clock[0] += og.TEMPLATE_TTL_S + 1
    await get_og(site, "/night-shift")
    assert len(site.fetches) == 2  # refreshed after 10 minutes

    async def down(url: str) -> str:
        raise OSError("site down")

    site.app.state.og_template._fetch = down
    clock[0] += og.TEMPLATE_TTL_S + 1
    assert (await get_og(site, "/night-shift")).status_code == 200  # stale copy beats an error


async def test_og_503_without_any_template(site):
    async def down(url: str) -> str:
        raise OSError("site down")

    site.app.state.og_template._fetch = down
    r = await get_og(site, "/night-shift")
    assert r.status_code == 503 and r.json()["error"] == "og_unavailable"


async def test_og_404_without_site_url(ctx):
    assert (await get_og(ctx, "/night-shift")).status_code == 404


async def test_backfill_failure_never_fails_boot_and_keeps_version_unset(tmp_path, monkeypatch):
    from tac_platform.storage import LocalStore

    async with make_ctx(tmp_path, site_url=SITE) as ctx:
        h, s = await publish(ctx)
        await ctx.app.state.card_backfill
        ctx.app.state.store.path(f"public/{h}/{s}/card.jpg").unlink()
        await ctx.app.state.db.execute("DELETE FROM kv WHERE key = 'cards_version'")

    real_put = LocalStore.put

    async def failing_put(self, key, data):
        if key.endswith(("share.jpg", "card.jpg")):
            raise OSError("disk full")
        return await real_put(self, key, data)

    monkeypatch.setattr(LocalStore, "put", failing_put)
    async with make_ctx(tmp_path, site_url=SITE) as ctx:  # boot succeeds despite the failing piece
        async with ctx.client() as c:
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.get("/v1/community.json")).json()["pieces"][0]["id"] == f"{h}/{s}"
        assert await ctx.app.state.card_backfill == 0
        assert await ctx.app.state.db.fetchone("SELECT 1 FROM kv WHERE key = 'cards_version'") is None

    monkeypatch.setattr(LocalStore, "put", real_put)
    async with make_ctx(tmp_path, site_url=SITE) as ctx:  # next boot retries and records the version
        assert await ctx.app.state.card_backfill == 1
        row = await ctx.app.state.db.fetchone("SELECT value FROM kv WHERE key = 'cards_version'")
        assert row["value"] == cards.VERSION


async def test_backfill_holds_media_lock_per_piece_and_skips_hidden(site, monkeypatch):
    h, s = await publish(site)
    pub = site.app.state.publisher
    await site.app.state.card_backfill
    site.app.state.store.path(f"public/{h}/{s}/card.jpg").unlink()
    held = []
    real = pub.write_cards

    async def spy(handle, slug):
        held.append(pub._media_lock.locked())
        return await real(handle, slug)

    monkeypatch.setattr(pub, "write_cards", spy)
    assert await pub.backfill_cards() == 1 and held == [True]
    assert not pub._media_lock.locked()  # released after the piece, not held across the loop
    await pub.hide(h, s, "test")
    await site.app.state.db.execute("UPDATE kv SET value = 'old' WHERE key = 'cards_version'")
    assert await pub.backfill_cards() == 0
    assert await site.app.state.store.get(f"public/{h}/{s}/share.jpg") is None  # hidden stays unpublished


async def test_card_render_failure_still_publishes(site, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pillow exploded")

    monkeypatch.setattr(cards, "share_jpg", boom)
    h, s = await publish(site)  # publish() asserts the piece reached "published"
    store = site.app.state.store
    assert await store.get(f"public/{h}/{s}/preview.webp") and await store.get(f"public/{h}/{s}/share.jpg") is None
    doc = await site.app.state.publisher.regenerate()
    assert [p["id"] for p in doc["pieces"]] == [f"{h}/{s}"]
    m = metas((await get_og(site, f"/night-shift/{h}/{s}")).text)
    assert m["og:image"][0].endswith("/og.jpg")  # falls back to the render's own still



def _template(handler) -> og.Template:
    return og.Template(f"{SITE}/index.html", transport=httpx.MockTransport(handler))


async def test_template_http_fetch_streams_with_cap_and_no_redirects():
    ok = _template(lambda req: httpx.Response(200, text=TEMPLATE))
    assert await ok.get() == TEMPLATE

    big = _template(lambda req: httpx.Response(200, content=b"<html><head></head>" + b"x" * og.TEMPLATE_MAX_BYTES))
    with pytest.raises(ApiError) as e:
        await big.get()
    assert e.value.status == 503

    seen = []

    def redirect(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://evil.example/index.html"})

    with pytest.raises(ApiError):
        await _template(redirect).get()
    assert seen == [f"{SITE}/index.html"]  # the redirect was not followed

    with pytest.raises(ApiError):
        await _template(lambda req: httpx.Response(503, text="<html><head></head></html>")).get()


async def test_template_negative_cache_window(monkeypatch):
    clock = [5000.0]
    monkeypatch.setattr(og.time, "monotonic", lambda: clock[0])
    hits = []
    up = [False]

    def handler(req):
        hits.append(1)
        return httpx.Response(200, text=TEMPLATE) if up[0] else httpx.Response(500)

    t = _template(handler)
    for _ in range(5):  # within 30 s of the failure: immediate 503, one upstream attempt only
        with pytest.raises(ApiError):
            await t.get()
        clock[0] += 5
    assert len(hits) == 1
    up[0] = True
    clock[0] += og.TEMPLATE_FAIL_TTL_S  # window over: retried and recovered
    assert await t.get() == TEMPLATE and len(hits) == 2
    assert og.TEMPLATE_TTL_S == 120 and og.TEMPLATE_FAIL_TTL_S == 30
