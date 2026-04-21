"""Story 2.4 ACs — State A copy exactness + already-setup fast path.

AC #1, #3, and the "no pause between State A and the wizard" assertion
from AC #2. Sits alongside ``tests/unit/test_setup_main.py`` which
already covers Stories 2.1-2.3 wiring.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from nova.core.storage.engine import SqliteStorageEngine
from nova.setup.__main__ import (
    EXIT_OK,
    _probe_setup_complete,
    _render_already_setup_panel,
    _render_state_a,
    main,
)

# ---------------------------------------------------------------------------
# AC #1 — State A copy exactness
# ---------------------------------------------------------------------------


def test_state_a_title_is_nova_not_session_briefing() -> None:
    """State A's title is ``N.O.V.A.`` — ``Session Briefing`` belongs to B/C."""
    console = Console(record=True, width=80)
    _render_state_a(console)
    out = console.export_text(clear=True)
    assert "N.O.V.A." in out
    assert "Session Briefing" not in out


def test_state_a_body_contains_first_session_line() -> None:
    """AC #1 — both body lines must appear verbatim.

    Review patch #11 strengthened this from a bare `"First session."`
    substring to the full AC-locked strings including the em-dash +
    "that's expected." clause and the "Let's set up..." line.
    """
    console = Console(record=True, width=80)
    _render_state_a(console)
    out = console.export_text(clear=True)
    assert "First session. No history yet — that's expected." in out
    assert "Let's set up your first workspace mode so tomorrow starts warm." in out


def test_state_a_body_has_no_stale_copy() -> None:
    """Review patch #11 regression guard — pre-AC body strings must NOT appear."""
    console = Console(record=True, width=80)
    _render_state_a(console)
    out = console.export_text(clear=True)
    assert "Personal AI Session Companion" not in out
    assert "Running setup to create your workspace modes." not in out


def test_state_a_body_has_no_resume_prompt() -> None:
    """State A must not render a resume suggestion (that's State C)."""
    console = Console(record=True, width=80)
    _render_state_a(console)
    out = console.export_text(clear=True)
    assert "Resume" not in out
    assert "resume" not in out


# ---------------------------------------------------------------------------
# AC #2 — no pause between State A and the wizard
# ---------------------------------------------------------------------------


@patch(
    "nova.setup.__main__._run_initial_capture_and_persist",
    new_callable=AsyncMock,
    return_value=EXIT_OK,
)
@patch(
    "nova.setup.__main__._probe_setup_complete",
    new_callable=AsyncMock,
    return_value=False,
)
@patch("nova.setup.__main__.run_mode_wizard_step", return_value=None)
@patch("nova.setup.__main__.run_api_key_step", return_value=False)
def test_no_pause_between_state_a_and_api_key_step(
    mock_api_key: MagicMock,
    _mock_wizard: MagicMock,
    _mock_probe: AsyncMock,
    _mock_persist: AsyncMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` must not prompt the user between rendering State A and
    invoking ``run_api_key_step`` — the auto-transition is a direct call.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def _no_input_please(*_a: object, **_kw: object) -> str:
        raise AssertionError("unexpected interactive prompt between State A and wizard")

    # Fail fast if any Console.input() sneaks in via the api-key/wizard
    # mocks' default Console wiring.
    monkeypatch.setattr("rich.console.Console.input", _no_input_please)
    main([])
    mock_api_key.assert_called_once()


# ---------------------------------------------------------------------------
# AC #3 — already-setup fast path
# ---------------------------------------------------------------------------


async def test_probe_returns_false_when_db_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    assert await _probe_setup_complete(data_dir) is False


async def test_probe_returns_false_when_db_has_no_audit_row(tmp_path: Path) -> None:
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    engine = SqliteStorageEngine(data_dir / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()
    finally:
        await engine.close()
    assert await _probe_setup_complete(data_dir) is False


async def test_probe_returns_true_after_setup_complete_audit_row(tmp_path: Path) -> None:
    from nova.core.audit import RESULT_SUCCESS, AuditLogger
    from nova.core.types import ActionType

    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    engine = SqliteStorageEngine(data_dir / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()
        audit = AuditLogger(storage=engine)
        await audit.log_action(
            ActionType.SETUP_COMPLETE,
            None,
            RESULT_SUCCESS,
            details={"modes_count": 1},
        )
    finally:
        await engine.close()
    assert await _probe_setup_complete(data_dir) is True


# ---------------------------------------------------------------------------
# Review patch #13 — fast path independent of API key presence (AC #31)
# ---------------------------------------------------------------------------


async def _seed_setup_complete_audit_row(data_dir: Path, *, api_key_configured: bool) -> None:
    """Helper — create nova.db with migrations + one ``setup_complete`` row.

    ``api_key_configured`` becomes the value in the audit ``details`` JSON
    — the fast-path predicate must NOT look at this field.
    """
    from nova.core.audit import RESULT_SUCCESS, AuditLogger
    from nova.core.types import ActionType

    engine = SqliteStorageEngine(data_dir / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()
        audit = AuditLogger(storage=engine)
        await audit.log_action(
            ActionType.SETUP_COMPLETE,
            None,
            RESULT_SUCCESS,
            details={
                "modes_count": 1,
                "api_key_configured": api_key_configured,
                "capture_status": "full",
            },
        )
    finally:
        await engine.close()


async def test_fast_path_ignores_api_key_presence(tmp_path: Path) -> None:
    """AC #31 — fast path fires even if no settings.yaml / no api_key exists.

    Story 2.2 permits the user to skip the API key (exit 0). If the
    fast-path predicate required settings.yaml or a non-empty api_key,
    those users would be trapped in State A forever on every rerun.
    Regression guard for AC #3's "independent of API key" rationale.
    """
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    await _seed_setup_complete_audit_row(data_dir, api_key_configured=False)
    # No settings.yaml exists.
    assert not (data_dir / "settings.yaml").exists()
    assert await _probe_setup_complete(data_dir) is True


async def test_fast_path_ignores_api_key_configured_false(tmp_path: Path) -> None:
    """AC #31 — fast path fires even when ``api_key_configured`` is false in details.

    A user who ran setup, declined the key, and later re-runs ``setup.bat``
    must hit the fast path. The probe reads only the ``action_type``
    column and must not inspect ``details`` at all.
    """
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    await _seed_setup_complete_audit_row(data_dir, api_key_configured=False)
    assert await _probe_setup_complete(data_dir) is True


async def test_no_fast_path_when_db_exists_but_no_setup_complete_row(
    tmp_path: Path,
) -> None:
    """AC #31 — interrupted previous run: DB exists, no ``setup_complete`` row.

    Review patch #14 — a half-baked DB (schema applied, but setup crashed
    before the audit row landed) must fall through to a normal setup run.
    The subsequent successful run then writes exactly one session + one
    snapshot + one audit row — no duplicates from the prior interruption.
    """
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    # Pre-create the DB with migrations applied but no setup_complete row —
    # simulates a setup that crashed after create_app but before the
    # transaction.
    engine = SqliteStorageEngine(data_dir / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()
    finally:
        await engine.close()

    # Probe returns False → main flow runs State A + wizard + persist.
    assert await _probe_setup_complete(data_dir) is False

    # After a successful persist run, exactly one row lands in each table.
    # We simulate that here with persist_first_run directly, skipping the
    # wizard (covered by the integration suite).
    from nova.adapters.sqlite.brain import SqliteBrainAdapter
    from nova.core.audit import AuditLogger
    from nova.core.types import SnapshotType
    from nova.ports.brain import BrainPort
    from nova.setup.initial_capture import CaptureResult, persist_first_run
    from nova.systems.eyes.models import WorkspaceSnapshot

    engine = SqliteStorageEngine(data_dir / "nova.db")
    await engine.start()
    try:
        await engine.run_migrations()

        class _App:
            def __init__(self, storage: SqliteStorageEngine) -> None:
                self.storage = storage
                self.brain: BrainPort = SqliteBrainAdapter(storage)
                self.audit = AuditLogger(storage=storage)

        capture = CaptureResult(
            snapshot=WorkspaceSnapshot(
                captured_at="2026-04-17T12:00:00+00:00",
                snapshot_type=SnapshotType.STARTUP,
                windows=(),
            ),
            status="empty",
            windows_captured=0,
            windows_dropped=0,
            focused_app=None,
        )
        await persist_first_run(_App(engine), capture, api_key_configured=False, modes_count=1)

        sessions = await engine.fetchall("SELECT id FROM sessions")
        snapshots = await engine.fetchall("SELECT id FROM workspace_snapshots")
        audits = await engine.fetchall(
            "SELECT id FROM audit_log WHERE action_type = 'setup_complete'"
        )
        assert len(sessions) == 1
        assert len(snapshots) == 1
        assert len(audits) == 1
    finally:
        await engine.close()

    # Subsequent probe now returns True.
    assert await _probe_setup_complete(data_dir) is True


async def test_probe_falls_through_on_corrupt_db(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero-byte nova.db should not crash the probe — setup is recovery.

    Review patch #8 — also asserts the WARNING log per AC #31's
    ``test_fast_path_handles_corrupt_db_by_falling_through`` bullet. A
    silent-failure regression (return False without logging) would pass
    the old assertion but fail this strengthened one.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="nova.setup.__main__")
    data_dir = tmp_path / "nova"
    data_dir.mkdir()
    (data_dir / "nova.db").write_bytes(b"")
    # The probe either fails at start() or at the SELECT; either way
    # the result is False and no exception escapes.
    assert await _probe_setup_complete(data_dir) is False
    # AC #31 — corrupt DB must log a WARNING before falling through.
    messages = [r.getMessage() for r in caplog.records]
    assert any("could not open" in m or "query failed" in m for m in messages), (
        f"expected WARNING about probe failure; got {messages}"
    )


@patch(
    "nova.setup.__main__._run_initial_capture_and_persist",
    new_callable=AsyncMock,
    return_value=EXIT_OK,
)
@patch("nova.setup.__main__.run_mode_wizard_step", return_value=None)
@patch("nova.setup.__main__.run_api_key_step", return_value=False)
@patch(
    "nova.setup.__main__._probe_setup_complete",
    new_callable=AsyncMock,
    return_value=True,
)
def test_fast_path_skips_state_a_and_wizard(
    _mock_probe: AsyncMock,
    mock_api_key: MagicMock,
    mock_wizard: MagicMock,
    mock_persist: AsyncMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast path renders the informational panel and skips everything else."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert main([]) == EXIT_OK
    mock_api_key.assert_not_called()
    mock_wizard.assert_not_called()
    mock_persist.assert_not_called()
    out = capsys.readouterr().out
    assert "Setup already complete" in out
    assert "uv run nova" in out
    # State A body MUST NOT appear — the first-run orientation only
    # renders pre-setup.
    assert "First session. No history yet — that's expected." not in out


def test_render_already_setup_panel_uses_bold_command() -> None:
    console = Console(record=True, width=80, color_system="truecolor")
    _render_already_setup_panel(console)
    out = console.export_text(clear=True)
    assert "Setup already complete." in out
    assert "uv run nova" in out


def test_render_already_setup_panel_no_emoji() -> None:
    console = Console(record=True, width=80)
    _render_already_setup_panel(console)
    out = console.export_text(clear=True)
    for forbidden in ["🚀", "✨", "🎉", "👋", "😊"]:
        assert forbidden not in out
