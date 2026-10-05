"""SQLite via aiosqlite: one shared connection, writes serialised by an asyncio.Lock.

The SQL is plain enough to port to Postgres (RETURNING, ON CONFLICT); the swap means
replacing this module with an asyncpg-backed one exposing the same five methods.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("tac.db")
BUSY_TIMEOUT_MS = 5000
RETRY_LOG_EVERY = 10  # a blocked-truncate retry logs only every Nth attempt
TRUNCATE_RETRY_S = 30.0  # a WAL truncate blocked by a reader in another connection is retried this often

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    handle      TEXT NOT NULL UNIQUE,
    github_id   INTEGER UNIQUE,
    trusted     INTEGER NOT NULL DEFAULT 0,
    house_artist INTEGER NOT NULL DEFAULT 0,  -- set by admin only; never from submitted meta
    display_name TEXT,
    bio          TEXT,
    link         TEXT,
    instagram    TEXT,                       -- bare IG handle, claimed by the user (unverified)
    instagram_confirmed INTEGER NOT NULL DEFAULT 0,  -- admin-confirmed; only then public / tagged
    terms_version     INTEGER,               -- the TERMS_VERSION last accepted at sign-in; NULL = never
    terms_accepted_at TEXT,
    suspended_at     TEXT,                   -- admin suspension; NULL = active. No sign-in, no submissions
    suspended_reason TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token_sha256 TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sessions (
    session_sha256 TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    created_at     TEXT NOT NULL,
    expires_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS web_sessions_user ON web_sessions(user_id);
CREATE TABLE IF NOT EXISTS device_codes (
    device_code_sha256 TEXT PRIMARY KEY,
    user_code          TEXT NOT NULL UNIQUE,
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|consumed
    user_id            INTEGER REFERENCES users(id),
    expires_at         REAL NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    slug         TEXT NOT NULL,
    title        TEXT NOT NULL,
    meta_json    TEXT NOT NULL,
    status       TEXT NOT NULL,  -- queued|rendering|rejected|in_review|published
    reasons_json TEXT NOT NULL DEFAULT '[]',
    critique     TEXT,
    flags_json   TEXT NOT NULL DEFAULT '[]',
    automod_json TEXT,
    stats_json   TEXT,
    process_n    INTEGER NOT NULL DEFAULT 0,
    hidden       INTEGER NOT NULL DEFAULT 0,
    pick         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    published_at TEXT,
    platform_fault INTEGER NOT NULL DEFAULT 0,  -- rejected because OUR side failed: not counted in the daily limit
    ig_posted_at TEXT,  -- admin-set: we posted it on Instagram (a takedown must also remove it there)
    frames_json  TEXT,  -- the wall's frames.cells.gz when the render made one: sha256, bytes, fps, frames, loop_ms
    UNIQUE (user_id, slug)
);
CREATE INDEX IF NOT EXISTS submissions_status ON submissions(status, created_at);
CREATE INDEX IF NOT EXISTS submissions_user_created ON submissions(user_id, created_at);
CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(id),
    reporter      TEXT NOT NULL,  -- sha256(salt + ip)
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (submission_id, reporter)
);
CREATE TABLE IF NOT EXISTS rate_events (
    key TEXT NOT NULL,
    at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_events_key_at ON rate_events(key, at);
CREATE TABLE IF NOT EXISTS themes (
    week  TEXT PRIMARY KEY,  -- ISO week, e.g. 2026-W40
    title TEXT NOT NULL,
    blurb TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    actor         TEXT NOT NULL,  -- system|admin|user:<handle>|reports
    action        TEXT NOT NULL,
    submission_id TEXT,
    from_status   TEXT,
    to_status     TEXT,
    detail        TEXT,
    target        TEXT,  -- what a moderation action hit: "handle/slug" or "handle" (survives row deletes)
    data_json     TEXT   -- structured extras, e.g. delete_account {"ig": ["handle/slug", ...]}
);
CREATE TABLE IF NOT EXISTS views (          -- per-viewer dedupe; purged after 30 days
    submission_id TEXT NOT NULL,
    day           TEXT NOT NULL,                -- UTC YYYY-MM-DD
    ip_day_hash   TEXT NOT NULL,                -- sha256(day_salt | ip), salt deleted after the day
    UNIQUE (submission_id, day, ip_day_hash)
);
CREATE TABLE IF NOT EXISTS view_days (      -- rollup, kept
    submission_id TEXT NOT NULL,
    day           TEXT NOT NULL,
    views         INTEGER NOT NULL,
    PRIMARY KEY (submission_id, day)
);
CREATE TABLE IF NOT EXISTS event_days (     -- site events, counted per UTC day; no viewer data
    name TEXT NOT NULL,                         -- piece_share | install_copy | install_send
    day  TEXT NOT NULL,
    n    INTEGER NOT NULL,
    PRIMARY KEY (name, day)
);
CREATE TABLE IF NOT EXISTS automod_spend (  -- automod cost ledger, one row per UTC month
    month TEXT PRIMARY KEY,                     -- YYYY-MM
    usd   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS blocked_identities (  -- see blocklist.py; written when an admin deletes a suspended account
    id             INTEGER PRIMARY KEY,            -- what unblock targets: ref is NOT unique (handles get reused)
    github_id_hash TEXT NOT NULL UNIQUE,           -- sha256(salt | identity), never the raw id
    created_at     TEXT NOT NULL,
    ref            TEXT NOT NULL                   -- the deleted handle, for the admin to recognise the row
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self.truncate_retry_s = TRUNCATE_RETRY_S
        self.truncate_retries = 0
        self._truncate_task: asyncio.Task | None = None
        self._after_truncate: list[Callable[[], Awaitable[None]]] = []

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        # Deleted rows are overwritten with zeros, not left in free pages: an erased account must not be
        # recoverable from the file (see delete_account, which also truncates the WAL).
        await self._conn.execute("PRAGMA secure_delete=ON")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        if await self.fetchone("SELECT 1 FROM kv WHERE key = 'secure_delete_vacuumed'") is None:
            # once: free pages written before secure_delete still hold deleted rows; VACUUM rebuilds the file.
            # A cleanup, never a reason not to start: any failure (disk full, I/O, a lock) is logged and the
            # flag stays unset, so the next boot tries again. The flag is set only once the WAL is truncated
            # too (the old pages live on in WAL frames until then), possibly later by the background retry.
            try:
                await self.conn.execute("VACUUM")
            except Exception:  # noqa: BLE001
                log.exception("one-time VACUUM failed; serving anyway, the next boot retries")
            else:
                await self.truncate_or_retry(then=self._mark_vacuumed)

    async def _migrate(self) -> None:
        """Additive column migrations for DBs created by earlier versions."""
        cols = {r["name"] for r in await self.fetchall("PRAGMA table_info(users)")}
        if "house_artist" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN house_artist INTEGER NOT NULL DEFAULT 0")
        for col in ("display_name", "bio", "link", "instagram"):
            if col not in cols:
                await self.conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        if "terms_version" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN terms_version INTEGER")
        if "terms_accepted_at" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")
        if "instagram_confirmed" not in cols:
            await self.conn.execute("ALTER TABLE users ADD COLUMN instagram_confirmed INTEGER NOT NULL DEFAULT 0")
        for col in ("suspended_at", "suspended_reason"):
            if col not in cols:
                await self.conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        sub_cols = {r["name"] for r in await self.fetchall("PRAGMA table_info(submissions)")}
        if "ig_posted_at" not in sub_cols:
            await self.conn.execute("ALTER TABLE submissions ADD COLUMN ig_posted_at TEXT")
        if "platform_fault" not in sub_cols:
            await self.conn.execute("ALTER TABLE submissions ADD COLUMN platform_fault INTEGER NOT NULL DEFAULT 0")
        if "frames_json" not in sub_cols:  # the wall's frames.cells.gz: {sha256, bytes, fps, frames, loop_ms}
            await self.conn.execute("ALTER TABLE submissions ADD COLUMN frames_json TEXT")
        if "id" not in {r["name"] for r in await self.fetchall("PRAGMA table_info(blocked_identities)")}:
            # first shape keyed by the hash: rebuild with a surrogate id (can't ALTER in a PRIMARY KEY).
            # tx(): a failure rolls the whole rebuild back (executescript would leave the transaction open).
            async with self.tx() as tx:
                await tx.execute("ALTER TABLE blocked_identities RENAME TO blocked_identities_old")
                await tx.execute("CREATE TABLE blocked_identities (id INTEGER PRIMARY KEY, github_id_hash TEXT NOT NULL"
                                 " UNIQUE, created_at TEXT NOT NULL, ref TEXT NOT NULL)")
                await tx.execute("INSERT INTO blocked_identities (github_id_hash, created_at, ref)"
                                 " SELECT github_id_hash, created_at, ref FROM blocked_identities_old ORDER BY created_at")
                await tx.execute("DROP TABLE blocked_identities_old")
        if await self.fetchone("SELECT 1 FROM kv WHERE key = 'audit_github_ids_scrubbed'") is None:
            # once: user_created rows used to carry "github:<raw id>", which outlived account deletion. The live
            # users row still has github_id; the log keeps only that the account came from GitHub. All or nothing,
            # and idempotent if two processes race past the check (the flag is INSERT OR IGNORE).
            async with self.tx() as tx:
                await tx.execute("UPDATE audit_log SET detail = 'github' WHERE action = 'user_created'"
                                 " AND detail LIKE 'github:%'")
                await tx.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('audit_github_ids_scrubbed', '1')")
        audit_cols = {r["name"] for r in await self.fetchall("PRAGMA table_info(audit_log)")}
        for col in ("target", "data_json"):
            if col not in audit_cols:
                await self.conn.execute(f"ALTER TABLE audit_log ADD COLUMN {col} TEXT")

    async def _mark_vacuumed(self) -> None:
        await self.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('secure_delete_vacuumed', '1')")

    def _checkpoint_truncate_sync(self) -> tuple[int, int, int]:
        """The TRUNCATE checkpoint on its own short-lived connection with busy_timeout 0: it never waits for a
        reader, and the shared connection's busy_timeout is never touched (concurrent reads keep 5 s)."""
        con = sqlite3.connect(self.path, timeout=0, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=0")
            return tuple(con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        finally:
            con.close()  # never the last connection (self.conn stays open), so the WAL is not deleted here

    async def checkpoint_truncate(self) -> bool:
        """Copy the WAL into the main file and truncate it to zero bytes: old WAL frames hold pre-delete page
        images until the file is truncated (a normal checkpoint only rewinds it). True when done; False when a
        reader in another connection (an operator shell, `sqlite3 .backup`) holds an older snapshot. Never
        waits for that reader, so the write lock (held so no write lands mid-checkpoint) is held for
        milliseconds, not the 5 s busy timeout."""
        try:
            async with self._write_lock:
                busy, _, _ = await asyncio.to_thread(self._checkpoint_truncate_sync)
        except Exception:  # noqa: BLE001 - I/O error, "database is locked" during another process's recovery
            # A cleanup step: never fail the request or the boot that asked for it. False = the caller's
            # background retry takes over.
            log.exception("WAL truncate failed; will retry")
            return False
        return not busy

    async def truncate_or_retry(self, then: Callable[[], Awaitable[None]] | None = None) -> bool:
        """checkpoint_truncate now, or (blocked) in the background every truncate_retry_s until it succeeds.
        `then` runs after the truncate that succeeds. Returns whether it succeeded right away.
        Logging: one warning when first blocked, one every RETRY_LOG_EVERY failed retries, one on success."""
        if await self.checkpoint_truncate():
            if then is not None:
                await then()
            return True
        if then is not None:
            self._after_truncate.append(then)
        if self._truncate_task is None or self._truncate_task.done():
            log.warning("WAL truncate blocked by another reader; retrying every %gs", self.truncate_retry_s)
            self.truncate_retries = 0
            self._truncate_task = asyncio.create_task(self._truncate_retry(), name="tac-wal-truncate")
        return False

    async def _truncate_retry(self) -> None:
        while True:
            await asyncio.sleep(self.truncate_retry_s)
            self.truncate_retries += 1
            try:
                if not await self.checkpoint_truncate():
                    if self.truncate_retries % RETRY_LOG_EVERY == 0:
                        log.warning("WAL truncate still blocked after %d retries", self.truncate_retries)
                    continue
                log.info("WAL truncate done after %d retries", self.truncate_retries)
                while self._after_truncate:
                    await self._after_truncate.pop(0)()
                return
            except Exception:  # noqa: BLE001
                log.exception("background WAL truncate failed; retrying")

    async def close(self) -> None:
        if self._truncate_task is not None and not self._truncate_task.done():
            self._truncate_task.cancel()  # the next boot's VACUUM/truncate picks it up
            await asyncio.gather(self._truncate_task, return_exceptions=True)
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "database not open"
        return self._conn

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """One autocommitted write statement; returns rowcount."""
        async with self._write_lock:
            async with self.conn.execute(sql, params) as cur:
                return cur.rowcount

    @asynccontextmanager
    async def tx(self) -> AsyncIterator["Tx"]:
        """A write transaction. Holds the lock, so no other write interleaves."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(self.conn)
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")


class Tx:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self.conn.execute(sql, params) as cur:
            return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()) -> Any:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def audit(
        self,
        actor: str,
        action: str,
        submission_id: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
        detail: str | None = None,
        target: str | None = None,
        data: dict | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO audit_log (at, actor, action, submission_id, from_status, to_status, detail, target, data_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), actor, action, submission_id, from_status, to_status, detail, target,
             json.dumps(data) if data else None),
        )
