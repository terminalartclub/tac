"""Entry point of a tac-render Fly Machine (one job, then exit; auto_destroy removes the VM).

Runs as root inside the Machine's microVM. Sync stdlib only: a one-shot CLI, no uv, no deps.

    step                                  network   privileges
    1. GET  $TAC_IN_URL  (input tar)      yes       root (trusted code, holds only 2 presigned URLs)
    2. probe: new netns has no routes     NO        nobody
    3. check_piece.py /job/in             NO        nobody, new net namespace, rlimits
    4. render_piece.py /job/in --out      NO        nobody, new net namespace, rlimits
    5. PUT  $TAC_OUT_URL (output tar)     yes       root (after every piece process has exited)

Steps 2-4 run in a fresh network namespace (os.unshare(CLONE_NEWNET)): only a downed loopback,
no routes. If the namespace can't be created, or the probe sees any route, the job stops
BEFORE any untrusted code runs and uploads result {"error": "network isolation unavailable"}
(fail closed). The piece runs as uid 65534 with no_new_privs, so it can't re-enter the
Machine's network namespace (setns needs CAP_SYS_ADMIN).
"""

import ctypes
import io
import json
import os
import resource
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

JOB = Path("/job")
IN, OUT = JOB / "in", JOB / "out"
NOBODY = 65534
MAX_IN = 8 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
MAX_STREAM = 256 * 1024
CHILD_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
PROBE = r"""
import json, socket
v4 = [l for l in open("/proc/net/route").read().splitlines()[1:] if l.strip()]  # header, then routes
# a fresh netns carries kernel IPv6 *reject* routes on lo (flags & RTF_REJECT 0x200); anything else is a path out
v6 = [l for l in open("/proc/net/ipv6_route").read().splitlines() if l.strip() and not int(l.split()[8], 16) & 0x200]
try:
    socket.create_connection(("1.1.1.1", 443), timeout=2)
    tcp = "OK"
except OSError as e:
    tcp = type(e).__name__
print(json.dumps({"routes": v4 + v6, "tcp": tcp}))
"""


def _no_new_privs() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")


def _isolate() -> None:
    """preexec_fn for every untrusted step: new netns, limits, drop to nobody. Raises = no exec."""
    os.unshare(os.CLONE_NEWNET)
    os.setsid()
    for res, val in ((resource.RLIMIT_CPU, 200), (resource.RLIMIT_NOFILE, 256),
                     (resource.RLIMIT_FSIZE, 200 * 1024 * 1024), (resource.RLIMIT_NPROC, 256)):
        resource.setrlimit(res, (val, val))
    os.setgroups([])
    os.setgid(NOBODY)
    os.setuid(NOBODY)
    _no_new_privs()


def run_isolated(argv: list[str], timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd="/tmp", env=CHILD_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, preexec_fn=_isolate)
        rc, out, err, timed_out = p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as e:
        rc, out, err, timed_out = -9, e.stdout or b"", e.stderr or b"", True
    except (OSError, subprocess.SubprocessError) as e:  # isolation itself failed: piece never ran
        return {"returncode": -1, "stdout": "", "stderr": f"isolation failed: {e}", "timed_out": False,
                "isolation_failed": True}
    return {"returncode": rc, "stdout": out[:MAX_STREAM].decode("utf-8", "replace"),
            "stderr": err[-MAX_STREAM:].decode("utf-8", "replace"), "timed_out": timed_out,
            "seconds": round(time.monotonic() - t0, 2)}


def network_is_dropped() -> tuple[bool, dict]:
    r = run_isolated([sys.executable, "-c", PROBE], 20)
    try:
        probe = json.loads(r["stdout"])
    except ValueError:
        return False, r
    ok = r["returncode"] == 0 and probe["routes"] == [] and probe["tcp"] != "OK"
    return ok, probe


def fetch_input(url: str) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read(MAX_IN + 1)
    if len(data) > MAX_IN:
        raise ValueError("input bundle too large")
    IN.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(IN, filter="data")  # no absolute paths, no links out, no devices
    for p in [IN, *IN.rglob("*")]:
        os.chown(p, 0, 0)
        p.chmod(0o755 if p.is_dir() else 0o644)  # piece can read its input, not change it


def pack_output(result: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if OUT.is_dir():
            for p in sorted(OUT.rglob("*")):
                # regular files only, never followed symlinks, size-capped
                if p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_FILE:
                    tar.add(p, arcname=f"out/{p.relative_to(OUT)}", recursive=False)
        meta = json.dumps(result).encode()
        info = tarfile.TarInfo("result.json")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    return buf.getvalue()


def upload(url: str, data: bytes) -> None:
    req = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": "application/gzip"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def main() -> int:
    in_url, out_url = os.environ["TAC_IN_URL"], os.environ["TAC_OUT_URL"]
    check_t = float(os.environ.get("TAC_CHECK_TIMEOUT", "60"))
    render_t = float(os.environ.get("TAC_RENDER_TIMEOUT", "300"))  # outer: kill render_piece.py after this
    # inner: render_piece.py's own --timeout, a margin earlier, so it exits 124 with a message instead of being
    # killed silently (an API that predates TAC_RENDER_PIECE_TIMEOUT: derive it)
    piece_t = float(os.environ.get("TAC_RENDER_PIECE_TIMEOUT", render_t - min(20.0, render_t / 4)))
    result: dict = {"check": None, "render": None, "error": None}
    try:
        fetch_input(in_url)
        OUT.mkdir(parents=True)
        os.chown(OUT, NOBODY, NOBODY)
        ok, probe = network_is_dropped()
        result["isolation"] = probe
        if not ok:
            result["error"] = "network isolation unavailable"
        else:
            result["check"] = run_isolated([sys.executable, "/app/check_piece.py", str(IN)], check_t)
            if result["check"]["returncode"] == 0:
                result["render"] = run_isolated(
                    [sys.executable, "/app/render_piece.py", str(IN), "--out", str(OUT), "--timeout", f"{piece_t:g}"],
                    render_t
                )
    except Exception as e:  # noqa: BLE001 - report, don't crash silently
        result["error"] = f"bootstrap: {type(e).__name__}: {e}"[:500]
    upload(out_url, pack_output(result))
    return 0 if not result["error"] else 3


if __name__ == "__main__":
    sys.exit(main())
