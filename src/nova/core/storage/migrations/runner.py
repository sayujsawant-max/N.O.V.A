"""SQLite migration runner — discovers, diffs, backs up, and applies migrations.

Numbered migration scripts (``NNN_short_name.py``) under this package are
discovered, validated, and applied in ascending version order by
:class:`MigrationRunner`. Backup-before-migrate is enforced for any DB
file with non-trivial content; the runner uses
:meth:`SqliteStorageEngine.transaction` so each migration's DDL and its
``schema_version`` insert commit or roll back atomically.

Architecture conventions owned by this module:

- **Migration file shape** — every file declares ``VERSION: int``,
  ``DESCRIPTION: str``, and ``async def up(engine: SqliteStorageEngine)``.
  Module-level I/O is forbidden (discovery imports every module).
- **Apply atomicity** — DDL + ``schema_version`` insert run inside one
  ``engine.transaction()``. On failure, the transaction rolls back and
  the runner propagates the exception unchanged.
- **Backup** — when the pending set is non-empty AND the **applied
  set is non-empty** (i.e., there is prior schema state worth
  protecting), a timestamped copy lands in
  ``backups/nova_YYYYMMDD_HHMMSS_ffffff.db`` BEFORE any migration
  runs. Fresh installs (applied set empty) skip the backup.
- **schema_version bootstrap** — the runner itself issues the
  ``CREATE TABLE IF NOT EXISTS schema_version (...)`` statement once
  per ``run()``. This is the documented exception to the "no raw DDL
  outside migrations" rule (project-context.md:41).
- **Migration `up()` contract** — ``up(engine)`` MUST NOT call
  ``engine.transaction()``. The runner already wraps every migration
  in a transaction (``_apply_migration``). A nested call raises
  ``StorageError("nested transaction")`` and the migration cannot
  succeed on retry. Use plain ``engine.execute(...)`` calls inside
  ``up()`` and let the runner own the transaction boundary.

Architecture divergence owned by this story
-------------------------------------------
``architecture.md`` lines 1170–1174 sketch migration files using
``aiosqlite.Connection``. Story 1.5 overrides that pattern: ``up()``
receives the project's ``SqliteStorageEngine``, not a raw connection.
This keeps every DB call on the engine's single-writer executor and
inside its error-translation net, matching the project-wide "no raw
sqlite3 outside engine.py" rule (Story 1.4).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files as resource_files
from pathlib import Path
from typing import TypeVar, cast

from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine

logger = logging.getLogger("nova.core.storage.migrations.runner")


def _utc_now_iso() -> str:
    """Canonical clock — single source of UTC ISO 8601 timestamps for this module.

    Tests monkeypatch this name to freeze time. Backup filenames and
    ``schema_version.applied_at`` both flow through this call so a single
    monkeypatch controls both.
    """
    return datetime.now(UTC).isoformat()


def _default_timestamp() -> str:
    """Factory indirection per project-context.md:46 two-function clock pattern.

    Body looks up ``_utc_now_iso`` at call time, so monkeypatching
    ``_utc_now_iso`` propagates without re-binding any
    ``field(default_factory=...)`` references.
    """
    return _utc_now_iso()


_FILENAME_RE: re.Pattern[str] = re.compile(r"(?P<num>\d{3})_[a-z][a-z0-9_]*\.py")
_T = TypeVar("_T")


@dataclass(frozen=True)
class MigrationModule:
    """A discovered migration: version, description, source filename, up coroutine.

    Internal type — not re-exported. Constructed by ``_discover_migrations``
    after validating each candidate module.

    ``up`` MUST NOT call ``engine.transaction()`` — see module docstring.
    The runner wraps every migration in its own transaction context.
    """

    version: int
    description: str
    filename: str
    up: Callable[[SqliteStorageEngine], Awaitable[None]]


class MigrationRunner:
    """Discovers and applies numbered migrations against a started engine.

    See module docstring for the contract. Single public entrypoint:
    :meth:`run`. Construct with the engine and (optionally) a custom
    migrations package path or backup directory; both default to the
    production locations.
    """

    def __init__(
        self,
        engine: SqliteStorageEngine,
        migrations_package: str = "nova.core.storage.migrations",
        backup_dir: Path | None = None,
    ) -> None:
        self._engine: SqliteStorageEngine = engine
        self._migrations_package: str = migrations_package
        self._backup_dir: Path | None = backup_dir

    async def run(self) -> list[int]:
        """Discover, diff, optionally back up, and apply pending migrations.

        Returns the sorted list of versions applied in this call (empty if
        no migrations were pending). Idempotent — re-running with no
        pending versions is a safe no-op.
        """
        # Bootstrap schema_version. Documented exception to the
        # "no raw DDL outside migrations" rule — chicken/egg.
        await self._engine.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "    version     INTEGER PRIMARY KEY,"
            "    applied_at  TEXT NOT NULL,"
            "    description TEXT"
            ")"
        )
        rows = await self._engine.fetchall("SELECT version FROM schema_version ORDER BY version")
        applied: set[int] = {int(row["version"]) for row in rows}

        discovered = self._discover_migrations()
        pending = [m for m in discovered if m.version not in applied]

        if not pending:
            logger.info("migrations: no pending versions")
            return []

        # Out-of-order guard: a freshly added migration whose version is
        # less than the highest already-applied version is a reorder hazard.
        if applied and min(m.version for m in pending) < max(applied):
            min_pending = min(m.version for m in pending)
            max_applied = max(applied)
            raise StorageError(
                f"out-of-order migration detected: version {min_pending} pending "
                f"but version {max_applied} already applied"
            )

        # Backup BEFORE applying anything, only if there are prior applied
        # migrations to protect. Fresh installs (applied set empty) skip
        # the backup — there's no prior schema state worth a copy. This
        # rule is precise (it doesn't depend on file size or WAL artifacts)
        # and matches the architecture intent at architecture.md:1179.
        if applied:
            db_path = self._engine._db_path  # noqa: SLF001 — runner is engine-coupled by design
            await self._backup_db(db_path)

        # Apply in ascending order.
        applied_versions: list[int] = []
        for migration in sorted(pending, key=lambda m: m.version):
            await self._apply_migration(migration)
            applied_versions.append(migration.version)
        return applied_versions

    def _discover_migrations(self) -> list[MigrationModule]:
        """Scan the migrations package, validate each candidate, return sorted list."""
        try:
            package_files = resource_files(self._migrations_package)
        except (ModuleNotFoundError, TypeError) as err:
            raise StorageError(f"migrations package not found: {self._migrations_package}") from err

        try:
            entries = list(package_files.iterdir())
        except (OSError, NotADirectoryError) as err:
            # iterdir() can fail on zip-imported packages or unreadable
            # directories. Translate so callers see only StorageError.
            raise StorageError(
                f"migrations package iteration failed: {self._migrations_package}"
            ) from err

        discovered: list[MigrationModule] = []
        for entry in entries:
            name = entry.name
            match = _FILENAME_RE.fullmatch(name)
            if match is None:
                # Not a migration file — skip silently. __init__.py, runner.py,
                # README, and any other non-matching name lands here.
                continue
            filename_version = int(match.group("num"))
            module_dotted = f"{self._migrations_package}.{name[:-3]}"
            try:
                module = importlib.import_module(module_dotted)
            except Exception as err:  # noqa: BLE001 — module body can raise anything
                raise StorageError(f"migration import failed: {name}") from err

            version = self._validate_attr(module, name, "VERSION", int)
            description = self._validate_attr(module, name, "DESCRIPTION", str)
            up = self._validate_up(module, name)

            if not description.strip():
                raise StorageError(f"migration description missing: {name}")
            if len(description) > 100:
                raise StorageError(
                    f"migration description too long: {name} declares "
                    f"{len(description)} chars (max 100)"
                )
            if "\n" in description or "\r" in description:
                raise StorageError(f"migration description must be single-line: {name}")
            if version != filename_version:
                raise StorageError(
                    f"migration file/version mismatch: {name} declares "
                    f"VERSION={version} but filename prefix is {filename_version:03d}"
                )
            discovered.append(
                MigrationModule(version=version, description=description, filename=name, up=up)
            )

        self._validate_no_duplicates(discovered)
        return sorted(discovered, key=lambda m: m.version)

    @staticmethod
    def _validate_attr(
        module: object, filename: str, attr_name: str, expected_type: type[_T]
    ) -> _T:
        if not hasattr(module, attr_name):
            raise StorageError(f"migration {filename} missing required attribute: {attr_name}")
        value = getattr(module, attr_name)
        if not isinstance(value, expected_type):
            raise StorageError(
                f"migration {filename} attribute {attr_name} must be "
                f"{expected_type.__name__}, got {type(value).__name__}"
            )
        return value

    @staticmethod
    def _validate_up(
        module: object, filename: str
    ) -> Callable[[SqliteStorageEngine], Awaitable[None]]:
        if not hasattr(module, "up"):
            raise StorageError(f"migration {filename} missing required attribute: up")
        up = getattr(module, "up")  # noqa: B009 — getattr keeps mypy quiet on dynamic module attr
        if not inspect.iscoroutinefunction(up):
            raise StorageError(f"migration {filename} attribute up must be an async function")
        # iscoroutinefunction narrowed `up` to a coroutine function; sqlite3-stub-style
        # cast: trust the runtime check at the dynamic-import boundary
        # (project-context.md:130 — narrow Any at integration boundaries).
        return cast(Callable[[SqliteStorageEngine], Awaitable[None]], up)

    @staticmethod
    def _validate_no_duplicates(discovered: list[MigrationModule]) -> None:
        seen: dict[int, str] = {}
        for m in discovered:
            if m.version in seen:
                raise StorageError(
                    f"duplicate migration version: {m.version} in "
                    f"{seen[m.version]} and {m.filename}"
                )
            seen[m.version] = m.filename

    async def _backup_db(self, db_path: Path) -> Path:
        """Checkpoint the WAL, copy nova.db to backups/nova_<ts>.db, return the backup path.

        Forces WAL contents into the main DB via ``PRAGMA wal_checkpoint(FULL)``
        before copying, so the single-file backup captures the full state
        even with WAL mode active. Verifies the checkpoint actually
        completed (the PRAGMA returns ``(busy, log, checkpointed)`` —
        ``busy != 0`` means WAL frames are still uncheckpointed and the
        backup would be incomplete).
        """
        # Force WAL contents into the main DB and verify completion.
        # If `busy != 0`, the checkpoint could not obtain the required
        # locks and the main DB file is missing recent WAL frames —
        # backing up now would silently lose data.
        checkpoint_row = await self._engine.fetchone("PRAGMA wal_checkpoint(FULL)")
        if checkpoint_row is not None and checkpoint_row[0] != 0:
            raise StorageError("backup failed: WAL checkpoint incomplete")

        backup_dir = (
            self._backup_dir if self._backup_dir is not None else db_path.parent / "backups"
        )
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            # NotADirectoryError, PermissionError — both OSError subclasses.
            # Surface as StorageError so the composition-root contract holds.
            raise StorageError("backup failed") from err

        backup_path = backup_dir / f"nova_{self._backup_timestamp()}.db"
        try:
            shutil.copy2(db_path, backup_path)
        except OSError as err:
            raise StorageError("backup failed") from err
        logger.info("migration backup created", extra={"path": str(backup_path)})
        return backup_path

    @staticmethod
    def _backup_timestamp() -> str:
        """Backup-filename timestamp routed through the monkeypatchable clock.

        Parses the ISO-8601 string from ``_utc_now_iso`` and reformats it
        as ``YYYYMMDD_HHMMSS_ffffff`` (microsecond precision) so two
        backups in the same wall-clock second do not collide. A single
        ``_utc_now_iso`` monkeypatch controls both
        ``schema_version.applied_at`` and backup filenames.
        """
        iso = _utc_now_iso()
        # Tolerate either trailing "Z" or "+00:00" forms; fromisoformat
        # accepts +00:00 directly in 3.12 and Z since 3.11.
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y%m%d_%H%M%S_%f")

    async def _apply_migration(self, migration: MigrationModule) -> None:
        """Apply one migration inside engine.transaction() — atomic DDL + version row."""
        async with self._engine.transaction():
            await migration.up(self._engine)
            await self._engine.execute(
                "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
                (migration.version, _utc_now_iso(), migration.description),
            )
        # Log AFTER commit succeeds — never lie about durability.
        logger.info(
            "migration applied",
            extra={"version": migration.version, "description": migration.description},
        )
