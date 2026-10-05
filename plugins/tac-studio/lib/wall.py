"""/tac:wall: this week's wall, piece after piece, in a terminal pane. Never anyone else's code: the platform
renders every piece in its isolated render VM and serves the frames as data (wallframes.py); this plays them.

    tacctl wall        fetch the playlist (GET /v1/wall.json), cache it, open the pane running `tacctl wall-play`
    tacctl wall-play   in the pane: each piece's frames fetched when it comes up, the next one while it plays
                       (never the whole wall up front), played for --seconds, credited, then the next; loops

Only the API base talks to us (no server-sent URL is ever fetched: the frames URL is built from a validated
handle and slug), redirects are refused, every body is size-capped before it's parsed, and frames are fully
checked (wallframes.load) before they're cached or played. Cache: ~/.cache/tac/wall/ (private, 128 MiB, least
recently played first). Offline: the cached playlist, cached pieces only.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from array import array
from pathlib import Path
from typing import Any, NamedTuple

import wallframes

HANDLE_RE = re.compile(r"[a-z0-9-]{2,24}")
SLUG_RE = re.compile(r"(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*")
ETAG_RE = re.compile(r'"[0-9a-f]{32}"')
TEXT_RE = re.compile(r"[^\x00-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁩﻿]{0,120}")
MAX_PLAYLIST = 256 * 1024
MAX_PIECES = 200
SOURCES = ("week", "week+recent", "picks", "recent")  # the platform's fallback order: week -> picks -> recent
CACHE_CAP = 128 * 1024 * 1024
TIMEOUT_S = 10  # per socket operation
DEADLINE_S = 30  # per request, whole: a server dripping bytes can't hold a piece up longer
OFFLINE_MAX_AGE_S = 7 * 86400  # offline, a cached piece last confirmed by the platform longer ago isn't replayed
STALE_TMP_S = 600
GROUND = (8, 8, 15)
CREDIT_FG = (150, 150, 160)  # the credit's ink: readable on the ground, quieter than the art


class WallError(Exception):
    pass


# ── network ────────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # noqa: ANN401
        return None  # a 3xx is an error here: we only ever talk to the API base


_OPENER = urllib.request.build_opener(_NoRedirect)


class Gone(WallError):
    """404/410: the piece is no longer on the wall (unpublished, hidden, taken down)."""


def get(url: str, cap: int, etag: str | None = None) -> tuple[int, bytes, str | None]:
    """(status, body, etag). 304 when etag still matches. Reads at most cap + 1 bytes (more is an error) within
    DEADLINE_S overall, in chunks, each socket operation at most TIMEOUT_S."""
    headers = {"Accept": "application/json, application/gzip", "User-Agent": "tac-wall"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    deadline = time.monotonic() + DEADLINE_S
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            status, tag = r.status, r.headers.get("ETag")
            body = bytearray()
            while len(body) <= cap:
                if time.monotonic() > deadline:
                    raise WallError(f"too slow: over {DEADLINE_S} s")
                chunk = r.read1(min(65536, cap + 1 - len(body)))  # what has arrived: read() would wait for all n
                if not chunk:
                    break
                body += chunk
            body = bytes(body)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, b"", etag
        if e.code in (404, 410):
            raise Gone(f"HTTP {e.code}") from None
        raise WallError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise WallError(f"cannot reach the platform ({getattr(e, 'reason', e)})") from None
    if len(body) > cap:
        raise WallError(f"response over {cap // 1024} KiB")
    return status, body, tag


def _text(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) and TEXT_RE.fullmatch(v) else default


def parse_playlist(body: bytes) -> dict:
    """The playlist, every field checked: names that can reach a path, text that reaches the screen, numbers
    the player sizes things by. A bad entry is dropped, never repaired."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WallError("the playlist isn't JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("pieces"), list):
        raise WallError("the playlist has no pieces list")
    pieces = []
    for p in doc["pieces"][:MAX_PIECES]:
        if not isinstance(p, dict):
            continue
        h, s, f = p.get("handle"), p.get("slug"), p.get("frames")
        if not (isinstance(h, str) and HANDLE_RE.fullmatch(h) and isinstance(s, str) and SLUG_RE.fullmatch(s)):
            continue
        if not (isinstance(f, dict) and isinstance(f.get("bytes"), int) and 0 < f["bytes"] <= wallframes.MAX_GZ):
            continue
        etag = p.get("etag") if isinstance(p.get("etag"), str) and ETAG_RE.fullmatch(p["etag"]) else None
        pieces.append({"handle": h, "slug": s, "title": _text(p.get("title"), s),
                       "model": _text(p.get("model_label")) or _text(p.get("model")), "bytes": f["bytes"],
                       "etag": etag})
    source = doc.get("source") if doc.get("source") in SOURCES else "week"
    return {"week": _text(doc.get("week")), "source": source, "pieces": pieces}


def fetch_playlist(base: str, picks: bool) -> dict:
    status, body, _ = get(f"{base}/v1/wall.json" + ("?picks=1" if picks else ""), MAX_PLAYLIST)
    if status != 200:
        raise WallError(f"HTTP {status}")
    return parse_playlist(body)


# ── cache ──────────────────────────────────────────────────────────────────


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tac" / "wall"


def private_dir(path: Path) -> Path:
    """`path` as a real directory of ours, 0700: never a symlink (a planted link would aim writes elsewhere)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise WallError(f"{path} is not a directory of yours; refusing to use it")
    os.chmod(path, 0o700)
    return path


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)  # atomic: a reader sees the old file or the new one


def _read(path: Path, cap: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        data = fh.read(cap + 1)
    return data if len(data) <= cap else None


class Cache:
    def __init__(self, root: Path | None = None, cap: int = CACHE_CAP) -> None:
        self.root = private_dir(root or cache_root())
        self.cap = cap
        self.lock = threading.Lock()

    def _paths(self, p: dict) -> tuple[Path, Path]:
        stem = f"{p['handle']}--{p['slug']}"  # both validated: no separator or dot can get in
        return self.root / f"{stem}.cells.gz", self.root / f"{stem}.json"

    def save_playlist(self, doc: dict, base: str, picks: bool = False) -> None:
        """In the server's own shape, so reading it back goes through the same parse_playlist checks. A fresh
        playlist is also the takedown list: every cached piece it no longer lists is deleted now."""
        pieces = [{"handle": p["handle"], "slug": p["slug"], "title": p["title"], "model_label": p["model"],
                   "frames": {"bytes": p["bytes"]}, "etag": p["etag"]} for p in doc["pieces"]]
        _write(self.root / "playlist.json", json.dumps({"week": doc["week"], "source": doc["source"],
                                                        "pieces": pieces, "api": base, "picks": picks}).encode())
        self.drop_unlisted(doc["pieces"])

    def load_playlist(self) -> dict | None:
        raw = _read(self.root / "playlist.json", MAX_PLAYLIST)
        if raw is None:
            return None
        try:
            doc = parse_playlist(raw)
            extra = json.loads(raw)
            api, picks = extra.get("api"), extra.get("picks")
        except (WallError, ValueError, AttributeError):
            return None
        return {**doc, "api": api if isinstance(api, str) else None, "picks": picks is True}

    def drop_unlisted(self, listed: list[dict]) -> int:
        """Delete every cached piece not in `listed` (it left the wall: unpublished, hidden, taken down)."""
        keep = {self._paths(p)[0].name for p in listed}
        n = 0
        with self.lock:
            for f in self.root.glob("*.cells.gz"):
                if f.name not in keep:
                    self._remove(f)
                    n += 1
        return n

    def drop(self, p: dict) -> None:
        with self.lock:
            self._remove(self._paths(p)[0])

    @staticmethod
    def _remove(f: Path) -> None:
        f.unlink(missing_ok=True)
        f.with_name(f.name.replace(".cells.gz", ".json")).unlink(missing_ok=True)

    def fresh(self, p: dict, max_age: float = OFFLINE_MAX_AGE_S) -> bool:
        """Confirmed by the platform (downloaded or revalidated) within max_age seconds."""
        raw = _read(self._paths(p)[1], 4096)
        try:
            at = json.loads(raw or b"null").get("checked")
        except (ValueError, AttributeError):
            return False
        return isinstance(at, (int, float)) and 0 <= time.time() - at <= max_age

    def _note(self, p: dict, etag: str | None, size: int) -> None:
        _write(self._paths(p)[1], json.dumps({"etag": etag, "bytes": size, "checked": time.time()}).encode())

    def cached(self, p: dict) -> bytes | None:
        data, meta = self._paths(p)
        gz = _read(data, wallframes.MAX_GZ)
        if gz is None:
            return None
        try:
            wallframes.load(gz)
        except wallframes.BadFrames:
            return None
        return gz

    def etag(self, p: dict) -> str | None:
        raw = _read(self._paths(p)[1], 4096)
        try:
            tag = json.loads(raw or b"null").get("etag")
        except (ValueError, AttributeError):
            return None
        return tag if isinstance(tag, str) and ETAG_RE.fullmatch(tag) else None

    def fetch(self, base: str, p: dict) -> bytes:
        """The piece's frames: revalidated (If-None-Match) when cached, else downloaded; checked before saved. A
        404/410 (taken down) deletes the cached copy and raises Gone."""
        have = self.cached(p)
        try:
            status, body, tag = get(f"{base}/v1/pieces/{p['handle']}/{p['slug']}/frames", wallframes.MAX_GZ,
                                    self.etag(p) if have else None)
        except Gone:
            self.drop(p)
            raise
        if status == 304 and have is not None:
            with self.lock:
                self._note(p, self.etag(p), len(have))
            self.touch(p)
            return have
        if status != 200:
            raise WallError(f"HTTP {status}")
        try:
            wallframes.load(body)
        except wallframes.BadFrames as e:
            raise WallError(f"the frames are not playable: {e}") from None
        data, _ = self._paths(p)
        with self.lock:
            _write(data, body)
            self._note(p, tag if isinstance(tag, str) and ETAG_RE.fullmatch(tag) else None, len(body))
            self.prune(keep={data})
        return body

    def touch(self, p: dict) -> None:
        try:
            os.utime(self._paths(p)[0])
        except OSError:
            pass

    def prune(self, keep: set[Path] = frozenset()) -> None:  # type: ignore[assignment]
        """Least recently played first, until the cache is under its cap; `keep` never goes. Also leftover
        temp files of an interrupted write."""
        for t in self.root.glob(".*.tmp"):
            try:
                if time.time() - t.lstat().st_mtime > STALE_TMP_S:
                    t.unlink()
            except OSError:
                pass
        files = []
        for f in self.root.glob("*.cells.gz"):
            st = f.lstat()
            if stat.S_ISREG(st.st_mode):
                files.append((st.st_mtime, st.st_size, f))
        total = sum(s for _, s, _ in files)
        for _, size, f in sorted(files):
            if total <= self.cap:
                break
            if f in keep:
                continue
            self._remove(f)
            total -= size


# ── drawing ────────────────────────────────────────────────────────────────


def _sgr(fg: int, bg: int) -> str:
    return (f"\x1b[0;38;2;{(fg >> 16) & 255};{(fg >> 8) & 255};{fg & 255};"
            f"48;2;{(bg >> 16) & 255};{(bg >> 8) & 255};{bg & 255}m")


GROUND_SGR = _sgr(wallframes.rgb(GROUND), wallframes.rgb(GROUND))
CREDIT_SGR = _sgr(wallframes.rgb(CREDIT_FG), wallframes.rgb(GROUND))  # never GROUND_SGR's ink: that's invisible
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J"
LEAVE = "\x1b]8;;\x1b\\\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l"  # closes any hyperlink first


def _blocks(n: int, m: int) -> list[tuple[int, int]]:
    """n source cells into m (<= n) target cells: each target's run [a, b) of sources, every source in one."""
    return [(t * n // m, (t + 1) * n // m) for t in range(m)]


STAND_OUT = (2.0, 16)  # x times the first cell's distance, plus a floor (luma units: 0..5100 a cell)


def _pick(vals: list[int], a: int, b: int) -> int:
    """The index in [a, b) to show. The first (plain sampling) unless another stands out from the local ground
    (the mean of the two cells just outside the run) by clearly more (STAND_OUT): a one-cell mast or line is
    kept, while the edge of a wide shape, which stands out from that ground about as much either way, stays
    put (no ragged edges, no shimmer). Hot (tens of thousands of calls a frame): plain loops, no key funcs."""
    if b - a == 1:
        return a
    if a > 0 and b < len(vals):
        ground = (vals[a - 1] + vals[b]) / 2
    elif a > 0:
        ground = vals[a - 1]
    elif b < len(vals):
        ground = vals[b]
    else:
        ground = sum(vals[a:b]) / (b - a)
    first = far = abs(vals[a] - ground)
    best = a
    for j in range(a + 1, b):
        d = abs(vals[j] - ground)
        if d > far:  # ties keep the earlier
            best, far = j, d
    return best if far > STAND_OUT[0] * first + STAND_OUT[1] else a


def _lum(cp: int, fg: int, bg: int) -> int:
    """How bright a cell looks (both halves, luma-weighted): a space shows only its bg, a full block its fg."""
    f = 3 * ((fg >> 16) & 255) + 6 * ((fg >> 8) & 255) + (fg & 255)
    b = 3 * ((bg >> 16) & 255) + 6 * ((bg >> 8) & 255) + (bg & 255)
    return 2 * b if cp == 0x20 else 2 * f if cp == 0x2588 else f + b


def fit(cur: Any, cols: int, rows: int, ow: int, oh: int) -> list[list[int]]:
    """The source cell (index into the frame) for each of oh x ow target cells, when the picture is scaled down.
    Each target cell stands for a run of 1-2 source cells per axis (columns first, then rows): _pick keeps a
    one-cell feature that plain sampling would drop (it dropped every 5th column at 80x66 into 64x53, and
    beacon-2's mast with it)."""
    lum = [_lum(cur[3 * i], cur[3 * i + 1], cur[3 * i + 2]) for i in range(cols * rows)]
    bx, by = _blocks(cols, ow), _blocks(rows, oh)
    across = [[y * cols + _pick(lum[y * cols:(y + 1) * cols], a, b) for a, b in bx] for y in range(rows)]
    grid = [[0] * ow for _ in range(oh)]
    for x in range(ow):
        down = [lum[across[y][x]] for y in range(rows)]
        for t, (a, b) in enumerate(by):
            grid[t][x] = across[_pick(down, a, b)][x]
    return grid


UPPER, LOWER, FULL = 0x2580, 0x2584, 0x2588
HALVES = frozenset((UPPER, LOWER, FULL, wallframes.SPACE))  # cells that are just two pixels, top and bottom


def fit_pixels(cur: Any, cols: int, rows: int, ow: int, oh: int) -> list[list[tuple[int, int, int]]] | None:
    """fit() at twice the vertical resolution, for a frame of half blocks (every cell UPPER, LOWER, FULL or a
    space): the cells as a cols x 2*rows image, scaled down with the same _pick per pixel column and pixel row,
    repacked as UPPER cells (fg the top pixel, bg the bottom one). A one-pixel line (half a cell) survives;
    cell rows would have kept a whole cell or dropped it. None for any other frame (glyphs need fit())."""
    cps = cur[0::3]
    fgs, bgs = cur[1::3], cur[2::3]
    if cps.count(UPPER) == len(cps):  # the usual frame: no per-cell work to unpack it
        top, bot = fgs, bgs
    elif HALVES.issuperset(cps):
        top = [f if cp in (UPPER, FULL) else b for cp, f, b in zip(cps, fgs, bgs)]
        bot = [f if cp in (LOWER, FULL) else b for cp, f, b in zip(cps, fgs, bgs)]
    else:
        return None
    img = []  # pixel rows
    for y in range(rows):
        img.append(top[y * cols:(y + 1) * cols])
        img.append(bot[y * cols:(y + 1) * cols])
    lum = [[2 * (3 * (c >> 16 & 255) + 6 * (c >> 8 & 255) + (c & 255)) for c in row] for row in img]  # 2x: _lum's units
    bx, by = _blocks(cols, ow), _blocks(2 * rows, 2 * oh)
    across = [[_pick(lrow, a, b) for a, b in bx] for lrow in lum]  # source column, per pixel row and target col
    out = [[0] * ow for _ in range(2 * oh)]
    for x in range(ow):
        down = [lum[py][across[py][x]] for py in range(2 * rows)]
        for t, (a, b) in enumerate(by):
            py = _pick(down, a, b)
            out[t][x] = img[py][across[py][x]]
    return [[(UPPER, out[2 * t][x], out[2 * t + 1][x]) for x in range(ow)] for t in range(oh)]


def _presample(cur: Any, cols: int, rows: int, ow: int, oh: int) -> tuple[Any, int, int]:
    """Past 2x down, every other cell of a big frame is work _pick can't use: sample it (cell centres) to at most
    2x the target first, so the cost follows the pane, not the frame (512x256 into 250x70: 131k cells -> 38k)."""
    sc, sr = min(cols, 2 * ow), min(rows, 2 * oh)
    if (sc, sr) == (cols, rows):
        return cur, cols, rows
    xs = [(2 * j + 1) * cols // (2 * sc) for j in range(sc)]
    sub = array("I")
    for j in range(sr):
        base = 3 * ((2 * j + 1) * rows // (2 * sr)) * cols
        row = cur[base:base + 3 * cols]
        for x in xs:
            sub.extend(row[3 * x:3 * x + 3])
    return sub, sc, sr


_LAST: list[Any] = [None]  # the last frame scaled: (cols, rows, ow, oh, a copy of it, its cells)


def scaled(cur: Any, cols: int, rows: int, ow: int, oh: int) -> list[list[tuple[int, int, int]]]:
    """The oh x ow cells (codepoint, fg, bg) to draw: the frame itself at its own size; scaled down, by pixels
    when it is all half blocks (fit_pixels), else by cells (fit). A frame unchanged since the last call (a delta
    with no spans: a still piece, a held pose) reuses the last result."""
    last = _LAST[0]
    if last is not None and last[:4] == (cols, rows, ow, oh) and last[4] == cur:
        return last[5]
    if (ow, oh) == (cols, rows):
        cells = [[(cur[3 * i], cur[3 * i + 1], cur[3 * i + 2]) for i in range(y * cols, (y + 1) * cols)]
                 for y in range(rows)]
    else:
        src, c, r = _presample(cur, cols, rows, ow, oh)
        cells = fit_pixels(src, c, r, ow, oh) or [
            [(src[3 * k], src[3 * k + 1], src[3 * k + 2]) for k in row] for row in fit(src, c, r, ow, oh)]
    _LAST[0] = (cols, rows, ow, oh, array("I", cur), cells)
    return cells


def frame_ansi(cur: Any, cols: int, rows: int, pane_cols: int, pane_rows: int, credit: Credit | str) -> str:
    """One whole frame for the pane: the picture scaled down (scaled(): one factor on both axes, thin features
    kept) to fit above the credit bar (the last row, always) when the pane is smaller, centred; every row by
    absolute address and cleared to its end, so a resize or a terminal's reflow can never leave stale cells."""
    pic_rows = max(1, pane_rows - 1)
    s = min(1.0, pane_cols / cols, pic_rows / rows)
    ow, oh = max(1, int(cols * s)), max(1, int(rows * s))
    x_off, y_off = max(0, (pane_cols - ow) // 2), max(0, (pic_rows - oh) // 2)
    cells = scaled(cur, cols, rows, ow, oh)
    out = ["\x1b[H"]
    for r in range(pic_rows):
        out.append(f"\x1b[{r + 1};1H{GROUND_SGR}")
        y = r - y_off
        if 0 <= y < oh:
            out.append(" " * x_off)
            last = None
            for cp, fg, bg in cells[y]:
                if (fg, bg) != last:
                    out.append(_sgr(fg, bg))
                    last = fg, bg
                out.append(wallframes.char(cp))  # never chr() of a raw codepoint: no escapes on the terminal
            out.append(GROUND_SGR)
        out.append("\x1b[K")
    out.append(credit_row(credit, pane_cols, pane_rows))
    return "".join(out)


def _clean(s: str) -> str:
    """Only characters the playlist sanitiser (TEXT_RE) allows, minus marks and format characters (they'd
    throw off the width)."""
    return "".join(ch for ch in s if TEXT_RE.fullmatch(ch) and unicodedata.category(ch)[0] not in "MC")


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _trunc(s: str, n: int) -> str:
    """s in at most n columns, cut with an ellipsis."""
    if _width(s) <= n:
        return s
    out, w = "", 0
    for ch in s:
        cw = _width(ch)
        if w + cw > n - 1:
            break
        out, w = out + ch, w + cw
    return out + "…" if n >= 1 else ""


SITE = "https://terminalart.club"
WORDMARK = (("terminal", (0x00, 0xE5, 0xC3)), ("art", (0xA7, 0x8B, 0xFA)), ("club", (0xF4, 0x72, 0xB6)))
SHORTMARK = tuple((word[0], rgb) for word, rgb in WORDMARK)  # "tac", same colours
GAP = 2  # columns at least between the credit and the wordmark


def _mark_text(mark: tuple) -> str:
    return " ".join(w for w, _ in mark) if mark is WORDMARK else "".join(w for w, _ in mark)


def _osc8(url: str | None, text: str) -> str:
    """text as an OSC 8 hyperlink (iTerm2, Ghostty, kitty; other terminals show the text), opened and closed in
    the same write. url is only ever built here from regex-checked names (Credit), never taken from the server."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\" if url else text


class Credit(NamedTuple):
    """The bottom bar: the attribution (CC BY 4.0: it must always show) on the left, the club's wordmark on the
    right, each a link where the terminal supports it."""

    title: str
    handle: str  # bare, as the playlist validated it (HANDLE_RE); kept whole in any pane wide enough for it
    slug: str = ""
    model: str = ""
    extra: tuple[str, ...] = ()  # i/n, offline, a skip note

    def handle_url(self) -> str | None:
        return f"{SITE}/@{self.handle}" if HANDLE_RE.fullmatch(self.handle) else None

    def piece_url(self) -> str | None:
        ok = HANDLE_RE.fullmatch(self.handle) and SLUG_RE.fullmatch(self.slug)
        return f"{SITE}/@{self.handle}/{self.slug}" if ok else None

    def layout(self, width: int) -> tuple[list[str], tuple[tuple[str, tuple[int, int, int]], ...]]:
        """(the left parts, the mark) for this many columns. Narrower: the model goes, then the title (cut first,
        then gone), then i/n, then the mark shortens to "tac", then it goes; "@handle" is cut only when it alone
        doesn't fit."""
        at = f"@{_clean(self.handle)}" if self.handle else ""
        head = [at] if at else []
        title = [self.title] if self.title else []
        model = [self.model] if self.model else []
        extra = list(self.extra)

        def fits(parts: list[str], mark: tuple) -> bool:
            return _width(" · ".join(parts)) + GAP + len(_mark_text(mark)) <= width

        for mark, parts in ((WORDMARK, title + head + model + extra), (WORDMARK, title + head + extra)):
            if fits(parts, mark):
                return parts, mark
        room = width - GAP - len(_mark_text(WORDMARK)) - _width(" · ".join([""] + head + extra))
        if title and room >= 4:
            return [_trunc(self.title, room)] + head + extra, WORDMARK
        for mark, parts in ((WORDMARK, head + extra), (SHORTMARK, head + extra), (SHORTMARK, head)):
            if fits(parts, mark):
                return parts, mark
        return [_trunc(at, width)], ()  # the mark goes before the handle is ever cut: attribution wins

    def fit(self, width: int) -> str:
        """The bar as plain text, for tests and widths."""
        parts, mark = self.layout(width)
        left, right = " · ".join(parts), _mark_text(mark)
        return left + " " * (width - _width(left) - len(right)) + right if right else left


def credit_line(p: dict, i: int, n: int, offline: bool) -> Credit:
    return Credit(_clean(p["title"]), p["handle"], p.get("slug", ""), _clean(p.get("model") or ""),
                  (f"{i + 1}/{n}",) + (("offline",) if offline else ()))


def credit_row(credit: Credit | str, pane_cols: int, pane_rows: int) -> str:
    """The pane's last row, which the art never uses: the credit in readable ink at the left, the wordmark at the
    right, every hyperlink closed before the row ends."""
    if not isinstance(credit, Credit):
        credit = Credit(_clean(credit), "")
    parts, mark = credit.layout(pane_cols)
    at = f"@{_clean(credit.handle)}" if credit.handle else None
    left = " · ".join(_osc8(credit.handle_url(), x) if x == at else x for x in parts)
    out = [f"\x1b[{pane_rows};1H{GROUND_SGR}\x1b[K{CREDIT_SGR}{left}"]
    if mark:
        out.append(f"\x1b[{pane_rows};{pane_cols - len(_mark_text(mark)) + 1}H")
        url = credit.piece_url()
        inner = []
        for k, (w, rgb) in enumerate(mark):
            sep = " " if mark is WORDMARK and k else ""
            inner.append(f"{_sgr(wallframes.rgb(rgb), wallframes.rgb(GROUND))}{sep}{w}")
        out.append(_osc8(url, "".join(inner)))
    out.append(f"{GROUND_SGR}\x1b[0m")
    return "".join(out)


def pane_size() -> tuple[int, int]:
    import shutil

    t = shutil.get_terminal_size()
    return max(1, t.columns), max(2, t.lines)


# ── playing ────────────────────────────────────────────────────────────────


class _Stop(Exception):
    pass


def playable_offline(doc: dict, cache: Cache) -> list[dict]:
    """Offline: only pieces cached and confirmed by the platform within the last OFFLINE_MAX_AGE_S (a piece
    taken down while we were offline stops replaying within a week)."""
    return [p for p in doc["pieces"] if cache.cached(p) is not None and cache.fresh(p)]


def play(doc: dict, base: str | None, cache: Cache, seconds: float, offline: bool,
         out: Any = None, clock: Any = time.monotonic, sleep: Any = time.sleep, rounds: int | None = None,
         picks: bool = False) -> int:
    """Plays the playlist until Ctrl-C (or `rounds` passes, for tests). Each piece: its frames from the cache or
    the platform (the next one fetched in the background meanwhile), `seconds` of it (0: one loop), then the
    next. Each piece is revalidated at most once a session; the playlist is fetched again every lap, and a
    piece it no longer lists (or that answers 404) is dropped from the session and the cache. A piece that
    can't be fetched or played is skipped with a note; none playable at all: exit 1."""
    out = out or sys.stdout
    pieces = list(doc["pieces"]) if not offline else playable_offline(doc, cache)
    if not pieces:
        print("tac: nothing on the wall to play" + (" (offline, and nothing cached from the last 7 days)"
                                                    if offline else ""), file=sys.stderr)
        return 1

    online = bool(base) and not offline
    pending: dict[tuple[str, str], Any] = {}  # the one piece fetching ahead, by (handle, slug)
    checked: set[tuple[str, str]] = set()  # revalidated with the platform this session
    gone: set[tuple[str, str]] = set()  # answered 404/410: taken down, skipped for the rest of the session

    def key(p: dict) -> tuple[str, str]:
        return p["handle"], p["slug"]

    def load(p: dict) -> bytes | None:
        """From the platform (once a session per piece), else whatever the cache holds. None when gone."""
        if online and key(p) not in checked:
            try:
                data = cache.fetch(base, p)
                checked.add(key(p))
                return data
            except Gone:
                gone.add(key(p))
                return None
            except WallError:
                pass  # the network failed: whatever we have
        return None if key(p) in gone else cache.cached(p)

    def prefetch(p: dict) -> None:
        if key(p) in pending or key(p) in gone:
            return
        box: dict = {}
        t = threading.Thread(target=lambda: box.update(data=load(p)), daemon=True)
        t.start()
        pending[key(p)] = (t, box)

    def take(p: dict) -> bytes | None:
        """The piece now: its prefetch if done; a cached copy rather than waiting on a slow network; else fetched
        now. Never longer than one request's DEADLINE_S."""
        if key(p) in gone:
            return None
        have = cache.cached(p)
        if key(p) in pending:
            t, box = pending.pop(key(p))
            t.join(DEADLINE_S + 1 if have is None else 0.5)
            if t.is_alive():
                return have
            return None if key(p) in gone else box.get("data") or have
        return have if have is not None else load(p)

    def start_refresh() -> tuple[threading.Thread, dict] | None:
        """The playlist again, in the background while the lap plays (never a pause on a slow network)."""
        if not online:
            return None
        box: dict = {}

        def run() -> None:
            try:
                box["doc"] = fetch_playlist(base, picks)
            except WallError:
                pass

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, box

    def next_lap(job: tuple[threading.Thread, dict] | None) -> list[dict]:
        """A lap is done: the fresh playlist if it arrived (pieces it no longer lists leave the session and the
        cache: save_playlist drops them), else this one; taken-down pieces (gone) leave either way."""
        current = [p for p in pieces if key(p) not in gone]
        if job is None or job[0].is_alive() or "doc" not in job[1]:
            return current
        fresh = job[1]["doc"]
        cache.save_playlist(fresh, base, picks)
        return [p for p in fresh["pieces"] if key(p) not in gone]

    def on_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise _Stop

    old = {sig: signal.signal(sig, on_stop) for sig in (signal.SIGTERM, signal.SIGHUP)}
    played = failed = 0
    out.write(ENTER)
    out.flush()
    try:
        lap = 0
        while rounds is None or lap < rounds:
            if not pieces:
                raise WallError("no piece left on the wall")
            job = start_refresh()
            for j, p in enumerate(pieces):
                data = take(p)
                prefetch(pieces[(j + 1) % len(pieces)])  # downloads while this one plays: never more than one ahead
                try:
                    h, w = wallframes.load(data) if data is not None else (None, None)
                except wallframes.BadFrames:
                    h = None
                if h is None:
                    failed += 1
                    pc, pr = pane_size()
                    note = "taken off the wall" if key(p) in gone else "couldn't load it; skipping"
                    out.write(f"\x1b[H{GROUND_SGR}\x1b[2J"
                              + credit_row(Credit(_clean(p["title"]), p["handle"], p["slug"], extra=(note,)), pc, pr))
                    out.flush()
                    if failed >= len(pieces) and played == 0:
                        raise WallError("no piece on the wall could be loaded")
                    sleep(1.5)
                    continue
                played += 1
                cache.touch(p)
                credit = credit_line(p, j, len(pieces), offline)
                length = seconds if seconds > 0 else h.frames / h.fps
                start = clock()
                k = 0
                it = wallframes.frames(h, w)
                while clock() - start < length:
                    try:
                        cur = next(it)
                    except StopIteration:
                        it = wallframes.frames(h, w)  # loop the piece
                        cur = next(it)
                    pc, pr = pane_size()
                    out.write(frame_ansi(cur, h.cols, h.rows, pc, pr, credit))
                    out.flush()
                    k += 1
                    delay = start + k / h.fps - clock()
                    if delay > 0:
                        sleep(min(delay, max(0.0, length - (clock() - start))))
            lap += 1
            pieces = next_lap(job)
        return 0
    except (KeyboardInterrupt, _Stop):
        return 0
    finally:
        for sig, h0 in old.items():
            signal.signal(sig, h0)
        out.write(LEAVE)
        out.flush()
        for t, _ in pending.values():  # a prefetch still running: give it a moment, never hold up the exit
            t.join(0.05)
