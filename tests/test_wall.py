"""/tac:wall: the frames format, the network and cache, the player, and the commands. A fake platform on
loopback; no prod host, no window, no one's code."""

import http.server
import io
import json
import os
import struct
import threading
import zlib
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


def plain(text: str) -> str:
    """The player's output without its OSC 8 hyperlinks (the @handle and the wordmark are links)."""
    import re

    return re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\", "", text)


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
        self.source = "week"
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
                    body = json.dumps({"week": "2026-W41", "source": platform.source, "pieces": platform.order}).encode()
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


@pytest.fixture(autouse=True)
def short_timeouts_and_no_stray_threads(monkeypatch):
    """Fast network timeouts here, and every thread a test started (prefetch, the fake platform) finished before
    the next test: the pty tests then fork from a single-threaded process (no forkpty warning)."""
    monkeypatch.setattr(wall, "TIMEOUT_S", 1)
    monkeypatch.setattr(wall, "DEADLINE_S", 3)
    before = set(threading.enumerate())
    yield
    for t in set(threading.enumerate()) - before:
        t.join(5)


@pytest.fixture
def plat(monkeypatch, tmp_path):
    p = Platform()
    monkeypatch.setenv("TAC_API", p.base)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    yield p
    p.server.shutdown()
    p.server.server_close()


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
    monkeypatch.setattr(wall, "pane_size", lambda: (80, 10))
    out = io.StringIO()
    clock = Clock()
    assert wall.play(doc, plat.base, cache, 1.0, False, out=out, clock=clock, sleep=clock.sleep, rounds=1) == 0
    first_piece_fetches = seen[0][0]
    assert first_piece_fetches <= 2  # while piece 0 plays: piece 0, maybe piece 1, never 2..4
    assert plat.frames_requests()[:5] == [f"/v1/pieces/alex/p{i}/frames" for i in range(5)]  # in order, lazily
    text = out.getvalue()
    assert text.startswith(wall.ENTER) and text.endswith(wall.LEAVE)
    assert "Piece 0 · @alex · Opus 5.5 · 1/5" in plain(text) and "Piece 4 · @alex · Opus 5.5 · 5/5" in plain(text)


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
    assert " · offline" in out.getvalue()
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
    assert "A Piece · @alex · taken off the wall" in plain(out.getvalue()) and "Still Plays" in out.getvalue()


def test_frames_scale_down_to_fit_and_are_written_by_address():
    h, w = wallframes.load(film(1, cols=80, rows=66))
    cur = next(wallframes.frames(h, w))
    s = wall.frame_ansi(cur, 80, 66, 40, 34, "credit")
    import pyte

    screen = pyte.Screen(40, 34)
    pyte.ByteStream(screen).feed(s.encode())
    drawn = [ln.rstrip() for ln in screen.display]
    assert drawn[0] == "0" + "▀" * 39  # 80 columns into 40: every other cell, the whole width
    assert drawn[33].startswith("credit") and drawn[33].endswith("terminal art club")
    assert all(len(ln) <= 40 for ln in drawn)
    assert "\n" not in s and "\x1b[A" not in s


def _scene(cols=80, rows=66, mast_x=None, line_y=None, ground=(40, 30, 30), ink=(10, 8, 12)):
    """A key frame of half-block cells on a flat ground, with a one-cell-wide mast (rows 10..55) at mast_x
    and/or a one-cell-tall line (cols 10..69) at line_y."""
    from array import array

    g, k = wallframes.rgb(ground), wallframes.rgb(ink)
    cur = array("I")
    for y in range(rows):
        for x in range(cols):
            hit = (x == mast_x and 10 <= y < 56) or (y == line_y and 10 <= x < 70)
            cur.extend((0x2580, k if hit else g, k if hit else g))
    return cur


def _screen(s, cols, rows):
    import pyte

    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(s.encode())
    return screen


@pytest.mark.parametrize("pane", [(107, 54), (60, 40)])
def test_a_one_cell_feature_survives_the_downscale(pane):
    """beacon-2's mast is one column of 80: nearest-cell sampling into 64x53 (a 107x54 pane) dropped every 5th
    column, the mast's among them. At any column, and a one-row line at any row, it stays: unbroken."""
    pc, pr = pane
    s = min(pc / 80, (pr - 1) / 66)
    for x in range(80):
        sc = _screen(wall.frame_ansi(_scene(mast_x=x), 80, 66, pc, pr, "c"), pc, pr)
        cols = {c for r in range(pr - 1) for c, cell in sc.buffer[r].items() if cell.bg == "0a080c"}
        assert len(cols) == 1, (x, cols)  # one column wide, never smeared or lost
        c0 = next(iter(cols))
        rows_hit = [r for r in range(pr - 1) if sc.buffer[r][c0].bg == "0a080c"]
        assert rows_hit == list(range(rows_hit[0], rows_hit[0] + len(rows_hit)))  # unbroken
        assert len(rows_hit) >= int(46 * s)
    for y in range(66):
        sc = _screen(wall.frame_ansi(_scene(line_y=y), 80, 66, pc, pr, "c"), pc, pr)
        rows = {r for r in range(pr - 1) if any(cell.bg == "0a080c" for cell in sc.buffer[r].values())}
        assert len(rows) == 1, (y, rows)
        r0 = next(iter(rows))
        assert sum(cell.bg == "0a080c" for cell in sc.buffer[r0].values()) >= int(60 * s)


def test_a_flat_ground_scales_to_itself():
    sc = _screen(wall.frame_ansi(_scene(), 80, 66, 60, 40, "c"), 60, 40)
    art = {(cell.fg, cell.bg) for r in range(39) for cell in sc.buffer[r].values() if cell.data == "▀"}
    assert art == {("281e1e", "281e1e")}


VANITAS = {"title": "Vanitas", "handle": "alex-radaev", "slug": "vanitas", "model": "Opus 5.5"}


def test_the_credit_is_readable_on_the_last_row():
    """It was drawn in the ground's own colour (08080f on 08080f): there, and invisible. CC BY needs it seen."""
    bar = wall.credit_line(VANITAS, 0, 4, False)
    sc = _screen(wall.frame_ansi(_scene(mast_x=25), 80, 66, 68, 54, bar), 68, 54)
    row = sc.display[53]
    assert row.startswith("Vanitas · @alex-radaev · Opus 5.5 · 1/4") and row.endswith("terminal art club")
    cells = sc.buffer[53]
    assert {cells[x].fg for x in range(39)} == {"9696a0"} and all(cells[x].bg == "08080f" for x in range(68))
    assert [cells[x].fg for x in (51, 60, 64)] == ["00e5c3", "a78bfa", "f472b6"]  # terminal / art / club


@pytest.mark.parametrize("width, bar", [
    (120, "Vanitas · @alex-radaev · Opus 5.5 · 1/4" + " " * 64 + "terminal art club"),
    (68, "Vanitas · @alex-radaev · Opus 5.5 · 1/4" + " " * 12 + "terminal art club"),
    (40, "@alex-radaev · 1/4" + " " * 5 + "terminal art club"),
    (24, "@alex-radaev · 1/4   tac"),
])
def test_the_bottom_bar_layout(width, bar):
    assert wall.credit_line(VANITAS, 0, 4, False).fit(width) == bar


def test_the_bar_degrades_model_then_title_then_the_mark_and_keeps_the_handle():
    c = wall.credit_line({**VANITAS, "title": "A Very Long Title Indeed"}, 1, 9, True)
    assert "Opus 5.5" in c.fit(85) and c.fit(84).startswith(
        "A Very Long Title Indeed · @alex-radaev · 2/9 · offline ")  # the model goes first
    assert c.fit(56).startswith("A Ver… · @alex-radaev · 2/9 · offline ")  # then the title is cut
    assert c.fit(52).startswith("@alex-radaev · 2/9 · offline ")  # then it goes
    assert c.fit(30) == "@alex-radaev" + " " * 15 + "tac"  # then the mark shortens
    for w in range(17, 140):
        bar = c.fit(w)
        assert "@alex-radaev" in bar and wall._width(bar) == w and bar.rstrip().endswith(("club", "tac")), w
    wide = wall.Credit("灯塔灯塔灯塔灯塔灯塔灯塔灯塔", "alex", "x", extra=("1/2",))
    assert wall._width(wide.fit(40)) == 40 and "… · @alex · 1/2" in wide.fit(40)


def test_the_bar_links_are_well_formed_closed_and_built_only_from_checked_names():
    import re

    row = wall.credit_row(wall.credit_line(VANITAS, 0, 4, False), 68, 54)
    opens = re.findall(r"\x1b\]8;;([^\x1b]*)\x1b\\", row)
    assert opens == ["https://terminalart.club/@alex-radaev", "",
                     "https://terminalart.club/@alex-radaev/vanitas", ""]  # each link opened, then closed
    assert row.rindex("\x1b]8;;\x1b\\") > row.rindex("\x1b]8;;https")
    sc = _screen(row, 68, 54)
    assert sc.display[53].endswith("terminal art club")
    # a name that isn't a valid handle or slug never reaches a URL (the playlist drops such entries anyway)
    evil = wall.Credit("t", "x\x1b]8;;http://evil\x1b\\", "../../a", extra=())
    assert "evil" not in "".join(re.findall(r"\x1b\]8;;([^\x1b]*)", wall.credit_row(evil, 80, 5)))
    assert wall.Credit("t", "alex", "../x").piece_url() is None and wall.Credit("t", "alex", "ok").piece_url()
    assert wall.LEAVE.startswith("\x1b]8;;\x1b\\")  # restore closes a link a stop may have cut


def test_the_bar_text_goes_through_the_sanitiser():
    bar = wall.credit_line({**VANITAS, "title": "Evil\u202e\u200b\x1b[2Jx\u0301"}, 0, 1, False)
    assert bar.title == "Evil[2Jx"


def test_the_credit_and_the_art_never_share_a_row():
    for pc, pr in ((107, 54), (60, 40), (80, 67), (24, 5), (18, 2)):
        bar = wall.credit_line(VANITAS, 0, 4, False)
        sc = _screen(wall.frame_ansi(_scene(mast_x=25), 80, 66, pc, pr, bar), pc, pr)
        assert "@alex-radaev" in sc.display[pr - 1] and "▀" not in sc.display[pr - 1]


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
    assert "nothing is cached from the last 7 days" in capsys.readouterr().err


@pytest.mark.parametrize("source, said", [
    ("week+recent", "playing this week's wall, topped up with recent pieces, 1 piece"),
    ("picks", "playing the picks (nothing on this week's wall yet), 1 piece"),
    ("recent", "playing the most recent pieces (nothing this week, no picks yet), 1 piece"),
    ("nonsense", "playing this week's wall, 1 piece"),
])
def test_tac_wall_says_which_fallback_it_plays(plat, opened, capsys, source, said):
    """The platform falls back week -> picks -> recent and tops up a thin week; the line Claude relays says so."""
    plat.source = source
    plat.add("alex", "kettle", film())
    assert tacctl.main(["wall"]) == 0
    assert said in capsys.readouterr().out


def test_an_empty_wall_says_so(plat, opened, capsys):
    assert tacctl.main(["wall"]) == 0
    assert "the wall is empty: nothing published yet" in capsys.readouterr().out and opened == []


@pytest.mark.parametrize("bad", ["-1", "3601", "nan", "x"])
def test_seconds_are_checked(plat, bad):
    with pytest.raises(SystemExit):
        tacctl.main(["wall", "--seconds", bad])


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_the_pane_player_gives_the_terminal_back_on_every_stop(plat, tmp_path, sig):
    """`tacctl wall-play` in a real pty, offline from its cache: plays, then Ctrl-C / kill / a closed pane each
    exit 0 with the main screen, autowrap and cursor restored."""
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
    for t in threading.enumerate():
        if t is not threading.main_thread():
            t.join(5)
    import ptyspawn

    lib = Path(tacctl.__file__).parent
    proc, fd = ptyspawn.spawn([sys.executable, str(lib / "tacctl.py"), "wall-play", "--seconds", "5"], 80, 30)
    pid = proc.pid
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


def test_the_bottom_bar_holds_the_last_row_through_a_resize_and_a_piece_change(plat):
    """`tacctl wall-play` in a real pty: after the first frame, after a resize (narrower: the model and title
    give way, @handle stays) and after the next piece starts (scaled: 80x66 into the pane), the last row is the
    bar, in its own ink on the ground, and no art cell is on it."""
    import select
    import signal as sigmod
    import sys
    import time

    import pyte

    plat.add("alex", "kettle", film(30), title="Kettle")
    plat.add("alex", "big", film(30, cols=80, rows=66), title="Big One")
    cache = wall.Cache()
    doc = wall.fetch_playlist(plat.base, False)
    cache.save_playlist(doc, plat.base)
    for p in doc["pieces"]:
        cache.fetch(plat.base, p)
    plat.server.shutdown()  # offline: plays from the cache
    plat.server.server_close()
    for t in threading.enumerate():
        if t is not threading.main_thread():
            t.join(5)
    import ptyspawn

    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    lib = Path(tacctl.__file__).parent
    proc, fd = ptyspawn.spawn([sys.executable, str(lib / "tacctl.py"), "wall-play", "--seconds", "2"], 70, 20, env)
    screen = pyte.Screen(70, 20)
    stream = pyte.ByteStream(screen)

    def pump(until, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                stream.feed(os.read(fd, 65536))
                if until():
                    return True
        return False

    def bar_ok(*want):
        last = screen.lines - 1
        row = screen.display[last]
        cells = [screen.buffer[last][x] for x in range(screen.columns)]
        return (all(w in row for w in want) and "▀" not in row and all(c.bg == "08080f" for c in cells)
                and "e6781e" not in {c.fg for c in cells})  # the art's ink never on the bar

    try:
        assert pump(lambda: bar_ok("Kettle", "@alex", "Opus 5.5", "terminal art club")), screen.display[-1]
        ptyspawn.winsize(fd, 26, 12)  # narrower and shorter: SIGWINCH, the next frame is drawn at 26x12
        screen.resize(12, 26)
        assert pump(lambda: bar_ok("@alex", "tac") and "Opus" not in screen.display[-1]), screen.display[-1]
        assert pump(lambda: bar_ok("@alex", "2/2", "tac")), screen.display[-1]  # the next piece, scaled
        assert screen.display[-1].startswith("@alex · 2/2")
    finally:
        proc.send_signal(sigmod.SIGTERM)
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()
            proc.wait()
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


# ── takedowns stick ────────────────────────────────────────────────────────


def test_a_fresh_playlist_deletes_cached_pieces_it_no_longer_lists(plat, opened, capsys):
    plat.add("alex", "kept", film(), title="Kept")
    plat.add("alex", "taken", film(), title="Taken Down")
    assert tacctl.main(["wall", "--no-window"]) == 0
    cache = wall.Cache()
    for p in wall.fetch_playlist(plat.base, False)["pieces"]:
        cache.fetch(plat.base, p)
    assert cache.cached({"handle": "alex", "slug": "taken"}) is not None
    plat.order = [e for e in plat.order if e["slug"] != "taken"]  # hidden for copyright
    assert tacctl.main(["wall", "--no-window"]) == 0
    assert cache.cached({"handle": "alex", "slug": "taken"}) is None
    assert not list(cache.root.glob("alex--taken*"))
    assert cache.cached({"handle": "alex", "slug": "kept"}) is not None


def test_a_404_on_revalidation_deletes_the_cached_copy(plat, tmp_path):
    plat.add("alex", "x", film())
    cache = wall.Cache(tmp_path / "c")
    cache.fetch(plat.base, {"handle": "alex", "slug": "x"})
    del plat.pieces["alex/x"]
    with pytest.raises(wall.Gone):
        cache.fetch(plat.base, {"handle": "alex", "slug": "x"})
    assert cache.cached({"handle": "alex", "slug": "x"}) is None


def test_a_piece_delisted_mid_session_leaves_after_the_lap(plat, tmp_path, monkeypatch):
    plat.add("alex", "a", film(3), title="Stays")
    plat.add("alex", "b", film(3), title="Goes")
    doc = wall.fetch_playlist(plat.base, False)
    cache = wall.Cache(tmp_path / "c")
    monkeypatch.setattr(wall, "pane_size", lambda: (100, 10))
    plat.order = [e for e in plat.order if e["slug"] != "b"]  # taken down while lap 1 plays
    out, clock = io.StringIO(), Clock()
    assert wall.play(doc, plat.base, cache, 0.3, False, out=out, clock=clock, sleep=clock.sleep, rounds=3) == 0
    text = out.getvalue()
    text = plain(text)
    assert text.count("Goes · @alex") >= 1 and "Stays · @alex · Opus 5.5 · 1/1" in text  # lap 2 on: one piece
    assert cache.cached({"handle": "alex", "slug": "b"}) is None


def test_offline_never_replays_a_piece_not_confirmed_for_a_week(plat, tmp_path, monkeypatch):
    plat.add("alex", "old", film(3), title="Old")
    plat.add("alex", "new", film(3), title="New")
    doc = wall.fetch_playlist(plat.base, False)
    cache = wall.Cache(tmp_path / "c")
    for p in doc["pieces"]:
        cache.fetch(plat.base, p)
    meta = tmp_path / "c" / "alex--old.json"
    m = json.loads(meta.read_text())
    meta.write_text(json.dumps({**m, "checked": m["checked"] - 8 * 86400}))
    assert [p["slug"] for p in wall.playable_offline(doc, cache)] == ["new"]


def test_each_piece_is_revalidated_once_a_session(plat, tmp_path, monkeypatch):
    plat.add("alex", "a", film(3))
    plat.add("alex", "b", film(3))
    doc = wall.fetch_playlist(plat.base, False)
    monkeypatch.setattr(wall, "pane_size", lambda: (100, 10))
    clock = Clock()
    wall.play(doc, plat.base, wall.Cache(tmp_path / "c"), 0.3, False, out=io.StringIO(), clock=clock,
              sleep=clock.sleep, rounds=4)
    assert sorted(plat.frames_requests()) == ["/v1/pieces/alex/a/frames", "/v1/pieces/alex/b/frames"]


def test_a_dripping_server_is_cut_off_at_the_deadline(monkeypatch):
    import socket
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def drip():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\n\r\n")
        while not stop.is_set():
            try:
                conn.sendall(b"x")
            except OSError:
                break
            time.sleep(0.2)
        conn.close()

    t = threading.Thread(target=drip, daemon=True)
    t.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(wall.WallError, match="too slow"):
            wall.get(f"http://127.0.0.1:{srv.getsockname()[1]}/v1/wall.json", wall.MAX_PLAYLIST)
        assert time.monotonic() - t0 < wall.DEADLINE_S + 2
    finally:
        stop.set()
        srv.close()
        t.join(5)


def test_prune_clears_stale_temp_files(tmp_path):
    import time

    cache = wall.Cache(tmp_path / "c")
    old, new = cache.root / ".alex--x.cells.gz.1.2.tmp", cache.root / ".alex--y.cells.gz.1.2.tmp"
    old.write_bytes(b"half")
    new.write_bytes(b"in flight")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    cache.prune()
    assert not old.exists() and new.exists()


def test_marks_are_drawn_as_the_fallback():
    for cp in (0x0301, 0x0903, 0x20DD):  # Mn, Mc, Me
        assert wallframes.char(cp) == "·"
