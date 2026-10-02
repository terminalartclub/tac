import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tac_platform.app import create_app
from tac_platform.automod import Automod
from tac_platform.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
ADMIN = "test-admin-token"
PIECE = b'from rich.text import Text\n\nwhile True:\n    canvas.write(Text("~"))\n    await sleep(0.1)\n'
META = {"title": "First Light", "description": "a window going grey", "model": "claude-opus-5-5",
        "tokens": 1234, "iterations": 3, "loop_s": 30.0, "license": "CC-BY-4.0 art / MIT code"}


def png_bytes(color=(200, 100, 30)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


class FakeMessages:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_claude(verdict: dict | None = None, stop_reason: str = "end_turn", category: str | None = None):
    content = [SimpleNamespace(type="text", text=json.dumps(verdict))] if verdict is not None else []
    resp = SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(type="refusal", category=category, explanation="") if category else None,
        content=content,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    return SimpleNamespace(messages=FakeMessages(resp))


class Ctx:
    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    def client(self, ip: str = "10.0.0.1", **headers) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(ip, 5000))
        return httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)

    def admin(self) -> httpx.AsyncClient:
        return self.client(ip="10.9.9.9", **{"x-admin-token": ADMIN})

    async def login(self, handle: str) -> str:
        async with self.client() as c:
            d = (await c.post("/v1/auth/device", json={})).json()
            r = await c.post("/device", data={"user_code": d["user_code"], "handle": handle})
            assert r.status_code == 200, r.text
            t = await c.post("/v1/auth/token", json={"device_code": d["device_code"]})
            assert t.status_code == 200, t.text
            return t.json()["access_token"]

    async def submit(self, token: str, piece: bytes = PIECE, meta: dict | None = None, process=(), notes=None):
        files = [("piece", ("piece.py", piece, "text/x-python"))]
        files += [("process", (f"{i}.png", p, "image/png")) for i, p in enumerate(process)]
        data = {"meta": json.dumps(meta or META)}
        if notes:
            data["notes"] = notes
        async with self.client(authorization=f"Bearer {token}") as c:
            return await c.post("/v1/submissions", data=data, files=files)

    async def wait(self, token: str, sub_id: str, until=("rejected", "in_review", "published"), timeout=30.0) -> dict:
        async with self.client(authorization=f"Bearer {token}") as c:
            for _ in range(int(timeout / 0.1)):
                body = (await c.get(f"/v1/submissions/{sub_id}")).json()
                if body["status"] in until:
                    return body
                await asyncio.sleep(0.1)
        raise AssertionError(f"timed out waiting, last: {body}")


@asynccontextmanager
async def make_ctx(tmp_path: Path, automod_client=None, **overrides) -> AsyncIterator[Ctx]:
    settings = Settings.from_env(
        data_dir=tmp_path / "data",
        db_path=None,
        tools_dir=FIXTURES / "tools",
        admin_token=ADMIN,
        anthropic_api_key_present=False,
        auth_mode="dev",
        **overrides,
    )
    automod = Automod(settings, client=automod_client)
    app = create_app(settings, automod=automod)
    async with app.router.lifespan_context(app):
        yield Ctx(app, settings)


@pytest.fixture
async def ctx(tmp_path) -> AsyncIterator[Ctx]:
    async with make_ctx(tmp_path) as c:
        yield c


def handle_slug(url: str) -> tuple[str, str]:
    m = re.search(r"/media/([^/]+)/([^/]+)/", url)
    assert m, url
    return m.group(1), m.group(2)
