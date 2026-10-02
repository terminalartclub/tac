"""FastAPI app factory: wiring, body-size limit, error shape, static media."""

import json
import logging
import secrets
from contextlib import asynccontextmanager

import aiofiles

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import admin, auth, moderation, submissions
from .automod import Automod
from .config import Settings
from .db import Database
from .pipeline import Pipeline
from .publish import Publisher
from .storage import LocalStore
from .web import ApiError

log = logging.getLogger("tac")

BODY_LIMITS = {"/v1/submissions": submissions.MAX_REQUEST}
DEFAULT_BODY_LIMIT = 64 * 1024
PUBLIC_PREFIXES = ("/media/", "/v1/community.json")


class BodyTooLarge(Exception):
    pass


class LimitsMiddleware:
    """Caps request bodies (Content-Length up front, streamed bytes as they arrive) and adds headers."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = BODY_LIMITS.get(path, DEFAULT_BODY_LIMIT)
        headers = dict(scope["headers"])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await _send_json(send, 413, {"error": "too_large", "detail": f"request over {limit // 1024} KB"})

        seen = 0
        started = False

        async def capped_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise BodyTooLarge
            return message

        async def send_with_headers(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                if path.startswith(PUBLIC_PREFIXES):
                    extra.append((b"access-control-allow-origin", b"*"))
                else:
                    extra.append((b"x-frame-options", b"DENY"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        try:
            await self.app(scope, capped_receive, send_with_headers)
        except BodyTooLarge:
            if not started:
                await _send_json(send, 413, {"error": "too_large", "detail": f"request over {limit // 1024} KB"})


async def _send_json(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


async def _load_themes(db: Database, settings: Settings) -> None:
    try:
        async with aiofiles.open(settings.themes_file) as f:
            themes = json.loads(await f.read())
    except FileNotFoundError:
        return
    async with db.tx() as tx:
        for week, t in themes.items():
            await tx.execute(
                "INSERT INTO themes (week, title, blurb) VALUES (?, ?, ?)"
                " ON CONFLICT(week) DO UPDATE SET title = excluded.title, blurb = excluded.blurb",
                (week, t["title"], t.get("blurb", "")),
            )


def create_app(settings: Settings | None = None, automod: Automod | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check_prod_safety()
    public_dir = settings.data_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.sqlite_path)
        await db.open()
        await db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('secret', ?)", (secrets.token_hex(32),))
        secret = (await db.fetchone("SELECT value FROM kv WHERE key = 'secret'"))["value"]
        await _load_themes(db, settings)
        store = LocalStore(settings.data_dir)
        publisher = Publisher(db, store)
        pipeline = Pipeline(settings, db, store, publisher, automod or Automod(settings))
        app.state.settings, app.state.db, app.state.store = settings, db, store
        app.state.secret, app.state.publisher, app.state.pipeline = secret, publisher, pipeline
        await publisher.regenerate()
        if settings.worker_enabled:
            await pipeline.start()
        log.info("tac-platform up: auth=%s automod=%s tools=%s", settings.auth_mode,
                 "on" if pipeline.automod.enabled else "off", settings.tools_dir)
        try:
            yield
        finally:
            await pipeline.stop()
            await db.close()

    app = FastAPI(title="spare cycles", version="0.1.0", lifespan=lifespan)
    app.add_middleware(LimitsMiddleware)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.error, **exc.extra}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        detail = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return JSONResponse({"error": "invalid_request", "detail": detail}, status_code=400)

    @app.get("/v1/community.json")
    async def community(request: Request):
        data = await request.app.state.store.get("public/community.json")
        if data is None:
            data = json.dumps(await request.app.state.publisher.regenerate()).encode()
        return Response(data, media_type="application/json", headers={"Cache-Control": "public, max-age=60"})

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    for r in (auth.router, submissions.router, moderation.router, admin.router):
        app.include_router(r)
    app.mount("/media", StaticFiles(directory=public_dir), name="media")
    return app
