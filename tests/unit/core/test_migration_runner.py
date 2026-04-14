"""Unit tests for MigrationRunner — discovery, diff, backup, atomic apply.

Covers the runner contract per Story 1.5 ACs #1–#7b plus #11–#12. Each
test constructs its own engine on a per-test ``tmp_path`` scratch DB
(never %LOCALAPPDATA%/nova/) and tears down explicitly. Synthetic
migration packages live in ``tmp_path`` so tests don't touch the real
``nova.core.storage.migrations`` package except for the integration-style
checks that validate the production ``001_initial_schema.py``.
"""

from __future__ import annotations

import sqlite3
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.storage.migrations.runner import MigrationModule, MigrationRunner

# --- Helpers ------------------------------------------------------------------


def _write_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> str:
    """Create a temporary Python package on sys.path; return its dotted name.

    Each entry in ``files`` is a filename → source-text pair. The package
    gets a unique name per call so multiple synthetic packages can coexist
    inside one test without import-cache collisions.
    """
    pkg_root = tmp_path / "syntheticpkgs"
    pkg_root.mkdir(exist_ok=True)
    pkg_name = f"synth_pkg_{abs(hash(tmp_path)) % (10**9)}_{len(list(pkg_root.iterdir()))}"
    pkg_dir = pkg_root / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    for name, body in files.items():
        (pkg_dir / name).write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg_root))
    return pkg_name


_NOOP_MIGRATION = """\
from __future__ import annotations
from nova.core.storage.engine import SqliteStorageEngine

VERSION = {version}
DESCRIPTION = "{description}"

async def up(engine: SqliteStorageEngine) -> None:
    pass
"""


def _noop_source(version: int, description: str = "noop") -> str:
    return _NOOP_MIGRATION.format(version=version, description=description)


# --- Test 1: discovery against the production package ------------------------


async def test_discovery_finds_001_initial_schema(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "discovery.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine)
        discovered = runner._discover_migrations()  # noqa: SLF001 — test-internal access
        assert len(discovered) == 1
        m = discovered[0]
        assert m.version == 1
        assert m.filename == "001_initial_schema.py"
        assert (
            m.description
            == "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"
        )
        # `up` is an async function (coroutine function).
        import inspect

        assert inspect.iscoroutinefunction(m.up)
    finally:
        await engine.close()


# --- Test 2: filename regex rejects malformed names --------------------------


async def test_discovery_filename_regex_rejects_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_good.py": _noop_source(1, "good"),
            # All of these must be ignored silently — not migrations.
            "1_initial.py": _noop_source(1, "no-zero-pad"),
            "001-dashes.py": _noop_source(1, "dashes"),
            "abc_nothing.py": _noop_source(1, "no-digits"),
            "runner.py": "# not a migration\n",
        },
    )
    engine = SqliteStorageEngine(tmp_path / "regex.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        discovered = runner._discover_migrations()  # noqa: SLF001
        names = [m.filename for m in discovered]
        assert names == ["001_good.py"]
    finally:
        await engine.close()


# --- Test 3: discovery returns sorted ----------------------------------------


async def test_discovery_returns_sorted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "003_third.py": _noop_source(3, "third"),
            "001_first.py": _noop_source(1, "first"),
            "002_second.py": _noop_source(2, "second"),
        },
    )
    engine = SqliteStorageEngine(tmp_path / "sorted.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        discovered = runner._discover_migrations()  # noqa: SLF001
        assert [m.version for m in discovered] == [1, 2, 3]
    finally:
        await engine.close()


# --- Test 4: filename/version mismatch ---------------------------------------


async def test_discovery_rejects_version_filename_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {"001_misnamed.py": _noop_source(7, "wrong version int")},
    )
    engine = SqliteStorageEngine(tmp_path / "mismatch.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="file/version mismatch"):
            runner._discover_migrations()  # noqa: SLF001
    finally:
        await engine.close()


# --- Test 5: duplicate version (helper-level) --------------------------------


async def test_validate_no_duplicates_rejects_collision(tmp_path: Path) -> None:
    async def fake_up(engine: SqliteStorageEngine) -> None:
        return None

    discovered = [
        MigrationModule(version=1, description="a", filename="001_a.py", up=fake_up),
        MigrationModule(version=1, description="b", filename="002_b.py", up=fake_up),
    ]
    with pytest.raises(StorageError, match="duplicate migration version: 1"):
        MigrationRunner._validate_no_duplicates(discovered)  # noqa: SLF001


# --- Test 6: missing required attribute --------------------------------------


async def test_discovery_rejects_missing_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_no_desc.py": (
                "from __future__ import annotations\n"
                "from nova.core.storage.engine import SqliteStorageEngine\n"
                "VERSION = 1\n"
                "async def up(engine: SqliteStorageEngine) -> None:\n"
                "    pass\n"
            )
        },
    )
    engine = SqliteStorageEngine(tmp_path / "missing.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="missing required attribute: DESCRIPTION"):
            runner._discover_migrations()  # noqa: SLF001
    finally:
        await engine.close()


# --- Test 6b: DESCRIPTION length cap (per AC #2) -----------------------------


async def test_discovery_rejects_oversize_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_too_long.py": (
                "from __future__ import annotations\n"
                "from nova.core.storage.engine import SqliteStorageEngine\n"
                "VERSION = 1\n"
                f'DESCRIPTION = "{"x" * 101}"\n'
                "async def up(engine: SqliteStorageEngine) -> None:\n"
                "    pass\n"
            )
        },
    )
    engine = SqliteStorageEngine(tmp_path / "toolong.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="description too long"):
            runner._discover_migrations()  # noqa: SLF001
    finally:
        await engine.close()


# --- Test 6c: DESCRIPTION must be single-line (per AC #2) --------------------


async def test_discovery_rejects_multiline_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_multiline.py": (
                "from __future__ import annotations\n"
                "from nova.core.storage.engine import SqliteStorageEngine\n"
                "VERSION = 1\n"
                'DESCRIPTION = "first line\\nsecond line"\n'
                "async def up(engine: SqliteStorageEngine) -> None:\n"
                "    pass\n"
            )
        },
    )
    engine = SqliteStorageEngine(tmp_path / "multiline.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="must be single-line"):
            runner._discover_migrations()  # noqa: SLF001
    finally:
        await engine.close()


# --- Test 7: up must be async -------------------------------------------------


async def test_discovery_rejects_non_async_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_sync_up.py": (
                "from __future__ import annotations\n"
                "from nova.core.storage.engine import SqliteStorageEngine\n"
                "VERSION = 1\n"
                "DESCRIPTION = 'sync up — should be rejected'\n"
                "def up(engine: SqliteStorageEngine) -> None:\n"
                "    pass\n"
            )
        },
    )
    engine = SqliteStorageEngine(tmp_path / "syncup.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="must be an async function"):
            runner._discover_migrations()  # noqa: SLF001
    finally:
        await engine.close()


# --- Test 8: schema_version table is bootstrapped ----------------------------


async def test_run_creates_schema_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Empty migrations package — no files at all means run() returns []
    # but schema_version still exists for future runs.
    pkg = _write_pkg(tmp_path, monkeypatch, {})
    engine = SqliteStorageEngine(tmp_path / "bootstrap.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        applied = await runner.run()
        assert applied == []
        rows = await engine.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert len(rows) == 1
    finally:
        await engine.close()


# --- Test 9: run applies 001_initial_schema fresh ----------------------------


async def test_run_applies_001_initial_schema_fresh(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "fresh.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine)
        applied = await runner.run()
        assert applied == [1]

        # All five product tables exist (sqlite_% are SQLite internals).
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

        # schema_version contains exactly one row for version 1.
        version_rows = await engine.fetchall("SELECT version, description FROM schema_version")
        assert len(version_rows) == 1
        assert version_rows[0]["version"] == 1
        assert (
            version_rows[0]["description"]
            == "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"
        )
    finally:
        await engine.close()


# --- Test 10: idempotent re-run ----------------------------------------------


async def test_run_is_idempotent_on_rerun(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "idem.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine)
        first = await runner.run()
        second = await runner.run()
        assert first == [1]
        assert second == []
        # Still exactly one row in schema_version.
        rows = await engine.fetchall("SELECT version FROM schema_version")
        assert len(rows) == 1
    finally:
        await engine.close()


# --- Test 11: backup created when pending exists -----------------------------


async def test_run_creates_backup_when_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_dir = tmp_path / "backups"
    engine = SqliteStorageEngine(tmp_path / "backup.db")
    await engine.start()
    try:
        # Apply 001 first so the applied set becomes {1}. Non-empty applied
        # set is what triggers the backup gate on the next migration.
        runner = MigrationRunner(engine, backup_dir=backup_dir)
        await runner.run()
        await engine.execute(
            "INSERT INTO sessions (started_at) VALUES (?)",
            ("2026-04-14T00:00:00+00:00",),
        )

        # Now add a synthetic 002 in a separate package.
        pkg = _write_pkg(tmp_path, monkeypatch, {"002_noop.py": _noop_source(2, "noop")})
        # The new runner sees its own pkg only (so its discovered set is {2}),
        # but reads schema_version from the same DB → applied set is {1} (from
        # the prior production 001 apply). Pending = {2}, applied = {1} → backup
        # fires before applying 002.
        runner2 = MigrationRunner(engine, migrations_package=pkg, backup_dir=backup_dir)
        applied = await runner2.run()
        assert applied == [2]

        backup_files = list(backup_dir.glob("nova_*.db"))
        assert len(backup_files) == 1
        # Backup name matches the YYYYMMDD_HHMMSS_ffffff pattern (microsecond
        # precision prevents same-second collision).
        import re as _re

        assert _re.fullmatch(r"nova_\d{8}_\d{6}_\d{6}\.db", backup_files[0].name)
    finally:
        await engine.close()


# --- Test 12: no backup when nothing pending ---------------------------------


async def test_run_skips_backup_when_no_pending(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    engine = SqliteStorageEngine(tmp_path / "nopending.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, backup_dir=backup_dir)
        await runner.run()  # apply 001 (fresh install: applied={} → no backup)
        await runner.run()  # idempotent re-run: no pending → return early before backup gate
        backup_files = list(backup_dir.glob("nova_*.db")) if backup_dir.exists() else []
        # First run: applied={} (fresh install) → backup gate skips.
        # Second run: pending={} → returns early before backup is considered.
        assert backup_files == []
    finally:
        await engine.close()


# --- Test 13: fresh DB skips backup ------------------------------------------


async def test_backup_skipped_on_fresh_db(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    engine = SqliteStorageEngine(tmp_path / "freshdb.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, backup_dir=backup_dir)
        applied = await runner.run()
        assert applied == [1]
        backup_files = list(backup_dir.glob("nova_*.db")) if backup_dir.exists() else []
        assert backup_files == []
    finally:
        await engine.close()


# --- Test 14: backup filename deterministic with monkeypatched clock --------


async def test_backup_filename_is_deterministic_with_monkeypatched_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_dir = tmp_path / "backups"
    engine = SqliteStorageEngine(tmp_path / "clock.db")
    await engine.start()
    try:
        # First run: apply production 001 to give the DB content.
        await MigrationRunner(engine, backup_dir=backup_dir).run()
        await engine.execute(
            "INSERT INTO sessions (started_at) VALUES (?)",
            ("2026-04-14T00:00:00+00:00",),
        )

        # Now freeze the clock and trigger a second migration that exercises backup.
        monkeypatch.setattr(
            "nova.core.storage.migrations.runner._utc_now_iso",
            lambda: "2026-04-14T19:30:45+00:00",
        )
        pkg = _write_pkg(tmp_path, monkeypatch, {"002_clock.py": _noop_source(2, "clock-test")})
        await MigrationRunner(engine, migrations_package=pkg, backup_dir=backup_dir).run()

        backups = sorted(backup_dir.glob("nova_*.db"))
        assert len(backups) == 1
        # Frozen clock string "2026-04-14T19:30:45+00:00" parses to microsecond=0,
        # so the deterministic filename is "...193045_000000.db".
        assert backups[0].name == "nova_20260414_193045_000000.db"
    finally:
        await engine.close()


# --- Test 15: atomicity on midway failure ------------------------------------


async def test_apply_is_atomic_on_midway_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_explodes.py": (
                "from __future__ import annotations\n"
                "from nova.core.storage.engine import SqliteStorageEngine\n"
                "VERSION = 1\n"
                "DESCRIPTION = 'creates a table then raises'\n"
                "async def up(engine: SqliteStorageEngine) -> None:\n"
                "    await engine.execute('CREATE TABLE doomed (val TEXT)')\n"
                "    raise RuntimeError('boom mid-up')\n"
            )
        },
    )
    engine = SqliteStorageEngine(tmp_path / "atomic.db")
    await engine.start()
    try:
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(RuntimeError, match="boom"):
            await runner.run()

        # (a) doomed table was rolled back.
        rows = await engine.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='doomed'"
        )
        assert rows == []

        # (b) schema_version has no row for version 1.
        version_rows = await engine.fetchall("SELECT version FROM schema_version")
        assert version_rows == []

        # (c) re-run attempts the same migration again (still pending).
        with pytest.raises(RuntimeError, match="boom"):
            await runner.run()
    finally:
        await engine.close()


# --- Test 16: out-of-order migration -----------------------------------------


async def test_apply_out_of_order_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _write_pkg(
        tmp_path,
        monkeypatch,
        {
            "001_first.py": _noop_source(1, "first"),
            "002_second.py": _noop_source(2, "second"),
            "003_third.py": _noop_source(3, "third"),
        },
    )
    engine = SqliteStorageEngine(tmp_path / "ooo.db")
    await engine.start()
    try:
        # Pre-populate schema_version with {1, 3} via raw inserts after the
        # bootstrap.
        await MigrationRunner(engine, migrations_package=pkg).run()  # applies 1,2,3
        # Now wipe version 2 to simulate "version 2 is missing in applied set".
        await engine.execute("DELETE FROM schema_version WHERE version = 2")
        # Re-run — version 2 is now pending but version 3 is already applied.
        runner = MigrationRunner(engine, migrations_package=pkg)
        with pytest.raises(StorageError, match="out-of-order migration detected"):
            await runner.run()
    finally:
        await engine.close()


# --- Test 17: requires started engine ----------------------------------------


async def test_run_requires_started_engine(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "notstarted.db")
    runner = MigrationRunner(engine)
    with pytest.raises(StorageError, match="not started"):
        await runner.run()


# --- Test 18: engine.run_migrations delegates --------------------------------


async def test_engine_run_migrations_delegates(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "delegate2.db")
    await engine.start()
    try:
        # Direct runner call vs engine delegator should match.
        applied_via_engine = await engine.run_migrations()
        # Re-running via runner directly returns [] (idempotent).
        applied_via_runner = await MigrationRunner(engine).run()
        assert applied_via_engine == [1]
        assert applied_via_runner == []
    finally:
        await engine.close()


# --- Test 19: applied_at is ISO 8601 UTC -------------------------------------


async def test_schema_version_applied_at_is_iso8601_utc(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "iso.db")
    await engine.start()
    try:
        await MigrationRunner(engine).run()
        row = await engine.fetchone("SELECT applied_at FROM schema_version WHERE version = 1")
        assert row is not None
        dt = datetime.fromisoformat(row["applied_at"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)
    finally:
        await engine.close()


# --- Test 20: schema_version description is exact ----------------------------


async def test_schema_version_description_is_exact(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "desc.db")
    await engine.start()
    try:
        await MigrationRunner(engine).run()
        row = await engine.fetchone("SELECT description FROM schema_version WHERE version = 1")
        assert row is not None
        assert (
            row["description"]
            == "Initial T1 schema: sessions, workspace_snapshots, memory_items, audit_log"
        )
    finally:
        await engine.close()


# --- Test 21: FK constraint enforced -----------------------------------------


async def test_fk_constraint_enforced_after_migration(tmp_path: Path) -> None:
    engine = SqliteStorageEngine(tmp_path / "fk.db")
    await engine.start()
    try:
        await MigrationRunner(engine).run()
        with pytest.raises(StorageError) as excinfo:
            await engine.execute(
                "INSERT INTO workspace_snapshots "
                "(session_id, captured_at, snapshot_type, workspace_data) "
                "VALUES (?, ?, ?, ?)",
                (999, "2026-04-14T00:00:00+00:00", "startup", "{}"),
            )
        assert isinstance(excinfo.value.__cause__, sqlite3.IntegrityError)
    finally:
        await engine.close()
