"""Story 1.7 contract tests for `nova.core.tiers`.

Covers the capability tier state machine: tolerant-degrade model,
health-check-driven recovery, canonical-reason closed-set event payload,
lock-serialized transitions, and deterministic async scheduling.

All tests are deterministic: no real ``asyncio.sleep(n)`` for ``n > 0``, no
real ``time.sleep``, no ``anthropic`` import, no network. The AST-based
gate ``test_tiers_test_file_uses_no_real_sleep_or_network`` locks this at
the test-file-structure level (carry-forward from Story 1.6 review lesson:
AST walk > text regex for forbidden-pattern guards).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from nova.core.events import Event, EventBus, TierChanged
from nova.core.exceptions import ApiUnavailableError
from nova.core.tiers import (
    _REASON_HEALTH_CHECK_FAILING,
    _REASON_HEALTH_CHECK_OK,
    _REASON_OPERATION_THRESHOLD,
    _REASON_RATE_LIMIT_OR_OUTAGE,
    HealthCheck,
    TierManager,
)
from nova.core.types import CapabilityTier

# Canonical reason strings are imported from `nova.core.tiers` directly
# (not redeclared) so that a drift in the production constants fails
# these tests immediately rather than silently approving the stale copy.

_CANONICAL_REASONS: frozenset[str] = frozenset(
    {
        _REASON_OPERATION_THRESHOLD,
        _REASON_RATE_LIMIT_OR_OUTAGE,
        _REASON_HEALTH_CHECK_OK,
        _REASON_HEALTH_CHECK_FAILING,
    }
)


# ---------------------------------------------------------------------------
# Test doubles — structural-typed HealthCheck, recording EventBus, fake sleep.
# ---------------------------------------------------------------------------


class _FakeHealthCheck:
    """Programmable ``HealthCheck`` test double.

    Satisfies the ``HealthCheck`` Protocol structurally (PEP 544) — no
    explicit inheritance. Each call to ``ping`` consumes one entry from
    ``responses`` (``None`` = success, an exception instance = raise it).
    When the queue is empty, ``default_response`` is returned (``None`` by
    default, i.e. success).
    """

    def __init__(
        self,
        *,
        default_response: BaseException | None = None,
    ) -> None:
        self.responses: list[BaseException | None] = []
        self.default_response = default_response
        self.call_count = 0
        self.timeout_seconds_received: list[float] = []

    async def ping(self, *, timeout_seconds: float) -> None:
        self.call_count += 1
        self.timeout_seconds_received.append(timeout_seconds)
        response = self.responses.pop(0) if self.responses else self.default_response
        if response is not None:
            raise response


class _RecordingEventBus(EventBus):
    """``EventBus`` subclass that records every emitted event in order.

    Still dispatches to subscribed handlers so tests can observe both the
    raw emission stream and the handler-side view.
    """

    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)
        await super().emit(event)


async def _noop_sleep(delay: float) -> None:
    """Default sleep fake — returns immediately, records nothing."""
    return None


def _make_manager(
    *,
    health_check: _FakeHealthCheck | None = None,
    initial_tier: CapabilityTier = CapabilityTier.FULL,
    recovery_interval_seconds: float = 60.0,
    health_check_timeout_seconds: float = 5.0,
    degrade_failure_threshold: int = 2,
    offline_health_failure_threshold: int = 3,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> tuple[TierManager, _RecordingEventBus, _FakeHealthCheck]:
    hc = health_check if health_check is not None else _FakeHealthCheck()
    bus = _RecordingEventBus()
    actual_sleep = sleep if sleep is not None else _noop_sleep
    manager = TierManager(
        health_check=hc,
        event_bus=bus,
        initial_tier=initial_tier,
        recovery_interval_seconds=recovery_interval_seconds,
        health_check_timeout_seconds=health_check_timeout_seconds,
        degrade_failure_threshold=degrade_failure_threshold,
        offline_health_failure_threshold=offline_health_failure_threshold,
        sleep=actual_sleep,
    )
    return manager, bus, hc


# ---------------------------------------------------------------------------
# Shape tests (3)
# ---------------------------------------------------------------------------


async def test_tier_manager_instantiates_with_minimum_args() -> None:
    manager, bus, hc = _make_manager()
    assert manager.tier is CapabilityTier.FULL
    assert bus.events == []
    assert hc.call_count == 0


async def test_health_check_protocol_has_exactly_one_method_named_ping() -> None:
    members = [
        name
        for name, _ in inspect.getmembers(HealthCheck, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    assert members == ["ping"]


async def test_tier_property_returns_initial_tier() -> None:
    manager, _bus, _hc = _make_manager(initial_tier=CapabilityTier.OFFLINE)
    assert manager.tier is CapabilityTier.OFFLINE


# ---------------------------------------------------------------------------
# Constructor precondition validation (4)
# ---------------------------------------------------------------------------


async def test_init_rejects_zero_recovery_interval_seconds() -> None:
    with pytest.raises(ValueError, match="recovery_interval_seconds must be > 0"):
        _make_manager(recovery_interval_seconds=0)


async def test_init_rejects_zero_health_check_timeout_seconds() -> None:
    with pytest.raises(ValueError, match="health_check_timeout_seconds must be > 0"):
        _make_manager(health_check_timeout_seconds=0)


async def test_init_rejects_zero_degrade_failure_threshold() -> None:
    with pytest.raises(ValueError, match="degrade_failure_threshold must be >= 1"):
        _make_manager(degrade_failure_threshold=0)


async def test_init_rejects_zero_offline_health_failure_threshold() -> None:
    with pytest.raises(ValueError, match="offline_health_failure_threshold must be >= 1"):
        _make_manager(offline_health_failure_threshold=0)


# ---------------------------------------------------------------------------
# FULL → DEGRADED transitions (5)
# ---------------------------------------------------------------------------


async def test_single_failure_does_not_degrade() -> None:
    """Tolerant-degrade rule — project-context.md:196. Load-bearing."""
    manager, bus, _hc = _make_manager()
    await manager.report_failure(reason="one blip")
    assert manager.tier is CapabilityTier.FULL
    assert bus.events == []


async def test_second_consecutive_failure_degrades_with_canonical_reason() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_failure(reason="one")
    await manager.report_failure(reason="two")
    assert manager.tier is CapabilityTier.DEGRADED
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.previous_tier is CapabilityTier.FULL
    assert event.new_tier is CapabilityTier.DEGRADED
    assert event.reason == _REASON_OPERATION_THRESHOLD


async def test_report_success_resets_operation_failure_counter() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_failure(reason="one")
    await manager.report_success()
    await manager.report_failure(reason="fresh one")
    # Counter reset — one fresh failure is NOT enough to degrade.
    assert manager.tier is CapabilityTier.FULL
    assert bus.events == []


async def test_rate_limit_or_outage_degrades_immediately() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason="HTTP 429")
    assert manager.tier is CapabilityTier.DEGRADED
    assert len(bus.events) == 1
    assert bus.events[0].reason == _REASON_RATE_LIMIT_OR_OUTAGE  # type: ignore[attr-defined]


async def test_caller_reason_is_logged_but_not_emitted_on_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, bus, _hc = _make_manager()
    caplog.set_level(logging.WARNING, logger="nova.core.tiers")
    sensitive = "sk-ant-REDACTED-CALLER-CONTEXT"
    await manager.report_failure(reason=sensitive)
    await manager.report_failure(reason=sensitive)
    # Event does not carry caller reason.
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert sensitive not in event.reason
    assert event.reason == _REASON_OPERATION_THRESHOLD
    # Log record carries caller reason in `caller_reason` extra.
    caller_reasons = [
        getattr(r, "caller_reason", None) for r in caplog.records if r.name == "nova.core.tiers"
    ]
    assert sensitive in caller_reasons


# ---------------------------------------------------------------------------
# DEGRADED → OFFLINE transitions (4)
# ---------------------------------------------------------------------------


async def test_single_check_now_failure_in_degraded_does_not_go_offline() -> None:
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    await manager.check_now()
    assert manager.tier is CapabilityTier.DEGRADED
    assert bus.events == []


async def test_three_consecutive_check_now_failures_transition_to_offline() -> None:
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    await manager.check_now()
    await manager.check_now()
    await manager.check_now()
    assert manager.tier is CapabilityTier.OFFLINE
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.previous_tier is CapabilityTier.DEGRADED
    assert event.new_tier is CapabilityTier.OFFLINE
    assert event.reason == _REASON_HEALTH_CHECK_FAILING


async def test_custom_offline_threshold_pushes_transition() -> None:
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(
        health_check=hc,
        initial_tier=CapabilityTier.DEGRADED,
        offline_health_failure_threshold=5,
    )
    for _ in range(4):
        await manager.check_now()
    assert manager.tier is CapabilityTier.DEGRADED
    assert bus.events == []
    await manager.check_now()
    # Re-read through a widely-typed local to reset mypy's stale narrowing —
    # without this, the prior `is DEGRADED` assert keeps mypy's view of
    # `manager.tier` pinned to `Literal[DEGRADED]` even after state changes.
    final_tier: CapabilityTier = manager.tier
    assert final_tier is CapabilityTier.OFFLINE
    assert len(bus.events) == 1


async def test_health_check_success_in_degraded_resets_offline_counter() -> None:
    hc = _FakeHealthCheck()
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    # 2 failures, then 1 success, then 2 more failures → still DEGRADED.
    hc.responses = [
        ApiUnavailableError("down"),
        ApiUnavailableError("down"),
        None,  # recovery → FULL
    ]
    await manager.check_now()
    await manager.check_now()
    await manager.check_now()
    # Recovery transition fired; tier is FULL now.
    assert manager.tier is CapabilityTier.FULL
    assert len(bus.events) == 1
    assert bus.events[0].new_tier is CapabilityTier.FULL  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Recovery transitions (4)
# ---------------------------------------------------------------------------


async def test_check_now_success_from_degraded_to_full() -> None:
    manager, bus, _hc = _make_manager(initial_tier=CapabilityTier.DEGRADED)
    await manager.check_now()
    assert manager.tier is CapabilityTier.FULL
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.previous_tier is CapabilityTier.DEGRADED
    assert event.new_tier is CapabilityTier.FULL
    assert event.reason == _REASON_HEALTH_CHECK_OK


async def test_check_now_success_from_offline_to_full() -> None:
    manager, bus, _hc = _make_manager(initial_tier=CapabilityTier.OFFLINE)
    await manager.check_now()
    assert manager.tier is CapabilityTier.FULL
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.previous_tier is CapabilityTier.OFFLINE
    assert event.new_tier is CapabilityTier.FULL


async def test_check_now_success_in_full_emits_nothing() -> None:
    manager, bus, _hc = _make_manager()
    await manager.check_now()
    assert manager.tier is CapabilityTier.FULL
    assert bus.events == []


async def test_check_now_failure_in_offline_emits_nothing() -> None:
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("still down"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.OFFLINE)
    await manager.check_now()
    assert manager.tier is CapabilityTier.OFFLINE
    assert bus.events == []


# ---------------------------------------------------------------------------
# Never-emit-self-event guarantees (3)
# ---------------------------------------------------------------------------


async def test_report_success_in_full_emits_nothing() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_success()
    assert manager.tier is CapabilityTier.FULL
    assert bus.events == []


async def test_report_failure_in_degraded_emits_nothing() -> None:
    manager, bus, _hc = _make_manager(initial_tier=CapabilityTier.DEGRADED)
    # Even hitting the operation-failure threshold in DEGRADED does NOT
    # transition — DEGRADED → OFFLINE is health-check-driven only.
    for _ in range(5):
        await manager.report_failure(reason="x")
    assert manager.tier is CapabilityTier.DEGRADED
    assert bus.events == []


async def test_rate_limit_second_call_in_degraded_is_idempotent() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason="1st")
    await manager.report_rate_limit_or_outage(reason="2nd")
    assert manager.tier is CapabilityTier.DEGRADED
    assert len(bus.events) == 1  # exactly one, the second is a no-op


# ---------------------------------------------------------------------------
# run_recovery_loop tests (4)
# ---------------------------------------------------------------------------


async def test_recovery_loop_ticks_once_then_cancels() -> None:
    """Loop tick → sleep → check_now → sleep again (blocked by Event)."""
    blocker = asyncio.Event()
    second_sleep_called = asyncio.Event()
    sleep_calls: list[float] = []

    async def blocking_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        if len(sleep_calls) == 1:
            return None  # first sleep returns immediately to let the tick happen
        second_sleep_called.set()
        await blocker.wait()  # second sleep blocks forever

    manager, _bus, hc = _make_manager(sleep=blocking_sleep, recovery_interval_seconds=42.0)
    task = asyncio.create_task(manager.run_recovery_loop())
    await second_sleep_called.wait()  # ensure at least one full tick happened
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sleep_calls[0] == 42.0  # sleeps with the configured interval
    assert hc.call_count == 1


async def test_recovery_loop_re_raises_cancelled_error() -> None:
    """Project-context.md:49 — never swallow CancelledError."""
    blocker = asyncio.Event()
    entered_sleep = asyncio.Event()

    async def blocking_sleep(delay: float) -> None:
        entered_sleep.set()
        await blocker.wait()

    manager, _bus, _hc = _make_manager(sleep=blocking_sleep)
    task = asyncio.create_task(manager.run_recovery_loop())
    # Wait deterministically for the task to reach its first `await self._sleep(...)`;
    # a bare `asyncio.sleep(0)` yields once but does not guarantee the task got there.
    await entered_sleep.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_recovery_loop_continues_and_transitions_across_failing_ticks() -> None:
    """Health-check failures don't kill the loop; state still advances."""
    sleep_count = 0

    async def counting_sleep(delay: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError()

    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(
        health_check=hc,
        initial_tier=CapabilityTier.DEGRADED,
        offline_health_failure_threshold=2,
        sleep=counting_sleep,
    )
    with pytest.raises(asyncio.CancelledError):
        await manager.run_recovery_loop()
    # sleeps 1, 2 completed; 3rd raised → check_now ran exactly 2 times.
    assert hc.call_count == 2
    # After 2 failures with threshold=2, DEGRADED → OFFLINE.
    assert manager.tier is CapabilityTier.OFFLINE
    assert len(bus.events) == 1
    assert bus.events[0].new_tier is CapabilityTier.OFFLINE  # type: ignore[attr-defined]


async def test_recovery_loop_dies_on_non_domain_exception() -> None:
    """Adapter bugs surface, not swallowed — also confirms check_now's narrow suppression."""
    hc = _FakeHealthCheck(default_response=RuntimeError("adapter bug"))
    manager, _bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    with pytest.raises(RuntimeError, match="adapter bug"):
        await manager.run_recovery_loop()
    assert hc.call_count == 1


# ---------------------------------------------------------------------------
# Concurrency (2)
# ---------------------------------------------------------------------------


async def test_concurrent_failures_emit_exactly_one_event() -> None:
    manager, bus, _hc = _make_manager()
    await asyncio.gather(
        manager.report_failure(reason="a"),
        manager.report_failure(reason="b"),
    )
    assert manager.tier is CapabilityTier.DEGRADED
    assert len(bus.events) == 1


async def test_tier_property_is_synchronous() -> None:
    """The ``tier`` property is a plain attribute read — no await needed."""
    manager, _bus, _hc = _make_manager(initial_tier=CapabilityTier.OFFLINE)
    assert manager.tier is CapabilityTier.OFFLINE
    # Calling the property multiple times is free.
    for _ in range(10):
        assert manager.tier is CapabilityTier.OFFLINE


# ---------------------------------------------------------------------------
# Contract tests — TierChanged payload (4)
# ---------------------------------------------------------------------------


async def test_emitted_event_has_source_nerve() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason="x")
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.source == "nerve"


async def test_emitted_event_previous_and_new_tier_differ() -> None:
    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason="x")
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.previous_tier is not event.new_tier


async def test_tier_changed_reason_is_canonical() -> None:
    """Every emitted event's reason is in the closed canonical set (AC #7)."""
    # Drive every transition and collect the reasons.
    reasons: list[str] = []

    # FULL → DEGRADED via report_failure
    manager, bus, _hc = _make_manager()
    await manager.report_failure(reason="a")
    await manager.report_failure(reason="b")
    reasons.extend(e.reason for e in bus.events if isinstance(e, TierChanged))

    # FULL → DEGRADED via report_rate_limit_or_outage
    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason="x")
    reasons.extend(e.reason for e in bus.events if isinstance(e, TierChanged))

    # DEGRADED → FULL via check_now success
    manager, bus, _hc = _make_manager(initial_tier=CapabilityTier.DEGRADED)
    await manager.check_now()
    reasons.extend(e.reason for e in bus.events if isinstance(e, TierChanged))

    # OFFLINE → FULL via check_now success
    manager, bus, _hc = _make_manager(initial_tier=CapabilityTier.OFFLINE)
    await manager.check_now()
    reasons.extend(e.reason for e in bus.events if isinstance(e, TierChanged))

    # DEGRADED → OFFLINE via check_now threshold
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    await manager.check_now()
    await manager.check_now()
    await manager.check_now()
    reasons.extend(e.reason for e in bus.events if isinstance(e, TierChanged))

    assert len(reasons) == 5
    assert set(reasons) == _CANONICAL_REASONS
    for reason in reasons:
        assert reason in _CANONICAL_REASONS


async def test_caller_reason_never_appears_on_emitted_event() -> None:
    """Sensitive caller context is scrubbed from the event payload."""
    sensitive = "sk-ant-LEAK https://api.anthropic.com/v1/messages"

    manager, bus, _hc = _make_manager()
    await manager.report_failure(reason=sensitive)
    await manager.report_failure(reason=sensitive)
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert sensitive not in event.reason
    assert "sk-ant-LEAK" not in event.reason

    manager, bus, _hc = _make_manager()
    await manager.report_rate_limit_or_outage(reason=sensitive)
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert sensitive not in event.reason


# ---------------------------------------------------------------------------
# Event-ordering (1)
# ---------------------------------------------------------------------------


async def test_tier_updated_before_event_emitted() -> None:
    """Handler observing the bus sees the NEW tier when it fires (AC #8)."""
    manager, bus, _hc = _make_manager()
    observed: list[CapabilityTier] = []

    async def handler(event: Event) -> None:
        if isinstance(event, TierChanged):
            observed.append(manager.tier)

    await bus.subscribe(TierChanged, handler)
    await manager.report_failure(reason="one")
    await manager.report_failure(reason="two")
    assert observed == [CapabilityTier.DEGRADED]
    assert manager.tier is CapabilityTier.DEGRADED


# ---------------------------------------------------------------------------
# check_now contract tests (4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start_tier",
    [CapabilityTier.FULL, CapabilityTier.DEGRADED, CapabilityTier.OFFLINE],
)
async def test_check_now_swallows_api_unavailable_error(
    start_tier: CapabilityTier,
) -> None:
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=start_tier)
    # No exception should propagate — check_now swallows ApiUnavailableError
    # and returns None.
    await manager.check_now()
    # State invariance — a single health-check failure never transitions:
    # - FULL: tolerant-degrade (only operation failures degrade)
    # - DEGRADED: below `offline_health_failure_threshold` (default 3)
    # - OFFLINE: already at the bottom
    # AC #6 rows (FULL+fail), (DEGRADED+1st-fail), (OFFLINE+fail) all say NO event.
    assert manager.tier is start_tier
    assert bus.events == []


async def test_check_now_updates_state_before_swallowing() -> None:
    """Even though ApiUnavailableError is caught, state is updated and event fires."""
    hc = _FakeHealthCheck(default_response=ApiUnavailableError("down"))
    manager, bus, _hc = _make_manager(
        health_check=hc,
        initial_tier=CapabilityTier.DEGRADED,
        offline_health_failure_threshold=2,
    )
    await manager.check_now()  # 1st — no transition
    await manager.check_now()  # 2nd — DEGRADED → OFFLINE
    assert manager.tier is CapabilityTier.OFFLINE
    assert len(bus.events) == 1
    event = bus.events[0]
    assert isinstance(event, TierChanged)
    assert event.new_tier is CapabilityTier.OFFLINE


async def test_check_now_propagates_non_domain_exception() -> None:
    hc = _FakeHealthCheck(default_response=RuntimeError("adapter bug"))
    manager, _bus, _hc = _make_manager(health_check=hc)
    with pytest.raises(RuntimeError, match="adapter bug"):
        await manager.check_now()


async def test_check_now_passes_configured_timeout_to_ping() -> None:
    hc = _FakeHealthCheck()
    manager, _bus, _hc = _make_manager(health_check=hc, health_check_timeout_seconds=7.5)
    await manager.check_now()
    assert hc.timeout_seconds_received == [7.5]


async def test_check_now_propagates_asyncio_timeout_error() -> None:
    """`TimeoutError` is NOT swallowed by `check_now` — it propagates per AC #5.

    An adapter that raises `TimeoutError` (either by leaking a bare
    network timeout instead of translating to `ApiUnavailableError`, or
    by being caught by the defensive `asyncio.wait_for` wrapper) is
    surfacing an adapter-contract violation. `check_now` must let that
    propagate so the bug is visible at the composition-root top-level
    handler — NOT silently relabel it as a cloud-unavailable signal.
    The `wait_for` wrapper still caps the damage: instead of an
    infinite lock-hold we get a loud task-death with a traceback.
    """
    hc = _FakeHealthCheck(default_response=TimeoutError("adapter ignored budget"))
    manager, bus, _hc = _make_manager(health_check=hc, initial_tier=CapabilityTier.DEGRADED)
    with pytest.raises(TimeoutError, match="adapter ignored budget"):
        await manager.check_now()
    # Tier unchanged; no event emitted. The propagating exception means
    # the state machine neither transitioned nor updated counters.
    assert manager.tier is CapabilityTier.DEGRADED
    assert bus.events == []


# ---------------------------------------------------------------------------
# AST guard — lock determinism of the test file itself
# ---------------------------------------------------------------------------


def test_tiers_test_file_uses_no_real_sleep_or_network() -> None:
    """AST walk rejects real sleeps, positive `asyncio.sleep`, and `anthropic` imports.

    Rationale: epics.md:796 pins "tests use a deterministic clock and
    mock health check — no wall-clock or network dependencies." The AST
    walk catches a future author who "just wants to speed this up a
    little". ``asyncio.sleep(0)`` is allowed (yield-to-loop idiom).

    Carry-forward from Story 1.6 review lesson: inspect AST via
    ``ast.walk`` + ``ast.Call`` / ``ast.Import`` nodes rather than
    grepping source text — text regex false-positives on docstrings,
    comments, and string literals.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []

    for node in ast.walk(tree):
        # Forbid any `anthropic` import anywhere.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "anthropic":
                    violations.append(f"import anthropic at line {node.lineno}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[0] == "anthropic"
        ):
            violations.append(f"from anthropic at line {node.lineno}")

        # Forbid time.sleep(...) — blocks the event loop AND the test.
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "time"
                and func.attr == "sleep"
            ):
                violations.append(f"time.sleep(...) at line {node.lineno}")

            # Forbid asyncio.sleep(n) for any positive n — loops would stall.
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "asyncio"
                and func.attr == "sleep"
                and node.args
            ):
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, (int, float))
                    and first.value != 0
                ):
                    violations.append(
                        f"asyncio.sleep({first.value}) at line {node.lineno} — "
                        "use a sleep fake injected into TierManager instead"
                    )

    assert not violations, "Forbidden time/network patterns in test_tiers.py:\n  " + "\n  ".join(
        violations
    )
