"""Unit tests for :class:`nova.systems.nerve.system.NerveSystem` (Story 3.5).

Eight blocks per AC #30:

1. Constructor + startup ordering — including the read-then-write lock
   that's the first-blocker fix (briefing aggregate Brain reads MUST
   precede ``create_session``).
2. Skip-briefing policy — pure-helper decision table.
3. Briefing render path — conditional render gated on the policy.
4. Dispatch table — every CommandVerb member has a case arm.
5. Idempotent shutdown — second SHUTDOWN is a clean no-op.
6. REPL loop — three exit paths (SHUTDOWN, shutdown_event, EOF/KbdInt).
7. Signal handler — write-then-emit + one-shot + shutdown_event.
8. Tier-gate helper — structural seam for Epic 7's prose enrichment.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nova.core.config import (
    AppConfig,
    ExclusionConfig,
    ModeConfig,
    NovaConfig,
    UserSettings,
)
from nova.core.events import EventBus, SessionEnded, SessionStarted
from nova.core.exceptions import StorageError
from nova.core.tiers import TierManager
from nova.core.types import ActionType, BriefingState, CapabilityTier
from nova.ports.hands import HandsPort
from nova.systems.brain.models import (
    BriefingAggregate,
    SessionSummary,
)
from nova.systems.hands.models import ActionResult
from nova.systems.nerve.models import CommandOutcome
from nova.systems.nerve.system import NerveSystem, _should_skip_briefing
from nova.systems.ritual.models import BriefingViewModel
from nova.systems.skin.models import Command, CommandVerb

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _config(
    modes: dict[str, ModeConfig] | None = None,
    *,
    settings: UserSettings | None = None,
) -> NovaConfig:
    """Build a NovaConfig — never opens a real DB."""
    return NovaConfig(
        db_path=Path("/tmp/never-opened.db"),
        data_dir=Path("/tmp/never-opened"),
        modes=modes if modes is not None else {},
        exclusions=ExclusionConfig(),
        settings=settings if settings is not None else UserSettings(),
        api_key=None,
    )


def _mode(stem_name: str = "coding") -> ModeConfig:
    return ModeConfig(
        name=stem_name.capitalize(),
        apps=(AppConfig(name="x", executable="x.exe"),),
        is_default=True,
    )


def _session_summary(
    *,
    session_id: int = 42,
    ended_at: str | None = "2026-04-01T10:00:05+00:00",
    is_complete: bool = True,
    mode_name: str | None = None,
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at="2026-04-01T10:00:00+00:00",
        ended_at=ended_at,
        duration_seconds=5 if ended_at is not None else 0,
        mode_name=mode_name,
        summary=None,
        is_complete=is_complete,
    )


def _empty_aggregate() -> BriefingAggregate:
    return BriefingAggregate(
        last_session=None,
        last_snapshot=None,
        last_seed=None,
        available_modes=(),
        recent_memory=(),
    )


def _aggregate_with_prior_session(prior: SessionSummary | None = None) -> BriefingAggregate:
    return BriefingAggregate(
        last_session=prior if prior is not None else _session_summary(),
        last_snapshot=None,
        last_seed=None,
        available_modes=(),
        recent_memory=(),
    )


def _state_a_view_model() -> BriefingViewModel:
    return BriefingViewModel(
        state=BriefingState.FIRST_RUN,
        tier=CapabilityTier.OFFLINE,
        title="N.O.V.A.",
        auto_start_setup=True,
        intro_lines=("intro1", "intro2"),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text=None,
        available_modes=(),
        suggested_mode=None,
    )


def _state_b_view_model() -> BriefingViewModel:
    return BriefingViewModel(
        state=BriefingState.POST_SETUP,
        tier=CapabilityTier.OFFLINE,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=("preface",),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label="Available mode: Coding",
        prose_enrichment=None,
        prompt_text="Start in Coding mode?",
        available_modes=(),
        suggested_mode=None,
    )


def _state_c_view_model() -> BriefingViewModel:
    return BriefingViewModel(
        state=BriefingState.WARM_RESUME,
        tier=CapabilityTier.OFFLINE,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=(),
        seed_quote='"yesterday seed"',
        last_session_label="Last session: Coding mode, 1h",
        last_apps_label=None,
        available_modes_label=None,
        prose_enrichment=None,
        prompt_text="Resume Coding mode?",
        available_modes=(),
        suggested_mode=None,
    )


def _make_brain_mock(
    *,
    session_id: int = 42,
    last_session: SessionSummary | None = None,
    last_seed: str | None = None,
) -> MagicMock:
    """Build a Brain port mock that satisfies load_briefing_aggregate + lifecycle calls."""
    brain = MagicMock(name="brain")
    brain.create_session = AsyncMock(return_value=session_id)
    brain.end_session = AsyncMock(return_value="2026-04-01T11:00:00+00:00")
    brain.get_last_session = AsyncMock(return_value=last_session)
    brain.get_last_seed = AsyncMock(return_value=last_seed)
    brain.get_last_snapshot_for_session = AsyncMock(return_value=None)
    brain.get_mode_last_used = AsyncMock(return_value=None)
    return brain


def _make_ritual_mock(view_model: BriefingViewModel | None = None) -> MagicMock:
    ritual = MagicMock(name="ritual")
    ritual.build_briefing = AsyncMock(
        return_value=view_model if view_model is not None else _state_c_view_model()
    )
    return ritual


def _make_skin_mock(
    *,
    inputs: list[str] | None = None,
    parse_side_effect: Any = None,
) -> MagicMock:
    skin = MagicMock(name="skin")
    skin.render_briefing_card = AsyncMock(return_value=None)
    skin.render_response = AsyncMock(return_value=None)
    inputs_iter = iter(inputs if inputs is not None else ["shutdown"])
    skin.collect_input = AsyncMock(side_effect=lambda prompt: next(inputs_iter))

    if parse_side_effect is not None:
        skin.parse_command = AsyncMock(side_effect=parse_side_effect)
    else:
        # Real parser delegation — the inputs are real strings, parsed
        # exactly as the production adapter would.
        from nova.systems.skin.commands import parse

        async def _parse(raw: str) -> Command:
            return parse(raw)

        skin.parse_command = AsyncMock(side_effect=_parse)
    return skin


def _make_event_bus_mock() -> MagicMock:
    bus = MagicMock(spec=EventBus)
    bus.emit = AsyncMock(return_value=None)
    return bus


def _make_hands_mock() -> MagicMock:
    """HandsPort-spec'd MagicMock — restore_mode returns an empty result list.

    Story 3.6 added the ``hands: HandsPort`` constructor parameter. Most
    tests do not exercise the mode-restore path, so the default mock
    simply returns an empty list (no apps launched, no apps failed) on
    every call. Tests that DO exercise mode restore override this via
    ``hands.restore_mode = AsyncMock(side_effect=...)``.
    """
    hands = MagicMock(spec=HandsPort)
    hands.restore_mode = AsyncMock(return_value=[])
    return hands


def _make_tier_manager_mock(tier: CapabilityTier = CapabilityTier.OFFLINE) -> MagicMock:
    """Build a TierManager-spec'd MagicMock with ``tier`` set to the given value.

    Sets ``tier`` as an instance attribute (NOT a class-level property)
    so test fixtures don't share mutated type state. The earlier
    ``type(tier_mgr).tier = property(...)`` pattern worked because
    ``MagicMock(spec=TierManager)`` generates a per-instance subclass,
    but if a future refactor drops the spec or shares subclasses, every
    nerve test would see the last-built tier value. Instance attribute
    is the simpler + safer pattern.
    """
    tier_mgr = MagicMock(spec=TierManager)
    tier_mgr.tier = tier
    return tier_mgr


def _build_nerve_system(
    *,
    brain: MagicMock | None = None,
    ritual: MagicMock | None = None,
    skin: MagicMock | None = None,
    event_bus: MagicMock | None = None,
    tier_manager: MagicMock | None = None,
    config: NovaConfig | None = None,
    hands: MagicMock | None = None,
    clock: Any = None,
) -> NerveSystem:
    fixed_clock = (
        clock if clock is not None else (lambda: datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC))
    )
    return NerveSystem(
        brain=brain if brain is not None else _make_brain_mock(),
        ritual=ritual if ritual is not None else _make_ritual_mock(),
        skin=skin if skin is not None else _make_skin_mock(),
        event_bus=event_bus if event_bus is not None else _make_event_bus_mock(),
        tier_manager=tier_manager if tier_manager is not None else _make_tier_manager_mock(),
        config=config if config is not None else _config(modes={"coding": _mode("coding")}),
        hands=hands if hands is not None else _make_hands_mock(),
        clock=fixed_clock,
    )


# ===========================================================================
# Block 1 — Constructor + startup ordering (AC #5, #6, #8)
# ===========================================================================


@pytest.mark.asyncio
async def test_constructor_does_not_acquire_resources() -> None:
    """Constructor stores refs only — never raises even if Brain is broken."""
    brain = _make_brain_mock()
    brain.create_session = AsyncMock(side_effect=StorageError("synthetic"))
    nerve = _build_nerve_system(brain=brain)
    # If the constructor touched Brain, the StorageError above would surface.
    assert nerve is not None


def test_constructor_does_not_create_shutdown_event() -> None:
    """``_shutdown_event`` is None until ``startup`` runs.

    Lazy-loop binding: ``asyncio.Event()`` constructed in __init__ would
    bind to whatever loop happened to be current at that time.
    """
    nerve = _build_nerve_system()
    assert nerve._shutdown_event is None


@pytest.mark.asyncio
async def test_startup_creates_shutdown_event_lazily() -> None:
    """``_shutdown_event`` is created INSIDE startup, not in ``__init__``."""
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(skin=skin)
    await nerve.startup()
    assert nerve._shutdown_event is not None
    assert isinstance(nerve._shutdown_event, asyncio.Event)


@pytest.mark.asyncio
async def test_startup_reads_prior_state_before_creating_session() -> None:
    """First-blocker lock — prior-state Brain reads MUST precede ``create_session``.

    A reverse ordering would cause the freshly-created open session row
    to shadow the prior-session reads, breaking State A/B/C determination.
    """
    brain = _make_brain_mock()
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(brain=brain, skin=skin)
    await nerve.startup()
    method_names = [call_obj[0] for call_obj in brain.method_calls]
    create_idx = method_names.index("create_session")
    for read_method in (
        "get_last_session",
        "get_last_seed",
        "get_mode_last_used",
    ):
        assert read_method in method_names, f"expected {read_method} call missing"
        read_idx = method_names.index(read_method)
        assert read_idx < create_idx, (
            f"{read_method} (idx {read_idx}) must come BEFORE create_session "
            f"(idx {create_idx}) — see Story 3.5 Dev Notes 'Why session "
            f"creation moved AFTER briefing assembly'"
        )


@pytest.mark.asyncio
async def test_startup_state_a_does_not_create_session() -> None:
    """Second-blocker lock — State A returns BEFORE session creation. No orphan row."""
    # FIRST_RUN: empty modes + no last_session.
    brain = _make_brain_mock(last_session=None)
    ritual = _make_ritual_mock(view_model=_state_a_view_model())
    skin = _make_skin_mock()
    nerve = _build_nerve_system(
        brain=brain,
        ritual=ritual,
        skin=skin,
        config=_config(modes={}),
    )
    await nerve.startup()
    assert brain.create_session.call_count == 0, "State A path must NOT create a session row"


@pytest.mark.asyncio
async def test_startup_state_a_renders_briefing_then_returns_without_repl(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """State A renders the first-run card and exits — no REPL, no commands useful.

    Per AC #6 step 6, also asserts the State-A INFO log fires with the
    documented message ("State A briefing rendered — setup wizard
    auto-start deferred to setup.bat first-run gate").
    """
    brain = _make_brain_mock(last_session=None)
    ritual = _make_ritual_mock(view_model=_state_a_view_model())
    skin = _make_skin_mock()
    nerve = _build_nerve_system(
        brain=brain,
        ritual=ritual,
        skin=skin,
        config=_config(modes={}),
    )
    with caplog.at_level(logging.INFO, logger="nova.systems.nerve"):
        await nerve.startup()
    assert ritual.build_briefing.call_count == 1
    assert skin.render_briefing_card.call_count == 1
    assert skin.collect_input.call_count == 0, "REPL must not be entered for State A"
    state_a_logs = [
        r
        for r in caplog.records
        if "State A briefing rendered" in r.message and "deferred to setup.bat" in r.message
    ]
    assert len(state_a_logs) == 1, (
        f"AC #6 step 6 requires the State-A INFO log to fire exactly once; "
        f"found {len(state_a_logs)} matching records"
    )


@pytest.mark.asyncio
async def test_startup_creates_session_after_briefing_render() -> None:
    """Order: build_briefing → render_briefing_card → create_session → emit SessionStarted."""
    # State C path (modes + completed prior session + seed → WARM_RESUME).
    brain = _make_brain_mock(
        last_session=_session_summary(is_complete=True),
        last_seed="seed",
    )
    ritual = _make_ritual_mock(view_model=_state_c_view_model())
    skin = _make_skin_mock(inputs=["shutdown"])
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(
        brain=brain,
        ritual=ritual,
        skin=skin,
        event_bus=event_bus,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    # Reconstruct cross-mock ordering by call count check at boundaries.
    assert ritual.build_briefing.call_count >= 1
    assert skin.render_briefing_card.call_count >= 1
    assert brain.create_session.call_count == 1
    # SessionStarted emitted after create_session
    started_emits = [
        emit_call
        for emit_call in event_bus.emit.call_args_list
        if isinstance(emit_call.args[0], SessionStarted)
    ]
    assert len(started_emits) == 1


@pytest.mark.asyncio
async def test_startup_persist_before_emit_session_started() -> None:
    """``brain.create_session`` returns BEFORE ``event_bus.emit(SessionStarted)``.

    Locks architecture.md:1037 write-then-emit invariant.
    """
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    event_bus = _make_event_bus_mock()
    skin = _make_skin_mock(inputs=["shutdown"])
    # Capture call order across two mocks via a side-effect counter.
    counter: list[str] = []

    original_create = brain.create_session

    async def tracked_create(**kwargs: Any) -> int:
        counter.append("create_session")
        result = await original_create(**kwargs)
        return int(result)

    brain.create_session = tracked_create

    original_emit = event_bus.emit

    async def tracked_emit(event: Any) -> None:
        if isinstance(event, SessionStarted):
            counter.append("emit_session_started")
        await original_emit(event)

    event_bus.emit = tracked_emit

    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        event_bus=event_bus,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    assert "create_session" in counter
    assert "emit_session_started" in counter
    assert counter.index("create_session") < counter.index("emit_session_started")


@pytest.mark.asyncio
async def test_startup_passes_mode_name_none_to_create_session() -> None:
    """``mode_name=None, started_at=None`` — adapter stamps timestamp."""
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    brain.create_session.assert_awaited_once_with(mode_name=None, started_at=None)


@pytest.mark.asyncio
async def test_startup_finally_runs_uninstall_signal_handler_on_state_a_path() -> None:
    """State A early-return still uninstalls the signal handler (try/finally)."""
    brain = _make_brain_mock(last_session=None)
    ritual = _make_ritual_mock(view_model=_state_a_view_model())
    nerve = _build_nerve_system(brain=brain, ritual=ritual, config=_config(modes={}))
    await nerve.startup()
    # Signal handler was installed (step 2) but uninstalled (finally) — flag flipped
    assert nerve._signal_handlers_installed is False


def test_startup_raises_keyboard_interrupt_when_signal_handler_ran() -> None:
    """Signal-driven exit MUST surface as ``KeyboardInterrupt`` so cli.py maps to 130.

    Without this, the custom signal handler suppresses normal KbdInt
    propagation and ``startup()`` returns normally → ``_async_main``
    returns ``EXIT_OK`` → user's Ctrl-C silently exits with code 0
    while the session is marked interrupted in nova.db.

    Sync test using ``asyncio.run`` + sync ``pytest.raises`` because a
    ``BaseException`` raised inside a coroutine driven by
    ``@pytest.mark.asyncio`` aborts the entire pytest session (the
    runner can't catch BaseException). ``asyncio.run`` propagates the
    ``KeyboardInterrupt`` synchronously, where sync ``pytest.raises``
    catches it cleanly.

    Simulates the post-REPL state: ``_signal_handler_task`` is set and
    completed (signal handler ran during what would have been the REPL).
    Patches ``_run_repl`` to inject this state without actually firing
    a real signal — keeps the test deterministic.
    """

    async def _run() -> None:
        skin = _make_skin_mock(inputs=["shutdown"])
        brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
        nerve = _build_nerve_system(
            brain=brain,
            skin=skin,
            config=_config(
                modes={"coding": _mode("coding")},
                settings=UserSettings(skip_briefing_if_recent=False),
            ),
        )

        # Patch _run_repl: simulate "signal handler ran and set the
        # task field" without firing an actual signal.
        async def patched_repl() -> None:
            nerve._signal_handler_task = asyncio.create_task(asyncio.sleep(0))
            await nerve._signal_handler_task

        nerve._run_repl = patched_repl  # type: ignore[method-assign]
        await nerve.startup()

    with pytest.raises(KeyboardInterrupt, match="session interrupted by signal"):
        asyncio.run(_run())


@pytest.mark.asyncio
async def test_startup_does_not_raise_when_shutdown_command_routed() -> None:
    """SHUTDOWN command exit path → startup returns normally → cli.py returns EXIT_OK.

    Asymmetry lock with the previous test: only signal-driven exit
    raises KeyboardInterrupt; user-typed shutdown is a clean exit.
    """
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    # No exception
    await nerve.startup()
    assert nerve._signal_handler_task is None, (
        "SHUTDOWN command path must NOT touch _signal_handler_task — "
        "leaves it None so startup does not raise"
    )


@pytest.mark.asyncio
async def test_startup_does_not_raise_when_eof_terminates_input() -> None:
    """EOFError at input → idempotent _handle_shutdown → no signal-handler raise.

    EOF is a clean stdin-closed event, not an interrupt. cli.py should
    return EXIT_OK, not EXIT_INTERRUPTED.
    """

    async def raise_eof(prompt: str) -> str:  # noqa: ARG001
        raise EOFError

    skin = _make_skin_mock()
    skin.collect_input = AsyncMock(side_effect=raise_eof)
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()  # no exception
    assert nerve._signal_handler_task is None


@pytest.mark.asyncio
async def test_startup_finally_writes_interrupted_marker_when_repl_raises() -> None:
    """Defense-in-depth — REPL exits via uncaught exception → finally writes is_complete=False."""
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    skin = _make_skin_mock(inputs=["shutdown"])
    # Force route_command to raise an unexpected error AFTER session create.
    skin.parse_command = AsyncMock(side_effect=RuntimeError("synthetic"))
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        event_bus=event_bus,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        await nerve.startup()
    # Defense-in-depth end_session called with is_complete=False
    end_calls = brain.end_session.await_args_list
    assert len(end_calls) == 1
    assert end_calls[0].kwargs["is_complete"] is False
    # SessionEnded emitted after end_session succeeded
    ended_emits = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended_emits) == 1
    assert ended_emits[0].args[0].is_complete is False


# ===========================================================================
# Block 2 — Skip-briefing pure helper (AC #7)
# ===========================================================================


_FIXED_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


@pytest.mark.parametrize("flag", [True, False])
def test_skip_briefing_returns_false_when_setting_disabled(flag: bool) -> None:
    """Setting disabled → False (always render); enabled with no prior → False."""
    settings = UserSettings(skip_briefing_if_recent=flag)
    result = _should_skip_briefing(prior_session=None, settings=settings, clock=_fixed_clock)
    assert result is False


def test_skip_briefing_returns_false_when_prior_session_is_none() -> None:
    settings = UserSettings(skip_briefing_if_recent=True)
    assert _should_skip_briefing(None, settings, _fixed_clock) is False


def test_skip_briefing_returns_false_when_ended_at_is_none() -> None:
    """Interrupted prior session — no defined end → never skip."""
    settings = UserSettings(skip_briefing_if_recent=True)
    prior = _session_summary(ended_at=None, is_complete=False)
    assert _should_skip_briefing(prior, settings, _fixed_clock) is False


def test_skip_briefing_returns_false_when_threshold_is_zero() -> None:
    """Threshold 0 → recency disabled, always render."""
    settings = UserSettings(skip_briefing_if_recent=True, briefing_recency_threshold_minutes=0)
    prior = _session_summary(ended_at="2026-04-01T11:59:00+00:00")  # 1 minute ago
    assert _should_skip_briefing(prior, settings, _fixed_clock) is False


def test_skip_briefing_returns_true_when_within_threshold() -> None:
    settings = UserSettings(skip_briefing_if_recent=True, briefing_recency_threshold_minutes=60)
    prior = _session_summary(ended_at="2026-04-01T11:30:00+00:00")  # 30 min ago
    assert _should_skip_briefing(prior, settings, _fixed_clock) is True


def test_skip_briefing_returns_false_when_outside_threshold() -> None:
    settings = UserSettings(skip_briefing_if_recent=True, briefing_recency_threshold_minutes=60)
    prior = _session_summary(ended_at="2026-04-01T10:30:00+00:00")  # 90 min ago
    assert _should_skip_briefing(prior, settings, _fixed_clock) is False


def test_skip_briefing_returns_false_on_malformed_ended_at() -> None:
    """Malformed ISO string → fall back to rendering (fail-open)."""
    settings = UserSettings(skip_briefing_if_recent=True)
    prior = _session_summary(ended_at="not-an-iso-string")
    assert _should_skip_briefing(prior, settings, _fixed_clock) is False


def test_setup_row_recency_skips_briefing_first_bare_nova_boot() -> None:
    """Story 2.4 reconciliation — setup row ended 20 min ago → skip."""
    settings = UserSettings(skip_briefing_if_recent=True, briefing_recency_threshold_minutes=60)
    twenty_min_ago = (_FIXED_NOW - timedelta(minutes=20)).isoformat()
    setup_row = _session_summary(ended_at=twenty_min_ago, is_complete=True, mode_name=None)
    assert _should_skip_briefing(setup_row, settings, _fixed_clock) is True


# ===========================================================================
# Block 3 — Briefing render path (AC #6 steps 7-8)
# ===========================================================================


@pytest.mark.asyncio
async def test_briefing_skipped_does_not_call_ritual_build_or_skin_render() -> None:
    """When policy returns True (recent prior session), skip render entirely."""
    twenty_min_ago = (_FIXED_NOW - timedelta(minutes=20)).isoformat()
    brain = _make_brain_mock(
        last_session=_session_summary(ended_at=twenty_min_ago, is_complete=True),
        last_seed="x",
    )
    ritual = _make_ritual_mock(view_model=_state_c_view_model())
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=brain,
        ritual=ritual,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(
                skip_briefing_if_recent=True, briefing_recency_threshold_minutes=60
            ),
        ),
        clock=_fixed_clock,
    )
    await nerve.startup()
    assert ritual.build_briefing.call_count == 0
    assert skin.render_briefing_card.call_count == 0


@pytest.mark.asyncio
async def test_briefing_rendered_when_policy_returns_false() -> None:
    """Policy disabled → both build_briefing and render_briefing_card called once."""
    brain = _make_brain_mock(
        last_session=_session_summary(is_complete=True),
        last_seed="x",
    )
    ritual = _make_ritual_mock(view_model=_state_c_view_model())
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=brain,
        ritual=ritual,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    assert ritual.build_briefing.call_count == 1
    assert skin.render_briefing_card.call_count == 1


@pytest.mark.asyncio
async def test_briefing_skipped_log_includes_prior_session_ended_at(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When skipped, the INFO log carries the prior session's ended_at."""
    twenty_min_ago = (_FIXED_NOW - timedelta(minutes=20)).isoformat()
    brain = _make_brain_mock(
        last_session=_session_summary(ended_at=twenty_min_ago, is_complete=True),
        last_seed="x",
    )
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(
                skip_briefing_if_recent=True, briefing_recency_threshold_minutes=60
            ),
        ),
        clock=_fixed_clock,
    )
    with caplog.at_level(logging.INFO, logger="nova.systems.nerve"):
        await nerve.startup()
    skip_logs = [r for r in caplog.records if "briefing skipped" in r.message]
    assert len(skip_logs) == 1
    assert getattr(skip_logs[0], "prior_session_ended_at", None) == twenty_min_ago


@pytest.mark.asyncio
async def test_skip_policy_reads_aggregate_last_session_not_separate_get_call() -> None:
    """Helper takes ``aggregate.last_session`` — no second ``get_last_session`` call."""
    brain = _make_brain_mock(
        last_session=_session_summary(is_complete=True),
        last_seed="x",
    )
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    # get_last_session called exactly once (inside load_briefing_aggregate)
    assert brain.get_last_session.await_count == 1


# ===========================================================================
# Block 4 — Dispatch table (AC #9, #10, #11, #12)
# ===========================================================================


def _build_session_active_nerve() -> tuple[NerveSystem, MagicMock, MagicMock, MagicMock]:
    """Build a nerve with _session_active=True so handlers can run standalone.

    Returns (nerve, brain_mock, skin_mock, event_bus_mock).
    """
    brain = _make_brain_mock()
    skin = _make_skin_mock()
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, skin=skin, event_bus=event_bus)
    nerve._session_id = 42
    nerve._session_active = True
    return nerve, brain, skin, event_bus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verb", "target"),
    [
        (CommandVerb.MODE, None),
        # MODE/"coding" is intentionally NOT in this list: Story 3.6
        # replaced its placeholder body with HandsPort delegation, so
        # the "render_response called once with placeholder" shape no
        # longer applies. Coverage moves to Block I's dedicated
        # ``test_mode_switch_*`` tests.
        (CommandVerb.MODE_CREATE, None),
        (CommandVerb.MODE_EDIT, None),
        (CommandVerb.MODE_EDIT, "coding"),
        (CommandVerb.STATUS, None),
        (CommandVerb.MEMORY, None),
        (CommandVerb.FORGET, None),
        (CommandVerb.FORGET, "Meridian"),
        (CommandVerb.HELP, None),
    ],
)
async def test_route_command_layer_b_routable_returns_continue(
    verb: CommandVerb, target: str | None
) -> None:
    """Every Layer B routable verb (except SHUTDOWN, MODE/<target>) returns CONTINUE."""
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(verb=verb, target=target, raw_input="x", is_contextual=False)
    outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.CONTINUE
    assert skin.render_response.call_count == 1


@pytest.mark.asyncio
async def test_route_command_modes_list_with_no_modes() -> None:
    """``mode`` (target=None) with empty config renders the empty-modes guidance."""
    brain = _make_brain_mock()
    skin = _make_skin_mock()
    nerve = _build_nerve_system(brain=brain, skin=skin, config=_config(modes={}))
    nerve._session_id = 42
    nerve._session_active = True
    cmd = Command(verb=CommandVerb.MODE, target=None, raw_input="mode", is_contextual=False)
    await nerve.route_command(cmd)
    response = skin.render_response.call_args.args[0]
    assert "No modes configured" in response


@pytest.mark.asyncio
async def test_route_command_modes_list_renders_sorted_stems() -> None:
    """``mode`` (target=None) with modes renders ``Modes: a, b, c`` sorted."""
    brain = _make_brain_mock()
    skin = _make_skin_mock()
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        config=_config(modes={"writing": _mode("writing"), "coding": _mode("coding")}),
    )
    nerve._session_id = 42
    nerve._session_active = True
    cmd = Command(verb=CommandVerb.MODE, target=None, raw_input="mode", is_contextual=False)
    await nerve.route_command(cmd)
    response = skin.render_response.call_args.args[0]
    assert response == "Modes: coding, writing"


@pytest.mark.asyncio
async def test_route_command_mode_edit_partial_renders_guidance() -> None:
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(
        verb=CommandVerb.MODE_EDIT, target=None, raw_input="mode edit", is_contextual=False
    )
    await nerve.route_command(cmd)
    assert "Try mode edit coding" in skin.render_response.call_args.args[0]


@pytest.mark.asyncio
async def test_route_command_forget_partial_renders_example() -> None:
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.FORGET, target=None, raw_input="forget", is_contextual=False)
    await nerve.route_command(cmd)
    assert "Tell me what to forget" in skin.render_response.call_args.args[0]
    assert "forget Meridian" in skin.render_response.call_args.args[0]


@pytest.mark.asyncio
async def test_route_command_memory_renders_transparency_placeholder() -> None:
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.MEMORY, target=None, raw_input="memory", is_contextual=False)
    await nerve.route_command(cmd)
    assert "Transparency coming soon" in skin.render_response.call_args.args[0]


@pytest.mark.asyncio
async def test_route_command_shutdown_calls_brain_emits_renders_then_returns_exit() -> None:
    """SHUTDOWN ordering: brain.end_session → emit SessionEnded → render → EXIT."""
    nerve, brain, skin, event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.SHUTDOWN, target=None, raw_input="shutdown", is_contextual=False)
    outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.EXIT
    brain.end_session.assert_awaited_once_with(42, seed_text=None, summary=None, is_complete=True)
    # Emit happened
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 1
    assert ended[0].args[0].is_complete is True
    # Render happened
    assert skin.render_response.call_count == 1
    assert skin.render_response.call_args.args[0] == "Session ended."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verb",
    [
        CommandVerb.RESUME,
        CommandVerb.YES,
        CommandVerb.NO,
        CommandVerb.SKIP,
        CommandVerb.CANCEL,
        CommandVerb.CONFIRM,
    ],
)
async def test_route_command_layer_c_without_prompt_context_returns_continue(
    verb: CommandVerb,
) -> None:
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    assert nerve._prompt_context is None
    cmd = Command(verb=verb, target=None, raw_input=str(verb), is_contextual=True)
    outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.CONTINUE
    response = skin.render_response.call_args.args[0]
    if verb is CommandVerb.RESUME:
        assert "Nothing to resume right now" in response
    else:
        assert "Nothing to confirm right now" in response


@pytest.mark.asyncio
async def test_route_command_unknown_echoes_target() -> None:
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.UNKNOWN, target="audit", raw_input="audit", is_contextual=False)
    outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.CONTINUE
    response = skin.render_response.call_args.args[0]
    assert "audit" in response
    assert "Didn't catch that" in response


@pytest.mark.asyncio
async def test_route_command_empty_does_not_render_response() -> None:
    """EMPTY is silent no-op — no log, no render."""
    nerve, _brain, skin, _event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.EMPTY, target=None, raw_input="", is_contextual=False)
    outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.CONTINUE
    assert skin.render_response.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", list(CommandVerb))
async def test_route_command_dispatch_table_covers_every_command_verb(
    verb: CommandVerb,
) -> None:
    """Exhaustiveness lock — every CommandVerb member has a case arm."""
    nerve, _brain, _skin, _event_bus = _build_session_active_nerve()
    # SHUTDOWN advances session lifecycle; reset for each test.
    target = "x" if verb in (CommandVerb.MODE, CommandVerb.MODE_EDIT, CommandVerb.FORGET) else None
    is_contextual = verb in (
        CommandVerb.RESUME,
        CommandVerb.YES,
        CommandVerb.NO,
        CommandVerb.SKIP,
        CommandVerb.CANCEL,
        CommandVerb.CONFIRM,
    )
    cmd = Command(verb=verb, target=target, raw_input="x", is_contextual=is_contextual)
    # No raise — dispatch handles every member.
    outcome = await nerve.route_command(cmd)
    assert outcome in (CommandOutcome.CONTINUE, CommandOutcome.EXIT)


# ===========================================================================
# Block 5 — Idempotent shutdown (AC #15)
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_shutdown_is_idempotent_second_call_returns_exit_no_re_end() -> None:
    """Second SHUTDOWN call is a clean no-op — _session_active guard."""
    nerve, brain, skin, event_bus = _build_session_active_nerve()
    cmd = Command(verb=CommandVerb.SHUTDOWN, target=None, raw_input="shutdown", is_contextual=False)
    first = await nerve.route_command(cmd)
    second = await nerve.route_command(cmd)
    assert first is CommandOutcome.EXIT
    assert second is CommandOutcome.EXIT
    assert brain.end_session.await_count == 1
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 1
    # render_response was called once (first SHUTDOWN); second was no-op
    assert skin.render_response.call_count == 1


# ===========================================================================
# Block 6 — REPL loop (AC #14) — three exit paths
# ===========================================================================


@pytest.mark.asyncio
async def test_repl_exits_on_shutdown_command() -> None:
    """Path (a) — user types ``shutdown``; CommandOutcome.EXIT terminates."""
    skin = _make_skin_mock(inputs=["shutdown"])
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    assert skin.collect_input.call_count == 1


@pytest.mark.asyncio
async def test_repl_continues_after_unknown_then_exits_on_shutdown() -> None:
    """UNKNOWN routes, response renders, REPL continues, SHUTDOWN exits."""
    skin = _make_skin_mock(inputs=["hello", "shutdown"])
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    assert skin.collect_input.call_count == 2


@pytest.mark.asyncio
async def test_repl_exits_when_shutdown_event_set_before_first_iteration() -> None:
    """Path (b) — _shutdown_event set BEFORE _run_repl → loop guard fires immediately."""
    skin = _make_skin_mock(inputs=[])  # would StopIteration if entered
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )

    # Hijack startup so we set the event after step 9 (session created) but
    # before _run_repl entry. We do this by patching _run_repl.
    real_run_repl = nerve._run_repl

    async def patched_run_repl() -> None:
        assert nerve._shutdown_event is not None
        nerve._shutdown_event.set()
        await real_run_repl()

    nerve._run_repl = patched_run_repl  # type: ignore[method-assign]
    await nerve.startup()
    # collect_input never called — guard fired before first iteration.
    assert skin.collect_input.call_count == 0


@pytest.mark.asyncio
async def test_repl_exits_when_shutdown_event_set_during_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Path (b) — long-blocking input + external _shutdown_event.set() → loser cancelled."""
    block_event = asyncio.Event()

    async def blocking_input(prompt: str) -> str:  # noqa: ARG001
        await block_event.wait()
        return "shutdown"

    skin = _make_skin_mock(inputs=["x"])
    skin.collect_input = AsyncMock(side_effect=blocking_input)
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )

    async def trigger_shutdown_after_delay() -> None:
        # Yield several times so collect_input has a chance to start.
        for _ in range(5):
            await asyncio.sleep(0)
        assert nerve._shutdown_event is not None
        nerve._shutdown_event.set()
        # Release the blocking task so the cancelled-task drain can complete.
        block_event.set()

    with caplog.at_level(logging.INFO, logger="nova.systems.nerve"):
        await asyncio.gather(nerve.startup(), trigger_shutdown_after_delay())
    assert any("shutdown event" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_repl_eof_error_triggers_clean_shutdown() -> None:
    """Path (c) — EOFError at input → idempotent _handle_shutdown."""

    async def raise_eof(prompt: str) -> str:  # noqa: ARG001
        raise EOFError

    skin = _make_skin_mock()
    skin.collect_input = AsyncMock(side_effect=raise_eof)
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    await nerve.startup()
    # end_session called once (via _handle_shutdown EOF branch)
    assert brain.end_session.await_count == 1


@pytest.mark.asyncio
async def test_repl_drains_cancelled_pending_task_without_warning(
    recwarn: pytest.WarningsRecorder,
) -> None:
    """AC #30 Block 6 — `_shutdown_event.set()` while ``input_task`` in flight; no RuntimeWarning.

    The REPL's race pattern cancels the loser task and drains it to
    prevent ``RuntimeWarning: Task was destroyed but it is pending!``
    at process exit (project-context.md:105 — "no silent warnings in
    passing tests"). Locks both the inner pending-drain block (the
    FIRST_COMPLETED winner-cancels-loser path) AND the outer
    ``except CancelledError`` drain (Patch 2 from the 2026-05-05
    review).
    """
    block_event = asyncio.Event()

    async def blocking_input(prompt: str) -> str:  # noqa: ARG001
        await block_event.wait()
        return "shutdown"

    skin = _make_skin_mock(inputs=["x"])
    skin.collect_input = AsyncMock(side_effect=blocking_input)
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )

    async def trigger_shutdown_after_delay() -> None:
        for _ in range(5):
            await asyncio.sleep(0)
        assert nerve._shutdown_event is not None
        nerve._shutdown_event.set()
        # Release the blocking task so the cancelled-task drain completes.
        block_event.set()

    await asyncio.gather(nerve.startup(), trigger_shutdown_after_delay())

    # Filter to the specific warning class the drain prevents. Ignore
    # unrelated warnings (asyncio's debug noise, deprecation notices in
    # libs we don't own).
    pending_warnings = [
        w
        for w in recwarn.list
        if issubclass(w.category, RuntimeWarning) and "pending" in str(w.message).lower()
    ]
    assert pending_warnings == [], (
        f"REPL drain must prevent cancelled-task RuntimeWarnings; "
        f"got: {[(w.category.__name__, str(w.message)) for w in pending_warnings]}"
    )


def test_repl_eof_and_keyboard_interrupt_share_same_except_branch() -> None:
    """Path (c) — :class:`KeyboardInterrupt` shares the EOFError branch.

    A pytest-asyncio test that raised ``KeyboardInterrupt`` from inside a
    coroutine task would propagate the BaseException past asyncio's task
    wrapping and abort the entire test session. The behavioral guarantee
    we need is: ``except (EOFError, KeyboardInterrupt)`` catches both
    via the same handler. This static check on the source AST asserts
    the except clause names BOTH classes — equivalent coverage without
    the BaseException-in-asyncio brittleness.
    """
    import ast
    import inspect

    from nova.systems.nerve import system as nerve_system_module

    source = inspect.getsource(nerve_system_module)
    tree = ast.parse(source)
    repl_func: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_repl":
            repl_func = node
            break
    assert repl_func is not None, "_run_repl not found in nerve system module"
    found_handlers: list[set[str]] = []
    for node in ast.walk(repl_func):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
            names: set[str] = set()
            for elt in node.type.elts:
                if isinstance(elt, ast.Name):
                    names.add(elt.id)
            if names:
                found_handlers.append(names)
    assert any({"EOFError", "KeyboardInterrupt"}.issubset(handler) for handler in found_handlers), (
        f"_run_repl must handle EOFError and KeyboardInterrupt in the same "
        f"except clause; found handlers: {found_handlers}"
    )


# ===========================================================================
# Block 7 — Signal handler (AC #18, #19) — write-then-emit + shutdown_event
# ===========================================================================


@pytest.mark.asyncio
async def test_signal_handler_sets_shutdown_event_first() -> None:
    """Even on no-Brain-write path (no active session), event is set."""
    nerve = _build_nerve_system()
    # Simulate startup state without entering the REPL: shutdown_event
    # exists, session_active=False (simulating State A or pre-create).
    nerve._shutdown_event = asyncio.Event()
    nerve._session_active = False
    await nerve._signal_handler_callback()
    assert nerve._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_signal_handler_calls_brain_end_session_with_is_complete_false() -> None:
    brain = _make_brain_mock()
    nerve = _build_nerve_system(brain=brain)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    await nerve._signal_handler_callback()
    brain.end_session.assert_awaited_once_with(42, seed_text=None, summary=None, is_complete=False)


@pytest.mark.asyncio
async def test_signal_handler_emits_session_ended_after_brain_write() -> None:
    """Order: brain.end_session resolves → event_bus.emit(SessionEnded)."""
    counter: list[str] = []
    brain = _make_brain_mock()

    async def tracked_end(*args: Any, **kwargs: Any) -> str:  # noqa: ARG001
        counter.append("end_session")
        return "2026-04-01T12:00:00+00:00"

    brain.end_session = tracked_end
    event_bus = _make_event_bus_mock()

    async def tracked_emit(event: Any) -> None:
        if isinstance(event, SessionEnded):
            counter.append("emit_session_ended")

    event_bus.emit = tracked_emit
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    await nerve._signal_handler_callback()
    assert counter == ["end_session", "emit_session_ended"]


@pytest.mark.asyncio
async def test_signal_handler_does_not_emit_session_ended_when_brain_write_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Third-blocker lock — failed Brain write means NO SessionEnded emission."""
    brain = _make_brain_mock()
    brain.end_session = AsyncMock(side_effect=StorageError("synthetic"))
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    with caplog.at_level(logging.ERROR, logger="nova.systems.nerve"):
        await nerve._signal_handler_callback()
    # NO SessionEnded emission
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 0
    # _session_active remains True (flag flip happens after successful write only)
    assert nerve._session_active is True
    assert any("brain.end_session failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_signal_handler_does_not_emit_session_ended_when_brain_write_times_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Third-blocker lock — timed-out Brain write means NO emission."""
    brain = _make_brain_mock()

    async def slow_end(*args: Any, **kwargs: Any) -> str:  # noqa: ARG001
        await asyncio.sleep(10)
        return "x"

    brain.end_session = slow_end
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    with caplog.at_level(logging.WARNING, logger="nova.systems.nerve"):
        await asyncio.wait_for(
            nerve._signal_handler_callback(),
            timeout=3.0,
        )
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 0
    assert nerve._session_active is True
    assert any("timed out" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_signal_handler_swallows_emission_failure_after_successful_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Brain write succeeds; emission fails; handler returns cleanly."""
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    event_bus.emit = AsyncMock(side_effect=RuntimeError("broken bus"))
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    with caplog.at_level(logging.ERROR, logger="nova.systems.nerve"):
        await nerve._signal_handler_callback()
    # _session_active flipped after the successful write
    assert nerve._session_active is False
    assert any("emission failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_signal_handler_one_shot_via_session_active_guard() -> None:
    """Second handler invocation is a no-op — _session_active guard."""
    brain = _make_brain_mock()
    nerve = _build_nerve_system(brain=brain)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    await nerve._signal_handler_callback()
    await nerve._signal_handler_callback()
    assert brain.end_session.await_count == 1


@pytest.mark.asyncio
async def test_signal_handler_no_op_before_session_creation() -> None:
    """_session_active=False, _session_id=None → handler sets event + returns cleanly."""
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_active = False
    nerve._session_id = None
    await nerve._signal_handler_callback()
    # Event set, no Brain call, no emit
    assert nerve._shutdown_event.is_set()
    assert brain.end_session.await_count == 0
    assert event_bus.emit.call_count == 0


# ===========================================================================
# Block 8 — Tier-gate helper (AC #13)
# ===========================================================================


@pytest.mark.asyncio
async def test_tier_check_returns_true_in_full() -> None:
    nerve = _build_nerve_system(tier_manager=_make_tier_manager_mock(CapabilityTier.FULL))
    result = nerve._tier_check_or_offline_response(op_name="prose_enrichment")
    assert result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [CapabilityTier.DEGRADED, CapabilityTier.OFFLINE])
async def test_tier_check_returns_false_in_degraded_offline(
    tier: CapabilityTier, caplog: pytest.LogCaptureFixture
) -> None:
    nerve = _build_nerve_system(tier_manager=_make_tier_manager_mock(tier))
    with caplog.at_level(logging.INFO, logger="nova.systems.nerve"):
        result = nerve._tier_check_or_offline_response(op_name="prose_enrichment")
    assert result is False
    matching = [r for r in caplog.records if "op skipped due to tier" in r.message]
    assert len(matching) == 1
    assert getattr(matching[0], "op", None) == "prose_enrichment"
    assert getattr(matching[0], "tier", None) == str(tier)


# ===========================================================================
# Block 9 — Coverage for defensive paths
# ===========================================================================


@pytest.mark.asyncio
async def test_startup_cleanup_swallows_brain_failure_no_emit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines 391-392 — startup finally: brain.end_session failure logs + skips emit."""
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    skin = _make_skin_mock(inputs=["shutdown"])
    skin.parse_command = AsyncMock(side_effect=RuntimeError("REPL force-fail"))
    event_bus = _make_event_bus_mock()

    # Cleanup will see _session_active=True (Story 3.5 startup step 9 sets it
    # after create_session succeeds). Fail brain.end_session ONLY for the
    # cleanup call. The signal handler also calls end_session, but it
    # short-circuits because _shutdown_event was never set.
    brain.end_session = AsyncMock(side_effect=StorageError("cleanup fail"))

    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        event_bus=event_bus,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    with (
        caplog.at_level(logging.ERROR, logger="nova.systems.nerve"),
        pytest.raises(RuntimeError, match="REPL force-fail"),
    ):
        await nerve.startup()
    assert any("brain.end_session failed" in r.message for r in caplog.records)
    # No SessionEnded emission after the failed Brain write
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 0


@pytest.mark.asyncio
async def test_startup_cleanup_swallows_emission_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines 403-404 — startup finally: SessionEnded emit failure logs cleanly."""
    brain = _make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x")
    skin = _make_skin_mock(inputs=["shutdown"])
    skin.parse_command = AsyncMock(side_effect=RuntimeError("REPL force-fail"))
    event_bus = _make_event_bus_mock()

    # First emit (SessionStarted) succeeds; second emit (SessionEnded from
    # cleanup) fails. Use side_effect list-style: success then failure.
    event_bus.emit = AsyncMock(side_effect=[None, RuntimeError("emit fail")])

    nerve = _build_nerve_system(
        brain=brain,
        skin=skin,
        event_bus=event_bus,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )
    with (
        caplog.at_level(logging.ERROR, logger="nova.systems.nerve"),
        pytest.raises(RuntimeError, match="REPL force-fail"),
    ):
        await nerve.startup()
    assert any("SessionEnded emission failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_repl_re_raises_external_cancellation() -> None:
    """Lines 447-452 — REPL outer CancelledError cancels both tasks then re-raises."""
    block_event = asyncio.Event()

    async def blocking_input(prompt: str) -> str:  # noqa: ARG001
        await block_event.wait()
        return "shutdown"

    skin = _make_skin_mock()
    skin.collect_input = AsyncMock(side_effect=blocking_input)
    nerve = _build_nerve_system(
        brain=_make_brain_mock(last_session=_session_summary(is_complete=True), last_seed="x"),
        skin=skin,
        config=_config(
            modes={"coding": _mode("coding")},
            settings=UserSettings(skip_briefing_if_recent=False),
        ),
    )

    startup_task = asyncio.create_task(nerve.startup())
    # Yield enough to let the REPL enter its asyncio.wait()
    for _ in range(10):
        await asyncio.sleep(0)
    startup_task.cancel()
    block_event.set()  # release the orphan thread for clean teardown
    with pytest.raises(asyncio.CancelledError):
        await startup_task


@pytest.mark.asyncio
async def test_install_signal_handler_is_idempotent() -> None:
    """Line 522 — second _install_signal_handler call is a no-op."""
    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._install_signal_handler()
    assert nerve._signal_handlers_installed is True
    # Second call: early-return at the guard. No exception, flag unchanged.
    nerve._install_signal_handler()
    assert nerve._signal_handlers_installed is True
    nerve._uninstall_signal_handler()


def test_uninstall_signal_handler_when_no_running_loop() -> None:
    """Lines 553-558 — uninstall outside an asyncio loop is a clean no-op."""
    nerve = _build_nerve_system()
    # Force flag to True without an actual install — _uninstall should
    # see "no running loop" and clear the flag without raising.
    nerve._signal_handlers_installed = True
    nerve._uninstall_signal_handler()
    assert nerve._signal_handlers_installed is False


def test_uninstall_signal_handler_when_not_installed_is_noop() -> None:
    """Line 550 — uninstall when flag is False returns immediately."""
    nerve = _build_nerve_system()
    assert nerve._signal_handlers_installed is False
    # No exception, no state change
    nerve._uninstall_signal_handler()
    assert nerve._signal_handlers_installed is False


@pytest.mark.asyncio
async def test_install_signal_handler_handles_missing_sigbreak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines 534->541 + 564->576 branches — SIGBREAK absent on the platform.

    Windows always has SIGBREAK; this test simulates the False arm by
    making ``getattr(signal, 'SIGBREAK', None)`` return None via a
    monkeypatch on the system module's ``signal`` reference.
    """
    import signal as signal_module

    from nova.systems.nerve import system as system_module

    class _SignalWithoutSigbreak:
        SIGINT = signal_module.SIGINT
        SIGTERM = signal_module.SIGTERM
        SIG_DFL = signal_module.SIG_DFL

        @staticmethod
        def signal(sig: int, handler: Any) -> Any:
            # Capture but don't actually install — keeps the test from
            # mutating real Windows signal state.
            return signal_module.SIG_DFL

    monkeypatch.setattr(system_module, "signal", _SignalWithoutSigbreak)
    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._install_signal_handler()
    assert nerve._signal_handlers_installed is True
    # Uninstall path also exercises the SIGBREAK-absent branch
    nerve._uninstall_signal_handler()
    assert nerve._signal_handlers_installed is False


def test_uninstall_signal_handler_swallows_restore_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines 566-567 — signal.signal() raising during restore is logged + swallowed."""
    import signal as signal_module

    from nova.systems.nerve import system as system_module

    class _SignalThatFailsRestore:
        SIGINT = signal_module.SIGINT
        SIGTERM = signal_module.SIGTERM
        SIGBREAK = getattr(signal_module, "SIGBREAK", None)
        SIG_DFL = signal_module.SIG_DFL

        @staticmethod
        def signal(sig: int, handler: Any) -> Any:
            raise OSError("mocked restore failure")

    monkeypatch.setattr(system_module, "signal", _SignalThatFailsRestore)
    nerve = _build_nerve_system()
    # Pretend the install ran successfully
    nerve._signal_handlers_installed = True
    nerve._previous_sigint_handler = signal_module.SIG_DFL

    async def _run() -> None:
        with caplog.at_level(logging.WARNING, logger="nova.systems.nerve"):
            nerve._uninstall_signal_handler()

    asyncio.run(_run())
    assert any("failed to restore" in r.message for r in caplog.records)
    assert nerve._signal_handlers_installed is False


def test_sync_signal_handler_factory_hops_to_on_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows sync handler hops to the loop via ``call_soon_threadsafe(_on_signal)``.

    The unified design (race-fix) routes both POSIX and Windows through
    ``_on_signal`` so the handler-task reference is captured in one
    place. The Windows sync handler must therefore schedule
    ``_on_signal`` (no args), not ``asyncio.create_task`` directly.
    """

    async def _run() -> None:
        nerve = _build_nerve_system()
        nerve._shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        scheduled: list[Any] = []

        def fake_call_soon_threadsafe(callback: Any, *args: Any) -> None:
            scheduled.append((callback, args))

        monkeypatch.setattr(loop, "call_soon_threadsafe", fake_call_soon_threadsafe)
        handler = nerve._sync_signal_handler_factory(loop)
        # Invoke as if the OS delivered SIGINT
        handler(2, None)
        assert len(scheduled) == 1
        callback, args = scheduled[0]
        # Single-owner scheduling: the sync handler hops to _on_signal,
        # which captures the task reference for the cleanup race fix.
        assert callback == nerve._on_signal
        assert args == ()

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_on_signal_captures_task_for_cleanup_to_await() -> None:
    """Race-fix lock — ``_on_signal`` stores the handler task on the instance.

    Without this, the Windows ``call_soon_threadsafe(asyncio.create_task,
    coro)`` shortcut loses the task reference. Cleanup needs the
    reference to ``await`` the in-flight handler before deciding
    whether to run its own ``end_session``.
    """
    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._session_active = False  # short-circuit so no Brain calls happen
    assert nerve._signal_handler_task is None
    nerve._on_signal()
    assert nerve._signal_handler_task is not None
    # Drain the scheduled task so it doesn't leak as "unhandled"
    await nerve._signal_handler_task


@pytest.mark.asyncio
async def test_on_signal_does_not_replace_in_flight_task() -> None:
    """Second SIGINT before first handler completes — keep the original task.

    Idempotency rule for ``_on_signal``: a second signal that arrives
    while the first handler is still running does NOT create a second
    handler task. The first handler's ``_session_active`` guard makes
    a second call a no-op anyway, but tracking only the first task
    keeps the cleanup contract crisp.
    """
    block_event = asyncio.Event()

    async def slow_handler() -> None:
        await block_event.wait()

    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._signal_handler_callback = slow_handler  # type: ignore[method-assign]
    nerve._on_signal()
    first_task = nerve._signal_handler_task
    assert first_task is not None
    # Second invocation while first is in flight — no replacement
    nerve._on_signal()
    assert nerve._signal_handler_task is first_task
    block_event.set()
    await first_task


@pytest.mark.asyncio
async def test_cleanup_skips_end_session_when_signal_handler_owned_cleanup() -> None:
    """Race-fix lock — cleanup awaits the handler task and SKIPS its own write.

    Sets up ``_signal_handler_task`` to a completed handler that already
    flipped ``_session_active=False``. Cleanup must NOT call
    ``brain.end_session`` again — single-owner contract.
    """
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True

    # Simulate a successful handler run: it called brain.end_session once,
    # flipped the flag, and emitted SessionEnded.
    async def handler_already_ran() -> None:
        await brain.end_session(42, seed_text=None, summary=None, is_complete=False)
        nerve._session_active = False
        await event_bus.emit(SessionEnded(session_id=42, seed_text=None, is_complete=False))

    nerve._signal_handler_task = asyncio.create_task(handler_already_ran())
    await nerve._cleanup_after_repl()

    # ONE end_session call (from the handler), ONE SessionEnded emit.
    # If the cleanup race were unfixed, brain.end_session.await_count
    # would be 2 and there'd be a second SessionEnded emission.
    assert brain.end_session.await_count == 1
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 1


@pytest.mark.asyncio
async def test_cleanup_skips_end_session_even_when_handler_failed() -> None:
    """Race-fix corollary — handler's failure doesn't grant cleanup a second attempt.

    Best-effort = ONE attempt. If the handler tried and failed, cleanup
    does NOT retry — the handler logged the failure, the session row
    stays in its current state, the user's next ``nova`` invocation
    handles the interrupted-session marker via Story 3.10.
    """
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True

    async def handler_failed() -> None:
        # Simulate a Brain failure: handler attempted the write, swallowed
        # the exception, did NOT flip _session_active, did NOT emit.
        try:
            raise StorageError("synthetic handler failure")
        except StorageError:
            pass

    nerve._signal_handler_task = asyncio.create_task(handler_failed())
    await nerve._cleanup_after_repl()

    # NO end_session call from cleanup (handler had its shot).
    assert brain.end_session.await_count == 0
    # NO SessionEnded emission from cleanup either.
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 0
    # _session_active stays True — the row is still open in nova.db. Story
    # 3.10's next-startup detection handles it as an interrupted session.
    assert nerve._session_active is True


@pytest.mark.asyncio
async def test_handle_shutdown_brain_failure_returns_exit_without_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Patch 1 (HIGH) lock — Brain failure does NOT trigger cleanup retry.

    User typed ``shutdown``, Brain ``end_session`` raises. The fix flips
    ``_session_active=False`` BEFORE the await so cleanup's
    re-end-session-with-is_complete=False is suppressed. Critical: the
    user's clean-shutdown intent is NOT silently overwritten with an
    interrupted-marker.
    """
    brain = _make_brain_mock()
    brain.end_session = AsyncMock(side_effect=StorageError("synthetic"))
    event_bus = _make_event_bus_mock()
    skin = _make_skin_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus, skin=skin)
    nerve._session_id = 42
    nerve._session_active = True
    cmd = Command(verb=CommandVerb.SHUTDOWN, target=None, raw_input="shutdown", is_contextual=False)
    with caplog.at_level(logging.ERROR, logger="nova.systems.nerve"):
        outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.EXIT
    assert nerve._session_active is False, (
        "_session_active must be flipped before the Brain await so cleanup skips"
    )
    # Brain was called once; no retry happened
    assert brain.end_session.await_count == 1
    # NO SessionEnded emission (write didn't confirm)
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 0
    # NO confirmation render — user should know something went wrong
    assert skin.render_response.call_count == 0
    # Failure logged
    assert any("brain.end_session failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_shutdown_emit_failure_still_renders_confirmation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Patch 1 lock — emit failure after successful Brain write still renders confirmation.

    Brain write is the durable fact; emission is observability. If emit
    raises after Brain succeeded, the user still gets ``"Session ended."``
    so they have feedback, but the failure is logged for operators.
    """
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    event_bus.emit = AsyncMock(side_effect=RuntimeError("broken bus"))
    skin = _make_skin_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus, skin=skin)
    nerve._session_id = 42
    nerve._session_active = True
    cmd = Command(verb=CommandVerb.SHUTDOWN, target=None, raw_input="shutdown", is_contextual=False)
    with caplog.at_level(logging.ERROR, logger="nova.systems.nerve"):
        outcome = await nerve.route_command(cmd)
    assert outcome is CommandOutcome.EXIT
    # Confirmation rendered despite emission failure
    assert skin.render_response.call_count == 1
    assert skin.render_response.call_args.args[0] == "Session ended."
    # Failure logged
    assert any("SessionEnded emission failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cleanup_suppresses_exception_from_handler_task() -> None:
    """Patch 5 lock — the `except Exception` branch in cleanup wait_for.

    If the handler task raises an unexpected exception that escapes its
    own try/except chain (e.g., a `RuntimeError` from a future code
    path that doesn't guard), the cleanup `wait_for` re-raises it. The
    `except Exception: pass` suppresses so cleanup proceeds — the
    handler is best-effort and any unexpected exception was already a
    bug; cleanup still owns the signal-handler uninstall + finally
    teardown invariants regardless.
    """
    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._session_active = False  # Phase 2 is no-op anyway

    async def handler_raises_unexpected() -> None:
        raise RuntimeError("synthetic uncaught handler bug")

    nerve._signal_handler_task = asyncio.create_task(handler_raises_unexpected())
    # Should NOT raise — the cleanup `except Exception: pass` suppresses
    await nerve._cleanup_after_repl()


@pytest.mark.asyncio
async def test_cleanup_warns_when_handler_task_does_not_complete_in_3s(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Patch 5 lock — cleanup's `wait_for(handler_task, timeout=3.0)` bounds the wait.

    A hung handler task (e.g., blocked on something the inner 2s
    `wait_for` doesn't cover) cannot hang cleanup itself. After 3s,
    `wait_for` cancels the handler and proceeds.
    """
    nerve = _build_nerve_system()
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = False  # cleanup Phase 2 is a no-op anyway

    async def slow_handler() -> None:
        await asyncio.sleep(10)  # would block indefinitely

    nerve._signal_handler_task = asyncio.create_task(slow_handler())
    with caplog.at_level(logging.WARNING, logger="nova.systems.nerve"):
        await asyncio.wait_for(nerve._cleanup_after_repl(), timeout=4.0)
    assert any(
        "signal-handler task did not complete within 3s" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_cleanup_runs_end_session_when_no_signal_handler() -> None:
    """No signal arrived (uncaught REPL exception) — cleanup writes the marker."""
    brain = _make_brain_mock()
    event_bus = _make_event_bus_mock()
    nerve = _build_nerve_system(brain=brain, event_bus=event_bus)
    nerve._shutdown_event = asyncio.Event()
    nerve._session_id = 42
    nerve._session_active = True
    nerve._signal_handler_task = None  # No signal arrived
    await nerve._cleanup_after_repl()
    assert brain.end_session.await_count == 1
    ended = [c for c in event_bus.emit.call_args_list if isinstance(c.args[0], SessionEnded)]
    assert len(ended) == 1


@pytest.mark.asyncio
async def test_signal_handler_no_op_when_shutdown_event_is_none() -> None:
    """Line 633->637 branch — handler tolerates _shutdown_event=None defensively.

    Hits the False arm of ``if self._shutdown_event is not None`` so the
    guard is exercised. In production this never happens (startup always
    creates the event), but the defensive check keeps the handler total.
    """
    brain = _make_brain_mock()
    nerve = _build_nerve_system(brain=brain)
    nerve._shutdown_event = None
    nerve._session_active = False  # short-circuit before the assertion
    await nerve._signal_handler_callback()
    # No Brain call, no exception, _session_active still False.
    assert brain.end_session.await_count == 0


# ===========================================================================
# Block I — Story 3.6: _handle_mode_switch (HandsPort delegation)
# ===========================================================================


@pytest.mark.asyncio
async def test_mode_switch_lookup_is_case_insensitive_and_passes_lowercased_stem_to_hands() -> None:
    """Story 3.4 contract: Nerve's NovaConfig.modes lookup is case-insensitive.

    Closes /bmad-code-review HIGH (user-reported). The Story 3.4 parser
    preserves the user's casing in ``command.target`` (line 406:
    "the lookup against NovaConfig.modes is case-insensitive at
    Nerve's level"). The kebab-case stem validator in Story 1.6 means
    config keys are always lowercase, so ``command.target.lower()``
    is the canonical lookup key. Without this normalization, typing
    ``mode Coding`` or ``Switch to Coding mode`` (parser →
    target="Coding") misses the ``"coding"`` config key and
    incorrectly renders "No mode named 'Coding'".

    This test exercises both shapes:
    1. The lookup resolves the uppercase ``"Coding"`` to the
       lowercase ``"coding"`` mode config.
    2. Hands receives the LOWERCASED canonical stem (not the
       user's casing) so downstream identity (audit target,
       ModeRestored event, _active_mode_name) stays canonical.
    """
    coding_mode = _mode("coding")
    hands = _make_hands_mock()
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="x",
                success=True,
                reason=None,
            )
        ]
    )
    nerve = _build_nerve_system(hands=hands, config=_config(modes={"coding": coding_mode}))
    nerve._session_id = 42
    nerve._session_active = True

    # User types `mode Coding` — parser preserves casing in target.
    cmd = Command(
        verb=CommandVerb.MODE, target="Coding", raw_input="mode Coding", is_contextual=False
    )
    outcome = await nerve.route_command(cmd)

    assert outcome is CommandOutcome.CONTINUE
    # Hands received the LOWERCASED canonical stem AND the resolved mode_config.
    hands.restore_mode.assert_called_once_with("coding", coding_mode)
    # Active mode name tracked as canonical (lowercased) stem.
    assert nerve._active_mode_name == "coding"


@pytest.mark.asyncio
async def test_mode_switch_unknown_target_error_echoes_original_user_casing() -> None:
    """Unknown-mode error message echoes the user's original casing.

    Closes the user-facing half of the case-insensitivity fix. The
    LOOKUP is case-insensitive, but if the lookup misses entirely the
    error template should reflect what the USER typed (not the
    lowercased internal lookup key) so they recognize the input.
    """
    skin = _make_skin_mock()
    hands = _make_hands_mock()
    nerve = _build_nerve_system(
        skin=skin,
        hands=hands,
        config=_config(modes={"coding": _mode("coding"), "study": _mode("study")}),
    )
    nerve._session_id = 42
    nerve._session_active = True

    # User types `mode UnknownMode` (capital U + M) — neither casing
    # nor lowercased version exists in config.modes.
    cmd = Command(
        verb=CommandVerb.MODE,
        target="UnknownMode",
        raw_input="mode UnknownMode",
        is_contextual=False,
    )
    await nerve.route_command(cmd)

    skin.render_response.assert_called_once_with(
        "No mode named 'UnknownMode'. Try mode to see available modes."
    )
    assert hands.restore_mode.call_count == 0
    assert nerve._active_mode_name is None


@pytest.mark.asyncio
async def test_mode_switch_unknown_target_renders_friendly_error_and_does_not_call_hands() -> None:
    """Unknown mode → friendly response, NO Hands call, _active_mode_name stays None."""
    skin = _make_skin_mock()
    hands = _make_hands_mock()
    nerve = _build_nerve_system(
        skin=skin,
        hands=hands,
        config=_config(modes={"coding": _mode("coding"), "study": _mode("study")}),
    )
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(
        verb=CommandVerb.MODE, target="unknown", raw_input="mode unknown", is_contextual=False
    )
    outcome = await nerve.route_command(cmd)

    assert outcome is CommandOutcome.CONTINUE
    skin.render_response.assert_called_once_with(
        "No mode named 'unknown'. Try mode to see available modes."
    )
    assert hands.restore_mode.call_count == 0
    assert nerve._active_mode_name is None


@pytest.mark.asyncio
async def test_mode_switch_known_target_delegates_to_hands_with_stem_and_mode_config() -> None:
    """Known mode → hands.restore_mode(mode_stem='coding', mode_config=<config>)."""
    coding_mode = _mode("coding")
    skin = _make_skin_mock()
    hands = _make_hands_mock()
    nerve = _build_nerve_system(
        skin=skin, hands=hands, config=_config(modes={"coding": coding_mode})
    )
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(
        verb=CommandVerb.MODE, target="coding", raw_input="mode coding", is_contextual=False
    )
    await nerve.route_command(cmd)

    hands.restore_mode.assert_called_once_with("coding", coding_mode)


@pytest.mark.asyncio
async def test_mode_switch_passes_command_target_as_mode_stem() -> None:
    """The stem comes from command.target — NOT from mode_config.name."""
    config_with_display_name = ModeConfig(
        name="Coding Display",  # display label different from the stem
        apps=(AppConfig(name="x", executable="x.exe"),),
    )
    hands = _make_hands_mock()
    nerve = _build_nerve_system(
        hands=hands,
        config=_config(modes={"coding": config_with_display_name}),
    )
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(
        verb=CommandVerb.MODE, target="coding", raw_input="mode coding", is_contextual=False
    )
    await nerve.route_command(cmd)

    args, kwargs = hands.restore_mode.call_args
    assert args[0] == "coding"  # stem from command.target
    assert args[0] != "Coding Display"  # NOT the display label


@pytest.mark.asyncio
async def test_mode_switch_sets_active_mode_name_to_stem_after_successful_restore() -> None:
    hands = _make_hands_mock()
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="x",
                success=True,
                reason=None,
            )
        ]
    )
    nerve = _build_nerve_system(hands=hands, config=_config(modes={"coding": _mode("coding")}))
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    await nerve.route_command(cmd)

    assert nerve._active_mode_name == "coding"


@pytest.mark.asyncio
async def test_mode_switch_clears_active_mode_name_on_second_restore_total_failure() -> None:
    """Second-restore total-failure clears ``_active_mode_name`` (doesn't keep stale).

    Closes /bmad-code-review patch #4 (EC#5). Sequence: ``mode coding``
    succeeds → ``_active_mode_name="coding"`` → ``mode coding`` again
    but every app now fails → ``_active_mode_name`` MUST clear to None
    (otherwise Story 3.7 shutdown and Story 3.9 status both lie about
    the workspace state right after the user saw "No apps could be
    launched").
    """
    coding_mode = _mode("coding")
    hands = _make_hands_mock()
    nerve = _build_nerve_system(hands=hands, config=_config(modes={"coding": coding_mode}))
    nerve._session_id = 42
    nerve._session_active = True

    # First restore — succeeds.
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="x",
                success=True,
                reason=None,
            )
        ]
    )
    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    await nerve.route_command(cmd)
    assert nerve._active_mode_name == "coding"

    # Second restore — every app fails.
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="x",
                success=False,
                reason="not found",
            )
        ]
    )
    await nerve.route_command(cmd)
    assert nerve._active_mode_name is None


@pytest.mark.asyncio
async def test_mode_switch_does_not_set_active_mode_name_on_total_failure() -> None:
    """Total-failure restore (zero apps launched) → ``_active_mode_name`` stays None.

    Closes Blind Hunter finding #13: claiming a mode is "active" when
    all configured apps failed to launch would lie to Story 3.7's
    shutdown summary and Story 3.9's status command.
    """
    hands = _make_hands_mock()
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="x",
                success=False,
                reason="not found",
            ),
            ActionResult(
                action_type=ActionType.APP_LAUNCH,
                target="y",
                success=False,
                reason="not found",
            ),
        ]
    )
    nerve = _build_nerve_system(hands=hands, config=_config(modes={"coding": _mode("coding")}))
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    await nerve.route_command(cmd)

    assert nerve._active_mode_name is None


@pytest.mark.asyncio
async def test_mode_switch_sets_active_mode_name_even_on_partial_restore() -> None:
    """Hands returns a partial result list — _active_mode_name still set (partial is active)."""
    coding_mode = _mode("coding")
    hands = _make_hands_mock()
    hands.restore_mode = AsyncMock(
        return_value=[
            ActionResult(action_type=ActionType.APP_LAUNCH, target="x", success=True, reason=None),
            ActionResult(
                action_type=ActionType.APP_LAUNCH, target="y", success=False, reason="not found"
            ),
        ]
    )
    nerve = _build_nerve_system(hands=hands, config=_config(modes={"coding": coding_mode}))
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    await nerve.route_command(cmd)

    assert nerve._active_mode_name == "coding"


@pytest.mark.asyncio
async def test_mode_switch_does_not_consult_tier_manager() -> None:
    """Mode restore is purely-local — must NOT read tier_manager.tier."""
    from unittest.mock import PropertyMock

    tier_mgr = MagicMock(spec=TierManager)
    tier_property = PropertyMock(return_value=CapabilityTier.OFFLINE)
    type(tier_mgr).tier = tier_property
    nerve = _build_nerve_system(
        tier_manager=tier_mgr, config=_config(modes={"coding": _mode("coding")})
    )
    nerve._session_id = 42
    nerve._session_active = True

    # Reset the access count after construction (TierManager.tier might be
    # touched during __init__ in the future; we only care about access
    # during the handler).
    tier_property.reset_mock()

    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    await nerve.route_command(cmd)

    assert tier_property.call_count == 0


@pytest.mark.asyncio
async def test_mode_switch_returns_continue_outcome() -> None:
    nerve = _build_nerve_system(config=_config(modes={"coding": _mode("coding")}))
    nerve._session_id = 42
    nerve._session_active = True

    cmd = Command(verb=CommandVerb.MODE, target="coding", raw_input="x", is_contextual=False)
    outcome = await nerve.route_command(cmd)

    assert outcome is CommandOutcome.CONTINUE
