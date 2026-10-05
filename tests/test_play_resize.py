"""`tac play` follows a resized terminal: after the pane changes size, the next frame is the piece drawn fresh at
the new size, with no stale cells. Driven in a real pty, read back with pyte. No window opened."""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

import pytest

from conftest import LIB

import pyte  # a test dependency (README, CI): never skipped silently

BOX = (
    "for i in range(100000):\n"
    "    canvas.clear()\n"
    "    canvas.write(Text('+' + '-' * (width - 2) + '+'))\n"
    "    for _ in range(height - 3):\n"
    "        canvas.write(Text('|' + ' ' * (width - 2) + '|'))\n"
    "    canvas.write(Text('+' + '-' * (width - 2) + '+'))\n"
    "    await sleep(0.05)\n"
)


def box(cols: int, rows: int) -> list[str]:
    """What the piece draws at cols x rows (height - 1 lines: the last terminal row stays free)."""
    edge = "+" + "-" * (cols - 2) + "+"
    return [edge] + ["|" + " " * (cols - 2) + "|"] * (rows - 3) + [edge]


def winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Pty:
    def __init__(self, piece: Path, cols: int, rows: int) -> None:
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # the child: the pty is its controlling terminal, so a resize sends it SIGWINCH
            os.execv(sys.executable, [sys.executable, str(LIB / "vscreen.py"), "play", str(piece)])
        winsize(self.fd, cols, rows)
        os.kill(self.pid, signal.SIGWINCH)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.stream.feed(os.read(self.fd, 65536))
                except OSError:
                    return

    def lines(self) -> list[str]:
        return [ln.rstrip() for ln in self.screen.display]

    def resize(self, cols: int, rows: int) -> None:
        self.screen.resize(rows, cols)
        winsize(self.fd, cols, rows)  # the kernel sends the child's process group SIGWINCH

    def close(self) -> None:
        os.kill(self.pid, signal.SIGKILL)  # our own child only
        os.waitpid(self.pid, 0)
        os.close(self.fd)


def drawn(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln]


@pytest.mark.parametrize("start,end", [((60, 20), (40, 14)), ((40, 14), (70, 24))])
def test_a_resized_pane_is_redrawn_fresh_at_the_new_size(tmp_path, start, end):
    piece = tmp_path / "box.py"
    piece.write_text(BOX)
    p = Pty(piece, *start)
    try:
        p.pump(2.5)  # uv-free: this python already has rich; first frames
        assert drawn(p.lines()) == box(*start)
        p.resize(*end)
        p.pump(1.0)  # past the 100 ms debounce and a few frames
        assert drawn(p.lines()) == box(*end)  # the piece at the new size, nothing stale around it
        assert all(len(ln) <= end[0] for ln in p.lines())
    finally:
        p.close()


def test_a_drag_resize_settles_on_the_last_size(tmp_path):
    piece = tmp_path / "box.py"
    piece.write_text(BOX)
    p = Pty(piece, 60, 20)
    try:
        p.pump(2.5)
        for cols in (58, 55, 52, 49, 46):  # a drag: one SIGWINCH every 20 ms
            p.resize(cols, 20)
            p.pump(0.02)
        p.pump(1.0)
        assert drawn(p.lines()) == box(46, 20)
    finally:
        p.close()


def test_a_pane_too_narrow_for_the_piece_plays_it_cropped_and_recovers_when_widened(tmp_path):
    """No error text: the piece plays on at the last size it ran at, centred and cropped to the pane, with a
    one-line note when the crop hides more than a quarter of it."""
    piece = tmp_path / "picky.py"
    piece.write_text("assert width >= 50, 'needs 50 columns'\n" + BOX)
    p = Pty(piece, 60, 20)
    try:
        p.pump(2.5)
        assert drawn(p.lines()) == box(60, 20)
        p.resize(40, 20)
        p.pump(1.0)
        lines = p.lines()
        assert lines[0] == "-" * 40  # the 60-wide box's top edge, centred: columns 10-49 of it
        assert lines[1] == "" and lines[18] == "-" * 40  # inside, and the bottom edge (the box is 19 rows)
        assert lines[19] == "60x20 doesn't fit this 40x20 pane: widen"  # the note, cut to the pane
        os.kill(p.pid, 0)  # still playing
        p.resize(56, 20)
        p.pump(1.0)
        assert drawn(p.lines()) == box(56, 20)  # tried again at the new size: it runs there
    finally:
        p.close()


def test_a_piece_that_never_runs_in_a_narrow_pane_starts_at_its_own_size_cropped(tmp_path):
    piece = tmp_path / "picky.py"
    piece.write_text("assert width >= 50, 'needs 50 columns'\n" + BOX)
    p = Pty(piece, 40, 30)
    try:
        p.pump(3.0)
        lines = p.lines()
        assert all(ln == "" for ln in lines[:29])  # 80x66 centred in 40x30: the inside of the box, cropped
        assert "80x66 doesn't fit this 40x30 pane" in lines[29]
        os.kill(p.pid, 0)
    finally:
        p.close()


def test_frames_are_written_by_absolute_address_only():
    """Every row starts at ESC[r;1H and ends cleared (ESC[K): no relative cursor moves a reflowing terminal
    could misplace."""
    import vscreen

    grid = vscreen.to_cells(["x" * 10] * 4, 10, 4)
    s = vscreen.frame_ansi(grid, 8, 4)
    assert s.startswith("\x1b[H") and all(f"\x1b[{r};1H" in s for r in (1, 2, 3, 4))
    assert s.count("\x1b[K") == 4 and "\x1b[A" not in s and "\x1b[1A" not in s and "\n" not in s


def test_a_piece_that_catches_exception_around_sleep_still_follows_a_resize(tmp_path):
    piece = tmp_path / "guarded.py"
    piece.write_text(BOX.replace("    await sleep(0.05)\n",
                                 "    try:\n        await sleep(0.05)\n    except Exception:\n        pass\n"))
    p = Pty(piece, 60, 20)
    try:
        p.pump(2.5)
        assert drawn(p.lines()) == box(60, 20)
        p.resize(44, 16)
        p.pump(1.0)
        assert drawn(p.lines()) == box(44, 16)
    finally:
        p.close()


def test_the_repaint_never_clears_the_scrollback():
    src = (LIB / "vscreen.py").read_text()
    assert "\\x1b[3J" not in src  # ESC[3J wipes the scrollback in some terminals (the paste fallback runs in yours)


STRIPES = (
    "for i in range(100000):\n"
    "    canvas.clear()\n"
    "    for y in range(height - 1):\n"
    "        t = Text()\n"
    "        for x in range(width):\n"
    "            t.append('▀', style=f'rgb({(x * 7) % 256},{(y * 11) % 256},90) on rgb(20,{(x + y) % 256},{(y * 5) % 256})')\n"
    "        canvas.write(t)\n"
    "    await sleep(0.1)\n"
)


def test_the_screen_is_exactly_the_published_renderers_cells_after_a_resize(tmp_path):
    """Glyphs AND colours: what the terminal shows is vscreen.to_cells of the frame at the pane's size, cell for
    cell, so a live play looks like the published render, before and after a resize."""
    import asyncio

    import vscreen

    piece = tmp_path / "stripes.py"
    piece.write_text(STRIPES)
    p = Pty(piece, 50, 16)
    try:
        p.pump(2.5)
        p.resize(37, 12)
        p.pump(1.0)
        cap = asyncio.run(vscreen.capture(STRIPES, 37, 12, times=[0.0]))
        grid = vscreen.to_cells(cap.samples[0][1], 37, 12)
        hexc = lambda c: "%02x%02x%02x" % c  # noqa: E731
        for y in range(12):
            for x in range(37):
                got, want = p.screen.buffer[y][x], grid[y][x]
                assert (got.data, got.fg, got.bg) == (want.ch, hexc(want.fg), hexc(want.bg)), (x, y)
    finally:
        p.close()
