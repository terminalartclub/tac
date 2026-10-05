"""/tac:wall pixel mode: terminal detection (by asking it), the two image protocols, layout, the published
raster, the fallback to cells, resize, and the terminal given back clean on every stop. No real terminal: a pty
whose far end answers like one."""

import base64
import io
import os
import re
import select
import sys
import threading
import time
from array import array
from pathlib import Path

import pytest
from PIL import Image

import tacctl
import wall
import wallframes
import wallpix

LIB = Path(wall.__file__).parent
APC = re.compile(r"\x1b_G([^;\x1b]*)(?:;([^\x1b]*))?\x1b\\")
OSC1337 = re.compile(r"\x1b\]1337;File=([^:]*):([A-Za-z0-9+/=]*)\x07")
CREDIT = wall.credit_line({"title": "Kettle", "handle": "alex", "slug": "kettle", "model": "Opus 5.5"}, 0, 2, False)


def halves(cols=80, rows=66, colour=(40, 30, 30), mast_x=None, cp=0x2580):
    g, k = wallframes.rgb(colour), wallframes.rgb((230, 120, 30))
    cur = array("I")
    for y in range(rows):
        for x in range(cols):
            c = k if x == mast_x else g
            cur.extend((cp, c, c))
    return cur


def text_only(s: str) -> str:
    """The write without its image escapes (pyte doesn't know APC or OSC 1337)."""
    return OSC1337.sub("", APC.sub("", s))


def screen(s, cols, rows):
    import pyte

    sc = pyte.Screen(cols, rows)
    pyte.ByteStream(sc).feed(text_only(s).encode())
    return sc


# ── detection ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("env, want", [
    ({"TERM_PROGRAM": "iTerm.app"}, wallpix.ITERM),
    ({"TERM_PROGRAM": "ghostty"}, wallpix.KITTY),
    ({"TERM": "xterm-kitty"}, wallpix.KITTY),
    ({"TERM": "xterm-ghostty"}, wallpix.KITTY),
    ({"KITTY_WINDOW_ID": "1", "TERM": "xterm-256color"}, wallpix.KITTY),
    ({"TERM_PROGRAM": "Apple_Terminal"}, None),
    ({"TERM_PROGRAM": "vscode"}, None),
    ({"TERM_PROGRAM": "iTerm.app", "TMUX": "/tmp/x"}, None),  # a multiplexer swallows image escapes
    ({"TERM": "screen-256color", "KITTY_WINDOW_ID": "1"}, None),
    ({}, None),
])
def test_which_protocol_the_terminal_claims(env, want):
    assert wallpix.guess(env) == want


def test_no_tty_or_no_claim_is_cells_without_asking():
    r, w = os.pipe()
    try:
        assert wallpix.detect({"TERM_PROGRAM": "iTerm.app"}, r, w) is None
        assert not select.select([r], [], [], 0)[0]  # a pipe isn't a terminal: no probe written to it
        assert wallpix.detect({"TERM_PROGRAM": "Apple_Terminal"}, 0, 1) is None
    finally:
        os.close(r)
        os.close(w)


class Term:
    """The far end of a pty, answering like a terminal: `answers` maps a query (bytes it sees) to its reply. DA1
    is always answered, as every terminal does. Collects everything the program writes."""

    def __init__(self, fd: int, answers: dict[bytes, bytes]) -> None:
        self.fd, self.answers, self.got, self.stop = fd, answers, b"", False
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self) -> None:
        pending = b""
        while not self.stop:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.02)
                if not r:
                    continue
                chunk = os.read(self.fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.got += chunk
            pending += chunk
            for q, a in self.answers.items():
                if q in pending:
                    os.write(self.fd, a)
                    pending = pending.replace(q, b"", 1)
            if b"\x1b[c" in pending:
                os.write(self.fd, b"\x1b[?62;22c")
                pending = pending.replace(b"\x1b[c", b"", 1)
            pending = pending[-256:]

    def wait_for(self, what: bytes, timeout: float = 6.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if what in self.got:
                return True
            time.sleep(0.02)
        return False

    def close(self) -> None:
        self.stop = True
        self.t.join(1)


KITTY_ANSWERS = {b"a=q,t=d,f=24;AAAA\x1b\\": b"\x1b_Gi=31;OK\x1b\\", b"\x1b[16t": b"\x1b[6;34;14t"}
ITERM_ANSWERS = {b"\x1b]1337;ReportCellSize\x07": b"\x1b]1337;ReportCellSize=17.0;7.0;2.0\x07"}


def run_detect(env_extra: dict, answers: dict) -> str:
    import ptyspawn

    env = {**os.environ, **env_extra}
    code = ("import os, sys; sys.path.insert(0, %r); import wallpix; p = wallpix.detect(os.environ, 0, 1); "
            "print('RESULT', p and p.proto, p and p.cell)" % str(LIB))
    proc, fd = ptyspawn.spawn([sys.executable, "-c", code], 80, 30, env)
    term = Term(fd, answers)
    try:
        assert term.wait_for(b"RESULT")
        proc.wait(5)
        term.wait_for(b"\n", 1)
        return term.got.decode(errors="replace")
    finally:
        term.close()
        os.close(fd)


def test_kitty_is_confirmed_by_its_graphics_query_and_measured():
    got = run_detect({"TERM": "xterm-kitty"}, KITTY_ANSWERS)
    assert "RESULT kitty (14, 34)" in got  # the pty reports no pixels: CSI 16 t gave the cell size
    assert "\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\" in got


def test_iterm_is_confirmed_by_report_cell_size_at_device_pixels():
    got = run_detect({"TERM_PROGRAM": "iTerm.app"}, ITERM_ANSWERS)
    assert "RESULT iterm (14.0, 34.0)" in got  # 7x17 points at scale 2


@pytest.mark.parametrize("env", [{"TERM": "xterm-kitty"}, {"TERM_PROGRAM": "iTerm.app"}])
def test_a_terminal_that_doesnt_answer_gets_cells(env):
    t0 = time.monotonic()
    got = run_detect(env, {})  # only DA1: the query was ignored
    assert "RESULT None None" in got and time.monotonic() - t0 < 3  # DA1 ends the wait: no timeout


# ── the protocols ──────────────────────────────────────────────────────────


def test_iterm_inline_image_is_well_formed():
    im = Image.new("RGB", (40, 60), (200, 10, 10))
    b64 = wallpix.encode(wallpix.ITERM, im)
    s = wallpix.iterm_image(b64, 5, 3)
    m = OSC1337.fullmatch(s)
    args = dict(kv.split("=") for kv in m[1].split(";"))
    data = base64.standard_b64decode(m[2])
    assert args == {"inline": "1", "size": str(len(data)), "width": "5", "height": "3", "preserveAspectRatio": "0"}
    assert Image.open(io.BytesIO(data)).format == "JPEG" and Image.open(io.BytesIO(data)).size == (40, 60)


def test_iterm_images_over_its_osc_limit_drop_quality_then_go_to_cells(monkeypatch):
    """iTerm2 drops an OSC 1337 over 1,048,576 bytes; noise at the published size is ~1.9 MB at q90."""
    def noise(w, h):
        return Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))

    b64 = wallpix.encode(wallpix.ITERM, noise(952, 1691))
    assert 0 < len(b64) <= wallpix.ITERM_MAX  # q90 and q75 too big: a lower quality fits
    assert len(wallpix.iterm_image(b64, 68, 50)) < 1_048_576
    assert wallpix.encode(wallpix.ITERM, noise(1040, 1848)) == ""  # too big even at the lowest: cells
    monkeypatch.setattr(wallpix, "ITERM_MAX", 100)
    px, _ = pixels(wallpix.ITERM, (68, 54, 0, 0))
    assert px.frame(halves(), 80, 66, 0, "k", CREDIT) is None and not px.broken  # this frame as cells
    assert px.cache == {0: ""}  # and not encoded again on the next loop


def test_kitty_image_is_chunked_and_placed_by_id():
    im = Image.effect_noise((200, 200), 64).convert("RGB")  # big enough for several chunks
    b64 = wallpix.encode(wallpix.KITTY, im)
    s = wallpix.kitty_image(b64, 7301, 9, 4)
    chunks = APC.findall(s)
    assert len(chunks) > 2 and "".join(c for _, c in chunks) == b64 and APC.sub("", s) == ""
    first = dict(kv.split("=") for kv in chunks[0][0].split(","))
    assert first == {"a": "T", "f": "100", "i": "7301", "q": "2", "p": "1", "c": "9", "r": "4", "C": "1", "m": "1"}
    assert [c[0] for c in chunks[1:]] == ["m=1"] * (len(chunks) - 2) + ["m=0"]
    assert all(len(c) <= 4096 for _, c in chunks) and all(len(c) % 4 == 0 for _, c in chunks[:-1])
    assert Image.open(io.BytesIO(base64.standard_b64decode(b64))).format == "PNG"
    assert wallpix.kitty_delete(7301) == "\x1b_Ga=d,d=I,i=7301,q=2\x1b\\"


# ── layout and raster ──────────────────────────────────────────────────────


@pytest.mark.parametrize("pane, cell", [((68, 54), (14, 34)), ((107, 54), (14, 34)), ((200, 30), (8, 17)),
                                        ((30, 80), (20, 40)), ((80, 67), (13, 28))])
def test_the_image_fits_the_picture_rows_keeps_its_aspect_and_is_centred(pane, cell):
    pc, pr = pane
    lay = wallpix.layout(80, 66, pc, pr, *cell)
    assert lay.y + lay.nrows <= pr - 1 and lay.x + lay.ncols <= pc  # never on the bar's row
    assert abs(lay.fit_w / lay.fit_h - (80 * 13) / (66 * 28)) < 0.01
    assert lay.canvas_w * lay.canvas_h <= wallpix.MAX_PIXELS
    assert abs((pc - lay.ncols) - 2 * lay.x) <= 1 and abs((pr - 1 - lay.nrows) - 2 * lay.y) <= 1
    assert lay.fit_w == lay.canvas_w or lay.fit_h == lay.canvas_h or abs(lay.canvas_w - lay.fit_w) < cell[0] * 2


def test_the_picture_is_the_published_raster():
    """At the published size a half-block frame is pixel for pixel what vscreen's Rasterizer draws (the
    previews' renderer); a frame with glyphs goes through that Rasterizer itself."""
    import vscreen

    cur = halves(mast_x=25)
    cur[3 * 100 + 1] = wallframes.rgb((10, 200, 10))  # one cell's top half differs from its bottom
    lay = wallpix.Layout(0, 0, 80, 66, 1040, 1848, 1040, 1848, 0, 0)
    got = wallpix.picture(cur, 80, 66, lay)
    grid = [[vscreen.Cell("▀", wallframes.unrgb(cur[3 * i + 1]), wallframes.unrgb(cur[3 * i + 2]), False)
             for i in range(y * 80, (y + 1) * 80)] for y in range(66)]
    assert got.tobytes() == vscreen.Rasterizer().frame(grid).tobytes()
    mixed = array("I", cur)
    mixed[0] = ord("x")
    assert wallpix.picture(mixed, 80, 66, lay).size == (1040, 1848)
    big = halves(cols=512, rows=256)
    big[0] = ord("x")
    assert wallpix.picture(big, 512, 256, wallpix.layout(512, 256, 100, 40, 14, 34)) is None  # past the cap


def test_lower_halves_and_full_blocks_are_the_same_pixels():
    up, lo = halves(cp=0x2580), halves(cp=0x2584)
    lay = wallpix.layout(80, 66, 68, 54, 14, 34)
    assert wallpix.picture(up, 80, 66, lay).tobytes() == wallpix.picture(lo, 80, 66, lay).tobytes()


# ── the player's side ──────────────────────────────────────────────────────


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def pixels(proto, size, clock=None, logo=True):
    box = {"size": size}
    logo_png = Path(wallpix.LOGO_PATH).read_bytes() if logo else None
    px = wallpix.Pixels(proto, (14.0, 34.0), clock=clock or Clock(), size=lambda: box["size"], logo=logo_png)
    return px, box


@pytest.mark.parametrize("proto", [wallpix.ITERM, wallpix.KITTY])
def test_a_frame_is_one_image_over_the_picture_and_the_bar_stays_text(proto):
    px, _ = pixels(proto, (68, 54, 0, 0))
    s = px.frame(halves(), 80, 66, 0, "k", CREDIT)
    lay = px.lay[1]
    assert f"\x1b[{lay.y + 1};{lay.x + 1}H" in s
    if proto == wallpix.ITERM:
        assert len(OSC1337.findall(s)) == 2  # the frame and the logo
        assert f"width={lay.ncols};height={lay.nrows}" in s and "width=2;height=1" in s
    else:
        ids = re.findall(r"a=T,f=100,i=(\d+)", s)
        assert ids == ["7301", "7303"] and "\x1b_Ga=d,d=I,i=7302,q=2\x1b\\" in s  # new placed, old deleted
    sc = screen(s, 68, 54)
    assert "@alex" in sc.display[53] and sc.display[53].rstrip().endswith("terminal art club")
    assert len(sc.display[53].rstrip()) <= 68 - 3  # the logo's two cells and a gap are kept free
    assert {sc.buffer[53][x].bg for x in range(68)} == {"08080f"}


@pytest.mark.parametrize("proto", [wallpix.ITERM, wallpix.KITTY])
def test_the_logo_is_sent_once_a_layout_not_with_every_frame(proto):
    """iTerm2 3.7.3: the logo resent with each of 247 frames in 25 s, one more image to decode a frame. Once a
    layout now: the bar's row is erased up to the logo's cells only; a resize or a clear draws it again (kitty:
    a placement of the image already sent, never the bytes again)."""
    clock = Clock()
    px, box = pixels(proto, (68, 54, 0, 0), clock)
    logo = "width=2;height=1" if proto == wallpix.ITERM else "i=7303,"
    sent = lambda s: s.count(logo) if proto == wallpix.ITERM else s.count("a=T,f=100,i=7303,")  # noqa: E731
    frames = [px.frame(halves(mast_x=k), 80, 66, k, "a", CREDIT) for k in range(10)]
    frames += [px.frame(halves(mast_x=k), 80, 66, k, "b", CREDIT) for k in range(10)]  # the next piece
    assert sent("".join(frames)) == 1 and all(logo not in f for f in frames[1:])
    assert all(f"\x1b[54;1H{wall.GROUND_SGR}\x1b[65X" in f and "\x1b[54;1H" + wall.GROUND_SGR + "\x1b[K" not in f
               for f in frames[1:])  # ECH, not EL: the logo's cells untouched (frame 0 clears first)
    box["size"] = (100, 40, 0, 0)
    assert px.frame(halves(), 80, 66, 0, "b", CREDIT) == ""
    clock.t = 0.2
    again = "".join(px.frame(halves(mast_x=k), 80, 66, k, "b", CREDIT) for k in range(5))
    if proto == wallpix.ITERM:
        assert again.count(logo) == 1
    else:
        assert sent(again) == 0 and again.count(wallpix.kitty_place(7303, 2, 1)) == 1
        assert wallpix.kitty_unplace(7303) in again and wallpix.kitty_delete(7303) not in again
    assert px.cleanup().endswith(wallpix.kitty_delete(7303)) or proto == wallpix.ITERM  # on exit: the data too


def test_kitty_double_buffers_and_frames_are_cached_per_size():
    px, _ = pixels(wallpix.KITTY, (68, 54, 0, 0), logo=False)
    a = px.frame(halves(), 80, 66, 0, "k", CREDIT)
    b = px.frame(halves(mast_x=3), 80, 66, 1, "k", CREDIT)
    c = px.frame(halves(), 80, 66, 0, "k", CREDIT)
    assert re.findall(r"a=T,f=100,i=(\d+)", a + b + c) == ["7301", "7302", "7301"]
    assert set(px.cache) == {0, 1}
    payload = lambda s: "".join(x[1] for x in APC.findall(s) if x[0].startswith(("a=T", "m=")))  # noqa: E731
    assert payload(a) == payload(c) != payload(b)  # frame 0 again: the same bytes, from the cache


def test_a_resize_waits_for_the_size_to_hold_then_rerenders():
    clock = Clock()
    px, box = pixels(wallpix.ITERM, (68, 54, 0, 0), clock)
    first = px.frame(halves(), 80, 66, 0, "k", CREDIT)
    assert "width=68;height=50" in first
    box["size"] = (100, 40, 0, 0)
    clock.t = 0.02
    assert px.frame(halves(), 80, 66, 1, "k", CREDIT) == ""  # still dragging: nothing drawn
    clock.t = 0.2
    again = px.frame(halves(), 80, 66, 2, "k", CREDIT)
    lay = px.lay[1]
    assert lay.nrows == 39 and f"width={lay.ncols};height=39" in again
    assert again.startswith("\x1b[1;1H")  # cleared first: no stale image left around the new one
    assert "\x1b[40;1H" in again  # the bar on the new last row
    assert px.cache_for[2] == (100, 40, 0, 0) and set(px.cache) == {2}


def test_a_frame_pixels_cant_draw_falls_back_to_cells_and_clears_the_images():
    px, _ = pixels(wallpix.KITTY, (100, 40, 0, 0))
    assert px.frame(halves(), 80, 66, 0, "k", CREDIT)
    big = halves(cols=512, rows=256)
    big[0] = ord("x")
    assert px.frame(big, 512, 256, 0, "big", CREDIT) is None
    gone = px.drop(40)
    assert all(wallpix.kitty_delete(i) in gone for i in (7301, 7302)) and wallpix.kitty_unplace(7303) in gone
    assert px.drop(40) == ""
    px.broken = True
    assert px.frame(halves(), 80, 66, 0, "k", CREDIT) is None


def test_a_stop_signal_mid_render_is_never_swallowed(monkeypatch):
    """SIGTERM/SIGHUP raise wall._Stop wherever the player is; mid-encode it went through Pixels.frame's
    `except Exception` (fall back to cells) and the player kept going: 3 of 12 kitty runs hung on kill."""
    px, _ = pixels(wallpix.KITTY, (68, 54, 0, 0))

    def stop(*a):
        raise wall._Stop

    monkeypatch.setattr(wallpix, "picture", stop)
    with pytest.raises(wall._Stop):
        px.frame(halves(), 80, 66, 0, "k", CREDIT)
    monkeypatch.setattr(wallpix, "picture", lambda *a: 1 / 0)  # a real rendering error: cells from now on
    assert px.frame(halves(), 80, 66, 1, "k", CREDIT) is None and px.broken


@pytest.mark.parametrize("answer, want", [
    (b"\x1b]1337;ReportCellSize=1.2.3;7.0\x07", None),
    (b"\x1b]1337;ReportCellSize=.;.\x07", None),
    (b"\x1b]1337;ReportCellSize=17.0;7.0;0\x07", None),
    (b"\x1b]1337;ReportCellSize=17;7;2\x07", (14.0, 34.0)),
    (b"\x1b]1337;ReportCellSize=" + b"9" * 400 + b";0.001\x07", (1.0, 512.0)),  # clamped to 1..512 px
], ids=["two-dots", "dots", "zero-scale", "ok", "huge"])
def test_odd_iterm_cell_size_answers_are_parsed_strictly_and_clamped(monkeypatch, answer, want):
    monkeypatch.setattr(wallpix, "winsize", lambda fd: (80, 24, 0, 0))
    monkeypatch.setattr(wallpix, "ask", lambda *a: answer + b"\x1b[?62c")
    assert wallpix.cell_px(wallpix.ITERM, 0, 1) == want


def test_kitty_cell_sizes_are_clamped(monkeypatch):
    monkeypatch.setattr(wallpix, "ask", lambda *a: b"\x1b_Gi=31;OK\x1b\\\x1b[6;99999;0t")
    monkeypatch.setattr(wallpix, "winsize", lambda fd: (80, 24, 0, 0))
    assert wallpix.cell_px(wallpix.KITTY, 0, 1) is None  # a zero side: not measured, cells
    monkeypatch.setattr(wallpix, "winsize", lambda fd: (80, 24, 80 * 4000, 24 * 17))
    assert wallpix.cell_px(wallpix.KITTY, 0, 1) == (512.0, 17.0)


def test_a_malformed_remeasure_answer_or_a_layout_error_is_cells_never_a_crash(monkeypatch):
    clock = Clock()
    box = {"size": (68, 54, 0, 0)}
    px = wallpix.Pixels(wallpix.ITERM, (14.0, 34.0), 0, 1, clock=clock, size=lambda: box["size"])
    assert px.frame(halves(), 80, 66, 0, "k", CREDIT)
    monkeypatch.setattr(wallpix, "ask", lambda *a: b"\x1b]1337;ReportCellSize=1.2.3;9.9.9\x07\x1b[?62c")
    box["size"] = (100, 40, 0, 0)
    assert px.frame(halves(), 80, 66, 1, "k", CREDIT) == ""  # settling
    clock.t = 0.2
    assert px.frame(halves(), 80, 66, 1, "k", CREDIT) and px.cell == (14.0, 34.0)  # kept the last good size
    monkeypatch.setattr(wallpix, "ask", lambda *a: 1 / 0)  # the tty went away mid-probe
    box["size"] = (90, 40, 0, 0)
    assert px.frame(halves(), 80, 66, 2, "k", CREDIT) == ""
    clock.t = 0.4
    assert px.frame(halves(), 80, 66, 2, "k", CREDIT) is None and px.broken
    px, _ = pixels(wallpix.KITTY, (68, 54, 0, 0))
    monkeypatch.setattr(wallpix, "layout", lambda *a: 1 / 0)
    assert px.frame(halves(), 80, 66, 0, "k", CREDIT) is None and px.broken


def test_play_uses_pixels_and_cells_as_each_frame_allows(monkeypatch, tmp_path):
    """wall.play with a pixel drawer: the image for frames it can draw, frame_ansi for the rest, and its
    cleanup before the terminal is given back."""
    calls = []

    class Fake:
        def frame(self, cur, cols, rows, index, piece, credit):
            calls.append(index)
            return None if index == 1 else f"<img {index}>"

        def drop(self, pr):
            return "<drop>"

        def cleanup(self):
            return "<cleanup>"

    enc = wallframes.Encoder(8, 4, 10)
    for k in range(3):
        enc.sample(k / 10, [[type("C", (), {"ch": "▀", "fg": (k, 0, 0), "bg": (0, 0, 0)}) for _ in range(8)]
                            for _ in range(4)])
    gz = enc.finish(0.3)

    class Cache:
        def cached(self, p):
            return gz

        def touch(self, p):
            pass

        def fresh(self, p):
            return True

    monkeypatch.setattr(wall, "pane_size", lambda: (40, 10))
    out = io.StringIO()
    t = [0.0]
    doc = {"pieces": [{"handle": "alex", "slug": "a", "title": "A", "model": "", "bytes": len(gz), "etag": None}]}
    assert wall.play(doc, None, Cache(), 0.3, True, out=out, clock=lambda: t[0],
                     sleep=lambda s: t.__setitem__(0, t[0] + max(s, 0.001)), rounds=1, pixels=Fake()) == 0
    s = out.getvalue()
    assert "<img 0>" in s and "<img 2>" in s and "<drop>" in s and calls[:3] == [0, 1, 2]
    assert s.endswith("<cleanup>" + wall.LEAVE)


@pytest.mark.parametrize("proto", [wallpix.ITERM, wallpix.KITTY])
def test_a_stop_mid_frame_ends_the_image_escape_before_the_cleanup(monkeypatch, proto):
    """SIGTERM, SIGHUP or Ctrl-C during out.write(frame) drops the rest of the frame: without a terminator the
    kitty deletes and LEAVE land inside the cut APC / OSC 1337 and the terminal swallows them as payload."""
    from test_wall import film

    gz = film(3, cols=80, rows=66)

    class Cache:
        def cached(self, p):
            return gz

        def touch(self, p):
            pass

        def fresh(self, p):
            return True

    class Cut(io.StringIO):
        at = None

        def write(self, s):
            head = "a=T," if proto == wallpix.KITTY else "1337;File="
            if self.at is None and head in s:
                payload = s.index(";" if proto == wallpix.KITTY else ":", s.index(head) + len(head)) + 1
                self.at = len(self.getvalue()) + payload + 100
                super().write(s[:payload + 100])
                raise wall._Stop
            return super().write(s)

    monkeypatch.setattr(wall, "pane_size", lambda: (68, 54))
    px, _ = pixels(proto, (68, 54, 0, 0))
    out = Cut()
    doc = {"pieces": [{"handle": "alex", "slug": "a", "title": "A", "model": "", "bytes": len(gz), "etag": None}]}
    assert wall.play(doc, None, Cache(), 1, True, out=out, sleep=lambda s: None, rounds=1, pixels=px) == 0
    s = out.getvalue()
    assert out.at and "\x1b" not in s[out.at - 100:out.at]  # cut inside the payload
    rest = s[out.at:]
    assert rest.startswith(wall.ST) and rest.endswith(wall.LEAVE)
    if proto == wallpix.KITTY:
        assert rest.index(wall.ST) < rest.index("\x1b_Ga=d")


# ── a real pty: the whole pane player ───────────────────────────────────────


@pytest.fixture
def cached_wall(monkeypatch, tmp_path):
    """A playlist and one piece in the cache, offline: wall-play plays without a platform."""
    from test_wall import film

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cache = wall.Cache()
    gz = film(30, cols=80, rows=66)
    doc = {"week": "2026-W41", "source": "week",
           "pieces": [{"handle": "alex", "slug": "kettle", "title": "Kettle", "model": "Opus 5.5",
                       "bytes": len(gz), "etag": None}]}
    cache.save_playlist(doc, "http://127.0.0.1:9")
    data, _ = cache._paths(doc["pieces"][0])
    wall._write(data, gz)
    cache._note(doc["pieces"][0], None, len(gz))
    return tmp_path


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGHUP"])
@pytest.mark.parametrize("proto", ["kitty", "iterm"])
def test_the_pane_player_draws_images_and_cleans_up_on_every_stop(cached_wall, sig, proto):
    import signal as sigmod

    import ptyspawn

    env = {**os.environ, **({"TERM": "xterm-kitty"} if proto == "kitty" else {"TERM_PROGRAM": "iTerm.app"})}
    proc, fd = ptyspawn.spawn([sys.executable, str(LIB / "tacctl.py"), "wall-play", "--seconds", "30",
                               "--offline"], 68, 54, env)
    term = Term(fd, KITTY_ANSWERS if proto == "kitty" else ITERM_ANSWERS)
    try:
        image = b"a=T,f=100,i=730" if proto == "kitty" else b"\x1b]1337;File=inline=1;"
        assert term.wait_for(image) and term.wait_for(b"@alex")
        time.sleep(0.3)
        os.kill(proc.pid, getattr(sigmod, sig))
        assert proc.wait(5) == 0
        assert term.wait_for(wall.LEAVE.encode(), 2)
        tail = term.got[term.got.rindex(b"\x1b[?1049h"):].decode(errors="replace")
        assert tail.rstrip("\r\n").endswith(wall.LEAVE)
        if proto == "kitty":  # after the last image drawn: all three of ours deleted, then the screen restored
            last = tail[tail.rindex("a=T,f=100"):]
            assert last.endswith("".join(wallpix.kitty_delete(i) for i in (7301, 7302, 7303)) + wall.LEAVE)
    finally:
        term.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(fd)


def test_cells_flag_and_an_unconfirmed_terminal_play_cells(cached_wall):
    import signal as sigmod

    import ptyspawn

    for args, answers in ((["--cells"], KITTY_ANSWERS), ([], {})):
        env = {**os.environ, "TERM": "xterm-kitty"}
        proc, fd = ptyspawn.spawn([sys.executable, str(LIB / "tacctl.py"), "wall-play", "--seconds", "30",
                                   "--offline", *args], 68, 54, env)
        term = Term(fd, answers)
        try:
            assert term.wait_for("▀".encode()) and term.wait_for(b"@alex")
            assert b"a=T" not in term.got and b"1337;File" not in term.got
            if args:
                assert b"a=q" not in term.got  # --cells doesn't even ask
            os.kill(proc.pid, sigmod.SIGTERM)
            assert proc.wait(5) == 0
        finally:
            term.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(fd)


def test_tac_wall_passes_cells_through(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))  # never the real ~/.cache/tac/wall
    calls = []
    import termwin

    monkeypatch.setattr(termwin, "open_play_window",
                        lambda argv, **kw: (calls.append(argv), (termwin.Opened("iTerm", (60, 50), where="pane"), ""))[1])
    monkeypatch.setattr(wall, "fetch_playlist", lambda base, picks: {"week": "", "source": "week", "pieces": [
        {"handle": "alex", "slug": "k", "title": "K", "model": "", "bytes": 1, "etag": None}]})
    assert tacctl.main(["wall", "--cells"]) == 0
    assert calls[0][-1] == "--cells" and all(termwin.SAFE_ARG.fullmatch(a) for a in calls[0])
