"""The wall's frames: a piece's rendered frames as terminal cells, made in the render VM and played by
`tacctl wall`. Plain data, never code: nobody runs anyone else's piece to watch the wall.

Stdlib only. This file is copied byte for byte to platform/src/tac_platform/wallframes.py (the API
validates what the render VM wrote with the same code the plugin plays it with; a test keeps the two equal).

    file = gzip( header, frame, frame, ... )        at most MAX_GZ gzipped, MAX_RAW inflated
    header = "TACW", version, cols, rows, fps, frames, loop_ms, 0      eight little-endian u32 (32 bytes)
    frame  = KEY n cell*n                            n == cols*rows: the whole screen
           | DELTA n (start len cell*len)*n          only the runs of cells that changed
    cell   = codepoint, fg 0xRRGGBB, bg 0xRRGGBB     u32 each; a printable BMP character one column wide

Frame 0 is a key. Every count, span and length is checked against the header before the first frame plays
(scan): a file that lies about its sizes is refused, never read past its end, never looped on.
"""

from __future__ import annotations

import struct
import sys
import unicodedata
import zlib
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

MAGIC = b"TACW"
VERSION = 1
HEADER = struct.Struct("<4s7I")
KEY, DELTA = 0, 1
MAX_GZ = 8 * 1024 * 1024
MAX_RAW = 32 * 1024 * 1024
MAX_COLS, MAX_ROWS = 512, 256
MAX_FPS = 30
MAX_FRAMES = MAX_FPS * 600  # ten minutes at 30 fps
FPS_STEPS = (15, 10, 5)  # over MAX_RAW at its own frame rate, a piece is sampled at the next of these
SPACE = 0x20
FALLBACK = 0xB7  # '·' for anything a terminal can't draw in one column
GROUND = (8, 8, 15)  # vscreen.BG


class BadFrames(ValueError):
    """The file isn't a well-formed frames file within the limits."""


class TooBig(Exception):
    """The frames pass MAX_RAW at this frame rate."""


@dataclass(frozen=True)
class Header:
    cols: int
    rows: int
    fps: int
    frames: int
    loop_ms: int

    @property
    def cells(self) -> int:
        return self.cols * self.rows


# ── cells ──────────────────────────────────────────────────────────────────


def glyph(ch: str) -> int:
    """The codepoint a terminal cell can show: printable, one column, in the BMP; else FALLBACK."""
    if ch == "":  # the filler after a wide character
        return SPACE
    if len(ch) != 1:
        return FALLBACK
    cp = ord(ch)
    if cp > 0xFFFF or unicodedata.category(ch)[0] in "CZ" and ch != " ":
        return FALLBACK
    if unicodedata.east_asian_width(ch) in ("W", "F") or unicodedata.category(ch)[0] == "M":
        return FALLBACK  # wide, or a mark (Mn/Mc/Me) that draws onto its neighbour
    return cp


_SAFE: dict[int, str] = {}


def char(cp: int) -> str:
    """The character to draw for a cell's codepoint, from a file that may not be ours: anything glyph() would
    not have written (a control or escape character, a wide, combining or invisible one, a surrogate, a value
    past U+FFFF) is drawn as FALLBACK. So no file can put an escape sequence on the terminal."""
    hit = _SAFE.get(cp)
    if hit is None:
        ok = 0x20 <= cp <= 0xFFFF and not 0xD800 <= cp <= 0xDFFF and glyph(chr(cp)) == cp
        hit = _SAFE[cp] = chr(cp) if ok else chr(FALLBACK)
    return hit


def rgb(c: Any) -> int:
    """0xRRGGBB, each channel clamped to 0..255 (a piece can hand Rich an out-of-range colour)."""
    r, g, b = (min(255, max(0, int(x))) for x in c)
    return (r << 16) | (g << 8) | b


def unrgb(v: int) -> tuple[int, int, int]:
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def pack(grid: list[list[Any]], cols: int, rows: int) -> array:
    """vscreen.to_cells' grid (cells with .ch .fg .bg) as cols*rows*3 words; short grids padded with ground."""
    words = array("I")
    blank = (SPACE, rgb(GROUND), rgb(GROUND))
    for y in range(rows):
        row = grid[y] if y < len(grid) else []
        for x in range(cols):
            if x < len(row):
                c = row[x]
                words.extend((glyph(c.ch), rgb(c.fg), rgb(c.bg)))
            else:
                words.extend(blank)
    return words


def _spans(prev: array, cur: array) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = -1
    for i in range(len(cur) // 3):
        same = prev[3 * i] == cur[3 * i] and prev[3 * i + 1] == cur[3 * i + 1] and prev[3 * i + 2] == cur[3 * i + 2]
        if not same and start < 0:
            start = i
        elif same and start >= 0:
            out.append((start, i - start))
            start = -1
    if start >= 0:
        out.append((start, len(cur) // 3 - start))
    return out


def encode_frame(prev: array | None, cur: array) -> array:
    n = len(cur) // 3
    key = array("I", (KEY, n))
    key.extend(cur)
    if prev is None:
        return key
    delta = array("I", (DELTA, 0))
    runs = _spans(prev, cur)
    delta[1] = len(runs)
    for s, ln in runs:
        delta.extend((s, ln))
        delta.extend(cur[3 * s:3 * (s + ln)])
    return delta if len(delta) < len(key) else key


def _le(words: array) -> bytes:
    if sys.byteorder == "big":
        words = array("I", words)
        words.byteswap()
    return words.tobytes()


class Encoder:
    """Frames at a fixed frame rate from a stream of samples (sample(t, grid): the grid shown from virtual time
    t until the next sample), as vscreen.capture's on_sample gives them. TooBig past `limit` raw bytes."""

    def __init__(self, cols: int, rows: int, fps: int, limit: int | None = None) -> None:
        if not (1 <= cols <= MAX_COLS and 1 <= rows <= MAX_ROWS and 1 <= fps <= MAX_FPS):
            raise ValueError("cols, rows or fps out of range")
        self.cols, self.rows, self.fps, self.limit = cols, rows, fps, MAX_RAW if limit is None else limit
        self.prev: tuple[float, array] | None = None
        self.last: array | None = None
        self.k = 0
        self.body = bytearray()

    def _emit_until(self, t_end: float) -> None:
        assert self.prev is not None
        cur = self.prev[1]
        while self.k / self.fps < t_end - 1e-6:
            self.body += _le(encode_frame(self.last, cur))
            self.last = cur
            self.k += 1
            if HEADER.size + len(self.body) > self.limit or self.k > MAX_FRAMES:
                raise TooBig(f"over {self.limit // 2**20} MiB at {self.fps} fps")

    def sample(self, t: float, grid: list[list[Any]]) -> None:
        if self.prev is not None:
            self._emit_until(t)
        self.prev = (t, pack(grid, self.cols, self.rows))

    def finish(self, loop_s: float) -> bytes:
        """The gzipped file. At least one frame."""
        if self.prev is None:
            raise ValueError("no samples")
        self._emit_until(max(loop_s, 1 / self.fps))
        head = HEADER.pack(MAGIC, VERSION, self.cols, self.rows, self.fps, self.k, round(loop_s * 1000), 0)
        return zlib.compress(head + bytes(self.body), 6, wbits=31)  # wbits 31: a gzip member


# ── reading (everything from the network or disk goes through here) ────────────────────────────────


def inflate(gz: bytes, limit: int | None = None) -> bytes:
    """One gzip member, at most MAX_GZ in and `limit` out, read in bounded steps: a small file that inflates
    to gigabytes is refused after limit + 1 bytes, never held whole."""
    limit = MAX_RAW if limit is None else limit
    if len(gz) > MAX_GZ:
        raise BadFrames(f"over {MAX_GZ // 2**20} MiB")
    d = zlib.decompressobj(wbits=31)
    try:
        raw = d.decompress(gz, limit + 1)
    except zlib.error as e:
        raise BadFrames(f"not gzip: {e}") from None
    if len(raw) > limit or d.unconsumed_tail:
        raise BadFrames(f"inflates past {limit // 2**20} MiB")
    if not d.eof:
        raise BadFrames("truncated")
    if d.unused_data:
        raise BadFrames("trailing data after the gzip member")
    return raw


def _words(raw: bytes) -> array:
    body = raw[HEADER.size:]
    if len(body) % 4:
        raise BadFrames("not whole u32 words")
    w = array("I")
    w.frombytes(body)
    if sys.byteorder == "big":
        w.byteswap()
    return w


def header(raw: bytes) -> Header:
    if len(raw) < HEADER.size:
        raise BadFrames("no header")
    magic, version, cols, rows, fps, frames, loop_ms, zero = HEADER.unpack_from(raw)
    if magic != MAGIC or version != VERSION or zero != 0:
        raise BadFrames("not a TACW v1 frames file")
    if not (1 <= cols <= MAX_COLS and 1 <= rows <= MAX_ROWS):
        raise BadFrames("size out of range")
    if not (1 <= fps <= MAX_FPS and 1 <= frames <= MAX_FRAMES and 1 <= loop_ms <= 3_600_000):
        raise BadFrames("fps, frame count or loop out of range")
    return Header(cols, rows, fps, frames, loop_ms)


def scan(w: array, h: Header) -> None:
    """Every frame's kind, counts and bounds, against the header, before anything plays: one pass over the
    headers and spans (O(spans)), no cell copied. The first frame is a key; the count is exact."""
    n, off, seen = h.cells, 0, 0
    end = len(w)
    while off < end:
        if off + 2 > end:
            raise BadFrames("a frame header runs past the end")
        kind, count = w[off], w[off + 1]
        off += 2
        if kind == KEY:
            if count != n:
                raise BadFrames("a key frame of the wrong size")
            off += 3 * n
            if off > end:
                raise BadFrames("a key frame runs past the end")
        elif kind == DELTA:
            if seen == 0:
                raise BadFrames("the first frame is not a key")
            if count > n:
                raise BadFrames("more spans than cells")
            total = 0
            for _ in range(count):
                if off + 2 > end:
                    raise BadFrames("a span header runs past the end")
                start, ln = w[off], w[off + 1]
                off += 2
                total += ln
                if ln > n or start > n - ln or total > n:
                    raise BadFrames("a span outside the screen")
                off += 3 * ln
                if off > end:
                    raise BadFrames("a span runs past the end")
        else:
            raise BadFrames(f"unknown frame kind {kind}")
        seen += 1
        if seen > h.frames:
            raise BadFrames("more frames than the header says")
    if seen != h.frames:
        raise BadFrames("fewer frames than the header says")


def load(gz: bytes) -> tuple[Header, array]:
    """Inflate, parse and scan: (header, words) of a file that is safe to play, else BadFrames."""
    raw = inflate(gz)
    h = header(raw)
    w = _words(raw)
    scan(w, h)
    return h, w


def frames(h: Header, w: array) -> Iterator[array]:
    """The screen after each frame, in order (one buffer, updated in place). Only for scanned words."""
    cur = array("I", bytes(4 * 3 * h.cells))
    off = 0
    for _ in range(h.frames):
        kind, count = w[off], w[off + 1]
        off += 2
        if kind == KEY:
            cur[:] = w[off:off + 3 * count]
            off += 3 * count
        else:
            for _ in range(count):
                start, ln = w[off], w[off + 1]
                off += 2
                cur[3 * start:3 * (start + ln)] = w[off:off + 3 * ln]
                off += 3 * ln
        yield cur
