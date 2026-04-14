"""T1 initial schema migration.

Creates the four T1 product tables — sessions, workspace_snapshots,
memory_items, audit_log — per architecture.md lines 527–585.

Order is load-bearing: ``sessions`` is created FIRST because
``workspace_snapshots`` and ``memory_items`` declare FOREIGN KEY references
to it. Foreign-key enforcement is enabled by the storage engine's
``PRAGMA foreign_keys = ON`` (Story 1.4 AC #3), so the create order
matters for clean DDL even though SQLite doesn't validate FK targets at
``CREATE TABLE`` time.

The ``schema_version`` table is NOT created here — it is bootstrapped by
``MigrationRunner.run()`` before this migration executes.
"""

from __future__ import annotations

from nova.core.storage.engine import SqliteStorageEngine

VERSION: int = 1
DESCRIPTION: str = "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"


async def up(engine: SqliteStorageEngine) -> None:
    await engine.execute(
        """
        CREATE TABLE sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            ended_at    TEXT,
            mode_name   TEXT,
            seed_text   TEXT,
            summary     TEXT,
            is_complete INTEGER DEFAULT 0
        )
        """
    )
    await engine.execute(
        """
        CREATE TABLE workspace_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id     INTEGER NOT NULL REFERENCES sessions(id),
            captured_at    TEXT NOT NULL,
            snapshot_type  TEXT NOT NULL,
            workspace_data TEXT NOT NULL
        )
        """
    )
    await engine.execute(
        """
        CREATE TABLE memory_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER REFERENCES sessions(id),
            category        TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            relevance_score REAL DEFAULT 1.0
        )
        """
    )
    await engine.execute(
        """
        CREATE TABLE audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target      TEXT,
            result      TEXT NOT NULL,
            details     TEXT
        )
        """
    )
