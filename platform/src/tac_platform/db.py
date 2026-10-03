"""SQLite via aiosqlite: one shared connection, writes serialised by an asyncio.Lock.

The SQL is plain enough to port to Postgres (RETURNING, ON CONFLICT); the swap means
replacing this module with an asyncpg-backed one exposing the same five methods.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

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
    detail        TEXT
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

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._migrate()

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

    async def close(self) -> None:
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
    ) -> None:
        await self.execute(
            "INSERT INTO audit_log (at, actor, action, submission_id, from_status, to_status, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), actor, action, submission_id, from_status, to_status, detail),
        )
