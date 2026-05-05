"""Tests for :func:`nova.core.formatting.format_duration_seconds` (Story 3.3 Task 2).

Locks the value-based contract: the formatter maps any non-negative
integer of seconds to a render string, and **does not** encode
session-state policy. The "interrupted session ⇒ omit duration" decision
lives in Ritual's :func:`~nova.systems.ritual.system._build_last_session_label`,
not here.
"""

from __future__ import annotations

import pytest

from nova.core.formatting import format_duration_seconds


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        # --- Zero — completed session that rounded to 0 seconds. NOT a
        # "None / omit" sentinel; that policy belongs in Ritual. ---
        pytest.param(0, "0s", id="zero_seconds_renders_as_0s"),
        # --- Sub-minute band ---
        pytest.param(1, "1s", id="one_second"),
        pytest.param(45, "45s", id="forty_five_seconds"),
        pytest.param(59, "59s", id="fifty_nine_seconds_just_below_minute"),
        # --- Minute band; sub-minute remainder dropped ---
        pytest.param(60, "1m", id="one_minute_exact"),
        pytest.param(61, "1m", id="one_minute_one_second_drops_remainder"),
        pytest.param(120, "2m", id="two_minutes_exact"),
        pytest.param(3599, "59m", id="fifty_nine_minutes_just_below_hour"),
        # --- Hour band; sub-minute remainder dropped ---
        pytest.param(3600, "1h 0m", id="one_hour_exact"),
        pytest.param(3661, "1h 1m", id="one_hour_one_minute"),
        pytest.param(6120, "1h 42m", id="one_hour_forty_two_minutes"),
        pytest.param(43200, "12h 0m", id="twelve_hours_exact"),
    ],
)
def test_format_duration_seconds_canonical_cases(seconds: int, expected: str) -> None:
    assert format_duration_seconds(seconds) == expected


@pytest.mark.parametrize(
    "seconds",
    [
        pytest.param(-1, id="minus_one"),
        pytest.param(-3600, id="minus_one_hour"),
    ],
)
def test_format_duration_seconds_rejects_negative_input(seconds: int) -> None:
    with pytest.raises(ValueError, match="seconds must be non-negative"):
        format_duration_seconds(seconds)


def test_format_duration_seconds_is_pure(caplog: pytest.LogCaptureFixture) -> None:
    """Same input → byte-identical returns; no logging, no global mutation."""
    a = format_duration_seconds(6120)
    b = format_duration_seconds(6120)
    c = format_duration_seconds(6120)
    assert a == b == c == "1h 42m"
    assert caplog.records == []  # no logging side effect


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
    ],
)
def test_format_duration_seconds_rejects_bool(value: bool) -> None:
    """Review finding P15 — ``bool`` is an int subclass but not a duration.

    Without this guard, ``format_duration_seconds(True)`` would treat
    True as 1 and produce ``"Trues"`` (since f-string formatting of
    ``True`` gives ``"True"``, then appended ``"s"``). Surface the
    contract violation loudly with a TypeError instead of silently
    formatting nonsense.
    """
    with pytest.raises(TypeError, match="seconds must be int, not bool"):
        format_duration_seconds(value)


def test_format_duration_seconds_does_not_encode_interrupted_session_policy() -> None:
    """Documents the boundary: zero is "0s" (a value), not None (a policy signal).

    The interrupted-session policy lives in Ritual
    (``_build_last_session_label``), keyed on
    ``last_session.is_complete is False``. Any caller that infers
    "interrupted" from ``duration_seconds == 0`` is reading the WRONG
    upstream signal — this test pins the contract so a future helper
    cannot silently re-encode the policy.
    """
    assert format_duration_seconds(0) == "0s"
    assert format_duration_seconds(0) is not None


# ===========================================================================
# Story 3.7 — diff_iso_seconds (ISO-8601 duration parser)
# ===========================================================================


from nova.core.formatting import diff_iso_seconds  # noqa: E402


def test_diff_iso_seconds_returns_positive_seconds_for_valid_range() -> None:
    """30-minute window → 1800 seconds."""
    result = diff_iso_seconds(
        "2026-04-01T10:00:00+00:00",
        "2026-04-01T10:30:00+00:00",
    )
    assert result == 1800


def test_diff_iso_seconds_clamps_negative_to_zero() -> None:
    """Clock skew defense — ended_at < started_at clamps to 0."""
    result = diff_iso_seconds(
        "2026-04-01T11:00:00+00:00",
        "2026-04-01T10:00:00+00:00",
    )
    assert result == 0


def test_diff_iso_seconds_zero_for_equal_timestamps() -> None:
    iso = "2026-04-01T10:00:00+00:00"
    assert diff_iso_seconds(iso, iso) == 0


def test_diff_iso_seconds_handles_trailing_z_form() -> None:
    """Trailing-Z normalization — Python <3.11 compatibility."""
    result = diff_iso_seconds(
        "2026-04-01T10:00:00Z",
        "2026-04-01T10:30:00Z",
    )
    assert result == 1800


def test_diff_iso_seconds_handles_mixed_z_and_offset_forms() -> None:
    result = diff_iso_seconds(
        "2026-04-01T10:00:00Z",
        "2026-04-01T10:30:00+00:00",
    )
    assert result == 1800


def test_diff_iso_seconds_returns_integer_not_float() -> None:
    result = diff_iso_seconds(
        "2026-04-01T10:00:00.500+00:00",
        "2026-04-01T10:00:01.500+00:00",
    )
    assert isinstance(result, int)
    assert result == 1
