"""Publishing: public media copies, community.json, and every state change after review.

Each transition is a compare-and-swap on the current state inside one transaction with its
audit row, so two admins (or an admin and the report threshold) can't double-apply.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from .db import Database, now_iso
from .storage import MediaStore

log = logging.getLogger("tac.publish")

PUBLIC_FILES = ("preview.webp", "og.jpg")
STATS_KEYS = ("motion_median", "seam", "void")


def model_label(model_id: str) -> str:
    """claude-opus-5-5 -> Claude Opus 5.5; claude-haiku-4-5 -> Claude Haiku 4.5."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", model_id)
    if not m:
        return model_id
    family, major, minor = m.groups()
    return f"Claude {family.capitalize()} {major}{'.' + minor if minor else ''}"


def iso_week(dt: datetime | None = None) -> str:
    year, week, _ = (dt or datetime.now(UTC)).isocalendar()
    return f"{year}-W{week:02d}"


class Publisher:
    def __init__(self, db: Database, store: MediaStore) -> None:
        self.db = db
        self.store = store
        self._regen_lock = asyncio.Lock()
        # Serialises every transition that touches public media (publish/hide/unhide/delete), so a
        # hide's delete_prefix can never wipe the files a concurrent unhide just copied back.
        # In-process only: one API Machine. Multi-instance needs a DB/advisory lock instead.
        self._media_lock = asyncio.Lock()

    async def publish(self, sub_id: str, actor: str, from_status: str = "in_review") -> bool:
        async with self._media_lock:
            return await self._publish(sub_id, actor, from_status)

    async def hide(self, handle: str, slug: str, actor: str, detail: str = "") -> bool:
        async with self._media_lock:
            return await self._hide(handle, slug, actor, detail)

    async def unhide(self, handle: str, slug: str, actor: str) -> bool:
        async with self._media_lock:
            return await self._unhide(handle, slug, actor)

    async def delete(self, handle: str, slug: str, actor: str, reason: str) -> bool:
        async with self._media_lock:
            return await self._delete(handle, slug, actor, reason)

    # ------------------------------------------------------------ media

    async def _copy_public(self, sub_id: str, handle: str, slug: str) -> None:
        src, dst = f"submissions/{sub_id}", f"public/{handle}/{slug}"
        for name in PUBLIC_FILES:
            data = await self.store.get(f"{src}/render/{name}")
            if data is None:
                raise RuntimeError(f"render output missing: {name}")
            await self.store.put(f"{dst}/{name}", data)
        piece = await self.store.get(f"{src}/piece.py")
        if piece is None:
            raise RuntimeError("piece.py missing")
        await self.store.put(f"{dst}/piece.py", piece)
        await self.store.copy_prefix(f"{src}/render/process", f"{dst}/process")

    async def _row(self, sub_id: str):
        return await self.db.fetchone(
            "SELECT s.*, u.handle FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sub_id,)
        )

    async def _piece_row(self, handle: str, slug: str):
        return await self.db.fetchone(
            "SELECT s.*, u.handle FROM submissions s JOIN users u ON u.id = s.user_id"
            " WHERE u.handle = ? AND s.slug = ?",
            (handle, slug),
        )

    # ------------------------------------------------------------ transitions

    async def _publish(self, sub_id: str, actor: str, from_status: str = "in_review") -> bool:
        row = await self._row(sub_id)
        if row is None or row["status"] != from_status:
            return False
        await self._copy_public(sub_id, row["handle"], row["slug"])
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET status = 'published', published_at = ?, updated_at = ?"
                " WHERE id = ? AND status = ?",
                (now_iso(), now_iso(), sub_id, from_status),
            )
            if ok:
                await tx.audit(actor, "publish", sub_id, from_status, "published")
        if not ok:
            current = await self._row(sub_id)
            if current and current["status"] != "published":
                await self.store.delete_prefix(f"public/{row['handle']}/{row['slug']}")
            return False
        await self.regenerate()
        return True

    async def reject(self, sub_id: str, actor: str, reasons: list[str], from_status: str = "in_review") -> bool:
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET status = 'rejected', reasons_json = ?, updated_at = ?"
                " WHERE id = ? AND status = ?",
                (json.dumps(reasons), now_iso(), sub_id, from_status),
            )
            if ok:
                await tx.audit(actor, "reject", sub_id, from_status, "rejected", "; ".join(reasons))
        return bool(ok)

    async def _hide(self, handle: str, slug: str, actor: str, detail: str = "") -> bool:
        row = await self._piece_row(handle, slug)
        if row is None:
            return False
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET hidden = 1, updated_at = ? WHERE id = ? AND status = 'published' AND hidden = 0",
                (now_iso(), row["id"]),
            )
            if ok:
                await tx.audit(actor, "hide", row["id"], "published", "published", detail or None)
        if ok:
            await self.store.delete_prefix(f"public/{handle}/{slug}")
            await self.regenerate()
        return bool(ok)

    async def _unhide(self, handle: str, slug: str, actor: str) -> bool:
        row = await self._piece_row(handle, slug)
        if row is None or row["status"] != "published" or not row["hidden"]:
            return False
        await self._copy_public(row["id"], handle, slug)
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET hidden = 0, updated_at = ? WHERE id = ? AND status = 'published' AND hidden = 1",
                (now_iso(), row["id"]),
            )
            if ok:
                await tx.execute("DELETE FROM reports WHERE submission_id = ?", (row["id"],))
                await tx.audit(actor, "unhide", row["id"], "published", "published")
        if not ok:  # lost a race (deleted meanwhile): don't leave media public
            await self.store.delete_prefix(f"public/{handle}/{slug}")
            return False
        await self.regenerate()
        return True

    async def _delete(self, handle: str, slug: str, actor: str, reason: str) -> bool:
        """Take a published piece down for good: status -> rejected, public media removed."""
        row = await self._piece_row(handle, slug)
        if row is None:
            return False
        reasons = [f"removed by moderator: {reason}"]
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET status = 'rejected', hidden = 0, reasons_json = ?, updated_at = ?"
                " WHERE id = ? AND status = 'published'",
                (json.dumps(reasons), now_iso(), row["id"]),
            )
            if ok:
                await tx.audit(actor, "delete", row["id"], "published", "rejected", reason)
        if ok:
            await self.store.delete_prefix(f"public/{handle}/{slug}")
            await self.regenerate()
        return bool(ok)

    # ------------------------------------------------------------ community.json

    async def theme(self, week: str) -> dict:
        row = await self.db.fetchone(
            "SELECT title, blurb FROM themes WHERE week <= ? ORDER BY week DESC LIMIT 1", (week,)
        )
        return {"title": row["title"], "blurb": row["blurb"]} if row else {"title": "", "blurb": ""}

    async def build(self) -> dict:
        rows = await self.db.fetchall(
            "SELECT s.*, u.handle FROM submissions s JOIN users u ON u.id = s.user_id"
            " WHERE s.status = 'published' AND s.hidden = 0 ORDER BY s.published_at DESC, s.id"
        )
        pieces = []
        for r in rows:
            meta = json.loads(r["meta_json"])
            stats = json.loads(r["stats_json"] or "{}")
            base = f"{r['handle']}/{r['slug']}"
            notes = meta.get("process_notes") or []
            pieces.append(
                {
                    "id": base,
                    "handle": r["handle"],
                    "slug": r["slug"],
                    "title": r["title"],
                    "description": meta.get("description", ""),
                    "model": meta["model"],
                    "model_label": model_label(meta["model"]),
                    "house_artist": False,
                    "human_role": meta.get("human_role", "none"),
                    "size": meta.get("size", "full"),
                    "tokens": meta.get("tokens"),
                    "iterations": meta.get("iterations"),
                    "loop_s": meta.get("loop_s") or stats.get("loop_s"),
                    "license": meta.get("license", ""),
                    "created": r["created_at"][:10],
                    "pick": bool(r["pick"]),
                    "preview": f"{base}/preview.webp",
                    "og": f"{base}/og.jpg",
                    "source": f"{base}/piece.py",
                    "process": [
                        {
                            "label": f"iteration {i}",
                            "image": f"{base}/process/{i:02d}.webp",
                            "note": notes[i - 1] if i - 1 < len(notes) else "",
                        }
                        for i in range(1, r["process_n"] + 1)
                    ],
                    "stats": {k: stats[k] for k in STATS_KEYS if k in stats},
                }
            )
        week = iso_week()
        return {
            "generated_at": now_iso(),
            "week": week,
            "theme": await self.theme(week),
            "totals": {
                "pieces": len(pieces),
                "artists": len({p["handle"] for p in pieces}),
                "tokens": sum(p["tokens"] or 0 for p in pieces),
            },
            "pieces": pieces,
        }

    async def regenerate(self) -> dict:
        # Serialised: each run reads the DB after the previous write, so the last file is the newest state.
        async with self._regen_lock:
            doc = await self.build()
            await self.store.put("public/community.json", json.dumps(doc, indent=1).encode())
            return doc
