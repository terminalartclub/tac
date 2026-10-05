"""Pixel mode for /tac:wall: each frame drawn as a real image, the way the site's previews look, in terminals
that show images (iTerm2: OSC 1337 inline images; kitty and Ghostty: the kitty graphics protocol). Every other
terminal, a multiplexer, a probe that gets no answer or any error: the cell mode in wall.py, unchanged.

    detect()            which protocol, confirmed by asking the terminal (kitty: a graphics query; iTerm2:
                        ReportCellSize), or None
    Pixels.frame()      one frame: the image (or None: draw cells), placed centred in the picture rows
    Pixels.cleanup()    what to write before giving the terminal back (kitty: delete our images)

The image bytes are only ever ours: rasterised here from the frames already checked by wallframes.load (the
published renderer's geometry: 13x28 px a cell, half blocks as two 13x14 pixels), resized with Lanczos like the
preview, encoded locally. Nothing from the server is decoded as an image.
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import select
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any

import wallframes

ITERM, KITTY = "iterm", "kitty"
CELL_W, CELL_H = 13, 28  # the published raster (vscreen.CELL_W/CELL_H): 80x66 cells -> 1040x1848 px
MAX_PIXELS = 80 * 13 * 66 * 28  # never render more than a published 80x66 frame; the terminal scales up
MAX_GLYPH_CELLS = 2 * 80 * 66  # a frame with glyphs is rasterised with fonts only up to this size; past it, cells
CACHE_CAP = 64 * 1024 * 1024  # encoded frames kept per piece and size (base64 chars); 80x66 at ~1 MP: ~35 MiB
SETTLE_S = 0.1  # vscreen.RESIZE_SETTLE_S: a drag sends a burst of size changes; re-render once it has held
PROBE_S = 0.5
KITTY_IDS = (7301, 7302)  # two images, double-buffered: place the new one, then delete the old: no flicker
GROUND = (8, 8, 15)
JPEG_Q = 90  # iTerm2: JPEG (2 ms vs PNG's 8 ms at 952x1691); the site's previews are lossy WebP q70
ITERM_MAX = 1_040_000  # iTerm2 drops an OSC 1337 over 1,048,576 bytes; base64 plus a < 100-byte header
CHUNK = 4096  # kitty: base64 per escape, a multiple of 4


# ── which terminal ──────────────────────────────────────────────────────────


def guess(env: dict) -> str | None:
    """The protocol this terminal says it speaks, before asking it. Multiplexers swallow image escapes: none."""
    if env.get("TMUX") or env.get("STY") or env.get("TERM", "").startswith(("screen", "tmux")):
        return None
    if env.get("TERM_PROGRAM") == "iTerm.app":
        return ITERM
    if env.get("TERM_PROGRAM") == "ghostty" or env.get("KITTY_WINDOW_ID") or env.get("TERM") in (
            "xterm-kitty", "xterm-ghostty"):
        return KITTY
    return None


KITTY_PROBE = b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\\x1b[16t\x1b[c"
ITERM_PROBE = b"\x1b]1337;ReportCellSize\x07\x1b[c"
DA1 = re.compile(rb"\x1b\[\?[0-9;]*c")
KITTY_OK = re.compile(rb"\x1b_Gi=31;OK\x1b\\")
CELL_PX = re.compile(rb"\x1b\[6;(\d+);(\d+)t")
NUM = rb"(\d+(?:\.\d+)?)"
ITERM_CELL = re.compile(rb"\x1b\]1337;ReportCellSize=" + NUM + b";" + NUM + b"(?:;" + NUM + rb")?(?:\x07|\x1b\\)")


def ask(fd_in: int, fd_out: int, query: bytes, timeout: float = PROBE_S) -> bytes:
    """Write a query, read the answers until the DA1 reply that every terminal sends (so a terminal that ignores
    the query costs one round trip, not the timeout). Echo and line buffering off meanwhile, then restored."""
    import termios

    old = termios.tcgetattr(fd_in)
    raw = termios.tcgetattr(fd_in)
    raw[3] &= ~(termios.ICANON | termios.ECHO)
    raw[6][termios.VMIN], raw[6][termios.VTIME] = 0, 0
    termios.tcsetattr(fd_in, termios.TCSANOW, raw)
    buf = b""
    try:
        os.write(fd_out, query)
        end = time.monotonic() + timeout
        while not DA1.search(buf) and len(buf) < 4096:
            left = end - time.monotonic()
            if left <= 0 or not select.select([fd_in], [], [], left)[0]:
                break
            chunk = os.read(fd_in, 1024)
            if not chunk:
                break
            buf += chunk
    finally:
        termios.tcsetattr(fd_in, termios.TCSANOW, old)
    return buf


def winsize(fd: int) -> tuple[int, int, int, int]:
    """(cols, rows, x pixels, y pixels) of the terminal; pixels 0 when it doesn't say."""
    import fcntl
    import termios

    rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return cols, rows, xp, yp


def _px(w: float, h: float) -> tuple[float, float] | None:
    """A cell size in device pixels, each side clamped to 1..512; None unless both are positive."""
    if not (w > 0 and h > 0):
        return None
    return min(512.0, max(1.0, w)), min(512.0, max(1.0, h))


def cell_px(proto: str, fd_in: int, fd_out: int) -> tuple[float, float] | None:
    """Device pixels a cell, confirming the protocol on the way, or None (not confirmed: cells). iTerm2: its
    ReportCellSize (points x scale: sharp on a Retina screen), else the window size's pixel fields; kitty: the
    graphics query must answer OK, then the window size's pixel fields, else CSI 16 t."""
    cols, rows, xp, yp = winsize(fd_out)
    if proto == ITERM:
        m = ITERM_CELL.search(ask(fd_in, fd_out, ITERM_PROBE))
        if not m:
            return None
        h, w, scale = float(m[1]), float(m[2]), float(m[3] or 1)
        return _px(w * scale, h * scale)
    reply = ask(fd_in, fd_out, KITTY_PROBE)
    if not KITTY_OK.search(reply):
        return None
    if xp and yp and cols and rows:
        return _px(xp / cols, yp / rows)
    m = CELL_PX.search(reply)
    return _px(int(m[2]), int(m[1])) if m else None


# ── layout and raster ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Layout:
    """Where the image goes (0-based cell x, y; ncols x nrows cells) and what it holds: a canvas_w x canvas_h
    image, ground colour, with the picture (fit_w x fit_h) at (off_x, off_y)."""

    x: int
    y: int
    ncols: int
    nrows: int
    canvas_w: int
    canvas_h: int
    fit_w: int
    fit_h: int
    off_x: int
    off_y: int


def layout(cols: int, rows: int, pane_cols: int, pane_rows: int, cw: float, ch: float) -> Layout | None:
    """The piece (cols x rows cells, 13x28 px each) fitted to the picture rows (all but the last, the bar),
    aspect kept, centred; the image is cell-aligned, the picture centred in it to the pixel. Rendered at most
    MAX_PIXELS (the terminal scales it to its cells)."""
    pic = pane_rows - 1
    if pane_cols < 2 or pic < 2 or cw <= 0 or ch <= 0:
        return None
    w0, h0 = cols * CELL_W, rows * CELL_H
    s = min(pane_cols * cw / w0, pic * ch / h0)
    fw, fh = w0 * s, h0 * s
    ncols, nrows = min(pane_cols, math.ceil(fw / cw - 1e-6)), min(pic, math.ceil(fh / ch - 1e-6))
    bw, bh = ncols * cw, nrows * ch
    k = min(1.0, math.sqrt(MAX_PIXELS / (bw * bh)))
    cw_, ch_ = max(1, round(bw * k)), max(1, round(bh * k))
    fw_, fh_ = max(1, min(cw_, round(fw * k))), max(1, min(ch_, round(fh * k)))
    return Layout((pane_cols - ncols) // 2, (pic - nrows) // 2, ncols, nrows, cw_, ch_, fw_, fh_,
                  (cw_ - fw_) // 2, (ch_ - fh_) // 2)


UPPER, LOWER, FULL = 0x2580, 0x2584, 0x2588
HALVES = frozenset((UPPER, LOWER, FULL, wallframes.SPACE))


def _halves(cur: Any, cols: int, rows: int) -> Any:
    """A frame of half blocks as its cols x 2*rows pixel image, else None."""
    from array import array

    from PIL import Image

    cps, fgs, bgs = cur[0::3], cur[1::3], cur[2::3]
    if cps.count(UPPER) == len(cps):
        top, bot = fgs, bgs
    elif HALVES.issuperset(cps):
        top = array("I", (f if cp in (UPPER, FULL) else b for cp, f, b in zip(cps, fgs, bgs)))
        bot = array("I", (f if cp in (LOWER, FULL) else b for cp, f, b in zip(cps, fgs, bgs)))
    else:
        return None
    px = array("I")
    for y in range(rows):
        px.extend(top[y * cols:(y + 1) * cols])
        px.extend(bot[y * cols:(y + 1) * cols])
    if sys.byteorder == "big":
        px.byteswap()  # 0x00RRGGBB as little-endian bytes: B G R 0
    return Image.frombytes("RGB", (cols, 2 * rows), px.tobytes(), "raw", "BGRX")


_RAS: list[Any] = []


def _glyphs(cur: Any, cols: int, rows: int) -> Any:
    """Any other frame through the published rasterizer (fonts and all), up to MAX_GLYPH_CELLS; else None."""
    if cols * rows > MAX_GLYPH_CELLS:
        return None
    import vscreen

    if not _RAS:
        _RAS.append(vscreen.Rasterizer())
    grid = [[vscreen.Cell(wallframes.char(cur[3 * i]), wallframes.unrgb(cur[3 * i + 1]),
                          wallframes.unrgb(cur[3 * i + 2]), False) for i in range(y * cols, (y + 1) * cols)]
            for y in range(rows)]
    return _RAS[0].frame(grid)


def picture(cur: Any, cols: int, rows: int, lay: Layout) -> Any:
    """The frame as lay.canvas_w x lay.canvas_h RGB: the piece rasterised (half blocks as 13x14 px pixels,
    integer-scaled no further than the fit needs), Lanczos to the fit like the site's previews, on the ground."""
    from PIL import Image

    im = _halves(cur, cols, rows)
    if im is not None:
        mx = max(1, min(CELL_W, math.ceil(lay.fit_w / cols)))
        my = max(1, min(CELL_H // 2, math.ceil(lay.fit_h / (2 * rows))))
        im = im.resize((cols * mx, 2 * rows * my), Image.Resampling.NEAREST)
    else:
        im = _glyphs(cur, cols, rows)
        if im is None:
            return None
    if im.size != (lay.fit_w, lay.fit_h):
        im = im.resize((lay.fit_w, lay.fit_h), Image.Resampling.LANCZOS)
    if (lay.fit_w, lay.fit_h) == (lay.canvas_w, lay.canvas_h):
        return im
    canvas = Image.new("RGB", (lay.canvas_w, lay.canvas_h), GROUND)
    canvas.paste(im, (lay.off_x, lay.off_y))
    return canvas


# ── the protocols ───────────────────────────────────────────────────────────


def encode(proto: str, im: Any) -> str:
    """The image as base64: JPEG for iTerm2 (lower quality until it fits ITERM_MAX; "" if none does: cells),
    PNG (zlib level 1) for kitty (its protocol takes PNG or raw)."""
    if proto != ITERM:
        b = io.BytesIO()
        im.save(b, "PNG", compress_level=1)
        return base64.standard_b64encode(b.getvalue()).decode("ascii")
    for q in (JPEG_Q, 75, 55):
        b = io.BytesIO()
        im.save(b, "JPEG", quality=q)
        s = base64.standard_b64encode(b.getvalue()).decode("ascii")
        if len(s) <= ITERM_MAX:
            return s
    return ""


def iterm_image(b64: str, ncols: int, nrows: int, aspect: bool = False) -> str:
    """OSC 1337 File=: inline, sized in cells (the terminal maps them to its pixels), at the cursor."""
    size = len(b64) * 3 // 4 - b64[-2:].count("=")
    return (f"\x1b]1337;File=inline=1;size={size};width={ncols};height={nrows};"
            f"preserveAspectRatio={int(aspect)}:{b64}\x07")


def kitty_image(b64: str, image_id: int, ncols: int, nrows: int, place: bool = True) -> str:
    """Transmit (and place: a=T) a PNG as image `image_id`, in CHUNK-sized escapes, quiet (q=2), the cursor left
    where it was (C=1), over ncols x nrows cells."""
    head = f"a={'T' if place else 't'},f=100,i={image_id},q=2" + (f",p=1,c={ncols},r={nrows},C=1" if place else "")
    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)] or [""]
    out = []
    for n, part in enumerate(parts):
        more = int(n < len(parts) - 1)
        out.append(f"\x1b_G{head},m={more};{part}\x1b\\" if n == 0 else f"\x1b_Gm={more};{part}\x1b\\")
    return "".join(out)


def kitty_delete(image_id: int) -> str:
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"  # I: the placements and the image data


# ── the player's side ───────────────────────────────────────────────────────


class Pixels:
    """Draws frames as images for one protocol. frame() returns the whole write for a frame (clearing on a new
    layout), "" while a resize settles, or None when this frame can't be an image (the caller draws cells)."""

    def __init__(self, proto: str, cell: tuple[float, float], fd_in: int | None = None, fd_out: int | None = None,
                 clock: Any = time.monotonic, size: Any = None) -> None:
        self.proto, self.cell, self.fd_in, self.fd_out, self.clock = proto, cell, fd_in, fd_out, clock
        self.size = size or (lambda: winsize(fd_out))  # () -> (cols, rows, xpix, ypix)
        self.seen: tuple | None = None  # the window size (cols, rows, xpix, ypix) last seen, and since when
        self.since = 0.0
        self.drawn: tuple | None = None  # the window size the current layout is for
        self.lay: tuple | None = None  # ((piece cols, rows, cell size), Layout) on screen now
        self.cache: dict[tuple, str] = {}
        self.cache_bytes = 0
        self.cache_for: Any = None
        self.flip = 0
        self.broken = False

    def _settled(self) -> tuple | None:
        """The window size to draw at, or None while a change of it hasn't held SETTLE_S yet."""
        raw = self.size()
        now = self.clock()
        if raw != self.seen:
            self.seen, self.since = raw, now
        if self.drawn is not None and raw != self.drawn and now - self.since < SETTLE_S:
            return None
        return raw

    def _remeasure(self, raw: tuple) -> None:
        """The cell size, again, when the window changed (a font size change changes it too)."""
        cols, rows, xp, yp = raw
        cell = None
        if self.proto == KITTY and xp and yp and cols and rows:
            cell = _px(xp / cols, yp / rows)
        elif self.proto == ITERM and self.fd_in is not None and self.fd_out is not None:
            m = ITERM_CELL.search(ask(self.fd_in, self.fd_out, ITERM_PROBE, 0.2))
            if m:
                cell = _px(float(m[2]) * float(m[3] or 1), float(m[1]) * float(m[3] or 1))
        if cell:
            self.cell = cell

    def frame(self, cur: Any, cols: int, rows: int, index: int, piece: Any, credit: Any) -> str | None:
        import wall

        if self.broken:
            return None
        raw = self._settled()
        if raw is None:
            return ""
        pc, pr = raw[0], max(2, raw[1])
        out = []
        try:
            if self.drawn is not None and raw != self.drawn:
                self._remeasure(raw)
            relay = self.lay is None or raw != self.drawn or self.lay[0] != (cols, rows, self.cell)
            lay = layout(cols, rows, pc, pr, *self.cell) if relay else None
        except Exception:  # noqa: BLE001 - an odd probe answer or tty: cells from now on
            self.broken = True
            return None
        if relay:
            if lay is None:
                return None
            out.append(self.clear(pr))
            self.lay, self.drawn = ((cols, rows, self.cell), lay), raw
        lay = self.lay[1]
        if self.cache_for != (piece, self.lay[0], raw):
            self.cache, self.cache_bytes, self.cache_for = {}, 0, (piece, self.lay[0], raw)
        b64 = self.cache.get(index)
        if b64 is None:
            try:
                im = picture(cur, cols, rows, lay)
                if im is None:
                    return None
                b64 = encode(self.proto, im)
            except Exception:  # noqa: BLE001 - any failure: cells from now on, never a broken pane
                self.broken = True
                return None
            if self.cache_bytes + len(b64) <= CACHE_CAP:
                self.cache[index] = b64
                self.cache_bytes += len(b64)
        if not b64:  # too big for iTerm2 even at the lowest quality: this frame as cells
            return None
        out.append(f"\x1b[{lay.y + 1};{lay.x + 1}H")
        if self.proto == ITERM:
            out.append(iterm_image(b64, lay.ncols, lay.nrows))
        else:
            new, old = KITTY_IDS[self.flip], KITTY_IDS[1 - self.flip]
            self.flip = 1 - self.flip
            out.append(kitty_image(b64, new, lay.ncols, lay.nrows) + kitty_delete(old))
        out.append(wall.credit_row(credit, pc, pr))
        return "".join(out)

    def drop(self, pane_rows: int) -> str:
        """Leaving pixels for cells (a frame that can't be an image, a skip note): our images off the screen."""
        if self.lay is None:
            return ""
        self.lay = self.drawn = None
        return self.clear(pane_rows)

    def clear(self, pane_rows: int) -> str:
        """The picture rows back to the ground (a new piece, a new size), our images gone."""
        import wall

        out = ["".join(kitty_delete(i) for i in KITTY_IDS) if self.proto == KITTY else ""]
        out.extend(f"\x1b[{r + 1};1H{wall.GROUND_SGR}\x1b[K" for r in range(max(1, pane_rows)))
        return "".join(out)

    def cleanup(self) -> str:
        """Before the terminal is given back: kitty keeps images until deleted; iTerm2's leave with the alt
        screen."""
        if self.proto != KITTY:
            return ""
        return "".join(kitty_delete(i) for i in KITTY_IDS)


def detect(env: dict | None = None, fd_in: int | None = None, fd_out: int | None = None) -> Pixels | None:
    """Pixel mode if this terminal speaks a protocol we have and confirms it; else None (cells)."""
    env = os.environ if env is None else env
    proto = guess(env)
    if proto is None or fd_in is None or fd_out is None:
        return None
    try:
        if not (os.isatty(fd_in) and os.isatty(fd_out)):
            return None
        import PIL  # noqa: F401 - pixel mode rasterises with Pillow; without it, cells

        cell = cell_px(proto, fd_in, fd_out)
    except Exception:  # noqa: BLE001 - no answer, no Pillow, an odd tty: cells
        return None
    if cell is None:
        return None
    return Pixels(proto, cell, fd_in, fd_out)
