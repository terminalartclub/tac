"""In-process background worker: queued -> rendering -> rejected | in_review | published.

The queue is the submissions table itself. A worker claims the oldest queued row with one
UPDATE ... WHERE id = (SELECT ...) RETURNING id, so concurrent workers never share a job.
On startup, rows left in 'rendering' by a crash go back to 'queued'.

  piece_dir (temp) -> check_piece.py -> render_piece.py -> automod -> decision
"""

import asyncio
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from .automod import Automod
from .config import Settings
from .db import Database, now_iso
from .models import HIGH_TOKENS
from .publish import Publisher
from .renderer import Renderer, make_renderer
from . import wallframes
from .storage import MediaStore

log = logging.getLogger("tac.pipeline")


def _log_json(items: list[str]) -> str:
    """A list for a log line: JSON with ASCII escapes, so control, bidi and newline characters stay inert."""
    return json.dumps([str(x) for x in items], ensure_ascii=True)


def _rc(res) -> str:
    return "-" if res is None else str(res.returncode)


def _automod_summary(am, reasons: list[str]) -> str:
    """One line for the log: what automod decided, and what it cost."""
    if am.verdict is not None:
        v = am.verdict
        head = f"verdict safe={v.safe} on_brief={v.on_brief} flags={_log_json(v.flags)}"  # model output
    else:
        head = "budget reached" if am.budget else "skipped" if am.skipped else f"no verdict {_log_json(reasons)}"
    return f"{head} cost=${am.cost_usd:.5f}"

RENDER_FILES = {"preview.webp", "og.jpg", "stats.json"}
FRAMES_FILE = "frames.cells.gz"  # optional: the wall's frames (wallframes.py); a piece renders without it
PROCESS_RE = re.compile(r"^process/[A-Za-z0-9_-]{1,40}\.webp$")
MAX_RENDER_FILE = 25 * 1024**2


class Rejected(Exception):
    """platform=True: our side failed (render budget, backend, tooling), not the piece. Such a rejection is
    flagged platform_fault and doesn't count toward the submitter's daily limit."""

    def __init__(self, reasons: list[str], platform: bool = False) -> None:
        self.reasons = reasons
        self.platform = platform


def check_frames(data: bytes) -> dict | None:
    """frames.cells.gz as the render VM wrote it, checked with the same code the plugin plays it with: its
    header + sha256 for the DB, or None (logged) when it isn't a well-formed file within the limits."""
    import hashlib

    try:
        h, _ = wallframes.load(data)
    except wallframes.BadFrames as e:
        log.warning("render frames refused: %s", e)
        return None
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "cols": h.cols, "rows": h.rows,
            "fps": h.fps, "frames": h.frames, "loop_ms": h.loop_ms}


def _collect_frames(out_dir: Path) -> tuple[bytes, dict] | None:
    p = out_dir / FRAMES_FILE
    if not p.is_file() or p.is_symlink() or p.stat().st_size > wallframes.MAX_GZ:
        return None
    data = p.read_bytes()
    info = check_frames(data)
    return (data, info) if info else None


def _collect_render(out_dir: Path) -> tuple[dict, dict[str, bytes], list[bytes]]:
    """Validate and read render outputs (sync; called via to_thread)."""
    files = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file() and not p.is_symlink())
    missing = RENDER_FILES - set(files)
    if missing:
        raise Rejected([f"render produced no {', '.join(sorted(missing))}"], platform=True)
    try:
        stats = json.loads((out_dir / "stats.json").read_text())
    except ValueError:
        stats = None
    if not isinstance(stats, dict):
        raise Rejected(["render produced invalid stats.json"], platform=True)
    process = [f for f in files if PROCESS_RE.match(f)][:4]
    for name in [*RENDER_FILES, *process]:
        if (out_dir / name).stat().st_size > MAX_RENDER_FILE:
            raise Rejected([f"render output {name} is over {MAX_RENDER_FILE // 1024**2} MB"])
    main = {name: (out_dir / name).read_bytes() for name in sorted(RENDER_FILES)}
    return stats, main, [(out_dir / f).read_bytes() for f in process]


def _tail(text: str, n: int = 300) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[-n:]


class Pipeline:
    def __init__(
        self, settings: Settings, db: Database, store: MediaStore, publisher: Publisher, automod: Automod
    ) -> None:
        self.settings = settings
        self.db = db
        self.store = store
        self.publisher = publisher
        self.automod = automod
        self.renderer: Renderer = make_renderer(settings, store)
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        check = getattr(self.renderer, "available", None)
        problem = await check() if check else None
        if problem:
            if self.settings.env == "prod":
                raise RuntimeError(problem)  # fail fast: prod must not accept uploads it can't render
            log.warning("%s (renders will fail until it exists; build with render-image/build.sh)", problem)
        async with self.db.tx() as tx:
            n = await tx.execute("UPDATE submissions SET status = 'queued' WHERE status = 'rendering'")
            if n:
                await tx.audit("system", "requeue_after_restart", detail=f"{n} rows")
        self._tasks = [
            asyncio.create_task(self._loop(i), name=f"tac-worker-{i}") for i in range(self.settings.render_concurrency)
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self, n: int) -> None:
        while True:
            try:
                sub_id = await self.claim()
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                sub_id = None
            if sub_id is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                except TimeoutError:
                    pass
                continue
            await self.process(sub_id)

    async def claim(self) -> str | None:
        async with self.db.tx() as tx:
            row = await tx.fetchone(
                "UPDATE submissions SET status = 'rendering', updated_at = ?"
                " WHERE id = (SELECT id FROM submissions WHERE status = 'queued'"
                " AND user_id NOT IN (SELECT id FROM users WHERE suspended_at IS NOT NULL)"  # suspended: never rendered
                " ORDER BY created_at, id LIMIT 1)"
                " AND status = 'queued' RETURNING id",
                (now_iso(),),
            )
            if row is None:
                return None
            await tx.audit("system", "claim", row["id"], "queued", "rendering")
        log.info("pipeline %s: claimed (queued -> rendering)", row["id"])
        return row["id"]

    # ------------------------------------------------------------ one job

    async def process(self, sub_id: str) -> None:
        tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="tac-job-"))
        try:
            await self._process(sub_id, tmp)
        except Rejected as r:
            await self._finish(sub_id, "rejected", r.reasons, platform=r.platform)
        except Exception as exc:  # noqa: BLE001 - never leave a row stuck in 'rendering'
            log.exception("pipeline crashed on %s", sub_id)
            await self._finish(sub_id, "rejected", [f"pipeline error ({exc.__class__.__name__}); please resubmit"],
                               platform=True)
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def _materialize(self, sub_id: str, piece_dir: Path) -> None:
        prefix = f"submissions/{sub_id}/"
        for key in await self.store.list(prefix):
            rel = key[len(prefix) :]
            if rel.startswith("render/"):
                continue
            data = await self.store.get(key)
            dest = piece_dir / rel
            await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(dest.write_bytes, data or b"")

    async def _process(self, sub_id: str, tmp: Path) -> None:
        piece_dir, out_dir, work = tmp / "piece", tmp / "out", tmp / "work"
        for d in (piece_dir, out_dir, work):
            await asyncio.to_thread(d.mkdir)
        await self._materialize(sub_id, piece_dir)
        # 1+2. static check then render, both inside the render backend (they touch untrusted source)
        log.info("pipeline %s: render start (backend=%s)", sub_id, self.settings.renderer)
        started = time.monotonic()
        job = await self.renderer.run_job(piece_dir, out_dir, work)
        log.info("pipeline %s: render finish in %.1fs (check_exit=%s render_exit=%s timed_out=%s backend_error=%s)",
                 sub_id, time.monotonic() - started, _rc(job.check), _rc(job.render), job.timed_out,
                 job.backend_error is not None)
        if job.isolation is not None:  # Fly: the in-VM probe, run before any untrusted code (DEPLOY.md smoke test)
            log.info("pipeline %s: isolation %s", sub_id, json.dumps(job.isolation, ensure_ascii=True, sort_keys=True))
        if job.timed_out:
            raise Rejected([f"render timed out after {self.settings.render_timeout_s:.0f} s"], platform=True)
        if job.backend_error:
            log.error("render backend failed for %s: %s", sub_id, job.backend_error)
            raise Rejected(["render backend unavailable; please resubmit later"], platform=True)
        res = job.check
        try:
            verdict = json.loads(res.stdout)
        except ValueError:
            verdict = None
        if res.timed_out or not isinstance(verdict, dict) or res.returncode not in (0, 1):
            log.error("check_piece failed rc=%s stderr=%s", res.returncode, _tail(res.stderr))
            raise Rejected([f"static check could not run (exit {res.returncode}); please resubmit"], platform=True)
        if res.returncode != 0 or not verdict.get("ok"):
            raise Rejected([str(r)[:300] for r in verdict.get("reasons") or ["static check failed"]])

        res = job.render
        if res is None:
            raise Rejected(["render did not run"], platform=True)
        if res.timed_out:
            raise Rejected([f"render timed out after {self.settings.render_timeout_s:.0f} s"], platform=True)
        if res.returncode == 125 and self.settings.renderer == "docker":
            raise Rejected(["render backend unavailable; please resubmit later"], platform=True)
        if res.returncode == 124:  # render_piece.py's own --timeout (render_piece_timeout_s) fired
            raise Rejected([f"render did not finish within {self.settings.render_piece_timeout_s:.0f} s on the render "
                            "machine; please resubmit"], platform=True)
        if res.returncode != 0:
            raise Rejected([f"render failed (exit {res.returncode}): {_tail(res.stderr) or 'no output'}"])
        stats, process_n = await self._store_render(sub_id, out_dir)

        # 3. automod
        row = await self.db.fetchone(
            "SELECT s.title, s.meta_json, u.trusted FROM submissions s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
            (sub_id,),
        )
        meta = json.loads(row["meta_json"])
        code = await self.store.get(f"submissions/{sub_id}/piece.py") or b""
        preview = await self.store.get(f"submissions/{sub_id}/render/preview.webp") or b""
        am = await self.automod.review(code.decode("utf-8", "replace"), preview, row["title"], meta.get("description", ""),
                                       db=self.db)
        verdict_json = None
        reasons: list[str] = []
        flags: list[str] = []
        critique = None
        if am.budget:
            reasons = ["automod budget reached"]  # never blocks: a human reviews it
        elif am.skipped:
            reasons = ["automod skipped"]
        elif am.verdict is None:
            reasons = [am.refusal or am.error or "automod failed"]
        else:
            v = am.verdict
            verdict_json = v.model_dump_json()
            flags = v.flags
            if not v.safe:
                reasons = [f"automod: {f}" for f in v.flags] or ["automod: unsafe"]
            elif not v.on_brief:
                reasons = ["automod: off brief"]

        log.info("pipeline %s: automod %s", sub_id, _automod_summary(am, reasons))
        async with self.db.tx() as tx:
            await tx.execute(
                "UPDATE submissions SET stats_json = ?, process_n = ?, critique = ?, flags_json = ?, automod_json = ?,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(stats), process_n, critique, json.dumps(flags), verdict_json, now_iso(), sub_id),
            )
            await tx.audit("system", "automod", sub_id, detail=verdict_json or "; ".join(reasons))

        # 4. decision
        if am.verdict is not None and not am.verdict.safe:
            await self._finish(sub_id, "rejected", reasons)
            return
        await self._finish(sub_id, "in_review", reasons)
        clean = am.verdict is not None and am.verdict.safe and am.verdict.on_brief
        tokens = json.loads(row["meta_json"]).get("tokens")
        high_tokens = isinstance(tokens, int) and tokens > HIGH_TOKENS  # feeds the public counter: a human checks it
        if clean and row["trusted"] and not high_tokens:
            if await self.publisher.publish(sub_id, "system:trusted", from_status="in_review"):
                log.info("pipeline %s: auto-published (trusted, clean automod)", sub_id)

    async def _store_render(self, sub_id: str, out_dir: Path) -> tuple[dict, int]:
        stats, main, process = await asyncio.to_thread(_collect_render, out_dir)
        prefix = f"submissions/{sub_id}/render"
        for name, data in main.items():
            await self.store.put(f"{prefix}/{name}", data)
        for i, data in enumerate(process, 1):  # renumbered 01..04 so community.json can name them
            await self.store.put(f"{prefix}/process/{i:02d}.webp", data)
        await self._store_frames(sub_id, await asyncio.to_thread(_collect_frames, out_dir))
        return stats, len(process)

    async def _store_frames(self, sub_id: str, frames: tuple[bytes, dict] | None) -> None:
        """Always sets frames_json: a render without frames clears what an earlier render of the row left."""
        if frames is not None:
            await self.store.put(f"submissions/{sub_id}/render/{FRAMES_FILE}", frames[0])
        await self.db.execute("UPDATE submissions SET frames_json = ? WHERE id = ?",
                              (json.dumps(frames[1]) if frames else None, sub_id))

    # ------------------------------------------------------------ wall backfill

    async def backfill_frames(self, force: bool = False) -> dict:
        """Frames for published pieces that have none (all of them with force): each piece re-rendered in the
        render backend (the same isolated job as a submission; the piece's code never runs here), only
        frames.cells.gz kept, stored with the submission (never public/: the frames endpoint serves it only while
        the piece is published and not hidden). One piece at a time. Returns counts; logs and audits them."""
        rows = await self.db.fetchall(
            "SELECT s.id FROM submissions s WHERE s.status = 'published' AND s.hidden = 0"
            + ("" if force else " AND s.frames_json IS NULL") + " ORDER BY s.published_at DESC"
        )
        done = failed = 0
        for r in rows:
            sub_id = r["id"]
            tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="tac-frames-"))
            try:
                piece_dir, out_dir, work = tmp / "piece", tmp / "out", tmp / "work"
                for d in (piece_dir, out_dir, work):
                    await asyncio.to_thread(d.mkdir)
                await self._materialize(sub_id, piece_dir)
                job = await self.renderer.run_job(piece_dir, out_dir, work)
                frames = None
                if job.render is not None and not job.timed_out and not job.backend_error and job.render.returncode == 0:
                    frames = await asyncio.to_thread(_collect_frames, out_dir)
                if frames is None:
                    failed += 1
                    log.warning("frames backfill: no frames for %s", sub_id)
                    continue
                await self._store_frames(sub_id, frames)  # the old render's preview etc. are left as they are
                done += 1  # served from the submission, gated on the DB: nothing to copy (wall.py)
            except Exception:  # noqa: BLE001 - one piece never stops the rest
                failed += 1
                log.exception("frames backfill failed for %s", sub_id)
            finally:
                await asyncio.to_thread(shutil.rmtree, tmp, True)
        async with self.db.tx() as tx:
            await tx.audit("system", "wall_backfill_done", detail=f"{done} rendered, {failed} failed, of {len(rows)}")
        log.info("frames backfill: %d rendered, %d failed, of %d", done, failed, len(rows))
        return {"pieces": len(rows), "rendered": done, "failed": failed}

    async def _finish(self, sub_id: str, status: str, reasons: list[str], platform: bool = False) -> None:
        async with self.db.tx() as tx:
            ok = await tx.execute(
                "UPDATE submissions SET status = ?, reasons_json = ?, platform_fault = ?, updated_at = ?"
                " WHERE id = ? AND status = 'rendering'",
                (status, json.dumps(reasons), int(platform), now_iso(), sub_id),
            )
            if ok:
                await tx.audit("system", "pipeline_result", sub_id, "rendering", status,
                               ("; ".join(reasons) + (" [platform fault]" if platform else "")) or None)
        if ok:
            # reasons can carry piece-derived text (check output, render stderr): JSON-escaped, so a newline
            # or ESC in a reason can't forge or break log lines
            log.info("pipeline %s: final status %s%s", sub_id, status, f" {_log_json(reasons)}" if reasons else "")
