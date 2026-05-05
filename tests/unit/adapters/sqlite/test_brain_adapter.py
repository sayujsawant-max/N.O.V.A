"""Unit tests for :class:`nova.adapters.sqlite.brain.SqliteBrainAdapter` (Story 3.1).

Fixtures use a real in-memory :class:`SqliteStorageEngine` with the 001
migration applied — project-context.md:95 permits in-memory SQLite for
Brain unit tests. Each test gets a fresh engine (no shared state).

Three coverage bands per Epic 2 retro A9:

* **Happy path** — create/end/round-trip session, snapshot storage, seed retrieval.
* **Degraded / partial-failure path** — rollback on transaction failure,
  interrupted session summaries, corrupt JSON / unknown enum, adapter does
  not double-catch ``StorageError`` from the engine.
* **Retry / rerun / idempotency** — Story 2.4 setup-row reconciliation,
  round-trip JSON fidelity, clock indirection via
  ``events._utc_now_iso`` monkeypatch.

Plus port-contract and Epic 5 scope coverage.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nova.adapters.sqlite.brain import SqliteBrainAdapter, _compute_duration_seconds
from nova.core.exceptions import StorageError
from nova.core.storage.engine import SqliteStorageEngine
from nova.core.types import SnapshotType
from nova.ports.brain import BrainPort
from nova.systems.brain.models import DeletionPreview, SessionSummary, WorkspaceSnapshotInput
from nova.systems.eyes.models import WorkspaceSnapshot

# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[SqliteStorageEngine]:
    """Per-test engine on a tmp-path DB with migrations applied.

    Uses a per-test file instead of ``:memory:`` because the engine's
    WAL-mode verification would reject ``:memory:`` (the file-backed
    path lets WAL fire normally and matches production behavior).
    """
    db_path = tmp_path / "test.db"
    eng = SqliteStorageEngine(db_path)
    await eng.start()
    await eng.run_migrations()
    try:
        yield eng
    finally:
        await eng.close()


@pytest.fixture
def adapter(engine: SqliteStorageEngine) -> SqliteBrainAdapter:
    return SqliteBrainAdapter(engine)


# --- Happy path -------------------------------------------------------------


async def test_create_session_returns_lastrowid_and_writes_expected_row(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    session_id = await adapter.create_session(mode_name=None, started_at=None)
    assert session_id == 1
    row = await engine.fetchone(
        "SELECT mode_name, is_complete, ended_at, seed_text, summary FROM sessions WHERE id = ?",
        (session_id,),
    )
    assert row is not None
    assert row["mode_name"] is None
    assert row["is_complete"] == 0
    assert row["ended_at"] is None
    assert row["seed_text"] is None
    assert row["summary"] is None


async def test_end_session_updates_expected_fields(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-21T10:00:00+00:00")
    session_id = await adapter.create_session(mode_name="coding", started_at=None)
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-21T11:00:00+00:00")
    await adapter.end_session(
        session_id,
        seed_text="tomorrow: finish the migration",
        summary="good session",
        is_complete=True,
    )
    row = await engine.fetchone(
        "SELECT started_at, ended_at, seed_text, summary, is_complete FROM sessions WHERE id = ?",
        (session_id,),
    )
    assert row is not None
    assert row["started_at"] == "2026-04-21T10:00:00+00:00"
    assert row["ended_at"] == "2026-04-21T11:00:00+00:00"
    assert row["seed_text"] == "tomorrow: finish the migration"
    assert row["summary"] == "good session"
    assert row["is_complete"] == 1


async def test_round_trip_create_store_end(
    adapter: SqliteBrainAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter(
        [
            "2026-04-21T10:00:00+00:00",  # create_session clock
            "2026-04-21T11:00:00+00:00",  # end_session clock
        ]
    )
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: next(clock))

    session_id = await adapter.create_session(mode_name="coding", started_at=None)
    await adapter.store_snapshot(
        session_id,
        WorkspaceSnapshotInput(
            captured_at="2026-04-21T10:00:05+00:00",
            snapshot_type=SnapshotType.STARTUP,
            apps=("code", "chrome"),
            focused_app="code",
            mode_name="coding",
        ),
    )
    await adapter.end_session(
        session_id, seed_text="resume here", summary="productive", is_complete=True
    )

    last = await adapter.get_last_session()
    assert last == SessionSummary(
        session_id=session_id,
        started_at="2026-04-21T10:00:00+00:00",
        ended_at="2026-04-21T11:00:00+00:00",
        duration_seconds=3600,
        mode_name="coding",
        summary="productive",
        is_complete=True,
    )

    assert await adapter.get_last_seed() == "resume here"

    snap = await adapter.get_last_snapshot_for_session(session_id)
    assert snap is not None
    assert snap.snapshot_type is SnapshotType.STARTUP
    assert snap.captured_at == "2026-04-21T10:00:05+00:00"
    assert [w.app_name for w in snap.windows] == ["code", "chrome"]


async def test_get_last_session_returns_none_on_empty_db(
    adapter: SqliteBrainAdapter,
) -> None:
    assert await adapter.get_last_session() is None


async def test_get_last_seed_returns_none_on_empty_db(
    adapter: SqliteBrainAdapter,
) -> None:
    assert await adapter.get_last_seed() is None


async def test_get_last_seed_returns_seed_from_completed_session_only(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    # Seed an interrupted session with seed_text set (unusual but possible).
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, ?, NULL, 0)",
        ("2026-04-20T10:00:00+00:00", "interrupted-but-has-seed"),
    )
    # Seed a completed session with a different seed.
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, ?, NULL, ?, NULL, 1)",
        (
            "2026-04-21T10:00:00+00:00",
            "2026-04-21T11:00:00+00:00",
            "the-real-seed",
        ),
    )
    assert await adapter.get_last_seed() == "the-real-seed"


# --- Degraded / partial-failure path ----------------------------------------


async def test_store_snapshot_rollback_on_transaction_failure(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Caller-raised exception inside a transaction rolls back all Brain writes."""
    with pytest.raises(RuntimeError, match="simulated"):
        async with engine.transaction():
            await adapter.create_session(mode_name="coding", started_at=None)
            await adapter.store_snapshot(
                1,
                WorkspaceSnapshotInput(
                    captured_at="2026-04-21T10:00:00+00:00",
                    snapshot_type=SnapshotType.STARTUP,
                    apps=("code",),
                    focused_app="code",
                    mode_name=None,
                ),
            )
            raise RuntimeError("simulated mid-transaction failure")

    # Both writes rolled back.
    session_count = await engine.fetchone("SELECT COUNT(*) AS c FROM sessions")
    snapshot_count = await engine.fetchone("SELECT COUNT(*) AS c FROM workspace_snapshots")
    assert session_count is not None and session_count["c"] == 0
    assert snapshot_count is not None and snapshot_count["c"] == 0


async def test_get_last_session_returns_interrupted_session_with_none_ended_at(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, ?, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00", "coding"),
    )
    summary = await adapter.get_last_session()
    assert summary is not None
    assert summary.ended_at is None
    assert summary.duration_seconds == 0
    assert summary.is_complete is False
    assert summary.mode_name == "coding"


async def test_store_snapshot_with_invalid_session_id_surfaces_storage_error_from_engine(
    adapter: SqliteBrainAdapter,
) -> None:
    # session_id=999 with FK ON → IntegrityError in engine → StorageError bubbles up
    # untouched by the adapter (no re-catch, no re-chain).
    with pytest.raises(StorageError) as exc_info:
        await adapter.store_snapshot(
            999,
            WorkspaceSnapshotInput(
                captured_at="2026-04-21T10:00:00+00:00",
                snapshot_type=SnapshotType.STARTUP,
                apps=(),
                focused_app=None,
                mode_name=None,
            ),
        )
    # The engine's translation chain: sqlite3.IntegrityError → StorageError.
    # The adapter must NOT re-wrap (would create StorageError → StorageError).
    cause = exc_info.value.__cause__
    assert isinstance(cause, sqlite3.IntegrityError)


async def test_get_last_snapshot_with_corrupt_json_translates_to_storage_error(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    await engine.execute(
        "INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data) "
        "VALUES (?, ?, ?, ?)",
        (1, "2026-04-21T10:00:00+00:00", "startup", "not json at all"),
    )
    with pytest.raises(StorageError) as exc_info:
        await adapter.get_last_snapshot_for_session(1)
    assert "brain adapter" in str(exc_info.value)
    # Message is opaque (no SQL, no session_id numeric, no row content).
    assert "INSERT" not in str(exc_info.value)
    assert "not json" not in str(exc_info.value)
    # Adapter-boundary translation: root cause is the JSON error.
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


async def test_get_last_snapshot_with_unknown_snapshot_type_translates_to_storage_error(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    await engine.execute(
        "INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data) "
        "VALUES (?, ?, ?, ?)",
        (
            1,
            "2026-04-21T10:00:00+00:00",
            "totally_bogus_type",
            '{"apps":[],"focused_app":null,"mode_name":null}',
        ),
    )
    with pytest.raises(StorageError) as exc_info:
        await adapter.get_last_snapshot_for_session(1)
    assert isinstance(exc_info.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "method_name,args,kwargs",
    [
        ("create_session", (None,), {"started_at": None}),
        (
            "end_session",
            (1,),
            {"seed_text": None, "summary": None, "is_complete": True},
        ),
        ("get_last_session", (), {}),
        ("get_last_seed", (), {}),
        (
            "store_snapshot",
            (
                1,
                WorkspaceSnapshotInput(
                    captured_at="2026-04-21T10:00:00+00:00",
                    snapshot_type=SnapshotType.STARTUP,
                    apps=(),
                    focused_app=None,
                    mode_name=None,
                ),
            ),
            {},
        ),
        # Story 3.2 addition — get_mode_last_used is a pure read via
        # storage.fetchone, same identity-propagation contract.
        ("get_mode_last_used", ("coding",), {}),
    ],
)
async def test_adapter_does_not_double_catch_storage_error_from_engine(
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    """Engine-raised StorageError bubbles up untouched — no re-wrap, no re-chain."""
    sentinel = StorageError("engine boundary failure — adapter must not re-wrap")

    async def _raise(*_a: object, **_k: object) -> object:
        raise sentinel

    # Monkeypatch every engine entry point the adapter might call.
    for method in (
        "execute",
        "execute_returning_lastrowid",
        "fetchone",
        "fetchall",
    ):
        monkeypatch.setattr(adapter._storage, method, _raise)

    method = getattr(adapter, method_name)
    with pytest.raises(StorageError) as exc_info:
        await method(*args, **kwargs)
    # Identity check — the SAME StorageError instance, not a re-chained one.
    assert exc_info.value is sentinel


# --- Retry / rerun / idempotency --------------------------------------------


async def test_brain_reads_setup_row_after_story_2_4_writes(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Simulate Story 2.4's persist_first_run row shape and read via Brain."""
    # Story 2.4's exact row shape: started_at = captured_at, is_complete=1,
    # mode_name/seed_text/summary NULL, ended_at stamped after the snapshot.
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, ?, NULL, NULL, NULL, 1)",
        ("2026-04-21T10:00:00+00:00", "2026-04-21T10:00:02+00:00"),
    )
    workspace_data = json.dumps(
        {"apps": ["code", "chrome"], "focused_app": "code", "mode_name": None},
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    await engine.execute(
        "INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data) "
        "VALUES (?, ?, ?, ?)",
        (1, "2026-04-21T10:00:00+00:00", str(SnapshotType.STARTUP), workspace_data),
    )

    last = await adapter.get_last_session()
    assert last is not None
    assert last.mode_name is None
    assert last.summary is None
    assert last.is_complete is True
    assert last.duration_seconds == 2  # 10:00:00 → 10:00:02

    assert await adapter.get_last_seed() is None

    snap = await adapter.get_last_snapshot_for_session(1)
    assert snap is not None
    assert snap.snapshot_type is SnapshotType.STARTUP
    assert snap.captured_at == "2026-04-21T10:00:00+00:00"
    assert [w.app_name for w in snap.windows] == ["code", "chrome"]
    # Lossy deserialization contract: synthesized WindowContexts only carry app_name.
    for w in snap.windows:
        assert w.window_title is None
        assert w.process_name is None
        assert w.is_opaque is False


async def test_snapshot_json_round_trip_preserves_story_2_4_shape(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Byte-exact JSON match between adapter writer and Story 2.4 writer."""
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    input_snapshot = WorkspaceSnapshotInput(
        captured_at="2026-04-21T10:00:00+00:00",
        snapshot_type=SnapshotType.STARTUP,
        apps=("chrome", "code"),
        focused_app="code",
        mode_name=None,
    )
    await adapter.store_snapshot(1, input_snapshot)
    row = await engine.fetchone(
        "SELECT workspace_data FROM workspace_snapshots ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
    # Byte-exact match to Story 2.4's compact shape.
    assert (
        row["workspace_data"] == '{"apps":["chrome","code"],"focused_app":"code","mode_name":null}'
    )


async def test_create_session_preserves_caller_started_at(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-override of started_at is preserved byte-exactly; clock NOT used."""
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2199-01-01T00:00:00+00:00")
    caller_ts = "2026-04-01T10:00:00+00:00"
    session_id = await adapter.create_session(mode_name=None, started_at=caller_ts)
    row = await engine.fetchone("SELECT started_at FROM sessions WHERE id = ?", (session_id,))
    assert row is not None
    assert row["started_at"] == caller_ts
    # Proves the clock was NOT used — caller-supplied timestamp won.


async def test_create_session_defaults_to_clock_when_started_at_is_none(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """started_at=None routes through events._utc_now_iso (module-attribute form)."""
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-21T10:00:00+00:00")
    session_id = await adapter.create_session(mode_name="coding", started_at=None)
    row = await engine.fetchone("SELECT started_at FROM sessions WHERE id = ?", (session_id,))
    assert row is not None
    assert row["started_at"] == "2026-04-21T10:00:00+00:00"


async def test_store_snapshot_preserves_captured_at_from_input(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """captured_at comes from the input — adapter never resamples the clock."""
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2199-01-01T00:00:00+00:00")
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    preserved = "2026-04-21T10:00:00+00:00"
    await adapter.store_snapshot(
        1,
        WorkspaceSnapshotInput(
            captured_at=preserved,
            snapshot_type=SnapshotType.STARTUP,
            apps=(),
            focused_app=None,
            mode_name=None,
        ),
    )
    row = await engine.fetchone(
        "SELECT captured_at FROM workspace_snapshots ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
    assert row["captured_at"] == preserved


# --- Port contract + Epic 5 scope -------------------------------------------


def test_sqlite_brain_adapter_structurally_satisfies_brainport(
    adapter: SqliteBrainAdapter,
) -> None:
    """mypy verifies this statically; the runtime check is belt-and-suspenders.

    ``BrainPort`` is a ``typing.Protocol``. Even without
    ``@runtime_checkable``, asserting the adapter has every expected
    attribute catches accidental method deletion.
    """
    port: BrainPort = adapter  # mypy strict accepts the assignment
    # Sanity: every port method attribute resolves on the adapter. This
    # list must stay in lockstep with ``BrainPort`` in ``nova.ports.brain``
    # and with ``PORT_CONTRACT[brain_port_module]`` in
    # ``tests/unit/ports/test_port_isolation.py``. Adding a method to the
    # port without updating this tuple gives a false pass (adapter "proves"
    # conformance to a stale contract).
    for name in (
        "create_session",
        "end_session",
        "get_last_session",
        "get_last_seed",
        "store_snapshot",
        "get_last_snapshot_for_session",
        "get_mode_last_used",  # Story 3.2 addition — parity with ports/brain.py
        "query_memory",
        "delete_matching",
        "confirm_deletion",
        "get_transparency_model",
    ):
        assert callable(getattr(port, name))


@pytest.mark.parametrize(
    "coro_factory",
    [
        lambda a: a.query_memory("anything"),
        lambda a: a.delete_matching("mode 'opaque'"),
        lambda a: a.confirm_deletion(
            DeletionPreview(
                target="mode 'opaque'", affected_tables=("sessions",), items_to_delete=0
            )
        ),
        lambda a: a.get_transparency_model(),
    ],
)
async def test_epic_5_methods_raise_not_implemented(
    adapter: SqliteBrainAdapter,
    coro_factory: object,
) -> None:
    """Story 3.1 AC #25 — parametrized over ALL four Epic 5 methods."""
    with pytest.raises(NotImplementedError, match="Epic 5 scope"):
        # ``coro_factory`` is a lambda returning the coroutine; await it.
        await coro_factory(adapter)  # type: ignore[operator]


@pytest.mark.parametrize(
    "started,ended,expected",
    [
        ("2026-04-21T10:00:00+00:00", None, 0),  # interrupted
        ("2026-04-21T10:00:00+00:00", "2026-04-21T10:00:00+00:00", 0),  # zero duration
        ("2026-04-21T10:00:00+00:00", "2026-04-21T10:00:02+00:00", 2),
        ("2026-04-21T10:00:00+00:00", "2026-04-21T11:00:00+00:00", 3600),
        ("2026-04-21T10:00:00+00:00", "not an iso string", 0),  # parse failure
        ("2026-04-21T11:00:00+00:00", "2026-04-21T10:00:00+00:00", 0),  # negative
    ],
)
def test_compute_duration_seconds(started: str, ended: str | None, expected: int) -> None:
    assert _compute_duration_seconds(started, ended) == expected


# --- Extra lossy-deserialization coverage -----------------------------------


async def test_get_last_snapshot_for_session_returns_none_when_no_row(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    assert await adapter.get_last_snapshot_for_session(1) is None


async def test_get_last_snapshot_with_apps_not_a_list_translates_to_storage_error(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, NULL, NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )
    await engine.execute(
        "INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data) "
        "VALUES (?, ?, ?, ?)",
        (
            1,
            "2026-04-21T10:00:00+00:00",
            "startup",
            '{"apps":"not a list","focused_app":null,"mode_name":null}',
        ),
    )
    with pytest.raises(StorageError):
        await adapter.get_last_snapshot_for_session(1)


async def test_round_trip_empty_apps_produces_empty_windows_tuple(
    adapter: SqliteBrainAdapter,
) -> None:
    session_id = await adapter.create_session(mode_name=None, started_at=None)
    await adapter.store_snapshot(
        session_id,
        WorkspaceSnapshotInput(
            captured_at="2026-04-21T10:00:00+00:00",
            snapshot_type=SnapshotType.SHUTDOWN,
            apps=(),
            focused_app=None,
            mode_name=None,
        ),
    )
    snap = await adapter.get_last_snapshot_for_session(session_id)
    assert snap is not None
    assert snap.windows == ()
    assert snap.snapshot_type is SnapshotType.SHUTDOWN


async def test_all_snapshot_types_round_trip(
    adapter: SqliteBrainAdapter,
) -> None:
    """Every SnapshotType value round-trips through the adapter's serializer.

    The timestamp suffix uses ``enumerate`` for a numeric second so the
    ISO-8601 strings are always valid. (An earlier revision used
    ``snapshot_type.value[0]`` — the first character of the enum's string
    value, which for ``STARTUP`` is ``'s'``, producing
    ``"2026-04-21T10:00:0s+00:00"`` — technically malformed ISO-8601 that
    the adapter happens not to reparse in this test.)
    """
    session_id = await adapter.create_session(mode_name=None, started_at=None)
    for i, snapshot_type in enumerate(SnapshotType):
        await adapter.store_snapshot(
            session_id,
            WorkspaceSnapshotInput(
                captured_at=f"2026-04-21T10:00:0{i}+00:00",
                snapshot_type=snapshot_type,
                apps=(),
                focused_app=None,
                mode_name=None,
            ),
        )
        snap = await adapter.get_last_snapshot_for_session(session_id)
        assert snap is not None
        assert snap.snapshot_type is snapshot_type


async def test_workspace_snapshot_is_deserialized_as_frozen_dataclass(
    adapter: SqliteBrainAdapter,
) -> None:
    session_id = await adapter.create_session(mode_name=None, started_at=None)
    await adapter.store_snapshot(
        session_id,
        WorkspaceSnapshotInput(
            captured_at="2026-04-21T10:00:00+00:00",
            snapshot_type=SnapshotType.STARTUP,
            apps=("code",),
            focused_app="code",
            mode_name=None,
        ),
    )
    snap = await adapter.get_last_snapshot_for_session(session_id)
    assert snap is not None
    assert isinstance(snap, WorkspaceSnapshot)
    with pytest.raises(AttributeError):
        snap.captured_at = "mutated"  # type: ignore[misc]


# --- Story 3.2 — get_mode_last_used ----------------------------------------


async def test_get_mode_last_used_returns_started_at_for_most_recent_session(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With two sessions for the same mode, the LATER one's started_at wins."""
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-20T09:00:00+00:00")
    first_id = await adapter.create_session(mode_name="coding", started_at=None)
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-21T10:00:00+00:00")
    second_id = await adapter.create_session(mode_name="coding", started_at=None)

    assert second_id > first_id  # AUTOINCREMENT is monotonic

    result = await adapter.get_mode_last_used("coding")
    assert result == "2026-04-21T10:00:00+00:00"


async def test_get_mode_last_used_returns_none_for_unused_mode(
    adapter: SqliteBrainAdapter,
) -> None:
    """A mode that never appears in ``sessions.mode_name`` returns None."""
    await adapter.create_session(mode_name="coding", started_at="2026-04-20T09:00:00+00:00")

    assert await adapter.get_mode_last_used("writing") is None


async def test_get_mode_last_used_returns_none_on_empty_db(
    adapter: SqliteBrainAdapter,
) -> None:
    """No sessions at all — returns None, never raises."""
    assert await adapter.get_mode_last_used("anything") is None


async def test_get_mode_last_used_filters_by_mode_name_exactly(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Filter is `=` not `LIKE` — ``coding`` does not match ``coding-v2`` or ``code``."""
    # Seed three sessions with similar-but-distinct mode names.
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, 'code', NULL, NULL, 0)",
        ("2026-04-20T09:00:00+00:00",),
    )
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, 'coding', NULL, NULL, 0)",
        ("2026-04-20T10:00:00+00:00",),
    )
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, 'coding-v2', NULL, NULL, 0)",
        ("2026-04-20T11:00:00+00:00",),
    )

    assert await adapter.get_mode_last_used("coding") == "2026-04-20T10:00:00+00:00"
    assert await adapter.get_mode_last_used("code") == "2026-04-20T09:00:00+00:00"
    assert await adapter.get_mode_last_used("coding-v2") == "2026-04-20T11:00:00+00:00"


async def test_get_mode_last_used_skips_sessions_with_null_mode_name(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Setup-row shape (mode_name=NULL) never matches any stem query.

    Locks Story 2.4 compatibility: the setup session has mode_name=NULL,
    and SQL `WHERE mode_name = 'coding'` with NULL on the left returns
    UNKNOWN (filtered out). Stem queries therefore look past the setup
    row to any real session with a populated mode_name.
    """
    # Row 1: setup-row shape (mode_name NULL), started earlier.
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, ?, NULL, NULL, NULL, 1)",
        ("2026-04-20T09:00:00+00:00", "2026-04-20T09:00:02+00:00"),
    )
    # Row 2: real runtime session with mode_name populated, started later.
    await engine.execute(
        "INSERT INTO sessions (started_at, ended_at, mode_name, seed_text, summary, is_complete) "
        "VALUES (?, NULL, 'coding', NULL, NULL, 0)",
        ("2026-04-21T10:00:00+00:00",),
    )

    assert await adapter.get_mode_last_used("coding") == "2026-04-21T10:00:00+00:00"


async def test_get_mode_last_used_is_idempotent_on_reread(
    adapter: SqliteBrainAdapter,
) -> None:
    """Repeated reads against the same row return byte-identical strings."""
    await adapter.create_session(mode_name="coding", started_at="2026-04-21T10:00:00+00:00")

    first = await adapter.get_mode_last_used("coding")
    second = await adapter.get_mode_last_used("coding")
    third = await adapter.get_mode_last_used("coding")
    assert first == second == third == "2026-04-21T10:00:00+00:00"


async def test_get_mode_last_used_returns_none_for_empty_string_mode_name(
    adapter: SqliteBrainAdapter,
) -> None:
    """Empty ``mode_name`` input returns ``None`` (docstring contract).

    No session carries an empty string as ``mode_name`` — setup writes
    ``NULL`` and runtime writes populated stems. An empty-string argument
    therefore matches no rows and the method returns ``None`` rather than
    raising. Locking this lets future drift in upstream writers
    (introducing an empty-string stem somewhere) surface as a failing
    test at this boundary instead of mysteriously succeeding.
    """
    # Seed a populated session so the query runs against real data and
    # only the empty-string filter returns no match.
    await adapter.create_session(mode_name="coding", started_at="2026-04-21T10:00:00+00:00")

    assert await adapter.get_mode_last_used("") is None


# ===========================================================================
# Story 3.7 — commit_shutdown (atomic three-write transactional commit)
# ===========================================================================


async def test_commit_shutdown_writes_all_three_rows_atomically(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Story 3.7 — sessions UPDATE + memory_items INSERT + workspace_snapshots INSERT."""
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    commit = ShutdownCommit(
        seed_text="finish auth tests",
        summary="Coding mode, 30m",
        snapshot_apps=("VS Code", "Postman"),
        snapshot_focused_app=None,
        snapshot_mode_name="coding",
    )
    ended_at = await adapter.commit_shutdown(sid, commit)
    sess = await engine.fetchone(
        "SELECT seed_text, summary, is_complete, ended_at FROM sessions WHERE id = ?",
        (sid,),
    )
    assert sess is not None
    assert sess["seed_text"] == "finish auth tests"
    assert sess["summary"] == "Coding mode, 30m"
    assert sess["is_complete"] == 1
    assert sess["ended_at"] == ended_at
    mem = await engine.fetchone(
        "SELECT category, content, created_at FROM memory_items WHERE session_id = ?",
        (sid,),
    )
    assert mem is not None
    assert mem["category"] == "seed"
    assert mem["content"] == "finish auth tests"
    assert mem["created_at"] == ended_at
    snap = await engine.fetchone(
        "SELECT snapshot_type, captured_at, workspace_data FROM workspace_snapshots "
        "WHERE session_id = ?",
        (sid,),
    )
    assert snap is not None
    assert snap["snapshot_type"] == "shutdown"
    assert snap["captured_at"] == ended_at


async def test_commit_shutdown_skips_memory_items_when_seed_text_is_none(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text=None,
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    sess = await engine.fetchone("SELECT seed_text, is_complete FROM sessions WHERE id = ?", (sid,))
    assert sess is not None
    assert sess["seed_text"] is None
    assert sess["is_complete"] == 1
    mem_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM memory_items WHERE session_id = ?", (sid,)
    )
    assert mem_count is not None and mem_count["n"] == 0
    snap_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM workspace_snapshots WHERE session_id = ?", (sid,)
    )
    assert snap_count is not None and snap_count["n"] == 1


async def test_commit_shutdown_writes_session_mode_name_for_get_mode_last_used_lookup(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Story 3.7 review patch — sessions.mode_name lands at shutdown.

    ``startup()`` creates the session with ``mode_name=None``; mode
    switches during the session do NOT update the column. Shutdown is
    the natural durable boundary where the active mode lands so the
    next session's ``get_mode_last_used("coding")`` (Story 3.2)
    enriches ``ModeInfo.last_used_at``.
    """
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text="finish auth tests",
            summary="Coding mode, 30m",
            snapshot_apps=("VS Code",),
            snapshot_focused_app=None,
            snapshot_mode_name="coding",
        ),
    )
    sess = await engine.fetchone("SELECT mode_name FROM sessions WHERE id = ?", (sid,))
    assert sess is not None
    assert sess["mode_name"] == "coding"
    # And get_mode_last_used resolves it.
    started_at = await adapter.get_mode_last_used("coding")
    assert started_at == "2026-04-01T10:00:00+00:00"


async def test_commit_shutdown_with_no_active_mode_keeps_session_mode_name_null(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Bare-boot shutdown (no mode active) leaves sessions.mode_name as NULL."""
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text=None,
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    sess = await engine.fetchone("SELECT mode_name FROM sessions WHERE id = ?", (sid,))
    assert sess is not None
    assert sess["mode_name"] is None


async def test_commit_shutdown_normalizes_empty_string_seed_to_null_and_skips_memory_item(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Adapter defense — ``seed_text=""`` is treated as "no seed" for BOTH writes.

    Nerve's ``_collect_seed_with_reprompt`` strips and rejects whitespace-only
    inputs, so ``""`` only ever reaches commit_shutdown via a future caller
    that bypasses Nerve. The adapter normalizes to keep the two writes
    consistent: sessions.seed_text=NULL AND zero memory_items rows.
    """
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text="",
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    sess = await engine.fetchone("SELECT seed_text FROM sessions WHERE id = ?", (sid,))
    assert sess is not None
    assert sess["seed_text"] is None  # normalized to NULL, NOT empty string
    mem_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM memory_items WHERE session_id = ?", (sid,)
    )
    assert mem_count is not None and mem_count["n"] == 0


async def test_commit_shutdown_uses_same_ended_at_across_all_three_rows(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Story 3.7 — single source of truth for the timestamp."""
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    ended_at = await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text="x",
            summary="x",
            snapshot_apps=("a",),
            snapshot_focused_app=None,
            snapshot_mode_name="coding",
        ),
    )
    sess_row = await engine.fetchone("SELECT ended_at FROM sessions WHERE id = ?", (sid,))
    mem_row = await engine.fetchone(
        "SELECT created_at FROM memory_items WHERE session_id = ?", (sid,)
    )
    snap_row = await engine.fetchone(
        "SELECT captured_at FROM workspace_snapshots WHERE session_id = ?", (sid,)
    )
    assert sess_row is not None
    assert mem_row is not None
    assert snap_row is not None
    assert sess_row["ended_at"] == mem_row["created_at"] == snap_row["captured_at"] == ended_at


async def test_commit_shutdown_persists_seed_category_as_string(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text="x",
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    row = await engine.fetchone("SELECT category FROM memory_items WHERE session_id = ?", (sid,))
    assert row is not None and row["category"] == "seed"


async def test_commit_shutdown_persists_apps_via_workspace_data_json(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """JSON shape locked to Story 2.4 / Story 3.1 — byte-exact."""
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text=None,
            summary=None,
            snapshot_apps=("VS Code", "Postman"),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    row = await engine.fetchone(
        "SELECT workspace_data FROM workspace_snapshots WHERE session_id = ?", (sid,)
    )
    assert row is not None
    expected = '{"apps":["VS Code","Postman"],"focused_app":null,"mode_name":null}'
    assert row["workspace_data"] == expected


async def test_commit_shutdown_logger_does_not_emit_content(
    adapter: SqliteBrainAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sensitive content (seed text, app names) MUST NOT appear in logs."""
    import logging

    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    secret_seed = "private-tomorrow-thought-12345"
    with caplog.at_level(logging.DEBUG, logger="nova.adapters.sqlite.brain"):
        await adapter.commit_shutdown(
            sid,
            ShutdownCommit(
                seed_text=secret_seed,
                summary=None,
                snapshot_apps=("SecretApp123",),
                snapshot_focused_app=None,
                snapshot_mode_name=None,
            ),
        )
    log_text = " ".join(r.message for r in caplog.records)
    assert secret_seed not in log_text
    assert "SecretApp123" not in log_text


async def test_commit_shutdown_returns_stamped_ended_at(
    adapter: SqliteBrainAdapter,
) -> None:
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    ended_at = await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text=None,
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    assert isinstance(ended_at, str)
    assert "T" in ended_at  # ISO-8601 shape (defensive)


async def test_commit_shutdown_raises_storage_error_when_session_does_not_exist(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Pre-write SELECT — a missing session_id is a programmer error.

    Surfaces loudly via :class:`StorageError` rather than silently
    inserting orphan memory_items / workspace_snapshots rows whose
    foreign keys would dangle.
    """
    from nova.systems.brain.models import ShutdownCommit

    bogus_session_id = 99999
    with pytest.raises(StorageError, match="session_id=99999"):
        await adapter.commit_shutdown(
            bogus_session_id,
            ShutdownCommit(
                seed_text="x",
                summary=None,
                snapshot_apps=("y",),
                snapshot_focused_app=None,
                snapshot_mode_name=None,
            ),
        )

    # No orphan rows landed.
    mem_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM memory_items WHERE session_id = ?", (bogus_session_id,)
    )
    assert mem_count is not None and mem_count["n"] == 0
    snap_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM workspace_snapshots WHERE session_id = ?", (bogus_session_id,)
    )
    assert snap_count is not None and snap_count["n"] == 0


async def test_commit_shutdown_re_called_on_completed_session_skips_all_writes(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Idempotency — re-call must NOT create duplicate seed memory or snapshot rows.

    First commit_shutdown finalizes the session; re-call returns the
    existing ended_at and SKIPS all three writes. The post-condition
    checks: sessions row unchanged, exactly ONE memory_items row,
    exactly ONE workspace_snapshots row.
    """
    import logging

    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")
    first_commit = ShutdownCommit(
        seed_text="first seed",
        summary="first summary",
        snapshot_apps=("FirstApp",),
        snapshot_focused_app=None,
        snapshot_mode_name="coding",
    )
    first_ended_at = await adapter.commit_shutdown(sid, first_commit)

    # Second call with DIFFERENT inputs — the impl must ignore them and
    # return the existing ended_at; no new rows MAY land.
    second_commit = ShutdownCommit(
        seed_text="second seed",
        summary="second summary",
        snapshot_apps=("SecondApp",),
        snapshot_focused_app=None,
        snapshot_mode_name="coding",
    )
    with caplog.at_level(logging.WARNING, logger="nova.adapters.sqlite.brain"):
        second_ended_at = await adapter.commit_shutdown(sid, second_commit)

    assert second_ended_at == first_ended_at

    # Sessions row carries the FIRST commit's data; not overwritten.
    sess = await engine.fetchone(
        "SELECT seed_text, summary, ended_at FROM sessions WHERE id = ?", (sid,)
    )
    assert sess is not None
    assert sess["seed_text"] == "first seed"
    assert sess["summary"] == "first summary"
    assert sess["ended_at"] == first_ended_at

    # Exactly ONE memory_items row (the first call's), NOT two.
    mems = await engine.fetchall("SELECT content FROM memory_items WHERE session_id = ?", (sid,))
    assert len(mems) == 1
    assert mems[0]["content"] == "first seed"

    # Exactly ONE workspace_snapshots row, with the first call's apps.
    snaps = await engine.fetchall(
        "SELECT workspace_data FROM workspace_snapshots WHERE session_id = ?", (sid,)
    )
    assert len(snaps) == 1
    assert "FirstApp" in snaps[0]["workspace_data"]
    assert "SecondApp" not in snaps[0]["workspace_data"]

    # WARNING fired about the no-op re-call.
    assert any("already-completed" in r.message for r in caplog.records)


async def test_commit_shutdown_re_called_with_no_seed_skips_all_writes(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Idempotency on the cancel-then-re-call sequence.

    Even if the first commit had ``seed_text=None`` (cancel path) and
    the second call provides a seed, the second call must still SKIP
    all writes — the row is already finalized.
    """
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text=None,
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    # Re-call WITH a seed — must skip the memory_items INSERT.
    await adapter.commit_shutdown(
        sid,
        ShutdownCommit(
            seed_text="late seed attempt",
            summary=None,
            snapshot_apps=(),
            snapshot_focused_app=None,
            snapshot_mode_name=None,
        ),
    )
    # Zero memory_items rows because the second call short-circuited.
    mem_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM memory_items WHERE session_id = ?", (sid,)
    )
    assert mem_count is not None and mem_count["n"] == 0


async def test_commit_shutdown_rolls_back_when_mid_transaction_failure(
    engine: SqliteStorageEngine,
    adapter: SqliteBrainAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomicity — a real mid-transaction failure rolls back ALL three writes.

    Inject a failure into ``_serialize_workspace_data`` (called between
    the memory_items INSERT and the workspace_snapshots INSERT) so the
    third write never fires; the previous two writes must roll back.
    """
    from nova.systems.brain.models import ShutdownCommit

    sid = await adapter.create_session(mode_name="coding", started_at="2026-04-01T10:00:00+00:00")

    # Explicit signature mirrors the production helper (apps + focused_app
    # + mode_name keyword-only). A future refactor that switches to
    # positional args at the call site would surface here as a TypeError
    # rather than silently passing through a permissive ``**kwargs``.
    def boom(
        *,
        apps: tuple[str, ...],
        focused_app: str | None,
        mode_name: str | None,
    ) -> str:
        del apps, focused_app, mode_name
        raise RuntimeError("simulated mid-transaction serializer failure")

    monkeypatch.setattr("nova.adapters.sqlite.brain._serialize_workspace_data", boom)

    with pytest.raises(RuntimeError, match="simulated"):
        await adapter.commit_shutdown(
            sid,
            ShutdownCommit(
                seed_text="rolled-back seed",
                summary="rolled-back summary",
                snapshot_apps=("a",),
                snapshot_focused_app=None,
                snapshot_mode_name="coding",
            ),
        )

    # All three writes rolled back: sessions still incomplete, no
    # memory_items row, no workspace_snapshots row.
    sess = await engine.fetchone("SELECT is_complete, seed_text FROM sessions WHERE id = ?", (sid,))
    assert sess is not None
    assert sess["is_complete"] == 0
    assert sess["seed_text"] is None
    mem_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM memory_items WHERE session_id = ?", (sid,)
    )
    assert mem_count is not None and mem_count["n"] == 0
    snap_count = await engine.fetchone(
        "SELECT COUNT(*) AS n FROM workspace_snapshots WHERE session_id = ?", (sid,)
    )
    assert snap_count is not None and snap_count["n"] == 0


# ===========================================================================
# Story 3.7 — end_session idempotency guard (closes deferred-work.md:231)
# ===========================================================================


async def test_end_session_re_called_on_completed_session_returns_existing_ended_at(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """Idempotency guard — second call does not overwrite seed/summary/ended_at."""
    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    first_ended_at = await adapter.end_session(
        sid, seed_text="first", summary="first", is_complete=True
    )
    second_ended_at = await adapter.end_session(
        sid, seed_text="second", summary="second", is_complete=True
    )
    assert first_ended_at == second_ended_at
    row = await engine.fetchone(
        "SELECT seed_text, summary, ended_at FROM sessions WHERE id = ?", (sid,)
    )
    assert row is not None
    assert row["seed_text"] == "first"  # NO overwrite
    assert row["summary"] == "first"
    assert row["ended_at"] == first_ended_at


async def test_end_session_re_called_logs_warning_about_no_op(
    adapter: SqliteBrainAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    await adapter.end_session(sid, seed_text="first", summary=None, is_complete=True)
    with caplog.at_level(logging.WARNING, logger="nova.adapters.sqlite.brain"):
        await adapter.end_session(sid, seed_text="second", summary=None, is_complete=True)
    assert any("already-completed" in r.message for r in caplog.records)


async def test_end_session_first_call_on_incomplete_row_proceeds_normally(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    ended_at = await adapter.end_session(sid, seed_text="seed", summary="summary", is_complete=True)
    row = await engine.fetchone(
        "SELECT seed_text, summary, is_complete, ended_at FROM sessions WHERE id = ?",
        (sid,),
    )
    assert row is not None
    assert row["seed_text"] == "seed"
    assert row["summary"] == "summary"
    assert row["is_complete"] == 1
    assert row["ended_at"] == ended_at


async def test_end_session_with_is_complete_false_can_be_called_multiple_times(
    engine: SqliteStorageEngine, adapter: SqliteBrainAdapter
) -> None:
    """The idempotency filter only blocks RE-completion (1→1); is_complete=False stays writable."""
    sid = await adapter.create_session(mode_name=None, started_at="2026-04-01T10:00:00+00:00")
    first = await adapter.end_session(sid, seed_text=None, summary=None, is_complete=False)
    second = await adapter.end_session(sid, seed_text=None, summary=None, is_complete=False)
    row = await engine.fetchone("SELECT is_complete FROM sessions WHERE id = ?", (sid,))
    assert row is not None and row["is_complete"] == 0
    assert first
    assert second


async def test_end_session_zero_rows_match_logs_warning_and_returns_iso(
    adapter: SqliteBrainAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="nova.adapters.sqlite.brain"):
        ended_at = await adapter.end_session(99999, seed_text=None, summary=None, is_complete=True)
    assert isinstance(ended_at, str) and "T" in ended_at
    assert any("matched zero rows" in r.message for r in caplog.records)
