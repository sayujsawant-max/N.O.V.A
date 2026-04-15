"""Capability tier state machine — the global ``TierManager``.

Single source of truth for N.O.V.A.'s operational tier (FULL / DEGRADED /
OFFLINE per ``CapabilityTier``). Every system reads the tier via
``tier_manager.tier`` before a cloud-dependent decision; every system
that detects a cloud failure calls ``report_failure`` /
``report_rate_limit_or_outage`` back into this module. No system owns a
parallel tier variable; no system guesses its own tier locally.

Tolerant-degrade model (pinned by architecture.md:796–801 and
epics.md:776–796)
----------------------------------------------------------------
- A **single** operation failure does NOT degrade from FULL — the
  ``degrade_failure_threshold`` (default 2) gates the FULL → DEGRADED
  transition. Locks project-context.md:196 ("Single malformed API
  response does NOT trigger tier degradation").
- An explicit upstream signal (HTTP 429 / 503 → ``report_rate_limit_or_outage``)
  bypasses the counter and transitions immediately.
- DEGRADED → OFFLINE is health-check-driven only: after
  ``offline_health_failure_threshold`` (default 3) consecutive failed
  ``check_now`` probes.
- Recovery up to FULL is also health-check-driven: a successful
  ``check_now`` from DEGRADED or OFFLINE transitions straight to FULL.
- Every transition emits exactly one ``TierChanged`` event; the
  ``TierChanged.reason`` field is drawn from a **closed canonical set**
  owned by this module. Caller-supplied ``reason`` arguments to
  ``report_failure`` / ``report_rate_limit_or_outage`` are logged (as
  ``caller_reason`` in the structured log's ``extra`` payload) but
  never cross the event-bus boundary — keeps the event payload safe
  for downstream renderers (Story 5.4 tier notice, Story 1.8 audit
  logger) without per-consumer scrubbing.

Architecture rules this module enforces
---------------------------------------
- No adapter imports (project-context.md:71, architecture.md:1271). The
  Claude adapter (future story) satisfies the ``HealthCheck`` Protocol
  structurally via PEP 544 — no explicit inheritance required.
- Constructor injection only; ``TierManager`` receives its
  ``HealthCheck`` and ``EventBus`` dependencies in ``__init__``.
- ``asyncio.Lock`` serializes every read-modify-(emit) sequence —
  concurrent failure reports cannot double-emit.
- ``check_now`` is a **state-updating probe that returns ``None``**:
  ``ApiUnavailableError`` is caught internally, state is updated, any
  transition event is emitted, and the method returns normally. Only
  non-domain exceptions (adapter bugs) propagate.
- The recovery loop re-raises ``asyncio.CancelledError``
  (project-context.md:49); any other exception reaching the loop is
  necessarily an adapter bug and ends the task.

References
----------
- Source: _bmad-output/planning-artifacts/epics.md Story 1.7.
- Source: _bmad-output/planning-artifacts/architecture.md §Decision 4.
- Source: _bmad-output/planning-artifacts/prd.md NFR17 (5-second detection budget).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from nova.core.events import EventBus, TierChanged
from nova.core.exceptions import ApiUnavailableError
from nova.core.types import CapabilityTier

__all__ = ["HealthCheck", "TierManager"]

logger = logging.getLogger("nova.core.tiers")


# ---------------------------------------------------------------------------
# Canonical reason strings — the closed set ``TierManager`` emits on
# ``TierChanged.reason``. Adding a new member is a story-level schema
# change; downstream consumers (Story 5.4 renders these verbatim) depend
# on the set staying stable.
# ---------------------------------------------------------------------------

_REASON_OPERATION_THRESHOLD = "2 consecutive API failures"
_REASON_RATE_LIMIT_OR_OUTAGE = "rate limit or outage signal"
_REASON_HEALTH_CHECK_OK = "health check succeeded"
_REASON_HEALTH_CHECK_FAILING = "health check consistently failing"


class HealthCheck(Protocol):
    """Narrow adapter-facing contract for the cloud reachability probe.

    The Claude adapter (future story) satisfies this structurally (PEP
    544) — no explicit inheritance required. ``ping`` returns ``None``
    on success and raises ``ApiUnavailableError`` on every failure mode
    the adapter chooses to surface (network timeout, HTTP error,
    malformed response, authentication failure). The ``timeout_seconds``
    keyword argument is required; ``TierManager`` always passes its
    configured ``health_check_timeout_seconds`` so the probe respects
    NFR17's 5-second detection budget.
    """

    async def ping(self, *, timeout_seconds: float) -> None: ...


class TierManager:
    """Owns the global capability tier state and emits ``TierChanged`` on every transition.

    Construct once at composition-root startup (Story 1.10) with an
    injected ``HealthCheck`` (the Claude adapter) and ``EventBus``. Nerve
    (Story 3.5) is the single driver of the public API: it calls
    ``report_success`` / ``report_failure`` / ``report_rate_limit_or_outage``
    from command handlers, calls ``check_now`` opportunistically before
    cloud-requiring actions, and runs ``run_recovery_loop`` as an
    ``asyncio.create_task`` background task for periodic probing.

    Every public mutator acquires ``_lock`` so concurrent callers
    serialize cleanly — guarantees "exactly one ``TierChanged`` per
    transition" (epics.md:791) even under ``asyncio.gather`` fan-out.
    """

    def __init__(
        self,
        *,
        health_check: HealthCheck,
        event_bus: EventBus,
        initial_tier: CapabilityTier = CapabilityTier.FULL,
        recovery_interval_seconds: float = 60.0,
        health_check_timeout_seconds: float = 5.0,
        degrade_failure_threshold: int = 2,
        offline_health_failure_threshold: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        # Precondition checks — surface a typo in composition-root wiring
        # immediately rather than silently subverting the state machine.
        # A zero or negative `recovery_interval_seconds` turns the recovery
        # loop into a busy spin; `degrade_failure_threshold < 1` breaks the
        # tolerant-degrade contract (first failure would trip degrade); a
        # zero `offline_health_failure_threshold` skips DEGRADED entirely.
        if recovery_interval_seconds <= 0:
            raise ValueError(
                f"recovery_interval_seconds must be > 0, got {recovery_interval_seconds}"
            )
        if health_check_timeout_seconds <= 0:
            raise ValueError(
                f"health_check_timeout_seconds must be > 0, got {health_check_timeout_seconds}"
            )
        if degrade_failure_threshold < 1:
            raise ValueError(
                f"degrade_failure_threshold must be >= 1, got {degrade_failure_threshold}"
            )
        if offline_health_failure_threshold < 1:
            raise ValueError(
                "offline_health_failure_threshold must be >= 1, "
                f"got {offline_health_failure_threshold}"
            )
        self._health_check = health_check
        self._event_bus = event_bus
        self._tier: CapabilityTier = initial_tier
        self._recovery_interval_seconds = recovery_interval_seconds
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self._degrade_failure_threshold = degrade_failure_threshold
        self._offline_health_failure_threshold = offline_health_failure_threshold
        self._clock = clock
        self._sleep = sleep
        self._consecutive_operation_failures = 0
        self._consecutive_health_check_failures = 0
        self._lock = asyncio.Lock()

    # --- Public API ---------------------------------------------------------

    @property
    def tier(self) -> CapabilityTier:
        """Current tier — synchronous, lock-free attribute read.

        Every system calls this before a cloud-dependent operation per
        project-context.md:71 ("Check tier state before cloud-dependent
        operations — never assume full connectivity").
        """
        return self._tier

    async def report_success(self) -> None:
        """A cloud operation just succeeded — resets the operation-failure counter.

        Does NOT recover the tier on its own (recovery is
        health-check-driven via ``check_now``, per architecture.md:800).
        Idempotent when the counter is already zero.
        """
        async with self._lock:
            self._consecutive_operation_failures = 0

    async def report_failure(self, *, reason: str) -> None:
        """A cloud operation failed with a tolerable failure (single timeout / malformed response).

        Increments the operation-failure counter. Transitions FULL →
        DEGRADED when the counter hits ``degrade_failure_threshold``.
        In DEGRADED/OFFLINE, the counter still advances (reserved for
        future multi-counter telemetry) but NO tier change fires —
        DEGRADED → OFFLINE is health-check-driven only.

        The caller's ``reason`` is log-only context; it never appears
        on the emitted ``TierChanged.reason`` field (canonical-only
        event contract, AC #7).
        """
        async with self._lock:
            self._consecutive_operation_failures += 1
            logger.warning(
                "operation failure reported",
                extra={
                    "tier": str(self._tier),
                    "caller_reason": reason,
                    "consecutive_failures": self._consecutive_operation_failures,
                    "threshold": self._degrade_failure_threshold,
                },
            )
            if (
                self._tier is CapabilityTier.FULL
                and self._consecutive_operation_failures >= self._degrade_failure_threshold
            ):
                await self._transition_to(CapabilityTier.DEGRADED, _REASON_OPERATION_THRESHOLD)

    async def report_rate_limit_or_outage(self, *, reason: str) -> None:
        """Explicit upstream signal (HTTP 429 / 503) — transitions FULL → DEGRADED immediately.

        Bypasses the operation-failure counter per architecture.md:798
        ("clear upstream signal"). Idempotent when already
        DEGRADED/OFFLINE — logs at DEBUG and no second event is emitted.

        The caller's ``reason`` is log-only context; the event payload's
        ``reason`` is the canonical ``"rate limit or outage signal"``.
        """
        async with self._lock:
            if self._tier is CapabilityTier.FULL:
                logger.warning(
                    "rate limit or outage reported",
                    extra={"tier": str(self._tier), "caller_reason": reason},
                )
                await self._transition_to(CapabilityTier.DEGRADED, _REASON_RATE_LIMIT_OR_OUTAGE)
            else:
                logger.debug(
                    "rate limit or outage reported while already degraded/offline",
                    extra={"tier": str(self._tier), "caller_reason": reason},
                )

    async def check_now(self) -> None:
        """Run the health check and update tier state. Always returns ``None``.

        ``ApiUnavailableError`` is caught internally: state is updated
        (counter incremented, possible DEGRADED → OFFLINE transition
        fired), and the exception is swallowed. Any other exception is
        a bug in the adapter's domain-exception translation and is
        allowed to propagate — the composition root's top-level handler
        (Story 1.10) is the safety net.

        Called by Nerve (Story 3.5) opportunistically before cloud-
        requiring actions, and by ``run_recovery_loop`` on every tick.
        """
        async with self._lock:
            try:
                # Defense-in-depth: an adapter that silently ignores its
                # `timeout_seconds` argument would otherwise hold the lock
                # indefinitely. `asyncio.wait_for` caps the await at the
                # configured budget regardless of what the adapter does.
                await asyncio.wait_for(
                    self._health_check.ping(timeout_seconds=self._health_check_timeout_seconds),
                    timeout=self._health_check_timeout_seconds,
                )
            except ApiUnavailableError:
                await self._handle_health_check_failure()
                return
            # NOTE: `TimeoutError` (raised by `asyncio.wait_for` when the
            # budget trips, or leaked from an adapter that failed to
            # translate a network timeout to `ApiUnavailableError`) is
            # intentionally NOT caught here. Per AC #5 only
            # `ApiUnavailableError` is swallowed; every other exception
            # propagates so adapter-translation bugs are visible rather
            # than being silently relabeled as a cloud-unavailable signal.
            # The `wait_for` wrapper above still contains the damage — it
            # converts an infinite lock-hold into a loud task-death with
            # a traceback at the composition-root top-level handler.
            await self._handle_health_check_success()

    async def run_recovery_loop(self) -> None:
        """Long-lived background coroutine — periodic health-check ticking.

        Started by Nerve (Story 3.5) via
        ``asyncio.create_task(tier_manager.run_recovery_loop())``. Each
        iteration sleeps for ``recovery_interval_seconds`` and then
        calls ``check_now``. ``asyncio.CancelledError`` is re-raised
        per project-context.md:49; ``ApiUnavailableError`` never
        reaches this coroutine (``check_now`` swallows it); any other
        exception is an adapter bug and ends the task.
        """
        logger.debug(
            "recovery loop starting",
            extra={
                "recovery_interval_seconds": self._recovery_interval_seconds,
                "monotonic_seconds": self._clock(),
            },
        )
        try:
            while True:
                try:
                    await self._sleep(self._recovery_interval_seconds)
                except asyncio.CancelledError:
                    raise
                logger.debug("recovery loop tick")
                await self.check_now()
        except asyncio.CancelledError:
            logger.info("recovery loop cancelled")
            raise

    # --- Private helpers ----------------------------------------------------

    async def _handle_health_check_success(self) -> None:
        """Health check succeeded — reset counters, recover if degraded/offline.

        Caller must hold ``self._lock``.
        """
        self._consecutive_operation_failures = 0
        self._consecutive_health_check_failures = 0
        if self._tier is CapabilityTier.FULL:
            return
        await self._transition_to(CapabilityTier.FULL, _REASON_HEALTH_CHECK_OK)

    async def _handle_health_check_failure(self) -> None:
        """Health check raised ``ApiUnavailableError`` — update state accordingly.

        Behavior varies by current tier:

        - FULL: tolerant-degrade rule — a single health-check flake does
          NOT degrade. Only operation-facing failures drive the FULL →
          DEGRADED transition.
        - DEGRADED: increment the health-check failure counter; transition
          to OFFLINE when the counter hits
          ``offline_health_failure_threshold``.
        - OFFLINE: already at the bottom — log at DEBUG and return.

        Caller must hold ``self._lock``.
        """
        if self._tier is CapabilityTier.FULL:
            logger.warning(
                "health check failed in FULL tier — not degrading (tolerant-degrade rule)",
                extra={"tier": str(self._tier)},
            )
            return
        if self._tier is CapabilityTier.OFFLINE:
            logger.debug(
                "health check failed in OFFLINE tier — no transition",
                extra={"tier": str(self._tier)},
            )
            return
        # DEGRADED
        self._consecutive_health_check_failures += 1
        logger.warning(
            "health check failed in DEGRADED tier",
            extra={
                "tier": str(self._tier),
                "consecutive_failures": self._consecutive_health_check_failures,
                "threshold": self._offline_health_failure_threshold,
            },
        )
        if self._consecutive_health_check_failures >= self._offline_health_failure_threshold:
            await self._transition_to(CapabilityTier.OFFLINE, _REASON_HEALTH_CHECK_FAILING)

    async def _transition_to(self, new_tier: CapabilityTier, reason: str) -> None:
        """Single transition call site — updates state, logs, then emits.

        Update-then-emit ordering (AC #8) is enforced here. ``reason``
        MUST be one of the module-level canonical constants; a
        self-transition is a bug at every caller. Caller MUST hold
        ``self._lock``.
        """
        # Explicit RuntimeError (not `assert`) so the invariant survives
        # `python -O` / `PYTHONOPTIMIZE=1` — a self-transition would
        # otherwise silently emit a bogus `TierChanged(prev == new)` event.
        if new_tier is self._tier:
            raise RuntimeError(f"self-transition from {self._tier} to {new_tier} is a bug")
        previous = self._tier
        self._tier = new_tier
        # Reset health-check counter whenever we leave the failing-ladder.
        if new_tier is CapabilityTier.FULL:
            self._consecutive_operation_failures = 0
            self._consecutive_health_check_failures = 0
        elif new_tier is CapabilityTier.OFFLINE:
            self._consecutive_health_check_failures = 0
        logger.info(
            "tier changed",
            extra={
                "previous_tier": str(previous),
                "new_tier": str(new_tier),
                "reason": reason,
            },
        )
        await self._event_bus.emit(
            TierChanged(previous_tier=previous, new_tier=new_tier, reason=reason)
        )
