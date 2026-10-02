"""Presigned GET/PUT for render job I/O against the LocalStore.

The isolated renderer (a Fly Machine) holds no credentials: the API hands it one GET URL for
its input bundle and one PUT URL for its output bundle, each HMAC-signed over (method, key, exp)
and valid for minutes. Keys are confined to `render-io/`. In prod the S3-compatible store
(Tigris) issues real S3 presigned URLs instead; same shape, so FlyMachineRenderer doesn't care.
"""

import hmac
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, Response

from .web import ApiError

router = APIRouter()

PREFIX = "render-io/"
MAX_PUT = 64 * 1024 * 1024


def _sig(secret: str, method: str, key: str, exp: int) -> str:
    return hmac.new(secret.encode(), f"render-io|{method}|{key}|{exp}".encode(), "sha256").hexdigest()


def presign(base_url: str, secret: str, key: str, method: str, ttl_s: int = 600) -> str:
    if not key.startswith(PREFIX) or method not in ("GET", "PUT"):
        raise ValueError("render-io keys and GET/PUT only")
    exp = int(time.time()) + ttl_s
    query = urlencode({"m": method, "exp": exp, "sig": _sig(secret, method, key, exp)})
    return f"{base_url}/v1/render-io/{quote(key[len(PREFIX):])}?{query}"


def _verify(request: Request, rest: str, method: str) -> str:
    key = PREFIX + rest
    q = request.query_params
    try:
        exp = int(q.get("exp", "0"))
    except ValueError:
        exp = 0
    good = _sig(request.app.state.secret, method, key, exp)
    if q.get("m") != method or exp < time.time() or not hmac.compare_digest(q.get("sig", "").encode(), good.encode()):
        raise ApiError(403, "invalid_signature")
    return key


@router.get("/v1/render-io/{rest:path}")
async def get_object(rest: str, request: Request) -> Response:
    key = _verify(request, rest, "GET")
    data = await request.app.state.store.get(key)
    if data is None:
        raise ApiError(404, "not_found")
    return Response(data, media_type="application/octet-stream")


@router.put("/v1/render-io/{rest:path}", status_code=200)
async def put_object(rest: str, request: Request) -> Response:
    key = _verify(request, rest, "PUT")
    body = await request.body()  # capped at MAX_PUT by LimitsMiddleware
    await request.app.state.store.put(key, body)
    return Response(status_code=200)
