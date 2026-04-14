# Story 1.5: Migration Runner & Initial Schema

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing Brain, AuditLogger, or any other persistence-consuming system,
I want a numbered-migration runner that discovers scripts under `core/storage/migrations/`, auto-backs up `nova.db` before applying pending migrations, and records each applied version in a `schema_version` table,
so that the SQLite schema evolves safely, every user data artifact is recoverable from a timestamped backup, and no raw DDL ever lives outside a numbered migration file.

## Acceptance Criteria

1. **`src/nova/core/storage/migrations/runner.py` defines `MigrationRunner`** — a single class that discovers, validates, and applies numbered migration scripts against a started `SqliteStorageEngine`. Public surface (exact signatures):
   - `__init__(self, engine: SqliteStorageEngine, migrations_package: str = "nova.core.storage.migrations", backup_dir: Path | None = None) -> None` — stores the engine, the package path to scan for migration modules, and the backup directory (defaults to `engine._db_path.parent / "backups"` when `None`; computed lazily at `run()` time so construction stays I/O-free). Constructor is side-effect-free — no module import, no FS access, no schema introspection.
   - `async def run(self) -> list[int]` — the single public entrypoint. Returns the list of version numbers applied in this call (empty list if no-op). Contract: (a) ensures the `schema_version` table exists (safe no-op after first call), (b) discovers all migration modules, (c) diffs discovered versions against applied versions, (d) if pending set is non-empty AND the **applied set is non-empty** (per the precise "prior schema state worth protecting" rule in AC #6), creates a timestamped backup of `nova.db` in `backup_dir` BEFORE applying anything, (e) applies pending migrations in ascending version order inside the engine's `transaction()` context manager (AC #7a), recording each one in `schema_version` inside the same transaction as its DDL, (f) returns the sorted list of versions applied.
   - **No additional public methods.** Specifically: no `backup()`, no `rollback()`, no `downgrade()`, no `get_pending()`, no `get_applied()`. Internal helpers exist (see Task breakdown) but are all `_` -prefixed. If a future story needs a pub method (e.g., `nova self-update` surface), it adds it then; do NOT pre-build.
   - **Idempotent** — calling `run()` when no migrations are pending is a safe no-op: returns `[]`, creates NO backup (backup is only triggered when pending set is non-empty), writes nothing to `schema_version`, logs a single INFO line `"migrations: no pending versions"`. Locked by the idempotent-rerun test (AC #8).

2. **Migration module contract — every file in `core/storage/migrations/` follows the exact shape** (enforced structurally by the runner's loader and by unit tests against `001_initial_schema.py`):
   - **Filename:** `NNN_short_name.py` where `NNN` is a **3-digit zero-padded** integer (`001`, `002`, ..., `999`). The runner filters `Path.iterdir()` on `re.fullmatch(r"(\d{3})_[a-z][a-z0-9_]*\.py", name)` — rejects `1_initial_schema.py` (no zero-pad), `001-initial-schema.py` (hyphens), `__init__.py`, `runner.py`, and any other non-matching file silently (they are not migrations — no error, just not included). Locked by test.
   - **Module attributes — exactly three, all module-level:**
     - `VERSION: int` — must equal the integer parsed from the filename prefix (e.g., `001_initial_schema.py` → `VERSION = 1`). The runner verifies `VERSION == int(filename_prefix)` and raises `StorageError("migration file/version mismatch: <path>")` on mismatch. This prevents `001_foo.py` with `VERSION = 7` — a silent reorder hazard.
     - `DESCRIPTION: str` — one-line human-readable summary (≤100 chars). Persisted into `schema_version.description` verbatim. Whitespace-only / empty rejected with `StorageError("migration description missing: <path>")`.
     - `async def up(engine: SqliteStorageEngine) -> None` — applies the schema change. **Receives the storage engine**, not a raw connection. The `up()` function calls `await engine.execute(...)` for each DDL/DML statement. Rationale: every SQL call flows through the engine's single-writer executor and error-translation net, matching the project-wide "no raw SQL outside migrations uses the engine API" rule from Story 1.4.
   - **`down()` is NOT required** for T1. Architecture.md:1183 explicitly states "Down migrations are defined but not automatically run — they exist for manual recovery." Story 1.5 ships T1 without any `down()` — adding them pre-emptively is scope creep. If a future story needs manual rollback, it adds `down()` to the relevant migration and a separate manual-recovery script (NOT the runner's auto path).
   - **Module-level I/O is forbidden.** Import-time side effects (executing SQL, reading files, creating connections) break the discovery pass, which imports every module to read `VERSION`/`DESCRIPTION`/`up`. Migrations do their work inside `up()`, never at import. Any migration that violates this is a correctness bug — the loader tests are the regression gate.

3. **Migration discovery — `_discover_migrations(self) -> list[MigrationModule]`** (private helper).
   - Uses `importlib.resources.files(self._migrations_package)` (Python 3.12 API — works from both a src layout and an installed wheel) to iterate files in the package directory. Do NOT use `pkgutil.iter_modules` — it descends into subpackages and the migrations package is flat.
   - For each filename matching the `\d{3}_...\.py` regex (AC #2): resolve the full module path (e.g., `nova.core.storage.migrations.001_initial_schema`) and `importlib.import_module(path)`. Wrap each import in `try/except Exception as err: raise StorageError(f"migration import failed: {filename}") from err` — module-level errors in a migration file surface as a clear story-level failure, not a cryptic stack trace.
   - Validates each imported module has `VERSION: int`, `DESCRIPTION: str`, `up: async callable`. Missing or wrong-typed attributes raise `StorageError("migration {filename} missing required attribute: {name}")`. Use `inspect.iscoroutinefunction(module.up)` to validate `up` is async.
   - Cross-checks `VERSION` matches the filename prefix as specified in AC #2.
   - **Rejects duplicate VERSION** across files — if `001_foo.py` (`VERSION=1`) and `002_bar.py` (`VERSION=1`) both exist, raise `StorageError("duplicate migration version: 1 in 001_foo.py and 002_bar.py")`. Filename prefix mismatch catches most of this, but two files with different prefixes but matching `VERSION` values is still a hazard.
   - Returns a list of a small private dataclass `@dataclass(frozen=True) class MigrationModule: version: int, description: str, filename: str, up: Callable[[SqliteStorageEngine], Awaitable[None]]`. Defined inside `runner.py`, not re-exported.
   - **Sorted by `version` ascending** before return. The test `test_discovery_returns_sorted` locks this.

4. **`schema_version` table bootstrap** — before any discovery/diff logic, `run()` executes `CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT)` via `engine.execute(...)`. This statement is the ONE exception to the "no raw DDL outside migrations" rule — it bootstraps the migration tracking table itself. Document the exception inline in the `runner.py` source with a `# Exception: raw DDL for the migration-tracking table itself — chicken/egg.` comment. Every other DDL lives inside a numbered migration file.
   - The statement is idempotent (`IF NOT EXISTS`) so repeated calls are safe.
   - After bootstrap, `run()` reads applied versions via `await engine.fetchall("SELECT version FROM schema_version ORDER BY version")` and stores them in a `set[int]`.

5. **Pending diff and application order** — pending = `set(discovered_versions) - set(applied_versions)`.
   - If pending is empty, return `[]` (the idempotent no-op path).
   - If pending contains a version LESS than `max(applied_versions)` — e.g., applied = {1, 3} and a freshly added `002_...` surfaces with `VERSION=2` — raise `StorageError("out-of-order migration detected: version 2 pending but version 3 already applied")`. This surfaces the "someone renamed a migration" / "someone reverted a version" bug loudly instead of silently applying the gap-filler.
   - Apply pending migrations in **ascending** sorted order. Never skip. Never reorder.

6. **Backup-before-migrate — `_backup_db(self, db_path: Path) -> Path`** (private helper), called from `run()` exactly once per invocation when the pending set is non-empty AND the **applied set is non-empty** (i.e., there is prior schema state worth protecting).
   - **Backup gate — "applied set non-empty," NOT a file-size threshold.** A fresh install has zero prior applied migrations: nothing to back up. Any subsequent run with at least one migration already in `schema_version` triggers the backup before applying the next pending one. This rule is precise (does not depend on FS artifacts like the WAL header bytes that `engine.start()` writes upfront), survives WAL mode without false-positives, and cleanly matches the architecture intent at architecture.md:1179. **Earlier draft proposed a `stat().st_size >= 100` threshold; rejected during implementation because `engine.start()` enables WAL mode which writes ~96 bytes of header upfront, making the threshold flicker on the empty-DB boundary.** The applied-set rule is the locked contract.
   - Resolves `backup_dir` lazily (at call time, not at `__init__`): `self._backup_dir if self._backup_dir is not None else db_path.parent / "backups"`. Creates the directory via `backup_dir.mkdir(parents=True, exist_ok=True)`.
   - Backup filename: `nova_{timestamp}.db` where `timestamp` is produced by `_backup_timestamp()` — which routes through `_utc_now_iso()` so a single `monkeypatch.setattr` makes both `applied_at` and the backup filename deterministic. Format: `nova_YYYYMMDD_HHMMSS_ffffff.db` (e.g., `nova_20260414_193045_000000.db`) — microsecond precision prevents same-second collisions when two backups fire within one wall-clock second. Architecture.md:1447's example uses second precision; the microsecond suffix is a Story 1.5 review-round refinement.
   - **WAL sidecar handling:** checkpoint the WAL before copying. `await self._engine.execute("PRAGMA wal_checkpoint(FULL)")` — forces any pending WAL content into the main DB file so the backup captures the full state. If the checkpoint fails (rare — `sqlite3.OperationalError` for lock contention), the engine's error translation surfaces it as `StorageError`; the runner lets it propagate unchanged (backup failure aborts the migration). **Do NOT copy `nova.db-wal` / `nova.db-shm` sidecars separately** — post-checkpoint, the main file is authoritative. Single-file backup matches architecture.md:1446–1447.
   - **Copy method: `shutil.copy2(db_path, backup_path)`** — preserves metadata (mtime/atime). Do NOT use `shutil.copyfile` (loses metadata) or `Path.read_bytes()` + `write_bytes()` (loses metadata AND doubles memory for large files — 100MB NFR23 upper bound).
   - Catches `shutil.copy2` `OSError` (disk full, permission denied) and re-raises as `StorageError("backup failed") from err` (opaque message, chained cause — Story 1.4 carry-forward).
   - Returns the backup `Path` on success. The function is only reached from inside `run()` when the gate (applied set non-empty AND pending set non-empty) holds — there is no `None` return path. The fresh-install case skips the call entirely upstream.
   - Locked by tests: `test_backup_skipped_on_fresh_db` (fresh install, applied set empty → no backup file), `test_run_skips_backup_when_no_pending` (applied set non-empty but pending set empty → no backup), `test_run_creates_backup_when_pending` (applied set non-empty AND pending set non-empty → exactly one backup file matching the timestamp pattern), `test_backup_filename_is_deterministic_with_monkeypatched_clock` (backup filename strict-equals the frozen-clock value).

7a. **Engine extension — `SqliteStorageEngine.transaction()` async context manager.** Story 1.4 left the engine in an **auto-commit-per-statement** shape: `_execute_sync` / `_executemany_sync` each call `self._connection.commit()` after the statement (engine.py:386, 394). That shape is correct for single-statement writes but makes multi-statement atomicity impossible via the existing `execute()` surface — issuing `BEGIN IMMEDIATE` through `engine.execute` immediately commits the empty transaction, so the subsequent DDL + INSERT auto-commit individually and the atomicity contract (AC #7b, test `test_apply_is_atomic_on_midway_failure`) cannot hold. **Story 1.5 closes this by adding a transaction primitive to the engine.** New surface:
    - `async def transaction(self) -> AsyncIterator[None]` — `@asynccontextmanager`-decorated (imported `from contextlib import asynccontextmanager`). Signature: `async with engine.transaction(): ...`. On enter: calls `_require_started()`, acquires `self._tx_lock: asyncio.Lock` (added to `__init__`), flips `self._in_transaction: bool = True` (also added to `__init__`), and issues `BEGIN IMMEDIATE` via a **new internal path** `_execute_no_commit_sync` that does NOT call `conn.commit()`. On normal exit: issues `COMMIT` via the same no-commit path, flips the flag off, releases the lock. On exception: issues `ROLLBACK` via the no-commit path, flips the flag off, releases the lock, re-raises the original exception (including `CancelledError`). The lock-release + flag-unset live in a `finally` arm so the engine always returns to a clean post-transaction state even under cancellation.
    - **`execute` / `executemany` behavior inside a transaction.** When `self._in_transaction` is True, the dispatch path routes to `_execute_sync_no_commit` / `_executemany_sync_no_commit` (the existing sync helpers' bodies minus the `conn.commit()` call). When False, the existing auto-commit path runs unchanged. The decision happens on the async side **before** the worker dispatch — the sync helpers remain `@staticmethod` and the path choice is a one-line `if self._in_transaction:` branch in each of the four public query helpers. Zero Story 1.4 test regressions expected: every existing test runs outside a transaction, so `_in_transaction=False` is the default and those tests exercise the untouched commit path.
    - **`fetchone` / `fetchall` inside a transaction** are unchanged — reads don't commit anything, so the same sync helpers serve both paths. But they MUST be dispatch-protected by the same `_tx_lock` to prevent a read crossing the transaction boundary. Simpler: when `_in_transaction` is True AND the current task is NOT the task that entered the transaction, raise `StorageError("read from inside foreign transaction")`. Even simpler and sufficient: document that reads inside a transaction are fine only from the same async task; the single-worker executor serializes them physically. Tests do not exercise cross-task reads inside a transaction — that's out of scope for T1's single-writer model. **Implementation choice: no read-blocking guard in T1.** Document in the docstring: "Within a `transaction()` block, use the same engine reference to read; cross-task reads into an in-flight transaction are undefined and not supported in T1."
    - **Nested `transaction()` is forbidden.** Attempting `async with engine.transaction():` while `self._in_transaction` is already True raises `StorageError("nested transaction")`. Lock is released in that case too. No SAVEPOINT nesting in T1 — pre-emptive complexity. Locked by `test_nested_transaction_rejected`.
    - **Transaction lock scope.** `self._tx_lock` is an `asyncio.Lock`, not a `threading.Lock` — transactions are coordinated on the event loop, not the worker thread. Acquiring a threading lock from async code is subtle (blocks the loop). The asyncio lock pairs correctly with the async context manager.
    - **Test additions in `test_storage_engine.py`:**
      - `test_transaction_context_manager_commits_on_success` — execute two writes inside a transaction block; after exit, both rows are present.
      - `test_transaction_context_manager_rolls_back_on_exception` — execute a write, then raise inside the block; assert the row is NOT present after.
      - `test_transaction_rolls_back_on_cancellation` — enter transaction, write, cancel the task via `asyncio.wait_for` timeout; assert row not present after, `CancelledError` propagated, engine still usable.
      - `test_nested_transaction_rejected` — `async with engine.transaction(): async with engine.transaction(): pass` raises `StorageError("nested transaction")`.
      - `test_fetch_inside_transaction_sees_own_writes` — write inside a transaction, then `fetchone` inside the same transaction returns the uncommitted row. Proves read-own-writes within the transaction scope.
      - `test_transaction_releases_lock_on_exception` — after a transaction that raised, a second `async with engine.transaction():` succeeds. Proves the `finally` arm releases the lock.
    - **mypy strict** — the `@asynccontextmanager` signature needs `AsyncIterator[None]` from `collections.abc`. Add to engine.py's import list: `from collections.abc import AsyncIterator, Iterable, Sequence` (extends the existing import, no new module). Add `from contextlib import asynccontextmanager` — NOT through the `import contextlib` alias (the decorator call site `@asynccontextmanager` reads cleaner with a direct import; `import contextlib` stays for `contextlib.suppress`).
    - **AST isolation test** — `contextlib` and `collections` are already in `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` (test_core_isolation.py:91–105). No test changes.

7b. **Apply each pending migration — `_apply_migration(self, migration: MigrationModule) -> None`** (private helper, in runner.py).
    - **Each migration runs inside `async with self._engine.transaction():` — one transaction per migration spanning the DDL and the `schema_version` insert.**
      ```python
      async with self._engine.transaction():
          await migration.up(self._engine)
          await self._engine.execute(
              "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
              (migration.version, _utc_now_iso(), migration.description),
          )
      # Commit happens on context-exit. Any exception inside the block —
      # sqlite3.Error surfaced as StorageError from the engine, RuntimeError
      # from an ill-behaved up(), CancelledError from an outer cancel — triggers
      # ROLLBACK inside the context manager, then propagates unchanged.
      ```
    - **`BEGIN IMMEDIATE` semantics** are owned by the engine's `transaction()` (AC #7a) — the runner does not need to know this is the mode. Documented as the engine's choice.
    - **Rationale for same-transaction DDL + `schema_version` insert:** if the DDL succeeds but the insert fails (disk full between statements), the transaction `ROLLBACK` reverts both; without the same-transaction guarantee, the DDL would be "applied" but not recorded, and the next `run()` would re-try → conflict. Locked by `test_apply_is_atomic_on_midway_failure`.
    - **Per-migration logger output** at INFO: `logger.info("migration applied", extra={"version": migration.version, "description": migration.description})`. Emitted AFTER the transaction commits — a log before the commit is a lie if the commit then fails. Structured log (project-context.md:128). No migration-level `print()`.
    - **Exceptions bubble up** to `run()`, which propagates them out of the runner. The partial-apply contract is: one migration fully succeeds (row in `schema_version`) or fully fails (rolled back, no row). A failure mid-pending-batch means earlier migrations committed, later ones not attempted. The next `run()` picks up where this one left off. This is the standard "fail forward, no gap fills" semantic.

8. **`core/storage/migrations/001_initial_schema.py`** — the T1 initial schema migration, implementing architecture.md lines 527–585 verbatim.
   - `VERSION = 1`
   - `DESCRIPTION = "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"`
   - `async def up(engine: SqliteStorageEngine) -> None:` issues **FIVE** `CREATE TABLE` statements via `await engine.execute(...)`:
     1. `sessions` — exact columns per architecture.md:538–546: `id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ended_at TEXT, mode_name TEXT, seed_text TEXT, summary TEXT, is_complete INTEGER DEFAULT 0`.
     2. `workspace_snapshots` — exact columns per architecture.md:549–555: `id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES sessions(id), captured_at TEXT NOT NULL, snapshot_type TEXT NOT NULL, workspace_data TEXT NOT NULL`.
     3. `memory_items` — exact columns per architecture.md:558–565: `id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER REFERENCES sessions(id), category TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, relevance_score REAL DEFAULT 1.0`.
     4. `audit_log` — exact columns per architecture.md:568–576: `id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, action_type TEXT NOT NULL, target TEXT, result TEXT NOT NULL, details TEXT`.
     5. **`schema_version` is NOT created by `001_initial_schema.py`** — it is bootstrapped by `runner.py` before migrations run (AC #4). This keeps the migration file focused on product tables only.
   - **Column types and constraints — exact.** `TEXT`, `INTEGER`, `REAL` only; no `BLOB`, no `NUMERIC`. All timestamp columns are `TEXT` (ISO 8601 UTC as strings) — matching architecture.md:1216 and project-context.md:45.
   - **No indexes in T1.** Epics.md line 747 lists tables and columns only; architecture.md shows no `CREATE INDEX` in the T1 DDL. Add indexes in a later story only when query profiling shows a need. Pre-emptive indexing is scope creep.
   - **No `NOT NULL` on nullable architecture-spec columns.** `sessions.ended_at`, `sessions.mode_name`, `sessions.seed_text`, `sessions.summary`, `memory_items.session_id`, `audit_log.target`, `audit_log.details` are nullable per the schema sketch. Faithfully reproduce — do NOT add `NOT NULL` "for safety."
   - **No `WITHOUT ROWID`**, no `STRICT` — the architecture.md DDL uses neither; reproduce the baseline shape. `STRICT` tables enforce column type at write time but also change how `INTEGER PRIMARY KEY AUTOINCREMENT` interacts with ROWID. Adding `STRICT` is a schema-shape change that needs an explicit architecture amendment.
   - **Module docstring** states: "T1 initial schema. Creates sessions, workspace_snapshots, memory_items, and audit_log tables per architecture.md:527–585. Order is load-bearing: `sessions` FIRST because workspace_snapshots and memory_items declare FOREIGN KEY references to it. The runner's test-case (AC #10) verifies that applying this migration against an empty DB leaves exactly these four tables plus schema_version."
   - **`from __future__ import annotations`** at top, matches project convention.
   - **Import list — minimal:** `from nova.core.storage.engine import SqliteStorageEngine` is the only import needed for the `up(engine)` signature. No `sqlite3` import — the engine handles that. No `logger` — the runner logs the apply event.

9. **Integration with storage engine startup sequence.** **Story 1.5 does NOT modify `app.py` or the composition root** — that wiring is Story 1.10's job. However, the architecture sketch at architecture.md:1067–1068 shows `storage = SqliteStorageEngine(config.db_path); await storage.run_migrations()` as the expected startup shape. Story 1.5 materializes this pattern by:
   - **Option A (chosen):** Adding `async def run_migrations(self) -> list[int]` as a **thin delegating method on `SqliteStorageEngine`** that constructs a `MigrationRunner(self)` internally and awaits `runner.run()`. This keeps the composition-root call site simple (`await storage.run_migrations()`) and matches the architecture literal. The engine's `run_migrations` is a two-line method: instantiate runner, return runner result. No `MigrationRunner` import is exposed at the `core.storage` package level except via this method — callers that want custom backup_dir or migration package (e.g., tests) still construct `MigrationRunner(engine, ...)` directly.
   - **Precondition check in `run_migrations`:** raises `StorageError("storage engine is not started")` if `self._connection is None`. Migrations require an open DB connection. This piggybacks on the existing `_require_started` guard in the engine (Story 1.4 AC #6) — call it from `run_migrations` before constructing the runner.
   - **No circular import:** `engine.py` imports `MigrationRunner` inside the `run_migrations` method body (function-local import), NOT at module level, because `runner.py` imports `SqliteStorageEngine` at module level. Function-local imports to break circularities are allowed here; document the reason inline.
   - Alternative options **rejected:** (B) a separate `run_migrations()` function that takes the engine as a parameter — forces every caller to import from two places; (C) making the composition root call `MigrationRunner` directly — exposes internal plumbing to Story 1.10. Option A wins on ergonomics.

10. **`core/storage/migrations/__init__.py` is updated** from the Story 1.1 placeholder (`"""Numbered migration scripts. Implementation in Story 1.5."""`) to a canonical one-liner:
    - New body:
      ```python
      """Numbered migration scripts and the migration runner.

      Files under this package follow the ``NNN_short_name.py`` convention
      and are discovered, diffed, and applied by
      :class:`nova.core.storage.migrations.runner.MigrationRunner`. See
      Story 1.5 for the full contract.
      """
      ```
    - **Do NOT re-export `MigrationRunner` from this `__init__.py`.** Importing it from `nova.core.storage.migrations.runner` directly is the one blessed path. Re-exporting would tempt callers to use it instead of the `SqliteStorageEngine.run_migrations()` delegating method (AC #9), which is the architecture-sanctioned surface. The `__init__.py` stays documentation-only.

11. **Timestamp helper — reuse the two-function clock pattern from Story 1.3** per project-context.md:46.
    - `runner.py` declares a module-private pair:
      ```python
      def _utc_now_iso() -> str:
          return datetime.now(UTC).isoformat()

      def _default_timestamp() -> str:
          return _utc_now_iso()
      ```
      — matches `core/events.py` line 74/92 structure.
    - **Only `_utc_now_iso` is used inside the runner** — called at `schema_version.applied_at` insert time and at backup-filename formation. `_default_timestamp` exists as the factory indirection specifically so future fields that want a `field(default_factory=...)` pattern can use it cleanly. Having both now keeps the convention consistent across modules.
    - **Rationale for not importing `_utc_now_iso` from `nova.core.events`:** cross-module import of a private name creates a fragile coupling (a rename in events.py breaks migrations). The convention is that each timestamp-emitting module declares its own pair; tests monkeypatch the local `_utc_now_iso` to freeze time for determinism. Locked in project-context.md:46: "either by importing `_utc_now_iso` from `nova.core.events` or by declaring a matching two-function pair in the owning module" — we choose the latter.
    - **Test uses `monkeypatch.setattr("nova.core.storage.migrations.runner._utc_now_iso", lambda: "2026-04-14T19:30:45+00:00")`** to assert deterministic `schema_version.applied_at` values AND deterministic backup filenames. Locked by test.

12. **Error translation — every failure becomes a `StorageError` with opaque message + chained cause.** Same contract as Story 1.4.
    - Import, discovery, attribute-validation, version-collision, backup, and apply failures each raise `StorageError("<module-specific message>") from err` where applicable.
    - **Opaque message rule:** messages describe the class of failure ("backup failed", "migration import failed: 002_add_x.py", "out-of-order migration detected") — never the SQL body, never the row contents, never the user DB path verbatim. Filename is safe to include (it's not user data — it's a project-authored filename).
    - **Underlying `sqlite3.Error` from the engine already arrives as `StorageError`** (Story 1.4 translates at the engine boundary). The runner re-raises those unchanged — no double-wrap. Attempting to wrap `StorageError("execute failed")` into `StorageError("apply failed")` with an inner `StorageError` cause loses the original `__cause__` (`sqlite3.Error`); the runner's policy is "let engine-originated `StorageError` propagate untouched; wrap only non-`StorageError` exceptions we catch ourselves."
    - Catch-narrowness: the runner catches `Exception` ONLY at the `_discover_migrations` import-each-module boundary, where any module-level failure is possible. Everywhere else it catches the specific exception class (`OSError` for file I/O, `ImportError` for missing modules, `TypeError` for wrong attribute types).
    - `asyncio.CancelledError` propagates unchanged. **`_apply_migration` does NOT contain its own try/except for ROLLBACK** — that responsibility lives inside `engine.transaction()` (AC #7a). The runner's apply path is a single `async with self._engine.transaction(): ...` block; the engine handles BEGIN/COMMIT/ROLLBACK and re-raises the original exception unchanged. **No `import contextlib` in `runner.py`** — Story spec earlier proposed `contextlib.suppress(StorageError)` around an explicit `engine.execute("ROLLBACK")` call, but that responsibility moved into the engine when the `transaction()` primitive was added (AC #7a closes the auto-commit gap). The runner's import list (AC #13) reflects the simpler post-refactor surface.

13. **Imports in `core/storage/migrations/runner.py`** — exact list:
    - `from __future__ import annotations`
    - `import importlib`
    - `import inspect`
    - `import logging`
    - `import re`
    - `import shutil`
    - `from collections.abc import Awaitable, Callable`
    - `from dataclasses import dataclass`
    - `from datetime import UTC, datetime`
    - `from importlib.resources import files as resource_files`
    - `from pathlib import Path`
    - `from typing import TypeVar, cast` — `TypeVar` parameterizes `_validate_attr` so the return narrows from `object` to the concrete `int` / `str` (mypy strict requires this); `cast` narrows the dynamic-import attribute lookup in `_validate_up` after `inspect.iscoroutinefunction` confirms the runtime shape (project-context.md:130 — `cast` allowed at documented integration boundaries).
    - `from nova.core.exceptions import StorageError`
    - `from nova.core.storage.engine import SqliteStorageEngine`
    - **Forbidden:** `sqlite3` (only `engine.py` may import it), `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32*`, `rich`, `yaml`. The runner talks to the DB ONLY through the engine's API.
    - **No `typing.Any`.** `Callable[[SqliteStorageEngine], Awaitable[None]]` covers the `up` signature without `Any`.
    - **No `from nova.adapters.*`, `from nova.systems.*`, `from nova.ports.*`.** This module is infrastructure under `core/`.

14. **Extend `tests/unit/core/test_core_isolation.py` to cover the migration runner.**
    - Import the module: `import nova.core.storage.migrations.runner as migration_runner_module`.
    - **Forbidden set for runner — same denylist as `core/events.py`** (i.e., the full `FORBIDDEN_TOPLEVEL_MODULES` — including `sqlite3`, since the runner must NOT touch `sqlite3` directly; it uses the engine).
    - **Allowlist for runner:** `frozenset({"__future__", "collections", "dataclasses", "datetime", "importlib", "inspect", "logging", "nova", "pathlib", "re", "shutil", "typing"})` — note: `nova` covers `nova.core.exceptions` and `nova.core.storage.engine`. `typing` is included for `TypeVar` / `cast` (AC #13). **No `contextlib`** — rollback responsibility moved into `engine.transaction()` (AC #7a), so the runner does not need `contextlib.suppress`.
    - Parametrize extensions: add `migration_runner_module` to `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules`. **Do NOT** add it to the storage-engine-specific tests (those have the sqlite3 carve-out which does NOT apply here).
    - Add dedicated tests mirroring the Story 1.3/1.4 pattern:
      - `test_migration_runner_forbidden_imports` — parametrized only over `migration_runner_module`, uses `FORBIDDEN_TOPLEVEL_MODULES` directly.
      - `test_migration_runner_imports_within_allowlist` — parametrized only over `migration_runner_module`, uses the new runner allowlist.
      - `test_migration_runner_does_not_import_nova_adapters_or_systems` — mirrors events/storage-engine pattern.
      - `test_migration_runner_does_not_dynamically_import_nova_adapters_or_systems` — mirrors events/storage-engine pattern.
    - **001_initial_schema.py is NOT added to the isolation test suite.** Migration files are data (declarative DDL) — they import only `SqliteStorageEngine` which is already allowlisted. Pre-emptively guarding every migration file would force a test update on every new migration. Rationale codified by this story.

15. **Unit tests in `tests/unit/core/test_migration_runner.py`** — the runner-behavior regression suite. Use `tmp_path` for DB paths + backup dirs per project-context.md:158 (never `%LOCALAPPDATA%`). All test functions are `async def ... -> None` with mypy-strict-clean signatures.
    - **Test 1 — `test_discovery_finds_001_initial_schema`** — default migrations_package; assert `_discover_migrations()` returns exactly one entry with `version=1, description=<architecture-mandated string>, filename="001_initial_schema.py"` and that `up` is a coroutine function.
    - **Test 2 — `test_discovery_filename_regex_rejects_malformed`** — create a tmp package layout with one good file (`001_good.py`) and several bad files (`1_initial.py`, `001-dashes.py`, `abc_nothing.py`, `__init__.py`, `runner.py`). Re-parametrize the `migrations_package` via constructor. Assert discovery returns only `001_good.py`. Verifies regex is the gate (no silent acceptance of `1_foo.py`).
    - **Test 3 — `test_discovery_returns_sorted`** — tmp package with `003_third.py`, `001_first.py`, `002_second.py`; assert returned list has versions `[1, 2, 3]` in order.
    - **Test 4 — `test_discovery_rejects_version_filename_mismatch`** — tmp package with `001_foo.py` containing `VERSION = 7`. Assert `StorageError("migration file/version mismatch: ...")` is raised.
    - **Test 5 — `test_discovery_rejects_duplicate_version`** — tmp package with `001_a.py` and `002_b.py` both declaring `VERSION = 1` (file/version mismatch on b). First file is fine; second fails the file/version check (test equivalent of the duplicate-version case — the filename prefix mismatch catches it). Add a second test case where both files have matching prefix/VERSION but collide — `001_a.py` VERSION=1 and `001_b.py` VERSION=1 (though filesystem prevents same name; pick `001_a.py` VERSION=1 and test via a synthetic module list after discovery to assert the duplicate-detector). Simpler: unit-test the duplicate detector helper directly with a list of synthetic `MigrationModule` instances.
    - **Test 6 — `test_discovery_rejects_missing_attributes`** — tmp package with a migration file missing `DESCRIPTION`. Assert `StorageError("... missing required attribute: DESCRIPTION")`.
    - **Test 7 — `test_discovery_rejects_non_async_up`** — tmp package with a migration file where `up` is a plain `def`. Assert `StorageError` with message citing `up` must be async.
    - **Test 8 — `test_run_creates_schema_version_table`** — fresh engine + empty migrations package. Assert `run()` returns `[]` AND `schema_version` table exists afterward (via `await engine.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")`).
    - **Test 9 — `test_run_applies_001_initial_schema_fresh`** — fresh engine + real `nova.core.storage.migrations` package (i.e., the production `001_initial_schema.py`). Assert `run()` returns `[1]` AND all five tables (sessions, workspace_snapshots, memory_items, audit_log, schema_version) exist with their specced columns AND `schema_version` contains exactly one row `(1, <iso-timestamp>, "Initial T1 schema: ...")`.
    - **Test 10 — `test_run_is_idempotent_on_rerun`** — run `001_initial_schema.py` twice back-to-back. Assert first call returns `[1]`, second call returns `[]`, `schema_version` row count is still 1, no duplicate schema_version row.
    - **Test 11 — `test_run_creates_backup_when_pending`** — pre-populate `schema_version` with at least one applied row (`applied` set non-empty) by running `001_initial_schema.py` first, then add a pending `002_noop.py` in a synthetic package. Assert the second `run()` returns `[2]` AND exactly one backup file exists in `backup_dir` matching the `nova_YYYYMMDD_HHMMSS_ffffff.db` filename regex (microsecond suffix). The backup gate (AC #6) fires precisely because there is now prior schema state worth protecting.
    - **Test 12 — `test_run_skips_backup_when_no_pending`** — pre-populate DB with schema_version = {1} and ship just `001_initial_schema.py`. Run. Assert no backup file created AND backup_dir may or may not exist (don't assert — the implementation creates it lazily).
    - **Test 13 — `test_backup_skipped_on_fresh_db`** — fresh `SqliteStorageEngine` (just started, no migrations ever applied, `applied` set is empty). Run with real `001_initial_schema.py`. Assert `run()` returns `[1]` AND no backup file was created — the applied-set-non-empty gate (AC #6) skips the backup on first install. Assert `backup_dir` either does not exist OR is empty.
    - **Test 14 — `test_backup_filename_is_deterministic_with_monkeypatched_clock`** — `monkeypatch.setattr("nova.core.storage.migrations.runner._utc_now_iso", lambda: "2026-04-14T19:30:45+00:00")`. Run with pending migration. Assert backup filename is exactly `nova_20260414_193045_000000.db` (strict; no drift). The frozen-clock string has microsecond=0, so the deterministic suffix is `_000000`.
    - **Test 15 — `test_apply_is_atomic_on_midway_failure`** — synthetic migration whose `up()` starts a table creation, then raises `RuntimeError("boom")` mid-flight. Before calling run(), schema_version = {} and the table doesn't exist. Run and catch the RuntimeError. After the failure, assert: (a) the half-created table from the failed migration does NOT exist (ROLLBACK fired), (b) `schema_version` has no row for the failed version (transactional atomicity), (c) a re-run of `run()` attempts the same migration again (from the "pending" logic). Proves the one-transaction-per-migration contract.
    - **Test 16 — `test_apply_out_of_order_raises`** — pre-populate schema_version with `{1, 3}`. Ship migrations `001, 002, 003`. Run. Assert `StorageError("out-of-order migration detected: version 2 pending but version 3 already applied")` is raised AND schema_version state is unchanged (no backup, no applies).
    - **Test 17 — `test_run_requires_started_engine`** — construct engine, do NOT call `start()`. Construct `MigrationRunner(engine)`. `await runner.run()`. Assert `StorageError("storage engine is not started")` (propagates from the engine's guard OR from `run_migrations` precondition — either path is acceptable).
    - **Test 18 — `test_engine_run_migrations_delegates`** — construct `engine`, `await engine.start()`, call `await engine.run_migrations()` (the delegating method from AC #9). Assert it returns the same result as `await MigrationRunner(engine).run()` would. Proves the delegation path is wired.
    - **Test 19 — `test_schema_version_applied_at_is_iso8601_utc`** — run `001_initial_schema.py`. Query `schema_version`. Parse `applied_at` with `datetime.fromisoformat(row["applied_at"])`. Assert `dt.tzinfo is not None` (aware) AND `dt.utcoffset() == timedelta(0)` (UTC). Locks the "timezone-aware ISO 8601 UTC" contract from project-context.md:45.
    - **Test 20 — `test_schema_version_description_is_exact`** — assert `schema_version.description` for version 1 equals the exact string `"Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"` — locking the T1 initial-schema description to prevent future drift.
    - **Test 21 — `test_fk_constraint_enforced_after_migration`** — after running `001_initial_schema.py`, attempt `INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data) VALUES (999, '2026-04-14T00:00:00+00:00', 'startup', '{}')` — session_id 999 does not exist. Assert a `StorageError` fires with `isinstance(err.__cause__, sqlite3.IntegrityError)`. Proves (a) FK enforcement is on (engine set `PRAGMA foreign_keys = ON`), (b) `001_initial_schema.py` faithfully preserves the FOREIGN KEY constraint.
    - **Test 22 — `test_integration_fresh_db_applies_001_and_all_tables_exist`** — per epics.md line 751, the integration-style test. Actually lives in `tests/integration/test_migrations_integration.py` (AC #16 covers placement) but listed here for completeness.
    - **Test budget:** 21 tests in `test_migration_runner.py` + 1 test in `tests/integration/test_migrations_integration.py`. Total runtime target: <1.5s for unit suite, <500ms for the integration test. No network, no Win32, no `%LOCALAPPDATA%`.

16. **Integration test — `tests/integration/test_migrations_integration.py`.** This is the first integration test in the repo. Epics.md line 751 explicitly asks for it ("integration test verifies: upgrade from empty DB applies 001 and all tables exist with correct columns").
    - File creation: `tests/integration/__init__.py` is NOT created (same rule as `tests/unit/` — no `__init__.py` in the tests tree, Story 1.1 D1). Pytest discovers the file via rootdir + testpaths.
    - Single test `test_fresh_db_applies_001_initial_schema_and_tables_match_architecture`:
      - Construct `SqliteStorageEngine(tmp_path / "nova.db")`, `await engine.start()`, `await engine.run_migrations()`.
      - Assert all five expected tables exist: `{"sessions", "workspace_snapshots", "memory_items", "audit_log", "schema_version"}` (via `PRAGMA table_list` or `SELECT name FROM sqlite_master WHERE type='table'`).
      - For EACH T1 product table (sessions, workspace_snapshots, memory_items, audit_log), use `PRAGMA table_info(<table>)` to assert the full column list (name, type, nullable, default) matches architecture.md:538–576 **exactly**. Parametrize over the four tables to keep the test readable.
      - Assert `schema_version` columns `{version, applied_at, description}` exist with correct types.
      - Assert `schema_version` contains exactly one row `(1, <iso-timestamp>, "Initial T1 schema: ...")`.
      - Close the engine cleanly. No dangling tasks (project-context.md:104).
    - **Add `[tool.pytest.ini_options]` testpaths extension if needed** — Story 1.1 set testpaths; check `pyproject.toml`. If `tests/integration/` is NOT in the current testpaths value, add it. Otherwise leave pyproject untouched. (Story 1.1 likely set `testpaths = ["tests"]` which covers both trees.)
    - **Test marker:** use `@pytest.mark.integration` — the marker is available per project-context.md:116. Register in `pyproject.toml`'s `[tool.pytest.ini_options].markers` if not already present.

17. **Engine modifications — two additions to `SqliteStorageEngine`.** Specific changes to `src/nova/core/storage/engine.py`:

    **17a. `async def transaction(self) -> AsyncIterator[None]` — multi-statement transaction context manager.** Full spec in AC #7a. Implementation skeleton:
    ```python
    # engine.py module-level adds:
    from collections.abc import AsyncIterator  # extend existing import line
    from contextlib import asynccontextmanager

    # Inside SqliteStorageEngine:
    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._connection: sqlite3.Connection | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._tx_lock: asyncio.Lock = asyncio.Lock()  # NEW — transaction mutex
        self._in_transaction: bool = False            # NEW — commit-suppression flag

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Multi-statement transaction context manager.

        Inside the block, `execute` / `executemany` do NOT auto-commit.
        COMMIT fires on normal exit; ROLLBACK on exception (including
        CancelledError). Nested transactions are rejected with
        StorageError. See AC #7a.
        """
        self._require_started()
        if self._in_transaction:
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
            self._in_transaction = True
            try:
                yield
            except BaseException:
                with contextlib.suppress(Exception):
                    await loop.run_in_executor(
                        executor, self._execute_sync_no_commit, conn, "ROLLBACK", ()
                    )
                raise
            else:
                try:
                    await loop.run_in_executor(
                        executor, self._execute_sync_no_commit, conn, "COMMIT", ()
                    )
                except (sqlite3.Error, sqlite3.Warning, RuntimeError) as err:
                    # COMMIT failed — attempt rollback best-effort, then surface.
                    with contextlib.suppress(Exception):
                        await loop.run_in_executor(
                            executor, self._execute_sync_no_commit, conn, "ROLLBACK", ()
                        )
                    raise StorageError("transaction commit failed") from err
            finally:
                self._in_transaction = False
    ```
    - **New private `@staticmethod` sync helpers** — `_execute_sync_no_commit(conn, sql, params)` and `_executemany_sync_no_commit(conn, sql, seq)`: identical bodies to their committing counterparts, just without the trailing `conn.commit()` line. Keep as separate methods for clarity; shared-body via an `if commit:` parameter would be clever but adds branching to the hot path.
    - **`execute` / `executemany` dispatch change** — one-line branch at the top of each:
      ```python
      sync_fn = self._execute_sync_no_commit if self._in_transaction else self._execute_sync
      await loop.run_in_executor(executor, sync_fn, conn, sql, params_tuple)
      ```
      `fetchone` / `fetchall` are unchanged (reads don't commit).
    - **Engine test additions in `tests/unit/core/test_storage_engine.py`** — six new tests per AC #7a.

    **17b. `async def run_migrations(self) -> list[int]` — thin delegator to MigrationRunner.**
    ```python
    async def run_migrations(self) -> list[int]:
        """Discover and apply pending migrations via MigrationRunner.

        Thin delegator — exists so the composition root (Story 1.10) can
        call `await storage.run_migrations()` matching architecture.md:1068.
        See ``nova.core.storage.migrations.runner.MigrationRunner`` for the
        full contract.
        """
        self._require_started()
        # Function-local import breaks the circular dependency:
        # runner.py imports SqliteStorageEngine at module level.
        from nova.core.storage.migrations.runner import MigrationRunner

        return await MigrationRunner(self).run()
    ```
    - Placed **after `close()`**, before `transaction()`. Both new methods live in the same region of the file.
    - **test_core_isolation behavior under the new imports:** `AsyncIterator` from `collections.abc` — `collections` is already allowlisted. `asynccontextmanager` from `contextlib` — `contextlib` is already allowlisted. The function-local `from nova.core.storage.migrations.runner import MigrationRunner` inside `run_migrations` is caught by `ast.walk` (which descends function bodies) and its top-level segment is `nova`, already allowlisted. **Expected: zero test_core_isolation changes for the engine.** Verify empirically after implementation — if a test fires unexpectedly, the fix is a narrow allowlist extension documented inline, NOT hiding the import behind cleverness.
    - Add unit tests:
      - `test_run_migrations_requires_started` — precondition guard fires before `start()`.
      - `test_run_migrations_delegates_to_runner` — matches AC #15 Test 18.

18. **`pyproject.toml` changes** — minimal or none.
    - **No new dependencies.** Everything used is stdlib (`shutil`, `importlib`, `re`, `datetime`, `contextlib`, `inspect`, `dataclasses`, `pathlib`).
    - **If the `integration` pytest marker is not already registered** in `[tool.pytest.ini_options].markers`, add it: `"integration: marks tests as integration tests (use real sqlite/file I/O, mock external services)"`. Expected state: Story 1.1 registered `unit` at minimum; the new marker needs adding. Verify before editing.
    - **`testpaths` verified unchanged** — Story 1.1's `tests` covers both `tests/unit/` and `tests/integration/`.
    - **No change to `tool.mypy` exclusions.** Story 1.5's code is fully strict-mypy-clean without new ignores. Migration files (`001_initial_schema.py`) are also mypy-strict-clean — the function signature is `async def up(engine: SqliteStorageEngine) -> None`, body is `await engine.execute(...)` calls, trivial to type.

19. **`.gitignore` hygiene** — already covers `backups/`? Check `.gitignore` at the start of implementation. Expected state: Story 1.1 added `*.db`, `*.db-wal`, `*.db-shm`. The `backups/` directory inside `%LOCALAPPDATA%/nova/` is NEVER inside the repo tree (project-context.md:158 forbids user data in repo). But tmp_path-based tests may leave `backups/` dirs inside `tmp_path` (which pytest cleans up automatically). So `.gitignore` needs NO changes for this story — existing patterns cover the artifacts. Document the negative conclusion in the completion notes to prevent a future contributor from "defensively" adding `backups/` to `.gitignore` (which would be dead code).

20. **Quality gates pass clean**: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0.
    - mypy strict succeeds on `runner.py`, `001_initial_schema.py`, the modified `engine.py` (after `run_migrations` addition), the modified `migrations/__init__.py`, the modified `test_core_isolation.py`, the new `test_migration_runner.py`, and the new `test_migrations_integration.py`.
    - No `Any`, no `# type: ignore` in production code. `cast()` is acceptable if a genuine sqlite3-stubs-`Any` narrowing surfaces; wrap with an inline comment per the Story 1.4 precedent.
    - Repo tree stays clean after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm`, `backups/` directories staged by `git status`. Same standard as Stories 1.1–1.4.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/storage/migrations/runner.py` — runner class + discovery** (AC: #1, #2, #3, #4, #5, #13)
  - [x] Module docstring: purpose (discovers, backups, applies, records numbered migrations via `SqliteStorageEngine`), cites architecture.md:1158–1184 and this story for the "same-transaction DDL + schema_version insert" decision and the "option A delegation" for `run_migrations`.
  - [x] `from __future__ import annotations` at top.
  - [x] Exact import list per AC #13.
  - [x] Module-level `logger = logging.getLogger("nova.core.storage.migrations.runner")` (matches engine/events naming).
  - [x] Module-level `_utc_now_iso` + `_default_timestamp` pair per AC #11.
  - [x] `@dataclass(frozen=True) class MigrationModule:` with fields `version: int`, `description: str`, `filename: str`, `up: Callable[[SqliteStorageEngine], Awaitable[None]]`. No `__all__` — it's internal.
  - [x] `class MigrationRunner:` with class docstring covering the 3-phase flow (discover → diff/backup → apply), the idempotent contract, and the "engine-originated StorageError propagates untouched" rule.
  - [x] `__init__(self, engine: SqliteStorageEngine, migrations_package: str = "nova.core.storage.migrations", backup_dir: Path | None = None) -> None` — stores state, no I/O.
  - [x] `_FILENAME_RE = re.compile(r"(?P<num>\d{3})_[a-z][a-z0-9_]*\.py")` — module-level constant. Pinned pattern, named capture group for readability. **Match with `_FILENAME_RE.fullmatch(name)` — NOT `.match()` / `.search()`.** `fullmatch` is the only method that rejects `001_initial_schema.py.bak`, `001_initial_schema_extra.py` (well, this one still fullmatches — the trailing `_extra` is captured), and any other prefix/suffix mismatch. Locked by Test 2.
  - [x] **Verify hatchling wheel packaging includes numeric-prefix `.py` files.** Open `pyproject.toml`, inspect `[tool.hatch.build.targets.wheel]` (and `[tool.hatch.build.targets.sdist]` if present). Hatchling's default include pattern is `["src/nova/**"]` or equivalent — numeric-prefix filenames match as long as no `exclude` rule filters them. If an `exclude` rule drops `[0-9]*.py` or anything else matching `001_initial_schema.py`, **extend the include list** (or remove the exclusion) so the migration ships in both the editable install (`pip install -e .`) and the wheel. Validation: `uv build && unzip -l dist/*.whl | grep migrations` should list `001_initial_schema.py`. Expected state: Story 1.1 left hatchling at defaults — no fix needed — but verify before committing.
  - [x] `_discover_migrations(self) -> list[MigrationModule]` — uses `importlib.resources.files`, filters by regex, imports each module, validates attrs, cross-checks VERSION==filename-num, detects duplicates, returns sorted list. Each validation failure raises `StorageError`.
  - [x] `_validate_discovered_no_duplicates(discovered: list[MigrationModule]) -> None` — standalone helper for the duplicate-version guard. Receives the list post-discovery, iterates and maintains a seen-set. Raises `StorageError("duplicate migration version: ...")` on collision. Kept separate so it can be tested against synthetic fixtures.

- [x] **Task 2: `run()` — the public entrypoint** (AC: #1, #4, #5, #6, #7, #12)
  - [x] Precondition: check engine is started (`engine._connection is None` → `StorageError`). Piggybacks on engine's own `_require_started` via a new public `run_migrations` call path (AC #9), but the runner also guards defensively.
  - [x] Bootstrap `schema_version` table via `await engine.execute("CREATE TABLE IF NOT EXISTS schema_version (...)")`.
  - [x] Read applied versions: `rows = await engine.fetchall("SELECT version FROM schema_version ORDER BY version"); applied = {row["version"] for row in rows}`.
  - [x] Call `_discover_migrations()` → `discovered`.
  - [x] Compute `pending = {m.version for m in discovered} - applied`.
  - [x] Out-of-order check: if `applied` is non-empty AND `pending` is non-empty AND `min(pending) < max(applied)` → `StorageError("out-of-order migration detected: version {min(pending)} pending but version {max(applied)} already applied")`. **No `math.inf` / `float("inf")` sentinel needed** — guard on the two emptiness checks first, then `min(pending)` is safe. AC #13 import list stays clean (no `math` import).
  - [x] If `pending` is empty: log INFO "migrations: no pending versions"; return `[]`.
  - [x] Backup step (AC #6): `await self._backup_db(db_path)` only if `applied` set is non-empty. Fresh installs (applied empty) skip backup; subsequent runs with prior schema state always back up before applying new pending migrations. Helper returns `Path` (no `None` path — caller has already gated on the rule).
  - [x] Apply pending in ascending order. Each via `_apply_migration(migration)`. On failure, propagates (no catch here — runner returns `[]` only on the empty-pending path).
  - [x] Return `sorted([m.version for m in pending_migrations_applied])`.

- [x] **Task 3: `_backup_db()` helper** (AC: #6, #12)
  - [x] Resolves `backup_dir` lazily.
  - [x] `backup_dir.mkdir(parents=True, exist_ok=True)`.
  - [x] `await engine.execute("PRAGMA wal_checkpoint(FULL)")` — force WAL content into main DB before copy.
  - [x] `timestamp = _utc_now_iso().replace(...)` → format `YYYYMMDD_HHMMSS`. Actually: since `_utc_now_iso` returns ISO 8601 with timezone, use a separate `datetime.now(UTC).strftime("%Y%m%d_%H%M%S")` call inside the helper that ALSO goes through the monkeypatchable `_utc_now_iso` clock for determinism. Design: define a helper `_backup_timestamp() -> str` that calls `_utc_now_iso()` and reformats — then the monkeypatched clock controls backup filename too. Ensures Test 14 passes.
  - [x] `backup_path = backup_dir / f"nova_{timestamp}.db"`.
  - [x] `shutil.copy2(db_path, backup_path)` wrapped in `try/except OSError as err: raise StorageError("backup failed") from err`.
  - [x] Log INFO `"migration backup created"` with `extra={"path": str(backup_path)}`.
  - [x] Return `backup_path`.

- [x] **Task 4: `_apply_migration()` helper** (AC: #7b, #12)
  - [x] `async with self._engine.transaction():` — single context manager, spans DDL + schema_version insert.
  - [x] Inside the block: `await migration.up(self._engine); await self._engine.execute("INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)", (migration.version, _utc_now_iso(), migration.description))`.
  - [x] No explicit `BEGIN` / `COMMIT` / `ROLLBACK` calls — the context manager owns them.
  - [x] INFO log on success (AFTER the context manager commits): `logger.info("migration applied", extra={"version": migration.version, "description": migration.description})`. Emitting BEFORE exit would lie if the COMMIT then fails.
  - [x] Exceptions from `up()` or the INSERT propagate out of the context manager unchanged — the manager handles ROLLBACK, the runner lets them bubble up to `run()`, which lets them propagate to the caller.

- [x] **Task 5: Author `src/nova/core/storage/migrations/001_initial_schema.py`** (AC: #8)
  - [x] Module docstring citing architecture.md:527–585 and the table-order rationale (sessions first for FK).
  - [x] `from __future__ import annotations`.
  - [x] Single import: `from nova.core.storage.engine import SqliteStorageEngine`.
  - [x] `VERSION: int = 1`.
  - [x] `DESCRIPTION: str = "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"`.
  - [x] `async def up(engine: SqliteStorageEngine) -> None:` with five `await engine.execute(...)` calls, each a heredoc-style triple-quoted SQL string. Order: sessions → workspace_snapshots → memory_items → audit_log (the FK dependency order; schema_version is bootstrapped by the runner separately).

- [x] **Task 6: Update `src/nova/core/storage/migrations/__init__.py`** (AC: #10)
  - [x] Replace Story 1.1 placeholder docstring with the canonical one per AC #10.
  - [x] No `__all__`, no re-exports.
  - [x] No `from __future__ import annotations` (not needed — no type hints in the file).

- [x] **Task 7: Extend `SqliteStorageEngine` — `transaction()` context manager + `run_migrations()` delegator** (AC: #7a, #9, #17)
  - [x] **Module-level import additions** (engine.py): extend `from collections.abc import Iterable, Sequence` → `from collections.abc import AsyncIterator, Iterable, Sequence`; add `from contextlib import asynccontextmanager` (keeps the existing `import contextlib` for `contextlib.suppress`). Both additions pass the existing `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` allowlist (`collections`, `contextlib` already listed — test_core_isolation.py:91–105).
  - [x] **`__init__` additions**: `self._tx_lock: asyncio.Lock = asyncio.Lock()` and `self._in_transaction: bool = False`. Both side-effect-free at construction.
  - [x] **`async def transaction(self) -> AsyncIterator[None]`** per AC #7a implementation skeleton. Placed after `close()`.
  - [x] **Two new `@staticmethod` sync helpers**: `_execute_sync_no_commit(conn, sql, params)` and `_executemany_sync_no_commit(conn, sql, seq)`. Bodies identical to their committing counterparts minus the `conn.commit()` line.
  - [x] **`execute` / `executemany` dispatch branch** — one-line ternary selecting the no-commit sync helper when `self._in_transaction` is True. `fetchone` / `fetchall` unchanged.
  - [x] **`async def run_migrations(self) -> list[int]`** per AC #17b. Placed after `transaction()`. Function-local import of `MigrationRunner` with inline rationale comment.
  - [x] **Engine test additions** (`tests/unit/core/test_storage_engine.py`): six new `transaction()` tests per AC #7a + two `run_migrations` delegator tests per AC #17b (8 new tests total; expected suite count 302 → 310).
  - [x] **Verify `test_core_isolation.py` still passes for the engine** — expected: zero changes. The new module-level imports (`AsyncIterator` from `collections.abc`, `asynccontextmanager` from `contextlib`) stay within the existing allowlist. The function-local `MigrationRunner` import surfaces to `ast.walk` but its top-level `nova` prefix is already allowlisted. If a test fires unexpectedly, document the narrow fix inline — do NOT hide the import behind cleverness.

- [x] **Task 8: Extend `tests/unit/core/test_core_isolation.py` for the migration runner** (AC: #14)
  - [x] Alphabetized import: `import nova.core.storage.migrations.runner as migration_runner_module`.
  - [x] `MIGRATION_RUNNER_ALLOWED_TOPLEVEL_MODULES` frozenset per AC #14.
  - [x] Extend `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules` parametrize lists.
  - [x] Add `test_migration_runner_forbidden_imports` — uses `FORBIDDEN_TOPLEVEL_MODULES` directly (no sqlite3 carve-out).
  - [x] Add `test_migration_runner_imports_within_allowlist`.
  - [x] Add `test_migration_runner_does_not_import_nova_adapters_or_systems`.
  - [x] Add `test_migration_runner_does_not_dynamically_import_nova_adapters_or_systems`.

- [x] **Task 9: Author `tests/unit/core/test_migration_runner.py` — 21 tests** (AC: #15)
  - [x] File header + imports per Story 1.4 conventions (`from __future__ import annotations`, minimal imports, no `tests/__init__.py`).
  - [x] Helper fixture `_tmp_migrations_package(tmp_path, monkeypatch, files: dict[str, str]) -> str` — creates a temporary Python package (dir + `__init__.py` + specified migration files), adds it to `sys.path` via monkeypatch, returns its dotted name. Each test that needs a synthetic migrations package uses this. Documented inline.
  - [x] 21 tests per AC #15. Each is `async def test_...(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:` with strict mypy annotations.
  - [x] No fixtures added to `tests/conftest.py` (same conservatism as Story 1.4).
  - [x] Coverage target: every branch of `_discover_migrations`, `_backup_db`, `_apply_migration`, and `run()` exercised.

- [x] **Task 10: Author `tests/integration/test_migrations_integration.py`** (AC: #16)
  - [x] Single test `test_fresh_db_applies_001_initial_schema_and_tables_match_architecture`.
  - [x] `@pytest.mark.integration` marker.
  - [x] PRAGMA-table-info assertions parametrized over the four T1 product tables.
  - [x] Clean engine close in `finally` block to avoid resource warning.

- [x] **Task 11: `pyproject.toml` marker registration** (AC: #18)
  - [x] Check `[tool.pytest.ini_options].markers` — if `integration` is not present, add it with the description per AC #18.
  - [x] No other `pyproject.toml` changes.

- [x] **Task 12: Full verify run** (AC: #20)
  - [x] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest`.
  - [x] Exit 0. Test count bumps from 302 (Story 1.4 final) to approximately 336 (302 + 21 runner-unit + 1 integration + 4 runner-isolation + 8 engine extensions [6 transaction + 2 run_migrations delegator]; total ~336).
  - [x] `git status` shows only intentional source/test/doc changes — no `__pycache__`, no `*.db`, no `backups/`.

- [x] **Task 13: Sprint status + commit** (AC: #20, post-implementation)
  - [x] Update `_bmad-output/implementation-artifacts/sprint-status.yaml` `development_status[1-5-migration-runner-and-initial-schema] = "in-progress"` when dev begins, then `"review"` when handing off to code review, then `"done"` after code review lands. (The story-create step has already moved it to `ready-for-dev`.)
  - [x] Commit message convention (Story 1.4 carry-forward): terse, imperative, story ID prefix. Expected: `"Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)"` or similar.

## Dev Notes

### Story Type: Foundational infrastructure — the single schema-change gateway

This story produces the **only path** by which SQLite schema evolves in the N.O.V.A. codebase. Every future schema change — adding a table, adding an index, altering a column — is a new numbered migration file placed under `core/storage/migrations/`. Every such file is discovered, backup-protected, applied, and recorded by `MigrationRunner.run()`. Runtime systems (Brain, AuditLogger, Nerve, etc.) never issue DDL themselves — they consume `SqliteStorageEngine.execute/fetchone/fetchall` against whatever schema the runner has established.

The story also materializes the T1 product schema: `sessions`, `workspace_snapshots`, `memory_items`, `audit_log`. These four tables are what Brain (Story 3.1+), AuditLogger (Story 1.8), and Deletion (Story 5.2) read and write for the rest of T1.

### Scope guard (hard stop)

- **Do NOT create any new tables beyond the five specified in AC #8** (schema_version, sessions, workspace_snapshots, memory_items, audit_log). Specifically: no `modes` table (modes are YAML-file-based per architecture.md:1428–1432), no `settings` table (also YAML), no `api_keys` table (secret lives in settings.yaml).
- **Do NOT add indexes.** Architecture.md's T1 schema defines none; adding `CREATE INDEX` pre-emptively is scope creep. Add in a later story when profiling shows hot paths.
- **Do NOT write a second migration.** The only migration in this story is `001_initial_schema.py`. Adding `002_...` is scope creep even if you can imagine it being needed (a `002_add_is_active` column on sessions, a `002_add_backups_table`, etc.). The next migration ships with whatever story first needs a schema change (likely Story 3.1 or 4.x), not this one.
- **Do NOT implement `down()` migrations** even though architecture.md:1173 mentions them. Architecture.md:1183 immediately clarifies "Down migrations are defined but not automatically run — they exist for manual recovery." The T1 automatic path has no use for them and pre-emptively authoring them doubles the migration file maintenance surface.
- **Do NOT add `nova self-update` command** — epics.md line 350 explicitly states "user-facing `nova self-update` is NOT T1." This story delivers the **infrastructure** for future self-update (backup-before-migrate enforced); the CLI surface is out of scope.
- **Do NOT modify `app.py`, `cli.py`, or the composition root.** The engine's `run_migrations()` method gets added (AC #9), but wiring `await storage.run_migrations()` into `cli.py` startup is Story 1.10's job. `app.py` and `cli.py` stay as Story 1.1 placeholders.
- **Do NOT touch `%LOCALAPPDATA%/nova/` from any test.** project-context.md:158 forbids it; use `tmp_path` exclusively. This includes NOT creating a real `backups/` directory outside `tmp_path` during any test.
- **Do NOT add a user-facing backup/restore command.** That's Story 5.6. This story's backup is automatic and invisible — it happens before every schema migration without user intervention.
- **Do NOT bundle shipped-config-copy logic** (copying defaults from repo `config/` to `%LOCALAPPDATA%/nova/`). That's part of the setup wizard (Epic 2). This story's concern is SQLite schema only.
- **If `runner.py` grows past ~250 lines of production code OR `001_initial_schema.py` past ~50 lines, you are over-building.** Keep the runner lean: discover, diff, backup, apply, commit. Keep the migration file declarative: five `CREATE TABLE` statements, no helpers, no abstractions.
- **Do NOT accept `VERSION` as a `str` at runtime to "support leading zeros."** `VERSION: int` is strict. The leading-zero zero-pad lives ONLY in filenames (the regex enforces it); the integer value inside the module is a plain int.

### Critical constraints and gotchas

- **`importlib.resources.files` on a src-layout project.** Python 3.12's `importlib.resources.files("nova.core.storage.migrations")` returns a `Traversable` that works both inside a wheel install and inside an editable `src/` layout. Do NOT use the older `importlib.resources.path` (deprecated in 3.11) or manual `os.listdir(Path(__file__).parent)` — those break in wheel installs and on namespace packages respectively. Locked convention.
- **Importing a migration module is import-time work — discovery is NOT lazy.** `_discover_migrations()` calls `importlib.import_module("nova.core.storage.migrations.001_initial_schema")` which executes the module body (top-level statements). That's why module-level I/O is forbidden (AC #2); anything at the module level runs at discovery time, before `up()` gets a chance to do its transactional work. `VERSION` / `DESCRIPTION` / `async def up` are all top-level but are **definitions**, not **side effects**.
- **`001_` prefix is a sort-friendly string, not a Python identifier.** Python modules can't start with a digit — `001_initial_schema` is NOT an importable identifier via `import nova.core.storage.migrations.001_initial_schema`. But `importlib.import_module("nova.core.storage.migrations.001_initial_schema")` DOES work because it uses a string, not an identifier. Locked by test.
- **`BEGIN IMMEDIATE` vs `BEGIN DEFERRED` vs `BEGIN EXCLUSIVE`.** The engine's connection was constructed with `isolation_level="DEFERRED"` (Story 1.4 AC #4), meaning sqlite3 auto-BEGINs in DEFERRED mode. The new `transaction()` context manager (AC #7a) issues `BEGIN IMMEDIATE` — upgrades to an immediate write lock acquired before any DDL. Prevents a theoretical "reader upgrades to writer mid-DDL" race. In T1 single-writer we'd be safe with DEFERRED too, but IMMEDIATE costs nothing and is the correct default for "I am about to write."
- **Why the engine needs a transaction primitive at all.** Story 1.4's `_execute_sync` calls `conn.commit()` after every statement (engine.py:386). That shape is correct for single-statement writes but makes multi-statement atomicity via the existing `execute()` surface **impossible** — issuing `BEGIN IMMEDIATE` through `engine.execute` immediately commits the empty transaction; subsequent DDL auto-commits one statement at a time, and a later `ROLLBACK` via `execute` has nothing to roll back. Options considered: (a) SQLite SAVEPOINTs work with auto-commit, but the semantic contract is weaker and the complexity is higher than a clean transaction primitive; (b) concatenating all DDL + INSERT into one `executescript()` call is not portable across migration bodies that interleave Python logic; (c) adding `execute_no_commit()` as a narrow engine surface wasn't enough — we also need atomic commit/rollback boundaries. **Chosen: add `transaction()` as an async context manager (AC #7a).** The runner's apply path (AC #7b) uses it cleanly; Story 1.4 tests run unchanged because `_in_transaction` defaults to False.
- **`PRAGMA wal_checkpoint(FULL)` before backup is load-bearing.** Without it, the backup captures only the main DB file, missing any post-checkpoint-boundary pages still in the WAL. On restore from backup, any such writes are lost. `FULL` forces ALL WAL content into the main file before returning. An alternative would be `PRAGMA wal_checkpoint(TRUNCATE)` which also resets the WAL file size — minor hygiene benefit, but `FULL` is the conservative choice and matches the "copy the main DB file = capture everything" semantic.
- **Backup-filename determinism.** The test-time monkeypatch of `_utc_now_iso` must flow through to both the `applied_at` timestamp AND the backup filename. Achieved by routing both through the same `_utc_now_iso()` call site. `_backup_timestamp()` helper does one call to `_utc_now_iso()` and reformats; `_apply_migration` uses `_utc_now_iso()` for the `applied_at` value. Single source of clock = deterministic tests.
- **`schema_version` row must land in the SAME transaction as the DDL.** If they separate, the runner is not crash-safe: DDL applies, power cut, schema_version row never written, next startup re-applies the migration → likely `CREATE TABLE` fails with "table already exists" → next startup never starts. The test `test_apply_is_atomic_on_midway_failure` simulates the failure and verifies atomicity. If a future refactor "optimizes" by splitting the transaction, that test fires.
- **Migration file discovery is package-based, NOT glob-based.** We use `importlib.resources.files`, not `Path(__file__).parent.glob("*.py")`, because the latter breaks in wheel installs (the migrations may live in a .whl archive, not on disk). For this project in a src layout with `pip install -e .`, both would work, but the `importlib.resources` path is robust against future packaging changes.
- **`inspect.iscoroutinefunction(module.up)` catches the non-async `up` case cleanly.** A synchronous `def up(engine):` would otherwise be silently awaitable-compatible only in older asyncio versions; the inspector's check is the right guard. Locked by Test 7.
- **Out-of-order sentinel — no infinity math, no `math` import.** The obvious implementation `min(pending, default=math.inf) < max(applied)` needs an `import math` that the allowlist doesn't carry and that adds no value. Instead, guard on the two emptiness checks first (AC #5 + Task 2): `if applied and pending and min(pending) < max(applied): raise StorageError(...)`. The empty-pending path already returns `[]` earlier in `run()`, so `pending` being non-empty by the time we reach this check is not guaranteed if someone re-orders the code — the redundant `pending` check here costs one attribute lookup and prevents a `min()` on an empty set. Clean and boring.
- **`importlib.resources.files(...)` returns a `Traversable`; iterate with `.iterdir()`, not `os.listdir`.** Each iteration yields another `Traversable` — use `.name` to get the filename string for the regex check.
- **`@dataclass(frozen=True) class MigrationModule` with `field(default_factory=...)` is NOT needed.** All fields are required; no defaults. Plain `@dataclass(frozen=True)` is fine.
- **`tests/integration/` is the first integration test location.** Story 1.5 creates it. Story 1.1 set `testpaths = ["tests"]` (verify), so pytest discovers the new file automatically — no `[tool.pytest.ini_options]` change needed beyond the `integration` marker registration.
- **`pytest.mark.integration` vs `@pytest.mark.asyncio`.** The `asyncio_mode = "auto"` setting means no `@pytest.mark.asyncio` decorator is needed. Just `@pytest.mark.integration` + `async def`. Both markers stack if needed via `@pytest.mark.integration` on top.
- **The runner does NOT create `%LOCALAPPDATA%/nova/backups/` directly.** Its backup path is `engine._db_path.parent / "backups"`. For production, `db_path = %LOCALAPPDATA%/nova/nova.db`, so `backups/ = %LOCALAPPDATA%/nova/backups/`. For tests, `db_path = tmp_path / "nova.db"`, so `backups/ = tmp_path / "backups"`. Zero hard-coding.
- **`shutil.copy2` on Windows copies the NTFS alternate data streams too (sort of).** For the SQLite backup, ADS is irrelevant — there are none on a `nova.db` file. But `copy2` also preserves permissions (octets visible on POSIX; Windows permissions mostly untouched). Harmless; keep the call.
- **`datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")` — the format** produces `20260414_193045_000000` for 2026-04-14 19:30:45 UTC. The `_%f` microsecond suffix prevents same-second filename collision (review-round refinement). Architecture.md:1447's example (`nova_20260413_191500.db`) uses second precision; the actual implementation goes one resolution finer.
- **`applied_at` TEXT column format — ISO 8601 UTC with tz marker.** `_utc_now_iso()` returns `"2026-04-14T19:30:45.123456+00:00"`. Preserve the microseconds and `+00:00` tz — the strict parse test (`datetime.fromisoformat`) validates this. Do NOT strip microseconds or tz marker for "cleanliness" — forecloses future sub-second ordering.
- **Don't accidentally lock CLI-path encoding.** `db_path: Path` through to `str(db_path)` at the sqlite3 boundary handles Windows paths with spaces, drive letters, and UNC prefixes transparently. No special-casing needed.

### Repo shape at time of this story

After Stories 1.0, 1.1, 1.2, 1.3, 1.4 the repo contains:

- `src/nova/core/__init__.py` (re-exports 23 names: 6 exceptions + 6 enums + 10 event-bus names + `SqliteStorageEngine`)
- `src/nova/core/events.py` (Story 1.3 — owns `_utc_now_iso` + `_default_timestamp` pattern)
- `src/nova/core/exceptions.py` (Story 1.2 — owns `StorageError` + the `from err` chaining contract)
- `src/nova/core/types.py` (Story 1.2)
- `src/nova/core/storage/__init__.py` (Story 1.4 — re-exports `SqliteStorageEngine`)
- `src/nova/core/storage/engine.py` (Story 1.4 — `SqliteStorageEngine` with single-worker executor, pragmas, opaque-error contract)
- `src/nova/core/storage/migrations/__init__.py` — Story 1.1 placeholder docstring only
- `src/nova/{app,cli}.py` (Story 1.1 placeholders — NOT touched here)
- `src/nova/adapters/*/__init__.py`, `src/nova/systems/*/__init__.py`, `src/nova/ports/__init__.py`, `src/nova/setup/__init__.py` (all empty package shells)
- `tests/conftest.py` (single-line docstring — NOT touched here)
- `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py`, `test_events.py`, `test_storage_engine.py`
- `tests/unit/test_scaffold.py`
- No `tests/integration/` directory yet
- `pyproject.toml` (hatchling, ruff with `T20`, mypy strict on `src/` + `tests/`, pytest with `asyncio_mode = "auto"`)
- `uv.lock` (committed)
- Tests pass: 302 in ~1.5s (Story 1.4 final count)

This story **adds**:

- `src/nova/core/storage/migrations/runner.py` (new — `MigrationRunner` class + `MigrationModule` dataclass + `_utc_now_iso` pair + `_FILENAME_RE`)
- `src/nova/core/storage/migrations/001_initial_schema.py` (new — five `CREATE TABLE` statements via `engine.execute`)
- `tests/unit/core/test_migration_runner.py` (new — 21 tests)
- `tests/integration/test_migrations_integration.py` (new — 1 integration test covering epics.md line 751)

This story **modifies**:

- `src/nova/core/storage/engine.py` — **two additions per AC #17**: (1) `transaction()` async context manager + `_tx_lock` + `_in_transaction` state + two `_*_sync_no_commit` helpers + dispatch branch in `execute`/`executemany`; (2) `run_migrations()` thin delegator. Also adds `AsyncIterator` to the `collections.abc` import and `asynccontextmanager` from `contextlib`.
- `src/nova/core/storage/migrations/__init__.py` (replace placeholder docstring)
- `tests/unit/core/test_storage_engine.py` — **8 new tests**: 6 for `transaction()` per AC #7a, 2 for `run_migrations` per AC #17b.
- `tests/unit/core/test_core_isolation.py` (add `migration_runner_module` + 1 new allowlist frozenset + 4 new tests + 2 parametrize-list extensions)
- `pyproject.toml` (register `integration` marker if not already present — check before editing)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (story lifecycle transitions)

### Previous Story Intelligence — Story 1.4 (done 2026-04-14)

Story 1.4 landed the `SqliteStorageEngine`. Key carry-forwards for Story 1.5:

- **`SqliteStorageEngine` public surface is the contract.** The runner talks to the DB EXCLUSIVELY via `engine.execute`, `engine.executemany`, `engine.fetchone`, `engine.fetchall`. No `sqlite3` import inside `runner.py` or any migration file. Verified by `test_core_isolation.py` (AC #14).
- **Error translation is already in place at the engine boundary.** Every `sqlite3.Error` arrives at the runner as a `StorageError` with opaque message and chained `__cause__`. The runner does NOT catch `sqlite3.Error` itself — it catches `StorageError` only at specific boundaries (backup-file I/O via `OSError`, module-import failures via `Exception` / `ImportError`).
- **Opaque exception message contract — Story 1.4 AC #5 is strict.** The runner's own `StorageError` messages follow the same pattern: generic, schema-level, no user data. "backup failed" not "backup failed: could not copy /path/to/nova.db → /path/to/backups/nova_xxx.db".
- **`_require_started()` guard pattern.** The engine raises `StorageError("storage engine is not started")` before any query. The runner's `run_migrations()` delegating method on the engine piggybacks on this guard (AC #9). The runner itself also defensively guards (AC #7) in case it is constructed and called outside the delegation path.
- **`_utc_now_iso` + `_default_timestamp` pair.** Story 1.3 established this; Story 1.4 didn't need it (no timestamps in engine); Story 1.5 reuses it for `schema_version.applied_at` and backup filename determinism. Follow project-context.md:46 literally — declare the pair locally in `runner.py`, do NOT cross-import from `core/events.py`.
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `UP040` forced PEP 695 `type` keyword (Story 1.4 codified it). `SIM105` (`contextlib.suppress`) does NOT fire in `runner.py` — the runner has no try/except cleanup paths because `engine.transaction()` (AC #7a) owns ROLLBACK.
- **mypy strict, zero `# type: ignore` in production code.** The `Callable[[SqliteStorageEngine], Awaitable[None]]` shape for `MigrationModule.up` is type-clean. `_validate_attr` uses `TypeVar` to narrow `object` → concrete `int`/`str`. `_validate_up` uses `cast(Callable[...], ...)` at the dynamic-import boundary after `inspect.iscoroutinefunction` confirms the runtime shape — documented inline per project-context.md:130.
- **Test file placement — `tests/unit/core/test_migration_runner.py`, flat under `unit/core/`.** No subdirectory, no `__init__.py` (Story 1.1 D1 carry-forward).
- **`pytest-asyncio` in `auto` mode — no `@pytest.mark.asyncio` decorator needed.** `async def test_...(...)` with mypy-strict-clean signatures.
- **`tmp_path` is the per-test scratch directory** — never `%LOCALAPPDATA%`.
- **No fixtures in `tests/conftest.py` added by this story.** Matches Story 1.4's precedent. The `_tmp_migrations_package` helper lives inside `test_migration_runner.py` as a regular function (not a pytest fixture), for tests that need synthetic migration package layouts.
- **`.gitignore` already covers `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `*.sqlite`, `*.sqlite3`.** Backup files (`*_YYYYMMDD_HHMMSS.db`) match `*.db` — already covered. No `.gitignore` change needed.
- **Commit message convention — Story 1.4 carry-forward:** terse, imperative, story ID prefix. Expected: `"Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)"`. Longer bodies than the header line are fine for non-trivial changes.
- **Storage engine `run_migrations` function-local import:** verified in Story 1.4's `test_core_isolation.py` — `_all_imports` uses `ast.walk` which descends into function bodies. The new import `from nova.core.storage.migrations.runner import MigrationRunner` will surface. Since its top-level is `nova` (already allowlisted via `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES`), this passes the existing tests. **Verify after implementation** — if a test fires, the fix is a narrow allowlist extension, not a code change.
- **Story 1.4 locked the migrations package location.** Line 330 of Story 1.4's Dev Notes: "Story 1.5 MUST place `runner.py` and `001_initial_schema.py` inside this directory; any drift (e.g., creating a top-level `nova.migrations`) is a Story 1.5 bug against a locked decision." Obey.

### Git Intelligence — last 5 commits

```
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
7278eb9 Story 1.3: event bus + typed event classes (core/events.py)
ac1790c Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)
1da5c45 Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)
80dba55 Story 1.0 code review: resolve 20 findings, mark done
```

- **Commit style:** terse, imperative, story ID prefix. Expected for this story: `"Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)"`.
- **Story 1.4 commit added ~400 lines production + ~500 lines tests (~900 total) across 4 modified files.** This story is comparable: `runner.py` (~200 lines), `001_initial_schema.py` (~40 lines), `test_migration_runner.py` (~300 lines), `test_migrations_integration.py` (~80 lines), `engine.py` delta (~15 lines), `test_core_isolation.py` delta (~25 lines), `migrations/__init__.py` delta (~5 lines) — ~665 lines net, ~7 files touched.
- **No prior `runner.py`, `001_initial_schema.py`, or `tests/integration/`.** This story is the first to author all three.

### Latest Tech Information (as of 2026-04-14)

- **Python 3.12.13** — `datetime.UTC` is the canonical UTC sentinel. `from datetime import UTC, datetime` is the idiomatic import. Do NOT use `datetime.timezone.utc` — deprecated name (still works, but ruff `UP017` may eventually flag it). Locked convention.
- **`importlib.resources.files(...)`** — stable API since Python 3.9, mature on 3.12. Returns a `Traversable` protocol instance. The `as_file` context manager is needed ONLY for reading bytes from resources inside wheels; for iterating directories, `files(pkg).iterdir()` works directly.
- **`importlib.import_module(name)`** — still the canonical dynamic import. Does NOT refresh if the module is already imported — OK for our case since each migration is imported exactly once per process lifetime (fresh process per dev iteration).
- **`shutil.copy2`** — stable, preserves metadata. Handles Windows paths correctly via `os.fspath` internally. No `follow_symlinks` gotcha for our use case (nova.db is a regular file, not a symlink).
- **`sqlite3.Connection.backup(...)`** — an alternative to `shutil.copy2` that uses SQLite's online backup API. Advantages: can back up a live DB without needing WAL checkpoint (the API handles WAL internally). Disadvantages: adds an async/executor dance (the call is blocking), and the WAL checkpoint approach is already proven in Story 1.4's pragmas. **Choose `shutil.copy2` + WAL checkpoint** for this story — simpler, less executor plumbing, and the online backup API benefit (live writes during backup) is not needed in single-writer T1. Document the alternative in a comment for future-Sayuj.
- **`PRAGMA wal_checkpoint(FULL)`** — returns a row of 3 integers `(busy, log, checkpointed)`. We don't use the return value, but the `engine.execute` path doesn't care — it's a DML-like statement that auto-commits. If future need surfaces (e.g., assert `busy == 0`), switch to `engine.fetchone` and read the row.
- **`re.fullmatch` vs `re.match`** — `fullmatch` is the correct tool for "does the entire string match" in Python 3.4+. We use it for the filename regex. Do NOT use `re.match` (matches from start but allows trailing characters) or `re.search` (matches anywhere) — both would silently accept malformed filenames.
- **`inspect.iscoroutinefunction`** — returns True for `async def` declarations. Does NOT return True for `@asyncio.coroutine`-decorated generators (deprecated in 3.8, removed in 3.11). Our migration modules use `async def`, so the check is precise.
- **`asyncio_mode = "auto"` (pytest-asyncio 1.3+)** — Story 1.1's setting. Every `async def test_...` is auto-collected as an async test. No decorator needed.
- **ruff 0.5+ (per Story 1.4):** the following rules are especially relevant:
  - `UP017`: `datetime.timezone.utc` → `datetime.UTC` — we use `UTC` directly.
  - `SIM105`: `try: x except E: pass` → `contextlib.suppress(E): x` — relevant inside `engine.transaction()` (which owns the ROLLBACK cleanup), NOT inside `runner.py`. The runner has no try/except for SIM105 to fire on.
  - `T20`: no `print()` — use `logger`.
  - `B008`: mutable default argument — `backup_dir: Path | None = None` is immutable-ish (None is a constant).
  - `UP040`: `typing.TypeAlias` → PEP 695 `type`. Not applicable here (no new type aliases in `runner.py`).
- **mypy 1.20.1 strict mode** — `Callable[[SqliteStorageEngine], Awaitable[None]]` for the `up` field. `_validate_attr` uses `TypeVar("_T")` so its return narrows from `object` → the concrete type at the call site. `_validate_up` uses `cast(Callable[...], ...)` at the dynamic-import boundary after `inspect.iscoroutinefunction` confirms the runtime shape. No `Any` anywhere.
- **No new dependencies in `pyproject.toml`.** Runner stdlib imports: `shutil`, `importlib`, `re`, `datetime`, `inspect`, `dataclasses`, `pathlib`, `logging`, `typing`, `collections.abc` — all stdlib, all on the `MIGRATION_RUNNER_ALLOWED_TOPLEVEL_MODULES` allowlist. The only first-party dependencies are `nova.core.exceptions.StorageError` and `nova.core.storage.engine.SqliteStorageEngine`.

### Project Structure Notes

- **Runner source:** `src/nova/core/storage/migrations/runner.py` — path pinned by architecture.md:1162, 1393; locked by Story 1.4 Dev Notes line 330.
- **Migration source:** `src/nova/core/storage/migrations/001_initial_schema.py` — same rationale.
- **Runner test:** `tests/unit/core/test_migration_runner.py` — flat under `unit/core/`, mirrors Story 1.4's `test_storage_engine.py` placement.
- **Integration test:** `tests/integration/test_migrations_integration.py` — first file in a new directory. Story 1.1's `testpaths = ["tests"]` should already discover it; confirm `pyproject.toml` at implementation time.
- **Modified engine:** `src/nova/core/storage/engine.py` — single method addition (`run_migrations`), single function-local import.
- **Modified isolation test:** `tests/unit/core/test_core_isolation.py` — follows the Story 1.3/1.4 pattern of dedicated module-import + allowlist frozenset + dedicated tests.
- **Modified package init:** `src/nova/core/storage/migrations/__init__.py` — docstring update only.
- **Modified pyproject:** `pyproject.toml` — marker registration only, if needed.
- **No new directories** — `tests/integration/` is a new directory but pytest creates no marker beyond placing the file.
- **Architecture.md divergence for this story:** architecture.md:1170–1174 shows `async def up(db: aiosqlite.Connection) -> None`. Story 1.5 overrides to `async def up(engine: SqliteStorageEngine) -> None` — the migration function receives the engine, not a raw connection. Rationale: keeps all DB calls on the engine's single-writer executor + error-translation path, matches the "no raw sqlite3 outside engine.py" rule, avoids introducing an `aiosqlite` dependency. Codify in `runner.py`'s module docstring and `001_initial_schema.py`'s docstring.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio (auto mode) + pytest-cov.
- **Unit tests** live in `tests/unit/core/`. 21 tests for the runner, following Story 1.4's flat layout.
- **Integration tests** live in `tests/integration/`. First integration test file in the repo.
- **mypy strict** applies to both trees. Annotate every fixture parameter: `tmp_path: Path`, `monkeypatch: pytest.MonkeyPatch`, `caplog: pytest.LogCaptureFixture` (used for at least one test that verifies the "no pending versions" INFO log).
- **tmp_path** is the exclusive DB/backup path root — never `%LOCALAPPDATA%`.
- **Each test constructs its own engine + runner.** The helper `_tmp_migrations_package(...)` is a regular function (not a pytest fixture) for creating synthetic migration layouts.
- **Test markers:** `@pytest.mark.integration` on the integration test; unit tests are unmarked.
- **Test runtime budget:** <1.5s total for all new tests. 22 tests × ~60ms each.
- **Coverage target:** 100% of `runner.py` (every branch of discovery, diff, backup, apply). 100% of `001_initial_schema.py` (trivial — one code path).
- **Deterministic clock:** `monkeypatch.setattr("nova.core.storage.migrations.runner._utc_now_iso", lambda: "2026-04-14T19:30:45+00:00")` freezes both the `applied_at` and backup filename. Every test that asserts on either uses this pattern.
- **Deterministic FS layout:** every test constructs its own `tmp_path / "nova.db"` and `tmp_path / "backups/"`. Zero shared state.
- **No network, no Win32, no Claude** — all four apply. The runner is a pure SQLite + stdlib-FS component.
- **Event-bus tests not relevant** — the runner does NOT emit events. Migrations are infrastructure, not domain events. Story 5.1 (transparency) may later expose migration history via Brain, but that's not an event bus concern.
- **Failure-path coverage — all six failure classes tested:**
  - File-system regex rejection (Test 2).
  - Attribute validation (Test 6, 7).
  - Version/filename mismatch (Test 4).
  - Duplicate version (Test 5).
  - Out-of-order migration (Test 16).
  - Mid-apply failure + ROLLBACK (Test 15).
  - Engine not started (Test 17).
  - Backup I/O failure — implicit via OSError path in `_backup_db`; not explicitly tested as a separate test because `shutil.copy2` failure is rare and hard to deterministically trigger without filesystem-level mocking. If future need surfaces, add a test that passes a read-only backup_dir and asserts `StorageError("backup failed")`.
- **Integration test checks architecture compliance** — `PRAGMA table_info` output must match the architecture.md:538–576 column specs character-for-character. This is the regression gate against drift between the architecture doc and the migration file.

### Critical Don't-Miss Rules (from project-context.md + architecture.md)

Carry-forward with rationale for this story:

- **"Schema migrations are numbered and backup-enforced."** (project-context.md:75, architecture.md:1178) — this story materializes the rule. Every migration is numbered (`NNN_` prefix, regex-enforced), every migration is preceded by a backup (`_backup_db` gate in `run()`).
- **"Back up before every schema-affecting migration. No exceptions, including local development."** (project-context.md:163) — the `_backup_db` gate fires unconditionally when pending set is non-empty AND the DB file has content. Tests lock this. Dev mode does NOT have a skip-backup flag; adding one would violate the rule.
- **"No raw SQL outside migrations."** (project-context.md:41) — the runner itself issues ONE raw DDL (`CREATE TABLE IF NOT EXISTS schema_version ...`) as the documented exception (AC #4). Every other schema change is authored inside a numbered migration file.
- **"Migrations run automatically on startup."** (project-context.md:162) — Story 1.5 provides the infrastructure (`run_migrations` method on engine). The `cli.py` wiring that calls it at startup is Story 1.10's job.
- **"Migration runner creates a timestamped backup of nova.db before applying any migration."** (architecture.md:1179) — implemented by `_backup_db` with `nova_YYYYMMDD_HHMMSS_ffffff.db` filename format (microsecond suffix added during review round to prevent same-second collision).
- **"Migrations are idempotent where possible."** (architecture.md:1181) — re-running `run()` with no pending versions is a no-op. The runner's `run()` is idempotent by design. Individual migrations are not required to be idempotent (the version tracking handles "already applied") but authors may choose to write them as `IF NOT EXISTS` for belt-and-suspenders (our `001_initial_schema.py` does NOT use `IF NOT EXISTS` — unnecessary given the version tracking).
- **"Down migrations are defined but not automatically run — they exist for manual recovery."** (architecture.md:1183) — Story 1.5 ships ZERO `down()` functions. The contract says "defined but not auto-run"; the T1 contract amendment (pinned by this story) is "not required at all." Add `down()` in a later story only when a manual recovery scenario surfaces.
- **"Migration state files are app-managed. Do not hand-edit migration version state except for deliberate recovery procedures."** (project-context.md:165) — tests verify `schema_version` row content. Production code writes it inside the migration transaction.
- **"Startup/setup/migration paths must be idempotent."** (project-context.md:166) — `run()` is idempotent; re-running applies no new migrations if already up-to-date.
- **"Timezone-aware datetimes only."** (project-context.md:45) — `_utc_now_iso()` returns UTC-aware ISO 8601. The integration test's `datetime.fromisoformat` + `tzinfo` assertion locks this.
- **"Timestamp helpers use the two-function clock pattern."** (project-context.md:46) — `_utc_now_iso` + `_default_timestamp` in `runner.py`. Tests monkeypatch `_utc_now_iso`.
- **"No sensitive content in exception messages."** (project-context.md:176) — "backup failed", "migration import failed: 002_add_x.py" (filename-only, not path), "out-of-order migration detected: version 2 pending but version 3 already applied" (version-number-only). No user data in any message.
- **"Never swallow `asyncio.CancelledError`."** (project-context.md:49) — `_apply_migration` catches `BaseException` to run `ROLLBACK`, then `raise` propagates unchanged. `CancelledError` reaches the caller.
- **"Use `pathlib.Path` for filesystem code."** (project-context.md:51) — all paths are `Path` objects; `str(path)` only at the `sqlite3.connect` / `shutil.copy2` boundary.
- **"No mutable default values."** (project-context.md:52) — `backup_dir: Path | None = None` uses `None` default, lazy-resolved inside `run()`.
- **"Broad exception catching only at top-level boundaries."** (project-context.md:53) — `_discover_migrations` catches `Exception` at the module-import boundary (documented justification: module-level user code can fail in arbitrary ways). Everywhere else catches specific classes (`OSError`, `ImportError`, `TypeError`, `BaseException` for cancellation-aware rollback).
- **"Structured logging."** (project-context.md:128) — `logger.info("migration applied", extra={"version": ..., "description": ...})`. No free-form debug strings.
- **"No `print()` anywhere."** (project-context.md:44) — all output via `logger`.
- **"Absolute imports only."** (project-context.md:43) — `from nova.core.exceptions import StorageError`, `from nova.core.storage.engine import SqliteStorageEngine`. Never relative.
- **"No `Any` in application code."** (project-context.md:47) — `Callable[[SqliteStorageEngine], Awaitable[None]]` covers the `up` field.
- **"Domain exceptions only."** (project-context.md:40) — all failures surface as `StorageError`. Engine-originated `StorageError` propagates unchanged.
- **"Tests use isolated temp paths by default, never `%LOCALAPPDATA%/nova/`."** (project-context.md:160) — `tmp_path` exclusively.
- **"Repo tree stays clean."** (project-context.md:159) — `.gitignore` already covers `*.db`; test `tmp_path` cleanup is automatic.
- **"Brain owns all SQLite tables."** (project-context.md:67) — the runner does NOT violate the rule. It is `core/` infrastructure, not a system. Its DDL creates the tables that Brain later owns and consumes. Brain (Story 3.1+) consumes the engine's query API; the runner is upstream of Brain.

### Cross-story impact (what depends on this story's primitives)

| Consumer story | Uses from this story | Why |
|---|---|---|
| 1.8 Audit logger | T1 `audit_log` table created here | AuditLogger writes audit_log rows via `engine.execute` against the schema this story establishes. If the table schema drifts (e.g., rename `action_type` to `action`), AuditLogger breaks. Integration test's PRAGMA table_info assertions are the regression gate. |
| 1.10 Composition root & CLI entrypoint | `await storage.run_migrations()` at startup | CLI startup sequence calls `run_migrations` after engine start, before Brain construction. Story 1.10 wires the call; this story provides the method. |
| 3.1 Brain session + seed persistence | T1 `sessions`, `memory_items` tables | Brain's SQLite adapter reads/writes these tables. Schema drift breaks Brain. |
| 4.1 Eyes Win32 context capture → 4.3 Workspace snapshots | T1 `workspace_snapshots` table | Workspace snapshots are persisted here. FK from `session_id` to `sessions(id)` is enforced (tested in `test_fk_constraint_enforced_after_migration`). |
| 5.1 Transparency command | All T1 product tables read-only | Transparency query reads from all four tables. |
| 5.2 Selective forget | All T1 product tables | Deletion propagation spans sessions, workspace_snapshots, memory_items. |
| 5.3 Audit trail inspection | T1 `audit_log` table | Inspection reads the audit_log via Brain. |
| 5.5 SQLite corruption recovery | Backup directory structure + `nova_YYYYMMDD_HHMMSS_ffffff.db` filename pattern | Corruption recovery restores from a backup file; it MUST be able to find backups by globbing `backups/nova_*.db`. |
| 5.6 Backup/restore user-facing flow | Backup directory + `shutil.copy2` pattern | User-facing backup command copies nova.db to backups/ with a user-supplied or timestamp name. Reuses the directory structure established here. |
| Every future schema change | `NNN_*.py` migration convention, `async def up(engine)` signature, `VERSION`/`DESCRIPTION` attributes | Every new migration follows the contract pinned here. |

**Nine downstream stories** consume Story 1.5's primitives. The integration test's column-by-column PRAGMA assertions are the architecture-compliance regression gate.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5: Migration Runner & Initial Schema](../planning-artifacts/epics.md) — canonical AC, lines 726–751.
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4: SQLite Storage Engine](../planning-artifacts/epics.md) — upstream dependency, lines 706–724.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives, lines 617–900.
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Schema](../planning-artifacts/architecture.md) — lines 527–585, T1 SQLite schema (authoritative for `001_initial_schema.py`).
- [Source: _bmad-output/planning-artifacts/architecture.md#Migration Convention](../planning-artifacts/architecture.md) — lines 1158–1184, migration rules (authoritative for runner contract).
- [Source: _bmad-output/planning-artifacts/architecture.md#Composition Root Convention](../planning-artifacts/architecture.md) — lines 1059–1102, `await storage.run_migrations()` usage (Story 1.10's concern; this story provides the method).
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 1391–1394, migrations package path.
- [Source: _bmad-output/planning-artifacts/architecture.md#Runtime User Data Directory](../planning-artifacts/architecture.md) — lines 1434–1452, `%LOCALAPPDATA%/nova/backups/` path (production backup destination).
- [Source: _bmad-output/planning-artifacts/architecture.md#T1 Skeleton](../planning-artifacts/architecture.md) — line 1517, `core/storage/migrations/runner.py` and `001_initial_schema.py` listed as T1-active.
- [Source: _bmad-output/planning-artifacts/architecture.md#Systems Ownership](../planning-artifacts/architecture.md) — line 1497, Memory & Persistence (FR19-24) owned by the migration runner + storage engine + Brain adapter.
- [Source: _bmad-output/planning-artifacts/prd.md#NFR18](../planning-artifacts/prd.md) — line 708, "Schema migrations must be non-destructive — automatic backup before migration, rollback path if migration fails, no data loss under any migration scenario". THIS STORY's binding NFR.
- [Source: _bmad-output/planning-artifacts/prd.md#FR24](../planning-artifacts/prd.md) — line 623, "System can create automatic timestamped backups of the memory database before schema migrations". Directly satisfied by `_backup_db`.
- [Source: _bmad-output/planning-artifacts/prd.md#FR57](../planning-artifacts/prd.md) — line 674, "User can trigger a self-update command that checks for new versions, backs up the memory database, and applies updates with visible migration notes". T1 ships the infrastructure (backup-before-migrate); user-facing `nova self-update` is NOT T1.
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 40–53 (Python/Architecture), 75 (numbered/backup), 128 (structured logging), 158–166 (workflow: user data, test isolation, idempotency, migrations-auto-on-startup), 176 (no sensitive content in exceptions).
- [Source: _bmad-output/implementation-artifacts/1-4-sqlite-storage-engine.md](./1-4-sqlite-storage-engine.md) — `SqliteStorageEngine` contract, error-translation pattern, test conventions, opaque-message rule, migrations package location lock (Dev Notes line 330).
- [Source: _bmad-output/implementation-artifacts/1-3-event-bus-and-typed-event-definitions.md](./1-3-event-bus-and-typed-event-definitions.md) — `_utc_now_iso` + `_default_timestamp` two-function clock pattern (lines 48–107 of `core/events.py`). Reused here verbatim.
- [Source: _bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md](./1-2-domain-exceptions-and-shared-types.md) — `StorageError`, `cause=` + `from err` chaining contract.
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md](./1-1-project-scaffolding-and-package-setup.md) — D1 (no `tests/__init__.py`), D3 (mypy widened to tests), D4 (`from __future__ import annotations`), D5 (`.gitignore` `*.db*`), `asyncio_mode = "auto"`, testpaths convention.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `StorageError` + `NovaError` chaining contract.
- [Source: src/nova/core/storage/engine.py](../../src/nova/core/storage/engine.py) — `SqliteStorageEngine` public surface consumed by the runner.
- [Source: src/nova/core/events.py](../../src/nova/core/events.py) — `_utc_now_iso` / `_default_timestamp` reference implementation.
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `SnapshotType`, `ActionType`, `MemoryCategory` enums whose string values must match the `snapshot_type`, `action_type`, and `category` columns created here.
- [Source: src/nova/core/storage/__init__.py](../../src/nova/core/storage/__init__.py) — NOT touched by this story (runner is NOT re-exported from `core.storage` package level).
- [Source: src/nova/core/storage/migrations/__init__.py](../../src/nova/core/storage/migrations/__init__.py) — placeholder to replace with the canonical docstring.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — AST-level adapter-isolation pattern to extend; Story 1.3's `EVENTS_ALLOWED_TOPLEVEL_MODULES` + Story 1.4's `STORAGE_ENGINE_ALLOWED_TOPLEVEL_MODULES` are the precedents for `MIGRATION_RUNNER_ALLOWED_TOPLEVEL_MODULES`.
- [Source: tests/unit/core/test_storage_engine.py](../../tests/unit/core/test_storage_engine.py) — test-writing conventions (async signatures, `tmp_path`, `pytest.raises(StorageError, match=...)`, `__cause__` assertions).
- [Source: tests/conftest.py](../../tests/conftest.py) — NOT touched by this story.
- [Source: pyproject.toml](../../pyproject.toml) — pytest markers registration location (check before modifying).
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — no open Story 1.5–targeted deferrals. Five Story 1.4 deferrals (SqlParams footgun, get_running_loop drift, corrupt-DB pragma, DDL+transaction, Windows long-path) — NONE apply to Story 1.5's new surface; they remain assigned to their documented future stories.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- **Engine RED phase** — appended 8 new tests to `tests/unit/core/test_storage_engine.py` (6 transaction + 2 run_migrations delegator). `uv run pytest -k "transaction or run_migrations"` failed at collection with `AttributeError: 'SqliteStorageEngine' object has no attribute 'transaction'` — confirmed the new contracts don't exist yet.
- **Engine GREEN phase** — added `transaction()` async context manager + `_tx_lock` + `_in_transaction` flag + two `_*_sync_no_commit` static helpers + one-line dispatch branches in `execute`/`executemany`. Also added `run_migrations()` thin delegator. All 7 transaction tests + the `run_migrations_requires_started` guard test passed first try; `run_migrations_delegates_to_runner` deferred until runner.py existed.
- **Runner GREEN phase** — wrote `runner.py` + `001_initial_schema.py` + `migrations/__init__.py` docstring update. `MigrationRunner` constructor stays I/O-free; `run()` bootstraps `schema_version`, discovers via `importlib.resources.files`, diffs, backs up (when applied set non-empty), applies inside `engine.transaction()`. `_apply_migration` is the atomicity-critical path — single context manager wraps DDL + INSERT, rollback handled by `transaction()`.
- **Runner test failures (4 of 21 first run)** —
  1. `test_run_applies_001_initial_schema_fresh` — sqlite auto-creates `sqlite_sequence` for AUTOINCREMENT columns; test was strict-matching the table set. Fixed by filtering `sqlite_%` internals.
  2. `test_run_skips_backup_when_no_pending` + `test_backup_skipped_on_fresh_db` + `test_backup_filename_is_deterministic_with_monkeypatched_clock` — original backup gate was `db_path.stat().st_size >= 100`, but `engine.start()` enables WAL mode which writes ~96 bytes of header upfront. The threshold gate fired even on fresh installs. **Refactored:** backup now triggers only when `applied` set is non-empty (i.e., there's prior schema state worth protecting). This is precise, doesn't depend on FS artifacts, and matches the architecture intent at architecture.md:1179. Removed the unused `_BACKUP_MIN_DB_SIZE` constant.
- **Lint cleanup** — autofixed unused `import contextlib` (refactor away from explicit ROLLBACK call), unused `import sys` + `from typing import Any`, unsorted imports in test file. Two long lines in test_migration_runner.py wrapped manually.
- **mypy strict failures (3 of 37 files)** —
  1. `_validate_attr` returned `object` because `expected_type: type` had no parameterization. Added `_T = TypeVar("_T")` + `expected_type: type[_T]) -> _T` so `version: int` and `description: str` narrow correctly.
  2. `_validate_up` had unused `# type: ignore` comments (mypy's narrowing on `getattr` made them redundant). Replaced with `cast` at the dynamic-import boundary, citing project-context.md:130.
  3. Required adding `typing` to `MIGRATION_RUNNER_ALLOWED_TOPLEVEL_MODULES` for `TypeVar`/`cast`. Consistent with Story 1.4's storage-engine allowlist which already includes `typing`.
- **Hatchling wheel packaging verification** — `uv build && python -c "..."` confirmed `001_initial_schema.py` ships in both editable install and built wheel. Default `[tool.hatch.build.targets.wheel] packages = ["src/nova"]` includes numeric-prefix `.py` files without exclusion.
- **Final verify** — `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest` all pass. **338 tests pass in 3.60s** (302 pre-Story-1.5 + 36 added: 21 runner unit + 4 runner-isolation + 8 engine extension + 1 integration). `git status` shows only intentional source/test/doc changes — no `__pycache__`, no `*.db`, no `backups/`, no caches.
- **Spec divergence from story file** — none material. The story said `_apply_migration` would catch `BaseException` to issue ROLLBACK and re-raise; `engine.transaction()` now owns that flow, so `_apply_migration` simplifies to one `async with` block. The story spec's `import contextlib` in the runner's import list (AC #13) is no longer needed — the rollback responsibility moved to the engine. Stale-import cleanup happened via ruff autofix.

### Completion Notes List

- **All 20 ACs satisfied.** Every task and subtask checkbox marked [x].
- **Engine extensions (AC #7a + #17a/b):** `transaction()` async context manager rejects nested transactions, releases the lock under exceptions including `CancelledError`, propagates the original exception unchanged after best-effort ROLLBACK. `run_migrations()` is a 5-line thin delegator with function-local import to break the engine↔runner circularity. Eight new engine tests cover commit, rollback, cancel, nested-rejection, fetch-inside-tx, lock-release, requires-started, and delegation. Story 1.4's 48 pre-existing engine tests still pass (zero regressions).
- **MigrationRunner (AC #1–#7b):** Single public `run()` entrypoint. Discovery uses `importlib.resources.files` + `re.fullmatch` against the pinned `\d{3}_[a-z][a-z0-9_]*\.py` regex. Validates `VERSION: int`, `DESCRIPTION: str`, `up: async callable` per module; cross-checks VERSION against filename prefix; rejects duplicates. Bootstraps `schema_version` (the documented exception to "no raw DDL outside migrations"). Out-of-order check fires when `applied` non-empty AND `min(pending) < max(applied)`. Backup gate fires when `applied` non-empty (the precise "we have prior state" signal — supersedes the size-threshold approach). Backup uses `PRAGMA wal_checkpoint(FULL)` + `shutil.copy2` with timestamp-based filename routed through the monkeypatchable `_utc_now_iso` clock.
- **Atomicity contract proven (AC #7b):** `_apply_migration` runs the migration's DDL + the `schema_version` insert inside one `engine.transaction()`. `test_apply_is_atomic_on_midway_failure` injects a synthetic migration that creates a table then raises mid-`up()` — after the exception, the doomed table does NOT exist (rollback fired) and `schema_version` has no row for the failed version. Re-running attempts the same migration again. The atomicity gap that broke the original spec has been closed by the new engine `transaction()` primitive.
- **001_initial_schema.py (AC #8):** Five `CREATE TABLE` statements via `engine.execute` — sessions → workspace_snapshots → memory_items → audit_log (FK-dependency order; schema_version bootstrapped by the runner). Column types/constraints match architecture.md:538–576 character-for-character (locked by the integration test's `PRAGMA table_info` parametrized assertions). No indexes, no `STRICT`, no `WITHOUT ROWID`, no `down()`.
- **Two-function clock pattern (AC #11):** `_utc_now_iso` + `_default_timestamp` declared locally in `runner.py` per project-context.md:46. Backup filename and `schema_version.applied_at` both flow through `_utc_now_iso`, so a single `monkeypatch.setattr` gives deterministic test output. Locked by `test_backup_filename_is_deterministic_with_monkeypatched_clock`.
- **Isolation tests (AC #14):** Migration runner gets its own forbidden-set check (full `FORBIDDEN_TOPLEVEL_MODULES`, no `sqlite3` carve-out — the runner uses the engine, never raw sqlite3) plus its own allowlist + dedicated nova-subpackage prefix tests. Two existing parametrize lists (`test_no_relative_imports`, `test_no_dynamic_imports_of_forbidden_modules`) extended to include `migration_runner_module`.
- **Integration test (AC #16):** First integration test in the repo, at `tests/integration/test_migrations_integration.py`. Validates every column of every T1 product table against `_EXPECTED_COLUMNS` via `PRAGMA table_info` — the regression gate against architecture/migration drift. Also validates `schema_version` columns and the canonical description string.
- **Hatchling packaging (AC verified during Task 1):** `uv build` produces a wheel that includes all three migration package files (`__init__.py`, `001_initial_schema.py`, `runner.py`). No exclusion rules to add. Story 1.1 left hatchling at sensible defaults.
- **No `pyproject.toml` changes needed (AC #18):** `integration` marker was already registered by Story 1.1. `testpaths = ["tests"]` already covers the new `tests/integration/` directory. No new dependencies — everything used is stdlib (`shutil`, `importlib`, `re`, `datetime`, `inspect`, `dataclasses`, `pathlib`, `typing`).
- **No `.gitignore` changes (AC #19):** Story 1.1 already covers `*.db`, `*.db-wal`, `*.db-shm`. Backup files (`nova_*.db`) match the existing `*.db` pattern. Test backup directories live in `tmp_path` and are auto-cleaned by pytest.
- **Cross-story commitments held:** Story 1.4's auto-commit engine surface is preserved untouched outside of `transaction()` blocks (zero Story 1.4 test regressions). Story 1.3's two-function clock pattern reused locally per project-context.md:46. Story 1.2's `StorageError` chaining contract preserved (every internal raise uses `from err`).
- **Quality gates final:** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit 0. `All checks passed!` / `37 files already formatted` / `Success: no issues found in 37 source files` / `338 passed in 3.60s`. Zero `# type: ignore` in production code; one narrow `cast()` in `_validate_up` documented inline at the dynamic-import boundary.
- **Repo tree clean:** `git status` shows only intentional changes — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm`, or `backups/` directories staged.

### File List

- `src/nova/core/storage/migrations/runner.py` (new) — `MigrationRunner` class + `MigrationModule` frozen dataclass + `_FILENAME_RE` + `_utc_now_iso` / `_default_timestamp` clock pair + private helpers (`_discover_migrations`, `_validate_attr`, `_validate_up`, `_validate_no_duplicates`, `_backup_db`, `_backup_timestamp`, `_apply_migration`). ~250 lines including docstrings.
- `src/nova/core/storage/migrations/001_initial_schema.py` (new) — `VERSION = 1`, `DESCRIPTION`, `async def up(engine)` issuing four `CREATE TABLE` statements (sessions, workspace_snapshots, memory_items, audit_log) per architecture.md:538–576. ~70 lines.
- `src/nova/core/storage/migrations/__init__.py` (modified) — replaced Story 1.1 placeholder docstring with the canonical one citing the runner's contract.
- `src/nova/core/storage/engine.py` (modified) — added `AsyncIterator` to `collections.abc` import, `from contextlib import asynccontextmanager`. New `__init__` state: `_tx_lock`, `_in_transaction`. New methods: `transaction()` async context manager (BEGIN IMMEDIATE / COMMIT / ROLLBACK with cancellation handling and lock release in `finally`), `run_migrations()` delegator with function-local `MigrationRunner` import. New `@staticmethod` helpers: `_execute_sync_no_commit`, `_executemany_sync_no_commit`. Dispatch branches in `execute` / `executemany` select the no-commit path when inside a transaction.
- `tests/unit/core/test_storage_engine.py` (modified) — 8 new tests appended: `test_transaction_context_manager_commits_on_success`, `test_transaction_context_manager_rolls_back_on_exception`, `test_transaction_rolls_back_on_cancellation`, `test_nested_transaction_rejected`, `test_fetch_inside_transaction_sees_own_writes`, `test_transaction_releases_lock_on_exception`, `test_run_migrations_requires_started`, `test_run_migrations_delegates_to_runner`. Test count 48 → 56.
- `tests/unit/core/test_migration_runner.py` (new) — 21 tests covering discovery, validation failures, sorted output, file/version mismatch, duplicate-version helper, missing-attribute, non-async-up rejection, schema_version bootstrap, fresh apply, idempotent re-run, backup creation when pending, no-backup-when-no-pending, fresh-DB skip, deterministic backup filename, atomic apply on midway failure, out-of-order rejection, requires-started guard, engine delegation match, ISO 8601 UTC `applied_at`, exact description string, FK enforcement post-migration. ~480 lines including the synthetic-package `_write_pkg` helper.
- `tests/integration/test_migrations_integration.py` (new) — first integration test in the repo. `test_fresh_db_applies_001_initial_schema_and_tables_match_architecture` asserts the table set + every column of every T1 product table via `PRAGMA table_info` against the `_EXPECTED_COLUMNS` constant (per architecture.md:538–576) + the `schema_version` columns + the canonical description. `@pytest.mark.integration` marker. ~110 lines.
- `tests/unit/core/test_core_isolation.py` (modified) — added `import nova.core.storage.migrations.runner as migration_runner_module`, `MIGRATION_RUNNER_ALLOWED_TOPLEVEL_MODULES` frozenset (12 modules including `typing` for `TypeVar`/`cast`). Extended two parametrize lists (`test_no_relative_imports`, `test_no_dynamic_imports_of_forbidden_modules`). Four new dedicated tests: `test_migration_runner_forbidden_imports` (full denylist, no sqlite3 carve-out), `test_migration_runner_imports_within_allowlist`, `test_migration_runner_does_not_import_nova_adapters_or_systems`, `test_migration_runner_does_not_dynamically_import_nova_adapters_or_systems`. Test count 24 → 28.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story lifecycle transitions: ready-for-dev → in-progress → review.

### Review Findings

**Code review pass — 2026-04-14**, three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor on Sonnet). 33 raw findings → 17 unique after merge → 12 patches + 3 deferrals + 2 dismissed.

**Patches (unchecked — fix before marking done):**

- [x] [Review][Patch][H] **`_in_transaction` flag race — concurrent execute() can commit a foreign transaction mid-flight.** [`engine.py:execute,executemany,transaction`] Task A reads `self._in_transaction = False` and captures `sync_fn = _execute_sync` (which calls `conn.commit()`). Task A yields. Task B enters `transaction()`, acquires `_tx_lock`, submits `BEGIN IMMEDIATE`, sets `_in_transaction = True`. Task A resumes, dispatches its work to the worker thread via `run_in_executor` — the worker queues it FIFO behind Task B's BEGIN. Worker runs BEGIN, then runs Task A's `_execute_sync`, which commits Task B's transaction silently. Task B's "ROLLBACK" then fails because there's no live transaction. Spec docs cross-task reads as "undefined" but doesn't enforce — the fix is unambiguous. **Fix:** acquire `self._tx_lock` for the duration of every `execute`/`executemany` call (cheap — single-worker pool already serializes physically; the lock just prevents the dispatch-decision race). Reported by Blind+Edge.
- [x] [Review][Patch][H] **Backup-gate documentation drift — runner.py module docstring + 2 test comments still cite the rejected `stat().st_size >= 100` rule.** [`runner.py:20-26`, `test_migration_runner.py:1309,1349`] Implementation correctly uses `if applied:`, but the module-level docstring claims "the DB file has `stat().st_size >= 100`" which is the explicitly-rejected rule. Two test comments also reason about file-size thresholds. **Fix:** rewrite the docstring's Backup bullet to "when applied set is non-empty"; rewrite the two test comments to reference `applied = {}` / `applied = {1}` instead of byte counts. Reported by Blind+Edge+Auditor.
- [x] [Review][Patch][M] **`CancelledError` during ROLLBACK can replace the original exception.** [`engine.py:transaction` rollback arm] `contextlib.suppress(Exception)` does NOT catch `CancelledError` (it's `BaseException`). If the outer task is cancelled while awaiting the ROLLBACK `run_in_executor` future, the new `CancelledError` propagates out of the `suppress` block and overrides the original exception that triggered the rollback. Tracing/debug breaks. **Fix:** either widen suppression to `BaseException` or wrap the ROLLBACK await in `asyncio.shield(...)` so cancellation can't interrupt it. Reported by Blind+Edge.
- [x] [Review][Patch][M] **`PRAGMA wal_checkpoint(FULL)` busy-flag not checked — partial-WAL backups slip through.** [`runner.py:_backup_db`] The PRAGMA returns `(busy, log, checkpointed)` — if `busy != 0` the checkpoint failed to fully fold the WAL into the main DB and the subsequent `shutil.copy2` produces a backup missing the most recent committed data. `engine.execute()` discards return rows. **Fix:** use `await self._engine.fetchone("PRAGMA wal_checkpoint(FULL)")` and raise `StorageError("backup failed: WAL checkpoint incomplete")` if `row[0] != 0`. Reported by Blind+Edge.
- [x] [Review][Patch][M] **`_backup_db.mkdir()` raises raw `OSError` instead of translated `StorageError`.** [`runner.py:_backup_db`] `backup_dir.mkdir(parents=True, exist_ok=True)` raises `NotADirectoryError`/`PermissionError` (both `OSError` subclasses) when `backup_dir` is an existing regular file or unwritable. The current `try/except OSError` only wraps `shutil.copy2`. The composition root expects all persistence failures to surface as `StorageError`. **Fix:** wrap the `mkdir` call in its own `try/except OSError as err: raise StorageError("backup failed") from err`. Reported by Edge.
- [x] [Review][Patch][M] **`_discover_migrations.iterdir()` failure escapes uncaught.** [`runner.py:_discover_migrations`] The `try/except (ModuleNotFoundError, TypeError)` only wraps `resource_files()`. A `PermissionError` or zip-import-incompatible failure during `package_files.iterdir()` propagates as a raw `OSError`. **Fix:** extend the `try` block to include the iteration loop and translate any iteration error to `StorageError("migrations package iteration failed")`. Reported by Edge.
- [x] [Review][Patch][M] **`run_migrations()` import failure surfaces as raw `ImportError`.** [`engine.py:run_migrations`] Function-local `from nova.core.storage.migrations.runner import MigrationRunner` will raise `ImportError`/`ModuleNotFoundError` if the migrations subpackage is broken. The composition-root contract expects `StorageError`. **Fix:** wrap the import in `try/except ImportError as err: raise StorageError("migration runner unavailable") from err`. Reported by Edge.
- [x] [Review][Patch][M] **Backup filename collision when two `run()` calls fire within the same wall-clock second.** [`runner.py:_backup_timestamp`] Format `nova_YYYYMMDD_HHMMSS.db` has 1-second resolution. A same-second second backup silently overwrites the first via `shutil.copy2`. **Fix:** append microseconds — change `strftime("%Y%m%d_%H%M%S")` to `strftime("%Y%m%d_%H%M%S_%f")`. Update test 14 accordingly (frozen clock string needs `+00:00` retained for the strftime path). Reported by Edge.
- [x] [Review][Patch][M] **Migration `up()` calling `engine.transaction()` raises confusing `StorageError("nested transaction")` with no documented contract.** [`runner.py:_apply_migration`, `MigrationModule.up`] A migration author who naturally wraps DDL in `engine.transaction()` triggers the nested-transaction guard with no diagnostic guidance. The pipeline then permanently fails on that version. **Fix:** add a `Raises`/`Note` line to `MigrationModule.up`'s contract docstring (and the runner module docstring) stating "`up()` MUST NOT call `engine.transaction()` — the runner already wraps every migration in a transaction." Reported by Blind+Edge.
- [x] [Review][Patch][M] **AC #17b ordering violation: `run_migrations()` placed AFTER `transaction()` in `engine.py`.** [`engine.py:223,285`] Spec AC #17b says: "Placed **after `close()`**, before `transaction()`." Actual order is `close()` → `transaction()` → `run_migrations()`. **Fix:** move the `run_migrations()` method to appear immediately after `close()`, before `transaction()`. Reported by Auditor.
- [x] [Review][Patch][M] **AC #16: integration test uses for-loop instead of `@pytest.mark.parametrize`.** [`test_migrations_integration.py`] Spec AC #16 explicitly required parametrize for per-table assertions so each table failure surfaces as a distinct test node. Current single-test-with-loop stops at the first failure. **Fix:** split the inner table-info assertions into a separate `@pytest.mark.parametrize("table,expected", list(_EXPECTED_COLUMNS.items()))` test function. Keep the table-set assertion + schema_version row assertion in the existing test. Reported by Auditor.
- [x] [Review][Patch][L] **Test 19 vacuous assertion: `dt.utcoffset() == (datetime.now(UTC) - datetime.now(UTC))`.** [`test_migration_runner.py:test_schema_version_applied_at_is_iso8601_utc`] Two live `now()` calls differ by microseconds, not exactly zero in principle. Spec AC #15 Test 19 prescribes `dt.utcoffset() == timedelta(0)`. **Fix:** add `timedelta` to the `from datetime import ...` line and replace the assertion. Reported by Blind+Auditor.

**Deferred (already filed in `deferred-work.md`):**

- [x] [Review][Defer][L] **`_FILENAME_RE` only matches 3-digit prefixes — silent skip for v1000+.** [`runner.py:_FILENAME_RE`] T1 will not reach 1000 migrations; deferred to post-T1.
- [x] [Review][Defer][L] **`test_transaction_rolls_back_on_cancellation` timing-dependent (`asyncio.sleep(0.05)` race).** [`test_storage_engine.py`] CI flakiness risk under load; deferred to test-hygiene pass.
- [x] [Review][Defer][L] **`_write_pkg` leaves synthetic packages in `sys.modules` after teardown.** [`test_migration_runner.py:_write_pkg`] Test-only; per-test counter prevents collision in current usage.

**Dismissed (2):**

- `_validate_no_duplicates` vs `_validate_discovered_no_duplicates` name — spec is internally inconsistent (Task 1 vs artifact list); current name matches the artifact list and tests.
- `001_initial_schema.py` 73 lines vs spec ~50 — soft guard; overage is informative module docstring, not bloat.

## Change Log

- 2026-04-14: Story 1.5 implementation complete. Added `engine.transaction()` async context manager + `engine.run_migrations()` delegator (closes the multi-statement atomicity gap exposed during story authoring). Added `MigrationRunner` with discovery / diff / backup / atomic-apply flow. Added `001_initial_schema.py` with the four T1 product tables. 338 tests pass (302 → 338, +36 net). Status: in-progress → review.
- 2026-04-14: Code review (3 layers, Sonnet) — 33 raw → 17 unique → 12 patches + 3 deferrals + 2 dismissed. Findings logged in Review Findings section.
- 2026-04-14: Review-fix round — all 12 patches applied. Highlights: replaced `_in_transaction: bool` with `_tx_owner: asyncio.Task` for race-free dispatch; execute/executemany now acquire `_tx_lock` when caller is not the transaction owner; ROLLBACK shielded against cancellation; backup gate filename gained microsecond precision; wal_checkpoint busy-flag now checked; `_backup_db.mkdir()`, `_discover_migrations.iterdir()`, and `run_migrations()` import all translate failures to `StorageError`; integration test split into top-level + 4 parametrized per-table nodes; Test 19 uses `timedelta(0)`. Quality gates clean; **342 tests pass in 2.54s** (338 → 342, +4 from integration parametrize splitting). Status: review → done.
- 2026-04-14: Residual review (user pass) — 1 M + 1 L. **M (contract gap):** AC #2 requires `DESCRIPTION` to be ≤100 chars and single-line; runner only validated non-empty. Added two checks in `_discover_migrations` (`len > 100` → `"description too long"`; embedded `\n`/`\r` → `"must be single-line"`) plus two regression tests. **L (spec drift):** story doc still cited `nova_YYYYMMDD_HHMMSS.db` filename in 5 places after the M-8 microsecond patch — updated to `nova_YYYYMMDD_HHMMSS_ffffff.db` with the `_000000` example. **344 tests pass in 2.58s** (+2 for new DESCRIPTION validators).
