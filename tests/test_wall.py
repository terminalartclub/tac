"""/tac:wall: the frames format, the network and cache, the player, and the commands. A fake platform on
loopback; no prod host, no window, no one's code."""

import gzip
import http.server
import io
import json
import os
import struct
import threading
import zlib
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

import render_piece
import tacctl
import termwin
import wall
import wallframes


def grid_for(k: int, cols: int = 8, rows: int = 4, fg=(230, 120, 30)):
    return [[SimpleNamespace(ch="0123456789"[k % 10] if (x, y) == (0, 0) else "▀", fg=fg, bg=(8, 8, 15))
             for x in range(cols)] for y in range(rows)]


def film(n: int = 3, cols: int = 8, rows: int = 4, fps: int = 10, fg=(230, 120, 30)) -> bytes:
    enc = wallframes.Encoder(cols, rows, fps)
    for k in range(n):
        enc.sample(k / fps, grid_for(k, cols, rows, fg))
    return enc.finish(n / fps)


# ── the format ─────────────────────────────────────────────────────────────


def test_round_trip_keys_and_deltas():
    h, w = wallframes.load(film(5))
    assert (h.cols, h.rows, h.fps, h.frames, h.loop_ms) == (8, 4, 10, 5, 500)
    firsts = [chr(f[0]) for f in wallframes.frames(h, w)]
    assert firsts == ["0", "1", "2", "3", "4"]
    raw = wallframes.inflate(film(5))
    assert len(raw) < 32 + 5 * (2 + 3 * 32) * 4  # deltas: one changed cell a frame, not five key frames


def test_out_of_range_colours_are_clamped_not_a_crash():
    """chlorine (683) and wake (14) hand Rich channels outside 0..255: the render-side encoder clamps."""
    h, w = wallframes.load(film(2, fg=(300, -5, 20)))
    cur = next(wallframes.frames(h, w))
    assert wallframes.unrgb(cur[1]) == (255, 0, 20)


def _raw(cols, rows, fps, frames, body_words) -> bytes:
    head = wallframes.HEADER.pack(b"TACW", 1, cols, rows, fps, frames, 1000, 0)
    return head + struct.pack(f"<{len(body_words)}I", *body_words)


KEY = [0, 4] + [0x41, 0xFFFFFF, 0] * 4  # a 2x2 key frame


@pytest.mark.parametrize("words,frames,why", [
    (KEY + [1, 1, 999999, 1, 1, 1, 1], 2, "outside"),  # delta start past the end
    (KEY + [1, 1, 0, 0xFFFFFFF0], 2, "outside"),  # a huge span length
    (KEY + [1, 1 << 28], 2, "more spans than cells"),  # n = 2^28 with no data
    (KEY + [9, 0], 2, "unknown frame kind"),
    (KEY + [1], 2, "past the end"),  # truncated after the kind
    (KEY, 3, "fewer frames"),  # the header promises more
    (KEY + [1, 0], 1, "more frames"),  # and fewer
    ([1, 0] + KEY, 2, "not a key"),  # the first frame is a delta
    ([0, 5] + [0] * 15, 1, "wrong size"),  # a key frame of the wrong size
])
def test_hostile_files_are_refused_before_anything_plays(words, frames, why):
    gz = zlib.compress(_raw(2, 2, 10, frames, words), wbits=31)
    with pytest.raises(wallframes.BadFrames, match=why):
        wallframes.load(gz)


@pytest.mark.parametrize("gz,why", [
    (zlib.compress(bytes(40 * 2**20), 9, wbits=31), "inflates past"),  # a gzip bomb: 40 MiB of zeros
    (b"\x1f\x8b" + bytes(20), "not gzip"),
    (zlib.compress(_raw(2, 2, 10, 1, KEY), wbits=31)[:-6], "truncated"),
    (zlib.compress(_raw(2, 2, 10, 1, KEY), wbits=31) * 2, "trailing data"),
    (bytes(wallframes.MAX_GZ + 1), "over 8 MiB"),
    (zlib.compress(b"NOPE" + bytes(28), wbits=31), "not a TACW"),
    (zlib.compress(_raw(9999, 2, 10, 1, KEY), wbits=31), "size out of range"),
])
def test_bad_containers_are_refused(gz, why):
    with pytest.raises(wallframes.BadFrames, match=why):
        wallframes.load(gz)


def test_the_encoder_stops_past_its_cap():
    enc = wallframes.Encoder(8, 4, 10, limit=500)
    with pytest.raises(wallframes.TooBig):
        for k in range(50):
            enc.sample(k / 10, grid_for(k))
        enc.finish(5.0)


def test_render_drops_the_frame_rate_when_over_the_cap(tmp_path, monkeypatch):
    """Over MAX_RAW at the piece's own fps: another pass at 15, then 10, then 5; the piece always renders."""
    piece = tmp_path / "p"
    piece.mkdir()
    (piece / "piece.py").write_text(
        "for i in range(60):\n    canvas.clear()\n"
        "    canvas.write(Text(str(i % 10) * width))\n    await sleep(1 / 30)\n")
    out = tmp_path / "out"
    assert render_piece.worker(piece, out, 20, 6) == 0
    native = wallframes.load((out / "frames.cells.gz").read_bytes())[0]
    assert native.fps == 30
    raw30 = len(wallframes.inflate((out / "frames.cells.gz").read_bytes()))
    monkeypatch.setattr(wallframes, "MAX_RAW", raw30 * 2 // 3)  # 30 fps no longer fits; 15 does
    out2 = tmp_path / "out2"
    assert render_piece.worker(piece, out2, 20, 6) == 0
    assert wallframes.load((out2 / "frames.cells.gz").read_bytes())[0].fps == 15
    monkeypatch.setattr(wallframes, "MAX_RAW", 64)  # nothing fits: no frames file, the render still succeeds
    out3 = tmp_path / "out3"
    assert render_piece.worker(piece, out3, 20, 6) == 0
    assert not (out3 / "frames.cells.gz").exists() and (out3 / "preview.webp").exists()


# ── a fake platform on loopback ─────────────────────────────────────────────


class Platform:
    def __init__(self) -> None:
        self.pieces: dict[str, bytes] = {}
        self.order: list[dict] = []
        self.requests: list[str] = []
        self.redirect = False
        self.down = False
        platform = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                platform.requests.append(self.path)
                if platform.down:
                    self.send_error(503)
                    return
                if platform.redirect:
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example/x")
                    self.end_headers()
                    return
                if self.path.startswith("/v1/wall.json"):
                    body = json.dumps({"week": "2026-W41", "source": "week", "pieces": platform.order}).encode()
                    ctype, tag = "application/json", None
                else:
                    key = "/".join(self.path.split("/")[3:5])
                    body = platform.pieces.get(key)
                    if body is None:
                        self.send_error(404)
                        return
                    ctype = "application/gzip"
                    tag = '"' + __import__("hashlib").sha256(body).hexdigest()[:32] + '"'
                    if self.headers.get("If-None-Match") == tag:
                        self.send_response(304)
                        self.end_headers()
                        return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                if tag:
                    self.send_header("ETag", tag)
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, handle: str, slug: str, data: bytes, title: str = "A Piece") -> None:
        self.pieces[f"{handle}/{slug}"] = data
        self.order.append({"handle": handle, "slug": slug, "title": title, "model": "claude-opus-5-5",
                           "model_label": "Opus 5.5", "frames": {"bytes": len(data)}, "etag": None})

    def frames_requests(self) -> list[str]:
        return [r for r in self.requests if r.endswith("/frames")]


@pytest.fixture
def plat(monkeypatch, tmp_path):
    p = Platform()
    monkeypatch.setenv("TAC_API", p.base)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    yield p
    p.server.shutdown()


# ── playlist, network, cache ────────────────────────────────────────────────


def test_the_playlist_keeps_only_clean_entries():
    doc = {"pieces": [
        {"handle": "alex", "slug": "kettle", "title": "Kettle", "model_label": "Opus 5.5", "frames": {"bytes": 10}},
        {"handle": "../x", "slug": "kettle", "frames": {"bytes": 10}},  # a path in the handle
        {"handle": "bea", "slug": "a--b", "frames": {"bytes": 10}},  # not a slug
        {"handle": "cy", "slug": "ok", "title": "\x1b[2Jgotcha‮", "frames": {"bytes": 10}},  # escapes, bidi
        {"handle": "di", "slug": "big", "frames": {"bytes": wallframes.MAX_GZ + 1}},  # over the cap
        {"handle": "ed", "slug": "fine", "title": "x" * 500, "frames": {"bytes": 10}, "etag": "bogus"},
    ] + [{"handle": "zz", "slug": f"p{i}", "frames": {"bytes": 1}} for i in range(300)]}
    out = wall.parse_playlist(json.dumps(doc).encode())
    names = [(p["handle"], p["slug"]) for p in out["pieces"]]
    assert names[:3] == [("alex", "kettle"), ("cy", "ok"), ("ed", "fine")]
    assert out["pieces"][1]["title"] == "ok"  # the hostile title dropped for the slug
    assert out["pieces"][2]["title"] == "fine" and out["pieces"][2]["etag"] is None
    assert len(out["pieces"]) == 3 + wall.MAX_PIECES - 6  # the first 200 entries, bad ones dropped


def test_redirects_and_oversized_bodies_are_refused(plat):
    plat.redirect = True
    with pytest.raises(wall.WallError, match="HTTP 302"):
        wall.fetch_playlist(plat.base, False)
    assert len(plat.requests) == 1  # never followed to evil.example
    plat.redirect = False
    plat.order = [{"handle": "a" * 2, "slug": "x", "frames": {"bytes": 1}, "title": "y" * 1000}] * 400
    with pytest.raises(wall.WallError, match="over 256 KiB"):
        wall.fetch_playlist(plat.base, False)


def test_frames_are_checked_before_they_are_cached_or_played(plat, tmp_path):
    plat.add("alex", "bomb", zlib.compress(bytes(40 * 2**20), 9, wbits=31))
    plat.add("alex", "good", film())
    cache = wall.Cache(tmp_path / "c")
    with pytest.raises(wall.WallError, match="not playable"):
        cache.fetch(plat.base, {"handle": "alex", "slug": "bomb"})
    assert not list((tmp_path / "c").glob("alex--bomb*"))
    data = cache.fetch(plat.base, {"handle": "alex", "slug": "good"})
    assert cache.cached({"handle": "alex", "slug": "good"}) == data
    cache.fetch(plat.base, {"handle": "alex", "slug": "good"})  # revalidated: If-None-Match, a 304
    assert len(plat.frames_requests()) == 3


def test_the_cache_keeps_under_its_cap_least_recently_played_first(plat, tmp_path):
    for i in range(4):
        plat.add("alex", f"p{i}", film(30 + i))
    size = len(film(30))
    cache = wall.Cache(tmp_path / "c", cap=size * 2 + 10)
    for i in range(4):
        cache.fetch(plat.base, {"handle": "alex", "slug": f"p{i}"})
        os.utime(tmp_path / "c" / f"alex--p{i}.cells.gz", (1000 + i, 1000 + i))
    kept = sorted(f.name for f in (tmp_path / "c").glob("*.cells.gz"))
    assert kept == ["alex--p2.cells.gz", "alex--p3.cells.gz"]


def test_a_symlinked_cache_dir_is_refused(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "cache" / "tac").mkdir(parents=True)
    (tmp_path / "cache" / "tac" / "wall").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(wall.WallError, match="not a directory of yours"):
        wall.Cache(tmp_path / "cache" / "tac" / "wall")


# ── the player ─────────────────────────────────────────────────────────────


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(s, 0.001)


def test_lazy_fetch_never_more_than_one_ahead(plat, tmp_path, monkeypatch):
    for i in range(5):
        plat.add("alex", f"p{i}", film(3), title=f"Piece {i}")
    doc = wall.fetch_playlist(plat.base, False)
    cache = wall.Cache(tmp_path / "c")
    seen: list[tuple[int, int]] = []
    real = wall.frame_ansi

    def spy(*a, **k):
        seen.append((len(plat.frames_requests()), a[5].count("·")))
        return real(*a, **k)

    monkeypatch.setattr(wall, "frame_ansi", spy)
    monkeypatch.setattr(wall, "pane_size", lambda: (40, 10))
    out = io.StringIO()
    clock = Clock()
    assert wall.play(doc, plat.base, cache, 1.0, False, out=out, clock=clock, sleep=clock.sleep, rounds=1) == 0
    first_piece_fetches = seen[0][0]
    assert first_piece_fetches <= 2  # while piece 0 plays: piece 0, maybe piece 1, never 2..4
    assert plat.frames_requests()[:5] == [f"/v1/pieces/alex/p{i}/frames" for i in range(5)]  # in order, lazily
    text = out.getvalue()
    assert text.startswith(wall.ENTER) and text.endswith(wall.LEAVE)
    assert "Piece 0 · @alex · Opus 5.5 · 1/5" in text and "Piece 4 · @alex · Opus 5.5 · 5/5" in text


def test_offline_plays_only_what_is_cached(plat, tmp_path, monkeypatch):
    plat.add("alex", "a", film(3), title="Cached One")
    plat.add("alex", "b", film(3), title="Never Fetched")
    doc = wall.fetch_playlist(plat.base, False)
    cache = wall.Cache(tmp_path / "c")
    cache.fetch(plat.base, doc["pieces"][0])
    monkeypatch.setattr(wall, "pane_size", lambda: (100, 10))
    plat.down = True
    out, clock = io.StringIO(), Clock()
    assert wall.play(doc, None, cache, 0.5, True, out=out, clock=clock, sleep=clock.sleep, rounds=2) == 0
    assert "Cached One" in out.getvalue() and "Never Fetched" not in out.getvalue()
    assert "offline: cached pieces only" in out.getvalue()
    empty = wall.Cache(tmp_path / "empty")
    assert wall.play(doc, None, empty, 0.5, True, out=io.StringIO(), clock=clock, sleep=clock.sleep) == 1


def test_a_piece_that_fails_is_skipped_not_fatal(plat, tmp_path, monkeypatch):
    plat.add("alex", "gone", b"x")
    plat.add("alex", "ok", film(3), title="Still Plays")
    del plat.pieces["alex/gone"]  # 404 when its turn comes
    doc = wall.fetch_playlist(plat.base, False)
    monkeypatch.setattr(wall, "pane_size", lambda: (100, 10))
    out, clock = io.StringIO(), Clock()
    assert wall.play(doc, plat.base, wall.Cache(tmp_path / "c"), 0.5, False, out=out, clock=clock,
                     sleep=clock.sleep, rounds=1) == 0
    assert "couldn't load A Piece · @alex; skipping" in out.getvalue() and "Still Plays" in out.getvalue()


def test_frames_scale_down_to_fit_and_are_written_by_address():
    h, w = wallframes.load(film(1, cols=80, rows=66))
    cur = next(wallframes.frames(h, w))
    s = wall.frame_ansi(cur, 80, 66, 40, 34, "credit")
    import pyte

    screen = pyte.Screen(40, 34)
    pyte.ByteStream(screen).feed(s.encode())
    drawn = [ln.rstrip() for ln in screen.display]
    assert drawn[0] == "0" + "▀" * 39  # 80 columns into 40: every other cell, the whole width
    assert drawn[33] == "credit" and all(len(ln) <= 40 for ln in drawn)
    assert "\n" not in s and "\x1b[A" not in s


# ── the commands ───────────────────────────────────────────────────────────


@pytest.fixture
def opened(monkeypatch):
    calls = []

    def fake(argv, **kw):
        calls.append((argv, kw.get("where")))
        return termwin.Opened("iTerm", (60, 50), where="pane"), ""

    monkeypatch.setattr(termwin, "open_play_window", fake)
    return calls


def test_tac_wall_fetches_only_the_playlist_and_opens_the_pane(plat, opened, capsys):
    plat.add("alex", "kettle", film())
    assert tacctl.main(["wall", "--seconds", "20"]) == 0
    out = capsys.readouterr().out
    assert "playing this week's wall, 1 piece, 20 s each, in a pane on the right (iTerm)" in out
    argv, where = opened[0]
    assert argv[1:] == ["wall-play", "--seconds", "20"] and where == "split"
    assert plat.frames_requests() == []  # frames are the pane's job, one piece at a time
    assert all(termwin.SAFE_ARG.fullmatch(a) for a in argv)  # passes the same gate as /tac:play


def test_tac_wall_offline_uses_the_cache_and_says_so(plat, opened, capsys, tmp_path):
    plat.add("alex", "kettle", film())
    assert tacctl.main(["wall", "--no-window"]) == 0
    cache = wall.Cache()
    cache.fetch(plat.base, {"handle": "alex", "slug": "kettle"})
    plat.down = True
    capsys.readouterr()
    assert tacctl.main(["wall", "--tab"]) == 0
    out = capsys.readouterr().out
    assert "offline (HTTP 503): playing the 1 cached piece." in out
    assert opened[-1] == ([opened[-1][0][0], "wall-play", "--seconds", "30", "--offline"], "tab")


def test_tac_wall_offline_with_nothing_cached_is_an_error(plat, opened, capsys):
    plat.down = True
    assert tacctl.main(["wall"]) == 1
    assert "nothing is cached yet" in capsys.readouterr().err + "".join(capsys.readouterr())


def test_an_empty_wall_says_so(plat, opened, capsys):
    assert tacctl.main(["wall"]) == 0
    assert "the wall is empty this week" in capsys.readouterr().out and opened == []


@pytest.mark.parametrize("bad", ["-1", "3601", "nan", "x"])
def test_seconds_are_checked(plat, bad):
    with pytest.raises(SystemExit):
        tacctl.main(["wall", "--seconds", bad])


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_the_pane_player_gives_the_terminal_back_on_every_stop(plat, tmp_path, sig):
    """`tacctl wall-play` in a real pty, offline from its cache: plays, then Ctrl-C / kill / a closed pane each
    exit 0 with the main screen, autowrap and cursor restored."""
    import pty
    import select
    import signal as sigmod
    import sys
    import time

    plat.add("alex", "kettle", film(30), title="Kettle")
    cache = wall.Cache()
    doc = wall.fetch_playlist(plat.base, False)
    cache.save_playlist(doc, plat.base)
    cache.fetch(plat.base, doc["pieces"][0])
    plat.server.shutdown()  # offline (refused, not hung), and no thread alive in this process when it forks
    plat.server.server_close()
    lib = Path(tacctl.__file__).parent
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, str(lib / "tacctl.py"), "wall-play", "--seconds", "5"], dict(os.environ))
    raw = b""
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                raw += os.read(fd, 65536)
        assert b"Kettle" in raw and b"\x1b[?1049h" in raw
        os.kill(pid, getattr(sigmod, sig))
        end = time.monotonic() + 3
        while time.monotonic() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    raw += os.read(fd, 65536)
                except OSError:
                    break
        done, status = os.waitpid(pid, os.WNOHANG)
        if done == 0:
            time.sleep(0.5)
            done, status = os.waitpid(pid, os.WNOHANG)
        assert done == pid and os.waitstatus_to_exitcode(status) == 0
        pid = 0
        assert raw.rstrip(b"\r\n").endswith(wall.LEAVE.encode())
    finally:
        if pid:
            os.kill(pid, sigmod.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)


def test_a_hung_network_never_holds_up_a_cached_piece(plat, tmp_path, monkeypatch):
    """A platform that accepts and never answers: the cached piece plays at once; the fetch times out behind."""
    import socket
    import time

    plat.add("alex", "kettle", film(3), title="Kettle")
    doc = wall.fetch_playlist(plat.base, False)
    cache = wall.Cache(tmp_path / "c")
    cache.fetch(plat.base, doc["pieces"][0])
    hung = socket.socket()
    hung.bind(("127.0.0.1", 0))
    hung.listen(8)  # never accepted: connects, then silence
    base = f"http://127.0.0.1:{hung.getsockname()[1]}"
    monkeypatch.setattr(wall, "pane_size", lambda: (100, 10))
    out, clock = io.StringIO(), Clock()
    t0 = time.monotonic()
    try:
        assert wall.play(doc, base, cache, 0.3, False, out=out, clock=clock, sleep=clock.sleep, rounds=1) == 0
    finally:
        hung.close()
    assert time.monotonic() - t0 < 3 and "Kettle" in out.getvalue()


def test_a_hostile_codepoint_never_reaches_the_terminal():
    """A file from the network can hold any u32 where a character goes: ESC, C1 controls, bidi overrides,
    surrogates, values past Unicode. Each is drawn as '·'; nothing but the player's own escapes is written."""
    bad = [0x1B, 0x07, 0x9B, 0x202E, 0x200B, 0xD800, 0x110000, 0xFFFFFFFF, 0x0301, 0x754C]  # 界 is wide
    words = [0, len(bad)] + [x for cp in bad for x in (cp, 0xFFFFFF, 0)]
    gz = zlib.compress(_raw(len(bad), 1, 10, 1, words), wbits=31)
    h, w = wallframes.load(gz)
    s = wall.frame_ansi(next(wallframes.frames(h, w)), h.cols, h.rows, 40, 3, "credit")
    assert "\x07" not in s and "\x9b" not in s and "‮" not in s and "​" not in s
    assert s.count("\x1b") == s.count("\x1b[")  # every ESC starts one of our own CSI sequences
    import pyte

    screen = pyte.Screen(40, 3)
    pyte.ByteStream(screen).feed(s.encode())
    assert screen.display[0].strip() == "·" * len(bad)  # centred in the 40-column pane
