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
    Verify: ``EXIT_OK``, briefing rendered to stdout, ``Session ended.``
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

    # Patch the REPL input to type "shutdown" so the loop exits on the
    # first iteration. End-to-end through every other layer.
    monkeypatch.setattr("nova.adapters.rich.skin.Prompt.ask", lambda *a, **kw: "shutdown")

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    # Briefing card rendered (State C — prior session present).
    captured = capsys.readouterr()
    assert "Session Briefing" in captured.out
    # SHUTDOWN handler ran and rendered confirmation.
    assert "Session ended." in captured.out
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
    assert new_session["seed_text"] is None  # Story 3.7 owns seed capture
    assert new_session["is_complete"] == 1  # SHUTDOWN routed cleanly


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

    monkeypatch.setattr("nova.adapters.rich.skin.Prompt.ask", lambda *a, **kw: "shutdown")

    exit_code = _invoke_nova(monkeypatch, nova_data_dir)
    assert exit_code == EXIT_OK

    captured = capsys.readouterr()
    # Briefing card MUST NOT render — skip policy fired.
    assert "Session Briefing" not in captured.out
    # SHUTDOWN handler still ran.
    assert "Session ended." in captured.out

    # Verify the skip log line is in nova.log.
    log_text = (nova_data_dir / "logs" / "nova.log").read_text(encoding="utf-8")
    assert "briefing skipped" in log_text
