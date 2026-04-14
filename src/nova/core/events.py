"""Typed event classes + in-process async event bus for inter-system communication.

All inter-system communication in N.O.V.A. flows through an `EventBus`
instance wired in `app.py` (composition root, Story 1.10). Systems never
call each other's ports directly — they emit typed events and Nerve
routes subscriptions (Story 3.5).

Architecture divergence owned by this story
-------------------------------------------
`architecture.md` §996–1018 sketches a generic ``Event`` base with a
``payload: dict`` slot. Story 1.3 overrides that design — see
``epics.md`` line 691 — because a ``dict`` payload is untyped at every
boundary, defeats mypy strict, and degenerates into ad-hoc negotiation at
every emission site. Each concrete event below carries **explicit typed
fields** instead; the bus routes by event class, not by dot-notation
string.

EventBus semantics (T1 contract, pinned)
----------------------------------------
- **In-process only.** Single asyncio event loop, no durable queue,
  no replay, no persistence. The audit log (Story 1.8) records *actions*,
  not events.
- **Exact-class routing.** ``emit(event)`` dispatches to handlers
  subscribed under ``type(event)`` — subclasses are NOT fanned out.
  Subscribe to each concrete class you want to observe.
- **Ordered sequential delivery.** Handlers fire in registration order,
  awaited one at a time. No ``asyncio.gather``, no ``create_task``.
- **Handler failure isolation for ``Exception``.** A handler that raises
  ``Exception`` is logged via ``logger.exception`` and other handlers
  continue. ``BaseException`` (``KeyboardInterrupt``, ``SystemExit``,
  ``asyncio.CancelledError``) propagates out of ``emit()`` intact.
- **Handlers are bus-level.** The API types handlers as
  ``Callable[[Event], Awaitable[None]]``. Handlers that only care about a
  concrete subclass narrow internally with ``isinstance`` (see docstring
  on `EventBus`).

Write-then-emit rule (architecture.md:1037)
-------------------------------------------
Events that describe durable facts (``SeedSaved``, ``SessionEnded``,
``MemoryForgotten``, ``ModeRestored``) are emitted **only after** Brain
confirms the write. Enforcement lives at each emitter's story; the
classes themselves are passive carriers.

Two-function clock pattern (timestamps)
---------------------------------------
There are two module-level timestamp helpers working as a pair:

- ``_utc_now_iso()`` — the **canonical clock function**. Single source of
  truth for "what time is it?". Tests monkeypatch THIS name via
  ``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)`` to get
  deterministic behavior across Stories 1.7 / 3.x / 4.x.
- ``_default_timestamp()`` — the **factory indirection**. Its body is
  ``return _utc_now_iso()``. This is what ``field(default_factory=...)``
  captures. Because the indirection does a name lookup of ``_utc_now_iso``
  in the module globals on every call, monkeypatched replacements take
  effect on every new event. Pointing ``default_factory`` directly at
  ``_utc_now_iso`` would freeze the reference at class-definition time
  and defeat the monkeypatch contract. Do not inline the indirection back.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from nova.core.types import CapabilityTier

logger = logging.getLogger("nova.core.events")


def _utc_now_iso() -> str:
    """Canonical clock function — single source of truth for timestamps.

    Returns the current UTC time as an ISO 8601 string with ``+00:00``
    suffix. Uses ``datetime.now(UTC)`` — NOT the deprecated
    ``datetime.utcnow()``, which returns a naive datetime. The ``+00:00``
    suffix is preserved by ``.isoformat()`` and regex-asserted by the
    test suite.

    This is the name tests monkeypatch for deterministic timestamps
    (``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)``). The
    dataclass ``default_factory`` points at ``_default_timestamp`` below,
    which calls back into this function via late-bound module-global
    lookup so monkeypatched replacements actually take effect.
    """
    return datetime.now(UTC).isoformat()


def _default_timestamp() -> str:
    """Factory indirection — preserves monkeypatchability of ``_utc_now_iso``.

    This is what ``field(default_factory=...)`` captures on every ``Event``
    subclass. Its body does a name lookup of ``_utc_now_iso`` in the
    module globals on every call, so tests that do
    ``monkeypatch.setattr("nova.core.events._utc_now_iso", ...)`` see
    their replacement take effect on every new event construction.

    If ``default_factory`` pointed at ``_utc_now_iso`` directly, the
    reference would be frozen at class-definition time (before the
    monkeypatch runs) and the deterministic-clock pattern would silently
    break. Do NOT inline this back into the factory — the indirection is
    load-bearing.
    """
    return _utc_now_iso()


@dataclass(frozen=True)
class Event:
    """Abstract base for every typed event in N.O.V.A.

    Carries exactly two fields shared by all events:
    - ``source``: system name (``"eyes"``, ``"nerve"``, ``"ritual"``,
      ``"hands"``, ``"brain"``). Each concrete subclass fixes it via
      ``field(default=..., init=False)`` so callers cannot spoof it.
    - ``timestamp``: ISO 8601 UTC string. The dataclass
      ``default_factory`` points at ``_default_timestamp`` (the factory
      indirection) which in turn calls ``_utc_now_iso`` (the canonical
      clock). See the module docstring for the two-function clock
      pattern and why the indirection is load-bearing. Declared
      ``kw_only=True`` — this is REQUIRED, not optional. Dataclass
      inheritance places the parent's fields first in ``__init__``, and
      a defaulted parent field cannot precede non-defaulted child
      fields. ``kw_only=True`` moves ``timestamp`` to a keyword-only
      slot and keeps every concrete subclass's constructor ordering-
      safe. Do NOT "simplify" this away.

    Concrete events are single-level subclasses of ``Event`` — no deeper
    hierarchies in T1. All use ``@dataclass(frozen=True)`` so that
    attempting to mutate a field raises ``dataclasses.FrozenInstanceError``.
    Frozen semantics only cover the outer object's attribute bindings;
    container-valued fields (``ModeRestored.apps_launched`` etc.) are
    typed as tuples so the containers themselves are immutable too.

    ``Event`` itself is **abstract** in contract even though Python /
    ``@dataclass`` do not express that natively. ``__post_init__`` raises
    ``TypeError`` when the runtime class IS ``Event`` so ``Event(source=...)``
    cannot silently bypass the "only typed concrete events on the bus"
    rule. Concrete subclasses call ``super().__post_init__()`` through the
    default dataclass plumbing and pass through unaffected.
    """

    source: str
    timestamp: str = field(default_factory=_default_timestamp, kw_only=True)

    def __post_init__(self) -> None:
        if type(self) is Event:
            raise TypeError(
                "Event is an abstract base — instantiate a concrete subclass "
                "(ContextChanged, TierChanged, SessionStarted, SessionEnded, "
                "SeedSaved, ModeRestored, AppLaunched, MemoryForgotten)."
            )


@dataclass(frozen=True)
class ContextChanged(Event):
    """Eyes emitted: the foreground window changed.

    When ``is_opaque`` is ``True`` (excluded app per ``exclusions.yaml``),
    Eyes sets ``app_name``, ``window_title``, and ``process_name`` all to
    ``None``. That invariant is enforced at the Eyes capture layer
    (Story 4.2), not here — the event class is a passive carrier.
    """

    source: str = field(default="eyes", init=False)
    app_name: str | None
    window_title: str | None
    process_name: str | None
    is_opaque: bool


@dataclass(frozen=True)
class TierChanged(Event):
    """Nerve emitted: the global capability tier transitioned.

    Fired once per transition by the tier state machine (Story 1.7). Skin
    renders a one-line notice on receive.
    """

    source: str = field(default="nerve", init=False)
    previous_tier: CapabilityTier
    new_tier: CapabilityTier
    reason: str


@dataclass(frozen=True)
class SessionStarted(Event):
    """Nerve emitted: a new session began.

    ``mode_name`` is ``None`` when no mode has been chosen yet — e.g., the
    first-run briefing State A before the setup wizard fires.
    """

    source: str = field(default="nerve", init=False)
    session_id: int
    mode_name: str | None


@dataclass(frozen=True)
class SessionEnded(Event):
    """Ritual emitted: the session's shutdown flow completed.

    ``seed_text`` is ``None`` if the user skipped the seed prompt.
    ``is_complete`` is ``False`` for crash-recovery / abnormal
    termination paths (Story 3.10). Durable-fact event — emitted only
    after Brain confirms the write (architecture.md:1037).
    """

    source: str = field(default="ritual", init=False)
    session_id: int
    seed_text: str | None
    is_complete: bool


@dataclass(frozen=True)
class SeedSaved(Event):
    """Ritual emitted: the tomorrow-seed was persisted to Brain.

    Durable-fact event — emitted only after Brain confirms the write
    (architecture.md:1037).
    """

    source: str = field(default="ritual", init=False)
    session_id: int
    seed_text: str


@dataclass(frozen=True)
class ModeRestored(Event):
    """Hands emitted: the mode-restore flow finished (possibly partial).

    ``apps_launched`` and ``apps_failed`` are ``tuple[str, ...]`` (not
    ``list[str]``) so the containers are genuinely immutable — the
    ``frozen=True`` decorator only freezes attribute bindings, not the
    referenced containers. Callers populate both tuples explicitly per
    the project-context "no mutable default values" rule; the write-
    then-emit contract means Hands knows which apps landed by the time
    this fires. Durable-fact event (architecture.md:1037).
    """

    source: str = field(default="hands", init=False)
    mode_name: str
    apps_launched: tuple[str, ...]
    apps_failed: tuple[str, ...]


@dataclass(frozen=True)
class AppLaunched(Event):
    """Hands emitted: a single app launch attempt completed.

    ``reason`` carries the failure reason when ``success`` is ``False``;
    ``None`` on success.
    """

    source: str = field(default="hands", init=False)
    app_name: str
    executable: str
    success: bool
    reason: str | None


@dataclass(frozen=True)
class MemoryForgotten(Event):
    """Brain emitted: a forget operation deleted N items (Story 5.2).

    ``target`` MUST be an opaque reference (e.g., ``"project 'opaque'"``)
    — never a raw project/app name. The project-context rule that bans
    sensitive content from exception messages also covers event payloads.
    Durable-fact event (architecture.md:1037).
    """

    source: str = field(default="brain", init=False)
    target: str
    items_deleted: int


class EventBus:
    """In-process async event bus with exact-class routing.

    Contract (locked by tests in ``tests/unit/core/test_events.py``):

    1. **Exact-class routing.** ``emit(event)`` looks up handlers under
       ``type(event)`` via a direct dict lookup. A handler subscribed for
       ``SessionStarted`` does NOT fire on ``SessionEnded``.

    2. **Ordered sequential delivery.** Handlers registered for an event
       class run in registration order; each is awaited before the next.

    3. **Handler failure isolation for ``Exception``.** A handler raising
       ``Exception`` is caught, logged via ``logger.exception`` with
       ``event_class`` and ``handler`` in ``extra``, and the next handler
       still runs. ``BaseException`` (``KeyboardInterrupt``,
       ``SystemExit``, ``asyncio.CancelledError``) propagates through.

    4. **No raw-string API.** ``subscribe`` and ``emit`` are typed at the
       bus level as ``type[Event]`` / ``Event``. There is no string-name
       dispatch anywhere.

    Handlers are typed at the bus level as
    ``Callable[[Event], Awaitable[None]]``. A handler that only cares
    about a concrete subclass narrows internally::

        async def on_tier(event: Event) -> None:
            if not isinstance(event, TierChanged):
                return
            # ... handle TierChanged-specific fields here

    This avoids per-call-site ``typing.cast`` noise and keeps the bus's
    handler dict strict-mode-clean.

    T1 has no ``unsubscribe`` — handlers live for the process lifetime.
    T1 has no thread-safety apparatus — the whole app is single-asyncio
    event-loop by architecture.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Callable[[Event], Awaitable[None]]]] = defaultdict(
            list
        )

    async def subscribe(
        self,
        event_class: type[Event],
        handler: Callable[[Event], Awaitable[None]],
    ) -> None:
        """Register ``handler`` to fire on every ``emit`` of ``event_class``.

        ``async`` for API symmetry with ``emit`` and future-proofing
        (Story 4.5 may coordinate subscription with Brain's memory loader).
        No dedup: subscribing the same ``(event_class, handler)`` pair
        twice will fire the handler twice. No unsubscribe in T1.
        """
        self._handlers[event_class].append(handler)

    async def emit(self, event: Event) -> None:
        """Dispatch ``event`` to every handler registered for ``type(event)``.

        Handlers run sequentially in registration order. ``Exception``
        raised by a handler is logged and the loop continues.
        ``BaseException`` propagates out. Iterating a snapshot
        (``list(...)``) guards against handlers that mutate
        ``self._handlers`` mid-emit.
        """
        handlers = list(self._handlers.get(type(event), ()))
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "event handler failed",
                    extra={
                        "event_class": type(event).__name__,
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                    },
                )
