"""Shape + runtime-validation regression tests for :class:`Command` (Story 3.4 Group A).

Two test groups:

* **Shape regression** (AC #3) — walks ``dataclasses.fields(Command)`` plus
  ``typing.get_type_hints(Command)`` to lock the field tuple, types,
  frozen-invariant, and the ``CommandVerb`` enum's exact 16 members.
* **Runtime validation** (AC #3) — exercises ``Command.__post_init__``
  to confirm the closed-vocabulary contract holds at runtime, not just
  under mypy strict. Without these tests, ``Command(verb="memoryy", ...)``
  would happily construct because Python does not enforce annotations
  at runtime.

Both groups read the runtime classes (no AST walk on source) — they
verify behavior of the imported types.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing

import pytest

from nova.systems.skin.commands import parse
from nova.systems.skin.models import Command, CommandVerb

# --- Shape regression --------------------------------------------------------

_EXPECTED_FIELD_NAMES: tuple[str, ...] = (
    "verb",
    "target",
    "raw_input",
    "is_contextual",
)

_EXPECTED_VERB_VALUES: tuple[str, ...] = (
    # Layer B routable verbs
    "mode",
    "status",
    "memory",
    "forget",
    "help",
    "shutdown",
    "mode_create",
    "mode_edit",
    # Layer C contextual replies
    "resume",
    "yes",
    "no",
    "skip",
    "cancel",
    "confirm",
    # Marker verbs
    "unknown",
    "empty",
)


def test_command_field_tuple_matches_declared_order() -> None:
    """The four field names appear in declaration order, no additions."""
    actual = tuple(f.name for f in dataclasses.fields(Command))
    assert actual == _EXPECTED_FIELD_NAMES


def test_command_field_type_annotations() -> None:
    """``verb`` is ``CommandVerb``; ``target`` is ``str | None``;
    ``raw_input`` is ``str``; ``is_contextual`` is ``bool``.
    """
    hints = typing.get_type_hints(Command)
    assert hints["verb"] is CommandVerb
    assert hints["target"] == (str | None)
    assert hints["raw_input"] is str
    assert hints["is_contextual"] is bool


def test_command_is_frozen() -> None:
    """``Command`` stays a frozen dataclass.

    ``__dataclass_params__`` is undocumented in mypy stubs (Story 3.3
    debug-log note), so we use ``getattr`` with a default and assert
    non-None to satisfy strict typing.
    """
    params = getattr(Command, "__dataclass_params__", None)
    assert params is not None
    assert params.frozen is True


def test_command_verb_enum_member_count_and_values() -> None:
    """``CommandVerb`` has exactly 16 members with the expected values.

    Adding a new verb requires a deliberate update to this list — that
    is the regression guard against silent vocabulary drift.
    """
    actual_values = tuple(member.value for member in CommandVerb)
    assert actual_values == _EXPECTED_VERB_VALUES
    assert len(CommandVerb) == 16


@pytest.mark.parametrize("expected_value", _EXPECTED_VERB_VALUES)
def test_command_verb_member_lookup_by_value(expected_value: str) -> None:
    """Each expected value resolves to a ``CommandVerb`` member via the
    ``CommandVerb(value)`` constructor.
    """
    member = CommandVerb(expected_value)
    assert isinstance(member, CommandVerb)
    assert member.value == expected_value


# --- Runtime validation (AC #3) ----------------------------------------------


def test_command_construction_with_enum_member_succeeds() -> None:
    """Passing a ``CommandVerb`` member directly is the canonical path."""
    cmd = Command(
        verb=CommandVerb.MODE,
        target="coding",
        raw_input="mode coding",
    )
    assert cmd.verb is CommandVerb.MODE
    assert cmd.target == "coding"
    assert cmd.raw_input == "mode coding"
    assert cmd.is_contextual is False


@pytest.mark.parametrize(
    ("input_string", "expected_member"),
    [
        ("mode", CommandVerb.MODE),
        ("shutdown", CommandVerb.SHUTDOWN),
        ("unknown", CommandVerb.UNKNOWN),
        ("empty", CommandVerb.EMPTY),
        ("resume", CommandVerb.RESUME),
    ],
)
def test_command_construction_coerces_valid_string_verb(
    input_string: str, expected_member: CommandVerb
) -> None:
    """A valid value-string is coerced to its ``CommandVerb`` member.

    Identity (``is``) — not just equality — confirms ``__post_init__``
    replaced the raw string with the enum member, so downstream
    pattern-match consumers can rely on enum identity.
    """
    cmd = Command(verb=input_string, target=None, raw_input=input_string)  # type: ignore[arg-type]
    assert cmd.verb is expected_member


@pytest.mark.parametrize(
    "bad_string",
    [
        "memoryy",  # typo
        "shutd",  # truncated
        "MODE",  # case mismatch — StrEnum is value-case-sensitive
        "",  # empty
        " ",  # whitespace-only
        "mode_unknown",  # plausible but not a member
        "fooBar",  # arbitrary
    ],
)
def test_command_construction_rejects_invalid_string_verb(bad_string: str) -> None:
    """Unknown value-strings raise ``ValueError`` mentioning the offending value
    AND preserving the underlying ``ValueError`` chain via ``raise ... from err``.
    """
    with pytest.raises(ValueError) as err:
        Command(verb=bad_string, target=None, raw_input=bad_string)  # type: ignore[arg-type]
    # ``__post_init__`` formats the offending value with ``!r`` — assert the
    # repr form appears in the message (not the bare value, which would
    # match an empty string trivially).
    assert repr(bad_string) in str(err.value)
    # ``raise ... from err`` chain preservation (cross-cutting-patterns.md #4).
    assert err.value.__cause__ is not None
    assert isinstance(err.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "bad_value",
    [
        42,
        None,
        ["mode"],
        ("mode",),
        object(),
    ],
)
def test_command_construction_rejects_non_string_non_enum_verb(bad_value: object) -> None:
    """Non-string, non-enum values raise ``TypeError`` mentioning the type."""
    with pytest.raises(TypeError) as err:
        Command(verb=bad_value, target=None, raw_input="x")  # type: ignore[arg-type]
    assert type(bad_value).__name__ in str(err.value)


def test_command_remains_frozen_after_post_init_coercion() -> None:
    """The ``object.__setattr__`` inside ``__post_init__`` does NOT leak
    frozen-bypass to caller code — frozen semantics still hold after
    construction.
    """
    cmd = Command(verb="mode", target=None, raw_input="mode")  # type: ignore[arg-type]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cmd.verb = CommandVerb.STATUS  # type: ignore[misc]


# --- Function-shape contract -----------------------------------------------


def test_parse_is_sync_function() -> None:
    """``parse`` is sync; the ``async`` lives at the SkinPort boundary
    (``RichSkinAdapter.parse_command`` wraps the sync call). The parser
    does no awaitable work, so a thread hop would add overhead with no
    benefit. The async-shape companion lock for the adapter lives in
    ``tests/unit/adapters/rich/test_skin_adapter.py``.
    """
    assert inspect.iscoroutinefunction(parse) is False
