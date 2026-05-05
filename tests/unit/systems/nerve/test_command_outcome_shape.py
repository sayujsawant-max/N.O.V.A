"""Shape regression test for :class:`nova.systems.nerve.models.CommandOutcome`.

Story 3.5 AC #3 locks the closed two-member vocabulary so a future
maintainer adding ``ABORT`` / ``RESET`` / similar must update both the
enum and this test in one PR.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from nova.systems.nerve.models import CommandOutcome

_EXPECTED_MEMBERS: tuple[tuple[str, str], ...] = (
    ("CONTINUE", "continue"),
    ("EXIT", "exit"),
)


def test_command_outcome_subclasses_str_enum() -> None:
    """Closed-set vocabulary discipline — must be ``StrEnum``, not ``Enum``."""
    assert issubclass(CommandOutcome, StrEnum)


def test_command_outcome_has_exactly_two_members() -> None:
    """Adding a third outcome is a deliberate update to this test."""
    members = tuple((m.name, m.value) for m in CommandOutcome)
    assert members == _EXPECTED_MEMBERS


@pytest.mark.parametrize(("name", "value"), _EXPECTED_MEMBERS)
def test_command_outcome_member_value(name: str, value: str) -> None:
    """Each member's ``.value`` matches the canonical string."""
    member = CommandOutcome[name]
    assert member.value == value


@pytest.mark.parametrize(("name", "value"), _EXPECTED_MEMBERS)
def test_command_outcome_value_lookup_returns_identity(name: str, value: str) -> None:
    """``CommandOutcome("continue") is CommandOutcome.CONTINUE`` — identity, not equality.

    The identity invariant is what makes ``if outcome is CommandOutcome.EXIT``
    in the REPL safe; an equality-only contract would silently widen if a
    consumer constructed a new ``CommandOutcome`` instance from a string.
    """
    assert CommandOutcome(value) is CommandOutcome[name]


def test_command_outcome_str_value_matches_value() -> None:
    """``StrEnum`` guarantees ``str(member) == member.value`` — lock it."""
    assert str(CommandOutcome.CONTINUE) == "continue"
    assert str(CommandOutcome.EXIT) == "exit"


def test_command_outcome_rejects_unknown_value() -> None:
    """``CommandOutcome("abort")`` raises ``ValueError`` — closed set."""
    with pytest.raises(ValueError, match="abort"):
        CommandOutcome("abort")
