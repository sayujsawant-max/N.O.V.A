"""SQLite storage engine — the single sqlite3 boundary for the whole product.

``SqliteStorageEngine`` owns the process's one ``sqlite3.Connection`` and
routes every DB call through a dedicated single-worker
``ThreadPoolExecutor``. This is infrastructure under ``core/``, not a
system adapter. Later work (Brain's SQLite adapter in Story 3.x, the
migration runner in Story 1.5, the AuditLogger in Story 1.8) consumes
this engine via constructor injection through the composition root.

Architecture divergence owned by this story
-------------------------------------------
``architecture.md`` lines 1170–1174 sketch migration files using
``aiosqlite.Connection``. Story 1.4 overrides that pattern per
``epics.md`` line 717: stdlib ``sqlite3`` is the only persistence library
in T1, wrapped in an asyncio-friendly executor. ``aiosqlite`` would
duplicate the wrapping logic and add a dependency; the dedicated
single-worker ``ThreadPoolExecutor`` keeps the surface stdlib-only while
preserving sqlite3's thread-affinity contract.

Thread-affinity contract (load-bearing correctness)
---------------------------------------------------
``sqlite3.Connection`` objects are tied to the thread that created them
when ``check_same_thread=True`` (the default). The engine creates the
connection inside a dedicated ``ThreadPoolExecutor(max_workers=1)`` and
routes every subsequent call through the same worker via
``loop.run_in_executor(self._executor, ...)``. Never use
``asyncio.to_thread`` here — that targets the default multi-worker pool
and would break thread affinity on the second call.

close() is a process-shutdown operation
---------------------------------------
``close()`` calls ``executor.shutdown(wait=True)`` synchronously after
the final worker task completes — safe at process shutdown, do not call
during active session operation. The brief block until the worker drains
stalls the event loop. Story 1.10's composition-root shutdown invokes
``await engine.close()`` once, at the end of the session.

WAL sidecar files
-----------------
``PRAGMA journal_mode = WAL`` creates two sidecar files beside the main
DB: ``<db>-wal`` and ``<db>-shm``. Any user-facing backup flow (Story
5.6) MUST copy all three files together. ``.gitignore`` covers them for
the repo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import cast

from nova.core.exceptions import StorageError

logger = logging.getLogger("nova.core.storage.engine")

type SqlParams = Sequence[str | int | float | bytes | None]
"""Typed parameter shape for sqlite3 query helpers.

``sqlite3`` itself accepts a broader ``Any`` via its stubs; we narrow to
the five storable scalar types so the domain boundary is strict-mypy
clean without ``Any``.
"""

type _SqlParamsTuple = tuple[str | int | float | bytes | None, ...]


def _reject_scalar_string_params(params: SqlParams) -> None:
    """Reject bare ``str`` / ``bytes`` passed where a row of params is expected.

    ``SqlParams = Sequence[str | int | float | bytes | None]`` is broad by
    design — it accepts tuples, lists, and any other ordered sequence of
    storable scalars. But a bare ``str`` is also a ``Sequence[str]`` and
    a bare ``bytes`` is a ``Sequence[int]``, so type-checking accepts
    ``execute("... WHERE c = ?", "abc")`` even though sqlite3 will
    iterate the string and raise a confusing
    ``ProgrammingError: Incorrect number of bindings supplied``.
    This guard catches that footgun at the engine boundary and raises a
    clear ``StorageError`` instead. Only bare ``str`` and ``bytes`` are
    rejected — every other sequence shape passes through untouched.
    """
    if isinstance(params, (str, bytes)):
        raise StorageError("params must be a tuple or list of scalars, not a bare str/bytes")


class SqliteStorageEngine:
    """Async SQLite storage engine — connection lifecycle + query helpers.

    Construct with a ``db_path: Path``. Call ``await engine.start()`` once
    to open the connection and apply pragmas; call ``await engine.close()``
    once at process shutdown. The engine is **not reentrant-started** —
    calling ``start()`` twice without an intervening ``close()`` raises
    ``StorageError``. ``close()`` is idempotent.

    Query helpers (``execute``, ``executemany``, ``fetchone``, ``fetchall``)
    all route through a dedicated ``ThreadPoolExecutor(max_workers=1)`` so
    sqlite3's default thread-affinity contract holds. Handlers that need
    to issue multi-statement operations (transactions, Brain-owned batch
    writes) can layer those on top in a later story; this class is a
    narrow infrastructure primitive.

    Error translation: every ``sqlite3.Error`` and ``OSError`` raised by
    sqlite3 or filesystem calls becomes a ``StorageError`` with an opaque
    generic message (``"execute failed"``, ``"start failed"``, etc.). The
    underlying exception is chained via ``from err`` so tracebacks retain
    detail while top-level messages never leak SQL bodies or user params.

    Intended use: one instance per process, constructed in the composition
    root (Story 1.10), injected into systems/adapters that need storage.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._connection: sqlite3.Connection | None = None
        self._executor: ThreadPoolExecutor | None = None
        # Story 1.5: transaction mutex + owning-task identity. asyncio.Lock
        # in 3.10+ lazily binds to the running loop on first acquire, so
        # constructing here (outside any loop) is safe. Tracking the owning
        # asyncio.Task lets execute()/executemany() decide synchronously
        # whether the caller is inside its own transaction (re-entrant via
        # the same task) — a plain bool would race with concurrent tasks.
        self._tx_lock: asyncio.Lock = asyncio.Lock()
        self._tx_owner: asyncio.Task[object] | None = None

    async def __aenter__(self) -> SqliteStorageEngine:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Create parent dirs if missing, open the connection, apply pragmas.

        Raises ``StorageError`` on failure. On any failure after executor
        instantiation, any partial state (local connection, local
        executor) is cleaned up and the engine is left indistinguishable
        from a never-started instance — ``close()`` after a failed
        ``start()`` is a safe no-op, and ``start()`` may be retried on a
        fresh path.
        """
        if self._connection is not None:
            raise StorageError("storage engine already started")

        local_executor: ThreadPoolExecutor | None = None
        local_connection: sqlite3.Connection | None = None

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            local_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="nova-sqlite",
            )
            loop = asyncio.get_running_loop()
            local_connection = await loop.run_in_executor(
                local_executor, self._open_and_configure_sync
            )
        except (OSError, sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
            self._cleanup_partial_start(local_connection, local_executor)
            raise StorageError("start failed") from err
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit — clean up
            # and re-raise untouched.
            self._cleanup_partial_start(local_connection, local_executor)
            raise

        self._executor = local_executor
        self._connection = local_connection

    async def close(self) -> None:
        """Close the connection and shut down the worker executor.

        Idempotent — calling twice (or before ``start()``, or after a
        failed ``start()``) is a safe no-op. NOT safe to call during
        active session operation — ``executor.shutdown(wait=True)``
        blocks the event loop until the worker drains.

        Exception safety: the executor is shut down in a ``finally`` arm
        so a failing ``conn.close()`` or an incoming ``CancelledError``
        never leaks the worker thread. ``cancel_futures=True`` drops any
        queued work so shutdown doesn't wait on concurrent submissions.
        """
        if self._connection is None and self._executor is None:
            return

        conn = self._connection
        executor = self._executor
        # Flip state FIRST so even if close steps raise, the engine is
        # already in the "not started" state per the post-condition.
        self._connection = None
        self._executor = None

        pending_error: StorageError | None = None
        try:
            if conn is not None and executor is not None:
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(executor, conn.close)
                except (sqlite3.Error, sqlite3.Warning, OSError, RuntimeError) as err:
                    pending_error = StorageError("close failed")
                    pending_error.__cause__ = err
        finally:
            if executor is not None:
                # Always reap the executor — guarantees no worker-thread leak
                # even if conn.close raised above or CancelledError propagates.
                try:
                    executor.shutdown(wait=True, cancel_futures=True)
                except Exception:  # noqa: BLE001 — cleanup path, log and swallow
                    logger.debug("secondary error during close() executor shutdown", exc_info=True)

        if pending_error is not None:
            raise pending_error

    async def run_migrations(self) -> list[int]:
        """Discover and apply pending migrations via MigrationRunner.

        Thin delegator — exists so the composition root (Story 1.10) can
        call ``await storage.run_migrations()`` matching architecture.md
        line 1068. See
        ``nova.core.storage.migrations.runner.MigrationRunner`` for the
        full contract.
        """
        self._require_started()
        try:
            # Function-local import breaks the circular dependency:
            # runner.py imports SqliteStorageEngine at module level.
            from nova.core.storage.migrations.runner import MigrationRunner
        except ImportError as err:
            # A broken migrations sub-package must surface as StorageError so
            # the composition-root contract (all persistence failures route
            # through StorageError) holds.
            raise StorageError("migration runner unavailable") from err

        return await MigrationRunner(self).run()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Multi-statement transaction context manager.

        Inside the block, ``execute`` / ``executemany`` called from the same
        asyncio task do NOT auto-commit — COMMIT fires on context exit;
        ROLLBACK on any exception (including ``CancelledError``). Nested
        transactions on the owning task are rejected with
        ``StorageError("nested transaction")``.

        Concurrency model: ``transaction()`` acquires ``self._tx_lock`` and
        records ``self._tx_owner = asyncio.current_task()``. Any
        ``execute``/``executemany`` call from a *different* task blocks on
        ``self._tx_lock`` until the transaction completes — preventing
        the auto-commit-of-foreign-transaction race. Calls from the
        *owning* task short-circuit the lock acquisition (asyncio.Lock is
        not reentrant) and use the no-commit dispatch path.

        Story 1.5 added this primitive specifically so the migration runner
        can keep DDL + the ``schema_version`` insert in one atomic unit;
        Story 1.4's per-statement auto-commit shape made multi-statement
        atomicity via ``execute()`` impossible.
        """
        self._require_started()
        current = asyncio.current_task()
        if self._tx_owner is current and current is not None:
            raise StorageError("nested transaction")
        async with self._tx_lock:
            assert self._connection is not None
            assert self._executor is not None
            conn = self._connection
            executor = self._executor
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    executor, self._execute_sync_no_commit, conn, "BEGIN IMMEDIATE", ()
                )
            except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
                raise StorageError("transaction begin failed") from err
            self._tx_owner = current
            try:
                try:
                    yield
                except BaseException:
                    # ROLLBACK best-effort. asyncio.shield prevents an outer
                    # cancellation from interrupting the rollback — without
                    # shield, a CancelledError during the await would replace
                    # the original exception and abandon the rollback on the
                    # worker thread mid-flight. suppress(BaseException) here
                    # so the shielded ROLLBACK's own CancelledError doesn't
                    # mask the original failure either.
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(
                            loop.run_in_executor(
                                executor,
                                self._execute_sync_no_commit,
                                conn,
                                "ROLLBACK",
                                (),
                            )
                        )
                    raise
                else:
                    try:
                        await loop.run_in_executor(
                            executor, self._execute_sync_no_commit, conn, "COMMIT", ()
                        )
                    except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
                        # COMMIT failed — attempt rollback best-effort, then
                        # surface as StorageError per the engine boundary
                        # contract.
                        with contextlib.suppress(BaseException):
                            await asyncio.shield(
                                loop.run_in_executor(
                                    executor,
                                    self._execute_sync_no_commit,
                                    conn,
                                    "ROLLBACK",
                                    (),
                                )
                            )
                        raise StorageError("transaction commit failed") from err
            finally:
                self._tx_owner = None

    async def execute(self, sql: str, params: SqlParams = ()) -> None:
        """Run a single write (INSERT/UPDATE/DELETE/DDL) and commit.

        Inside an active ``transaction()`` block on the same asyncio task,
        the per-statement commit is suppressed — COMMIT fires on
        context-exit. Calls from a *different* task block on the
        transaction lock until the active transaction completes; this
        prevents the auto-commit from prematurely committing a foreign
        transaction (the race that motivated the ``_tx_owner`` design).
        """
        self._require_started()
        # mypy narrowing — _require_started raised above, asserts are load-bearing
        # for type checking only. The captured locals below defeat the
        # concurrent-close race where `self._connection` could be nulled
        # between the guard and the worker reading it.
        assert self._connection is not None
        assert self._executor is not None
        conn = self._connection
        executor = self._executor

        _reject_scalar_string_params(params)
        params_tuple: _SqlParamsTuple = tuple(params)
        current = asyncio.current_task()
        is_tx_owner = self._tx_owner is current and current is not None
        loop = asyncio.get_running_loop()
        try:
            if is_tx_owner:
                # Same task as the active transaction — skip lock (asyncio.Lock
                # is not reentrant) and use the no-commit path.
                await loop.run_in_executor(
                    executor, self._execute_sync_no_commit, conn, sql, params_tuple
                )
            else:
                # Different task (or no active transaction) — acquire the lock
                # so any in-flight transaction completes first, then auto-commit.
                async with self._tx_lock:
                    await loop.run_in_executor(
                        executor, self._execute_sync, conn, sql, params_tuple
                    )
        except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
            raise StorageError("execute failed") from err

    async def executemany(self, sql: str, seq_of_params: Iterable[SqlParams]) -> None:
        """Run a batch write with ``cursor.executemany`` and commit.

        Materializes ``seq_of_params`` on the caller thread BEFORE
        dispatch — generators crossing into the worker thread are a
        footgun sqlite3 does not protect against. Same task-identity
        dispatch contract as :meth:`execute` — see its docstring.
        """
        self._require_started()
        assert self._connection is not None
        assert self._executor is not None
        conn = self._connection
        executor = self._executor

        # Guard the top-level argument too: a bare `str` would yield
        # single-char strings (caught by the per-row guard, but with a
        # misleading message) and a bare `bytes` would yield int values
        # that the per-row guard does NOT catch, causing `tuple(int)` to
        # raise a raw TypeError outside the error-translation net.
        if isinstance(seq_of_params, (str, bytes)):
            raise StorageError(
                "seq_of_params must be an iterable of parameter rows, not a bare str/bytes"
            )

        seq_as_list: list[_SqlParamsTuple] = []
        for row in seq_of_params:
            _reject_scalar_string_params(row)
            seq_as_list.append(tuple(row))
        current = asyncio.current_task()
        is_tx_owner = self._tx_owner is current and current is not None
        loop = asyncio.get_running_loop()
        try:
            if is_tx_owner:
                await loop.run_in_executor(
                    executor, self._executemany_sync_no_commit, conn, sql, seq_as_list
                )
            else:
                async with self._tx_lock:
                    await loop.run_in_executor(
                        executor, self._executemany_sync, conn, sql, seq_as_list
                    )
        except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
            raise StorageError("executemany failed") from err

    async def fetchone(self, sql: str, params: SqlParams = ()) -> sqlite3.Row | None:
        """Run a read; return the first row or ``None``."""
        self._require_started()
        assert self._connection is not None
        assert self._executor is not None
        conn = self._connection
        executor = self._executor

        _reject_scalar_string_params(params)
        params_tuple: _SqlParamsTuple = tuple(params)
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                executor, self._fetchone_sync, conn, sql, params_tuple
            )
        except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
            raise StorageError("fetchone failed") from err

    async def fetchall(self, sql: str, params: SqlParams = ()) -> list[sqlite3.Row]:
        """Run a read; return all rows as a list."""
        self._require_started()
        assert self._connection is not None
        assert self._executor is not None
        conn = self._connection
        executor = self._executor

        _reject_scalar_string_params(params)
        params_tuple: _SqlParamsTuple = tuple(params)
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                executor, self._fetchall_sync, conn, sql, params_tuple
            )
        except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
            raise StorageError("fetchall failed") from err

    # --- private helpers -----------------------------------------------------

    def _require_started(self) -> None:
        if self._connection is None or self._executor is None:
            raise StorageError("storage engine is not started")

    def _open_and_configure_sync(self) -> sqlite3.Connection:
        """Open the connection, apply pragmas, return the configured connection.

        Runs inside the dedicated worker thread. On pragma failure,
        closes the partial connection before re-raising so the caller's
        failure-cleanup never sees a leaked connection object from the
        worker.

        Verifies ``PRAGMA journal_mode = WAL`` actually took effect. On
        unsupported filesystems (some network FSes, certain VFSes)
        SQLite silently falls back to rollback journaling and reports
        the actual mode in the pragma's returned row. WAL semantics are
        load-bearing for crash safety and future backup flows, so a
        silent fallback must surface as an error, not pass.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=5.0,
            detect_types=0,
            isolation_level="DEFERRED",
        )
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("PRAGMA journal_mode = WAL")
            journal_row = cursor.fetchone()
            if journal_row is None or str(journal_row[0]).lower() != "wal":
                # Keep the message filesystem-agnostic; the chained __cause__
                # in StorageError("start failed") carries full detail.
                raise sqlite3.OperationalError("WAL journal mode unsupported on this filesystem")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            raise
        return conn

    def _cleanup_partial_start(
        self,
        local_connection: sqlite3.Connection | None,
        local_executor: ThreadPoolExecutor | None,
    ) -> None:
        """Tear down partially created start() state.

        Swallows secondary failures — the primary exception is what the
        caller needs to see. After this call, ``self._connection`` and
        ``self._executor`` remain ``None`` (never assigned on the failure
        path), so the engine is indistinguishable from a never-started
        instance.

        Thread-affinity: ``local_connection`` was created inside
        ``local_executor``'s worker thread. Closing it must happen on
        that same thread, so we route the close through
        ``executor.submit(...).result(timeout=...)``. A bounded 5s wait
        matches sqlite3.connect's own timeout — the worker should be
        idle or in a short-lived open call at this point.
        """
        if local_connection is not None and local_executor is not None:
            try:
                future = local_executor.submit(local_connection.close)
                future.result(timeout=5.0)
            except (sqlite3.Error, sqlite3.Warning, OSError, TimeoutError, RuntimeError):
                logger.debug(
                    "secondary error during start() cleanup connection close", exc_info=True
                )
        if local_executor is not None:
            try:
                local_executor.shutdown(wait=True, cancel_futures=True)
            except Exception:  # noqa: BLE001 — cleanup path, swallow and continue
                logger.debug("secondary error during start() executor shutdown", exc_info=True)

    @staticmethod
    def _execute_sync(conn: sqlite3.Connection, sql: str, params: _SqlParamsTuple) -> None:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()

    @staticmethod
    def _execute_sync_no_commit(
        conn: sqlite3.Connection, sql: str, params: _SqlParamsTuple
    ) -> None:
        """Inside-transaction variant: execute without per-statement commit."""
        cursor = conn.cursor()
        cursor.execute(sql, params)

    @staticmethod
    def _executemany_sync(
        conn: sqlite3.Connection, sql: str, seq_of_params_list: list[_SqlParamsTuple]
    ) -> None:
        cursor = conn.cursor()
        cursor.executemany(sql, seq_of_params_list)
        conn.commit()

    @staticmethod
    def _executemany_sync_no_commit(
        conn: sqlite3.Connection, sql: str, seq_of_params_list: list[_SqlParamsTuple]
    ) -> None:
        """Inside-transaction variant: executemany without per-statement commit."""
        cursor = conn.cursor()
        cursor.executemany(sql, seq_of_params_list)

    @staticmethod
    def _fetchone_sync(
        conn: sqlite3.Connection, sql: str, params: _SqlParamsTuple
    ) -> sqlite3.Row | None:
        cursor = conn.cursor()
        # sqlite3 stubs type fetch results as Any because the row type depends
        # on runtime `row_factory`. We set `row_factory = sqlite3.Row` in
        # _open_and_configure_sync, so narrowing here is correct. Documented
        # third-party integration boundary per project-context.md:130.
        return cast(sqlite3.Row | None, cursor.execute(sql, params).fetchone())

    @staticmethod
    def _fetchall_sync(
        conn: sqlite3.Connection, sql: str, params: _SqlParamsTuple
    ) -> list[sqlite3.Row]:
        cursor = conn.cursor()
        # See _fetchone_sync — same third-party-boundary rationale.
        return cast(list[sqlite3.Row], cursor.execute(sql, params).fetchall())
