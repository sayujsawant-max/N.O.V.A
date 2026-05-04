"""Tests for :meth:`RitualSystem.build_briefing` (Story 3.3 Group G, AC #22-#24).

Each test constructs a :class:`BriefingAggregate` fixture and a state +
tier, calls ``build_briefing``, and asserts the returned ViewModel
matches the AC's field-by-field expectations. **Skin rendering is NOT
tested here** — see ``test_skin_adapter.py`` for that. Group G locks
the Ritual side; Group H locks the Skin side.
"""

from __future__ import annotations

import pytest

from nova.core.types import BriefingState, CapabilityTier, SnapshotType
from nova.systems.brain.models import BriefingAggregate, ModeInfo, SessionSummary
from nova.systems.eyes.models import WindowContext, WorkspaceSnapshot
from nova.systems.ritual.system import (
    _STATE_A_INTRO_LINE_1,
    _STATE_A_INTRO_LINE_2,
    _STATE_B_INTRO_LINE,
    RitualSystem,
)

# --- Fixture builders --------------------------------------------------------


def _empty_aggregate(
    *,
    available_modes: tuple[ModeInfo, ...] = (),
) -> BriefingAggregate:
    """An aggregate with everything None / empty (matches FIRST_RUN inputs)."""
    return BriefingAggregate(
        last_session=None,
        last_snapshot=None,
        last_seed=None,
        available_modes=available_modes,
        recent_memory=(),
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


def _session(
    *,
    session_id: int = 1,
    started_at: str = "2026-04-01T10:00:00+00:00",
    ended_at: str | None = "2026-04-01T11:42:00+00:00",
    duration_seconds: int = 6120,
    mode_name: str | None = "coding",
    summary: str | None = None,
    is_complete: bool = True,
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        mode_name=mode_name,
        summary=summary,
        is_complete=is_complete,
    )


def _snapshot(*app_names: str | None) -> WorkspaceSnapshot:
    """Build a :class:`WorkspaceSnapshot` with a window per ``app_name`` arg.

    ``None`` entries simulate opaque/excluded windows (all identity
    fields ``None``, ``is_opaque=True``).
    """
    windows = tuple(
        WindowContext(
            app_name=name,
            window_title=None if name is None else f"{name} title",
            process_name=None if name is None else f"{name}.exe",
            is_opaque=name is None,
        )
        for name in app_names
    )
    return WorkspaceSnapshot(
        captured_at="2026-04-01T11:42:00+00:00",
        snapshot_type=SnapshotType.STARTUP,
        windows=windows,
    )


# --- State A tests (AC #22) --------------------------------------------------


@pytest.mark.asyncio
async def test_state_a_view_model_has_locked_field_values() -> None:
    """All 13 fields match the AC #9 declaration; intro_lines uses the locked constants."""
    aggregate = _empty_aggregate()
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.FULL,
    )
    assert vm.state is BriefingState.FIRST_RUN
    assert vm.tier is CapabilityTier.FULL
    assert vm.title == "N.O.V.A."
    assert vm.auto_start_setup is True
    assert vm.intro_lines == (_STATE_A_INTRO_LINE_1, _STATE_A_INTRO_LINE_2)
    assert vm.seed_quote is None
    assert vm.last_session_label is None
    assert vm.last_apps_label is None
    assert vm.available_modes_label is None
    assert vm.prose_enrichment is None
    assert vm.prompt_text is None
    assert vm.available_modes == ()
    assert vm.suggested_mode is None


@pytest.mark.asyncio
async def test_state_a_warns_and_overrides_non_empty_aggregate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """State A trusts Nerve's contract — but logs a WARNING when violated.

    Review finding D1 (resolved as option 3): if the aggregate ever
    arrives non-empty (Nerve contract violation), Ritual logs a warning
    and still returns the State A locked fields. The render does NOT
    crash, but the upstream bug surfaces in the log instead of being
    silently swallowed.

    Renamed from ``_ignores_aggregate_modes`` (review finding P19) —
    "ignore" suggests "doesn't read"; the implementation reads and
    overrides, which is "override" semantically.
    """
    aggregate = _empty_aggregate(
        available_modes=(_mode("coding", "Coding", is_default=True),),
    )
    with caplog.at_level("WARNING", logger="nova.systems.ritual"):
        vm = await RitualSystem().build_briefing(
            aggregate=aggregate,
            state=BriefingState.FIRST_RUN,
            tier=CapabilityTier.OFFLINE,
        )

    # Override behavior: aggregate's modes do NOT leak into the ViewModel.
    assert vm.available_modes == ()
    assert vm.suggested_mode is None

    # Warning surfaces the upstream contract violation. ``extra`` carries
    # closed-set category labels — never user data (project-context.md
    # opacity rule).
    warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "FIRST_RUN with non-empty aggregate" in r.message
    ]
    assert len(warnings) == 1, f"expected exactly one FIRST_RUN warning; got {warnings}"
    assert getattr(warnings[0], "available_modes_count", None) == 1
    assert getattr(warnings[0], "has_last_session", None) is False


@pytest.mark.asyncio
async def test_state_a_does_not_warn_for_clean_empty_aggregate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The contract-violation warning only fires when the aggregate is non-empty.

    A normal FIRST_RUN call (Nerve contract honored) must produce zero
    log noise — the warning is a regression signal, not a routine event.
    """
    aggregate = _empty_aggregate()
    with caplog.at_level("WARNING", logger="nova.systems.ritual"):
        await RitualSystem().build_briefing(
            aggregate=aggregate,
            state=BriefingState.FIRST_RUN,
            tier=CapabilityTier.FULL,
        )
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "FIRST_RUN" in r.message]
    assert warnings == []


# --- State B tests (AC #23) --------------------------------------------------


@pytest.mark.asyncio
async def test_state_b_view_model_with_one_mode() -> None:
    """Singular preface + singular available-modes label + Start prompt."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = _empty_aggregate(available_modes=(coding,))
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.FULL,
    )
    assert vm.intro_lines == (_STATE_B_INTRO_LINE,)
    assert vm.available_modes_label == "Available mode: Coding"
    assert vm.prompt_text == "Start in Coding mode?"
    assert vm.suggested_mode == coding
    assert vm.seed_quote is None
    assert vm.last_session_label is None
    assert vm.last_apps_label is None
    assert vm.auto_start_setup is False


@pytest.mark.asyncio
async def test_state_b_view_model_with_multiple_modes_uses_default_first() -> None:
    """Default mode wins rung c; available-modes label uses plural form."""
    coding = _mode("coding", "Coding", is_default=False)
    writing = _mode("writing", "Writing", is_default=True)
    aggregate = _empty_aggregate(available_modes=(coding, writing))
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.FULL,
    )
    assert vm.suggested_mode == writing
    assert vm.available_modes_label == "Available modes: Coding, Writing"
    assert vm.prompt_text == "Start in Writing mode?"


@pytest.mark.asyncio
async def test_state_b_view_model_with_no_modes_falls_back_to_what_mode() -> None:
    """Empty modes + State B → fallback prompt, no available-modes label."""
    aggregate = _empty_aggregate()
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.FULL,
    )
    assert vm.available_modes_label is None
    assert vm.prompt_text == "What mode?"
    assert vm.suggested_mode is None


# --- State C tests (AC #24) --------------------------------------------------


@pytest.mark.asyncio
async def test_state_c_with_seed_and_session_renders_full() -> None:
    """The canonical "warm resume" card — every label populated."""
    coding = _mode("coding", "Coding", is_default=True, last_used_at="2026-04-01T10:00:00+00:00")
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=_snapshot("VS Code", "Terminal", "Chrome"),
        last_seed="Push the deploy through",
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.intro_lines == ()  # State C has no preface
    assert vm.seed_quote == '"Push the deploy through"'
    assert vm.last_session_label == "Last session: Coding mode, 1h 42m"
    assert vm.last_apps_label == "Apps: VS Code, Terminal, Chrome"
    assert vm.available_modes_label is None  # State C omits this line
    assert vm.prompt_text == "Resume Coding mode?"
    assert vm.prose_enrichment is None
    assert vm.suggested_mode == coding


@pytest.mark.asyncio
async def test_state_c_with_setup_row_only_omits_progressively() -> None:
    """The Story 2.4 setup-row case — null mode_name, null seed, empty snapshot.

    State determination already evaluated to WARM_RESUME (Story 3.2's
    decisive pivot). Progressive omission carries the entire render —
    only the prompt resolves (via rung c default match).
    """
    coding = _mode("coding", "Coding", is_default=True)
    setup_session = _session(
        mode_name=None,
        duration_seconds=5,
        ended_at="2026-04-01T10:00:05+00:00",
    )
    aggregate = BriefingAggregate(
        last_session=setup_session,
        last_snapshot=_snapshot(),  # empty windows tuple
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.intro_lines == ()
    assert vm.seed_quote is None
    assert vm.last_session_label is None  # mode_name is None → omit
    assert vm.last_apps_label is None  # empty windows tuple → omit
    assert vm.prompt_text == "Resume Coding mode?"
    assert vm.suggested_mode == coding


@pytest.mark.asyncio
async def test_state_c_with_interrupted_session_omits_duration() -> None:
    """is_complete=False → omit the duration tail; mode + seed still render."""
    coding = _mode("coding", "Coding", is_default=True)
    interrupted = _session(
        ended_at=None,
        duration_seconds=0,  # Story 3.1 convention
        is_complete=False,
        mode_name="coding",
    )
    aggregate = BriefingAggregate(
        last_session=interrupted,
        last_snapshot=None,
        last_seed="partial thought",
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label == "Last session: Coding mode"
    assert vm.seed_quote == '"partial thought"'
    assert vm.last_apps_label is None  # last_snapshot is None


@pytest.mark.asyncio
async def test_state_c_with_completed_session_zero_duration_renders_zero_seconds() -> None:
    """The policy-split lock — 0 seconds + is_complete=True renders as ``0s``.

    A short completed session (user typed ``shutdown`` immediately
    after boot) MUST NOT be silently relabeled as interrupted. The
    interrupted-session decision keys on ``is_complete``, not on
    ``duration_seconds == 0``.
    """
    coding = _mode("coding", "Coding", is_default=True)
    very_short = _session(
        duration_seconds=0,
        is_complete=True,
        mode_name="coding",
    )
    aggregate = BriefingAggregate(
        last_session=very_short,
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label == "Last session: Coding mode, 0s"


@pytest.mark.asyncio
async def test_state_c_with_deleted_mode_omits_last_session_label() -> None:
    """Stem in last_session.mode_name absent from available_modes → omit (no leak).

    Renders the suggestion via fall-through rungs (b/c/d) but does NOT
    fabricate a "archived" label — progressive omission, not raw-stem leak.
    """
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(mode_name="archived"),
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label is None
    assert vm.suggested_mode == coding  # falls through to rung c
    assert vm.prompt_text == "Resume Coding mode?"


@pytest.mark.asyncio
async def test_state_c_with_opaque_window_filtered_from_apps() -> None:
    """Opaque windows have ``app_name=None`` upstream — filter drops them silently."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=_snapshot("VS Code", None),  # one normal + one opaque
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_apps_label == "Apps: VS Code"


def test_build_last_session_label_returns_none_for_no_session() -> None:
    """Defensive path — helper handles ``last_session=None`` even though State C
    guarantees a non-None session in practice. Locked to keep the helper
    safe for future reuse from other states.
    """
    from nova.systems.ritual.system import _build_last_session_label

    assert _build_last_session_label(None, None) is None


@pytest.mark.asyncio
async def test_state_c_with_no_snapshot_omits_apps_label() -> None:
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_apps_label is None


@pytest.mark.asyncio
async def test_state_c_seed_with_embedded_quotes_escapes_them() -> None:
    """Review finding P3 — inner ``"`` chars are backslash-escaped.

    Without escaping, a seed like ``Push "the deploy" through`` would
    render as ``"Push "the deploy" through"`` — visible quote ambiguity
    on the hero line.
    """
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=None,
        last_seed='Push "the deploy" through',
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.seed_quote == r'"Push \"the deploy\" through"'


@pytest.mark.asyncio
async def test_state_c_seed_with_pure_whitespace_omits() -> None:
    """Review finding P11 — pure-whitespace seed is data-corruption defense-in-depth."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=None,
        last_seed="   ",
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.seed_quote is None


@pytest.mark.asyncio
async def test_state_c_seed_with_embedded_newlines_collapses_to_spaces() -> None:
    """Review finding P11 — multi-line seed collapsed to single line.

    The renderer's block-spacing model assumes each label is one visual
    block. Embedded newlines would split the hero line and confuse the
    layout. Collapse internal whitespace runs (including ``\\n``) to
    single spaces.
    """
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=None,
        last_seed="First insight\nSecond insight",
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.seed_quote == '"First insight Second insight"'


@pytest.mark.asyncio
async def test_state_c_with_negative_duration_clamps_to_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review finding P4 — negative duration_seconds clamps to 0 instead of raising.

    A clock-skew or corrupt-row regression that produces
    ``duration_seconds < 0`` must NOT crash the briefing render.
    Ritual clamps via ``max(0, duration_seconds)`` defensively.
    """
    coding = _mode("coding", "Coding", is_default=True)
    weird_session = _session(
        duration_seconds=-1,
        is_complete=True,
        mode_name="coding",
    )
    aggregate = BriefingAggregate(
        last_session=weird_session,
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    # Should not raise.
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label == "Last session: Coding mode, 0s"


@pytest.mark.asyncio
async def test_state_c_with_int_zero_is_complete_treated_as_falsy() -> None:
    """Review finding P5 — truthy ``not is_complete`` covers a degraded type-coercion.

    If a future adapter regression passes ``is_complete=0`` (raw int)
    instead of ``False`` (bool), the previous ``is False`` identity
    check would silently flip to "completed" and render a fake duration
    tail. The truthy form (``not last_session.is_complete``) catches both.
    """
    coding = _mode("coding", "Coding", is_default=True)
    # Construct a session with the dataclass (which does NOT coerce
    # types at construction); pass a literal 0 to simulate the type-drift
    # regression.
    interrupted = SessionSummary(
        session_id=1,
        started_at="2026-04-01T10:00:00+00:00",
        ended_at=None,
        duration_seconds=0,
        mode_name="coding",
        summary=None,
        is_complete=False,
    )
    aggregate = BriefingAggregate(
        last_session=interrupted,
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    # Bool False or int 0 — the truthy check treats both as interrupted.
    assert vm.last_session_label == "Last session: Coding mode"


@pytest.mark.asyncio
async def test_state_c_with_empty_display_name_omits_last_session() -> None:
    """Review finding P6 — empty/whitespace display_name → omit, never blank label."""
    blank_named = _mode("coding", "   ", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(mode_name="coding"),
        last_snapshot=None,
        last_seed=None,
        available_modes=(blank_named,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label is None


@pytest.mark.asyncio
async def test_state_b_with_empty_display_name_filters_from_label() -> None:
    """Review finding P6 — empty-display_name modes don't appear in available_modes_label."""
    coding = _mode("coding", "Coding", is_default=True)
    blank = _mode("blank", "", is_default=False)
    aggregate = _empty_aggregate(available_modes=(coding, blank))
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.FULL,
    )
    # Label shows only the visible mode — singular form.
    assert vm.available_modes_label == "Available mode: Coding"


@pytest.mark.asyncio
async def test_state_c_with_comma_in_display_name_escapes() -> None:
    """Review finding D3 — comma in mode display_name is backslash-escaped.

    Without escaping, ``"Coding, Deep"`` renders as
    ``"Last session: Coding, Deep mode, 1h 42m"`` (3 commas, ambiguous).
    The escape preserves the user's chosen name visually while
    disambiguating the join boundary.
    """
    coding_deep = _mode("coding", "Coding, Deep", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(mode_name="coding"),
        last_snapshot=None,
        last_seed=None,
        available_modes=(coding_deep,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_session_label == r"Last session: Coding\, Deep mode, 1h 42m"


@pytest.mark.asyncio
async def test_state_b_with_comma_in_display_name_escapes_in_modes_label() -> None:
    """Review finding D3 — comma escape applies to available_modes_label too."""
    coding_deep = _mode("coding", "Coding, Deep", is_default=True)
    writing = _mode("writing", "Writing", is_default=False)
    aggregate = _empty_aggregate(available_modes=(coding_deep, writing))
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.FULL,
    )
    assert vm.available_modes_label == r"Available modes: Coding\, Deep, Writing"
    assert vm.prompt_text == "Start in Coding, Deep mode?"  # prompt stays unescaped


@pytest.mark.asyncio
async def test_state_c_with_comma_in_app_name_escapes_in_apps_label() -> None:
    """Review finding D3 — comma escape applies to last_apps_label too."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=_snapshot("Acme, Inc. Browser", "VS Code"),
        last_seed=None,
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.last_apps_label == r"Apps: Acme\, Inc. Browser, VS Code"


@pytest.mark.asyncio
async def test_state_c_with_curly_braces_in_display_name_renders_safely() -> None:
    """Review finding P10 — ``{`` in display_name does NOT trigger format-spec parsing.

    ``_format_prompt`` uses :meth:`str.replace` (not :meth:`str.format`)
    so substituted values are treated as opaque text — no IndexError,
    no recursive format-spec interpretation.
    """
    odd_mode = _mode("coding", "Mode {0}", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(mode_name="coding"),
        last_snapshot=None,
        last_seed=None,
        available_modes=(odd_mode,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    # Literal display_name flows through; no IndexError, no special parse.
    assert vm.prompt_text == "Resume Mode {0} mode?"


def test_build_briefing_raises_for_unknown_state() -> None:
    """Review finding P1 — exhaustiveness guard.

    A future StrEnum member added without updating ``build_briefing``'s
    dispatch must raise loudly, not silently fall through to a hardcoded
    state literal. The implementation guards this with a final
    ``raise ValueError``.

    We monkeypatch a fake state value that bypasses the StrEnum's three
    members to prove the raise is reachable.
    """
    import asyncio

    aggregate = _empty_aggregate()

    # ``_StateLike`` looks like a BriefingState member but is not one of
    # FIRST_RUN / POST_SETUP / WARM_RESUME — so it falls past all three
    # `if state is ...` arms and into the exhaustiveness raise.
    fake_state = object()  # not a BriefingState; identity check fails for all 3 arms

    async def _run() -> None:
        await RitualSystem().build_briefing(
            aggregate=aggregate,
            state=fake_state,  # type: ignore[arg-type]
            tier=CapabilityTier.FULL,
        )

    with pytest.raises(ValueError, match="Unhandled BriefingState"):
        asyncio.run(_run())


@pytest.mark.asyncio
async def test_state_c_with_empty_seed_string_omits_seed_quote() -> None:
    """Data-corruption defense in depth — empty string is falsy, so it omits."""
    coding = _mode("coding", "Coding", is_default=True)
    aggregate = BriefingAggregate(
        last_session=_session(),
        last_snapshot=None,
        last_seed="",
        available_modes=(coding,),
        recent_memory=(),
    )
    vm = await RitualSystem().build_briefing(
        aggregate=aggregate,
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.FULL,
    )
    assert vm.seed_quote is None
