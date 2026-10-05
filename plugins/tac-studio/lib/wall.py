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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import wallframes

HANDLE_RE = re.compile(r"[a-z0-9-]{2,24}")
SLUG_RE = re.compile(r"(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*")
ETAG_RE = re.compile(r'"[0-9a-f]{32}"')
TEXT_RE = re.compile(r"[^\x00-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁩﻿]{0,120}")
MAX_PLAYLIST = 256 * 1024
MAX_PIECES = 200
CACHE_CAP = 128 * 1024 * 1024
TIMEOUT_S = 10
GROUND = (8, 8, 15)
FG = (204, 204, 204)


class WallError(Exception):
    pass


# ── network ────────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:  # noqa: ANN401
        return None  # a 3xx is an error here: we only ever talk to the API base


_OPENER = urllib.request.build_opener(_NoRedirect)


def get(url: str, cap: int, etag: str | None = None) -> tuple[int, bytes, str | None]:
    """(status, body, etag). 304 when etag still matches. Reads at most cap + 1 bytes: more is an error."""
    headers = {"Accept": "application/json, application/gzip", "User-Agent": "tac-wall"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            body = r.read(cap + 1)
            status, tag = r.status, r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, b"", etag
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
    source = doc.get("source") if doc.get("source") in ("week", "picks") else "week"
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

    def save_playlist(self, doc: dict, base: str) -> None:
        """In the server's own shape, so reading it back goes through the same parse_playlist checks."""
        pieces = [{"handle": p["handle"], "slug": p["slug"], "title": p["title"], "model_label": p["model"],
                   "frames": {"bytes": p["bytes"]}, "etag": p["etag"]} for p in doc["pieces"]]
        _write(self.root / "playlist.json", json.dumps({"week": doc["week"], "source": doc["source"],
                                                        "pieces": pieces, "api": base}).encode())

    def load_playlist(self) -> dict | None:
        raw = _read(self.root / "playlist.json", MAX_PLAYLIST)
        if raw is None:
            return None
        try:
            doc = parse_playlist(raw)
            api = json.loads(raw).get("api")
        except (WallError, ValueError, AttributeError):
            return None
        return {**doc, "api": api if isinstance(api, str) else None}

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
        """The piece's frames: revalidated (If-None-Match) when cached, else downloaded; checked before saved."""
        have = self.cached(p)
        status, body, tag = get(f"{base}/v1/pieces/{p['handle']}/{p['slug']}/frames", wallframes.MAX_GZ,
                                self.etag(p) if have else None)
        if status == 304 and have is not None:
            self.touch(p)
            return have
        if status != 200:
            raise WallError(f"HTTP {status}")
        try:
            wallframes.load(body)
        except wallframes.BadFrames as e:
            raise WallError(f"the frames are not playable: {e}") from None
        data, meta = self._paths(p)
        with self.lock:
            _write(data, body)
            _write(meta, json.dumps({"etag": tag if isinstance(tag, str) and ETAG_RE.fullmatch(tag) else None,
                                     "bytes": len(body)}).encode())
            self.prune(keep={data})
        return body

    def touch(self, p: dict) -> None:
        try:
            os.utime(self._paths(p)[0])
        except OSError:
            pass

    def prune(self, keep: set[Path] = frozenset()) -> None:  # type: ignore[assignment]
        """Least recently played first, until the cache is under its cap; `keep` never goes."""
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
            f.unlink(missing_ok=True)
            f.with_name(f.name.replace(".cells.gz", ".json")).unlink(missing_ok=True)
            total -= size


# ── drawing ────────────────────────────────────────────────────────────────


def _sgr(fg: int, bg: int) -> str:
    return (f"\x1b[0;38;2;{(fg >> 16) & 255};{(fg >> 8) & 255};{fg & 255};"
            f"48;2;{(bg >> 16) & 255};{(bg >> 8) & 255};{bg & 255}m")


GROUND_SGR = _sgr((GROUND[0] << 16) | (GROUND[1] << 8) | GROUND[2], (GROUND[0] << 16) | (GROUND[1] << 8) | GROUND[2])
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J"
LEAVE = "\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l"


def frame_ansi(cur: Any, cols: int, rows: int, pane_cols: int, pane_rows: int, credit: str) -> str:
    """One whole frame for the pane: the picture scaled down (nearest cell, one factor on both axes) to fit
    above a one-line credit when the pane is smaller, centred; every row by absolute address and cleared to its
    end, so a resize or a terminal's reflow can never leave stale cells."""
    pic_rows = max(1, pane_rows - 1)
    s = min(1.0, pane_cols / cols, pic_rows / rows)
    ow, oh = max(1, int(cols * s)), max(1, int(rows * s))
    x_off, y_off = max(0, (pane_cols - ow) // 2), max(0, (pic_rows - oh) // 2)
    xs = [min(cols - 1, int(x / s)) for x in range(ow)]
    out = ["\x1b[H"]
    for r in range(pic_rows):
        out.append(f"\x1b[{r + 1};1H{GROUND_SGR}")
        y = r - y_off
        if 0 <= y < oh:
            sy = min(rows - 1, int(y / s))
            out.append(" " * x_off)
            last = None
            base = 3 * sy * cols
            for sx in xs:
                i = base + 3 * sx
                key = (cur[i + 1], cur[i + 2])
                if key != last:
                    out.append(_sgr(*key))
                    last = key
                out.append(wallframes.char(cur[i]))  # never chr() of a raw codepoint: no escapes on the terminal
            out.append(GROUND_SGR)
        out.append("\x1b[K")
    out.append(f"\x1b[{pane_rows};1H{GROUND_SGR}\x1b[2m{credit[:pane_cols]}\x1b[0m{GROUND_SGR}\x1b[K\x1b[0m")
    return "".join(out)


def credit_line(p: dict, i: int, n: int, offline: bool) -> str:
    parts = [p["title"], f"@{p['handle']}"] + ([p["model"]] if p["model"] else []) + [f"{i + 1}/{n}"]
    return " · ".join(parts) + ("  (offline: cached pieces only)" if offline else "")


def pane_size() -> tuple[int, int]:
    import shutil

    t = shutil.get_terminal_size()
    return max(1, t.columns), max(2, t.lines)


# ── playing ────────────────────────────────────────────────────────────────


class _Stop(Exception):
    pass


def play(doc: dict, base: str | None, cache: Cache, seconds: float, offline: bool,
         out: Any = None, clock: Any = time.monotonic, sleep: Any = time.sleep, rounds: int | None = None) -> int:
    """Plays the playlist until Ctrl-C (or `rounds` passes, for tests). Each piece: its frames from the cache or
    the platform (the next one fetched in the background meanwhile), `seconds` of it (0: one loop), then the
    next. A piece that can't be fetched or played is skipped with a note; none playable at all: exit 1."""
    out = out or sys.stdout
    pieces = doc["pieces"] if not offline else [p for p in doc["pieces"] if cache.cached(p) is not None]
    if not pieces:
        print("tac: nothing on the wall to play" + (" (offline, and nothing cached)" if offline else ""),
              file=sys.stderr)
        return 1

    pending: dict[int, Any] = {}
    online = bool(base) and not offline

    def load(i: int) -> bytes | None:
        """From the platform (revalidating a cached copy), else whatever the cache holds."""
        p = pieces[i % len(pieces)]
        if online:
            try:
                return cache.fetch(base, p)
            except WallError:
                pass  # the network failed: whatever we have
        return cache.cached(p)

    def prefetch(i: int) -> None:
        if i in pending:
            return
        box: dict = {}
        t = threading.Thread(target=lambda: box.update(data=load(i)), daemon=True)
        t.start()
        pending[i] = (t, box)

    def take(i: int) -> bytes | None:
        """The piece now: its prefetch if done; a cached copy rather than waiting on a slow network; else
        fetched now (at most the request timeout)."""
        have = cache.cached(pieces[i % len(pieces)])
        if i in pending:
            t, box = pending.pop(i)
            t.join(None if have is None else 0.5)
            if not t.is_alive():
                return box.get("data") or have
            return have
        return have if have is not None else load(i)

    def on_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise _Stop

    old = {sig: signal.signal(sig, on_stop) for sig in (signal.SIGTERM, signal.SIGHUP)}
    played = failed = 0
    out.write(ENTER)
    out.flush()
    try:
        i = 0
        while rounds is None or i < rounds * len(pieces):
            p = pieces[i % len(pieces)]
            data = take(i)
            prefetch(i + 1)  # the next piece downloads while this one plays: never more than one ahead
            try:
                h, w = wallframes.load(data) if data is not None else (None, None)
            except wallframes.BadFrames:
                h = None
            if h is None:
                failed += 1
                pc, pr = pane_size()
                out.write(f"\x1b[H{GROUND_SGR}\x1b[2J\x1b[{pr};1H\x1b[2mcouldn't load {p['title']} · @{p['handle']};"
                          f" skipping\x1b[0m")
                out.flush()
                if failed >= len(pieces) and played == 0:
                    raise WallError("no piece on the wall could be loaded")
                sleep(1.5)
                i += 1
                continue
            played += 1
            cache.touch(p)
            credit = credit_line(p, i % len(pieces), len(pieces), offline)
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
            i += 1
        return 0
    except (KeyboardInterrupt, _Stop):
        return 0
    finally:
        for sig, h0 in old.items():
            signal.signal(sig, h0)
        out.write(LEAVE)
        out.flush()
