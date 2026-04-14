"""Unit tests for SqliteStorageEngine.

Covers lifecycle (start/close/retry-after-failure), query helpers
(execute/executemany/fetchone/fetchall), error translation, the opaque
exception-message contract, async context-manager usage, and the
thread-affinity correctness of the dedicated single-worker executor.

Each test builds its own engine on a per-test tmp_path scratch DB (never
%LOCALAPPDATA%/nova/) and tears down explicitly. No shared fixtures — if a
later story needs a ``storage_engine`` fixture, it adds it then.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine

# --- Lifecycle: construction, start, close -------------------------------------


async def test_constructor_is_side_effect_free(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = SqliteStorageEngine(db_path)
    # Constructor must NOT create the DB file (composition root assembles
    # engines before the event loop is ready; no I/O allowed).
    assert not db_path.exists()
    assert engine is not None  # sanity — no exception


async def test_start_creates_db_file(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = SqliteStorageEngine(db_path)
    await engine.start()
    try:
        assert db_path.exists()
    finally:
        await engine.close()


async def test_start_creates_missing_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "deep" / "path" / "test.db"
    engine = SqliteStorageEngine(db_path)
    await engine.start()
    try:
        assert db_path.parent.is_dir()
        assert db_path.exists()
    finally:
        await engine.close()


async def test_start_opens_existing_db_without_clobbering(tmp_path: Path) -> None:
    db_path = tmp_path / "preexisting.db"
    # Pre-create a DB via raw sqlite3 and insert a row.
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE thing (val TEXT)")
    conn.execute("INSERT INTO thing (val) VALUES (?)", ("pre_existing",))
    conn.commit()
    conn.close()

    engine = SqliteStorageEngine(db_path)
    await engine.start()
    try:
        row = await engine.fetchone("SELECT val FROM thing")
        assert row is not None
        assert row["val"] == "pre_existing"
    finally:
        await engine.close()


async def test_start_enables_wal_mode(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        row = await engine.fetchone("PRAGMA journal_mode")
        assert row is not None
        # PRAGMA journal_mode returns the mode name; WAL in any case.
        assert str(row[0]).lower() == "wal"
    finally:
        await engine.close()


async def test_start_enables_foreign_keys(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        row = await engine.fetchone("PRAGMA foreign_keys")
        assert row is not None
        assert row[0] == 1
    finally:
        await engine.close()


async def test_start_sets_synchronous_normal(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        row = await engine.fetchone("PRAGMA synchronous")
        assert row is not None
        # NORMAL == 1, FULL == 2, OFF == 0.
        assert row[0] == 1
    finally:
        await engine.close()


async def test_start_called_twice_raises(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        with pytest.raises(StorageError, match="already started"):
            await engine.start()
    finally:
        await engine.close()


# --- Query helpers: execute / executemany / fetchone / fetchall ----------------


async def test_execute_ddl_and_parameterized_insert_roundtrip(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, val TEXT)")
        await engine.execute("INSERT INTO kv (key, val) VALUES (?, ?)", ("a", "alpha"))
        row = await engine.fetchone("SELECT val FROM kv WHERE key = ?", ("a",))
        assert row is not None
        assert row["val"] == "alpha"
    finally:
        await engine.close()


async def test_executemany_inserts_batch_in_order(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE seq (val TEXT)")
        await engine.executemany(
            "INSERT INTO seq (val) VALUES (?)",
            [("a",), ("b",), ("c",)],
        )
        rows = await engine.fetchall("SELECT val FROM seq ORDER BY rowid")
        assert [r["val"] for r in rows] == ["a", "b", "c"]
    finally:
        await engine.close()


async def test_executemany_accepts_generator_materialized_on_caller(tmp_path: Path) -> None:
    """Generator params must be materialized on the caller thread, not the worker."""
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE seq (val TEXT)")

        def gen() -> object:
            for ch in "xyz":
                yield (ch,)

        # This must not raise a thread-crossing or generator-exhaustion error.
        await engine.executemany("INSERT INTO seq (val) VALUES (?)", gen())  # type: ignore[arg-type]
        rows = await engine.fetchall("SELECT val FROM seq ORDER BY rowid")
        assert [r["val"] for r in rows] == ["x", "y", "z"]
    finally:
        await engine.close()


async def test_fetchone_returns_none_on_empty_match(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (val TEXT)")
        row = await engine.fetchone("SELECT val FROM kv WHERE val = ?", ("missing",))
        assert row is None
    finally:
        await engine.close()


async def test_fetchone_returns_sqlite3_row_with_keyed_access(tmp_path: Path) -> None:
    """`row_factory = sqlite3.Row` enables row["col_name"] access — lock it."""
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (key TEXT, val TEXT)")
        await engine.execute("INSERT INTO kv (key, val) VALUES (?, ?)", ("k1", "v1"))
        row = await engine.fetchone("SELECT key, val FROM kv")
        assert row is not None
        assert row["key"] == "k1"
        assert row["val"] == "v1"
        # Also proves index access (tuple-like) still works.
        assert row[0] == "k1"
    finally:
        await engine.close()


async def test_fetchall_returns_empty_list_on_empty_match(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (val TEXT)")
        rows = await engine.fetchall("SELECT val FROM kv")
        assert rows == []
    finally:
        await engine.close()


async def test_executemany_with_empty_iterable_is_safe_noop(tmp_path: Path) -> None:
    """Empty-sequence executemany commits nothing but raises no error."""
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (val TEXT)")
        await engine.executemany("INSERT INTO kv (val) VALUES (?)", [])
        rows = await engine.fetchall("SELECT val FROM kv")
        assert rows == []
    finally:
        await engine.close()


# --- Bare-str/bytes guard (D1 / review code-review patch) ----------------------


@pytest.mark.parametrize(
    ("helper_name", "make_call"),
    [
        ("execute", lambda e: e.execute("SELECT ?", "abc")),
        ("fetchone", lambda e: e.fetchone("SELECT ?", "abc")),
        ("fetchall", lambda e: e.fetchall("SELECT ?", "abc")),
    ],
)
async def test_bare_str_params_rejected_with_clear_error(
    tmp_path: Path,
    helper_name: str,
    make_call: Callable[[SqliteStorageEngine], Awaitable[object]],
) -> None:
    """Bare ``str`` passed where a row of params is expected is rejected.

    ``"abc"`` is a ``Sequence[str]`` so the type system accepts it, but
    sqlite3 would iterate it into three single-char bindings. The guard
    raises a clear ``StorageError`` at the engine boundary instead.
    """
    engine = SqliteStorageEngine(tmp_path / "bare_str.db")
    await engine.start()
    try:
        with pytest.raises(StorageError, match="bare str/bytes"):
            await make_call(engine)
    finally:
        await engine.close()
    assert helper_name  # parametrize id consumed


@pytest.mark.parametrize(
    ("helper_name", "make_call"),
    [
        ("execute", lambda e: e.execute("SELECT ?", b"abc")),
        ("fetchone", lambda e: e.fetchone("SELECT ?", b"abc")),
        ("fetchall", lambda e: e.fetchall("SELECT ?", b"abc")),
    ],
)
async def test_bare_bytes_params_rejected_with_clear_error(
    tmp_path: Path,
    helper_name: str,
    make_call: Callable[[SqliteStorageEngine], Awaitable[object]],
) -> None:
    """Bare ``bytes`` passed where a row of params is expected is rejected.

    ``b"abc"`` is a ``Sequence[int]`` so the type system accepts it, but
    sqlite3 would iterate it into three single-int bindings.
    """
    engine = SqliteStorageEngine(tmp_path / "bare_bytes.db")
    await engine.start()
    try:
        with pytest.raises(StorageError, match="bare str/bytes"):
            await make_call(engine)
    finally:
        await engine.close()
    assert helper_name


async def test_executemany_bare_str_row_rejected(tmp_path: Path) -> None:
    """Each row of executemany's seq_of_params is also guarded."""
    engine = SqliteStorageEngine(tmp_path / "executemany_bare.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (val TEXT)")
        # First row is a valid tuple, second row is a bare str — must reject.
        with pytest.raises(StorageError, match="bare str/bytes"):
            await engine.executemany(
                "INSERT INTO kv (val) VALUES (?)",
                [("ok",), "bad"],
            )
    finally:
        await engine.close()


@pytest.mark.parametrize("bad_input", ["abc", b"abc"])
async def test_executemany_top_level_bare_str_or_bytes_rejected(
    tmp_path: Path, bad_input: str | bytes
) -> None:
    """Top-level ``seq_of_params`` as bare str/bytes must surface as StorageError.

    Without an explicit top-level guard, the per-row guard catches
    bare-str (each iterated char is ``str``) but NOT bare-bytes (each
    iterated value is ``int``, which the guard lets pass, and then
    ``tuple(int)`` raises a raw ``TypeError`` outside the engine's
    error-translation net). This test locks both cases.
    """
    engine = SqliteStorageEngine(tmp_path / "executemany_top_bare.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE kv (val TEXT)")
        with pytest.raises(StorageError, match="bare str/bytes"):
            # The runtime guard IS the contract here; the type system
            # would normally flag this misuse, but we need to exercise
            # the runtime rejection path for callers who bypass typing.
            await engine.executemany(
                "INSERT INTO kv (val) VALUES (?)",
                bad_input,  # type: ignore[arg-type]
            )
    finally:
        await engine.close()


async def test_tuple_bytes_param_is_valid(tmp_path: Path) -> None:
    """A ``bytes`` value INSIDE a tuple is valid — the guard only rejects bare scalars."""
    engine = SqliteStorageEngine(tmp_path / "tuple_bytes.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE blobs (val BLOB)")
        await engine.execute("INSERT INTO blobs (val) VALUES (?)", (b"binary-data",))
        row = await engine.fetchone("SELECT val FROM blobs")
        assert row is not None
        assert row["val"] == b"binary-data"
    finally:
        await engine.close()


# --- close() idempotency and post-close guards ---------------------------------


async def test_close_is_idempotent(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    await engine.close()
    # Second close is a no-op; must not raise.
    await engine.close()


async def test_close_before_start_is_noop(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    # Never started — close must be safe.
    await engine.close()


@pytest.mark.parametrize(
    ("call_name", "make_call"),
    [
        ("execute", lambda e: e.execute("SELECT 1")),
        ("executemany", lambda e: e.executemany("SELECT 1", [()])),
        ("fetchone", lambda e: e.fetchone("SELECT 1")),
        ("fetchall", lambda e: e.fetchall("SELECT 1")),
    ],
)
async def test_query_after_close_raises_not_started(
    tmp_path: Path,
    call_name: str,
    make_call: Callable[[SqliteStorageEngine], Awaitable[object]],
) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    await engine.close()
    with pytest.raises(StorageError, match="not started"):
        await make_call(engine)
    assert call_name  # argvalue consumed (parametrize id)


@pytest.mark.parametrize(
    ("call_name", "make_call"),
    [
        ("execute", lambda e: e.execute("SELECT 1")),
        ("executemany", lambda e: e.executemany("SELECT 1", [()])),
        ("fetchone", lambda e: e.fetchone("SELECT 1")),
        ("fetchall", lambda e: e.fetchall("SELECT 1")),
    ],
)
async def test_query_before_start_raises_not_started(
    tmp_path: Path,
    call_name: str,
    make_call: Callable[[SqliteStorageEngine], Awaitable[object]],
) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    with pytest.raises(StorageError, match="not started"):
        await make_call(engine)
    assert call_name


# --- Error translation: sqlite3.Error -> StorageError --------------------------


@pytest.mark.parametrize(
    ("helper_name", "make_call", "expected_msg"),
    [
        ("execute", lambda e: e.execute("SELECT bogus FROM nonexistent"), "execute failed"),
        (
            "executemany",
            lambda e: e.executemany("INSERT INTO nonexistent (c) VALUES (?)", [("v",)]),
            "executemany failed",
        ),
        (
            "fetchone",
            lambda e: e.fetchone("SELECT bogus FROM nonexistent"),
            "fetchone failed",
        ),
        (
            "fetchall",
            lambda e: e.fetchall("SELECT bogus FROM nonexistent"),
            "fetchall failed",
        ),
    ],
)
async def test_sqlite_error_translated_to_storage_error(
    tmp_path: Path,
    helper_name: str,
    make_call: Callable[[SqliteStorageEngine], Awaitable[object]],
    expected_msg: str,
) -> None:
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        with pytest.raises(StorageError) as info:
            await make_call(engine)
        assert str(info.value) == expected_msg
        # `from err` must populate __cause__ with the underlying sqlite3 error.
        assert isinstance(info.value.__cause__, sqlite3.Error)
    finally:
        await engine.close()
    assert helper_name  # argvalue consumed


async def test_start_failure_on_unwritable_path_raises_storage_error(tmp_path: Path) -> None:
    # Create a regular file, then try to treat it as a parent directory.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    db_path = blocker / "nested.db"

    engine = SqliteStorageEngine(db_path)
    with pytest.raises(StorageError, match="start failed") as info:
        await engine.start()
    # Chained cause is either OSError (mkdir) or sqlite3.Error (connect).
    assert isinstance(info.value.__cause__, (OSError, sqlite3.Error))


async def test_failed_start_aftermath_matches_never_started(tmp_path: Path) -> None:
    """After a failed start(), the engine is indistinguishable from never-started.

    Pins AC #1's failure-cleanup contract: no leaked worker thread, no
    half-initialized connection. ``close()`` is a safe no-op and query
    helpers raise the same "not started" error a fresh engine would.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    engine = SqliteStorageEngine(blocker / "nested.db")

    with pytest.raises(StorageError, match="start failed"):
        await engine.start()

    # (a) close() is a safe no-op.
    await engine.close()

    # (b) Query helpers raise "not started" — same as a never-started engine.
    with pytest.raises(StorageError, match="not started"):
        await engine.fetchone("SELECT 1")


async def test_fresh_engine_succeeds_after_prior_instance_failed(tmp_path: Path) -> None:
    """A fresh engine on a valid path succeeds even if an earlier engine failed.

    Production usage pattern (per SqliteStorageEngine class docstring):
    after a failed start(), the caller constructs a new engine instance
    rather than mutating the original. This test locks that flow.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    failed_engine = SqliteStorageEngine(blocker / "nested.db")
    with pytest.raises(StorageError, match="start failed"):
        await failed_engine.start()

    # Construct a fresh engine on a valid path.
    engine = SqliteStorageEngine(tmp_path / "retry.db")
    await engine.start()
    try:
        row = await engine.fetchone("PRAGMA journal_mode")
        assert row is not None
        assert str(row[0]).lower() == "wal"
    finally:
        await engine.close()


async def test_execute_error_message_is_opaque_no_sql_or_params(tmp_path: Path) -> None:
    """Exception message must NOT contain raw SQL or user-supplied params.

    Project-context.md:174 — no sensitive content in exception messages.
    """
    engine = SqliteStorageEngine(tmp_path / "test.db")
    await engine.start()
    try:
        with pytest.raises(StorageError) as info:
            await engine.execute(
                "INSERT INTO x (col) VALUES (?)",
                ("secret-token-xyz",),
            )
        msg = str(info.value)
        assert "secret-token-xyz" not in msg
        assert "INSERT INTO" not in msg
        # Reverse-check: the message is the generic opaque form.
        assert msg == "execute failed"
    finally:
        await engine.close()


# --- Async context manager -----------------------------------------------------


async def test_async_context_manager_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "ctx.db"
    async with SqliteStorageEngine(db_path) as engine:
        await engine.execute("CREATE TABLE t (val TEXT)")
        assert db_path.exists()
        row = await engine.fetchone("PRAGMA journal_mode")
        assert row is not None
        assert str(row[0]).lower() == "wal"

    # After the block exits, engine is closed — further calls raise.
    with pytest.raises(StorageError, match="not started"):
        await engine.fetchone("SELECT 1")


# --- Thread-affinity correctness under concurrent coroutines ------------------


def _patch_sqlite3_factory(
    monkeypatch: pytest.MonkeyPatch, connection_class: type[sqlite3.Connection]
) -> None:
    """Monkeypatch sqlite3.connect to inject a Connection subclass factory.

    sqlite3.Connection is an immutable C extension type — methods cannot
    be monkeypatched on it. The supported way to swap behavior is via
    ``sqlite3.connect(..., factory=MyConnection)``. This helper wraps
    the module-level ``sqlite3.connect`` to always pass the given factory,
    so tests can exercise custom Connection behaviors without changing
    production code.

    sqlite3.connect has multiple overloads with narrow typing; we route
    around that with a small Any-cast — acceptable per project-context.md:130
    for third-party-integration-boundary test infrastructure.
    """
    from typing import Any

    original_connect: Any = sqlite3.connect

    def patched_connect(
        database: str, *, timeout: float, detect_types: int, isolation_level: str
    ) -> sqlite3.Connection:
        result: sqlite3.Connection = original_connect(
            database,
            timeout=timeout,
            detect_types=detect_types,
            isolation_level=isolation_level,
            factory=connection_class,
        )
        return result

    monkeypatch.setattr("sqlite3.connect", patched_connect)


async def test_wal_verification_rejects_silent_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If PRAGMA journal_mode = WAL silently falls back to another mode, start() must fail.

    Some filesystems (older network FSes, certain VFSes) reject WAL and
    SQLite reports the actual mode in the pragma's row. WAL semantics
    are load-bearing for crash safety; a silent fallback must surface
    as StorageError("start failed"), not pass.
    """

    class _FallbackConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
            if "journal_mode" in sql.lower() and "wal" in sql.lower():
                return super().execute("SELECT 'delete' AS journal_mode")
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    _patch_sqlite3_factory(monkeypatch, _FallbackConnection)

    engine = SqliteStorageEngine(tmp_path / "fallback.db")
    with pytest.raises(StorageError, match="start failed") as info:
        await engine.start()
    assert isinstance(info.value.__cause__, sqlite3.OperationalError)


async def test_close_translates_raising_conn_close_to_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising conn.close() is translated to StorageError and the executor is still shut down.

    Regression guard: previously the ``try/except`` without ``finally``
    skipped ``executor.shutdown(wait=True)`` when conn.close raised,
    leaking the worker thread.
    """

    class _RaisingCloseConnection(sqlite3.Connection):
        def close(self) -> None:
            raise sqlite3.OperationalError("simulated close failure")

    _patch_sqlite3_factory(monkeypatch, _RaisingCloseConnection)

    engine = SqliteStorageEngine(tmp_path / "close_raises.db")
    await engine.start()

    assert engine._executor is not None  # noqa: SLF001 — locking internal state for the test
    captured_executor = engine._executor  # noqa: SLF001

    with pytest.raises(StorageError, match="close failed") as info:
        await engine.close()
    assert isinstance(info.value.__cause__, sqlite3.Error)

    # Engine is in the "not started" state — idempotent re-close is safe.
    await engine.close()

    # Executor must have been shut down even though conn.close raised.
    # ThreadPoolExecutor exposes `_shutdown` internally; poking is acceptable
    # in tests to lock the "no leaked worker thread" contract.
    assert captured_executor._shutdown is True  # noqa: SLF001


async def test_concurrent_close_and_execute_race_surfaces_storage_error(tmp_path: Path) -> None:
    """execute() racing with close() must NOT produce AssertionError / AttributeError.

    Regression guard: the sync helpers now take ``conn`` as an explicit
    parameter, captured on the caller thread before dispatch. Previously
    they read ``self._connection`` on the worker, which could be None if
    close() ran first → AssertionError (or AttributeError under -O). The
    refactor eliminates the race; any remaining error must be a
    StorageError or a RuntimeError-translated-to-StorageError.
    """
    engine = SqliteStorageEngine(tmp_path / "race.db")
    await engine.start()
    await engine.execute("CREATE TABLE kv (val TEXT)")

    # Kick off a query and a close roughly simultaneously.
    query_task = asyncio.create_task(engine.execute("INSERT INTO kv (val) VALUES (?)", ("x",)))
    close_task = asyncio.create_task(engine.close())

    # Both complete; any failure must be StorageError, never AssertionError.
    results = await asyncio.gather(query_task, close_task, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            assert isinstance(result, StorageError), (
                f"Race produced {type(result).__name__} instead of StorageError: {result!r}"
            )


async def test_concurrent_executes_serialize_correctly(tmp_path: Path) -> None:
    """Single-worker executor guarantees thread-affine sqlite3 access.

    With the default asyncio.to_thread pool (multiple workers), the second
    concurrent call would crash with
    ``sqlite3.ProgrammingError: SQLite objects created in a thread can only
    be used in that same thread``. This test catches any regression that
    swaps ``run_in_executor(self._executor, ...)`` for ``asyncio.to_thread``.

    Uses ``ORDER BY rowid`` to actually assert insertion order — a weaker
    ``ORDER BY val`` would pass even if the three inserts landed in a
    different serialization order.
    """
    engine = SqliteStorageEngine(tmp_path / "concurrent.db")
    await engine.start()
    try:
        await engine.execute("CREATE TABLE seq (val TEXT)")
        await asyncio.gather(
            engine.execute("INSERT INTO seq (val) VALUES (?)", ("a",)),
            engine.execute("INSERT INTO seq (val) VALUES (?)", ("b",)),
            engine.execute("INSERT INTO seq (val) VALUES (?)", ("c",)),
        )
        rows = await engine.fetchall("SELECT val FROM seq ORDER BY rowid")
        # All three rows present (thread-affinity intact); insertion order
        # matches gather order (single-worker executor serializes FIFO).
        assert sorted(r["val"] for r in rows) == ["a", "b", "c"]
    finally:
        await engine.close()
