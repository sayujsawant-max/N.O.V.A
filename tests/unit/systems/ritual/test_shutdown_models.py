"""Tests for Story 3.7 frozen dataclasses (ShutdownState, ShutdownViewModel).

ShutdownCommit (Brain-side input DTO) is tested separately at
``tests/unit/adapters/sqlite/test_brain_adapter.py`` since its concrete
behavior is locked at the adapter layer.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from nova.systems.brain.models import ShutdownCommit
from nova.systems.ritual.models import ShutdownState, ShutdownViewModel

# --- ShutdownState ----------------------------------------------------------


def test_shutdown_state_is_frozen() -> None:
    state = ShutdownState(
        session_id=1,
        started_at="x",
        ended_at="y",
        active_mode_stem=None,
        active_mode_display_name=None,
        apps_used=(),
    )
    with pytest.raises(FrozenInstanceError):
        state.session_id = 2  # type: ignore[misc]


def test_shutdown_state_apps_used_is_tuple_not_list() -> None:
    state = ShutdownState(
        session_id=1,
        started_at="x",
        ended_at="y",
        active_mode_stem=None,
        active_mode_display_name=None,
        apps_used=("a", "b"),
    )
    assert isinstance(state.apps_used, tuple)


def test_shutdown_state_constructs_with_all_fields() -> None:
    state = ShutdownState(
        session_id=42,
        started_at="2026-04-01T10:00:00Z",
        ended_at="2026-04-01T10:30:00Z",
        active_mode_stem="coding",
        active_mode_display_name="Coding",
        apps_used=("VS Code",),
    )
    assert state.session_id == 42
    assert state.active_mode_stem == "coding"
    assert state.active_mode_display_name == "Coding"


# --- ShutdownViewModel ------------------------------------------------------


def test_shutdown_view_model_is_frozen() -> None:
    vm = ShutdownViewModel(
        session_id=1,
        title="t",
        mode_label=None,
        duration_label="Duration: 0s",
        apps_label=None,
        prompt_text="p",
    )
    with pytest.raises(FrozenInstanceError):
        vm.title = "other"  # type: ignore[misc]


def test_shutdown_view_model_constructs_with_required_fields() -> None:
    vm = ShutdownViewModel(
        session_id=42,
        title="Session ending",
        mode_label="Mode: Coding",
        duration_label="Duration: 30m",
        apps_label="Apps: VS Code",
        prompt_text="What should you pick up tomorrow?",
    )
    assert vm.title == "Session ending"
    assert vm.duration_label == "Duration: 30m"


# --- ShutdownCommit ---------------------------------------------------------


def test_shutdown_commit_is_frozen() -> None:
    commit = ShutdownCommit(
        seed_text=None,
        summary=None,
        snapshot_apps=(),
        snapshot_focused_app=None,
        snapshot_mode_name=None,
    )
    with pytest.raises(FrozenInstanceError):
        commit.seed_text = "x"  # type: ignore[misc]


def test_shutdown_commit_constructs_with_required_fields() -> None:
    commit = ShutdownCommit(
        seed_text="x",
        summary="Coding mode, 30m",
        snapshot_apps=("VS Code",),
        snapshot_focused_app=None,
        snapshot_mode_name="coding",
    )
    assert commit.seed_text == "x"
    assert commit.snapshot_mode_name == "coding"


def test_shutdown_commit_snapshot_apps_is_tuple_not_list() -> None:
    """Story 1.9 AC #5 — sequence fields on frozen dataclasses are tuples."""
    commit = ShutdownCommit(
        seed_text=None,
        summary=None,
        snapshot_apps=("a", "b"),
        snapshot_focused_app=None,
        snapshot_mode_name=None,
    )
    assert isinstance(commit.snapshot_apps, tuple)


def test_shutdown_commit_does_not_carry_timestamp_field() -> None:
    """Story 3.7 — adapter is single source of truth for ended_at/created_at/captured_at.

    Caller-supplied timestamps would create cross-row drift; ShutdownCommit
    deliberately has NO timestamp-suffixed field.
    """
    field_names = {f.name for f in fields(ShutdownCommit)}
    timestamp_suffixes = ("_at", "ended_at", "created_at", "captured_at")
    forbidden = {name for name in field_names if any(name.endswith(s) for s in timestamp_suffixes)}
    assert not forbidden, (
        f"ShutdownCommit MUST NOT carry timestamp fields (adapter owns timestamps); "
        f"forbidden field(s) found: {forbidden}"
    )


# --- Export contract --------------------------------------------------------


def test_shutdown_data_no_longer_exported_from_ritual_models() -> None:
    """Story 3.7 — ShutdownData (Story 1.9 stub) is retired with zero callers."""
    import nova.systems.ritual.models as ritual_models

    assert "ShutdownData" not in ritual_models.__all__
    # Also assert the symbol itself isn't accidentally still defined.
    assert not hasattr(ritual_models, "ShutdownData")


def test_shutdown_state_and_view_model_are_exported_from_ritual_models() -> None:
    """Story 3.7 — both new dataclasses are public surface."""
    import nova.systems.ritual.models as ritual_models

    assert "ShutdownState" in ritual_models.__all__
    assert "ShutdownViewModel" in ritual_models.__all__


def test_shutdown_commit_is_exported_from_brain_models() -> None:
    """Story 3.7 — ShutdownCommit is the typed input DTO for BrainPort.commit_shutdown."""
    import nova.systems.brain.models as brain_models

    assert "ShutdownCommit" in brain_models.__all__


def test_brain_port_does_not_expose_add_memory_item_in_t1() -> None:
    """Story 3.7 — memory-item writes are encapsulated inside commit_shutdown's transaction.

    A standalone ``add_memory_item`` write surface would be Epic 4/5 scope
    when their use cases (session notes, context summaries, pattern memory)
    need a non-transactional write path. This test locks the T1 decision
    so a future regression that adds the standalone surface trips the
    test, prompting an explicit story-level decision.
    """
    from nova.ports.brain import BrainPort

    assert not hasattr(BrainPort, "add_memory_item"), (
        "BrainPort must NOT expose add_memory_item in T1 — "
        "Story 3.7 lands the seed-write inside commit_shutdown's transaction; "
        "standalone surface is Epic 4/5 scope"
    )
