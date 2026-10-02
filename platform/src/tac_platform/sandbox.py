"""Run builder-community's tools against untrusted pieces, with best-effort limits.

NOT a sandbox on macOS: the child runs as our user, can read our files and reach the
network. See SECURITY.md for what prod must do instead (ephemeral Fly Machine / nsjail).
"""

import asyncio
import os
import resource
import signal
from dataclasses import dataclass
from pathlib import Path

LIMITS = {
    resource.RLIMIT_CPU: 180,  # seconds of CPU
    resource.RLIMIT_AS: 3 * 1024**3,  # address space (ignored by the macOS kernel)
    resource.RLIMIT_NOFILE: 256,
    resource.RLIMIT_FSIZE: 200 * 1024**2,  # largest file the child may write
}

# Only these survive into the child. Everything else (API keys, admin token) is dropped.
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL")
ENV_ALLOW_PREFIX = ("UV_",)
ENV_DENY_SUBSTR = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")


def scrubbed_env(tmpdir: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if (k in ENV_ALLOW or k.startswith(ENV_ALLOW_PREFIX))
        and not any(s in k.upper() for s in ENV_DENY_SUBSTR)
    }
    env["TMPDIR"] = str(tmpdir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _apply_limits() -> None:  # runs in the child between fork and exec
    for res, value in LIMITS.items():
        try:
            _, hard = resource.getrlimit(res)
            soft = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(res, (soft, hard))
        except (ValueError, OSError):
            pass  # macOS refuses some (e.g. RLIMIT_AS above current usage); best effort


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


async def run_limited(argv: list[str], cwd: Path, timeout_s: float, max_output: int = 256 * 1024) -> RunResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=scrubbed_env(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=_apply_limits,
        start_new_session=True,  # own process group, so a timeout kills grandchildren too
    )
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        timed_out = True
        _killpg(proc.pid)
        out, err = await proc.communicate()
    except asyncio.CancelledError:  # server shutting down: never leave a render running
        _killpg(proc.pid)
        await asyncio.shield(proc.wait())
        raise
    _killpg(proc.pid)  # reap anything the piece left running in its group
    return RunResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out[:max_output].decode("utf-8", "replace"),
        stderr=err[-max_output:].decode("utf-8", "replace"),
        timed_out=timed_out,
    )
