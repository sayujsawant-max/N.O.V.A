"""Tie-break ladder tests for the suggested-mode helpers (Story 3.3 AC #25).

Locks each rung independently and the cascade between rungs:
    a. State C only — match ``last_session.mode_name`` against an
       available mode's stem.
    b. Most recent ``last_used_at`` (lexicographic ISO sort).
    c. ``is_default=True`` mode with the alphabetically-first stem.
    d. Alphabetically-first stem.
    e. ``None`` (no available modes).
"""

from __future__ import annotations

from nova.systems.brain.models import BriefingAggregate, ModeInfo, SessionSummary
from nova.systems.ritual.system import (
    _select_suggested_mode_for_state_b,
    _select_suggested_mode_for_state_c,
)


def _mode(
    stem: str,
    display_name: str,
    *,
    app_count: int = 1,
    is_default: bool = False,
    last_used_at: str | None = None,
) -> ModeInfo:
    return ModeInfo(
        stem=stem,
        display_name=display_name,
        app_count=app_count,
        is_default=is_default,
        last_used_at=last_used_at,
    )


def _aggregate(
    *,
    available_modes: tuple[ModeInfo, ...],
    last_session: SessionSummary | None = None,
) -> BriefingAggregate:
    return BriefingAggregate(
        last_session=last_session,
        last_snapshot=None,
        last_seed=None,
        available_modes=available_modes,
        recent_memory=(),
    )


def _session(*, mode_name: str | None) -> SessionSummary:
    return SessionSummary(
        session_id=1,
        started_at="2026-04-01T10:00:00+00:00",
        ended_at="2026-04-01T11:00:00+00:00",
        duration_seconds=3600,
        mode_name=mode_name,
        summary=None,
        is_complete=True,
    )


# --- Rung a (State C only) ---------------------------------------------------


def test_state_c_prefers_last_session_mode_match() -> None:
    """Rung a beats rungs b/c/d when last_session.mode_name has a matching stem."""
    coding = _mode(
        "coding",
        "Coding",
        last_used_at="2026-04-01T10:00:00+00:00",
    )
    writing = _mode(
        "writing",
        "Writing",
        is_default=True,
        last_used_at="2026-04-02T10:00:00+00:00",  # more recent than coding
    )
    aggregate = _aggregate(
        available_modes=(coding, writing),
        last_session=_session(mode_name="coding"),
    )
    # Even though writing has a more recent last_used_at AND is the default,
    # rung a wins on stem match with last_session.mode_name="coding".
    assert _select_suggested_mode_for_state_c(aggregate) == coding


def test_state_c_falls_through_when_last_mode_not_in_available() -> None:
    """Rung a fails (mode deleted) → rungs b/c/d fire."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = _aggregate(
        available_modes=(coding,),
        last_session=_session(mode_name="archived"),  # no match
    )
    assert _select_suggested_mode_for_state_c(aggregate) == coding


# --- Rung b (most recent last_used_at) ---------------------------------------


def test_picks_most_recent_last_used_at_when_present() -> None:
    """Three modes; two have timestamps, one is unused → most-recent wins."""
    older = _mode("admin", "Admin", last_used_at="2026-04-01T10:00:00+00:00")
    newer = _mode("coding", "Coding", last_used_at="2026-04-02T10:00:00+00:00")
    unused = _mode("writing", "Writing")
    aggregate = _aggregate(available_modes=(older, newer, unused))
    assert _select_suggested_mode_for_state_c(aggregate) == newer


def test_breaks_last_used_at_tie_alphabetically() -> None:
    """Same timestamp → alphabetically-first stem wins."""
    same_time = "2026-04-01T10:00:00+00:00"
    coding = _mode("coding", "Coding", last_used_at=same_time)
    admin = _mode("admin", "Admin", last_used_at=same_time)
    aggregate = _aggregate(available_modes=(coding, admin))
    assert _select_suggested_mode_for_state_c(aggregate) == admin


# --- Rung c (is_default tie-break) -------------------------------------------


def test_falls_back_to_default_when_no_last_used_at() -> None:
    """No timestamps → default wins."""
    not_default = _mode("admin", "Admin")
    default = _mode("coding", "Coding", is_default=True)
    other = _mode("writing", "Writing")
    aggregate = _aggregate(available_modes=(not_default, default, other))
    assert _select_suggested_mode_for_state_c(aggregate) == default


def test_breaks_default_tie_alphabetically() -> None:
    """Two defaults → alphabetically-first stem wins."""
    coding_default = _mode("coding", "Coding", is_default=True)
    admin_default = _mode("admin", "Admin", is_default=True)
    other = _mode("writing", "Writing")
    aggregate = _aggregate(available_modes=(coding_default, admin_default, other))
    assert _select_suggested_mode_for_state_c(aggregate) == admin_default


# --- Rung d (alphabetical) ---------------------------------------------------


def test_falls_back_to_alphabetically_first_when_no_default() -> None:
    """No timestamps, no defaults → first stem alphabetically."""
    writing = _mode("writing", "Writing")
    coding = _mode("coding", "Coding")
    admin = _mode("admin", "Admin")
    aggregate = _aggregate(available_modes=(writing, coding, admin))
    assert _select_suggested_mode_for_state_c(aggregate) == admin


# --- Rung e (None) -----------------------------------------------------------


def test_returns_none_for_empty_modes() -> None:
    aggregate = _aggregate(available_modes=())
    assert _select_suggested_mode_for_state_c(aggregate) is None
    assert _select_suggested_mode_for_state_b(aggregate) is None


# --- State B specific lock ---------------------------------------------------


def test_state_b_ignores_last_session_match_rung() -> None:
    """State B helper does NOT consult last_session — rung a is skipped.

    State B by the state-machine definition has no usable last_session
    (Nerve already excluded that). The State B helper must apply the
    same mode-selection logic regardless of whatever happens to be in
    ``aggregate.last_session``.
    """
    coding = _mode("coding", "Coding")
    writing = _mode("writing", "Writing", is_default=True)
    aggregate = _aggregate(
        available_modes=(coding, writing),
        last_session=_session(mode_name="coding"),  # would win rung a in State C
    )
    # State B picks writing (default, rung c) — does NOT pick coding via rung a.
    assert _select_suggested_mode_for_state_b(aggregate) == writing
