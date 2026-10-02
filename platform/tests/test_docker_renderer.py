"""DockerRenderer against the real tac-render:local image. Skipped without Docker or the image.

Build the image first: platform/render-image/build.sh
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import META, make_ctx
from tac_platform.config import Settings
from tac_platform.renderer import DockerRenderer

IMAGE = "tac-render:local"
LAPS = Path(__file__).resolve().parents[2] / "pieces" / "studio-fable" / "laps"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_ok(), reason=f"docker or image {IMAGE} unavailable")


def _renderer(**kw) -> DockerRenderer:
    return DockerRenderer(Settings.from_env(renderer="docker", render_image=IMAGE, **kw))


def _piece(tmp_path: Path, code: str) -> tuple[Path, Path]:
    job = tmp_path / "job"
    (job / "piece").mkdir(parents=True)
    (job / "out").mkdir()
    (job / "piece" / "piece.py").write_text(code)
    return job / "piece", job / "out"


async def _run_direct(tmp_path, code: str, timeout: float = 60):
    """Run a crafted file straight in the container (bypasses check_piece's lint on purpose)."""
    piece, out = _piece(tmp_path, code)
    r = _renderer()
    return await r._run(["python", "/in/piece.py"], piece, out, timeout), out


async def _containers() -> str:
    p = await asyncio.create_subprocess_exec("docker", "ps", "-aq", "--filter", "label=tac-render=1",
                                             stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    return out.decode().strip()


async def test_network_is_unreachable(tmp_path):
    res, _ = await _run_direct(tmp_path, """
import json, socket
out = {}
for name, fn in {
    "tcp": lambda: socket.create_connection(("1.1.1.1", 443), timeout=3),
    "dns": lambda: socket.getaddrinfo("example.com", 443),
    "udp": lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("8.8.8.8", 53)),
}.items():
    try:
        fn(); out[name] = "OK"
    except OSError as e:
        out[name] = type(e).__name__
print(json.dumps(out))
""")
    result = json.loads(res.stdout)
    assert "OK" not in result.values(), result


async def test_cannot_write_outside_out(tmp_path):
    res, out = await _run_direct(tmp_path, """
import json, os
r = {}
for p in ("/app/pwn.py", "/etc/pwn", "/in/pwn", "/usr/local/lib/pwn", "/out/ok.txt", "/tmp/ok.txt"):
    try:
        open(p, "w").write("x"); r[p] = "OK"
    except OSError as e:
        r[p] = type(e).__name__
r["uid"] = os.getuid()
print(json.dumps(r))
""")
    r = json.loads(res.stdout)
    assert r["uid"] == 65534
    assert all(r[p] != "OK" for p in ("/app/pwn.py", "/etc/pwn", "/in/pwn", "/usr/local/lib/pwn")), r
    assert r["/out/ok.txt"] == "OK" and r["/tmp/ok.txt"] == "OK"
    assert (out / "ok.txt").read_text() == "x"
    assert not (tmp_path / "job" / "piece" / "pwn").exists()


async def test_env_has_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("TAC_ADMIN_TOKEN", "admin-test")
    monkeypatch.setenv("UV_INDEX_URL", "https://u:p@example.invalid/simple")
    res, _ = await _run_direct(tmp_path, "import json, os; print(json.dumps(dict(os.environ)))")
    env = json.loads(res.stdout)
    assert not {"ANTHROPIC_API_KEY", "TAC_ADMIN_TOKEN", "UV_INDEX_URL"} & set(env)
    assert not any(v in json.dumps(env) for v in ("sk-test", "admin-test", "u:p@"))


async def test_timeout_kills_container(tmp_path):
    piece, out = _piece(tmp_path, "import time\ntime.sleep(600)\n")
    t0 = asyncio.get_running_loop().time()
    res = await _renderer()._run(["python", "/in/piece.py"], piece, out, 4)
    assert res.timed_out and res.returncode != 0
    assert asyncio.get_running_loop().time() - t0 < 30
    assert await _containers() == ""  # killed and removed


async def test_cancel_removes_container(tmp_path):
    piece, out = _piece(tmp_path, "import time\ntime.sleep(600)\n")
    task = asyncio.create_task(_renderer()._run(["python", "/in/piece.py"], piece, out, 120))
    await asyncio.sleep(3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _containers() == ""


@pytest.mark.skipif(not LAPS.is_dir(), reason="seed piece pieces/studio-fable/laps missing")
async def test_laps_renders_through_docker_pipeline(tmp_path):
    """The real seed piece, end to end in-process: check + render both run in the container."""
    async with make_ctx(tmp_path, renderer="docker", render_image=IMAGE) as ctx:
        token = await ctx.login("alex")
        meta = {**META, "title": "laps", "model": "claude-fable-5-1", "loop_s": 30.0}
        r = await ctx.submit(token, piece=(LAPS / "piece.py").read_bytes(), meta=meta)
        assert r.status_code == 202, r.text
        st = await ctx.wait(token, r.json()["id"], timeout=180)
        assert st["status"] == "in_review", st
        stats = json.loads((await ctx.app.state.db.fetchone("SELECT stats_json FROM submissions"))["stats_json"])
        assert stats["seam"] == "CLEAN" and stats["frames"] == 300
        preview = await ctx.app.state.store.get(f"submissions/{r.json()['id']}/render/preview.webp")
        assert preview[:4] == b"RIFF" and len(preview) > 100_000
    assert await _containers() == ""
