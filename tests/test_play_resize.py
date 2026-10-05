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


def test_a_piece_that_fails_at_the_new_size_says_so_and_recovers_when_widened(tmp_path):
    piece = tmp_path / "picky.py"
    piece.write_text("assert width >= 50, 'needs 50 columns'\n" + BOX)
    p = Pty(piece, 60, 20)
    try:
        p.pump(2.5)
        assert drawn(p.lines()) == box(60, 20)
        p.resize(40, 20)
        p.pump(1.0)
        said = " ".join(drawn(p.lines()))
        assert said == "this piece doesn't run at 40x20 (AssertionError): widen the pane, or play it with --window"
        os.kill(p.pid, 0)  # still running, not crashed out of the pane
        p.resize(56, 20)
        p.pump(1.0)
        assert drawn(p.lines()) == box(56, 20)
    finally:
        p.close()


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
