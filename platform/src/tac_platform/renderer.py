"""Render backends: where check_piece.py / render_piece.py touch untrusted code.

  local  -> run_limited subprocess on this host (rlimits + scrubbed env). NOT isolated; dev only.
  docker -> one throwaway container per call: no network, read-only rootfs, non-root, no caps,
            2 GB / 1 CPU / 256 pids, piece dir mounted read-only, only /out writable.

  fly-machine -> one throwaway Fly Machine per job (fly_machine.py): check + render in one VM,
            untrusted steps in a fresh network namespace, I/O via presigned URLs.

Every backend implements run_job(): the static check, then (only if it passed) the render.
"""

import asyncio
import logging
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .sandbox import RunResult, run_limited

log = logging.getLogger("tac.renderer")

DOCKER_ENV_ALLOW = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
                    "DOCKER_TLS_VERIFY")
MAX_OUTPUT = 256 * 1024


@dataclass
class JobResult:
    check: RunResult | None
    render: RunResult | None = None
    backend_error: str | None = None  # infrastructure failure: not the piece's fault
    timed_out: bool = False  # the whole job (check + render) ran past its wall clock
    isolation: dict | None = None  # the render backend's network-isolation probe (Fly: result.json "isolation")


class Renderer(Protocol):
    async def run_job(self, piece_dir: Path, out_dir: Path, work: Path) -> JobResult: ...


def check_passed(res: RunResult) -> bool:
    return not res.timed_out and res.returncode == 0


class StepwiseRenderer:
    """Backends with separate check/render calls (local, docker)."""

    async def run_job(self, piece_dir: Path, out_dir: Path, work: Path) -> JobResult:
        check = await self.check(piece_dir, work)  # type: ignore[attr-defined]
        if not check_passed(check):
            return JobResult(check=check)
        return JobResult(check=check, render=await self.render(piece_dir, out_dir, work))  # type: ignore[attr-defined]


class LocalRenderer(StepwiseRenderer):
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    async def check(self, piece_dir: Path, work: Path) -> RunResult:
        argv = [self.s.tools_python, str(self.s.tools_dir / "check_piece.py"), str(piece_dir)]
        return await run_limited(argv, work, self.s.check_timeout_s)

    async def render(self, piece_dir: Path, out_dir: Path, work: Path) -> RunResult:
        argv = [self.s.tools_python, str(self.s.tools_dir / "render_piece.py"), str(piece_dir), "--out", str(out_dir),
                "--timeout", f"{self.s.render_piece_timeout_s:g}"]
        return await run_limited(argv, work, self.s.render_timeout_s)


def _open_perms(piece_dir: Path, out_dir: Path | None) -> None:
    """The container runs as nobody: let it read /in and write /out (mkdtemp dirs are 0700)."""
    for p in piece_dir.rglob("*"):
        p.chmod(p.stat().st_mode | stat.S_IRGRP | stat.S_IROTH | (stat.S_IXGRP | stat.S_IXOTH if p.is_dir() else 0))
    piece_dir.chmod(0o755)
    piece_dir.parent.chmod(0o755)
    if out_dir is not None:
        out_dir.chmod(0o777)


def _docker_env() -> dict[str, str]:
    """The docker CLI's own env: enough to find the daemon, nothing else (no API keys)."""
    return {k: v for k, v in os.environ.items() if k in DOCKER_ENV_ALLOW}


async def _docker(*args: str, timeout: float = 30) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, env=_docker_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        return -1, out, err
    return proc.returncode or 0, out, err


class DockerRenderer(StepwiseRenderer):
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.image = settings.render_image

    def run_args(self, name: str, piece_dir: Path, out_dir: Path | None) -> list[str]:
        args = [
            "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:size=256m",
            "--memory", "2g", "--memory-swap", "2g", "--cpus", "1", "--pids-limit", "256",
            "--ulimit", "nofile=256:256", "--ulimit", "fsize=209715200:209715200",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--label", "tac-render=1",
            "-v", f"{piece_dir}:/in:ro",
        ]
        if out_dir is not None:
            args += ["-v", f"{out_dir}:/out:rw"]
        return args + [self.image]

    async def available(self) -> str | None:
        """None if usable, else why not."""
        rc, _, err = await _docker("image", "inspect", "--format", "{{.Id}}", self.image)
        if rc != 0:
            return f"docker image {self.image} unavailable: {err.decode(errors='replace').strip()[:200]}"
        return None

    async def _run(self, argv_tail: list[str], piece_dir: Path, out_dir: Path | None, timeout: float) -> RunResult:
        await asyncio.to_thread(_open_perms, piece_dir, out_dir)
        name = f"tac-render-{secrets.token_hex(6)}"
        argv = ["docker", *self.run_args(name, piece_dir, out_dir), *argv_tail]
        proc = await asyncio.create_subprocess_exec(
            *argv, env=_docker_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout)
            except TimeoutError:
                timed_out = True
                await _docker("kill", name)  # killing the CLI would leave the container running
                out, err = await proc.communicate()
        finally:
            # every exit path (normal, timeout, cancellation): the container must be gone
            if proc.returncode is None:
                await asyncio.shield(_docker("kill", name))
                await asyncio.shield(proc.wait())
            await asyncio.shield(_docker("rm", "-f", name))
        rc = proc.returncode if proc.returncode is not None else -1
        if rc == 125:  # docker itself failed (daemon, image, mount): not the piece's fault
            log.error("docker run failed: %s", err.decode(errors="replace")[-500:])
        return RunResult(
            returncode=rc,
            stdout=out[:MAX_OUTPUT].decode("utf-8", "replace"),
            stderr=err[-MAX_OUTPUT:].decode("utf-8", "replace"),
            timed_out=timed_out,
        )

    async def check(self, piece_dir: Path, work: Path) -> RunResult:
        return await self._run(["python", "/app/check_piece.py", "/in"], piece_dir, None, self.s.check_timeout_s)

    async def render(self, piece_dir: Path, out_dir: Path, work: Path) -> RunResult:
        return await self._run(
            ["python", "/app/render_piece.py", "/in", "--out", "/out", "--timeout", f"{self.s.render_piece_timeout_s:g}"],
            piece_dir, out_dir, self.s.render_timeout_s
        )


def make_renderer(settings: Settings, store=None) -> Renderer:
    if settings.renderer == "fly-machine":
        from .fly_machine import FlyMachineRenderer

        return FlyMachineRenderer(settings, store)
    if settings.renderer == "docker":
        return DockerRenderer(settings)
    if settings.renderer == "local":
        if settings.env == "prod":  # belt and braces: check_prod_safety already refuses this
            raise RuntimeError("local renderer is not allowed in prod")
        return LocalRenderer(settings)
    raise RuntimeError(f"unknown TAC_RENDERER {settings.renderer!r}")
