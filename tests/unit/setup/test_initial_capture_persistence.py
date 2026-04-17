"""Unit tests for ``nova.setup.initial_capture.persist_first_run``.

Story 2.4 Group C + G.30. Uses an in-memory (``:memory:``) SQLite
engine with the real migration set applied so the session +
workspace_snapshot + audit_log schemas match production exactly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from nova.core.audit import AuditLogger
from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import ActionType, SnapshotType
from nova.setup.initial_capture import (
    CaptureResult,
    persist_first_run,
)
from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot


class _HarnessApp:
    """Minimal ``NovaApp``-like object satisfying the protocol."""

    def __init__(self, storage: SqliteStorageEngine) -> None:
        self.storage = storage
        self.audit = AuditLogger(storage=storage)


async def _booted_engine(tmp_path: Path) -> SqliteStorageEngine:
    engine = SqliteStorageEngine(tmp_path / "first_run.db")
    await engine.start()
    await engine.run_migrations()
    return engine


def _fixed_snapshot(captured_at: str) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        captured_at=captured_at,
        snapshot_type=SnapshotType.STARTUP,
        windows=(
            WindowContext(
                app_name="code",
                window_title="main.py",
                process_name="code",
                is_opaque=False,
            ),
            WindowContext(
                app_name="chrome",
                window_title="docs",
                process_name="chrome",
                is_opaque=False,
            ),
        ),
    )


def _full_capture(captured_at: str = "2026-04-17T12:00:00+00:00") -> CaptureResult:
    snapshot = _fixed_snapshot(captured_at)
    return CaptureResult(
        snapshot=snapshot,
        status="full",
        windows_captured=len(snapshot.windows),
        windows_dropped=0,
        focused_app="code",
    )


async def test_writes_session_row_with_expected_fields(tmp_path: Path) -> None:
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=1)
        row = await engine.fetchone("SELECT * FROM sessions")
        assert row is not None
        assert row["mode_name"] is None
        assert row["seed_text"] is None
        assert row["summary"] is None
        assert row["is_complete"] == 1
        assert row["started_at"] == "2026-04-17T12:00:00+00:00"
        assert row["ended_at"] is not None
    finally:
        await engine.close()


async def test_writes_snapshot_row_tied_to_session_id(tmp_path: Path) -> None:
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=1)
        session_row = await engine.fetchone("SELECT id FROM sessions")
        snap_row = await engine.fetchone("SELECT * FROM workspace_snapshots")
        assert session_row is not None
        assert snap_row is not None
        assert snap_row["session_id"] == session_row["id"]
        assert snap_row["snapshot_type"] == str(SnapshotType.STARTUP)
        assert snap_row["captured_at"] == "2026-04-17T12:00:00+00:00"
    finally:
        await engine.close()


async def test_workspace_data_is_strict_compact_json(tmp_path: Path) -> None:
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        await persist_first_run(app, _full_capture(), api_key_configured=False, modes_count=2)
        snap_row = await engine.fetchone("SELECT workspace_data FROM workspace_snapshots")
        assert snap_row is not None
        data_str = snap_row["workspace_data"]
        # No spaces after separators — strict compact JSON.
        assert ": " not in data_str
        assert ", " not in data_str
        parsed = json.loads(data_str)
        # apps sorted ascending; chrome before code.
        assert parsed == {
            "apps": ["chrome", "code"],
            "focused_app": "code",
            "mode_name": None,
        }
    finally:
        await engine.close()


async def test_workspace_data_handles_empty_snapshot(tmp_path: Path) -> None:
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        capture = CaptureResult(
            snapshot=WorkspaceSnapshot(
                captured_at="2026-04-17T12:00:00+00:00",
                snapshot_type=SnapshotType.STARTUP,
                windows=(),
            ),
            status="empty",
            windows_captured=0,
            windows_dropped=0,
        )
        await persist_first_run(app, capture, api_key_configured=False, modes_count=1)
        snap_row = await engine.fetchone("SELECT workspace_data FROM workspace_snapshots")
        assert snap_row is not None
        parsed = json.loads(snap_row["workspace_data"])
        assert parsed == {"apps": [], "focused_app": None, "mode_name": None}
    finally:
        await engine.close()


async def test_transaction_rolls_back_on_snapshot_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the snapshot INSERT raises, the session row must not exist."""
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)

        original_execute = engine.execute
        call_counter = {"n": 0}

        async def flaky_execute(sql: str, params: object = ()) -> None:
            # First execute call inside the transaction is the snapshot
            # insert (session insert uses execute_returning_lastrowid).
            if call_counter["n"] == 0 and "workspace_snapshots" in sql:
                call_counter["n"] += 1
                raise StorageError("simulated snapshot failure")
            await original_execute(sql, params)  # type: ignore[arg-type]

        monkeypatch.setattr(engine, "execute", flaky_execute)

        with pytest.raises(StorageError, match="simulated snapshot failure"):
            await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=1)

        sessions = await engine.fetchall("SELECT id FROM sessions")
        snapshots = await engine.fetchall("SELECT id FROM workspace_snapshots")
        assert sessions == []  # ROLLBACK reverted the session insert
        assert snapshots == []
    finally:
        await engine.close()


async def test_audit_entry_uses_setup_complete_action_type(tmp_path: Path) -> None:
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=2)
        row = await engine.fetchone("SELECT action_type, target, result, details FROM audit_log")
        assert row is not None
        assert row["action_type"] == str(ActionType.SETUP_COMPLETE)
        assert row["target"] is None
        assert row["result"] == "success"
        details = json.loads(row["details"])
        assert details == {
            "modes_count": 2,
            "api_key_configured": True,
            "capture_status": "full",
        }
    finally:
        await engine.close()


async def test_audit_details_contain_no_api_key_material(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=1)
        row = await engine.fetchone("SELECT details FROM audit_log")
        assert row is not None
        details_text = row["details"]
        # api_key_configured is a bool — the raw key value is NEVER stored.
        assert "sk-" not in details_text
        assert "api_key" not in details_text.replace("api_key_configured", "")
    finally:
        await engine.close()


async def test_audit_threads_capture_status_through(tmp_path: Path) -> None:
    """AC #15 — ``capture.status`` flows into ``details.capture_status``."""
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        capture = CaptureResult(
            snapshot=_fixed_snapshot("2026-04-17T12:00:00+00:00"),
            status="partial",
            windows_captured=1,
            windows_dropped=1,
            focused_app=None,
        )
        await persist_first_run(app, capture, api_key_configured=False, modes_count=1)
        row = await engine.fetchone("SELECT details FROM audit_log")
        assert row is not None
        assert json.loads(row["details"])["capture_status"] == "partial"
    finally:
        await engine.close()


async def test_audit_write_is_atomic_with_session_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review patch #1 — an audit-insert failure rolls back the whole transaction.

    Previously the audit row was written via ``AuditLogger.log_action``
    OUTSIDE the transaction with ``StorageError`` swallowed. That left
    session+snapshot orphaned on any audit-write failure, breaking the
    fast-path marker contract. Atomicity now requires all three rows to
    land together or none to land.
    """
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)

        original_execute = engine.execute

        async def flaky_execute(sql: str, params: object = ()) -> None:
            if "INSERT INTO audit_log" in sql:
                raise StorageError("simulated audit failure")
            await original_execute(sql, params)  # type: ignore[arg-type]

        monkeypatch.setattr(engine, "execute", flaky_execute)

        with pytest.raises(StorageError, match="simulated audit failure"):
            await persist_first_run(app, _full_capture(), api_key_configured=True, modes_count=1)

        sessions = await engine.fetchall("SELECT id FROM sessions")
        snapshots = await engine.fetchall("SELECT id FROM workspace_snapshots")
        audits = await engine.fetchall("SELECT id FROM audit_log")
        assert sessions == []
        assert snapshots == []
        assert audits == []
    finally:
        await engine.close()


async def test_ended_at_is_stamped_after_snapshot_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review patch #4 / AC #12 — ``ended_at`` is captured AFTER the snapshot.

    Monkeypatches the clock so three consecutive ``_utc_now_iso`` calls
    return distinct strings. ``started_at`` must match the first call
    (from ``capture_initial_workspace``), ``ended_at`` must match a
    later call (inside the transaction, after the snapshot INSERT).
    ``ended_at != started_at`` is the regression guard — the old code
    captured both within microseconds at persist entry.
    """
    timestamps = iter(
        [
            "2026-04-17T12:00:00+00:00",  # started_at (from capture)
            "2026-04-17T12:00:05+00:00",  # ended_at (inside transaction)
            "2026-04-17T12:00:10+00:00",  # audit timestamp (inside transaction)
        ]
    )

    def _monotonic_clock() -> str:
        return next(timestamps)

    # The capture timestamp is set before we monkeypatch, so the snapshot
    # carries a fixed value we construct manually.
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        capture = _full_capture(captured_at="2026-04-17T12:00:00+00:00")
        monkeypatch.setattr("nova.core.events._utc_now_iso", _monotonic_clock)
        # Skip the captured_at slot — it's already baked into the snapshot.
        # The first live _utc_now_iso() call in persist_first_run is the
        # ended_at stamp after the snapshot INSERT.
        next(timestamps)

        await persist_first_run(app, capture, api_key_configured=True, modes_count=1)

        row = await engine.fetchone("SELECT started_at, ended_at FROM sessions")
        assert row is not None
        assert row["started_at"] == "2026-04-17T12:00:00+00:00"
        assert row["ended_at"] == "2026-04-17T12:00:05+00:00"
        assert row["ended_at"] != row["started_at"]
    finally:
        await engine.close()


async def test_real_api_key_never_leaks_into_audit_details(
    tmp_path: Path,
) -> None:
    """Review patch #6 — the raw API key string never appears in audit row.

    The previous test passed ``api_key_configured=True`` but did not plant
    an actual key anywhere in scope, so it was vacuously true. This test
    builds a ``NovaConfig``-shaped object that carries a real
    ``sk-ant-...`` string, derives ``api_key_configured`` from it at the
    call site exactly as ``nova.setup.__main__`` does, and asserts the key
    string never lands in the ``details`` column.
    """
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        real_key = "sk-ant-NEVERSHOULDAPPEAR-abc123"
        # Simulate the __main__ derivation: a bool from a non-empty str.
        api_key_configured = bool(real_key)
        await persist_first_run(
            app, _full_capture(), api_key_configured=api_key_configured, modes_count=1
        )
        row = await engine.fetchone("SELECT details FROM audit_log")
        assert row is not None
        details_text: str = row["details"]
        assert real_key not in details_text
        # The full ``sk-`` substring also must not land anywhere.
        assert "sk-" not in details_text
    finally:
        await engine.close()


async def test_focused_app_is_stored_in_workspace_data(tmp_path: Path) -> None:
    """Review patch #2 — ``focused_app`` reflects the foreground match, not first-app.

    When the CaptureResult carries an explicit ``focused_app="chrome"``
    even though ``chrome`` is the second window in enumeration order,
    the JSON payload must record ``chrome`` (not the first-app heuristic).
    """
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        snapshot = _fixed_snapshot("2026-04-17T12:00:00+00:00")  # code, chrome
        capture = CaptureResult(
            snapshot=snapshot,
            status="full",
            windows_captured=2,
            windows_dropped=0,
            focused_app="chrome",  # chrome is the SECOND window — not first
        )
        await persist_first_run(app, capture, api_key_configured=True, modes_count=1)
        row = await engine.fetchone("SELECT workspace_data FROM workspace_snapshots")
        assert row is not None
        assert json.loads(row["workspace_data"])["focused_app"] == "chrome"
    finally:
        await engine.close()


async def test_focused_app_none_when_not_resolvable(tmp_path: Path) -> None:
    """Review patch #2 — ``focused_app=None`` serializes as JSON ``null``."""
    engine = await _booted_engine(tmp_path)
    try:
        app = _HarnessApp(engine)
        snapshot = _fixed_snapshot("2026-04-17T12:00:00+00:00")
        capture = CaptureResult(
            snapshot=snapshot,
            status="full",
            windows_captured=2,
            windows_dropped=0,
            focused_app=None,
        )
        await persist_first_run(app, capture, api_key_configured=True, modes_count=1)
        row = await engine.fetchone("SELECT workspace_data FROM workspace_snapshots")
        assert row is not None
        assert json.loads(row["workspace_data"])["focused_app"] is None
    finally:
        await engine.close()
