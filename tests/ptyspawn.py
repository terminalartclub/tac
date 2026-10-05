"""Start a program on a new pseudo-terminal, as its controlling terminal (so a resize really sends it SIGWINCH and
^C really sends SIGINT), without forking this Python process: subprocess's C fork+exec runs a tiny launcher that
takes the terminal (setsid + TIOCSCTTY) and execs the program. pty.fork() from a pytest process that has ever
started a thread (the OS's, not only Python's) warns that forking a multi-threaded process may deadlock."""

import fcntl
import os
import pty
import struct
import subprocess
import sys
import termios

LAUNCH = ("import fcntl, os, sys, termios; os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
          "os.execve(sys.argv[1], sys.argv[1:], os.environ)")


def winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn(argv: list[str], cols: int, rows: int, env: dict | None = None) -> tuple[subprocess.Popen, int]:
    """(process, master fd). The caller reads the master and waits on / kills the process."""
    master, slave = pty.openpty()
    winsize(slave, cols, rows)
    proc = subprocess.Popen([sys.executable, "-c", LAUNCH, *argv], stdin=slave, stdout=slave, stderr=slave,
                            env=env if env is not None else os.environ, close_fds=True)
    os.close(slave)
    return proc, master
