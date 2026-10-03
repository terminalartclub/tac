"""Link-preview images, rendered from a piece's og.jpg still at publish time.

share.jpg  og:image. The portrait still at its natural aspect (~560 px wide), with a dark
           gradient strip at the bottom: `@handle · model` and the three-colour wordmark.
card.jpg   twitter:image only. 1200x630: the still scaled to the full height and centred,
           the wordmark top-left, and a bottom band `title · @handle · model`.

The author is always drawn in. Text is shrunk to fit, then cut with an ellipsis; it is never
clipped mid-glyph. Both files stay under MAX_BYTES (JPEG quality steps down until they fit).
Pure functions of bytes in -> bytes out; callers run them in a thread (Pillow is CPU-bound).
"""

import io
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).with_name("fonts")  # JetBrains Mono 2.211, SIL OFL 1.1 (JetBrainsMono-OFL.txt)
BG = (8, 8, 15)  # #08080f
TITLE = (224, 224, 238)  # #e0e0ee
HANDLE = (0, 229, 195)  # #00e5c3
MODEL = (138, 138, 166)  # #8a8aa6
WORDMARK = (("terminal", (0, 229, 195)), ("art", (167, 139, 250)), ("club", (244, 114, 182)))
CARD_W, CARD_H = 1200, 630
SHARE_W = 560
MAX_BYTES = 200 * 1024
VERSION = "1"  # bump to re-render every stored card on the next startup


@cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"JetBrainsMono-{'Bold' if bold else 'Regular'}.ttf"), size)


Run = tuple[str, tuple[int, int, int], bool]  # text, colour, bold


def _width(runs: list[Run], size: int) -> float:
    return sum(font(size, b).getlength(t) for t, _, b in runs)


def _fit(runs: list[Run], size: int, min_size: int, max_w: float) -> tuple[list[Run], int]:
    """Shrink to fit; at min_size, cut the first (longest-variable) run with an ellipsis."""
    while size > min_size and _width(runs, size) > max_w:
        size -= 1
    runs = list(runs)
    while _width(runs, size) > max_w and len(runs[0][0]) > 2:
        t, c, b = runs[0]
        runs[0] = (t.rstrip("…")[:-1].rstrip() + "…", c, b)
    return runs, size


def _draw(d: ImageDraw.ImageDraw, x: float, baseline: float, runs: list[Run], size: int) -> None:
    for t, c, b in runs:
        d.text((x, baseline), t, font=font(size, b), fill=c, anchor="ls")
        x += font(size, b).getlength(t)


def _wordmark() -> list[Run]:
    out: list[Run] = []
    for i, (word, colour) in enumerate(WORDMARK):
        out.append((word + (" " if i < 2 else ""), colour, True))
    return out


def _byline(handle: str, model: str) -> list[Run]:
    return [(f"@{handle}", HANDLE, True), (" · ", MODEL, False), (model, MODEL, False)]


def _gradient(w: int, h: int, top_alpha: int, bottom_alpha: int) -> Image.Image:
    g = Image.new("L", (1, h))
    for y in range(h):
        g.putpixel((0, y), round(top_alpha + (bottom_alpha - top_alpha) * (y / max(1, h - 1)) ** 0.8))
    return g.resize((w, h))


def _shade(img: Image.Image, top: int, alpha_top: int = 0, alpha_bottom: int = 235) -> None:
    """Darken img from row `top` to the bottom with a BG-coloured gradient (in place)."""
    h = img.height - top
    if h <= 0:
        return
    layer = Image.new("RGB", (img.width, h), BG)
    img.paste(layer, (0, top), _gradient(img.width, h, alpha_top, alpha_bottom))


def _jpeg(img: Image.Image) -> bytes:
    for q in (86, 80, 74, 68, 60, 52, 44):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            break
    return buf.getvalue()


def _still(og: bytes) -> Image.Image:
    return Image.open(io.BytesIO(og)).convert("RGB")


def share_jpg(og: bytes, handle: str, model: str) -> tuple[bytes, tuple[int, int]]:
    """(jpeg, (w, h)): the still at ~SHARE_W wide, natural aspect, byline strip at the bottom."""
    art = _still(og)
    w = art.width if 480 <= art.width <= 640 else SHARE_W
    art = art.resize((w, round(art.height * w / art.width)), Image.Resampling.LANCZOS) if w != art.width else art
    pad, size, mark_size, gap = 16, 22, 15, 14
    byline, size = _fit(_byline(handle, model), size, 15, w - 2 * pad)
    mark = _wordmark()
    one_line = _width(byline, size) + gap + _width(mark, mark_size) <= w - 2 * pad
    strip = 64 if one_line else 88
    img = art.copy()
    _shade(img, img.height - strip - 24)  # the gradient starts a little above the strip
    d = ImageDraw.Draw(img)
    if one_line:
        base = img.height - 24
        _draw(d, pad, base, byline, size)
        _draw(d, w - pad - _width(mark, mark_size), base, mark, mark_size)
    else:
        _draw(d, pad, img.height - 46, byline, size)
        _draw(d, pad, img.height - 18, mark, mark_size)
    return _jpeg(img), img.size


def card_jpg(og: bytes, title: str, handle: str, model: str) -> bytes:
    """1200x630 for twitter:image: still at full height centred, wordmark top-left, title band."""
    art = _still(og)
    aw = round(art.width * CARD_H / art.height)
    art = art.resize((aw, CARD_H), Image.Resampling.LANCZOS)
    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    img.paste(art, ((CARD_W - aw) // 2, 0))
    band = 84
    _shade(img, CARD_H - band - 40, 0, 240)
    d = ImageDraw.Draw(img)
    mark = _wordmark()
    _shade_box(img, (0, 0, round(_width(mark, 22)) + 64, 62))
    _draw(d, 28, 42, mark, 22)
    runs = [(title, TITLE, True), (" · ", MODEL, False), *_byline(handle, model)]
    runs, size = _fit(runs, 30, 20, CARD_W - 96)
    _draw(d, (CARD_W - _width(runs, size)) / 2, CARD_H - 34, runs, size)
    return _jpeg(img)


def _shade_box(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    """A soft dark backing behind the top-left wordmark, so it reads over bright art."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w, h))
    for x in range(w):
        a = round(200 * min(1.0, (w - x) / (w * 0.35)))
        for y in range(h):
            mask.putpixel((x, y), min(a, round(200 * min(1.0, (h - y) / (h * 0.5)))))
    img.paste(Image.new("RGB", (w, h), BG), (x0, y0), mask)
