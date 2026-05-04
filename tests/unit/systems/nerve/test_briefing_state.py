"""State-determination tests for Story 3.2.

Pure-function tests on :func:`nova.systems.nerve.briefing.determine_briefing_state`.
No DB, no async fixtures — each case builds a :class:`BriefingAggregate`
inline and asserts the first-match-wins state machine returns the
expected :class:`BriefingState`.

The six parametrized cases mirror the epic 3.2 boundary contract
(AC #13–#18). Parametrize IDs name each boundary explicitly so a
failure's pytest output points at the exact rule that broke.
"""

from __future__ import annotations

import pytest

from nova.core.types import BriefingState, MemoryCategory
from nova.systems.brain.models import (
    BriefingAggregate,
    MemoryItem,
    ModeInfo,
    SessionSummary,
)
from nova.systems.nerve.briefing import determine_briefing_state


def _mode(stem: str = "coding", is_default: bool = True) -> ModeInfo:
    """Build a ModeInfo with test-friendly defaults."""
    return ModeInfo(
        stem=stem,
        display_name=stem.title(),
        app_count=1,
        is_default=is_default,
        last_used_at=None,
    )


def _session(*, is_complete: bool) -> SessionSummary:
    """Build a SessionSummary with test-friendly defaults.

    ``is_complete=True`` emits a session that looks like Story 2.4's
    setup row (mode_name=None, summary=None, ended_at present) —
    representative of the realistic DB state. ``is_complete=False``
    emits an interrupted-session shape (ended_at=None, duration 0).
    """
    return SessionSummary(
        session_id=1,
        started_at="2026-04-20T09:00:00+00:00",
        ended_at="2026-04-20T10:00:00+00:00" if is_complete else None,
        duration_seconds=3600 if is_complete else 0,
        mode_name=None,
        summary=None,
        is_complete=is_complete,
    )


def _aggregate(
    *,
    available_modes: tuple[ModeInfo, ...] = (),
    last_session: SessionSummary | None = None,
    last_seed: str | None = None,
    recent_memory: tuple[MemoryItem, ...] = (),
) -> BriefingAggregate:
    """Build a BriefingAggregate with last_snapshot=None by default."""
    return BriefingAggregate(
        last_session=last_session,
        last_snapshot=None,
        last_seed=last_seed,
        available_modes=available_modes,
        recent_memory=recent_memory,
    )


# --- The six boundary conditions from AC #13–#18 -----------------------------


@pytest.mark.parametrize(
    ("aggregate", "expected_state"),
    [
        pytest.param(
            _aggregate(),  # empty modes, no session, no seed
            BriefingState.FIRST_RUN,
            id="first_run_canonical",
        ),
        pytest.param(
            _aggregate(available_modes=(_mode(),)),  # modes exist, no session
            BriefingState.POST_SETUP,
            id="post_setup_empty_session",
        ),
        pytest.param(
            _aggregate(
                available_modes=(_mode(),),
                last_session=_session(is_complete=False),
                last_seed=None,
            ),
            BriefingState.POST_SETUP,
            id="post_setup_interrupted",
        ),
        pytest.param(
            _aggregate(
                available_modes=(_mode(),),
                # Setup-row-only: is_complete=True, seed_text is NULL so
                # get_last_seed returns None at the port boundary. This
                # is the pre-flag reconciliation case — the literal
                # state machine yields WARM_RESUME, not POST_SETUP.
                last_session=_session(is_complete=True),
                last_seed=None,
            ),
            BriefingState.WARM_RESUME,
            id="warm_resume_setup_row_only",
        ),
        pytest.param(
            _aggregate(
                available_modes=(_mode(),),
                last_session=_session(is_complete=True),
                last_seed="Push the deploy through",
            ),
            BriefingState.WARM_RESUME,
            id="warm_resume_seed_present",
        ),
        pytest.param(
            _aggregate(
                available_modes=(_mode(),),
                last_session=_session(is_complete=False),
                last_seed="partial thought",
            ),
            BriefingState.WARM_RESUME,
            id="warm_resume_interrupted_with_seed",
        ),
    ],
)
def test_determine_briefing_state_boundary_cases(
    aggregate: BriefingAggregate, expected_state: BriefingState
) -> None:
    """The six boundary conditions from AC #13–#18 each resolve to one state."""
    assert determine_briefing_state(aggregate) == expected_state


def test_determine_briefing_state_is_pure() -> None:
    """Function is pure: same input → same output, repeatable, no side effects."""
    aggregate = _aggregate(
        available_modes=(_mode("coding"), _mode("writing", is_default=False)),
        last_session=_session(is_complete=True),
        last_seed="resume this",
    )
    first = determine_briefing_state(aggregate)
    second = determine_briefing_state(aggregate)
    third = determine_briefing_state(aggregate)
    assert first is second is third is BriefingState.WARM_RESUME


def test_determine_briefing_state_first_match_wins() -> None:
    """When conditions overlap, the earlier rule in the machine wins.

    Empty-modes + None-session satisfies BOTH the FIRST_RUN guard AND
    the POST_SETUP guard (last_seed is None AND last_session is None).
    The ``if`` ladder must return FIRST_RUN first.
    """
    aggregate = _aggregate()  # no modes, no session, no seed
    assert determine_briefing_state(aggregate) == BriefingState.FIRST_RUN


def test_determine_briefing_state_recent_memory_is_irrelevant() -> None:
    """``recent_memory`` never affects state determination.

    Lock this invariant: populating ``recent_memory`` with non-empty
    content does not change the state decision for an otherwise
    warm-resume-shaped aggregate. If a future refactor accidentally
    adds a ``recent_memory`` branch to the state machine, this test
    fires.
    """
    memory = MemoryItem(
        id=1,
        category=MemoryCategory.SEED,
        content="x",
        created_at="2026-04-20T09:00:00+00:00",
    )
    aggregate = _aggregate(
        available_modes=(_mode(),),
        last_session=_session(is_complete=True),
        last_seed=None,
        recent_memory=(memory,),
    )
    # Same shape as warm_resume_setup_row_only with a populated
    # recent_memory — the answer must still be WARM_RESUME.
    assert determine_briefing_state(aggregate) == BriefingState.WARM_RESUME
