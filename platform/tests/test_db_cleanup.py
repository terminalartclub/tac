"""WAL truncate and the one-time VACUUM: never block on another reader, never stop the app from starting."""

import asyncio
import logging
import sqlite3
import time

import aiosqlite

from tac_platform.db import SCHEMA, Database

MARK = b"PRE-DELETE-MARKER"


def _wal(path):
    return path.with_name(path.name + "-wal")


def _bytes(path) -> bytes:
    return path.read_bytes() + (_wal(path).read_bytes() if _wal(path).exists() else b"")


def _reader(path) -> sqlite3.Connection:
    """Another connection holding a read snapshot: an operator shell, or `sqlite3 .backup` mid-copy."""
    r = sqlite3.connect(path)
    r.execute("BEGIN")
    r.execute("SELECT * FROM kv").fetchall()
    return r


async def _until(cond, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if await cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


async def test_truncate_blocked_by_a_reader_returns_fast_and_retries_in_the_background(tmp_path, caplog):
    path = tmp_path / "x.sqlite3"
    db = Database(path)
    await db.open()
    db.truncate_retry_s = 0.05
    try:
        await db.execute("CREATE TABLE t (x BLOB)")
        await db.execute("INSERT INTO t VALUES (?)", (MARK * 20,))
        reader = _reader(path)
        await db.execute("DELETE FROM t")
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="tac.db"):
            assert await db.truncate_or_retry() is False  # busy: reported, not ignored
        assert time.monotonic() - t0 < 1.0  # not the 5 s busy timeout
        assert "WAL truncate blocked" in caplog.text
        t0 = time.monotonic()
        await db.execute("INSERT INTO t VALUES (x'00')")  # the write lock was released
        assert time.monotonic() - t0 < 1.0
        assert MARK in _bytes(path)  # still in an old WAL frame while the reader holds its snapshot
        reader.rollback()
        reader.close()
        await _until(lambda: _done(db))  # the background retry finishes once the reader is gone
        assert MARK not in _bytes(path) and _wal(path).stat().st_size == 0
    finally:
        await db.close()


async def _done(db) -> bool:
    return db._truncate_task is not None and db._truncate_task.done()


async def test_account_delete_with_a_reader_open_is_fast_and_cleans_up_after(tmp_path):
    from conftest import make_ctx

    async with make_ctx(tmp_path) as ctx:
        db = ctx.app.state.db
        db.truncate_retry_s = 0.05
        token = await ctx.login("zz-unique-handle")
        reader = _reader(db.path)
        t0 = time.monotonic()
        async with ctx.client(authorization=f"Bearer {token}") as c:
            r = await c.request("DELETE", "/v1/me", json={"confirm": "zz-unique-handle"})
        assert r.status_code == 204 and time.monotonic() - t0 < 2.0
        async with ctx.admin() as a:  # media lock and write lock both free
            assert (await a.get("/v1/admin/queue")).status_code == 200
        reader.rollback()
        reader.close()
        await _until(lambda: _done(db))
        assert b"zz-unique-handle" not in _bytes(db.path)


def _old_file_with_residue(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA secure_delete=OFF")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO kv VALUES ('audit_github_ids_scrubbed', '1')")
    con.execute("CREATE TABLE junk (x BLOB)")
    con.execute("INSERT INTO junk VALUES (?)", (MARK * 50,))
    con.commit()
    con.execute("DELETE FROM junk")
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    assert MARK in path.read_bytes()


async def _flag(db):
    return await db.fetchone("SELECT value FROM kv WHERE key = 'secure_delete_vacuumed'")


async def test_boot_vacuum_flag_waits_for_a_truncate_that_isnt_blocked(tmp_path):
    path = tmp_path / "x.sqlite3"
    _old_file_with_residue(path)
    reader = _reader(path)
    db = Database(path)
    db.truncate_retry_s = 0.05
    t0 = time.monotonic()
    await db.open()
    try:
        assert time.monotonic() - t0 < 2.0
        assert await _flag(db) is None  # vacuumed, but the truncate was blocked: not done yet
        reader.rollback()
        reader.close()
        await _until(lambda: _done(db))
        assert (await _flag(db))["value"] == "1"
        assert MARK not in _bytes(path)
    finally:
        await db.close()


async def test_a_failing_boot_vacuum_never_stops_the_app(tmp_path, monkeypatch, caplog):
    path = tmp_path / "x.sqlite3"
    _old_file_with_residue(path)
    real = aiosqlite.Connection.execute

    def full_disk(self, sql, *args, **kwargs):
        if sql.strip().upper() == "VACUUM":
            raise sqlite3.OperationalError("database or disk is full")
        return real(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", full_disk)
    db = Database(path)
    with caplog.at_level(logging.ERROR, logger="tac.db"):
        await db.open()  # starts anyway
    try:
        assert "one-time VACUUM failed" in caplog.text and "disk is full" in caplog.text
        assert await _flag(db) is None  # next boot retries
        await db.execute("INSERT INTO kv VALUES ('still-writable', '1')")  # and the DB is usable
        assert not db.conn.in_transaction
    finally:
        await db.close()
    monkeypatch.setattr(aiosqlite.Connection, "execute", real)
    db = Database(path)  # the next boot, disk freed
    await db.open()
    try:
        assert (await _flag(db))["value"] == "1" and MARK not in _bytes(path)
    finally:
        await db.close()


async def test_checkpoint_truncate_reports_success_without_readers(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    await db.open()
    try:
        assert await db.checkpoint_truncate() is True
    finally:
        await db.close()


async def test_concurrent_reads_never_see_a_zero_busy_timeout(tmp_path):
    """The checkpoint must not lower the SHARED connection's busy_timeout, even for a moment: every read
    interleaved with checkpoints sees the normal 5 s."""
    db = Database(tmp_path / "x.sqlite3")
    await db.open()
    seen = []
    try:
        async def reads():
            for _ in range(200):
                seen.append((await db.fetchone("PRAGMA busy_timeout"))[0])
                await asyncio.sleep(0)

        async def checkpoints():
            for _ in range(200):
                await db.execute("INSERT INTO kv (key, value) VALUES ('k', 'v') ON CONFLICT(key) DO UPDATE SET value = 'v'")
                assert await db.checkpoint_truncate()
                await asyncio.sleep(0)

        await asyncio.gather(reads(), checkpoints())
    finally:
        await db.close()
    assert len(seen) == 200 and set(seen) == {5000}


async def test_blocked_retry_logs_first_every_tenth_and_success(tmp_path, caplog):
    path = tmp_path / "x.sqlite3"
    db = Database(path)
    await db.open()
    db.truncate_retry_s = 0.005
    try:
        await db.execute("CREATE TABLE t (x TEXT)")
        reader = _reader(path)
        await db.execute("INSERT INTO t VALUES ('x')")
        with caplog.at_level(logging.INFO, logger="tac.db"):
            assert await db.truncate_or_retry() is False
            assert await db.truncate_or_retry() is False  # a second caller while the task runs: no new line

            async def retried_25():
                return db.truncate_retries >= 25

            await _until(retried_25)
            reader.rollback()
            reader.close()
            await _until(lambda: _done(db))
        lines = [r.getMessage() for r in caplog.records if r.name == "tac.db"]
        assert lines[0].startswith("WAL truncate blocked by another reader")
        still = [m for m in lines if m.startswith("WAL truncate still blocked after")]
        assert still[:2] == ["WAL truncate still blocked after 10 retries", "WAL truncate still blocked after 20 retries"]
        assert len(still) == (db.truncate_retries - 1) // 10  # failed retries = all but the last
        assert lines[-1] == f"WAL truncate done after {db.truncate_retries} retries"
        assert len(lines) == 1 + len(still) + 1  # nothing per attempt
    finally:
        await db.close()


def _raising(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")  # e.g. another process mid WAL recovery, or disk I/O


async def test_a_raising_checkpoint_after_account_delete_is_a_204_with_a_retry(tmp_path, monkeypatch, caplog):
    from conftest import make_ctx

    async with make_ctx(tmp_path) as ctx:
        db = ctx.app.state.db
        db.truncate_retry_s = 0.05
        token = await ctx.login("zz-raise-handle")
        monkeypatch.setattr(Database, "_checkpoint_truncate_sync", _raising)
        with caplog.at_level(logging.ERROR, logger="tac.db"):
            async with ctx.client(authorization=f"Bearer {token}") as c:
                r = await c.request("DELETE", "/v1/me", json={"confirm": "zz-raise-handle"})
        assert r.status_code == 204  # the account is gone; the cleanup failing doesn't turn that into a 500
        assert "WAL truncate failed" in caplog.text
        assert (await db.fetchone("SELECT COUNT(*) AS n FROM users"))["n"] == 0
        assert db._truncate_task is not None and not db._truncate_task.done()  # retry scheduled
        monkeypatch.undo()  # the condition clears
        await _until(lambda: _done(db))
        assert b"zz-raise-handle" not in _bytes(db.path)


async def test_a_raising_checkpoint_at_boot_never_stops_the_app(tmp_path, monkeypatch):
    path = tmp_path / "x.sqlite3"
    _old_file_with_residue(path)
    monkeypatch.setattr(Database, "_checkpoint_truncate_sync", _raising)
    db = Database(path)
    db.truncate_retry_s = 0.05
    await db.open()  # VACUUM ran; its truncate raised: still starts
    try:
        assert await _flag(db) is None  # not done until a truncate succeeds
        await db.execute("INSERT INTO kv VALUES ('writable', '1')")
        monkeypatch.undo()
        await _until(lambda: _done(db))
        assert (await _flag(db))["value"] == "1" and MARK not in _bytes(path)
    finally:
        await db.close()
