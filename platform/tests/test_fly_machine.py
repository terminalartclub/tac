"""FlyMachineRenderer against a MOCKED Machines API (httpx.MockTransport). No Fly resources, no spend.

The fake "machine" plays fly_bootstrap's role over the real presigned-URL routes of the app:
GET the input bundle, produce outputs, PUT the output bundle. One test (Docker only) runs the
real image + real fly_bootstrap.py as the machine, to prove the network drop for untrusted steps.
"""

import asyncio
import io
import json
import shutil
import socket
import subprocess
import tarfile
from pathlib import Path

import httpx
import pytest

from conftest import META, PIECE, make_ctx
from tac_platform.fly_machine import FlyMachineRenderer, bundle, unpack

TOKEN = "fly-deploy-token-for-tests"
STATS = {"motion_median": 0.03, "seam": "CLEAN", "void": 0.6, "frames": 300}


def out_tar(result: dict, files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in {**(files or {}), "result.json": json.dumps(result).encode()}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def good_outputs() -> dict[str, bytes]:
    from PIL import Image

    frames = [Image.new("RGB", (40, 40), (i * 20, 10, 10)) for i in range(4)]
    webp, jpg = io.BytesIO(), io.BytesIO()
    frames[0].save(webp, "WEBP", save_all=True, append_images=frames[1:])
    frames[0].save(jpg, "JPEG")
    return {"out/preview.webp": webp.getvalue(), "out/og.jpg": jpg.getvalue(),
            "out/stats.json": json.dumps(STATS).encode()}


OK_RESULT = {"check": {"returncode": 0, "stdout": '{"ok": true, "reasons": []}', "stderr": "", "timed_out": False},
             "render": {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}, "error": None}


class FakeMachinesApi:
    """Records calls; `behave(app_client, body)` plays the machine (may upload output)."""

    def __init__(self, app, behave=None, wait_status: int = 200, create_status: int = 200) -> None:
        self.app = app
        self.behave = behave
        self.wait_status = wait_status
        self.create_status = create_status
        self.calls: list[tuple[str, str, dict]] = []
        self.created: dict | None = None
        self.auth: set[str] = set()
        self.machine_task: asyncio.Task | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("authorization", ""))
        path, method = request.url.path, request.method
        self.calls.append((method, path, dict(request.url.params)))
        if method == "POST" and path == "/v1/apps/tac-render/machines":
            if self.create_status != 200:
                return httpx.Response(self.create_status, json={"error": "capacity"})
            self.created = json.loads(request.content)
            if self.behave:
                self.machine_task = asyncio.create_task(self.behave(self, self.created))
            return httpx.Response(200, json={"id": "m-123", "instance_id": "01HX", "state": "created"})
        if method == "GET" and path.endswith("/wait"):
            if self.wait_status == 200 and self.machine_task:
                await self.machine_task
            return httpx.Response(self.wait_status, json={"ok": self.wait_status == 200, "state": "stopped"})
        if method == "GET" and path == "/v1/apps/tac-render/machines/m-123":
            return httpx.Response(200, json={"id": "m-123", "events": [
                {"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": 137, "oom_killed": True}}}]})
        if method == "DELETE" and path == "/v1/apps/tac-render/machines/m-123":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def deleted(self) -> bool:
        return any(m == "DELETE" and p.endswith("/m-123") and q.get("force") == "true" for m, p, q in self.calls)

    async def io(self, method: str, url: str, content: bytes | None = None) -> httpx.Response:
        """The machine's own HTTP: hits the app's presigned routes in-process."""
        path = url.split("http://127.0.0.1:8790", 1)[1]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://m") as c:
            return await c.request(method, path, content=content)


async def bootstrap_ok(api: FakeMachinesApi, body: dict) -> None:
    env = body["config"]["env"]
    r = await api.io("GET", env["TAC_IN_URL"])
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
        assert "piece.py" in names and "meta.json" in names
        assert tar.extractfile("piece.py").read() == PIECE
    assert (await api.io("PUT", env["TAC_OUT_URL"], out_tar(OK_RESULT, good_outputs()))).status_code == 200


async def _ctx_with(tmp_path, monkeypatch, api_factory, **kw):
    monkeypatch.setenv("FLY_API_TOKEN", TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-leak")
    cm = make_ctx(tmp_path, renderer="fly-machine", fly_render_image="registry.fly.io/tac-render:test", **kw)
    ctx = await cm.__aenter__()
    api = api_factory(ctx.app)
    st = ctx.app.state
    st.pipeline.renderer = FlyMachineRenderer(st.settings, st.store, transport=api.transport(),
                                              wait_total_s=kw.get("render_timeout_s", 300))
    return cm, ctx, api


async def _submit(ctx):
    token = await ctx.login("alex")
    sub = (await ctx.submit(token)).json()
    return await ctx.wait(token, sub["id"], timeout=20)


async def test_happy_path_create_wait_destroy(tmp_path, monkeypatch):
    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, bootstrap_ok))
    try:
        st = await _submit(ctx)
        assert st["status"] == "in_review", st
        stats = json.loads((await ctx.app.state.db.fetchone("SELECT stats_json FROM submissions"))["stats_json"])
        assert stats == STATS
        assert await ctx.app.state.store.list("render-io") == []  # job I/O cleaned up
    finally:
        await cm.__aexit__(None, None, None)
    cfg = api.created["config"]
    assert cfg["auto_destroy"] is True and cfg["restart"] == {"policy": "no"}
    assert cfg["guest"] == {"cpu_kind": "shared", "cpus": 1, "memory_mb": 2048}
    assert cfg["services"] == [] and cfg["dns"] == {"skip_registration": True}
    assert cfg["image"] == "registry.fly.io/tac-render:test"
    assert cfg["init"]["exec"] == ["/usr/local/bin/python", "/app/fly_bootstrap.py"]
    assert set(cfg["env"]) == {"TAC_IN_URL", "TAC_OUT_URL", "TAC_CHECK_TIMEOUT", "TAC_RENDER_TIMEOUT"}
    blob = json.dumps(api.created)
    assert TOKEN not in blob and "sk-must-not-leak" not in blob and "test-admin-token" not in blob
    assert api.auth == {f"Bearer {TOKEN}"}
    waits = [q for m, p, q in api.calls if p.endswith("/wait")]
    assert waits and all(q["state"] == "stopped" and q["instance_id"] == "01HX" and int(q["timeout"]) <= 60 for q in waits)
    assert api.deleted()


async def test_destroy_on_wait_error(tmp_path, monkeypatch):
    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, None, wait_status=500))
    try:
        st = await _submit(ctx)
        assert st["status"] == "rejected" and st["reasons"] == ["render backend unavailable; please resubmit later"]
    finally:
        await cm.__aexit__(None, None, None)
    assert api.deleted()


async def test_destroy_on_timeout(tmp_path, monkeypatch):
    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, None, wait_status=408),
                                   render_timeout_s=1.5)
    try:
        st = await _submit(ctx)
        assert st["status"] == "rejected" and st["reasons"] == ["render timed out after 2 s"]
    finally:
        await cm.__aexit__(None, None, None)
    assert api.deleted()
    assert len([1 for m, p, q in api.calls if p.endswith("/wait")]) >= 2  # looped on 408


async def test_no_output_reports_exit_event(tmp_path, monkeypatch, caplog):
    async def dies(api, body):  # OOM before upload
        return None

    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, dies))
    try:
        st = await _submit(ctx)
        assert st["status"] == "rejected" and "render backend unavailable" in st["reasons"][0]
    finally:
        await cm.__aexit__(None, None, None)
    assert "exit_code=137, oom_killed=True" in caplog.text
    assert api.deleted()


async def test_isolation_unavailable_fails_closed(tmp_path, monkeypatch, caplog):
    async def no_netns(api, body):
        result = {"check": None, "render": None, "error": "network isolation unavailable"}
        await api.io("PUT", body["config"]["env"]["TAC_OUT_URL"], out_tar(result))

    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, no_netns))
    try:
        st = await _submit(ctx)
        assert st["status"] == "rejected"
    finally:
        await cm.__aexit__(None, None, None)
    assert "network isolation unavailable" in caplog.text and api.deleted()


async def test_create_failure_no_destroy_needed(tmp_path, monkeypatch):
    cm, ctx, api = await _ctx_with(tmp_path, monkeypatch, lambda app: FakeMachinesApi(app, None, create_status=503))
    try:
        st = await _submit(ctx)
        assert st["status"] == "rejected" and "render backend unavailable" in st["reasons"][0]
        assert await ctx.app.state.store.list("render-io") == []
    finally:
        await cm.__aexit__(None, None, None)
    assert not any(m == "DELETE" for m, _, _ in api.calls)


async def test_cancel_still_destroys(tmp_path, monkeypatch):
    async def hangs(api, body):
        await asyncio.sleep(3600)

    async with make_ctx(tmp_path, worker_enabled=False) as ctx:
        monkeypatch.setenv("FLY_API_TOKEN", TOKEN)
        api = FakeMachinesApi(ctx.app, hangs)
        r = FlyMachineRenderer(ctx.settings, ctx.app.state.store, transport=api.transport())
        piece = tmp_path / "p"
        piece.mkdir()
        (piece / "piece.py").write_bytes(PIECE)
        task = asyncio.create_task(r.run_job(piece, tmp_path / "o", tmp_path))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        api.machine_task.cancel()
        assert api.deleted()
        assert await ctx.app.state.store.list("render-io") == []


async def test_presigned_urls(ctx):
    store = ctx.app.state.store
    await store.put("render-io/j1/in.tar.gz", b"payload")
    get_url = store.presign("render-io/j1/in.tar.gz", "GET", 60)
    put_url = store.presign("render-io/j1/out.tar.gz", "PUT", 60)
    path = lambda u: u.split("http://127.0.0.1:8790", 1)[1]  # noqa: E731
    async with ctx.client() as c:
        assert (await c.get(path(get_url))).content == b"payload"
        assert (await c.put(path(put_url), content=b"result")).status_code == 200
        assert await store.get("render-io/j1/out.tar.gz") == b"result"
        assert (await c.put(path(get_url), content=b"x")).status_code in (403, 405)  # GET URL can't write
        assert (await c.get(path(put_url))).status_code == 403  # PUT URL can't read
        assert (await c.get(path(get_url).replace("in.tar.gz", "other"))).status_code == 403  # bound to key
        assert (await c.get(path(get_url)[:-4] + "0000")).status_code == 403
        assert (await c.get(path(get_url).replace("sig=", "sig=%C3%A9"))).status_code == 403
    with pytest.raises(ValueError):
        store.presign("submissions/x/piece.py", "GET", 60)  # only render-io/ keys
    expired = store.presign("render-io/j1/in.tar.gz", "GET", -1)
    async with ctx.client() as c:
        assert (await c.get(path(expired))).status_code == 403


def test_unpack_rejects_escapes(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, kind in (("out/../../evil.txt", tarfile.REGTYPE), ("/abs.txt", tarfile.REGTYPE),
                           ("out/link", tarfile.SYMTYPE), ("out/ok.txt", tarfile.REGTYPE)):
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = 2
                tar.addfile(info, io.BytesIO(b"ok"))
    out = tmp_path / "out"
    out.mkdir()
    try:
        unpack(buf.getvalue(), out)
    except tarfile.TarError:
        pass  # refusing the whole bundle is also fine
    assert not (tmp_path / "evil.txt").exists() and not Path("/abs.txt").exists()
    assert not (out / "link").exists()


def test_bundle_skips_symlinks(tmp_path):
    (tmp_path / "piece.py").write_text("x")
    (tmp_path / "leak").symlink_to("/etc/passwd")
    with tarfile.open(fileobj=io.BytesIO(bundle(tmp_path)), mode="r:gz") as tar:
        assert tar.getnames() == ["piece.py"]


# ------------------------------------------------------------------ real image as the "machine"

IMAGE = "tac-render:local"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


LAPS = Path(__file__).resolve().parents[2] / "pieces" / "studio-fable" / "laps"


async def _run(argv: list[str]) -> str:
    """Async on purpose: the in-test API server shares this event loop."""
    p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(p.communicate(), 90)
    return out.decode() + ("" if out.strip() else err.decode())


@pytest.mark.skipif(not _docker_ok(), reason=f"docker or {IMAGE} unavailable")
async def test_real_bootstrap_drops_network_for_piece(tmp_path, monkeypatch):
    """The Fly path end to end with Docker standing in for the Machine (root + CAP_SYS_ADMIN, like a VM):
    the bootstrap reaches the API over presigned URLs, the untrusted steps run with no network."""
    import uvicorn

    port = _free_port()
    base = f"http://host.docker.internal:{port}"
    # github auth: dev login refuses a non-loopback base URL
    async with make_ctx(tmp_path, worker_enabled=False, public_base_url=base, auth_mode="github") as ctx:
        server = uvicorn.Server(uvicorn.Config(ctx.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        serve = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)

        async def docker_machine(api, body):
            env = [a for k, v in body["config"]["env"].items() for a in ("-e", f"{k}={v}")]
            p = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm", "--user", "0", "--cap-add", "SYS_ADMIN", "--memory", "2g",
                "--label", "tac-render=1", *env, IMAGE, *body["config"]["init"]["exec"],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            api.machine_out = await p.communicate()

        monkeypatch.setenv("FLY_API_TOKEN", TOKEN)
        api = FakeMachinesApi(ctx.app, docker_machine)
        r = FlyMachineRenderer(ctx.settings, ctx.app.state.store, transport=api.transport())
        try:
            # the real seed piece through the real bootstrap (check + render in the netns)
            piece, out = tmp_path / "piece", tmp_path / "out"
            piece.mkdir()
            out.mkdir()
            (piece / "piece.py").write_bytes((LAPS / "piece.py").read_bytes() if LAPS.is_dir() else PIECE)
            (piece / "meta.json").write_text(json.dumps({**META, "title": "laps", "model": "claude-fable-5-1"}))
            job = await r.run_job(piece, out, tmp_path)
            assert job.backend_error is None, (job.backend_error, api.machine_out)
            assert job.check.returncode == 0, job.check
            assert job.render is not None and job.render.returncode == 0, job.render.stderr[-500:]
            assert {"preview.webp", "og.jpg", "stats.json"} <= {p.name for p in out.iterdir()}
            assert api.deleted()

            # the isolation primitive itself, inside the same image, as the bootstrap uses it
            probe = await _run(
                ["docker", "run", "--rm", "--user", "0", "--cap-add", "SYS_ADMIN", "--entrypoint", "python", IMAGE, "-c",
                 "import sys, json, urllib.request; sys.path.insert(0, '/app'); import fly_bootstrap as b\n"
                 f"outer = urllib.request.urlopen('{base}/healthz', timeout=5).status\n"
                 "inner = b.run_isolated([sys.executable, '-c', 'import os, socket\\n"
                 f"print(os.getuid())\\nsocket.create_connection((\"host.docker.internal\", {port}), timeout=3)'], 20)\n"
                 "print(json.dumps({'outer': outer, 'inner': inner, 'probe': b.network_is_dropped()}))"])
            res = json.loads(probe.strip().splitlines()[-1])
            assert res["outer"] == 200  # the bootstrap (trusted) has network
            assert res["inner"]["stdout"].strip() == "65534" and res["inner"]["returncode"] != 0
            assert "gaierror" in res["inner"]["stderr"] or "unreachable" in res["inner"]["stderr"].lower()
            assert res["probe"][0] is True and res["probe"][1]["routes"] == []

            # without CAP_SYS_ADMIN the namespace can't be made: fail closed, piece never runs
            closed = await _run(
                ["docker", "run", "--rm", "--user", "0", "--entrypoint", "python", IMAGE, "-c",
                 "import sys; sys.path.insert(0, '/app'); import fly_bootstrap as b; print(b.network_is_dropped()[0])"])
            assert closed.strip() == "False"
        finally:
            server.should_exit = True
            await serve
