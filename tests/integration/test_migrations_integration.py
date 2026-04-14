"""Integration test — fresh DB applies 001_initial_schema and tables match architecture.

The first integration test in the repo. Exercises the real
``SqliteStorageEngine`` + ``MigrationRunner`` against a tmp_path DB,
then verifies every column of every T1 table matches architecture.md
lines 538–576 character-for-character via ``PRAGMA table_info``.

This is the regression gate against drift between the architecture doc
and ``001_initial_schema.py`` — if a column is renamed, retyped, or
loses a default, this test fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nova.core.storage.engine import SqliteStorageEngine

# Expected schema per architecture.md:538–576. Each tuple is
# (column_name, declared_type, notnull_flag, default_value).
# notnull_flag: 1 = NOT NULL, 0 = nullable.
# default_value: the literal default expression as SQLite reports it,
# or None if no default.
_EXPECTED_COLUMNS: dict[str, list[tuple[str, str, int, str | None]]] = {
    "sessions": [
        ("id", "INTEGER", 0, None),
        ("started_at", "TEXT", 1, None),
        ("ended_at", "TEXT", 0, None),
        ("mode_name", "TEXT", 0, None),
        ("seed_text", "TEXT", 0, None),
        ("summary", "TEXT", 0, None),
        ("is_complete", "INTEGER", 0, "0"),
    ],
    "workspace_snapshots": [
        ("id", "INTEGER", 0, None),
        ("session_id", "INTEGER", 1, None),
        ("captured_at", "TEXT", 1, None),
        ("snapshot_type", "TEXT", 1, None),
        ("workspace_data", "TEXT", 1, None),
    ],
    "memory_items": [
        ("id", "INTEGER", 0, None),
        ("session_id", "INTEGER", 0, None),
        ("category", "TEXT", 1, None),
        ("content", "TEXT", 1, None),
        ("created_at", "TEXT", 1, None),
        ("relevance_score", "REAL", 0, "1.0"),
    ],
    "audit_log": [
        ("id", "INTEGER", 0, None),
        ("timestamp", "TEXT", 1, None),
        ("action_type", "TEXT", 1, None),
        ("target", "TEXT", 0, None),
        ("result", "TEXT", 1, None),
        ("details", "TEXT", 0, None),
    ],
}


@pytest.mark.integration
async def test_fresh_db_applies_001_and_creates_expected_tables(tmp_path: Path) -> None:
    """Top-level checks: fresh-install apply, table set, schema_version row."""
    engine = SqliteStorageEngine(tmp_path / "nova.db")
    await engine.start()
    try:
        applied = await engine.run_migrations()
        assert applied == [1]

        # All five tables exist (filter SQLite internals like sqlite_sequence).
        rows = await engine.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        names = {r["name"] for r in rows}
        assert names == {
            "audit_log",
            "memory_items",
            "schema_version",
            "sessions",
            "workspace_snapshots",
        }

        # schema_version columns.
        sv_info = await engine.fetchall("PRAGMA table_info(schema_version)")
        sv_actual = [(r["name"], r["type"], r["notnull"]) for r in sv_info]
        assert sv_actual == [
            ("version", "INTEGER", 0),
            ("applied_at", "TEXT", 1),
            ("description", "TEXT", 0),
        ]

        # Exactly one row in schema_version, with the canonical description.
        version_rows = await engine.fetchall("SELECT version, description FROM schema_version")
        assert len(version_rows) == 1
        assert version_rows[0]["version"] == 1
        assert (
            version_rows[0]["description"]
            == "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"
        )
    finally:
        await engine.close()


@pytest.mark.integration
@pytest.mark.parametrize("table,expected", list(_EXPECTED_COLUMNS.items()))
async def test_t1_table_columns_match_architecture(
    tmp_path: Path, table: str, expected: list[tuple[str, str, int, str | None]]
) -> None:
    """Per-table column-by-column check via PRAGMA table_info.

    Parametrized so each table failure surfaces as a distinct test node
    (vs a single test that stops at the first mismatch). Locks every
    column name/type/nullability/default against architecture.md:538-576.
    """
    engine = SqliteStorageEngine(tmp_path / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()
        info = await engine.fetchall(f"PRAGMA table_info({table})")
        actual = [(r["name"], r["type"], r["notnull"], r["dflt_value"]) for r in info]
        assert actual == expected, f"Schema drift in {table}: {actual!r} != {expected!r}"
    finally:
        await engine.close()
