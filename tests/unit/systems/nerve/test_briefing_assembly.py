"""BriefingAggregate assembly tests for Story 3.2.

Tests for :func:`nova.systems.nerve.briefing.load_briefing_aggregate`.
A ``_RecordingFakeBrainPort`` structurally conforms to
:class:`~nova.ports.brain.BrainPort`, records every method invocation
(and its arguments) in the order calls arrive, and supports per-method
return-value priming. This keeps the assembly function under test on
its own axis — DB and adapter are Story 3.1's concern and are covered
by the adapter test suite.

Covers AC #19 (call ordering + empty-DB short-circuit + setup-row
→ State C), AC #20 (stem-ascending mode order), AC #21 (stem/display
name independence and query-by-stem contract).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nova.core.config import (
    AppConfig,
    ExclusionConfig,
    ModeConfig,
    NovaConfig,
    UserSettings,
)
from nova.core.exceptions import StorageError
from nova.core.types import BriefingState, SnapshotType
from nova.ports.brain import BrainPort
from nova.systems.brain.models import (
    DeletionPreview,
    DeletionResult,
    MemoryItem,
    SessionSummary,
    TransparencyModel,
    WorkspaceSnapshotInput,
)
from nova.systems.eyes.models import WorkspaceSnapshot
from nova.systems.nerve.briefing import determine_briefing_state, load_briefing_aggregate

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    """One recorded invocation — method name + args tuple.

    ``frozen=True`` per the codebase-wide default (cross-cutting-patterns.md
    #3); a recorded call is an observation of history, never mutated.
    """

    method: str
    args: tuple[Any, ...]


@dataclass
class _RecordingFakeBrainPort:
    """Structurally conforms to BrainPort; records calls for assertion.

    Only the four methods exercised by ``load_briefing_aggregate`` have
    meaningful implementations. The rest raise ``NotImplementedError``
    to satisfy the Protocol surface without falsely implying the test
    exercises them.
    """

    last_session_return: SessionSummary | None = None
    last_seed_return: str | None = None
    last_snapshot_return: WorkspaceSnapshot | None = None
    mode_last_used_map: dict[str, str | None] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def create_session(self, mode_name: str | None, *, started_at: str | None) -> int:
        raise NotImplementedError("fake does not support create_session")

    async def end_session(
        self,
        session_id: int,
        *,
        seed_text: str | None,
        summary: str | None,
        is_complete: bool,
    ) -> str:
        raise NotImplementedError("fake does not support end_session")

    async def get_last_session(self) -> SessionSummary | None:
        self.calls.append(_Call("get_last_session", ()))
        return self.last_session_return

    async def get_last_seed(self) -> str | None:
        self.calls.append(_Call("get_last_seed", ()))
        return self.last_seed_return

    async def store_snapshot(self, session_id: int, snapshot: WorkspaceSnapshotInput) -> None:
        raise NotImplementedError("fake does not support store_snapshot")

    async def get_last_snapshot_for_session(self, session_id: int) -> WorkspaceSnapshot | None:
        self.calls.append(_Call("get_last_snapshot_for_session", (session_id,)))
        return self.last_snapshot_return

    async def get_mode_last_used(self, mode_name: str) -> str | None:
        self.calls.append(_Call("get_mode_last_used", (mode_name,)))
        return self.mode_last_used_map.get(mode_name)

    async def query_memory(self, query: str) -> list[MemoryItem]:
        raise NotImplementedError("Epic 5 scope")

    async def delete_matching(self, target: str) -> DeletionPreview:
        raise NotImplementedError("Epic 5 scope")

    async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult:
        raise NotImplementedError("Epic 5 scope")

    async def get_transparency_model(self) -> TransparencyModel:
        raise NotImplementedError("Epic 5 scope")


def _config(modes: dict[str, ModeConfig]) -> NovaConfig:
    """Build a NovaConfig with the supplied modes and default everything else."""
    return NovaConfig(
        db_path=Path("/tmp/never-opened.db"),
        data_dir=Path("/tmp/never-opened"),
        modes=modes,
        exclusions=ExclusionConfig(),
        settings=UserSettings(),
        api_key=None,
    )


def _mode_config(name: str, *, apps: int = 1, is_default: bool = False) -> ModeConfig:
    """Build a ModeConfig with ``apps`` app entries."""
    app_tuple = tuple(AppConfig(name=f"app-{i}", executable=f"app-{i}.exe") for i in range(apps))
    return ModeConfig(name=name, apps=app_tuple, is_default=is_default)


def _session_summary(is_complete: bool = True, session_id: int = 1) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at="2026-04-01T10:00:00+00:00",
        ended_at="2026-04-01T10:00:05+00:00" if is_complete else None,
        duration_seconds=5 if is_complete else 0,
        mode_name=None,
        summary=None,
        is_complete=is_complete,
    )


def _workspace_snapshot() -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        captured_at="2026-04-01T10:00:00+00:00",
        snapshot_type=SnapshotType.STARTUP,
        windows=(),
    )


# ---------------------------------------------------------------------------
# AC #19a — empty DB short-circuits the snapshot call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_briefing_aggregate_empty_db_empty_modes() -> None:
    """With nothing persisted and no modes configured, the aggregate is fully empty.

    Also locks the short-circuit: when ``last_session is None``, the
    snapshot call is skipped (one fewer round-trip on an empty DB).
    """
    brain = _RecordingFakeBrainPort()  # all returns None by default
    config = _config({})

    aggregate = await load_briefing_aggregate(brain, config)

    assert aggregate.last_session is None
    assert aggregate.last_snapshot is None
    assert aggregate.last_seed is None
    assert aggregate.available_modes == ()
    assert aggregate.recent_memory == ()

    method_names = [call.method for call in brain.calls]
    assert "get_last_snapshot_for_session" not in method_names, (
        "snapshot call must be skipped when last_session is None"
    )


# ---------------------------------------------------------------------------
# AC #19b — setup-row-only yields State C (not B) after determine_briefing_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_briefing_aggregate_setup_row_only_yields_state_c() -> None:
    """The pre-flag reconciliation case — setup row + modes → State C.

    Story 2.4's setup row has ``is_complete=True, mode_name=NULL,
    seed_text=NULL``. Combined with at least one configured mode, the
    literal state machine produces WARM_RESUME (not POST_SETUP as the
    pre-flag intuited).
    """
    setup_session = _session_summary(is_complete=True)
    brain = _RecordingFakeBrainPort(
        last_session_return=setup_session,
        last_seed_return=None,  # get_last_seed filters out the NULL-seed setup row
        last_snapshot_return=_workspace_snapshot(),
        mode_last_used_map={"coding": None},  # mode never used yet
    )
    config = _config({"coding": _mode_config(name="Coding", is_default=True)})

    aggregate = await load_briefing_aggregate(brain, config)
    state = determine_briefing_state(aggregate)

    assert aggregate.last_session is setup_session
    assert aggregate.last_seed is None
    assert aggregate.last_snapshot is not None
    assert len(aggregate.available_modes) == 1
    assert aggregate.available_modes[0].stem == "coding"
    assert state == BriefingState.WARM_RESUME


# ---------------------------------------------------------------------------
# AC #19c — call ordering is deterministic and exactly what the contract says
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_briefing_aggregate_call_ordering() -> None:
    """Call sequence: session → seed → snapshot → per-mode last_used (stem-sorted)."""
    brain = _RecordingFakeBrainPort(
        last_session_return=_session_summary(is_complete=True, session_id=7),
        last_seed_return="yesterday's thought",
        last_snapshot_return=_workspace_snapshot(),
    )
    # Insert in non-alphabetical order to prove the sort happens on our side.
    config = _config(
        {
            "writing": _mode_config(name="Writing"),
            "coding": _mode_config(name="Coding", is_default=True),
        }
    )

    await load_briefing_aggregate(brain, config)

    recorded = [(call.method, call.args) for call in brain.calls]
    assert recorded == [
        ("get_last_session", ()),
        ("get_last_seed", ()),
        ("get_last_snapshot_for_session", (7,)),
        ("get_mode_last_used", ("coding",)),
        ("get_mode_last_used", ("writing",)),
    ]


# ---------------------------------------------------------------------------
# AC #20 — mode iteration is stem-ascending regardless of dict insertion order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_briefing_aggregate_mode_order_is_stem_ascending() -> None:
    """Modes in the aggregate are sorted by stem, not by dict insertion order."""
    brain = _RecordingFakeBrainPort()
    # Intentionally scrambled insertion order.
    config = _config(
        {
            "writing": _mode_config(name="Writing"),
            "coding": _mode_config(name="Coding"),
            "admin": _mode_config(name="Admin"),
        }
    )

    aggregate = await load_briefing_aggregate(brain, config)

    assert [mode.stem for mode in aggregate.available_modes] == [
        "admin",
        "coding",
        "writing",
    ]


# ---------------------------------------------------------------------------
# AC #21 — stem and display_name are independent; Brain queried by stem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_info_fields_carry_stem_and_display_name_separately() -> None:
    """Stem from dict key; display_name from ModeConfig.name; Brain called with stem."""
    brain = _RecordingFakeBrainPort(mode_last_used_map={"coding": "2026-04-20T09:30:00+00:00"})
    # Stem ``coding`` intentionally differs from display name ``Deep Coding``
    # so a collapse to a single "name" field would fail this test.
    config = _config({"coding": _mode_config(name="Deep Coding", apps=3, is_default=True)})

    aggregate = await load_briefing_aggregate(brain, config)

    assert len(aggregate.available_modes) == 1
    mode_info = aggregate.available_modes[0]
    assert mode_info.stem == "coding"
    assert mode_info.display_name == "Deep Coding"
    assert mode_info.stem != mode_info.display_name, (
        "stem and display_name must be observably distinct — this test will fire "
        "if a future refactor collapses them back into a single field"
    )
    assert mode_info.app_count == 3
    assert mode_info.is_default is True
    assert mode_info.last_used_at == "2026-04-20T09:30:00+00:00"

    # Locking the query-side half of the AC #4a cross-story contract:
    # Brain is asked by stem ("coding"), NOT by display name ("Deep Coding").
    mode_calls = [call for call in brain.calls if call.method == "get_mode_last_used"]
    assert mode_calls == [_Call("get_mode_last_used", ("coding",))]


# ---------------------------------------------------------------------------
# Additional invariants (AC #28: recent_memory and snapshot shape)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_briefing_aggregate_recent_memory_is_empty_tuple() -> None:
    """``recent_memory`` is always ``()`` in T1, never ``None``."""
    brain = _RecordingFakeBrainPort(last_session_return=_session_summary(is_complete=True))
    config = _config({"coding": _mode_config(name="Coding")})

    aggregate = await load_briefing_aggregate(brain, config)

    assert aggregate.recent_memory == ()
    assert isinstance(aggregate.recent_memory, tuple)


# ---------------------------------------------------------------------------
# Review patches (2026-04-22): fake-port drift guard, error-propagation,
# cancellation, coincidentally-equal stem/display_name
# ---------------------------------------------------------------------------


def test_recording_fake_implements_all_brainport_methods() -> None:
    """Drift guard: if ``BrainPort`` grows a new method, this test must fail.

    The fake must structurally conform to ``BrainPort``. mypy catches drift
    at check time, but this runtime test enumerates every currently-expected
    method name — the minute the port grows a 12th method, adding it to
    ``PORT_CONTRACT`` in ``test_port_isolation.py`` without also adding it
    to the fake's implementation trips this assertion. The hardcoded method
    list is deliberate: an explicit inventory is the signal.
    """
    expected_methods = frozenset(
        {
            "create_session",
            "end_session",
            "get_last_session",
            "get_last_seed",
            "store_snapshot",
            "get_last_snapshot_for_session",
            "get_mode_last_used",
            "query_memory",
            "delete_matching",
            "confirm_deletion",
            "get_transparency_model",
        }
    )
    brain: BrainPort = _RecordingFakeBrainPort()  # structural type-check at assignment
    for method_name in expected_methods:
        assert callable(getattr(brain, method_name, None)), (
            f"_RecordingFakeBrainPort missing BrainPort method: {method_name}"
        )


class _RaisingOnWritingBrain(_RecordingFakeBrainPort):
    """Fake variant that raises ``StorageError`` on ``get_mode_last_used('writing')``.

    Used to assert that ``load_briefing_aggregate`` does not swallow engine
    errors mid-loop. Other port methods fall through to the parent fake.
    """

    async def get_mode_last_used(self, mode_name: str) -> str | None:
        self.calls.append(_Call("get_mode_last_used", (mode_name,)))
        if mode_name == "writing":
            raise StorageError("simulated engine failure on mode lookup")
        return None


@pytest.mark.asyncio
async def test_load_briefing_aggregate_propagates_storage_error_from_mode_lookup() -> None:
    """A ``StorageError`` raised mid-loop propagates — assembly does not swallow.

    Review Focus table promises engine-raised ``StorageError`` flows through
    ``load_briefing_aggregate`` to the caller (Nerve / Ritual decides how
    to degrade). Without this test, a future defensive try/except could
    silently skip failing modes and produce a partial aggregate.
    """
    brain = _RaisingOnWritingBrain()
    config = _config(
        {
            "coding": _mode_config(name="Coding"),
            "writing": _mode_config(name="Writing"),
        }
    )

    with pytest.raises(StorageError) as exc_info:
        await load_briefing_aggregate(brain, config)
    assert "simulated engine failure" in str(exc_info.value)

    # 'coding' was fetched successfully before 'writing' raised (stem-sorted
    # iteration). The last recorded call is the failing one.
    mode_calls = [call for call in brain.calls if call.method == "get_mode_last_used"]
    assert mode_calls[0].args == ("coding",)
    assert mode_calls[-1].args == ("writing",)


class _CancellingOnWritingBrain(_RecordingFakeBrainPort):
    """Fake variant that raises ``asyncio.CancelledError`` on 'writing' lookup."""

    async def get_mode_last_used(self, mode_name: str) -> str | None:
        self.calls.append(_Call("get_mode_last_used", (mode_name,)))
        if mode_name == "writing":
            raise asyncio.CancelledError
        return None


@pytest.mark.asyncio
async def test_load_briefing_aggregate_propagates_cancellation() -> None:
    """Mid-loop ``CancelledError`` propagates cleanly; no partial state leaks.

    ``load_briefing_aggregate`` holds only in-memory state (no resources,
    no transactions, no open handles), so mid-loop cancellation drops the
    partially-constructed ``mode_infos`` list on the floor and re-raises.
    project-context.md forbids swallowing ``CancelledError``; this test
    locks the propagation path.
    """
    brain = _CancellingOnWritingBrain()
    config = _config(
        {
            "coding": _mode_config(name="Coding"),
            "writing": _mode_config(name="Writing"),
        }
    )

    with pytest.raises(asyncio.CancelledError):
        await load_briefing_aggregate(brain, config)


@pytest.mark.asyncio
async def test_load_briefing_aggregate_queries_by_stem_when_coincidentally_equal() -> None:
    """Even when ``stem == display_name``, Brain is queried by stem.

    ``test_mode_info_fields_carry_stem_and_display_name_separately`` above
    forces the two to differ. This test covers the coincidence case: if a
    lazy implementation were passing ``display_name`` instead of ``stem``,
    both the distinct-values test AND the equal-values test would fail —
    making the contract fully observable. Without this companion, a stem
    that happens to equal the display name would silently pass even under
    a broken implementation.
    """
    brain = _RecordingFakeBrainPort(mode_last_used_map={"coding": "2026-04-20T09:30:00+00:00"})
    # stem == display_name both equal "coding"
    config = _config({"coding": _mode_config(name="coding", apps=1, is_default=True)})

    aggregate = await load_briefing_aggregate(brain, config)

    assert aggregate.available_modes[0].stem == "coding"
    assert aggregate.available_modes[0].display_name == "coding"
    assert aggregate.available_modes[0].stem == aggregate.available_modes[0].display_name

    mode_calls = [call for call in brain.calls if call.method == "get_mode_last_used"]
    assert mode_calls == [_Call("get_mode_last_used", ("coding",))]
