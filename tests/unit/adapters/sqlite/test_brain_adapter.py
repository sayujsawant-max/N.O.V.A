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
