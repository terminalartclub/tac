"""Link-preview shell for any /night-shift URL, including pieces published after the last site deploy.

GET /v1/og?path=/night-shift                     the wall (feed)
GET /v1/og?path=/night-shift/@<handle>           an artist
GET /v1/og?path=/night-shift/<handle>/<slug>     a piece

Returns the site's own index.html (TAC_SITE_URL/index.html, cached, refreshed every 10 min) with the
<head> link-preview tags replaced for that path. The site's nginx proxies unmatched /night-shift/
paths here, so a messenger's crawler sees per-piece tags and a browser still boots the SPA.

The author always appears: a piece's og:title is `<title> · @handle · <model>`.
Only paths that match the handle/slug patterns and name a published, visible piece (or an artist
with one) get a page; anything else is a 404. Every value is HTML-escaped, and the raw path is never
echoed: og:url is rebuilt from the validated parts.
"""

import asyncio
import html
import io
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from PIL import Image

from .models import HANDLE_RE, SLUG_RE
from .publish import model_label
from .web import ApiError

log = logging.getLogger("tac.og")
router = APIRouter()

SITE_NAME = "terminal art club"
TEMPLATE_TTL_S = 600
TEMPLATE_MAX_BYTES = 512 * 1024
CACHE_CONTROL = "public, max-age=300"
FEED = "/night-shift"
_PIECE = re.compile(r"^/night-shift/([^/@]+)/([^/]+)/?$")
_ARTIST = re.compile(r"^/night-shift/@([^/]+)/?$")
_FEED = re.compile(r"^/night-shift/?$")
# Tags we own; any existing copy in the template is removed before ours go in.
_OWNED = ("og:title", "og:description", "og:url", "og:type", "og:site_name", "og:image", "og:image:width",
          "og:image:height", "og:image:alt", "twitter:card", "twitter:image", "twitter:title",
          "twitter:description", "description")


class Template:
    """The site's index.html, fetched lazily and re-fetched after TEMPLATE_TTL_S. A failed refresh
    keeps serving the last good copy; with no copy at all the endpoint answers 503."""

    def __init__(self, url: str, fetch=None) -> None:
        self.url = url
        self.html: str | None = None
        self.fetched_at = 0.0
        self._lock = asyncio.Lock()
        self._fetch = fetch or self._http_fetch

    async def _http_fetch(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as c:
            r = await c.get(url, headers={"Accept": "text/html"})
        r.raise_for_status()
        if len(r.content) > TEMPLATE_MAX_BYTES:
            raise ValueError(f"template over {TEMPLATE_MAX_BYTES} bytes")
        return r.text

    async def get(self) -> str:
        if self.html is not None and time.monotonic() - self.fetched_at < TEMPLATE_TTL_S:
            return self.html
        async with self._lock:  # one refresh at a time; the others wait and reuse it
            if self.html is None or time.monotonic() - self.fetched_at >= TEMPLATE_TTL_S:
                try:
                    text = await self._fetch(self.url)
                    if not re.search(r"</head\s*>", text, re.I):
                        raise ValueError("template has no </head>")
                    self.html, self.fetched_at = text, time.monotonic()
                except Exception as e:  # noqa: BLE001
                    log.warning("og template fetch failed (%s): %s", self.url, e)
                    if self.html is None:
                        raise ApiError(503, "og_unavailable") from None
                    self.fetched_at = time.monotonic() - TEMPLATE_TTL_S + 60  # retry in a minute
        return self.html


def _tag(key: str, value: str | int) -> str:
    attr = "name" if key.startswith("twitter:") or key == "description" else "property"
    return f'<meta {attr}="{html.escape(key)}" content="{html.escape(str(value), quote=True)}">'


def rewrite_head(page: str, title: str, tags: dict[str, str | int]) -> str:
    """Drop the template's copies of the tags we set (and <title>, canonical), add ours before </head>."""
    for key in tags:
        page = re.sub(rf"""<meta\b[^>]*\b(?:property|name)\s*=\s*["']{re.escape(key)}["'][^>]*>\s*""", "", page,
                      flags=re.I)
    page = re.sub(r"<title\b[^>]*>.*?</title\s*>\s*", "", page, count=1, flags=re.I | re.S)
    page = re.sub(r"""<link\b[^>]*\brel\s*=\s*["']canonical["'][^>]*>\s*""", "", page, flags=re.I)
    block = [f"<title>{html.escape(title)}</title>", *(_tag(k, v) for k, v in tags.items())]
    if "og:url" in tags:
        block.append(f'<link rel="canonical" href="{html.escape(str(tags["og:url"]), quote=True)}">')
    return re.sub(r"</head\s*>", lambda _: "\n".join(block) + "\n</head>", page, count=1, flags=re.I)


async def _image_size(store, key: str) -> tuple[int, int] | None:
    data = await store.get(key)
    if data is None:
        return None
    try:
        return Image.open(io.BytesIO(data)).size  # header only
    except Exception:  # noqa: BLE001
        return None


async def _images(st, handle: str, slug: str) -> dict[str, str | int]:
    """og:image = share.jpg with its real size (else og.jpg); twitter:image = card.jpg (else og.jpg)."""
    media = f"{st.settings.public_base_url}/media/{handle}/{slug}"
    out: dict[str, str | int] = {}
    for name in ("share.jpg", "og.jpg"):
        size = await _image_size(st.store, f"public/{handle}/{slug}/{name}")
        if size:
            out |= {"og:image": f"{media}/{name}", "og:image:width": size[0], "og:image:height": size[1]}
            break
    has_card = await st.store.get(f"public/{handle}/{slug}/card.jpg") is not None
    out |= {"twitter:card": "summary_large_image",
            "twitter:image": f"{media}/{'card.jpg' if has_card else 'og.jpg'}"}
    return out


def _description(handle: str) -> str:
    return f"animated terminal art made by @{handle}'s Claude. {SITE_NAME}"


async def _piece(st, handle: str, slug: str) -> tuple[str, dict]:
    row = await st.db.fetchone(
        "SELECT s.title, s.meta_json FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE u.handle = ? AND s.slug = ? AND s.status = 'published' AND s.hidden = 0",
        (handle, slug),
    )
    if row is None:
        raise ApiError(404, "not_found")
    model = model_label(json.loads(row["meta_json"]).get("model", ""))
    title = f"{row['title']} · @{handle} · {model}"  # AUTHOR ALWAYS: messengers print og:title as the text
    tags = {"og:type": "website", "og:site_name": SITE_NAME, "og:title": title,
            "og:description": _description(handle), "description": _description(handle),
            "og:url": f"{st.settings.site_url}/night-shift/{handle}/{slug}",
            **await _images(st, handle, slug)}
    return title, tags


async def _artist(st, handle: str) -> tuple[str, dict]:
    rows = await st.db.fetchall(
        "SELECT s.slug FROM submissions s JOIN users u ON u.id = s.user_id"
        " WHERE u.handle = ? AND s.status = 'published' AND s.hidden = 0 ORDER BY s.published_at DESC, s.id",
        (handle,),
    )
    if not rows:
        raise ApiError(404, "not_found")
    n = len(rows)
    title = f"@{handle} on {SITE_NAME} · {n} {'piece' if n == 1 else 'pieces'}"
    tags = {"og:type": "profile", "og:site_name": SITE_NAME, "og:title": title,
            "og:description": _description(handle), "description": _description(handle),
            "og:url": f"{st.settings.site_url}/night-shift/@{handle}",
            **await _images(st, handle, rows[0]["slug"])}  # their newest piece
    return title, tags


def _feed(st) -> tuple[str, dict]:
    title = f"the wall · {SITE_NAME}"
    return title, {"og:type": "website", "og:site_name": SITE_NAME, "og:title": title,
                   "og:url": f"{st.settings.site_url}{FEED}"}  # image + description: the template's


@router.get("/v1/og", response_class=HTMLResponse)
async def og_page(request: Request, path: str = "") -> HTMLResponse:
    st = request.app.state
    if not st.settings.site_url:
        raise ApiError(404, "not_found")  # no canonical site to point at (dev without TAC_SITE_URL)
    if m := _PIECE.match(path):
        handle, slug = m.groups()
        if not (HANDLE_RE.match(handle) and SLUG_RE.match(slug)):
            raise ApiError(404, "not_found")
        title, tags = await _piece(st, handle, slug)
    elif m := _ARTIST.match(path):
        if not HANDLE_RE.match(m.group(1)):
            raise ApiError(404, "not_found")
        title, tags = await _artist(st, m.group(1))
    elif _FEED.match(path):
        title, tags = _feed(st)
    else:
        raise ApiError(404, "not_found")
    page = rewrite_head(await st.og_template.get(), title, tags)
    return HTMLResponse(page, headers={"Cache-Control": CACHE_CONTROL})
