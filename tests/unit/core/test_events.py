"""Story 1.3 contract tests for `nova.core.events`.

Covers EventBus semantics (routing, ordering, failure isolation) and
typed event immutability / construction rules.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from nova.core.events import (
    AppLaunched,
    ContextChanged,
    Event,
    EventBus,
    MemoryForgotten,
    ModeRestored,
    SeedSaved,
    SessionEnded,
    SessionStarted,
    TierChanged,
)
from nova.core.types import CapabilityTier

# ---------------------------------------------------------------------------
# Factory helper — centralized valid construction for every concrete event.
# ---------------------------------------------------------------------------

CONCRETE_EVENT_CLASSES: tuple[type[Event], ...] = (
    ContextChanged,
    TierChanged,
    SessionStarted,
    SessionEnded,
    SeedSaved,
    ModeRestored,
    AppLaunched,
    MemoryForgotten,
)


def _make_event(event_class: type[Event]) -> Event:
    """Construct a valid instance of each of the eight concrete event classes.

    Centralized so adding a new field in a future story means updating a
    single location rather than every parametrized test.
    """
    if event_class is ContextChanged:
        return ContextChanged(
            app_name="chrome.exe",
            window_title="Gmail",
            process_name="chrome.exe",
            is_opaque=False,
        )
    if event_class is TierChanged:
        return TierChanged(
            previous_tier=CapabilityTier.FULL,
            new_tier=CapabilityTier.DEGRADED,
            reason="claude api timeout",
        )
    if event_class is SessionStarted:
        return SessionStarted(session_id=1, mode_name="coding")
    if event_class is SessionEnded:
        return SessionEnded(session_id=1, seed_text="pick up bug fix", is_complete=True)
    if event_class is SeedSaved:
        return SeedSaved(session_id=1, seed_text="pick up bug fix")
    if event_class is ModeRestored:
        return ModeRestored(
            mode_name="coding",
            apps_launched=("chrome.exe", "code.exe"),
            apps_failed=(),
        )
    if event_class is AppLaunched:
        return AppLaunched(
            app_name="chrome.exe",
            executable=r"C:\Program Files\Google\Chrome\chrome.exe",
            success=True,
            reason=None,
        )
    if event_class is MemoryForgotten:
        return MemoryForgotten(target="project 'opaque'", items_deleted=3)
    raise AssertionError(f"no factory for {event_class!r}")


EXPECTED_SOURCES: dict[type[Event], str] = {
    ContextChanged: "eyes",
    TierChanged: "nerve",
    SessionStarted: "nerve",
    SessionEnded: "ritual",
    SeedSaved: "ritual",
    ModeRestored: "hands",
    AppLaunched: "hands",
    MemoryForgotten: "brain",
}


EXPECTED_FIELD_SCHEMA: dict[type[Event], list[tuple[str, str]]] = {
    ContextChanged: [
        ("source", "str"),
        ("timestamp", "str"),
        ("app_name", "str | None"),
        ("window_title", "str | None"),
        ("process_name", "str | None"),
        ("is_opaque", "bool"),
    ],
    TierChanged: [
        ("source", "str"),
        ("timestamp", "str"),
        ("previous_tier", "CapabilityTier"),
        ("new_tier", "CapabilityTier"),
        ("reason", "str"),
    ],
    SessionStarted: [
        ("source", "str"),
        ("timestamp", "str"),
        ("session_id", "int"),
        ("mode_name", "str | None"),
    ],
    SessionEnded: [
        ("source", "str"),
        ("timestamp", "str"),
        ("session_id", "int"),
        ("seed_text", "str | None"),
        ("is_complete", "bool"),
    ],
    SeedSaved: [
        ("source", "str"),
        ("timestamp", "str"),
        ("session_id", "int"),
        ("seed_text", "str"),
    ],
    ModeRestored: [
        ("source", "str"),
        ("timestamp", "str"),
        ("mode_name", "str"),
        ("apps_launched", "tuple[str, ...]"),
        ("apps_failed", "tuple[str, ...]"),
    ],
    AppLaunched: [
        ("source", "str"),
        ("timestamp", "str"),
        ("app_name", "str"),
        ("executable", "str"),
        ("success", "bool"),
        ("reason", "str | None"),
    ],
    MemoryForgotten: [
        ("source", "str"),
        ("timestamp", "str"),
        ("target", "str"),
        ("items_deleted", "int"),
    ],
}


# ---------------------------------------------------------------------------
# EventBus lifecycle — subscribe + emit + identity check.
# ---------------------------------------------------------------------------


async def test_emit_invokes_subscribed_handler_with_event_identity() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe(TierChanged, handler)
    event = TierChanged(
        previous_tier=CapabilityTier.FULL,
        new_tier=CapabilityTier.DEGRADED,
        reason="test",
    )
    await bus.emit(event)

    assert len(received) == 1
    assert received[0] is event


# ---------------------------------------------------------------------------
# Ordered delivery — handlers fire in registration order.
# ---------------------------------------------------------------------------


async def test_handlers_fire_in_registration_order() -> None:
    bus = EventBus()
    order: list[str] = []

    async def h1(_: Event) -> None:
        order.append("h1")

    async def h2(_: Event) -> None:
        order.append("h2")

    async def h3(_: Event) -> None:
        order.append("h3")

    await bus.subscribe(TierChanged, h1)
    await bus.subscribe(TierChanged, h2)
    await bus.subscribe(TierChanged, h3)

    await bus.emit(_make_event(TierChanged))

    assert order == ["h1", "h2", "h3"]


# ---------------------------------------------------------------------------
# Handler failure isolation — Exception caught, logged, other handlers run.
# ---------------------------------------------------------------------------


async def test_handler_exception_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="nova.core.events")
    bus = EventBus()
    order: list[str] = []

    async def h1(_: Event) -> None:
        order.append("h1")

    async def h2(_: Event) -> None:
        order.append("h2-pre")
        raise RuntimeError("boom")

    async def h3(_: Event) -> None:
        order.append("h3")

    await bus.subscribe(TierChanged, h1)
    await bus.subscribe(TierChanged, h2)
    await bus.subscribe(TierChanged, h3)

    await bus.emit(_make_event(TierChanged))

    assert order == ["h1", "h2-pre", "h3"]

    error_records = [
        r for r in caplog.records if r.levelno == logging.ERROR and r.name == "nova.core.events"
    ]
    # Exactly one ERROR record per handler failure — locks against a future
    # refactor that double-logs (e.g., at both a wrapper and an inner site).
    assert len(error_records) == 1, (
        f"expected exactly one ERROR record from nova.core.events, got {len(error_records)}"
    )
    assert error_records[0].exc_info is not None, (
        "expected exc_info on the handler-failure log record"
    )


# ---------------------------------------------------------------------------
# BaseException propagates — CancelledError MUST NOT be swallowed.
# ---------------------------------------------------------------------------


async def test_base_exception_propagates_through_emit() -> None:
    bus = EventBus()

    async def cancelling_handler(_: Event) -> None:
        raise asyncio.CancelledError

    await bus.subscribe(TierChanged, cancelling_handler)

    with pytest.raises(asyncio.CancelledError):
        await bus.emit(_make_event(TierChanged))


# ---------------------------------------------------------------------------
# Exact-class routing — subscribing to SessionStarted does NOT fire on SessionEnded.
# ---------------------------------------------------------------------------


async def test_exact_class_routing_does_not_fire_siblings() -> None:
    bus = EventBus()
    fired: list[Event] = []

    async def handler(event: Event) -> None:
        fired.append(event)

    await bus.subscribe(SessionStarted, handler)
    await bus.emit(_make_event(SessionEnded))

    assert fired == []


# ---------------------------------------------------------------------------
# Multiple handlers same class + duplicate registration fires twice.
# ---------------------------------------------------------------------------


async def test_multiple_handlers_for_same_class_run_in_order() -> None:
    bus = EventBus()
    order: list[str] = []

    async def a(_: Event) -> None:
        order.append("a")

    async def b(_: Event) -> None:
        order.append("b")

    await bus.subscribe(AppLaunched, a)
    await bus.subscribe(AppLaunched, b)

    await bus.emit(_make_event(AppLaunched))

    assert order == ["a", "b"]


async def test_duplicate_registration_fires_handler_twice() -> None:
    bus = EventBus()
    count = 0

    async def handler(_: Event) -> None:
        nonlocal count
        count += 1

    await bus.subscribe(AppLaunched, handler)
    await bus.subscribe(AppLaunched, handler)

    await bus.emit(_make_event(AppLaunched))

    assert count == 2


# ---------------------------------------------------------------------------
# Event immutability — every field on every concrete class is frozen.
# ---------------------------------------------------------------------------


def _immutability_params() -> list[tuple[type[Event], str, object]]:
    """Build (event_class, field_name, replacement_value) tuples for every field."""
    replacements: dict[str, object] = {
        "source": "other",
        "timestamp": "2030-01-01T00:00:00+00:00",
        "app_name": "other.exe",
        "window_title": "other",
        "process_name": "other.exe",
        "is_opaque": True,
        "previous_tier": CapabilityTier.OFFLINE,
        "new_tier": CapabilityTier.OFFLINE,
        "reason": "other",
        "session_id": 999,
        "mode_name": "other",
        "seed_text": "other",
        "is_complete": False,
        "apps_launched": ("other.exe",),
        "apps_failed": ("other.exe",),
        "executable": r"C:\other.exe",
        "success": False,
        "target": "project 'other'",
        "items_deleted": 999,
    }
    params: list[tuple[type[Event], str, object]] = []
    for cls in CONCRETE_EVENT_CLASSES:
        for f in fields(cls):
            params.append((cls, f.name, replacements[f.name]))
    return params


@pytest.mark.parametrize(("event_class", "field_name", "replacement"), _immutability_params())
def test_event_fields_are_frozen(
    event_class: type[Event], field_name: str, replacement: object
) -> None:
    event = _make_event(event_class)
    with pytest.raises(FrozenInstanceError):
        setattr(event, field_name, replacement)


# ---------------------------------------------------------------------------
# `source` is locked via init=False — passing source= raises TypeError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_class", CONCRETE_EVENT_CLASSES)
def test_source_cannot_be_overridden_at_construction(event_class: type[Event]) -> None:
    kwargs: dict[str, object] = {"source": "other"}
    if event_class is ContextChanged:
        kwargs.update(app_name=None, window_title=None, process_name=None, is_opaque=True)
    elif event_class is TierChanged:
        kwargs.update(
            previous_tier=CapabilityTier.FULL,
            new_tier=CapabilityTier.FULL,
            reason="x",
        )
    elif event_class is SessionStarted:
        kwargs.update(session_id=1, mode_name=None)
    elif event_class is SessionEnded:
        kwargs.update(session_id=1, seed_text=None, is_complete=True)
    elif event_class is SeedSaved:
        kwargs.update(session_id=1, seed_text="x")
    elif event_class is ModeRestored:
        kwargs.update(mode_name="x", apps_launched=(), apps_failed=())
    elif event_class is AppLaunched:
        kwargs.update(app_name="x", executable="x", success=True, reason=None)
    elif event_class is MemoryForgotten:
        kwargs.update(target="x", items_deleted=0)

    with pytest.raises(TypeError):
        event_class(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# `source` has the expected per-class value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_class", "expected_source"),
    [(cls, src) for cls, src in EXPECTED_SOURCES.items()],
)
def test_source_has_expected_value(event_class: type[Event], expected_source: str) -> None:
    event = _make_event(event_class)
    assert event.source == expected_source


# ---------------------------------------------------------------------------
# Timestamp auto-populates and round-trips as ISO 8601 UTC.
# ---------------------------------------------------------------------------

ISO_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


@pytest.mark.parametrize("event_class", CONCRETE_EVENT_CLASSES)
def test_timestamp_auto_populates_as_iso_utc(event_class: type[Event]) -> None:
    event = _make_event(event_class)
    assert ISO_UTC_PATTERN.match(event.timestamp), f"unexpected timestamp: {event.timestamp!r}"
    parsed = datetime.fromisoformat(event.timestamp)
    assert parsed.tzinfo is UTC


# ---------------------------------------------------------------------------
# Timestamp is overridable via keyword — proves default_factory, not init=False.
# ---------------------------------------------------------------------------


def test_timestamp_can_be_overridden_as_keyword() -> None:
    fixed = "2026-01-01T00:00:00+00:00"
    event = AppLaunched(
        app_name="x",
        executable="x",
        success=True,
        reason=None,
        timestamp=fixed,
    )
    assert event.timestamp == fixed


# ---------------------------------------------------------------------------
# Timestamp is keyword-only — positional form raises TypeError.
# Parametrized across all eight concrete classes so a future subclass that
# drops `kw_only=True` on `timestamp` regresses immediately.
# ---------------------------------------------------------------------------


# Positional args for each concrete class — same values as `_make_event`
# but laid out as a tuple so we can append a would-be positional
# `timestamp` and assert TypeError.
POSITIONAL_ARGS: dict[type[Event], tuple[object, ...]] = {
    ContextChanged: ("chrome.exe", "Gmail", "chrome.exe", False),
    TierChanged: (CapabilityTier.FULL, CapabilityTier.DEGRADED, "timeout"),
    SessionStarted: (1, "coding"),
    SessionEnded: (1, "seed", True),
    SeedSaved: (1, "seed"),
    ModeRestored: ("coding", ("chrome.exe",), ()),
    AppLaunched: ("chrome.exe", r"C:\chrome.exe", True, None),
    MemoryForgotten: ("project 'opaque'", 3),
}


@pytest.mark.parametrize("event_class", CONCRETE_EVENT_CLASSES)
def test_timestamp_is_keyword_only(event_class: type[Event]) -> None:
    positional = POSITIONAL_ARGS[event_class] + ("2026-01-01T00:00:00+00:00",)
    with pytest.raises(TypeError):
        event_class(*positional)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Deterministic clock via monkeypatch — lock the pattern for Story 1.7 / 3.x.
# ---------------------------------------------------------------------------


def test_monkeypatched_utc_now_iso_gives_deterministic_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = "2026-04-14T12:00:00+00:00"
    monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: fixed)

    events = [
        _make_event(ContextChanged),
        _make_event(TierChanged),
        _make_event(SessionStarted),
        _make_event(SeedSaved),
        _make_event(MemoryForgotten),
    ]
    for event in events:
        assert event.timestamp == fixed


# ---------------------------------------------------------------------------
# Per-event field schema — catches renames, reorders, type drift.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_class", "expected_fields"),
    [(cls, EXPECTED_FIELD_SCHEMA[cls]) for cls in CONCRETE_EVENT_CLASSES],
)
def test_event_field_schema(
    event_class: type[Event], expected_fields: list[tuple[str, str]]
) -> None:
    actual = [(f.name, f.type) for f in fields(event_class)]
    assert actual == expected_fields


# ---------------------------------------------------------------------------
# ContextChanged opaque contract — NOT enforced at event layer in T1.
# ---------------------------------------------------------------------------
#
# epics.md:693 states that `is_opaque=True` implies the three string fields
# are None. Enforcement happens at the Eyes capture layer (Story 4.2), not
# here — the event class is a passive carrier. This test documents the
# non-enforcement so a future contributor doesn't add runtime validation and
# accidentally break the Eyes-owned contract.


def test_context_changed_opaque_invariant_is_not_runtime_enforced() -> None:
    # Deliberately violates the opaque invariant — must construct without error.
    event = ContextChanged(
        app_name="chrome.exe",
        window_title=None,
        process_name=None,
        is_opaque=True,
    )
    assert event.is_opaque is True
    assert event.app_name == "chrome.exe"


# ---------------------------------------------------------------------------
# Event base class is abstract — direct instantiation raises TypeError.
# The @dataclass machinery makes `Event` technically instantiable, but the
# bus contract is "only typed concrete events." __post_init__ enforces it.
# ---------------------------------------------------------------------------


def test_event_base_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract base"):
        Event(source="anywhere")


def test_concrete_event_subclasses_are_unaffected_by_abstract_guard() -> None:
    # Every concrete subclass must still construct cleanly — proves the
    # `type(self) is Event` check in __post_init__ does not fire for subclasses.
    for cls in CONCRETE_EVENT_CLASSES:
        event = _make_event(cls)
        assert isinstance(event, cls)
        assert isinstance(event, Event)


# ---------------------------------------------------------------------------
# Emit with zero subscribers is a no-op — pins the `.get(..., ())` contract.
# A future refactor that used `self._handlers[type(event)]` (defaultdict
# subscript) would pollute `_handlers` with an empty list on every emit and
# silently grow memory.
# ---------------------------------------------------------------------------


async def test_emit_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    await bus.emit(_make_event(TierChanged))
    # `_handlers` must NOT contain a key for `TierChanged` after an un-subscribed emit.
    assert TierChanged not in bus._handlers
    # And the container stays empty overall.
    assert len(bus._handlers) == 0


# ---------------------------------------------------------------------------
# Subscribe-during-emit: snapshot contract.
# A handler that subscribes a NEW handler for the same event class during
# its own execution must NOT trigger "dict changed size during iteration"
# and the new handler must NOT fire during the current emit cycle (the
# snapshot was taken before it was registered). It DOES fire on the next
# emit. This locks the `list(...)` snapshot defense against a future
# refactor that removes it.
# ---------------------------------------------------------------------------


async def test_subscribe_during_emit_does_not_fire_new_handler_this_cycle() -> None:
    bus = EventBus()
    order: list[str] = []

    async def late_handler(_: Event) -> None:
        order.append("late")

    async def self_subscribing(event: Event) -> None:
        order.append("self")
        await bus.subscribe(type(event), late_handler)

    await bus.subscribe(TierChanged, self_subscribing)

    # First emit — only `self_subscribing` ran; `late_handler` was not in the snapshot.
    await bus.emit(_make_event(TierChanged))
    assert order == ["self"]

    # Second emit — both handlers run in registration order.
    await bus.emit(_make_event(TierChanged))
    assert order == ["self", "self", "late"]
