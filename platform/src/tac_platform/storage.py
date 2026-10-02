"""Media storage behind a five-method interface.

Keys are slash-separated paths:
  submissions/<id>/piece.py, meta.json, notes.md, process/01.png   (private uploads)
  submissions/<id>/render/preview.webp, og.jpg, stats.json, process/*.webp
  public/<handle>/<slug>/...                                        (served under /media)
  public/community.json

LocalStore writes under data/. Prod swaps in an S3-compatible store (Tigris on Fly):
put -> PutObject, get -> GetObject, list -> ListObjectsV2(prefix), delete_prefix ->
list + DeleteObjects, and /media is served by the bucket's public URL instead of the API.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Protocol

import aiofiles
import aiofiles.os


class MediaStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def delete_prefix(self, prefix: str) -> None: ...
    async def copy_prefix(self, src: str, dst: str) -> None: ...


def _safe_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"bad storage key: {key!r}")
    return key


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, key: str) -> Path:
        return self.root / _safe_key(key)

    async def put(self, key: str, data: bytes) -> None:
        dest = self.path(key)
        await aiofiles.os.makedirs(dest.parent, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{id(data)}.tmp")
        async with aiofiles.open(tmp, "wb") as f:
            await f.write(data)
        await aiofiles.os.replace(tmp, dest)  # atomic: readers never see a half-written file

    async def get(self, key: str) -> bytes | None:
        try:
            async with aiofiles.open(self.path(key), "rb") as f:
                return await f.read()
        except FileNotFoundError:
            return None

    async def list(self, prefix: str) -> list[str]:
        base = self.path(prefix.rstrip("/"))

        def walk() -> list[str]:
            if not base.is_dir():
                return []
            return sorted(
                str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")
            )

        return await asyncio.to_thread(walk)

    async def delete_prefix(self, prefix: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self.path(prefix.rstrip("/")), True)

    async def copy_prefix(self, src: str, dst: str) -> None:
        src = src.rstrip("/")
        dst = dst.rstrip("/")
        for key in await self.list(src):
            data = await self.get(key)
            if data is not None:
                await self.put(dst + key[len(src) :], data)
