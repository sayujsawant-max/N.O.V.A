"""Story 3.5 AC #25 — end-to-end session-loop integration tests.

The first integration tests that boot the full T1 monolith with REAL
adapters end-to-end (Brain → Nerve → Ritual → Skin). Catch real-adapter
glue bugs that mocks would mask: Brain's adapter returning a different
``SessionSummary`` shape than the unit-test mock, asyncio loop scheduling
under real I/O, real SQLite serialization in the session-lifecycle
write path.

Two scenarios:

* **Bare boot → briefing render → SHUTDOWN command.** Empty data dir
  with one mode + the State C path (a prior completed session with
  seed). Verifies the briefing card renders, REPL accepts a SHUTDOWN,
  and ``nova.db`` shows a new session row with ``is_complete=1``.
* **Bare boot with recent prior session → briefing skipped.** Same data
  dir but the prior session ended <60 minutes ago. Verifies the
  briefing card does NOT render (skip-briefing policy fires) but the
  REPL still enters and exits cleanly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nova.cli import (
    _FILE_HANDLER_NAME,
    _STDERR_HANDLER_NAME,
    EXIT_OK,
    main,
)
from nova.core import SqliteStorageEngine

pytestmark = pytest.mark.integration


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def nova_data_dir(tmp_path: Path) -> Path:
    """Seed a minimal data dir with one mode + a present api_key.

    A dummy ``api_key`` is set so the Story 2.5 offline-notice does NOT
    fire on stderr — these tests assert clean stderr. The key is never
    used (no cloud op runs in Story 3.5).
    """
    (tmp_path / "settings.yaml").write_text(
        'api_key: "sk-ant-test-session-loop"\n', encoding="utf-8"
    )
    (tmp_path / "exclusions.yaml").write_text("{}\n", encoding="utf-8")
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    (modes_dir / "coding.yaml").write_text(
        "name: Coding\napps:\n  - name: VS Code\n    executable: code.exe\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_nova_logging() -> Iterator[None]:
    """Remove cli-owned handlers after each test so parallel tests are isolated."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.name in {_STDERR_HANDLER_NAME, _FILE_HANDLER_NAME}:
            root.removeHandler(handler)
            handler.close()


def _invoke_nova(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> int:
    monkeypatch.setattr("sys.argv", ["nova"])
    monkeypatch.setenv("NOVA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("NOVA_LOG_LEVEL", raising=False)
    return main()


async def _seed_prior_session(db_path: Path, *, ended_at: str, seed_text: str | None) -> None:
    """Write a completed prior session row + a seed memory item for State C."""
    storage = SqliteStorageEngine(db_path)
    await storage.start()
    try:
        await storage.run_migrations()
        await storage.execute(
            "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, "
            "summary, is_complete) VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-04-01T08:00:00+00:00", ended_at, "coding", seed_text, None, 1),
        )
        if seed_text is not None:
            await storage.execute(
                "INSERT INTO memory_items (session_id, category, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (1, "seed", seed_text, ended_at),
            )
    finally:
        await storage.close()


# --- Scenario 1: bare boot → briefing → shutdown ---------------------------


def test_bare_nova_boots_briefing_then_shuts_down_on_shutdown_command(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full happy path with REAL adapters end-to-end.

    Setup: data dir with one mode + a prior completed session ended
    long enough ago (2h) that the skip-briefing policy does NOT fire.
    Drive: the autouse-REPL-shortcircuit pattern from
    ``test_cli_bootstrap.py`` would patch ``Prompt.ask``; this test
    explicitly does the patch so the assertion that the briefing
    rendered + SHUTDOWN routed is unambiguous.
    Verify: ``EXIT_OK``, briefing rendered to stdout, ``Cancelled.``
    rendered, new session row in ``nova.db`` with ``is_complete=1``.
    """
    import asyncio

    # Seed a prior completed session ended 2 hours ago — outside the
    # 60-minute default recency threshold, so the briefing renders.
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    asyncio.run(
        _seed_prior_session(
            nova_data_dir / "nova.db",
            ended_at=two_hours_ago,
            seed_text="yesterday's seed",
        )
    )

    # Patch Prompt.ask: REPL reads "shutdown", then seed prompt reads "skip"
    # (cancel path) so we don't accidentally write a literal "shutdown" string
    # as seed_text. Use an iterator with a default fallback for any extra calls.
    inputs = iter(["shutdown", "skip"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs, "skip"),
    )

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    # Briefing card rendered (State C — prior session present).
    captured = capsys.readouterr()
    assert "Session Briefing" in captured.out
    # Story 3.7 — Shutdown card + cancel render (skip terminator).
    assert "Session ending" in captured.out
    assert "What should you pick up tomorrow?" in captured.out
    assert "Cancelled." in captured.out
    assert captured.err == ""

    # Verify the new session row was written with is_complete=1.
    storage = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def _read_sessions() -> list[dict[str, object]]:
        await storage.start()
        try:
            rows = await storage.fetchall(
                "SELECT id, mode_name, seed_text, is_complete FROM sessions ORDER BY id"
            )
            return [dict(row) for row in rows]
        finally:
            await storage.close()

    rows = asyncio.run(_read_sessions())
    # Two rows: the seeded prior + the new bare-`nova` session.
    assert len(rows) == 2
    new_session = rows[1]
    assert new_session["mode_name"] is None  # bare nova boot — no mode chosen
    assert new_session["seed_text"] is None  # Story 3.7 — user typed "skip"
    assert new_session["is_complete"] == 1  # commit_shutdown finalized cleanly


# --- Story 3.7: shutdown with seed entered (end-to-end persistence) --------


def test_shutdown_with_seed_persists_session_seed_memory_item_and_snapshot(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Story 3.7 end-to-end — seed entered → 3-row atomic commit + events + render.

    Stdin pipe: ``shutdown`` (REPL) → ``finish auth tests`` (seed prompt).
    Asserts:
    * EXIT_OK
    * Sessions row finalized: is_complete=1, seed_text="finish auth tests"
    * Memory_items row written: category="seed", content="finish auth tests"
    * Workspace_snapshots row written: snapshot_type="shutdown"
    * Audit log has SEED_CAPTURE row with result="success"
    * stdout contains shutdown card + "Planted for tomorrow."
    """
    import asyncio

    inputs = iter(["shutdown", "finish auth tests"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs),
    )

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    captured = capsys.readouterr()
    assert "Session ending" in captured.out
    assert "What should you pick up tomorrow?" in captured.out
    assert "Planted for tomorrow." in captured.out

    # Verify the three rows landed atomically.
    storage = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def _read_state() -> dict[str, list[dict[str, object]]]:
        await storage.start()
        try:
            sessions = await storage.fetchall(
                "SELECT id, seed_text, is_complete FROM sessions ORDER BY id"
            )
            memories = await storage.fetchall(
                "SELECT category, content FROM memory_items ORDER BY id"
            )
            snapshots = await storage.fetchall(
                "SELECT snapshot_type FROM workspace_snapshots ORDER BY id"
            )
            audits = await storage.fetchall(
                "SELECT action_type, result, details FROM audit_log "
                "WHERE action_type = 'seed_capture' ORDER BY id"
            )
            return {
                "sessions": [dict(r) for r in sessions],
                "memories": [dict(r) for r in memories],
                "snapshots": [dict(r) for r in snapshots],
                "audits": [dict(r) for r in audits],
            }
        finally:
            await storage.close()

    state = asyncio.run(_read_state())
    assert len(state["sessions"]) == 1
    sess = state["sessions"][0]
    assert sess["seed_text"] == "finish auth tests"
    assert sess["is_complete"] == 1
    assert len(state["memories"]) == 1
    mem = state["memories"][0]
    assert mem["category"] == "seed"
    assert mem["content"] == "finish auth tests"
    assert len(state["snapshots"]) == 1
    snap = state["snapshots"][0]
    assert snap["snapshot_type"] == "shutdown"
    assert len(state["audits"]) == 1
    audit = state["audits"][0]
    assert audit["result"] == "success"


def test_mode_switch_then_shutdown_persists_session_mode_name_and_snapshot_mode(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 3.7 review patch — mode coding → shutdown writes both columns.

    The user reported: shutdown captured the active mode in the
    workspace snapshot but NOT in ``sessions.mode_name``, so
    ``get_mode_last_used("coding")`` couldn't find the session and
    ``ModeInfo.last_used_at`` enrichment broke for next-startup
    briefing assembly.

    This test drives the full flow — ``mode coding`` (mocked launcher
    so the test runs cross-platform) → ``shutdown`` → seed → assert
    BOTH ``sessions.mode_name == "coding"`` AND the persisted
    workspace_snapshots row's mode_name field is ``"coding"``.

    Cross-platform via mocking ``Win32HandsAdapter.launch_app`` to
    return a synthetic success ``ActionResult``; no real subprocess
    spawned.
    """
    import asyncio

    from nova.adapters.win32.actions import Win32HandsAdapter
    from nova.core.config import AppConfig
    from nova.core.types import ActionType
    from nova.systems.hands.models import ActionResult

    # Mock the launcher's per-app launch primitive so HandsSystem's
    # orchestration runs end-to-end without spawning real processes.
    async def fake_launch_app(self: Win32HandsAdapter, app: AppConfig) -> ActionResult:
        del self
        return ActionResult(
            action_type=ActionType.APP_LAUNCH,
            target=app.name,
            success=True,
            reason=None,
        )

    monkeypatch.setattr(
        "nova.adapters.win32.actions.Win32HandsAdapter.launch_app",
        fake_launch_app,
    )

    inputs = iter(["mode coding", "shutdown", "finish auth tests"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs),
    )

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    storage = SqliteStorageEngine(nova_data_dir / "nova.db")

    async def _read_state() -> tuple[dict[str, object], dict[str, object]]:
        await storage.start()
        try:
            session_row = await storage.fetchone(
                "SELECT mode_name, seed_text, is_complete FROM sessions ORDER BY id DESC LIMIT 1"
            )
            snapshot_row = await storage.fetchone(
                "SELECT snapshot_type, workspace_data FROM workspace_snapshots "
                "ORDER BY id DESC LIMIT 1"
            )
            assert session_row is not None
            assert snapshot_row is not None
            return dict(session_row), dict(snapshot_row)
        finally:
            await storage.close()

    session, snapshot = asyncio.run(_read_state())

    # Sessions row carries the active mode stem so get_mode_last_used
    # finds it on next startup.
    assert session["mode_name"] == "coding"
    assert session["seed_text"] == "finish auth tests"
    assert session["is_complete"] == 1

    # Workspace snapshot's mode_name field carries the same stem (cross-row consistency).
    assert snapshot["snapshot_type"] == "shutdown"
    workspace_data = snapshot["workspace_data"]
    assert isinstance(workspace_data, str)
    assert '"mode_name":"coding"' in workspace_data


# --- Scenario 2: skip-briefing policy fires for recent prior ---------------


def test_bare_nova_skips_briefing_when_prior_session_recent(
    nova_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Recent prior session → briefing skipped, REPL still enters and exits.

    Prior session ended 20 minutes ago (well within the default 60-min
    threshold). The briefing card must NOT render. The REPL still
    enters; the synthetic SHUTDOWN exits cleanly.
    """
    import asyncio

    twenty_min_ago = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    asyncio.run(
        _seed_prior_session(
            nova_data_dir / "nova.db",
            ended_at=twenty_min_ago,
            seed_text="recent seed",
        )
    )

    inputs = iter(["shutdown", "skip"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs, "skip"),
    )

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    captured = capsys.readouterr()
    # Briefing card MUST NOT render — skip policy fired.
    assert "Session Briefing" not in captured.out
    # Story 3.7 — Shutdown card + cancel render still run.
    assert "Session ending" in captured.out
    assert "Cancelled." in captured.out

    # Verify the skip log line is in nova.log.
    log_text = (nova_data_dir / "logs" / "nova.log").read_text(encoding="utf-8")
    assert "briefing skipped" in log_text


# ===========================================================================
# Scenario 3 + 4: Story 3.6 — mode restore end-to-end (windows_only)
# ===========================================================================


def _stash_spawned_pids(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Wrap ``subprocess.Popen`` so spawned PIDs are stashed for cleanup.

    Returns a list that the test passes to teardown — terminating
    by spawned PID (not by name) avoids killing user notepads.
    """
    import subprocess as _subprocess
    from typing import Any

    real_popen = _subprocess.Popen
    spawned_pids: list[int] = []

    def wrapping_popen(*args: Any, **kwargs: Any) -> Any:
        proc = real_popen(*args, **kwargs)
        spawned_pids.append(proc.pid)
        return proc

    monkeypatch.setattr("nova.adapters.win32.actions.subprocess.Popen", wrapping_popen)
    return spawned_pids


def _terminate_spawned(spawned_pids: list[int]) -> None:
    """Best-effort terminate by PID — used in test teardown."""
    import psutil

    for pid in spawned_pids:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except psutil.TimeoutExpired:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Already gone or refused — both fine for cleanup.
            pass


@pytest.fixture
def notepad_mode_data_dir(tmp_path: Path) -> Path:
    """Seed a data dir whose ``coding`` mode launches Notepad only.

    Notepad is present on every Windows install, so the launch is
    deterministic. The mode YAML uses the stem ``coding`` so the
    user's typed ``mode coding`` resolves cleanly.
    """
    (tmp_path / "settings.yaml").write_text(
        'api_key: "sk-ant-test-mode-restore"\n', encoding="utf-8"
    )
    (tmp_path / "exclusions.yaml").write_text("{}\n", encoding="utf-8")
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    (modes_dir / "coding.yaml").write_text(
        "name: Coding\napps:\n  - name: Notepad\n    executable: notepad.exe\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def notepad_partial_data_dir(tmp_path: Path) -> Path:
    """Seed a coding mode with one real app (Notepad) + one bogus app."""
    (tmp_path / "settings.yaml").write_text(
        'api_key: "sk-ant-test-mode-restore-partial"\n', encoding="utf-8"
    )
    (tmp_path / "exclusions.yaml").write_text("{}\n", encoding="utf-8")
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    (modes_dir / "coding.yaml").write_text(
        "name: Coding\n"
        "apps:\n"
        "  - name: Notepad\n"
        "    executable: notepad.exe\n"
        "  - name: Bogus XYZ\n"
        "    executable: bogus_xyz_app_that_does_not_exist.exe\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.windows_only
def test_mode_restore_full_workspace_ready_end_to_end(
    notepad_mode_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full happy path with REAL Win32HandsAdapter.

    ``mode coding`` → Notepad launches via subprocess.Popen → audit row
    fires → render line appears → ``ModeRestored`` emits → final
    summary renders → ``shutdown`` exits cleanly.
    """
    import asyncio

    # Seed a prior session (2h ago) so State C briefing renders rather
    # than skip-briefing firing.
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    asyncio.run(
        _seed_prior_session(
            notepad_mode_data_dir / "nova.db",
            ended_at=two_hours_ago,
            seed_text="prior seed",
        )
    )

    # Stash spawned PIDs for cleanup.
    spawned_pids = _stash_spawned_pids(monkeypatch)

    # Iterator-backed Prompt.ask: returns "mode coding" then "shutdown".
    inputs = iter(["mode coding", "shutdown", "skip"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs, "skip"),
    )

    try:
        exit_code = _invoke_nova(monkeypatch, notepad_mode_data_dir)
        assert exit_code == EXIT_OK

        captured = capsys.readouterr()
        # Per-app render line.
        assert "✓ Notepad" in captured.out
        # Final-line summary (full success).
        assert "Workspace ready." in captured.out
        # SHUTDOWN handler ran.
        # Story 3.7 — shutdown card + cancel render.
        assert "Cancelled." in captured.out

        # Verify the audit log: 1 app_launch/success + 1 mode_restore/success.
        async def _read_audit() -> list[dict[str, object]]:
            storage = SqliteStorageEngine(notepad_mode_data_dir / "nova.db")
            await storage.start()
            try:
                rows = await storage.fetchall(
                    "SELECT action_type, target, result, details FROM audit_log ORDER BY id"
                )
                return [dict(row) for row in rows]
            finally:
                await storage.close()

        audit_rows = asyncio.run(_read_audit())
        app_launch_rows = [r for r in audit_rows if r["action_type"] == "app_launch"]
        mode_restore_rows = [r for r in audit_rows if r["action_type"] == "mode_restore"]
        assert len(app_launch_rows) == 1
        assert app_launch_rows[0]["result"] == "success"
        assert app_launch_rows[0]["target"] == "Notepad"
        assert len(mode_restore_rows) == 1
        assert mode_restore_rows[0]["result"] == "success"
        assert mode_restore_rows[0]["target"] == "coding"  # stem, not display
    finally:
        _terminate_spawned(spawned_pids)


@pytest.mark.windows_only
def test_mode_restore_partial_failure_workspace_partially_ready(
    notepad_partial_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Partial-restore: 1 of 2 apps launches; final line distinguishes from full.

    Notepad launches; ``bogus_xyz_app_that_does_not_exist.exe`` raises
    ``FileNotFoundError`` (no args, so the os.startfile fallback also
    runs and also fails) and maps to ``REASON_NOT_FOUND``.
    """
    import asyncio

    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    asyncio.run(
        _seed_prior_session(
            notepad_partial_data_dir / "nova.db",
            ended_at=two_hours_ago,
            seed_text="prior seed",
        )
    )

    spawned_pids = _stash_spawned_pids(monkeypatch)
    inputs = iter(["mode coding", "shutdown", "skip"])
    monkeypatch.setattr(
        "nova.adapters.rich.skin.Prompt.ask",
        lambda *a, **kw: next(inputs, "skip"),
    )

    try:
        exit_code = _invoke_nova(monkeypatch, notepad_partial_data_dir)
        assert exit_code == EXIT_OK

        captured = capsys.readouterr()
        assert "✓ Notepad" in captured.out
        assert "✗ Bogus XYZ (not found — is it installed?)" in captured.out
        assert "Workspace partially ready. Bogus XYZ was skipped." in captured.out
        # Story 3.7 — shutdown card + cancel render.
        assert "Cancelled." in captured.out

        async def _read_audit() -> list[dict[str, object]]:
            storage = SqliteStorageEngine(notepad_partial_data_dir / "nova.db")
            await storage.start()
            try:
                rows = await storage.fetchall(
                    "SELECT action_type, target, result FROM audit_log ORDER BY id"
                )
                return [dict(row) for row in rows]
            finally:
                await storage.close()

        audit_rows = asyncio.run(_read_audit())
        app_launch_rows = [r for r in audit_rows if r["action_type"] == "app_launch"]
        mode_restore_rows = [r for r in audit_rows if r["action_type"] == "mode_restore"]
        results_by_target = {r["target"]: r["result"] for r in app_launch_rows}
        assert results_by_target == {"Notepad": "success", "Bogus XYZ": "failed"}
        assert len(mode_restore_rows) == 1
        assert mode_restore_rows[0]["result"] == "partial"
        assert mode_restore_rows[0]["target"] == "coding"  # stem
    finally:
        _terminate_spawned(spawned_pids)
