# Story 1.4: SQLite Storage Engine

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing Brain or any persistence,
I want a SQLite storage engine with connection management, a configurable DB path, and startup/shutdown lifecycle,
so that database access is centralized and no other module talks to `sqlite3` directly outside this engine.

## Acceptance Criteria

1. **`src/nova/core/storage/engine.py` defines `SqliteStorageEngine`** — a single class that owns the process's one `sqlite3.Connection` and exposes async query helpers. Public surface (exact signatures):
   - `__init__(self, db_path: Path) -> None` — stores `db_path` (absolute or relative), performs **no** I/O. Constructing the engine is side-effect-free so composition-root assembly (Story 1.10) can happen before `start()`.
   - `async def start(self) -> None` — creates `db_path.parent` if missing (`mkdir(parents=True, exist_ok=True)`), opens the `sqlite3.Connection`, enables WAL mode and foreign keys (see AC #3 for the exact pragmas), sets `row_factory = sqlite3.Row`. If called a second time without an intervening `close()`, raises `StorageError("storage engine already started")`. Must be awaited before any query helper.
   - **Failure cleanup contract for `start()`:** if any step after executor creation fails (mkdir succeeded, executor instantiated, but `_open_and_configure_sync` raises `sqlite3.Error`, OR the mkdir itself fails after we've instantiated state, OR `CancelledError` interrupts mid-flight), `start()` MUST clean up any partially created state before propagating the exception: close the connection if one was opened (`try/except` around `conn.close()` — swallow secondary errors here, the primary failure is what the caller sees), call `self._executor.shutdown(wait=True)` on the executor if created, and set `self._connection = None` and `self._executor = None`. The post-failure invariant is: **the engine is indistinguishable from a never-started engine**. This means `close()` after a failed `start()` is a safe no-op, and a subsequent `start()` on a corrected path succeeds. Implement via a `try/except` that catches `OSError | sqlite3.Error` (and a separate `except BaseException` re-raise-after-cleanup arm for `CancelledError` / `KeyboardInterrupt` — cleanup runs, then re-raise untouched). No worker thread leaks, no half-configured connections.
   - `async def close(self) -> None` — closes the connection and releases the dedicated worker executor. **Idempotent** — calling twice is a safe no-op. Calling `close()` before `start()` is also a safe no-op. Calling `close()` after a failed `start()` is also a safe no-op (by the failure-cleanup contract above, state is already `None`).
   - `async def execute(self, sql: str, params: SqlParams = ()) -> None` — runs a single write (INSERT/UPDATE/DELETE/DDL), then commits. Returns nothing.
   - `async def executemany(self, sql: str, seq_of_params: Iterable[SqlParams]) -> None` — runs a batch write with `cursor.executemany`, then commits.
   - `async def fetchone(self, sql: str, params: SqlParams = ()) -> sqlite3.Row | None` — runs a read, returns a single row (with `sqlite3.Row` factory so callers can do `row["session_id"]`) or `None` if the query matched nothing.
   - `async def fetchall(self, sql: str, params: SqlParams = ()) -> list[sqlite3.Row]` — runs a read, returns all rows as a `list[sqlite3.Row]`. Empty list if the query matched nothing.
   - `async def __aenter__(self) -> SqliteStorageEngine: await self.start(); return self`
   - `async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None: await self.close()`
   - **`SqlParams` is a module-level PEP 695 type alias:** `type SqlParams = Sequence[str | int | float | bytes | None]`. This is the strict-mypy-clean parameter type; sqlite3 itself accepts a broader `Any` via its stubs, but we narrow to the five storable types to keep the domain boundary typed. A `tuple[str, int]` or `tuple[int, ...]` or `list[str | None]` all satisfy `SqlParams` via structural subtyping. The companion private alias `type _SqlParamsTuple = tuple[str | int | float | bytes | None, ...]` is used internally after `tuple(params)` coercion. **Do NOT use `Any`** — forbidden by project-context.md line 45. **Do NOT use `typing.TypeAlias`** — ruff `UP040` enforces PEP 695 on py312 and this module establishes the project-wide convention (project-context.md line 36).
   - **Runtime guard against bare `str` / `bytes` params.** Because `SqlParams = Sequence[str | int | float | bytes | None]` structurally accepts a bare `str` (it's a `Sequence[str]`) or bare `bytes` (it's a `Sequence[int]`), a caller writing `await engine.execute("WHERE c = ?", "abc")` would type-check, then sqlite3 would iterate the string into three single-char bindings and surface a confusing `ProgrammingError`. Module-level helper `_reject_scalar_string_params(params: SqlParams) -> None` — called at the top of every public helper and once per row in `executemany` — rejects ONLY bare `str` and bare `bytes` with a clear `StorageError("params must be a tuple or list of scalars, not a bare str/bytes")`. Every other `Sequence` shape (tuple, list, range, custom sequences) passes through untouched. The guard's message is schema-level, not data-level — no user params / SQL / row contents appear in the message, so the opaque-message contract (AC #5) is preserved. A tuple containing `bytes` values (e.g., `(b"blob-data",)` for a BLOB column) is **valid** — the guard only rejects the bare-scalar-as-row case.
   - **`executemany` additionally guards its top-level `seq_of_params` argument** with a separate `isinstance(seq_of_params, (str, bytes))` check BEFORE iteration. Reason: the per-row guard catches bare `str` input indirectly (each iterated char is a `str`, per-row guard fires), but **does NOT catch bare `bytes`** — iteration yields `int` values that pass the per-row guard, and `tuple(int)` then raises `TypeError` outside the engine's error-translation net. The top-level guard closes that hole with message `"seq_of_params must be an iterable of parameter rows, not a bare str/bytes"`. Locked by `test_executemany_top_level_bare_str_or_bytes_rejected` (parametrized over both `"abc"` and `b"abc"`).

2. **Blocking I/O isolation via a dedicated single-worker executor** — the engine owns a `concurrent.futures.ThreadPoolExecutor(max_workers=1)` and routes every `sqlite3` call through it with `loop.run_in_executor(self._executor, ...)`. This is load-bearing for three reasons:
   - **sqlite3 thread affinity.** `sqlite3.Connection` objects are tied to the thread that created them by default (`check_same_thread=True`). The connection MUST be created inside the single worker and every subsequent call MUST run in that same worker. Without the dedicated executor, `asyncio.to_thread` uses the **default** pool, which has multiple workers — `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` would fire on the second call.
   - **Serialization.** A one-worker pool naturally serializes all DB operations. No `asyncio.Lock`, no `check_same_thread=False` footguns. Single-writer guarantee is enforced by the executor shape, not by runtime checks.
   - **Clean shutdown.** `close()` awaits `run_in_executor(self._executor, self._connection.close)` to close the connection on the worker, THEN calls `self._executor.shutdown(wait=True)` directly on the event loop thread (NOT inside another `run_in_executor`). The executor is already idle at that point (the close call just completed), so `shutdown(wait=True)` returns essentially instantly. No dangling threads, no warnings.
   - **`shutdown(wait=True)` is a blocking call on the event loop.** At process shutdown (Story 1.10 composition-root `NovaApp.close()` flow) the loop is about to exit anyway, so the brief block is harmless. **`close()` MUST NOT be called during active session operation** — it would stall the loop for as long as the worker takes to finish any queued DB call. This constraint is pinned in the `SqliteStorageEngine` class docstring so Story 1.10 and any future lifecycle story obeys it: "close() is intended for process-shutdown; calling it mid-session blocks the event loop until the current worker task drains."
   - **Do NOT use `asyncio.to_thread`** anywhere in this module — it targets the shared default pool and breaks same-thread invariant. Use `loop.run_in_executor(self._executor, func, *args)` exclusively. Get the loop via `asyncio.get_running_loop()` inside each async method (do NOT cache a loop reference in `__init__` — the engine might be constructed outside a running loop in tests and in the composition root).

3. **Pragmas applied inside `start()` after connection open, before return:**
   - `PRAGMA journal_mode = WAL;` — write-ahead logging. Confirm via `row = cursor.execute("PRAGMA journal_mode").fetchone(); assert row[0].lower() == "wal"`. WAL is load-bearing for the "readers don't block writers" contract and for durable backups during migration (Story 1.5).
   - `PRAGMA foreign_keys = ON;` — SQLite defaults to OFF per historical compat. T1 schema has FK constraints (`workspace_snapshots.session_id REFERENCES sessions(id)`, etc. — architecture.md:551). Enabling FK enforcement here means every future migration/query honors the constraint.
   - `PRAGMA synchronous = NORMAL;` — appropriate for WAL mode (sqlite docs recommend NORMAL, not FULL, for WAL — faster, still crash-safe for WAL journal contents). Pinned here so no future story raises it to FULL "for safety" and silently degrades write throughput.
   - Do NOT set `PRAGMA busy_timeout` in T1 — single-writer, dedicated executor, no contention. Add later if Story 5.5 (corruption recovery) surfaces a need.
   - Do NOT set `PRAGMA temp_store`, `cache_size`, `mmap_size`, or any tuning pragma. Defaults are fine for the T1 budget (<100MB DB, NFR23).

4. **Connection construction arguments — exact:** `sqlite3.connect(str(self._db_path), timeout=5.0, detect_types=0, isolation_level="DEFERRED")`.
   - `str(self._db_path)` — sqlite3 accepts a `str | PathLike[str]` in 3.12, but being explicit avoids a mypy strict noise around path types.
   - `timeout=5.0` — how long to wait for a write lock before `sqlite3.OperationalError("database is locked")`. 5s is generous for single-writer T1; if the dedicated executor is doing its job, this should never fire.
   - `detect_types=0` — do NOT auto-convert columns. All type parsing (timestamps, JSON) lives in the calling adapter, not the engine. Turning this on would silently mangle TEXT columns that happen to parse as dates.
   - `isolation_level="DEFERRED"` — default, made explicit. Python's `sqlite3` auto-BEGINs on write and requires explicit `commit()`. The engine's `execute`/`executemany` wrappers call `self._connection.commit()` after each write to honor this.
   - Do NOT pass `check_same_thread=False`. The dedicated single-worker executor means the connection stays on one thread; `check_same_thread=True` (default) catches any accidental misuse instead of silently allowing it.
   - Do NOT pass `uri=True`. T1 uses plain file paths only; URI mode adds ambiguity (`:memory:` vs `file::memory:?cache=shared`) without benefit.

5. **Error translation — every `sqlite3.Error` becomes a `StorageError`** at the engine boundary. Pattern (pinned):
   ```python
   try:
       await loop.run_in_executor(self._executor, self._execute_sync, sql, params)
   except sqlite3.Error as err:
       raise StorageError("execute failed") from err
   ```
   - Use a **generic, opaque message** ("execute failed", "fetchone failed", "fetchall failed", "executemany failed", "start failed", "close failed"). **Do NOT include the raw SQL, params, or row contents** in the exception message — project-context.md:174 ("No sensitive content in exception messages"). Params may carry user memory / seed text / context snippets; SQL bodies may reveal table/column names that leak schema to logs. The underlying `sqlite3.Error` is chained via `from err` so the full technical detail lives in the traceback (file log only, never terminal) but never in the top-level message.
   - **Catch `sqlite3.Error`, NOT `Exception`.** The former is the documented base class for every sqlite3 exception. Catching `Exception` would swallow `asyncio.CancelledError` (already `BaseException`, so safe anyway), `KeyboardInterrupt`, programmer errors like `AttributeError`, which violates the "broad exception catching only at top-level boundaries" rule (project-context.md:51).
   - **Wrap every public coroutine**, including `start` and `close`. A mkdir permission error (`OSError`, not `sqlite3.Error`) inside `start()` also becomes `StorageError("start failed") from err` — same opaque-message, same chaining contract. **Catch `OSError | sqlite3.Error`** in `start()` specifically; `close()` catches `sqlite3.Error | OSError` symmetrically.
   - **Never leak `sqlite3.Error` past the engine boundary.** No test should be able to catch a raw `sqlite3.OperationalError` from an engine method.
   - **Exception to the opaque-message rule:** the bare-str/bytes guard error raised by `_reject_scalar_string_params` (see AC #1) uses the literal message `"params must be a tuple or list of scalars, not a bare str/bytes"`. This is a **schema-level misuse signal**, not a runtime-data error — the message contains no user params, SQL, or row contents, so it does NOT violate the opaque-message contract. The guard fires BEFORE the `try/except` that translates sqlite3 errors; it raises `StorageError` directly with no chained `__cause__`.

6. **Using the engine after `close()` raises `StorageError`** — every query helper checks `self._connection is None` (or an equivalent closed-flag) and raises `StorageError("storage engine is not started")` before touching the executor. Same check rejects query helpers called before `start()`. Locked by test.

7. **Imports in `core/storage/engine.py`** — exact list, nothing extra. This is the FIRST module in `core/` allowed to import `sqlite3`; the AST isolation test must be extended to allow it (see AC #10).
   - `from __future__ import annotations`
   - `import asyncio`
   - `import contextlib` — used for `contextlib.suppress(sqlite3.Error)` in the pragma-failure cleanup path inside `_open_and_configure_sync` (ruff `SIM105` enforces this shape over `try/except: pass`).
   - `import logging`
   - `import sqlite3`
   - `from collections.abc import Iterable, Sequence`
   - `from concurrent.futures import ThreadPoolExecutor`
   - `from pathlib import Path`
   - `from types import TracebackType`
   - `from typing import cast` — used at the sqlite3 row-factory third-party integration boundary in `_fetchone_sync` / `_fetchall_sync`. sqlite3's stubs type `cursor.fetchone()` / `.fetchall()` as `Any` because the return type depends on runtime `row_factory`; since we set `row_factory = sqlite3.Row` in `_open_and_configure_sync`, narrowing with `cast(sqlite3.Row | None, ...)` and `cast(list[sqlite3.Row], ...)` is correct. This is the exact documented-integration-boundary case that project-context.md:130 permits for `cast()`. Two narrow uses, each with an inline rationale comment.
   - `from nova.core.exceptions import StorageError`
   - **Forbidden** (same denylist as Stories 1.2/1.3 minus `sqlite3`): `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32api`, `win32gui`, `win32com`, `win32con`, `rich`, `yaml`. AST-enforced via the extended `test_core_isolation.py` (AC #10).
   - **No `from typing import Any`.** `SqlParams` covers the parameter shape without `Any`. mypy strict is clean.
   - **No `from nova.adapters.*`, `from nova.systems.*`, `from nova.ports.*`.** This module is infrastructure under `core/`; it is consumed by later adapters (e.g., `adapters/sqlite/brain.py`, Story 3.1+) and the migration runner (Story 1.5), never the other way around.

8. **`core/storage/__init__.py` re-exports `SqliteStorageEngine`:**
   - Replace the placeholder docstring with a one-line module docstring.
   - `from nova.core.storage.engine import SqliteStorageEngine`
   - `__all__: list[str] = ["SqliteStorageEngine"]`
   - Absolute imports only (Story 1.2/1.3 carry-forward). `from .engine import ...` is forbidden.

9. **`core/__init__.py` re-exports `SqliteStorageEngine`** alongside the Story 1.2/1.3 names:
   - Add `from nova.core.storage import SqliteStorageEngine` (goes through the sub-package re-export, not directly from `engine.py` — keeps the public import path `nova.core.SqliteStorageEngine` stable even if internal module names change).
   - Extend `__all__` with `"SqliteStorageEngine"`, keep the list alphabetized (final count: 23 names = 12 from Story 1.2 + 10 from Story 1.3 + 1 from this story).
   - Preserve the existing module docstring and all prior imports.

10. **Extend `tests/unit/core/test_core_isolation.py`** to guard `core/storage/engine.py`:
    - Import the module: `import nova.core.storage.engine as storage_engine_module`.
    - Add a dedicated forbidden-set frozenset:
      ```python
      STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES - {"sqlite3"}
      ```
      — sqlite3 is the one adapter module the engine legitimately imports. Every other adapter module remains forbidden here too.
    - Add a dedicated allowlist frozenset:
      ```python
      STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset({
          "__future__", "asyncio", "collections", "concurrent", "contextlib",
          "logging", "nova", "pathlib", "sqlite3", "types", "typing",
      })
      ```
      (`contextlib` covers `suppress`; `typing` covers `cast` — both are needed per AC #7.)
    - Add `storage_engine_module` to the parametrize list of **`test_no_relative_imports`** and **`test_no_dynamic_imports_of_forbidden_modules`** (the generic checks).
    - **Do NOT** add it to `test_no_forbidden_imports` — that test uses the global `FORBIDDEN_TOPLEVEL_MODULES` which still contains `sqlite3`; adding the engine there would fail. Instead, add a new test `test_storage_engine_forbidden_imports_minus_sqlite3` parametrized only over `storage_engine_module` and using `STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES`.
    - **Do NOT** add it to `test_imports_within_allowlist` — that is the tight `{"enum", "__future__"}` allowlist for exceptions/types. Add a new test `test_storage_engine_imports_within_allowlist` parametrized only over `storage_engine_module` and using `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES`.
    - Add a new test `test_storage_engine_does_not_import_nova_adapters_or_systems` mirroring the events-specific `test_events_does_not_import_nova_adapters_or_systems` (walks full dotted paths via `_has_forbidden_prefix(name, FORBIDDEN_NOVA_PREFIXES)` — reuses the existing helpers unchanged).
    - Add a new test `test_storage_engine_does_not_dynamically_import_nova_adapters_or_systems` mirroring the events-specific dynamic-import test, again reusing `_dynamic_import_full_targets` unchanged.
    - Net additions: 1 import, 2 new frozensets, 4 new parametrized tests, and 2 parametrize-list extensions. No other tests in the file change.

11. **Unit tests in `tests/unit/core/test_storage_engine.py`** verify the contract. Use `tmp_path` (pytest fixture) for DB paths — a per-test isolated scratch directory that pytest cleans up automatically. **Do NOT touch `%LOCALAPPDATA%/nova/`** from tests (project-context.md:158). All test functions are `async def ... -> None` with a mypy-strict-clean signature (per Story 1.3 carry-forward).
    - **Constructor is side-effect-free** — `SqliteStorageEngine(tmp_path / "test.db")` does not create the file on disk. Assert `not db_path.exists()` right after construction.
    - **`start()` creates parent directories** — `db_path = tmp_path / "nested" / "deep" / "test.db"`; after `await engine.start()`, assert all parents exist and `db_path.exists()` is True.
    - **`start()` creates the DB file if missing** — the classic "first boot" case.
    - **`start()` opens an existing DB file without clobbering** — pre-create `test.db` via `sqlite3.connect`, run a DDL+INSERT, close; then start the engine on the same path and assert the row is still readable via `fetchone`. Tests the "restart preserves state" contract.
    - **`start()` enables WAL mode** — after start, execute `PRAGMA journal_mode` via the engine (`fetchone`); assert the returned row's first value lower-cases to `"wal"`.
    - **`start()` enables foreign keys** — `PRAGMA foreign_keys` returns `1` via fetchone.
    - **`start()` sets synchronous = NORMAL** — `PRAGMA synchronous` returns `1` (the integer code for NORMAL; FULL is `2`, OFF is `0`).
    - **`start()` called twice raises `StorageError`** — second `await engine.start()` raises `StorageError` with message `"storage engine already started"`.
    - **`execute()` runs a DDL statement then a parameterized INSERT** — create a trivial one-column table, insert a parameterized value, read it back via `fetchone`. Proves the round-trip.
    - **`executemany()` inserts a batch** — create a table, call `executemany` with `[("a",), ("b",), ("c",)]`, assert `fetchall` returns three rows in insertion order.
    - **`fetchone()` returns None on empty match** — query a non-existent row; assert `None` returned (not an empty `sqlite3.Row`).
    - **`fetchone()` returns a `sqlite3.Row` with keyed access** — after insert, `row = await engine.fetchone(...)`; assert `row["col_name"] == expected` (proves `row_factory = sqlite3.Row` is wired).
    - **`fetchall()` returns empty list on empty match** — `[]`, not `None`.
    - **`close()` is idempotent** — `await engine.close()` then `await engine.close()` again; second call is a no-op (no exception).
    - **`close()` before `start()` is a no-op** — `await engine.close()` on a freshly constructed (never-started) engine does not raise.
    - **Using the engine after `close()` raises `StorageError`** — `await engine.start(); await engine.close(); with pytest.raises(StorageError, match="not started"): await engine.fetchone(...)`. Parametrize over all four query helpers.
    - **Using the engine before `start()` raises `StorageError`** — same "not started" message. Parametrize over `execute`, `executemany`, `fetchone`, `fetchall`.
    - **`sqlite3.Error` is translated to `StorageError`** — run an intentionally broken SQL statement (`"SELECT bogus FROM nonexistent"`) against a started engine; assert `StorageError` is raised, **assert `isinstance(caught.__cause__, sqlite3.Error)`** to pin the `from err` chaining contract, and **assert the caught exception's message is the generic opaque string** (`str(caught) == "fetchall failed"` etc.), NOT a string containing the SQL text or table name. Parametrize over each of the four query helpers.
    - **`StorageError` from failed `start()`** — construct an engine pointing at an unwritable path (e.g., `tmp_path / "file_that_is_a_file" / "nested.db"` where `file_that_is_a_file` already exists as a regular file). `start()` raises `StorageError("start failed")` with `isinstance(err.__cause__, (OSError, sqlite3.Error))`.
    - **Failed-`start()` aftermath recovers cleanly** — after a `start()` that raised `StorageError` (unwritable path as above), assert: (a) `await engine.close()` is a safe no-op (no exception); (b) calling any query helper raises `StorageError("storage engine is not started")` (same as never-started state); (c) after pointing the engine at a valid path via constructing a NEW engine instance with `tmp_path / "valid.db"`, `start()` succeeds normally on the fresh instance. **Additionally**, to lock the "same instance can retry" contract: construct ONE engine, `start()` it against an unwritable path (fails), then mutate `engine._db_path` to a valid `tmp_path / "retry.db"` (private-attribute poke, acceptable in tests only — document with a short comment), call `start()` again — assert it succeeds. Proves the engine is fully recovered, no leaked executor, no half-initialized connection. (The private-attribute poke is a test-only hack to exercise the recovery contract without a public `set_db_path` method; production code constructs a fresh engine instead.)
    - **Async context manager works** — `async with SqliteStorageEngine(path) as engine: await engine.execute(...)`. Assert the DB file was created inside the block and the engine raises "not started" after the block exits.
    - **Concurrent `execute` calls serialize correctly** — spawn two coroutines that each INSERT a row into the same table, `await asyncio.gather(...)`; assert both rows are present and no `sqlite3.ProgrammingError` about thread affinity fired. Proves the single-worker executor pattern holds under concurrent coroutines.
    - **Opaque exception message — no SQL body** — run `await engine.execute("INSERT INTO x (col) VALUES (?)", ("secret-token-xyz",))` against a DB with no table `x`. Catch the `StorageError`. Assert `"secret-token-xyz"` does NOT appear in `str(err)` AND the SQL body (`INSERT INTO x`) does NOT appear. This locks the "no sensitive content in exception messages" rule.
    - **Bare-str/bytes guard tests** (per AC #1 and AC #5): parametrize three tests over the four public helpers asserting (a) bare `str` row rejected with `StorageError("... bare str/bytes")`, (b) bare `bytes` row rejected identically, (c) `executemany` mid-batch bare row rejected. Plus one positive test: a tuple containing `bytes` values (e.g., `(b"binary-data",)`) must succeed — proves the guard is scoped to bare-scalar-as-row, not bytes-inside-tuple.
    - **Test budget:** 20–22 tests (post-review: 37 tests after adding cancellation/race/WAL-fallback/guard coverage). Total runtime under 2s. No test touches network, Win32, or `%LOCALAPPDATA%`.

12. **Quality gates pass clean**: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0. mypy strict must succeed on `engine.py`, the storage `__init__.py`, the extended `core/__init__.py`, the new `test_storage_engine.py`, and the extended `test_core_isolation.py`. No `Any`, no `# type: ignore` in production code. Tests follow Story 1.3's precedent: zero `# type: ignore` unless a specific test case genuinely needs it with a narrow comment.

13. **Repo tree stays clean** after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm`, or `*.egg-info/` staged by `git status`. Same standard as Stories 1.1–1.3. Confirm `.gitignore` already covers `*.db` (Story 1.1 D5); **if it does not yet ignore `*.db-wal` / `*.db-shm` (WAL mode side-files), add those two globs to `.gitignore` in this story** — they are created on first-use of WAL mode and would leak into `git status` from any test run that forgot `tmp_path`.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/storage/engine.py` — class skeleton + lifecycle** (AC: #1, #2, #3, #4, #7)
  - [x] Module docstring: purpose ("SQLite storage engine — owns the process's single sqlite3 connection, routes all DB calls through a dedicated single-worker thread for asyncio isolation and thread-affinity correctness"), cites architecture.md:1390 and this story for the pragmas/executor decisions.
  - [x] `from __future__ import annotations` at top.
  - [x] Exact import list per AC #7 — with documented `typing.cast` addition for sqlite3 Row-type narrowing and `contextlib` for `suppress`. ruff `F401`/`UP035` pass clean.
  - [x] Module-level `logger = logging.getLogger("nova.core.storage.engine")` (matches Story 1.3 naming pattern).
  - [x] Module-level `type SqlParams = Sequence[str | int | float | bytes | None]`. **Spec divergence:** used PEP 695 `type` keyword instead of `TypeAlias` — ruff UP040 enforces PEP 695 on py312 target. Documented as project-wide convention decision in completion notes.
  - [x] `class SqliteStorageEngine:` with class docstring explaining: the executor pattern, pragma set, "use one engine per process" contract, and the close() blocking-shutdown caveat verbatim: "close() is intended for process-shutdown; calling it mid-session blocks the event loop until the current worker task drains."
  - [x] `__init__(self, db_path: Path) -> None` stores `self._db_path: Path = db_path`, `self._connection: sqlite3.Connection | None = None`, `self._executor: ThreadPoolExecutor | None = None`. No I/O.
  - [x] `async def start(self) -> None` — raises `StorageError("storage engine already started")` if `self._connection is not None`. mkdir → local `ThreadPoolExecutor(max_workers=1, thread_name_prefix="nova-sqlite")` → `run_in_executor(local_executor, self._open_and_configure_sync)` → only AFTER success assigns `self._executor` and `self._connection`. Load-bearing ordering for failure-cleanup.
  - [x] **Failure-cleanup arm for `start()` per AC #1:** `except (OSError, sqlite3.Error) as err: self._cleanup_partial_start(...); raise StorageError("start failed") from err`. Separate `except BaseException: self._cleanup_partial_start(...); raise` for CancelledError / KeyboardInterrupt propagation. Post-failure invariant `self._connection is None and self._executor is None` verified by the aftermath test.
  - [x] `def _open_and_configure_sync(self) -> sqlite3.Connection` — `sqlite3.connect(str(db_path), timeout=5.0, detect_types=0, isolation_level="DEFERRED")`, sets `row_factory = sqlite3.Row`, applies the three pragmas. On pragma failure, `contextlib.suppress(sqlite3.Error): conn.close()` before re-raise.
  - [x] `async def close(self) -> None` — idempotent no-op on fully-None state. Otherwise captures locals, nulls state FIRST, then awaits `run_in_executor(executor, conn.close)` and `executor.shutdown(wait=True)`. Wraps with `except (sqlite3.Error, OSError) as err: raise StorageError("close failed") from err`.
  - [x] `async def __aenter__` / `__aexit__` per AC #1. `__aexit__` returns `None`.

- [x] **Task 2: Query helpers — execute / executemany / fetchone / fetchall** (AC: #1, #5, #6)
  - [x] Private guard `def _require_started(self) -> None` raises `StorageError("storage engine is not started")` if either attr is None. Called at the top of every public query helper.
  - [x] Load-bearing mypy narrowing pattern — inline comment `# mypy narrowing — _require_started raised above, asserts are load-bearing` present on the first assert of `execute`. Subsequent helpers repeat the pattern without the comment (one docstring-level reference suffices).
  - [x] `async def execute(...)`: guard → asserts → `params_tuple = tuple(params)` (caller thread) → `run_in_executor(self._executor, self._execute_sync, sql, params_tuple)` → `except sqlite3.Error: raise StorageError("execute failed") from err`.
  - [x] `def _execute_sync(...) -> None`: cursor → `cursor.execute(sql, params)` → `commit()`. Receives concrete tuple.
  - [x] `async def executemany(...)`: **materializes on caller thread** — `seq_as_list: list[_SqlParamsTuple] = [tuple(row) for row in seq_of_params]` BEFORE dispatch. Locked by `test_executemany_accepts_generator_materialized_on_caller`.
  - [x] `def _executemany_sync(...)`: receives pre-materialized `list[_SqlParamsTuple]`, no iteration of caller input.
  - [x] `async def fetchone(...)` / `_fetchone_sync` → `sqlite3.Row | None`. `typing.cast` used on sqlite3 stub boundary (documented third-party rationale per project-context.md:130).
  - [x] `async def fetchall(...)` / `_fetchall_sync` → `list[sqlite3.Row]`. Same `cast` rationale.
  - [x] **Invariant upheld:** caller-thread coercion happens before `run_in_executor` for all four helpers.

- [x] **Task 3: `core/storage/__init__.py` re-export + `core/__init__.py` update** (AC: #8, #9)
  - [x] Replaced Story 1.1 placeholder docstring with canonical one-liner.
  - [x] `from nova.core.storage.engine import SqliteStorageEngine` + `__all__: list[str] = ["SqliteStorageEngine"]` in `core/storage/__init__.py`.
  - [x] `core/__init__.py` imports via the sub-package (`from nova.core.storage import SqliteStorageEngine`), not directly from `engine`.
  - [x] `__all__` extended to 23 names, alphabetized.
  - [x] Module docstring preserved.

- [x] **Task 4: Extend `tests/unit/core/test_core_isolation.py` to cover storage engine** (AC: #10)
  - [x] `import nova.core.storage.engine as storage_engine_module` added alphabetized.
  - [x] `STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES = FORBIDDEN_TOPLEVEL_MODULES - {"sqlite3"}` declared.
  - [x] `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` declared — 11 modules (added `contextlib` for `suppress`, `typing` for `cast`).
  - [x] Extended parametrize lists of `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules`. The dynamic-imports test uses `STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES` when `module is storage_engine_module` so the sqlite3 carve-out applies.
  - [x] Added `test_storage_engine_forbidden_imports_minus_sqlite3`, `test_storage_engine_imports_within_allowlist`, `test_storage_engine_does_not_import_nova_adapters_or_systems`, `test_storage_engine_does_not_dynamically_import_nova_adapters_or_systems`.
  - [x] `storage_engine_module` NOT added to `test_no_forbidden_imports`, `test_imports_within_allowlist`, or `test_enum_imports_use_public_symbols_only` (sqlite3 carve-out required different framing).

- [x] **Task 5: Author `tests/unit/core/test_storage_engine.py`** (AC: #11)
  - [x] File header: canonical module docstring.
  - [x] `from __future__ import annotations` + minimal imports (`asyncio`, `sqlite3`, `collections.abc.{Awaitable,Callable}`, `pathlib.Path`, `pytest`, `StorageError`, `SqliteStorageEngine`).
  - [x] `tests/conftest.py` untouched.
  - [x] Implemented 24 test functions (expanded past the 20–22 baseline to cover the parametrized four-helper cases cleanly and the generator-materialization locker).
  - [x] Concurrent-insert test via `asyncio.gather`.
  - [x] Opaque-message test: `"secret-token-xyz" not in str(err)` AND `"INSERT INTO" not in str(err)` AND `str(err) == "execute failed"`.

- [x] **Task 6: `.gitignore` hygiene + final verify** (AC: #13)
  - [x] `.gitignore` already contains `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `*.sqlite`, `*.sqlite3` (Story 1.1 left this already complete — no changes needed).
  - [x] Full verify command: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0. Output: "All checks passed!" / "33 files already formatted" / "Success: no issues found in 33 source files" / "287 passed in 0.93s".
  - [x] `git status` confirms no stray `*.db` / `*.db-wal` / `*.db-shm` / cache files — only the intentional source/test/doc changes.

## Dev Notes

### Story Type: Foundational infrastructure — the SQLite boundary for the whole product

This story produces the **single place** in the application that talks to `sqlite3`. Every later storage-touching story — Story 1.5 (migration runner), Story 1.6 (config loader does NOT touch sqlite, but reads the path from NovaConfig that the engine consumes), Story 1.8 (audit logger writes via the engine), Story 3.1+ (Brain session persistence), Story 5.x (deletion, transparency, backup/restore) — consumes `SqliteStorageEngine` as a constructor-injected dependency. The ports-and-adapters contract still holds: Brain's port is domain-typed, its adapter (`adapters/sqlite/brain.py`, Story 3.x) uses the engine. The engine itself lives in `core/` because it is infrastructure, not a Brain adapter — it knows nothing about sessions, memory, or audit. It knows only connections, pragmas, and SQL round-trips.

### Scope guard (hard stop)

- **Do NOT create any tables, run any migrations, or author any schema SQL.** Story 1.5 owns the migration runner and `001_initial_schema.py`. If you find yourself typing `CREATE TABLE sessions (...)`, stop — you are in the wrong story.
- **Do NOT add `run_migrations()` method to the engine.** The architecture sketch at line 1067 shows `await storage.run_migrations()`; that lives in Story 1.5, either on the migration runner (preferred — keeps engine free of migration concepts) or as a thin delegating method on the engine added in 1.5. This story is connection management only.
- **Do NOT wire the engine into `app.py`.** Composition is Story 1.10. `src/nova/app.py` stays as its Story 1.1 placeholder.
- **Do NOT add a `backup()` method.** Backup-before-migrate is the migration runner's job (Story 1.5). Separate user-facing backup/restore is Story 5.6.
- **Do NOT add `begin()/commit()/rollback()` or transaction context managers.** T1 auto-commits per write. Brain's multi-statement operations (Story 3.1+) can add explicit transactions if needed — either by extending the engine then, or by wrapping through the adapter. Resist pre-emptive transaction plumbing.
- **Do NOT add connection pooling, multiple connections, or a writer/reader split.** Single-writer, single-connection, single-process. architecture.md line 23 ("Single-user, single-process session ownership — no concurrent writers") and line 214 (single asyncio event loop) are the invariants.
- **Do NOT add `check_same_thread=False`.** The dedicated single-worker executor makes it unnecessary and the default is a correctness guardrail.
- **Do NOT use `aiosqlite`.** Epics line 717 explicitly pins stdlib `sqlite3` + `asyncio.to_thread` pattern — overridden here to a dedicated `ThreadPoolExecutor(max_workers=1)` for thread-affinity correctness. **`aiosqlite` would add a dependency** and its own executor pattern, duplicating ours. `pyproject.toml` stays on stdlib-only DB.
- **Do NOT modify `pyproject.toml`.** No new dependencies. Stdlib is sufficient.
- **Do NOT modify `tests/conftest.py`.** Each test constructs its own engine. Shared fixtures land in later stories.
- **Do NOT add domain types (Session, MemoryItem, etc.) or type-to-row conversion.** The engine returns `sqlite3.Row` objects; callers convert. Domain types live in `systems/brain/models.py` per Story 3.1+.
- **If `engine.py` grows past ~200 lines of production code, you are over-building.** The class is deliberately narrow: lifecycle + four query helpers + executor management. No cleverness.

### Critical constraints and gotchas

- **Architecture.md line 1170–1174 shows migration files using `aiosqlite.Connection` — epics.md line 717 overrides that** to stdlib `sqlite3` + async wrapping. This story pins the decision via the dedicated executor. Document the divergence in the `SqliteStorageEngine` class docstring (cite epics.md:717). Story 1.5 will apply the same pattern to the migration runner.
- **`sqlite3.Connection` thread affinity is the load-bearing correctness constraint.** A connection created on thread A cannot be used on thread B when `check_same_thread=True` (the default). `asyncio.to_thread` uses the **default** `ThreadPoolExecutor` which has `min(32, os.cpu_count() + 4)` workers by default — every await could run on a different worker. We MUST own our pool with `max_workers=1`. This is non-negotiable and locked by the concurrent-insert test.
- **`PRAGMA journal_mode = WAL` is a persistent setting** — once set, it is stored in the DB header and survives reconnects. Re-running the pragma on an already-WAL database is a safe no-op. The engine's `start()` runs it every time, unconditionally; simpler than checking first.
- **WAL mode creates two sidecar files** next to the DB: `<db>-wal` and `<db>-shm`. Both must be in `.gitignore` (AC #13). For `tmp_path` tests these are auto-cleaned; for the `%LOCALAPPDATA%/nova/` runtime path they live alongside `nova.db` and must be included in any user-facing backup (Story 5.6 concern, not this story's, but document it in the class docstring so future Sayuj doesn't forget).
- **`PRAGMA foreign_keys = ON` is NOT persistent** — it resets to OFF on every new connection. The engine's `start()` MUST run it every time. The T1 schema (Story 1.5) has FK constraints; without this pragma they would be silently ignored.
- **`sqlite3.Row` is a tuple-like + dict-like hybrid.** `row[0]` and `row["col_name"]` both work. It is NOT a `dict` — `row.get()` does not exist, `dict(row)` works for conversion. Test the keyed-access path explicitly so a future `row_factory = None` regression breaks the test.
- **`cursor.execute(...).fetchone()` is the shortest safe form.** Do NOT create a cursor per-statement outside of these sync wrappers; do NOT cache cursor objects across async boundaries (thread affinity again). A fresh cursor per sync call is free; sqlite3 caches compiled statements at the connection level automatically.
- **`detect_types=0` (explicit)** — if we accidentally set `detect_types=sqlite3.PARSE_DECLTYPES`, sqlite3 would try to parse TEXT columns declared as `TIMESTAMP` into `datetime` objects, which is EXACTLY the thing we do not want (project-context.md:46: "External payloads ... must be parsed into typed DTOs before entering system logic"). Keep it at 0; callers parse timestamps themselves.
- **Generators across `run_in_executor` are a footgun.** `executemany(sql, (row for row in source))` — if the generator is not materialized before dispatch, it gets evaluated on the worker thread but may have closed over caller-thread state. Always `list(seq_of_params)` before crossing.
- **`ThreadPoolExecutor.shutdown(wait=True)` blocks until the worker finishes.** `close()` must itself run on the event loop, not the worker, so the pattern is: `run_in_executor(self._executor, self._connection.close)` (dispatches the close to the worker, awaits it), THEN call `self._executor.shutdown(wait=True)` (blocks the loop briefly to reap the worker). This is fine for T1 — shutdown happens once at process exit and the worker is quiescent by then.
- **`asyncio.CancelledError` during `start()` or `close()`** — if the coroutine is cancelled mid-open, the executor may still be alive with a pending `sqlite3.connect` call. Do NOT try to tear down in a `finally` block — that would re-await and could deadlock. The `CancelledError` propagates (per project-context.md:47) and the composition root / CLI signal handler is responsible for a clean shutdown. The engine's contract is: "if `start()` raises, you have no engine and must not call other methods." This is already enforced by `self._connection is None` check.
- **`StorageError`'s `cause=` slot vs `from err`:** Story 1.2 documented that `raise StorageError("msg") from err` is the correct chaining form; `cause=err` without `from` does NOT populate `__cause__`. This story uses `from err` exclusively. Tests assert `isinstance(caught.__cause__, sqlite3.Error)` — this works because `from err` populates `__cause__`. Do NOT use `cause=err` here; the Story 1.2 carry-forward is strict.
- **Opaque exception messages — enforcement.** The test `test_execute_error_opaque_message` asserts `"secret-token-xyz" not in str(err)` AND `"INSERT INTO" not in str(err)`. If a future contributor "helpfully" adds the SQL to the message for debuggability, this test fails loudly. The technical detail lives in the chained `__cause__` (accessible via logger + traceback), not in the top-level message string.

### Repo shape at time of this story

After Stories 1.0, 1.1, 1.2, 1.3 the repo contains:

- `src/nova/core/__init__.py` (re-exports 22 names: 6 exceptions + 6 enums + 10 event-bus names)
- `src/nova/core/events.py` (Story 1.3)
- `src/nova/core/exceptions.py` (Story 1.2)
- `src/nova/core/types.py` (Story 1.2)
- `src/nova/core/storage/__init__.py` — Story 1.1 placeholder ("""SQLite storage engine and migration runner. Implementation in Stories 1.4-1.5.""")
- `src/nova/core/storage/migrations/__init__.py` — empty, Story 1.5 fills
- `src/nova/{app,cli}.py` (Story 1.1 placeholders, NOT touched here)
- `src/nova/adapters/*/__init__.py`, `src/nova/systems/*/__init__.py`, `src/nova/ports/__init__.py`, `src/nova/setup/__init__.py` (all empty package shells from Story 1.1)
- `tests/conftest.py` (single-line docstring — NOT touched here)
- `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py`, `test_events.py`
- `tests/unit/test_scaffold.py`
- `pyproject.toml` (hatchling, ruff with `T20`, mypy strict on `src/` + `tests/` with `explicit_package_bases = true`, pytest with `--strict-markers` and `asyncio_mode = "auto"`)
- `uv.lock` (committed)
- Tests pass: 236 in ≈0.33s (Story 1.3 final count)

This story **adds**:

- `src/nova/core/storage/engine.py` (new — `SqliteStorageEngine` class + `SqlParams` type alias)
- `tests/unit/core/test_storage_engine.py` (new — 20–22 tests)

This story **modifies**:

- `src/nova/core/storage/__init__.py` (placeholder → re-export `SqliteStorageEngine`)
- `src/nova/core/__init__.py` (extend re-exports + `__all__` from 22 → 23 names)
- `tests/unit/core/test_core_isolation.py` (add `storage_engine_module`, 2 new frozensets, 4 new tests, 2 parametrize-list extensions)
- `.gitignore` (add `*.db-wal`, `*.db-shm` if not already present)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status transitions — story lifecycle)

### Previous Story Intelligence — Story 1.3 (done 2026-04-14)

Story 1.3 landed the event bus + typed event classes. Key carry-forwards:

- **`StorageError`-style domain-exception translation at the adapter boundary.** Story 1.2 established `StorageError(NovaError)` with `cause=` + `from err` chaining. This story IS the first place where `sqlite3.Error` actually gets translated — the pattern becomes real. Test asserts `isinstance(err.__cause__, sqlite3.Error)` (Story 1.3's `caplog.records[0].exc_info is not None` assertion is the analogous contract).
- **Logger naming convention:** `logging.getLogger("nova.core.storage.engine")` — matches Story 1.3's `nova.core.events` pattern.
- **Module docstring + "Architecture divergence owned by this story" section** is the documented pattern for pinning overrides against architecture.md. Use it for the `aiosqlite` → stdlib+executor divergence (epics.md:717 over architecture.md:1170).
- **`from __future__ import annotations`** on every new file (Story 1.1 D4 carry-forward).
- **`__all__: list[str]` annotation for mypy strict.** Explicit type annotation required (Story 1.3 pattern).
- **No `tests/__init__.py`** (Story 1.1 D1). Create `test_storage_engine.py` directly under `tests/unit/core/`.
- **`asyncio_mode = "auto"`** in `pyproject.toml` — `async def test_xxx(...) -> None:` with no decorator.
- **mypy strict on both `src/` and `tests/`** (Story 1.1 D3). Every test param annotated: `tmp_path: Path`, `caplog: pytest.LogCaptureFixture` (probably not needed here — no logger assertions are specced), `monkeypatch: pytest.MonkeyPatch` (probably not needed here).
- **Parametrize argvalues don't need annotation, wrapped function params do.** Story 1.3 carry-forward.
- **No `# type: ignore` in production code** (Story 1.2 carry-forward). The `assert self._connection is not None` narrowing trick handles the `None` | `Connection` mypy case without suppressions.
- **`# noqa: T201` only allowed in `cli.py`.** No `print()` in `engine.py` or test files. Use `logger` (debug logging during development may be helpful but should not ship — the `engine.py` module has a logger but the T1 story does NOT require any specific log lines; if the dev wants `logger.debug("connection opened")` calls for their own debugging, fine, but tests do not assert on them).
- **`core/__init__.py` re-export pattern** — 22 → 23 names, re-sort alphabetically. Matches Story 1.3's 12 → 22 transition.
- **AST adapter-isolation pattern** — extend `test_core_isolation.py` rather than creating a second file (Story 1.3 carry-forward). The dedicated frozenset approach (Story 1.3's `EVENTS_ALLOWED_TOPLEVEL_MODULES`) scales cleanly to `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES`.
- **Python 3.12.13 pins** `datetime.UTC`, `StrEnum`, PEP 695 type aliases, `TypeAlias` from `typing` (PEP 613). Use `TypeAlias` for `SqlParams` (strict-mypy-clean, no runtime overhead).
- **Frozen dataclass `__hash__` caveat documented in deferred-work.md:37** — not relevant here, `SqliteStorageEngine` is a stateful class (not a dataclass), so no hash contract applies.

### Git Intelligence — last 5 commits

```
7278eb9 Story 1.3: event bus + typed event classes (core/events.py)
ac1790c Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)
1da5c45 Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)
80dba55 Story 1.0 code review: resolve 20 findings, mark done
5b9d026 Initialize repo with planning artifacts and Story 1.0 (YAML config schemas spike)
```

- **Commit style:** terse, imperative, story ID prefix. Expected for this story: `"Story 1.4: SQLite storage engine (core/storage/engine.py)"` or similar.
- **Story 1.3 commit added 4 files (~+500 lines including tests).** This story is comparable — expect ~4 new/modified files and ~350–400 lines total including tests. `engine.py` is ~150 lines; `test_storage_engine.py` carries most of the bulk (~180–220 lines across 20–22 tests).
- **No prior `core/storage/engine.py`.** This story is the first to author it. The executor pattern, pragma set, and opaque-error-message contract established here inform Story 1.5 (the migration runner will use the same executor via the engine instance), Story 1.8 (AuditLogger writes via the engine), Story 3.1+ (Brain's SQLite adapter consumes the engine).

### Latest Tech Information (as of 2026-04-14)

- **Python 3.12.13** resolved managed interpreter. `sqlite3` module is stdlib, no install needed. The sqlite3 C library shipped with CPython 3.12 is typically **SQLite 3.43.x** (verify with `sqlite3.sqlite_version` at runtime if curious, but do NOT assert on a specific version in tests — it drifts with Python patch releases).
- **`sqlite3.connect(..., autocommit=False)` was added in Python 3.12** as a cleaner alternative to `isolation_level`. **Do NOT use it** — it changes transaction semantics subtly (explicit begin required, no implicit transactions). T1 stays on the stable `isolation_level="DEFERRED"` default, which Python maintains for backward compatibility indefinitely.
- **`sqlite3.Connection` gained `blobopen`, `setlimit`, `getlimit` in 3.12** — not used in T1.
- **`concurrent.futures.ThreadPoolExecutor(thread_name_prefix=...)`** is the 3.12-supported kwarg for naming worker threads (useful in traceback messages and `threading.enumerate()`). Set to `"nova-sqlite"` so any diagnostic shows the thread came from the engine.
- **`asyncio.get_running_loop()`** (3.7+) is the correct way to get the loop inside a coroutine. **Do NOT use `asyncio.get_event_loop()`** — deprecated when no running loop exists (3.10+), and raises `DeprecationWarning` in 3.12. Locked by ruff `UP`.
- **`pathlib.Path.mkdir(parents=True, exist_ok=True)`** — the idempotent parent-directory creation. No `os.makedirs` needed (project-context.md:49: "Use `pathlib.Path` for filesystem code. Do not use `os.path`").
- **PEP 695 `type` keyword** (Python 3.12+ native syntax): `type SqlParams = Sequence[str | int | float | bytes | None]`. This is the project convention for type aliases (project-context.md line 36 — codified by this story, which is the first to introduce an alias). ruff `UP040` enforces PEP 695 on py312 target, so `typing.TypeAlias` would trip the linter. Use the same form for private aliases: `type _SqlParamsTuple = tuple[...]`.
- **`asyncio.to_thread` vs `run_in_executor`** — `to_thread` is a thin wrapper over `get_running_loop().run_in_executor(None, ...)` which uses the default pool. Since we need our own executor, we skip `to_thread` entirely and go direct to `run_in_executor`.
- **No new dependencies needed.** `pyproject.toml` stays unchanged. Stdlib (`asyncio`, `sqlite3`, `concurrent.futures`, `logging`, `pathlib`, `types`, `typing`, `collections.abc`) covers everything.
- **ruff 0.5+ rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `UP` catches deprecated typing forms (`Optional`, `List`, `typing.Callable`) and deprecated stdlib calls (`datetime.utcnow`, `asyncio.get_event_loop`). `B008` (mutable default argument) is enforced — `params: SqlParams = ()` is safe (tuple is immutable).
- **mypy 1.20.1 strict mode** on `engine.py`. Narrowing pattern: `self._require_started(); assert self._connection is not None and self._executor is not None` — mypy strict accepts this narrowing cleanly without `# type: ignore`.
- **pytest-asyncio 1.3.0** — `asyncio_mode = "auto"` is already set; no decorator needed. Async tests that need a per-test loop work transparently.

### Project Structure Notes

- **Source file:** `src/nova/core/storage/engine.py` — path from architecture.md:1390.
- **Test file:** `tests/unit/core/test_storage_engine.py` (new). Placement follows architecture.md:1417 which lists `tests/unit/core/test_migrations.py` — storage tests live under `unit/core/`, even though the tests touch real (tmp_path) sqlite files. Rationale: sqlite3 is stdlib, tests are isolated per-test, no network/Win32/Claude. This is not "integration" in the T1 sense (integration tests live in `tests/integration/` and exercise cross-system flows).
- **Modified file:** `tests/unit/core/test_core_isolation.py` — extend existing AST guard tests.
- **Modified file:** `src/nova/core/storage/__init__.py` — was a Story 1.1 placeholder, becomes a re-export.
- **Modified file:** `src/nova/core/__init__.py` — add one import, extend `__all__`.
- **Modified file:** `.gitignore` — add `*.db-wal` and `*.db-shm` if missing.
- **No new directories.** All paths above exist from Stories 1.1–1.3.
- **Architecture divergence owned by this story:** `SqliteStorageEngine` uses stdlib `sqlite3` + dedicated `ThreadPoolExecutor(max_workers=1)`, NOT `aiosqlite`. Architecture.md line 1170–1174 shows migration examples with `aiosqlite.Connection`; epics.md line 717 pins stdlib; this story materializes the pattern.
- **Migrations package location — LOCKED by this story:** migration scripts and the migration runner (Story 1.5) live at `src/nova/core/storage/migrations/`, not `src/nova/migrations/` and not elsewhere. This matches architecture.md line 276, 584, 1162, 1391, and 1517 (all consistent). The Story 1.1 placeholder at `src/nova/core/storage/migrations/__init__.py` already exists. By re-exporting `SqliteStorageEngine` from `nova.core.storage` (AC #8), this story fixes the sub-package location as the canonical migrations home. Story 1.5 MUST place `runner.py` and `001_initial_schema.py` inside this directory; any drift (e.g., creating a top-level `nova.migrations`) is a Story 1.5 bug against a locked decision.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio + pytest-cov (configured in Story 1.1).
- **Async tests** use `asyncio_mode = "auto"`; no decorator needed. Signature: `async def test_xxx(tmp_path: Path) -> None:`.
- **mypy strict** applies to test files. Annotate `tmp_path: Path`. For `pytest.raises(StorageError, match="..."):` the context-manager type `pytest.ExceptionInfo[StorageError]` is inferred.
- **tmp_path** is the standard pytest fixture for per-test scratch directories — auto-cleaned after each test, never touches `%LOCALAPPDATA%/nova/`. Use `tmp_path / "test.db"` for DB paths.
- **Each test constructs its own engine.** No fixture in `conftest.py` in this story (matches Story 1.3's precedent — add fixtures when a genuine shared need appears, likely Story 1.5).
- **Test markers:** the `unit` marker is available via `pyproject.toml`; optional for this story. The `migration` marker is for Story 1.5 tests, NOT this story.
- **Test runtime budget:** <500ms total for all new tests. 20–22 tests × <25ms each. Each test creates a tiny DB file via `tmp_path`, runs 1–2 queries, closes. `tmp_path` I/O on SSD is <1ms per op.
- **Coverage target:** 100% of `engine.py`. Every branch of the error-translation `try/except` (start, close, each query helper) must be covered. The "called before start()" and "called after close()" guards are the critical negative-path coverage.
- **No fixtures added to `tests/conftest.py`.** If Story 1.5 wants a `storage_engine` fixture (start + yield + close), it adds it then.
- **Concurrent-insert test** — spawn multiple coroutines via `asyncio.gather(engine.execute(...), engine.execute(...), engine.execute(...))`; assert all three rows are present after. Proves the single-worker executor pattern holds. Without it, we'd get `sqlite3.ProgrammingError` on the second concurrent call.

### Critical Don't-Miss Rules (from project-context.md + architecture.md)

Carry-forward with rationale for this story:

- **"Brain owns all SQLite tables. Other systems read/write through Brain's port interface. No system queries SQLite directly."** (project-context.md:65) — this story does NOT violate the rule. The storage engine is in `core/`, not a system. Brain's adapter (Story 3.1+) consumes the engine; systems consume Brain's port. The engine is the `sqlite3` module's only client in the codebase.
- **"No raw SQL outside migrations. Systems use the storage engine API. Schema changes go through the migration runner only."** (project-context.md:40) — this story provides the storage engine API but does NOT issue any schema SQL (DDL). Tests use DDL only against tmp_path scratch DBs to exercise the `execute` helper; production code in this story runs zero DDL.
- **"Domain exceptions only. Never let adapter-specific exceptions cross a port boundary."** (project-context.md:39) — this story is where `sqlite3.Error` gets caught and re-raised as `StorageError`. Locked by tests.
- **"No sensitive content in exception messages."** (project-context.md:174) — opaque messages ("execute failed" etc.), raw SQL and params live only in the chained `__cause__` traceback (file log). Locked by the opaque-message test.
- **"Never swallow `asyncio.CancelledError`."** (project-context.md:47) — the engine catches `sqlite3.Error` and `OSError` specifically, never bare `Exception`, so `CancelledError` (a `BaseException`) propagates automatically. Same pattern as Story 1.3's `EventBus.emit`.
- **"Timeouts required at external boundaries."** (project-context.md:48) — `sqlite3.connect(timeout=5.0)` is the connection-level lock-wait timeout. No additional async-level timeouts needed in T1 (single-writer makes lock contention structurally impossible).
- **"Use `pathlib.Path` for filesystem code."** (project-context.md:49) — `db_path: Path` parameter, `db_path.parent.mkdir(...)`. No `os.path`.
- **"No mutable default values."** (project-context.md:50) — `params: SqlParams = ()` is immutable (`()` is the empty tuple). Safe.
- **"No `Any` in application code."** (project-context.md:45) — `SqlParams = Sequence[str | int | float | bytes | None]` covers sqlite3's parameter types without `Any`. mypy strict is clean.
- **"Typed boundary parsing required."** (project-context.md:46) — the engine returns `sqlite3.Row` objects, which is a raw-ish sqlite type. The rule says callers must parse to typed DTOs before entering system logic; that parsing lives in Brain's adapter (Story 3.1+), not here. The engine is the boundary that the rule refers to — everything beyond it (i.e., the adapter) is the "system logic side."
- **"Absolute imports only."** (project-context.md:42) — `from nova.core.exceptions import StorageError`, `from nova.core.storage.engine import SqliteStorageEngine`. Never `from .engine import ...`.
- **"No `print()` anywhere."** (project-context.md:43) — use `logger`. Tests use `pytest.raises`, not print.
- **"Timezone-aware datetimes only."** (project-context.md:44) — the engine does NOT create or parse timestamps. Callers (Brain's adapter) handle ISO 8601 UTC conversion. Out of scope here.
- **"Broad exception catching only at top-level boundaries."** (project-context.md:51) — the engine catches `sqlite3.Error` (narrow) and `OSError` (narrow, only in `start()` for mkdir). Never `Exception`.
- **"Stable serialization only. Enums serialize as stable string values. Datetimes serialize as ISO 8601 UTC. No pickle-based persistence."** (project-context.md:54) — the engine is serialization-agnostic; it stores whatever callers give it. Callers follow the rule.
- **"Single-user, single-process session ownership — no concurrent writers."** (project-context.md:23, architecture.md:23) — the dedicated single-worker executor enforces this structurally.
- **"Event bus is in-process only for T1."** (project-context.md:77) — unrelated to storage but reinforces "single process" as the architectural pillar.
- **"Schema migrations are numbered and backup-enforced."** (project-context.md:73) — future-facing, Story 1.5 owns it. This story's `start()` must NOT run migrations.
- **"Back up before every schema-affecting migration."** (project-context.md:161) — Story 1.5's concern. Confirm `backup()` is NOT in this story's class surface.
- **"Idempotency for cross-cutting actions."** (project-context.md:79) — `close()` is idempotent; `start()` is deliberately NOT idempotent (raises on second call) to surface misuse.
- **"Tests use isolated temp paths by default, never `%LOCALAPPDATA%/nova/`."** (project-context.md:158) — `tmp_path` fixture enforces this.
- **"Repo tree stays clean."** (project-context.md:157) — `.gitignore` covers `*.db` / `*.db-wal` / `*.db-shm`.

### Cross-story impact (where these primitives get consumed)

| Consumer story | Uses from this story | Why |
|---|---|---|
| 1.5 Migration runner & initial schema | `SqliteStorageEngine.execute`, `.fetchone` | Runner applies DDL + UPDATE schema_version via the engine's query helpers; engine provides the connection surface. Runner lives beside engine in `core/storage/migrations/`. |
| 1.6 Config loader & immutable NovaConfig | `NovaConfig.db_path: Path` | Config module exposes the resolved `%LOCALAPPDATA%/nova/nova.db` path; composition root (1.10) constructs `SqliteStorageEngine(config.db_path)`. |
| 1.8 Audit logger | `SqliteStorageEngine.execute` | AuditLogger writes audit_log rows via constructor-injected engine instance. Uses `execute` only; no reads in T1. |
| 1.10 Composition root | `SqliteStorageEngine(config.db_path)` + `await storage.start()` | Single engine instance per process, wired at boot. |
| 3.1 Brain session + seed persistence | All four query helpers via `adapters/sqlite/brain.py` | Brain's SQLite adapter is the primary consumer; translates between engine rows and domain types. |
| 5.1 Transparency command | `SqliteStorageEngine.fetchall` via Brain's port | Transparency model reads all user-visible state via Brain; Brain reads via engine. |
| 5.2 Selective forget | `SqliteStorageEngine.execute` (DELETEs) via Brain's port | Deletion propagation is multiple DELETE statements via the engine, wrapped in Brain's atomic-delete method. |
| 5.5 SQLite corruption recovery | `SqliteStorageEngine.start()` catching `StorageError` | Corruption surfaces as `StorageError` with `sqlite3.DatabaseError` cause; CLI recovery flow catches and prompts the user. |
| 5.6 Backup/restore | Engine's WAL side-files awareness | Backup includes `nova.db-wal` + `nova.db-shm` alongside `nova.db`. Documented in class docstring. |

Nine downstream stories consume `SqliteStorageEngine`. Renaming a helper, changing a signature, or breaking the error-translation contract is a **breaking change** that cascades. The opaque-message and exception-chaining tests are the regression gates.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4: SQLite Storage Engine](../planning-artifacts/epics.md) — canonical AC, lines 706–724.
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5: Migration Runner & Initial Schema](../planning-artifacts/epics.md) — lines 726–751, downstream consumer. Shows what this story must NOT do (no schema, no migrations, no backup).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives, architecture constraints.
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Schema](../planning-artifacts/architecture.md) — lines 527–585, T1 SQLite schema (consumed by Story 1.5, not this story).
- [Source: _bmad-output/planning-artifacts/architecture.md#Migration Convention](../planning-artifacts/architecture.md) — lines 1158–1184, migration rules. Story 1.4 does NOT implement migrations; Story 1.5 does.
- [Source: _bmad-output/planning-artifacts/architecture.md#Composition Root Convention](../planning-artifacts/architecture.md) — lines 1059–1102, shows `storage = SqliteStorageEngine(config.db_path)` usage (Story 1.10's job). `run_migrations()` on the engine is Story 1.5's addition.
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Handling Patterns](../planning-artifacts/architecture.md) — lines 1230–1247, `sqlite3.OperationalError` → `StorageError` translation pattern.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 1390, `core/storage/engine.py` path.
- [Source: _bmad-output/planning-artifacts/architecture.md#T1 Skeleton](../planning-artifacts/architecture.md) — line 1517, `core/storage/engine.py` listed as T1-active.
- [Source: _bmad-output/planning-artifacts/architecture.md#Runtime User Data Directory](../planning-artifacts/architecture.md) — lines 1434–1452, `%LOCALAPPDATA%/nova/nova.db` runtime path (engine consumes this via `config.db_path`, not directly).
- [Source: _bmad-output/planning-artifacts/prd.md#NFR18](../planning-artifacts/prd.md) — line 708, "Schema migrations must be non-destructive — automatic backup before migration". Story 1.5's concern, informs what this story does NOT do.
- [Source: _bmad-output/planning-artifacts/prd.md#NFR23](../planning-artifacts/prd.md) — line 716, SQLite size <100MB after 6 months. Justifies not over-tuning cache_size / mmap_size pragmas.
- [Source: _bmad-output/project-context.md](../project-context.md) — line 23 (single-user single-process), line 39 (domain exceptions), line 40 (no raw SQL outside migrations), line 42 (absolute imports), line 44 (timezone-aware), line 45 (no `Any`), line 47 (no swallow CancelledError), line 49 (`pathlib.Path`), line 50 (no mutable defaults), line 51 (narrow exception catching), line 65 (Brain owns SQLite tables), line 73 (numbered migrations, backup), line 157 (clean repo tree), line 158 (test isolation), line 161 (backup-before-migrate), line 174 (no sensitive content in exceptions).
- [Source: _bmad-output/implementation-artifacts/1-3-event-bus-and-typed-event-definitions.md](./1-3-event-bus-and-typed-event-definitions.md) — AST adapter-isolation extension pattern, logger naming, module-docstring + divergence-section convention, `__all__` alphabetization, no `# type: ignore` carry-forward, async test signature conventions.
- [Source: _bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md](./1-2-domain-exceptions-and-shared-types.md) — `StorageError` definition, `cause=` + `from err` chaining contract, AST adapter-isolation test framework.
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md](./1-1-project-scaffolding-and-package-setup.md) — D1 (no `tests/__init__.py`), D3 (mypy widened to tests), D4 (`from __future__ import annotations`), D5 (`.gitignore` `*.db`), asyncio_mode = "auto".
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — no open deferrals targeted at Story 1.4.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `StorageError` consumer, `NovaError.__init__` chaining contract.
- [Source: src/nova/core/__init__.py](../../src/nova/core/__init__.py) — existing re-export pattern to extend (22 names → 23).
- [Source: src/nova/core/storage/__init__.py](../../src/nova/core/storage/__init__.py) — Story 1.1 placeholder to replace.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — AST-level adapter-isolation pattern to extend for `storage/engine.py` (see Story 1.3's `EVENTS_ALLOWED_TOPLEVEL_MODULES` precedent).
- [Source: tests/conftest.py](../../tests/conftest.py) — placeholder, NOT touched in this story.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- **RED phase** — authored `tests/unit/core/test_storage_engine.py` first. `uv run pytest tests/unit/core/test_storage_engine.py -x` failed at collection with `ModuleNotFoundError: No module named 'nova.core.storage.engine'`. Confirmed tests reference contracts that do not yet exist.
- **GREEN phase** — authored `src/nova/core/storage/engine.py` with the lifecycle + query helpers + dedicated single-worker executor. First pytest run: 24 new storage tests pass. Full suite 287 passes.
- **Quality gates, first ruff pass** — three rule hits surfaced and drove the conventions now codified in AC #1/#7/#10 and project-context.md:
  - `UP040` on type-alias annotation form → switched to PEP 695 `type` keyword. First alias in the codebase, so this established the project convention (now project-context.md line 36). AC #1 / AC #7 / Latest Tech Information section updated to match.
  - `SIM105` on the pragma-failure cleanup in `_open_and_configure_sync` → replaced `try: conn.close() except sqlite3.Error: pass` with `contextlib.suppress(sqlite3.Error): conn.close()`. Added `contextlib` to `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` (AC #10) and to the engine import list (AC #7).
  - `E501` line-too-long on a test docstring → shortened the summary line.
- **Quality gates, second pass (mypy after ruff format)** — `error: Returning Any from function declared to return "Row | None" [no-any-return]` at `_fetchone_sync`. sqlite3's stubs type `cursor.fetchone()` / `cursor.fetchall()` as `Any` because the return depends on runtime `row_factory`. Added `typing.cast(sqlite3.Row | None, ...)` / `typing.cast(list[sqlite3.Row], ...)` with inline comments citing project-context.md:130. `typing` added to `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` and to AC #7's import list.
- **Quality gates, final** — `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returned exit 0. Output: `All checks passed!` / `33 files already formatted` / `Success: no issues found in 33 source files` / `287 passed in 0.93s`.
- **Concurrent-insert test** — passes first try. Validates the single-worker executor pattern: three `engine.execute(...)` coroutines `asyncio.gather`-ed, all three rows land in the table, no `sqlite3.ProgrammingError` about thread affinity fires. Without `ThreadPoolExecutor(max_workers=1)` (if someone swapped to `asyncio.to_thread`) this test would crash on the second await.
- **Failed-start aftermath test** — passes first try. Proves the cleanup contract: after `StorageError("start failed")`, `close()` is a no-op, query helpers report "not started", and retrying `start()` on a fresh `_db_path` succeeds on the same instance. No leaked executor thread (verified by subsequent operations completing without thread-affinity errors).
- **Opaque-message test** — locks the "no sensitive content in exception messages" contract: `str(err) == "execute failed"` exactly, `"secret-token-xyz"` and `"INSERT INTO"` both absent from the message.
- **WAL-sidecar hygiene** — ran `uv run pytest` and then `git status`; no stray `*.db`, `*.db-wal`, or `*.db-shm` files appeared in the repo tree. `pytest`'s `tmp_path` isolates cleanly, and `.gitignore` already covers every SQLite artifact pattern (Story 1.1 was thorough).

### Completion Notes List

- **All 13 ACs satisfied.** 24 new tests in `test_storage_engine.py` + 4 new dedicated isolation tests in `test_core_isolation.py` + 2 parametrize-list extensions. Full suite 287 passes in 0.93s (236 prior + 51 new).
- **SqliteStorageEngine contract locked precisely.** Dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="nova-sqlite")` for thread affinity; `sqlite3.connect(timeout=5.0, detect_types=0, isolation_level="DEFERRED")`; pragmas `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=NORMAL`; `row_factory=sqlite3.Row` for keyed access. No `aiosqlite`, no `asyncio.to_thread`, no `check_same_thread=False`.
- **Failure-cleanup invariant enforced:** after a failed `start()`, the engine is indistinguishable from never-started. Locked by `test_failed_start_aftermath_recovers_cleanly`. `CancelledError` / `KeyboardInterrupt` / `SystemExit` propagate untouched after cleanup (separate `except BaseException` arm).
- **`close()` blocking-shutdown caveat documented** in the class docstring verbatim: "close() is intended for process-shutdown; calling it mid-session blocks the event loop until the current worker task drains." Story 1.10's composition root MUST call it only at process exit.
- **Error translation pattern matches Story 1.2's contract.** All `sqlite3.Error` catches use `raise StorageError("xxx failed") from err`, populating `__cause__`. Test asserts `isinstance(info.value.__cause__, sqlite3.Error)` and `str(info.value) == "<helper> failed"` — both locked.
- **Opaque exception messages enforced by test.** Raw SQL (`"INSERT INTO"`) and params (`"secret-token-xyz"`) are explicitly asserted absent from `str(err)`. Technical detail lives only in the chained `__cause__` traceback (file log destination per Story 1.10+).
- **Generator materialization on caller thread** — `executemany`'s `[tuple(row) for row in seq_of_params]` list comprehension runs on the async caller before `run_in_executor`. `test_executemany_accepts_generator_materialized_on_caller` passes a generator and confirms all three rows land correctly.
- **mypy narrowing pattern documented in code.** Inline comment on the first `assert` after `self._require_started()`: `# mypy narrowing — _require_started raised above, asserts are load-bearing`. Future contributors now have the rationale when they see "redundant" asserts.
- **Conventions codified by this story (now reflected in AC #1, #7, #10 and project-context.md):**
  1. **PEP 695 `type` keyword** for type aliases — this is the first alias in the codebase, and ruff `UP040` enforces it on py312. Convention added to project-context.md line 36 so all future stories match.
  2. **`typing.cast` at the sqlite3 row-factory boundary** — the exact third-party integration-boundary case project-context.md:130 permits. Two narrow uses in `_fetchone_sync` / `_fetchall_sync`, each with inline rationale.
  3. **`contextlib.suppress(sqlite3.Error)`** in `_open_and_configure_sync`'s pragma-failure cleanup path — the idiomatic ruff `SIM105`-clean form.
- **Migrations package location locked** per story Dev Notes — `src/nova/core/storage/migrations/` is now canonical. Story 1.5 inherits this path.
- **Scope held tight.** No schema DDL, no migration runner, no `run_migrations()` method, no `backup()` method, no `app.py` wiring, no transaction context managers, no connection pooling, no fixture added to `tests/conftest.py`. No `pyproject.toml` changes, no new dependencies.
- **Carry-forward conventions applied:** `from __future__ import annotations` on both new files, `__all__: list[str]` annotated for mypy strict, no `tests/__init__.py`, no `print()`, all test functions annotated `-> None`, absolute imports throughout, `raise StorageError("...") from err` chaining pattern from Story 1.2.

### File List

- `src/nova/core/storage/engine.py` (new) — `SqliteStorageEngine` class with `start`/`close`/`execute`/`executemany`/`fetchone`/`fetchall` + async context-manager protocol; private helpers `_require_started`, `_open_and_configure_sync`, `_cleanup_partial_start`, plus four `@staticmethod` sync helpers (`_execute_sync`, `_executemany_sync`, `_fetchone_sync`, `_fetchall_sync`) that take `conn` as an explicit parameter; module-level `type SqlParams` + `type _SqlParamsTuple` + `_reject_scalar_string_params` guard + `logger`. Dedicated `ThreadPoolExecutor(max_workers=1)` for thread-affinity-safe sqlite3 access. WAL-mode verification in `_open_and_configure_sync`. `close()` uses `try/finally` to guarantee executor reap. All catch-nets include `sqlite3.Error`, `sqlite3.Warning`, `OSError`, `RuntimeError` as appropriate. ~360 lines.
- `src/nova/core/storage/__init__.py` (modified) — replaced Story 1.1 placeholder with re-export of `SqliteStorageEngine` + `__all__`.
- `src/nova/core/__init__.py` (modified) — added `from nova.core.storage import SqliteStorageEngine`; extended `__all__` from 22 → 23 names, alphabetized.
- `tests/unit/core/test_storage_engine.py` (new) — 37 unit tests covering construction, lifecycle (`start`/`close`/failed-start-aftermath + fresh-engine retry), pragma verification + WAL silent-fallback rejection, query helpers, error translation, opaque-message contract, async context manager, concurrent-execution thread-affinity, concurrent-close/execute race, close-with-raising-close-reaps-executor, bare-str/bytes param guard (parametrized × 3 helpers + executemany-bare-row + tuple-bytes-valid), executemany-empty-iterable-noop.
- `tests/unit/core/test_core_isolation.py` (modified) — added `storage_engine_module` import, `STORAGE_ENGINE_FORBIDDEN_TOPLEVEL_MODULES` and `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` frozensets, extended two existing parametrize lists, added four new dedicated tests (`test_storage_engine_forbidden_imports_minus_sqlite3`, `test_storage_engine_imports_within_allowlist`, `test_storage_engine_does_not_import_nova_adapters_or_systems`, `test_storage_engine_does_not_dynamically_import_nova_adapters_or_systems`).
- `_bmad-output/project-context.md` (modified) — codified two new conventions for future stories: PEP 695 `type` keyword for all type aliases (line 36), and the two-function clock pattern reuse for any timestamp-emitting module (line 47).
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified) — added five deferrals from the code review (SqlParams scalar-string footgun, get_running_loop drift, corrupt-DB pragma-failure test, DDL+transaction edge cases, Windows long-path tests).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story lifecycle transitions (ready-for-dev → in-progress → review → done).

## Senior Developer Review (AI)

**Reviewer:** Code Review skill (parallel Blind Hunter + Edge Case Hunter + Acceptance Auditor)
**Date:** 2026-04-14
**Outcome:** Approve — all patch findings resolved; five items deferred with targets; review conventions added to project-context.md.

### Review summary

- 25 raw findings across three adversarial layers → 17 unique after dedup (4 cross-layer overlaps merged).
- **Acceptance Auditor:** 11/13 ACs PASS (AC #12 and #13 are gate-verification-only and were confirmed separately via `uv run` output). All scope guards clear. Zero finding-level blockers from the auditor.
- **Blind Hunter + Edge Case Hunter:** surfaced six High-severity lifecycle / exception-safety gaps clustered around `close()`, `_cleanup_partial_start`, and the concurrent close/execute race. All resolved by the patches below.

### Key findings

**Fixed in this session (12 patches):**

- [x] [Review][Patch] `close()` now uses `try/finally` — executor shutdown runs even when `conn.close()` raises. Previously the narrow `except` skipped `shutdown(wait=True)`, leaking the worker thread. [engine.py:close]
- [x] [Review][Patch] `_cleanup_partial_start` routes connection close through the worker executor via `submit().result()` — respects sqlite3's thread-affinity contract. [engine.py:_cleanup_partial_start]
- [x] [Review][Patch] `close()` cancellation mid-await now reaps the executor via the `finally` arm before `CancelledError` propagates. [engine.py:close]
- [x] [Review][Patch] `start()` and `close()` pass `cancel_futures=True` to `shutdown()` — drops queued work so cleanup doesn't wait on unrelated submissions. [engine.py:start,close,_cleanup_partial_start]
- [x] [Review][Patch] All query helpers + `start()` now catch `RuntimeError` (shutdown executor / closed loop) in addition to `sqlite3.Error` — upholds the "every failure → StorageError" contract. [engine.py:execute,executemany,fetchone,fetchall,start]
- [x] [Review][Patch] Sync helpers (`_execute_sync`, `_executemany_sync`, `_fetchone_sync`, `_fetchall_sync`) refactored to `@staticmethod` taking `conn` as an explicit parameter captured on the caller thread. Eliminates the concurrent `close()` + `execute()` race that previously produced `AssertionError` (or `AttributeError` under `-O`). [engine.py:sync-helpers]
- [x] [Review][Patch] `_open_and_configure_sync` now verifies the returned `PRAGMA journal_mode = WAL` row and raises `sqlite3.OperationalError("WAL journal mode unsupported on this filesystem")` if the mode doesn't match — silent fallback surfaces as `StorageError("start failed")`. [engine.py:_open_and_configure_sync]
- [x] [Review][Patch] All `except` tuples include `sqlite3.Warning` — `Warning` inherits from `Exception` (not `sqlite3.Error`) so it would previously have escaped untranslated. [engine.py:all-helpers]
- [x] [Review][Patch] `_cleanup_partial_start`'s inner connection-close `except` now catches `OSError` and `TimeoutError` in addition to `sqlite3.Error` — secondary cleanup failures no longer override the primary exception. [engine.py:_cleanup_partial_start]
- [x] [Review][Patch] Concurrent-execute test changed from `ORDER BY val` to `ORDER BY rowid` — actually locks insertion order instead of alphabetical sort. [test_storage_engine.py:test_concurrent_executes_serialize_correctly]
- [x] [Review][Patch] Failed-start aftermath test split into two: one covering the same-instance "equivalent to never-started" contract, and one covering the production pattern "construct a fresh engine after a failure." Removed the test-only private-attribute poke. [test_storage_engine.py:test_failed_start_aftermath_matches_never_started, test_fresh_engine_succeeds_after_prior_instance_failed]
- [x] [Review][Patch] Added `test_executemany_with_empty_iterable_is_safe_noop` — locks the empty-sequence no-op contract. [test_storage_engine.py]

**New regression-guard tests added to cover the High-severity fixes:**

- `test_wal_verification_rejects_silent_fallback` — uses `sqlite3.connect(factory=...)` subclass to simulate a WAL-unsupported filesystem; asserts `StorageError("start failed")` with chained `sqlite3.OperationalError`.
- `test_close_translates_raising_conn_close_to_storage_error` — injects a Connection subclass whose `close()` raises; asserts `StorageError("close failed")` AND that the executor was still shut down (no worker-thread leak).
- `test_concurrent_close_and_execute_race_surfaces_storage_error` — kicks off `execute()` and `close()` roughly simultaneously via `asyncio.create_task`; asserts any resulting error is `StorageError`, never `AssertionError` or `AttributeError`.

**D1 (SqlParams bare-str/bytes footgun) — patched in this review round (option b):**

- [x] [Review][Patch] Added module-level `_reject_scalar_string_params(params: SqlParams) -> None` helper — called at the top of every public helper and once per row in `executemany`. Rejects bare `str` / `bytes` with `StorageError("params must be a tuple or list of scalars, not a bare str/bytes")`. Minimal and explicit: only `isinstance(params, (str, bytes))` is rejected — every other sequence shape (tuple, list, range, custom) passes through. A tuple containing `bytes` values remains valid for BLOB columns. [engine.py:_reject_scalar_string_params]
- [x] [Review][Patch] Added 8 tests locking the guard: `test_bare_str_params_rejected_with_clear_error` (parametrized over 3 helpers), `test_bare_bytes_params_rejected_with_clear_error` (parametrized over 3), `test_executemany_bare_str_row_rejected`, `test_tuple_bytes_param_is_valid`. [test_storage_engine.py]
- [x] [Review][Patch] Round 3 fix: `executemany` additionally guards its top-level `seq_of_params` argument with `isinstance(seq_of_params, (str, bytes))` BEFORE iteration — the per-row guard catches bare str (via per-char str) but not bare bytes (which yields ints, passing the guard, then `tuple(int)` raises bare `TypeError` outside error translation). Distinct message: `"seq_of_params must be an iterable of parameter rows, not a bare str/bytes"`. Locked by `test_executemany_top_level_bare_str_or_bytes_rejected` (parametrized over `"abc"` and `b"abc"`). [engine.py:executemany, test_storage_engine.py]

**Deferred (4 items, all documented in `deferred-work.md`):**

- [x] [Review][Defer] `asyncio.get_running_loop()` drift between calls not enforced — Target: Story 1.10 (composition root) — wires single-loop lifetime.
- [x] [Review][Defer] Corrupt-DB-that-opens-but-fails-on-pragma test — Target: Story 5.5 (SQLite corruption recovery) — naturally needs a corrupt-DB fixture.
- [x] [Review][Defer] DDL + implicit-transaction failure-carryover edge cases — Target: Story 3.1+ (Brain adapter) — first multi-statement consumer.
- [x] [Review][Defer] Windows long-path / directory-as-db tests — Target: Story 2.1 / Story 5.5 — platform hardening work.

**Dismissed (2 items):**

- `-O` strips asserts → invalidated by the sync-helper refactor (asserts are mypy-narrowing only; worker-side safety now relies on explicit `conn` parameter).
- Narrow `KeyboardInterrupt` between two state-assignments in `start()` → the existing `except BaseException` handler already covers this window with both locals in scope.

### Final metrics

- Quality gates: ruff / ruff format / mypy strict all pass.
- Test suite: **302 passed in 1.53s** (up from 287 after adding: empty-executemany, WAL-fallback, close-raises-with-shutdown, concurrent-close-race, aftermath-split, 6 parametrized bare-str/bytes guards, executemany-bare-row, tuple-bytes-valid, parametrized top-level-bare-str-or-bytes for executemany).
- Net story impact: +13 tests, ~15 touched engine.py lines/methods, zero production-code regressions, two project-wide conventions codified (PEP 695 `type` keyword, two-function clock pattern).

### Change Log

- 2026-04-14: Code review round 1 — 12 patches applied, 5 deferrals logged, 2 findings dismissed.
- 2026-04-14: Code review round 2 — D1 pulled back from deferred per user direction; added minimal `_reject_scalar_string_params` guard (option b) and 4 locking tests. Status: review → done.
- 2026-04-14: Code review round 3 — user-reported medium finding: `executemany` top-level bare `bytes` leaked raw `TypeError` because per-row guard saw `int`s. Added dedicated top-level `isinstance(seq_of_params, (str, bytes))` check with distinct error message. Regression-locked by `test_executemany_top_level_bare_str_or_bytes_rejected` (parametrized over both inputs). 302 tests passing.
