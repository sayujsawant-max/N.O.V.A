"""Story 1.2 round-trip serialization tests for `nova.core.types`."""

from __future__ import annotations

import re
from enum import Enum, StrEnum

import pytest

from nova.core.types import (
    ActionType,
    BluntnessLevel,
    BriefingState,
    CapabilityTier,
    MemoryCategory,
    SnapshotType,
)

NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _members(enum_cls: type[StrEnum]) -> list[tuple[StrEnum, str]]:
    return [(member, member.value) for member in enum_cls]


def _id(value: object) -> str:
    """Pytest id helper — show enum member name, fall back to str (P10).

    `StrEnum` members are `str` instances, so a naive `isinstance(v, str)`
    check matches both members and bare strings; `Enum` is the
    discriminator that actually separates them.
    """
    if isinstance(value, Enum):
        return value.name
    return str(value)


def _guaranteed_invalid_value(enum_cls: type[StrEnum]) -> str:
    """Compute a string guaranteed not to be a member of `enum_cls` (P11).

    Walks the value space; appends until no collision. Bulletproof against
    a future contributor accidentally landing on the previous magic
    sentinel.
    """
    valid = {m.value for m in enum_cls}
    candidate = "x"
    while candidate in valid:
        candidate += "x"
    return candidate


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(CapabilityTier),
    ids=_id,
)
def test_capability_tier_round_trip(member: CapabilityTier, expected: str) -> None:
    assert CapabilityTier(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(BriefingState),
    ids=_id,
)
def test_briefing_state_round_trip(member: BriefingState, expected: str) -> None:
    assert BriefingState(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(SnapshotType),
    ids=_id,
)
def test_snapshot_type_round_trip(member: SnapshotType, expected: str) -> None:
    assert SnapshotType(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(ActionType),
    ids=_id,
)
def test_action_type_round_trip(member: ActionType, expected: str) -> None:
    assert ActionType(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(MemoryCategory),
    ids=_id,
)
def test_memory_category_round_trip(member: MemoryCategory, expected: str) -> None:
    assert MemoryCategory(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    ("member", "expected"),
    _members(BluntnessLevel),
    ids=_id,
)
def test_bluntness_level_round_trip(member: BluntnessLevel, expected: str) -> None:
    assert BluntnessLevel(expected) is member
    assert member.value == expected
    assert str(member) == expected
    assert NAME_PATTERN.match(member.name)


@pytest.mark.parametrize(
    "enum_cls",
    [CapabilityTier, BriefingState, SnapshotType, ActionType, MemoryCategory, BluntnessLevel],
)
def test_invalid_value_raises(enum_cls: type[StrEnum]) -> None:
    with pytest.raises(ValueError):
        enum_cls(_guaranteed_invalid_value(enum_cls))


def test_bluntness_level_has_exactly_two_members() -> None:
    """Regression gate: ruthless is deferred to T2 per epics.md line 674."""
    assert len(list(BluntnessLevel)) == 2
    assert {m.value for m in BluntnessLevel} == {"calm", "direct"}


def test_capability_tier_exact_membership() -> None:
    assert {m.value for m in CapabilityTier} == {"full", "degraded", "offline"}


def test_briefing_state_exact_membership() -> None:
    assert {m.value for m in BriefingState} == {"first_run", "post_setup", "warm_resume"}


def test_snapshot_type_exact_membership() -> None:
    assert {m.value for m in SnapshotType} == {
        "startup",
        "shutdown",
        "mode_switch",
        "periodic",
    }


def test_action_type_exact_membership() -> None:
    assert {m.value for m in ActionType} == {
        "app_launch",
        "app_focus",
        "window_arrange",
        "mode_switch",
        "mode_restore",
        "mode_create",
        "mode_edit",
        "deletion",
        "seed_capture",
        "tier_change",
        "database_recovery",
        "setup_complete",
    }


def test_action_type_setup_complete_value() -> None:
    """Story 2.4 — ``SETUP_COMPLETE`` serializes as the canonical string.

    The first-run flow (``nova.setup.initial_capture.persist_first_run``)
    writes exactly one ``audit_log`` row with this action_type once setup
    reaches the "at least one mode ready + transactional session/snapshot
    write succeeded" milestone. The string literal is also the marker the
    ``__main__.py`` fast path probes for on subsequent ``setup.bat``
    invocations — changing the value is a schema-level event.
    """
    assert str(ActionType.SETUP_COMPLETE) == "setup_complete"
    assert ActionType("setup_complete") is ActionType.SETUP_COMPLETE


def test_memory_category_exact_membership() -> None:
    assert {m.value for m in MemoryCategory} == {
        "seed",
        "session_note",
        "context_summary",
        "pattern",
    }


def test_cross_enum_mode_switch_overlap_is_distinct_typed() -> None:
    """`mode_switch` is intentionally shared by SnapshotType and ActionType (P7).

    epics.md lines 671-672 pin both members. ``StrEnum`` semantics make the
    members `==` to each other and to the bare string. Pin the trap: the
    runtime types remain distinct, so Story 1.8's audit logger MUST use
    ``isinstance(x, ActionType)`` at its API boundary instead of
    bare-string comparison or duck-typing.
    """
    snap = SnapshotType.MODE_SWITCH
    act = ActionType.MODE_SWITCH
    # String-level equality is intentional (StrEnum semantics).
    # Cast through str() so the comparison goes through the StrEnum string
    # surface — mypy strict otherwise rejects the cross-enum `==` as a
    # `comparison-overlap` even though it succeeds at runtime.
    assert str(snap) == "mode_switch"
    assert str(act) == "mode_switch"
    assert str(snap) == str(act)
    # But the runtime types are distinct — isinstance is the safe check.
    assert type(snap) is SnapshotType
    assert type(act) is ActionType
    assert not isinstance(snap, ActionType)
    assert not isinstance(act, SnapshotType)
