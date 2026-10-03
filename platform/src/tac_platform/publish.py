"""Publishing: public media copies, community.json, and every state change after review.

Each transition is a compare-and-swap on the current state inside one transaction with its
audit row, so two admins (or an admin and the report threshold) can't double-apply.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from . import blocklist, cards
from .db import Database, now_iso
from .storage import MediaStore
from .views import utc_today, views_summary

log = logging.getLogger("tac.publish")

PUBLIC_FILES = ("preview.webp", "og.jpg")
STATS_KEYS = ("motion_median", "seam", "void")
REGEN_EVERY_S = 3600  # view counts in community.json are at most this stale
# CAS guard for every transition that puts a piece (back) on the wall: a suspended owner's pieces never go
# public, whether by admin approve, trusted auto-publish or unhide.
NOT_SUSPENDED = " AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = submissions.user_id AND users.suspended_at IS NOT NULL)"


def model_label(model_id: str) -> str:
    """claude-opus-5-5 -> Claude Opus 5.5; claude-haiku-4-5 -> Claude Haiku 4.5."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", model_id)
    if not m:
        return model_id
    family, major, minor = m.groups()
    return f"Claude {family.capitalize()} {major}{'.' + minor if minor else ''}"


def iso_z(stamp: str) -> str:
    """A stored ISO timestamp as UTC with a Z suffix and seconds: 2026-10-02T21:14:03Z."""
    d = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    d = d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_week(dt: datetime | None = None) -> str:
    year, week, _ = (dt or datetime.now(UTC)).isocalendar()
    return f"{year}-W{week:02d}"


class Publisher:
    def __init__(self, db: Database, store: MediaStore, secret: str = "") -> None:
        self.db = db
        self.store = store
        self.secret = secret  # install secret: salts the blocked-identity hashes
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

    async def unpublish(self, sub_id: str, actor: str) -> dict | None:
        """Artist takes their own piece down: published (incl. hidden) or in_review -> rejected.
        Public media and render outputs are deleted; the private upload stays for the audit trail."""
        async with self._media_lock:
            row = await self._row(sub_id)
            if row is None or row["status"] not in ("published", "in_review"):
                return None
            was = row["status"]
            reason = "unpublished by the artist" if was == "published" else "withdrawn by the artist"
            async with self.db.tx() as tx:
                ok = await tx.execute(
                    "UPDATE submissions SET status = 'rejected', hidden = 0, reasons_json = ?, updated_at = ?"
                    " WHERE id = ? AND status = ?",
                    (json.dumps([reason]), now_iso(), sub_id, was),
                )
                if ok:
                    await tx.audit(actor, "unpublish", sub_id, was, "rejected", reason)
            if not ok:
                return None
            await self.store.delete_prefix(f"public/{row['handle']}/{row['slug']}")
            await self.store.delete_prefix(f"submissions/{sub_id}/render")
            if was == "published":
                await self.regenerate()
            return {"id": f"{row['handle']}/{row['slug']}", "status": "rejected"}

    async def suspend(self, handle: str, reason: str, actor: str = "admin") -> dict | None:
        """Suspend `handle` (None if no such user). Idempotent: on an already-suspended user it only re-sweeps.

        One transaction under the DB write lock: suspended_at, every token/session/device code revoked, and
        hidden=1 with one audit row per published piece, so the DB never shows a half-suspended user. After
        the commit: every public/<handle>/<slug> prefix is deleted (each in its own try, failures reported)
        and community.json regenerated once. A retry (the already-suspended path) finishes whatever failed.
        _media_lock first, then the transaction: the same order as every other transition here."""
        async with self._media_lock:
            async with self.db.tx() as tx:
                user = await tx.fetchone("SELECT id, suspended_at FROM users WHERE handle = ?", (handle,))
                if user is None:
                    return None
                uid, already = user["id"], user["suspended_at"] is not None
                if not already:
                    await tx.execute("UPDATE users SET suspended_at = ?, suspended_reason = ? WHERE id = ?",
                                     (now_iso(), reason, uid))
                    await tx.audit(actor, "suspend", target=handle, detail=reason)
                tokens = await tx.execute("DELETE FROM access_tokens WHERE user_id = ?", (uid,))
                sessions = await tx.execute("DELETE FROM web_sessions WHERE user_id = ?", (uid,))
                await tx.execute("DELETE FROM device_codes WHERE user_id = ?", (uid,))  # approved, not yet polled
                queued = await tx.conn.execute_fetchall(
                    "SELECT id, slug FROM submissions WHERE user_id = ? AND status = 'queued'", (uid,))
                for q in queued:  # not rendered yet: stop it (a render already running ends in review, unpublishable)
                    await tx.execute(
                        "UPDATE submissions SET status = 'rejected', reasons_json = ?, updated_at = ?"
                        " WHERE id = ? AND status = 'queued'", (json.dumps(["account suspended"]), now_iso(), q[0]))
                    await tx.audit(actor, "reject", q[0], "queued", "rejected", "account suspended",
                                   target=f"{handle}/{q[1]}")
                visible = await tx.conn.execute_fetchall(
                    "SELECT id, slug, ig_posted_at FROM submissions WHERE user_id = ? AND status = 'published'"
                    " AND hidden = 0", (uid,))
                for p in visible:
                    await tx.execute("UPDATE submissions SET hidden = 1, updated_at = ? WHERE id = ?", (now_iso(), p[0]))
                    await tx.audit(actor, "hide", p[0], "published", "published", "account suspended",
                                   target=f"{handle}/{p[1]}")
                slugs = {r[0] for r in await tx.conn.execute_fetchall(
                    "SELECT slug FROM submissions WHERE user_id = ?", (uid,))}
            removed, failed = [], []
            try:  # also public dirs no row names any more
                slugs |= {k.split("/")[2] for k in await self.store.list(f"public/{handle}/") if k.count("/") >= 3}
            except Exception:  # noqa: BLE001
                log.exception("suspend %s: listing public media failed", handle)
                failed.append(f"{handle}/*")
            for slug in sorted(slugs):
                prefix = f"public/{handle}/{slug}"
                try:
                    if await self.store.list(f"{prefix}/"):
                        await self.store.delete_prefix(prefix)
                        removed.append(f"{handle}/{slug}")
                except Exception:  # noqa: BLE001
                    log.exception("suspend %s: deleting %s failed", handle, prefix)
                    failed.append(f"{handle}/{slug}")
            await self.regenerate()
            return {
                "already_suspended": already,
                "revoked_tokens": tokens,
                "revoked_sessions": sessions,
                "rejected_queued": [f"{handle}/{q[1]}" for q in queued],
                "hidden": [f"{handle}/{p[1]}" for p in visible],
                "ig_posted": [f"{handle}/{p[1]}" for p in visible if p[2]],
                "media_removed": removed,
                "media_failed": failed,
            }

    async def delete_account(self, user_id: int, handle: str, actor: str | None = None,
                             reason: str | None = None) -> list[str] | None:
        """Remove a user and everything they own. None if a render is in flight (worker holds a row), else the
        "handle/slug" ids that were marked posted to Instagram (those posts must be removed by hand).
        actor/reason: an admin deletion (audit action delete_account, in the takedown log); default = the user's own.
        An admin deletion of an account that is suspended at that moment also blocks the identity from signing up
        again (blocked_identities, same transaction); a self-deletion never does."""
        async with self._media_lock:
            async with self.db.tx() as tx:
                if await tx.fetchone(
                    "SELECT 1 FROM submissions WHERE user_id = ? AND status IN ('queued', 'rendering')", (user_id,)
                ):
                    # queued rows would be claimed mid-delete; fail them first, then let the caller retry
                    await tx.execute(
                        "UPDATE submissions SET status = 'rejected', reasons_json = '[\"account deleted\"]'"
                        " WHERE user_id = ? AND status = 'queued'",
                        (user_id,),
                    )
                    if await tx.fetchone(
                        "SELECT 1 FROM submissions WHERE user_id = ? AND status = 'rendering'", (user_id,)
                    ):
                        return None
                # suspended at this moment (same transaction as the delete) decides the block
                u = await tx.fetchone("SELECT github_id, suspended_at FROM users WHERE id = ?", (user_id,))
                ids = [r[0] for r in await tx.conn.execute_fetchall("SELECT id FROM submissions WHERE user_id = ?", (user_id,))]
                on_ig = [f"{handle}/{r[0]}" for r in await tx.conn.execute_fetchall(
                    "SELECT slug FROM submissions WHERE user_id = ? AND ig_posted_at IS NOT NULL ORDER BY slug", (user_id,))]
                marks = ",".join("?" * len(ids))
                if ids:
                    for table in ("reports", "views", "view_days"):
                        await tx.execute(f"DELETE FROM {table} WHERE submission_id IN ({marks})", tuple(ids))
                    await tx.execute(f"DELETE FROM submissions WHERE id IN ({marks})", tuple(ids))
                for table in ("access_tokens", "web_sessions", "device_codes"):
                    await tx.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                await tx.execute("DELETE FROM users WHERE id = ?", (user_id,))
                if actor is not None and u is not None and u["suspended_at"] is not None:
                    await tx.execute(
                        "INSERT OR IGNORE INTO blocked_identities (github_id_hash, created_at, ref) VALUES (?, ?, ?)",
                        (blocklist.identity_hash(self.secret, u["github_id"], handle), now_iso(), handle))
                    await tx.audit(actor, "block", target=handle, detail="suspended account deleted")
                if actor is None:
                    await tx.audit(f"user:{handle}", "account_deleted", detail=f"{len(ids)} submissions", target=handle)
                else:  # the IG list goes in the row: the submissions it names are gone after this commit
                    await tx.audit(actor, "delete_account", target=handle, detail=reason,
                                   data={"ig": on_ig} if on_ig else None)
            for sid in ids:
                await self.store.delete_prefix(f"submissions/{sid}")
            await self.store.delete_prefix(f"public/{handle}")
            await self.regenerate()
            return on_ig

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
        await self.write_cards(handle, slug)

    async def write_cards(self, handle: str, slug: str) -> bool:
        """share.jpg (og:image) + card.jpg (twitter:image) from the public og.jpg. Any failure (render,
        store read or write) is logged and returns False, never raises: the piece still publishes, and
        the og endpoint falls back to og.jpg. Caller holds _media_lock."""
        try:
            row = await self._piece_row(handle, slug)
            og = await self.store.get(f"public/{handle}/{slug}/og.jpg")
            if row is None or og is None:
                log.warning("no og.jpg for %s/%s: share cards skipped", handle, slug)
                return False
            model = model_label(json.loads(row["meta_json"]).get("model", ""))
            share, _ = await asyncio.to_thread(cards.share_jpg, og, handle, model)
            card = await asyncio.to_thread(cards.card_jpg, og, row["title"], handle, model)
            await self.store.put(f"public/{handle}/{slug}/share.jpg", share)
            await self.store.put(f"public/{handle}/{slug}/card.jpg", card)
            return True
        except Exception:  # noqa: BLE001
            log.exception("share cards failed for %s/%s", handle, slug)
            return False

    async def backfill_cards(self) -> int:
        """Render cards for published pieces that lack them, or all of them when the card layout
        (cards.VERSION) changed. Runs as a background task after startup. Idempotent; returns how many
        were written. Per piece: own try/except, _media_lock held for that piece only, and the piece is
        re-checked under the lock (a hide in between must not get its public files written back).
        cards_version is recorded only when every piece succeeded, so failures are retried next boot."""
        version = await self.db.fetchone("SELECT value FROM kv WHERE key = 'cards_version'")
        stale = version is None or version["value"] != cards.VERSION
        rows = await self.db.fetchall(
            "SELECT u.handle, s.slug FROM submissions s JOIN users u ON u.id = s.user_id"
            " WHERE s.status = 'published' AND s.hidden = 0"
        )
        n = failed = 0
        for r in rows:
            h, sl = r["handle"], r["slug"]
            try:
                async with self._media_lock:
                    cur = await self._piece_row(h, sl)
                    if cur is None or cur["status"] != "published" or cur["hidden"]:
                        continue
                    have = all([await self.store.get(f"public/{h}/{sl}/{f}") is not None
                                for f in ("share.jpg", "card.jpg")])
                    if not (stale or not have):
                        continue
                    if await self.write_cards(h, sl):
                        n += 1
                    else:
                        failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                log.exception("card backfill failed for %s/%s", h, sl)
        if not failed:
            await self.db.execute(
                "INSERT INTO kv (key, value) VALUES ('cards_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (cards.VERSION,),
            )
        if n or failed:
            log.info("share card backfill: %d rendered, %d failed", n, failed)
        return n

    async def _row(self, sub_id: str):
        return await self.db.fetchone(
            "SELECT s.*, u.handle, u.suspended_at FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sub_id,)
        )

    async def _piece_row(self, handle: str, slug: str):
        return await self.db.fetchone(
            "SELECT s.*, u.handle, u.suspended_at FROM submissions s JOIN users u ON u.id = s.user_id"
            " WHERE u.handle = ? AND s.slug = ?",
            (handle, slug),
        )

    # ------------------------------------------------------------ transitions

    async def _publish(self, sub_id: str, actor: str, from_status: str = "in_review") -> bool:
        row = await self._row(sub_id)
        if row is None or row["status"] != from_status or row["suspended_at"]:
            return False
        await self._copy_public(sub_id, row["handle"], row["slug"])
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET status = 'published', published_at = ?, updated_at = ?"
                " WHERE id = ? AND status = ?" + NOT_SUSPENDED,
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
                await tx.audit(actor, "hide", row["id"], "published", "published", detail or None,
                               target=f"{handle}/{slug}")
        if ok:
            await self.store.delete_prefix(f"public/{handle}/{slug}")
            await self.regenerate()
        return bool(ok)

    async def _unhide(self, handle: str, slug: str, actor: str) -> bool:
        row = await self._piece_row(handle, slug)
        if row is None or row["status"] != "published" or not row["hidden"] or row["suspended_at"]:
            return False
        await self._copy_public(row["id"], handle, slug)
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET hidden = 0, updated_at = ? WHERE id = ? AND status = 'published' AND hidden = 1"
                + NOT_SUSPENDED,
                (now_iso(), row["id"]),
            )
            if ok:
                await tx.execute("DELETE FROM reports WHERE submission_id = ?", (row["id"],))
                await tx.audit(actor, "unhide", row["id"], "published", "published", target=f"{handle}/{slug}")
        if not ok:  # lost a race (deleted or owner suspended meanwhile): don't leave media public
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
                await tx.audit(actor, "delete", row["id"], "published", "rejected", reason, target=f"{handle}/{slug}")
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
            "SELECT s.*, u.handle, u.house_artist, u.display_name, u.bio, u.link, u.instagram, u.instagram_confirmed"
            " FROM submissions s JOIN users u ON u.id = s.user_id"
            " WHERE s.status = 'published' AND s.hidden = 0 ORDER BY s.published_at DESC, s.id"
        )
        pieces = []
        counts = await views_summary(self.db, [r["id"] for r in rows])
        # "This week" = the ISO week the theme and the wall run on (Mon 00:00 UTC → now): the last
        # weekday()+1 entries of the 28-day series.
        week_days = utc_today().weekday() + 1
        week_views = 0
        artists: dict[str, dict] = {}
        for r in rows:
            meta = json.loads(r["meta_json"])
            c = counts[r["id"]]
            week_views += sum(c["views_28d"][-week_days:])
            artist = artists.setdefault(r["handle"], {"views": 0})
            artist["views"] += c["views_total"]
            if r["instagram"] and r["instagram_confirmed"]:  # only an admin-confirmed handle is public / tagged
                artist["instagram"] = r["instagram"]
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
                    "house_artist": bool(r["house_artist"]),  # owner's admin-set flag, never meta
                    "human_role": meta.get("human_role", "none"),
                    "size": meta.get("size", "full"),
                    "tokens": meta.get("tokens"),
                    "iterations": meta.get("iterations"),
                    "loop_s": meta.get("loop_s") or stats.get("loop_s"),
                    "license": meta.get("license", ""),
                    "created": iso_z(r["created_at"]),  # full UTC timestamp; the site formats dates
                    "pick": bool(r["pick"]),
                    "views": c["views_total"],  # public, anonymous: one per IP per piece per UTC day
                    "preview": f"{base}/preview.webp",
                    "og": f"{base}/og.jpg",
                    "share": f"{base}/share.jpg",  # og:image (portrait, byline strip)
                    "card": f"{base}/card.jpg",  # twitter:image (1200x630)
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
            if r["display_name"] or r["bio"] or r["link"]:  # optional; JSON-encoded, the site escapes on render
                pieces[-1]["artist"] = {"display_name": r["display_name"] or "", "bio": r["bio"] or "",
                                        "link": r["link"] or ""}
        week = iso_week()
        return {
            "generated_at": now_iso(),
            "week": {"label": week, "views": week_views},
            "theme": await self.theme(week),
            "totals": {
                "pieces": len(pieces),
                "artists": len({p["handle"] for p in pieces}),
                "tokens": sum(p["tokens"] or 0 for p in pieces),
            },
            "artists": artists,
            "pieces": pieces,
        }

    async def regenerate(self) -> dict:
        # Serialised: each run reads the DB after the previous write, so the last file is the newest state.
        async with self._regen_lock:
            doc = await self.build()
            await self.store.put("public/community.json", json.dumps(doc, indent=1).encode())
            return doc


async def regen_loop(publisher: Publisher, every_s: float = REGEN_EVERY_S) -> None:
    """Refresh community.json's view counts on a timer (never per view). Startup already built it."""
    while True:
        await asyncio.sleep(every_s)
        try:
            await publisher.regenerate()  # takes _regen_lock: serialised with publish/hide/delete regens
        except Exception:  # noqa: BLE001
            log.exception("community.json regen failed")
