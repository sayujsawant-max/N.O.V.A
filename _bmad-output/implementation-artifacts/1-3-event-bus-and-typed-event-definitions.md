# Story 1.3: Event Bus & Typed Event Definitions

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer wiring inter-system communication,
I want an in-process async event bus with all T1 event types defined as typed frozen dataclasses with explicit fields,
so that systems communicate through events via Nerve without raw string types, generic payload dicts, or direct cross-system calls.

## Acceptance Criteria

1. **`src/nova/core/events.py` defines the `EventBus` class** with async subscribe/emit semantics that route by event class, not by string name.
   - Signature: `async def subscribe(self, event_class: type[Event], handler: Callable[[Event], Awaitable[None]]) -> None`. The handler is stored under its event class key (no string lookup anywhere).
   - **Handler typing is bus-level `Event`, not per-event.** The handler parameter type is fixed at `Callable[[Event], Awaitable[None]]` — handlers that want to act on fields specific to `ContextChanged` (or any concrete subclass) should narrow internally with `isinstance(event, ContextChanged)` and do an early return otherwise. This keeps the bus's `dict[type[Event], list[Callable[[Event], Awaitable[None]]]]` strict-mode-clean and avoids per-call-site `typing.cast` noise. Document this convention in the `EventBus` class docstring with a short example. Downstream stories (1.7, 3.x, 4.x) follow this pattern; do NOT introduce a generic `subscribe[E: Event]` parameterization in T1 — PEP 695 TypeVar generics on async methods interact awkwardly with mypy's handler-variance rules, and the simpler narrow-inside-handler pattern is idiomatic Python and fully strict-mode-clean.
   - Signature: `async def emit(self, event: Event) -> None`. Routing looks up handlers by `type(event)` — a handler registered for `ContextChanged` only fires for `ContextChanged` instances, NEVER on subclasses unless explicitly subscribed to the subclass. The contract is "exact class match." Document this in the class docstring.
   - **Ordered sequential delivery within current process.** Handlers registered for an event class run in registration order, sequentially (`await` each before moving to the next). No `asyncio.gather`, no `create_task` fan-out. T1 explicitly does NOT do concurrent fan-out — this is pinned behavior.
   - **In-process, in-memory only.** No durable queue, no replay, no cross-process delivery, no persistence of events. The emitter awaits `emit()`; handler failures classified as `Exception` do NOT propagate out; there is no background scheduling, no `asyncio.create_task` spawn, and no post-process persistence. Do not describe this as "fire-and-forget" — the emitter IS awaiting; what it is guaranteed is best-effort delivery and non-propagating `Exception`-level handler failures.
   - **Handler failure isolation.** If a handler raises, EventBus catches the exception, logs it via `logging.getLogger("nova.core.events").exception(...)` with `extra={"event_class": type(event).__name__, "handler": handler.__qualname__}`, and continues to the next handler. A single handler crash must NOT block other handlers for the same event and must NOT propagate out of `emit()`. The bus swallows `Exception` only — `BaseException` (e.g., `KeyboardInterrupt`, `SystemExit`, `asyncio.CancelledError`) MUST propagate. Log at ERROR level with full traceback (`logger.exception` does this).
   - **No raw-string dispatch.** The public API never accepts a `str` event name. There is no `emit(event_name: str, payload: dict)` overload. Violating this is a test failure.

2. **`src/nova/core/events.py` defines an `Event` base class** and eight T1 concrete event types as `@dataclass(frozen=True)` subclasses with **explicit typed fields** (NOT a generic `payload: dict`):
   - `Event` is the abstract base — a `@dataclass(frozen=True)` with exactly two fields: `source: str` (which system emitted it) and `timestamp: str` (ISO 8601 UTC string, e.g., `"2026-04-14T18:22:05.123456+00:00"`). Both fields must be present on every concrete event via dataclass inheritance. `timestamp` is declared **`field(default_factory=_default_timestamp, kw_only=True)`** — `kw_only=True` is **required** (not optional) because dataclass inheritance places the parent's fields first in `__init__`, and a defaulted field cannot precede non-defaulted subclass fields without `kw_only`. Without it, every concrete subclass (`ContextChanged(app_name, ...)` etc.) would fail at class-definition time with `TypeError: non-default argument 'app_name' follows default argument`. `kw_only=True` moves `timestamp` to a keyword-only slot, yielding `__init__(self, app_name, window_title, process_name, is_opaque, *, timestamp=<factory>)` — ordering-safe and preserves the "tests can override" requirement. `source` is defaulted per subclass via `field(default="<system>", init=False)` — callers never pass `source`; it is a class constant baked into each concrete type.
   - **Two-function clock pattern** — `_utc_now_iso() -> str` is the canonical clock function and the single source of truth for "what time is it?": `return datetime.now(UTC).isoformat()`. **Not `datetime.utcnow()`** — that returns a naive datetime and is deprecated in 3.12. `_default_timestamp() -> str` is a thin indirection whose body is `return _utc_now_iso()`. `field(default_factory=_default_timestamp)` captures `_default_timestamp` at class-definition time, but the function's body does a name lookup of `_utc_now_iso` in the module globals on every call — so tests can `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` and the replacement takes effect on every new event. If `default_factory` pointed at `_utc_now_iso` directly, the reference would be frozen at class-definition time and monkeypatch would have no effect. Docstring on `_default_timestamp` must state "do not inline this back into the factory" so a future contributor doesn't undo the indirection.
   - **ContextChanged** — `source="eyes"`. Fields: `app_name: str | None`, `window_title: str | None`, `process_name: str | None`, `is_opaque: bool`. When `is_opaque=True` (excluded app), the three string fields MUST be `None`; document this invariant in the class docstring. No runtime validation in T1 — the invariant is a contract for Eyes to uphold in Story 4.2.
   - **TierChanged** — `source="nerve"`. Fields: `previous_tier: CapabilityTier`, `new_tier: CapabilityTier`, `reason: str`. `CapabilityTier` is imported from `nova.core.types` (Story 1.2).
   - **SessionStarted** — `source="nerve"`. Fields: `session_id: int`, `mode_name: str | None` (None when no mode has been chosen yet — first-run briefing State A).
   - **SessionEnded** — `source="ritual"`. Fields: `session_id: int`, `seed_text: str | None` (None if the user skipped the seed prompt), `is_complete: bool` (False for crash-recovery / abnormal termination paths wired in Story 3.10).
   - **SeedSaved** — `source="ritual"`. Fields: `session_id: int`, `seed_text: str`. Emitted after Brain confirms the write (write-then-emit rule — architecture.md:1037).
   - **ModeRestored** — `source="hands"`. Fields: `mode_name: str`, `apps_launched: tuple[str, ...]`, `apps_failed: tuple[str, ...]`. **Use `tuple[str, ...]`, NOT `list[str]`**, so the containers are genuinely immutable — `@dataclass(frozen=True)` only freezes the outer object's attribute bindings; a nested `list` remains mutable (`event.apps_launched.append("...")` would silently succeed). Tuples close that hole. Callers construct with tuple literals: `ModeRestored(mode_name="coding", apps_launched=("chrome.exe", "code.exe"), apps_failed=())`. Do NOT add `field(default_factory=tuple)` — per project-context "no mutable default values" and the write-then-emit contract, the caller must always pass both tuples explicitly (Story 3.6's Hands adapter owns the population logic).
   - **AppLaunched** — `source="hands"`. Fields: `app_name: str`, `executable: str`, `success: bool`, `reason: str | None` (the failure reason when `success=False`; None on success).
   - **MemoryForgotten** — `source="brain"`. Fields: `target: str` (the opaque forget target — e.g., `"project 'opaque'"`), `items_deleted: int`. Per project-context "No sensitive content in exception messages" (extended here to events): `target` MUST be an opaque reference, not a raw project/app name. Document this in the class docstring.

3. **All events are immutable and inherit from `Event`:**
   - Every concrete event class MUST be decorated with `@dataclass(frozen=True)`. Attempting to mutate a field (`event.app_name = "other"`) raises `dataclasses.FrozenInstanceError`. Locked by test.
   - Every concrete class MUST inherit directly from `Event` (single-level inheritance only in T1). No deeper hierarchies.
   - **No generic base with `payload: dict`**. Architecture.md §996–1018 shows a `payload: dict` design; **this story overrides that** to explicit typed fields per AC #2. Document the divergence in the `Event` class docstring, cite Story 1.3 AC #2 and epics.md line 691 as the owner of the divergence.
   - **`source` is `init=False` with a per-class default.** Callers construct `ContextChanged(app_name=..., window_title=..., process_name=..., is_opaque=False)` — they NEVER pass `source`. This prevents accidental source-spoofing from other systems. Locked by test: `ContextChanged(source="nerve", ...)` must raise `TypeError` (unexpected keyword argument).
   - **`timestamp` has `default_factory` + `kw_only=True`, not `init=False`.** Callers usually don't pass it, but tests need to be able to pass an explicit timestamp for determinism. So `timestamp: str = field(default_factory=_default_timestamp, kw_only=True)` (NOT `init=False`). The factory target is the `_default_timestamp` indirection (which calls `_utc_now_iso` with late binding) — see the two-function clock pattern above. `kw_only=True` is both an ergonomic and an ordering requirement — see the note in the `Event` base bullet above. Locked by test: passing an explicit `timestamp="2026-01-01T00:00:00+00:00"` as a keyword argument must succeed; passing it positionally must raise `TypeError`.

4. **`EventBus` state and concurrency contract:**
   - Internal state: `self._handlers: dict[type[Event], list[Callable[[Event], Awaitable[None]]]]`. A `defaultdict(list)` is acceptable; document the choice.
   - Subscription is append-only in T1 — there is no `unsubscribe`. Story 1.10 (composition root) wires handlers at boot and they live for the process lifetime. If a future story needs `unsubscribe`, it adds the method in that story, not here. (Scope guard: do NOT pre-emptively implement `unsubscribe`.)
   - `subscribe` is `async` for API symmetry with `emit` even though T1's implementation is fully synchronous under the hood (just append to a list). The `async` keyword is future-proofing for Story 4.5+ when subscribe may need to coordinate with Brain's memory loader; making it sync now would force a breaking change later. **Do not add a sync alias** — the `async` signature is the contract.
   - `emit` awaits each handler sequentially within a single `asyncio` task. No `asyncio.create_task`, no `asyncio.gather`. Order is deterministic: for a given event class, handlers fire in registration order.
   - Multiple handlers for the same event class are allowed (list append). Duplicate registration of the same `(event_class, handler)` pair is ALSO allowed without dedup — the handler fires twice. Pinning this behavior explicitly in a test prevents later contributors from "helpfully" adding dedup logic that would break Story 4.5's deliberate re-registration patterns.
   - **Thread safety is NOT a concern.** The whole app is single-asyncio-event-loop by architecture (architecture.md line 214). No `threading.Lock`, no `asyncio.Lock` — unnecessary and would mask concurrency bugs if the single-loop invariant ever breaks.

5. **Imports in `core/events.py` are stdlib only + `nova.core.types`:**
   - **Actual import list (exact, nothing extra):** `__future__.annotations`, `collections.defaultdict`, `collections.abc.Awaitable`, `collections.abc.Callable`, `dataclasses.dataclass`, `dataclasses.field`, `datetime.UTC`, `datetime.datetime`, `logging`, and `nova.core.types.CapabilityTier`. Nothing else. Any of the following would be flagged by ruff and/or mypy:
     - `import asyncio` — no asyncio symbol is actually used in production code; `F401`.
     - `from typing import Callable | Awaitable` — superseded by `collections.abc`; `UP035`.
     - `from dataclasses import FrozenInstanceError` — only tests reference it; `F401` in production.
   - First-party allowlist: `from nova.core.types import CapabilityTier` — this is the ONLY cross-module import. `core/events.py` must NOT import from `nova.systems.*`, `nova.adapters.*`, `nova.ports.*`, or `nova.core.exceptions` (event bus is infrastructure, not a domain-exception raiser — handler failures are logged, not converted to `NovaError`).
   - Forbidden (same as Story 1.2 AC #4): any adapter module (`sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32api`, `win32gui`, `win32com`, `win32con`, `rich`, `yaml`). AST-enforced.

6. **`core/__init__.py` re-exports the public names:**
   - Add `from nova.core.events import (...)` listing `EventBus`, `Event`, and all eight concrete event classes (alphabetized).
   - Extend `__all__: list[str]` with the new names, alphabetized for diff stability.
   - Preserve the existing Story 1.2 re-exports and the one-line module docstring.
   - Pattern carry-forward from Story 1.2: absolute imports (`from nova.core.events import ...`), never relative; `__all__: list[str]` annotation for mypy strict.

7. **Unit tests verify the event contract** in `tests/unit/core/test_events.py`:
   - **EventBus lifecycle** — one test subscribes a handler, emits an event, asserts the handler ran exactly once with the emitted event instance (identity check: `received is event`).
   - **Ordered delivery** — register three handlers (H1, H2, H3) in order on `TierChanged`. Emit a single event. Assert the recorded call order is exactly `[H1, H2, H3]`. Use a shared list and append to it inside each handler.
   - **Handler failure isolation** — register three handlers where H2 raises `RuntimeError("boom")`. Assert H1 and H3 still ran, `emit()` did NOT raise, and `caplog` captured the H2 failure at ERROR level with a traceback. Use `caplog.set_level(logging.ERROR, logger="nova.core.events")` to scope the capture.
   - **`BaseException` propagates** — register a handler that raises `asyncio.CancelledError` (a `BaseException`). Assert `emit()` re-raises it (does NOT swallow). This is a narrow but critical contract — cancellation must not be eaten by the event bus.
   - **Exact-class routing, no subclass fan-out** — register a handler for `SessionStarted`. Emit a `SessionEnded`. Assert the `SessionStarted` handler did NOT fire. (Both subclass `Event`, so a naive `isinstance` router would fire the handler on both — this test proves we use exact-type lookup.)
   - **Multiple handlers for the same event class** — register two handlers for `AppLaunched`. Emit one event. Assert both ran in registration order.
   - **Duplicate `(event_class, handler)` registration fires twice** — register the same handler twice. Emit once. Assert the handler ran exactly twice. Pins the "no dedup" contract.
   - **Event immutability** — for each of the eight concrete event classes, construct an instance, attempt to assign to each field (`with pytest.raises(dataclasses.FrozenInstanceError)`). Parametrize over `(event_class, field_name, replacement_value)` tuples.
   - **`source` is locked** — for each concrete event class, assert that passing `source="other"` to the constructor raises `TypeError` (unexpected keyword argument, since `source` is `init=False`). Parametrize over the eight classes.
   - **`source` has the expected value** — for each concrete event class, construct an instance (using per-class valid fields via a small factory helper), assert `event.source == <expected>`. Parametrize over `(event_class, expected_source)` tuples from AC #2.
   - **`timestamp` auto-populates** — construct an event without passing `timestamp`; assert the value is a non-empty ISO 8601 UTC string (regex match: `r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$"`). Parse via `datetime.fromisoformat(event.timestamp)` and assert `.tzinfo is UTC`.
   - **`timestamp` is overridable via keyword** — construct an event with `timestamp="2026-01-01T00:00:00+00:00"` passed as a kwarg; assert the value round-trips exactly. Proves `default_factory`, not `init=False`.
   - **`timestamp` is keyword-only** — attempt to pass `timestamp` positionally after the concrete event's required fields (e.g., `AppLaunched("Chrome", "chrome.exe", True, None, "2026-01-01T00:00:00+00:00")`); assert `TypeError` — pins the `kw_only=True` contract established in AC #2. Without this test, someone removing `kw_only=True` to "simplify" could break the inheritance-ordering invariant silently for a future event class and not notice until a new defaulted-field subclass is added.
   - **Deterministic clock via monkeypatch** — monkeypatch `nova.core.events._utc_now_iso` to return a fixed string; construct five events; assert all five carry the fixed timestamp. This is the pattern Story 1.7 (tier transitions) and Story 3.x (session lifecycle) will copy for deterministic testing.
   - **Per-event field types** — for each of the eight events, construct with AC-specified fields (valid types) and assert the instance has the expected attribute names and types via `dataclasses.fields(event_class)`. This is the "field schema" guard — renaming or reordering a field without updating this test fails immediately. Parametrize over expected `[(field_name, field_type)]` lists derived from AC #2. For `ModeRestored`, assert the `apps_launched` / `apps_failed` field types resolve to `tuple[str, ...]` (NOT `list[str]`) — pins the AC #2 immutability decision.
   - **`ContextChanged` opaque contract is a documented convention, not enforced** — add a test asserting you CAN construct `ContextChanged(app_name="Chrome", window_title=None, process_name=None, is_opaque=True)` without error (documents: no runtime validation in T1; Eyes enforces at Story 4.2). Comment cites epics.md line 693.

8. **Unit tests verify the AST-level adapter-isolation guardrail extends to `core/events.py`** in `tests/unit/core/test_core_isolation.py`:
   - Extend the existing parametrize lists to include `nova.core.events` as a third module under the **forbidden-imports** test (`test_no_forbidden_imports`), the **dynamic-imports** test (`test_no_dynamic_imports_of_forbidden_modules`), and the **relative-imports** test (`test_no_relative_imports`).
   - Do NOT add `nova.core.events` to `test_imports_within_allowlist` — that test's allowlist is `{"enum", "__future__"}` and `events.py` legitimately imports from `dataclasses`, `datetime`, `logging`, `collections.abc`, and `nova.core.types`. Adding events to that test would force a weakening of the `core/exceptions.py` and `core/types.py` allowlist. Instead, add a new test `test_events_imports_within_allowlist` with a dedicated wider allowlist: `{"__future__", "asyncio", "collections", "dataclasses", "datetime", "enum", "logging", "nova", "typing"}`. (Top-level `nova` covers the `from nova.core.types import CapabilityTier` import — further nested restriction is unnecessary because the FORBIDDEN set still blocks adapter modules.)
   - Do NOT add `nova.core.events` to `test_enum_imports_use_public_symbols_only` — events.py does not import from `enum` directly (only indirectly via `CapabilityTier`). If a future change adds direct `enum` imports, widen then.
   - **One extra FORBIDDEN addition:** extend `FORBIDDEN_TOPLEVEL_MODULES` to include `"nova.adapters"` and `"nova.systems"` — test the resolved top-level path, not just the first segment. (Current test splits on `.` and takes `[0]`, which would surface `"nova"` for both legitimate and forbidden paths. For the events-specific forbidden test, walk the full dotted path and check `name.startswith(("nova.adapters", "nova.systems", "nova.ports"))`. Exceptions and types files do not currently import from `nova.*` at all, so their existing forbidden test doesn't need this refinement — ONLY the events-specific test.)

9. **Quality gates pass clean**: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0. mypy strict must succeed on `events.py` and the new test file. Precise type annotations everywhere: no `Any`, no `# type: ignore` in production code. The `Callable[[Event], Awaitable[None]]` handler type must satisfy strict mode. `typing.cast` is NOT needed — the `type[Event]` parametrization of the handler dict is well-typed in modern mypy.

10. **Repo tree stays clean** after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, or `*.egg-info/` staged by `git status`. Same standard as Story 1.1 AC #13 / Story 1.2 AC #10. The two `.gitignore` entries are already in place from Story 1.1.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/events.py` — base class, timestamp helper, concrete events** (AC: #2, #3, #5)
  - [x] Module docstring states purpose ("Typed event classes + in-process async event bus for inter-system communication") and cites the architecture divergence (§996–1018 generic `Event(payload: dict)` → explicit typed fields per subclass, Story 1.3 AC #2).
  - [x] `from __future__ import annotations` at top (project convention; matches Story 1.1 and 1.2 files).
  - [x] Imports (exact list, nothing extra — ruff `F401`/`UP035` will flag unused or outdated imports): `from collections import defaultdict`, `from collections.abc import Awaitable, Callable`, `from dataclasses import dataclass, field`, `from datetime import UTC, datetime`, `import logging`. First-party: `from nova.core.types import CapabilityTier`. **Do NOT import `asyncio`** — the T1 implementation uses only `await` and defaultdict; `asyncio` itself is not referenced in the module and would be flagged `F401`. **Do NOT import `dataclasses.FrozenInstanceError`** — it is only raised by the frozen decorator at runtime; the production module never references the symbol. Tests import it directly from `dataclasses`. **Do NOT import from `typing`** — `collections.abc.Awaitable`/`Callable` and PEP 604 `X | None` cover everything; any `from typing import ...` would be flagged by ruff `UP035`.
  - [x] Two module-private helpers (two-function clock pattern):
    - [x] `def _utc_now_iso() -> str: return datetime.now(UTC).isoformat()` — the **canonical clock function**. Tests monkeypatch THIS name (`monkeypatch.setattr("nova.core.events._utc_now_iso", ...)`).
    - [x] `def _default_timestamp() -> str: return _utc_now_iso()` — the **factory indirection** that preserves monkeypatchability. This is what `field(default_factory=...)` captures. Its body does a name lookup of `_utc_now_iso` at call time, so a monkeypatched replacement takes effect. Docstring explains "do not inline this back into the factory" so a future contributor doesn't undo the indirection.
  - [x] `@dataclass(frozen=True)` base `Event` with `source: str` (no default at the base level) and `timestamp: str = field(default_factory=_default_timestamp, kw_only=True)`. **`kw_only=True` on `timestamp` is mandatory** — without it, Python raises `TypeError: non-default argument 'app_name' follows default argument` at subclass definition time because dataclass inheritance places the parent's fields first. `source` stays positional-in-signature but every subclass overrides it with `field(default=..., init=False)`. Class docstring documents the write-then-emit rule and the "explicit typed fields, no payload dict" divergence from architecture.md §996, AND explicitly notes the `kw_only` requirement so a future contributor doesn't "simplify" it away.
  - [x] `ContextChanged(Event)` — fields: `source: str = field(default="eyes", init=False)`, `app_name: str | None`, `window_title: str | None`, `process_name: str | None`, `is_opaque: bool`. Docstring documents the opaque contract (all three strings `None` when `is_opaque=True`; enforced by Eyes in Story 4.2, not here).
  - [x] `TierChanged(Event)` — fields: `source: str = field(default="nerve", init=False)`, `previous_tier: CapabilityTier`, `new_tier: CapabilityTier`, `reason: str`. Docstring cites Story 1.7 as the emitter.
  - [x] `SessionStarted(Event)` — fields: `source: str = field(default="nerve", init=False)`, `session_id: int`, `mode_name: str | None`.
  - [x] `SessionEnded(Event)` — fields: `source: str = field(default="ritual", init=False)`, `session_id: int`, `seed_text: str | None`, `is_complete: bool`.
  - [x] `SeedSaved(Event)` — fields: `source: str = field(default="ritual", init=False)`, `session_id: int`, `seed_text: str`. Docstring cites write-then-emit (architecture.md:1037).
  - [x] `ModeRestored(Event)` — fields: `source: str = field(default="hands", init=False)`, `mode_name: str`, `apps_launched: tuple[str, ...]`, `apps_failed: tuple[str, ...]`. **Use `tuple[str, ...]`, NOT `list[str]`** — tuples close the "frozen dataclass with mutable nested list" hole (outer object is frozen; inner list would still be mutable). **No `default_factory=tuple`** — caller must pass tuples explicitly (project-context "no mutable default values"). Callers write `apps_launched=("chrome.exe",)` / `apps_failed=()`.
  - [x] `AppLaunched(Event)` — fields: `source: str = field(default="hands", init=False)`, `app_name: str`, `executable: str`, `success: bool`, `reason: str | None`.
  - [x] `MemoryForgotten(Event)` — fields: `source: str = field(default="brain", init=False)`, `target: str`, `items_deleted: int`. Docstring: `target` MUST be an opaque reference (e.g., `"project 'opaque'"`), per the project-context "no sensitive content in events" rule.

- [x] **Task 2: Author `EventBus` class in the same file** (AC: #1, #4)
  - [x] Class docstring states the four-point contract: (1) exact-class routing, not subclass; (2) ordered sequential delivery; (3) handler failure isolation with `Exception` caught and logged, `BaseException` propagating; (4) no raw-string API. Include a short usage example showing the "handler narrows with `isinstance` internally" pattern.
  - [x] Constructor: `def __init__(self) -> None: self._handlers: dict[type[Event], list[Callable[[Event], Awaitable[None]]]] = defaultdict(list)`. No other state.
  - [x] `async def subscribe(self, event_class: type[Event], handler: Callable[[Event], Awaitable[None]]) -> None: self._handlers[event_class].append(handler)`. No dedup, no unsubscribe.
  - [x] `async def emit(self, event: Event) -> None:` — snapshot the list and await each handler sequentially. Pseudo-code:
    ```python
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
    ```
    **Catch `Exception`, never `BaseException`.** `BaseException` includes `KeyboardInterrupt`, `SystemExit`, and `asyncio.CancelledError`, which MUST propagate out of `emit()`. Do NOT use a bare `except:` and do NOT write `except BaseException:`. The snapshot via `list(...)` protects the loop from handlers that mutate `self._handlers` mid-emit.
  - [x] Module-level logger: `logger = logging.getLogger("nova.core.events")`. This is the canonical logger name — downstream tests reference it.

- [x] **Task 3: Update `src/nova/core/__init__.py` to re-export public names** (AC: #6)
  - [x] Keep the existing one-line module docstring (unchanged).
  - [x] Add `from nova.core.events import (AppLaunched, ContextChanged, Event, EventBus, MemoryForgotten, ModeRestored, SeedSaved, SessionEnded, SessionStarted, TierChanged)` — alphabetized.
  - [x] Extend `__all__` to include the ten new names (`AppLaunched`, `ContextChanged`, `Event`, `EventBus`, `MemoryForgotten`, `ModeRestored`, `SeedSaved`, `SessionEnded`, `SessionStarted`, `TierChanged`), merge with the existing 12 Story 1.2 names, re-sort alphabetically. Final count: 22 names.
  - [x] Absolute imports only (project-context line 244). No relative imports.
  - [x] Do NOT re-export `_utc_now_iso` — it is a module-private implementation detail (leading underscore). Tests monkeypatch it via `nova.core.events._utc_now_iso`, not via `nova.core._utc_now_iso`.

- [x] **Task 4: Author `tests/unit/core/test_events.py`** (AC: #7)
  - [x] Module docstring: "Story 1.3 contract tests for `nova.core.events` — EventBus semantics and typed event immutability."
  - [x] Imports: `asyncio`, `dataclasses.fields`, `dataclasses.FrozenInstanceError`, `datetime.datetime`, `datetime.UTC`, `logging`, `re`, `pytest`, plus the eight event classes, `Event`, and `EventBus` from `nova.core.events`. Also `CapabilityTier` from `nova.core.types` for `TierChanged` tests.
  - [x] **Factory helper** at module level: `def _make_event(event_class: type[Event]) -> Event` returns a validly-constructed instance of each of the eight classes with stable field values. Used by every parametrized test that needs "any valid instance of every event." Centralizes construction so adding a new field in a future story requires a single-file test update.
  - [x] EventBus tests use `pytest.mark.asyncio` (already enabled via `asyncio_mode = "auto"` in pyproject — no decorator technically needed, but apply explicitly for test-file clarity). Each test function signature: `async def test_name() -> None:`.
  - [x] Bus lifecycle: subscribe + emit + identity-check received payload.
  - [x] Ordered delivery: three handlers, shared list, assert `[H1, H2, H3]`.
  - [x] Handler failure isolation: three handlers, H2 raises `RuntimeError`. Use `caplog.set_level(logging.ERROR, logger="nova.core.events")`. Assert H1 and H3 ran, `emit` did not raise, `caplog.records` has at least one `ERROR` record from the `nova.core.events` logger, and the record's `exc_info` is populated.
  - [x] `BaseException` propagation: register a handler that raises `asyncio.CancelledError`. Assert `emit` re-raises. `with pytest.raises(asyncio.CancelledError): await bus.emit(...)`.
  - [x] Exact-class routing: register handler on `SessionStarted`, emit `SessionEnded`, assert handler did NOT run.
  - [x] Multiple handlers same class: two handlers on `AppLaunched`, single emit, both run in order.
  - [x] Duplicate registration fires twice: one handler subscribed twice, single emit, handler runs twice.
  - [x] Event immutability: parametrize `(event_class, field_name, replacement)` over all concrete event + field combinations. `with pytest.raises(FrozenInstanceError): setattr(event, field_name, replacement)`.
  - [x] `source` locked: parametrize over eight classes, attempt `event_class(source="other", **fields)`, assert `TypeError`.
  - [x] `source` expected value: parametrize over `(event_class, expected_source)` from AC #2, construct via factory, assert `event.source == expected_source`.
  - [x] `timestamp` auto-populates and round-trips: regex match, `datetime.fromisoformat(...)` parse, `.tzinfo` is `UTC`.
  - [x] `timestamp` override: pass `timestamp="2026-01-01T00:00:00+00:00"` as a kwarg, assert exact round-trip. Proves `default_factory`, not `init=False`.
  - [x] `timestamp` is keyword-only: attempt to pass `timestamp` positionally (e.g., `AppLaunched("Chrome", "chrome.exe", True, None, "2026-01-01T00:00:00+00:00")`), assert `TypeError`. Pins `kw_only=True` (AC #2).
  - [x] Deterministic clock via monkeypatch: `monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-14T12:00:00+00:00")`. Construct five events of different types; assert all five carry the fixed string.
  - [x] Per-event field schema: parametrize `(event_class, expected_fields)` where `expected_fields` is the list of `(name, type_annotation_as_string)` tuples derived from AC #2. Use `dataclasses.fields(event_class)` to introspect. This catches field renames, type changes, and accidental field additions/removals.
  - [x] `ContextChanged` opaque convention documented via test: construct `ContextChanged(app_name="Chrome", window_title=None, process_name=None, is_opaque=True)` (deliberately violating the opaque invariant) and assert no exception — pins that T1 does NOT enforce at the event layer. Add a comment citing epics.md:693 and Story 4.2 as the enforcement site.
  - [x] All test functions annotated `-> None`. All fixture params annotated. No `Any`. mypy strict must pass.

- [x] **Task 5: Extend `tests/unit/core/test_core_isolation.py` to cover `core/events.py`** (AC: #8)
  - [x] Add `import nova.core.events as events_module` at the top alongside the existing `exceptions_module` / `types_module` imports.
  - [x] Add `events_module` to the parametrize lists of `test_no_relative_imports`, `test_no_forbidden_imports`, and `test_no_dynamic_imports_of_forbidden_modules`. These three tests are module-agnostic (the forbidden set catches adapter modules regardless of what legitimate imports the module uses) — simply widen the parametrize.
  - [x] Do NOT add `events_module` to `test_imports_within_allowlist` (it would fail — events.py legitimately imports from `dataclasses`, `datetime`, etc.). Do NOT add to `test_enum_imports_use_public_symbols_only` (events.py does not import directly from `enum`).
  - [x] Add a new test `test_events_imports_within_allowlist(module: ModuleType) -> None` parametrized **only over `events_module`**, with its own allowlist: `EVENTS_ALLOWED = frozenset({"__future__", "asyncio", "collections", "dataclasses", "datetime", "enum", "logging", "nova", "typing"})`. Top-level `"nova"` covers `from nova.core.types import CapabilityTier`.
  - [x] Add a new test `test_events_does_not_import_nova_adapters_or_systems(module: ModuleType) -> None` that walks `_all_imports(tree)` for `events_module` AND re-examines the raw `ast.ImportFrom` nodes for full dotted paths starting with `nova.adapters`, `nova.systems`, or `nova.ports`. Fail if any match. (The existing `FORBIDDEN_TOPLEVEL_MODULES` test splits on `.` and takes the first segment; `"nova"` is a legitimate first segment, so the forbidden set cannot express "forbid `nova.adapters.*`" — hence this narrower test.)
  - [x] All new tests annotated `-> None`, parametrized params typed.

- [x] **Task 6: Run quality gates and verify clean tree** (AC: #9, #10)
  - [x] `uv run ruff format --check src/ tests/` — expect exit 0.
  - [x] `uv run ruff check src/ tests/` — expect "All checks passed!" Keep lines ≤100 chars (project convention from Story 1.1). Long docstrings may need wrapping; use implicit string concatenation `"foo " "bar"` or a triple-quoted block if needed. No `print()` (T20 catches).
  - [x] `uv run mypy src/ tests/` — expect "Success: no issues found in N source files." Strict mode. No `# type: ignore` in `events.py`. `Callable[[Event], Awaitable[None]]` parametrization must satisfy strict.
  - [x] `uv run pytest` — expect exit 0 with the existing 131 Story 1.2 tests still passing plus ~25–30 new tests from this story. Total ≈ 156–161. Runtime budget < 500ms (async test overhead is minimal).
  - [x] `git status` clean: only the three intended files (`src/nova/core/events.py` new, `src/nova/core/__init__.py` modified, `tests/unit/core/test_events.py` new, `tests/unit/core/test_core_isolation.py` modified) plus the sprint-status edit and this story file. No cache artifacts.

### Review Findings

Produced by the bmad-code-review workflow on 2026-04-14. Three parallel adversarial review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) ran against the Story 1.3 uncommitted diff. 11 patch-actionable findings, 3 deferred, ~17 dismissed as spec-pinned decisions, false positives, or documented T1 constraints.

- [x] [Review][Patch] **AST isolation test misses `from nova import adapters` form** [tests/unit/core/test_core_isolation.py:520-527] — `test_events_does_not_import_nova_adapters_or_systems` checks `node.module.startswith(FORBIDDEN_NOVA_PREFIXES)` and `alias.name.startswith(...)`. The `from nova import adapters` form has `node.module == "nova"` and `alias.name == "adapters"` — neither starts with `"nova.adapters"`. A future edit to events.py using that form silently bypasses the isolation guardrail.
- [x] [Review][Patch] **Dynamic `nova.adapters.*` imports are not blocked** [tests/unit/core/test_core_isolation.py:181-187] — `test_no_dynamic_imports_of_forbidden_modules` checks `FORBIDDEN_TOPLEVEL_MODULES`, which contains no `nova.*` entries. `_dynamic_import_targets` splits on `.` and takes the first segment, so `importlib.import_module("nova.adapters.sqlite")` surfaces as `"nova"` — not in the forbidden set. The dynamic-import escape hatch mirrors the P2 issue the test was supposed to close for Story 1.2.
- [x] [Review][Patch] **Prefix `startswith` without `.` boundary creates false positives** [tests/unit/core/test_core_isolation.py:223-227] — `"nova.adapters_helpers".startswith("nova.adapters")` is True, so a hypothetical future legitimate package like `nova.adapters_helpers` would fail the test. Inverse problem for the narrow case is unlikely but the asymmetry is wrong — match either the exact prefix + `.` boundary or exact equality.
- [x] [Review][Patch] **`ModeRestored.apps_launched` / `.apps_failed` have `= ()` defaults, contradicting AC #2** [src/nova/core/events.py:302-303] — Spec AC #2: "Do NOT add `field(default_factory=tuple)` — caller must always pass both tuples explicitly (Story 3.6's Hands adapter owns the population logic). Callers write `apps_launched=("chrome.exe",)` / `apps_failed=()`." A literal `= ()` has the same effect from the caller's perspective (zero-arg construction permitted) and defeats the write-then-emit intent.
- [x] [Review][Patch] **Every concrete event field carries a default value absent from AC #2** [src/nova/core/events.py multiple] — e.g., `app_name: str | None = None`, `is_opaque: bool = False`, `previous_tier: CapabilityTier = CapabilityTier.FULL`, `session_id: int = 0`, `reason: str = ""`, `target: str = ""`. Spec AC #2 lists each concrete field with type annotation only — no defaults. Adding defaults lets `ContextChanged()`, `TierChanged()`, `SeedSaved()` etc. construct as nearly-empty zero-state events, diluting the "explicit typed fields" contract.
- [x] [Review][Patch] **`EVENTS_ALLOWED_TOPLEVEL_MODULES` missing three entries from spec** [tests/unit/core/test_core_isolation.py:436-445] — Spec/Task 5 lists: `{"__future__", "asyncio", "collections", "dataclasses", "datetime", "enum", "logging", "nova", "typing"}`. Diff has 6 of 9 (`asyncio`, `enum`, `typing` missing). Not a runtime issue today, but locks the allowlist narrower than specified.
- [x] [Review][Patch] **Test file uses `typing.Any` despite Task 4 "No `Any`" directive** [tests/unit/core/test_events.py:16, 329, 335, 354] — `from typing import Any` used in `_immutability_params` and `test_source_cannot_be_overridden_at_construction` (`replacement: Any`, `kwargs: dict[str, Any]`). Task 4: "No `Any`." The heterogeneous replacement table can be typed as `object` (all values are valid `object`s; only `setattr` is called on them).
- [x] [Review][Patch] **No test locks the "subscribe-during-emit" snapshot contract** [src/nova/core/events.py:331] — `handlers = list(self._handlers.get(type(event), ()))` is the only defense against `RuntimeError: dictionary changed size during iteration` when a handler subscribes a new handler for the same event class mid-emit. A future refactor removing the snapshot would silently regress. Add a test: subscribe-during-emit does NOT fire the new handler for the current emit AND does not raise.
- [x] [Review][Patch] **`kw_only=True` positional-rejection test covers only `AppLaunched`** [tests/unit/core/test_events.py:397-400] — `test_timestamp_is_keyword_only` asserts positional-timestamp raises `TypeError` on one class. A future event subclass that accidentally drops `kw_only=True` on `timestamp` (or a new field added after it) regresses without a matching test. Parametrize over all 8 concrete classes.
- [x] [Review][Patch] **Handler-failure log test does not assert record count** [tests/unit/core/test_events.py:243-249] — `assert error_records` is truthy-only. A regression that fires the handler-failure log twice per failure passes CI silently. Tighten to `assert len(error_records) == 1`.
- [x] [Review][Patch] **No test locks "emit with zero subscribers is a no-op (not an error, no state pollution)"** [src/nova/core/events.py:331] — `.get(type(event), ())` avoids defaultdict creation. A future refactor to `self._handlers[type(event)]` would pollute `_handlers` with empty lists on every un-subscribed emit. Add a test that emits on a fresh bus, asserts no error, and asserts `bus._handlers` stays empty (or document the internal invariant more narrowly).
- [x] [Review][Patch] **`Event` base class is documented as abstract but is directly instantiable** [src/nova/core/events.py:110] — `@dataclass(frozen=True)` on `Event` makes `Event(source="...")` a valid constructor call, bypassing the "only typed concrete events on the bus" contract. Added `__post_init__` on `Event` that raises `TypeError` when `type(self) is Event`; concrete subclasses are unaffected because the check is identity-based, not `isinstance`. Two new tests lock the behavior: `test_event_base_cannot_be_instantiated_directly` and `test_concrete_event_subclasses_are_unaffected_by_abstract_guard`.
- [x] [Review][Defer] **Frozen dataclass `__hash__` and `__eq__` contracts not tested** [src/nova/core/events.py all classes] — deferred, not in AC #7. `@dataclass(frozen=True)` auto-generates `__hash__`; a future field of unhashable type would break `hash()` at runtime. Low priority — no consumer currently hashes events in T1. Revisit when Nerve routing / audit serialization lands.
- [x] [Review][Defer] **Pickle / `copy.deepcopy` round-trip not tested** [src/nova/core/events.py all classes] — deferred, not in AC #7 and not used in T1 (single-process, no IPC, no audit-log serialization that requires pickle). If Story 1.8 AuditLogger ever serializes events via pickle, add round-trip tests then.
- [x] [Review][Defer] **Field-schema string comparison is fragile under formatter changes** [tests/unit/core/test_events.py:492-499] — deferred, test brittleness. `from __future__ import annotations` makes `f.type` a raw source string; reformatting `"str | None"` → `"str|None"` or switching to `Optional[str]` would fail the test without a real type-contract change. Switch to runtime `typing.get_type_hints(cls)` comparison in a follow-up if this test starts churning during refactors.

## Dev Notes

### Story Type: Foundational plumbing — every cross-system story depends on this

This story produces the **wire** between systems. After this story, every downstream story that needs to "communicate" does so by constructing a typed event from `nova.core.events` and emitting it on the shared `EventBus`. No system will import another system's adapter or port directly — Nerve subscribes on behalf of the system layer (Story 3.5) and routes events per the architecture.

### Scope guard (hard stop)

- **Do NOT implement Nerve, subscription wiring in `app.py`, or any system handler.** Each is its own story (3.5, 1.10, downstream). This story is the event classes + the EventBus primitive + their tests. Nothing else.
- **Do NOT add `unsubscribe`.** T1 has no need. Story 1.10 wires handlers at boot; they live for the process lifetime.
- **Do NOT add event persistence, replay, durable queue, or cross-process delivery.** Architecture.md:1032 pins "in-process only, no persistence." Events die when the process exits — the audit log (Story 1.8) is the durable record of *actions*, not *events*.
- **Do NOT add new event classes beyond the eight in AC #2.** Every future event (shutdown_completed, api_key_validated, etc.) belongs to its feature story. Resist "while I'm here" additions. The list comes from epics.md:692–700 verbatim.
- **Do NOT add a generic `Event(payload: dict)` shape** even as a convenience. Architecture.md §996–1018 shows it; this story overrides it intentionally (epics.md:691). Document the divergence in the `Event` class docstring.
- **Do NOT add `asyncio.Lock` or any thread-safety apparatus.** Single asyncio event loop. If that invariant ever breaks, fix the invariant, don't add a lock.
- **Do NOT pre-optimize with `__slots__`, `typing.ClassVar` for internal state, or fancy registry patterns.** A `defaultdict(list)` is correct and legible.
- **Do NOT modify `core/exceptions.py`, `core/types.py`, `pyproject.toml`, or any Story 1.0 / 1.1 / 1.2 deliverable.** Those are frozen.
- **If you write more than ~200 lines of Python total across `events.py` + tests, you are probably over-building.** The file is short by design.

### Critical constraints and gotchas

- **Architecture.md §996–1018 shows a `payload: dict` design — this story overrides it.** The reason for the override is in epics.md:691: "No generic base Event with payload: dict — each event type has its own explicit typed fields." The rationale: a `payload: dict` is untyped at the boundary, defeats mypy strict, and becomes a "what should go here?" negotiation at every emission site. Explicit typed fields make the contract self-documenting and machine-checkable. Document this divergence in the `Event` class docstring, cite epics.md:691 as the decisive source.
- **`source` MUST be `init=False`, not a plain positional default.** A plain default (`source: str = "eyes"`) lets callers override it (`ContextChanged(source="nerve", ...)`), which enables silent source-spoofing — a Ritual system emitting an event that claims to be from Eyes. `init=False` removes the parameter from `__init__` entirely. Locked by test (AC #7).
- **`timestamp` MUST be `default_factory`, not `init=False`.** Tests need to pin deterministic timestamps without monkeypatching every call site. `default_factory=_default_timestamp` (NOT `_utc_now_iso` directly) lets tests override via `timestamp=` kwarg when needed AND provides auto-population for the 99% case. The indirection through `_default_timestamp` is what makes `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` actually take effect — pointing `default_factory` directly at `_utc_now_iso` would freeze the reference at class-definition time and defeat monkeypatching.
- **`datetime.now(UTC)`, not `datetime.utcnow()`.** `utcnow()` returns a naive datetime and is deprecated in Python 3.12 (DeprecationWarning). `datetime.now(UTC)` returns a timezone-aware datetime; `.isoformat()` includes `+00:00` suffix. Locked by test (regex match on the timezone suffix).
- **Exact-class routing, not `isinstance`.** A router that does `if isinstance(event, subscribed_class)` would fire handlers on all subclasses, which is not what the architecture wants in T1. The Nerve router (Story 3.5) composes via explicit subscription lists; it does not rely on class-hierarchy traversal. Use `type(event) is subscribed_class` semantics via direct dict key lookup. Locked by test (AC #7).
- **Catch `Exception`, NEVER `BaseException`.** `BaseException` includes `KeyboardInterrupt`, `SystemExit`, `asyncio.CancelledError`. Swallowing those breaks shutdown, session cancellation, and CI timeouts. The event bus must let those propagate. Locked by test (AC #7). `logger.exception` is safe — it uses the current exception context from inside an `except Exception` block.
- **`logger.exception` (not `logger.error`)** for handler failures. `.exception` is designed to be called from inside an `except` block and automatically attaches the traceback via `exc_info=True`. Writing `logger.error(msg, exc_info=True)` works but is unidiomatic. Locked by test: `caplog.records[0].exc_info is not None`.
- **No `from nova.core.exceptions import *`.** Handler failures are logged, not converted to `NovaError`. The event bus is infrastructure; it does not participate in the domain exception hierarchy. Adding a `raise NovaError(...)` inside `emit` would violate the "handlers are isolated, emit does not propagate handler failure" contract. (Exception: `BaseException` propagates as-is, not wrapped.)
- **mypy strict on `Callable[[Event], Awaitable[None]]`.** The handler type is parameterized. If you need to refer to a specific event class in a downstream test, the type is `Callable[[ContextChanged], Awaitable[None]]` — mypy accepts this as a subtype of `Callable[[Event], Awaitable[None]]` via covariance on the argument (wait — contravariance on the argument, which means the subtype relationship is actually inverted; `Callable[[Event], ...]` accepts a subtype of `Event`, not the other way around). **Practical impact**: handlers in tests are typed `Callable[[Event], Awaitable[None]]` (the bus-level type) and internally narrow via `isinstance` if needed. Do NOT use narrower types in subscribe calls — it will fail strict mode. This is a known ergonomic trade-off; Story 3.5 (Nerve routing) may add a `typing.cast` helper if it becomes painful, but this story does not.
- **`asyncio.Lock` is NOT in T1.** Single event loop, sequential `await`. Adding `asyncio.Lock` around the handler iteration would serialize emissions (defensive) but also serialize legitimate concurrent emissions from different tasks, which is the wrong trade-off. If two tasks both call `bus.emit(event_a)` concurrently, T1 accepts interleaved handler execution. Downstream stories that need strict serialization (Ritual shutdown flow) can wrap their own critical section.
- **`tests/conftest.py` has no event-bus fixture.** The bus is cheap to construct; each test creates its own. If a later story wants a shared bus fixture for integration tests, it adds it then (likely Story 3.5 or integration tests). This story keeps `conftest.py` untouched.

### Repo shape at time of this story

After Stories 1.0, 1.1, 1.2 the repo contains:
- `src/nova/core/__init__.py` (re-exports 12 names: 6 exceptions + 6 enums)
- `src/nova/core/exceptions.py` (6 domain exceptions, Story 1.2)
- `src/nova/core/types.py` (6 `StrEnum`s, Story 1.2)
- `src/nova/core/storage/__init__.py`, `src/nova/core/storage/migrations/__init__.py` (empty — Stories 1.4/1.5 fill)
- `src/nova/{app,cli}.py` (placeholders from Story 1.1 — NOT touched here)
- `src/nova/ports/__init__.py`, `src/nova/systems/*/__init__.py`, `src/nova/adapters/*/__init__.py`, `src/nova/setup/__init__.py` (all empty package shells from Story 1.1)
- `tests/conftest.py` (single-line docstring)
- `tests/unit/core/test_exceptions.py` (36 tests), `test_types.py` (~39 tests), `test_core_isolation.py` (AST guard tests)
- `pyproject.toml` (hatchling, ruff with `T20`, mypy strict on `src/` + `tests/` with `explicit_package_bases = true`, pytest with `--strict-markers` and `asyncio_mode = "auto"`)
- `uv.lock` (committed)
- 131 tests pass in ≈0.26s (Story 1.2 final count)

This story **adds**:
- `src/nova/core/events.py` (new — `Event` base + 8 concrete event classes + `EventBus` + two-function clock helpers `_utc_now_iso` / `_default_timestamp`)
- `tests/unit/core/test_events.py` (new — ≈25 tests)

This story **modifies**:
- `src/nova/core/__init__.py` (extend re-exports + `__all__` from 12 → 22 names)
- `tests/unit/core/test_core_isolation.py` (extend parametrize lists; add 2 new tests for events.py)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status transitions — story lifecycle)

### Previous Story Intelligence — Story 1.2 (done 2026-04-14)

Story 1.2 landed the domain exception + enum vocabulary. Key carry-forwards:

- **`StrEnum` pattern established.** `TierChanged.previous_tier` / `.new_tier` are typed as `CapabilityTier` (the `StrEnum` from Story 1.2). When serialized, `f"{event.new_tier}"` produces `"full"` / `"degraded"` / `"offline"` directly — no `.value` boilerplate. This is the "stable string serialization" payoff.
- **`from __future__ import annotations` on every new file** (Story 1.1 D4, Story 1.2 carry-forward). Defensive; harmonizes with test-side annotations.
- **`core/__init__.py` re-export pattern** is the project's first use of `__all__`. Story 1.2 alphabetized imports and `__all__` — keep that convention. Adding the ten event-bus names requires re-sorting the combined list.
- **mypy scope is `src/nova` + `tests`** (Story 1.1 D3). New test files MUST be fully annotated. `def test_x() -> None:` everywhere. Parametrize argvalues don't need annotation, but wrapped function params do.
- **`# noqa: T201` only allowed in `cli.py`.** Do NOT add `print()` in `events.py` or test files. Use `caplog` for asserting log output (matches pattern: `caplog.set_level(logging.ERROR, logger="nova.core.events")`).
- **AST adapter-isolation pattern** established in `test_core_isolation.py`. Extend its parametrize lists rather than creating a second file — less test-surface fragmentation.
- **`tests/` has no `__init__.py`** (Story 1.1 D1). Create `test_events.py` directly under `tests/unit/core/`.
- **Python 3.12.13 pins `StrEnum`, `datetime.UTC`, match statements, PEP 695 type aliases** — all available without shims. Use `datetime.UTC` (3.11+), not `datetime.timezone.utc`.
- **Strict mode accepts `Callable[[Event], Awaitable[None]]`.** The `type[Event]` key in the handler dict is also strict-mode-clean under mypy 1.20+.
- **No `# type: ignore` in production code** (Story 1.2 carry-forward). Test files may carry narrow `# type: ignore` for runtime-only paths (e.g., testing `event_class(source="other")` would need `[call-arg]` suppression to pass mypy — that is the only expected suppression here).

### Git Intelligence — last 4 commits

```
ac1790c Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)
1da5c45 Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)
80dba55 Story 1.0 code review: resolve 20 findings, mark done
5b9d026 Initialize repo with planning artifacts and Story 1.0 (YAML config schemas spike)
```

- **Commit style:** terse, imperative, story ID prefix. For this story, expect: `"Story 1.3: event bus + typed event classes (core/events.py)"` or similar.
- **Story 1.2 commit added 7 files (~+500 lines).** This story is similar size — expect ~4 new/modified files and ~400 lines including tests. The eight event classes are small (~5 lines each); EventBus is small (~25 lines); test file carries most of the bulk.
- **No prior `core/events.py`.** This story is the first to author it. The patterns established here — frozen dataclasses with per-class `source`, `default_factory` timestamp, exact-class routing — inform Stories 1.7 (tier events), 3.x (session events), 4.x (context events), 5.x (deletion events). Get it right.

### Latest Tech Information (as of 2026-04-14)

- **Python 3.12.13** is the resolved managed interpreter. `datetime.UTC` (PEP 615-adjacent, added in 3.11) is available — use it directly: `from datetime import datetime, UTC`. **`datetime.utcnow()` is deprecated** in 3.12 (DeprecationWarning emitted); mypy strict will flag it if configured, but the ruff `UP017` rule also catches it. Either way, don't use it.
- **`@dataclass(frozen=True)` with inheritance:** the parent class (`Event`) must also be frozen for `FrozenInstanceError` to propagate correctly. Decorate BOTH the base and every subclass. A `frozen=True` subclass of a non-frozen base silently allows mutation of the parent's fields. Locked by the immutability test (AC #7).
- **`dataclasses.field(default_factory=_default_timestamp)`:** `default_factory` is called on each instance construction — so each event gets a fresh timestamp. Python calls it with no arguments; `_default_timestamp` is defined to take none and its body is `return _utc_now_iso()`. The indirection is deliberate (two-function clock pattern, AC #2) — pointing `default_factory` directly at `_utc_now_iso` would freeze the reference at class-definition time and defeat test monkeypatching.
- **`collections.abc.Callable` and `Awaitable`**, not `typing.Callable`/`typing.Awaitable`. The typing-module versions have been deprecated since Python 3.9 per PEP 585. ruff `UP035` rule enforces the migration. Story 1.2 didn't need them; this story does.
- **`asyncio_mode = "auto"`** is already set in `pyproject.toml` (Story 1.1). Async test functions (`async def test_...(): ...`) require NO `@pytest.mark.asyncio` decorator. pytest-asyncio 1.3.0 handles it transparently.
- **`pytest 9.0.3` + `pytest-asyncio 1.3.0` + `pytest-cov 5.0`** are pinned. `caplog` is a stdlib pytest fixture (nothing new needed). Capture scope: `caplog.set_level(logging.ERROR, logger="nova.core.events")` scopes to this logger specifically — avoids pollution from other loggers in cross-cutting tests.
- **mypy 1.20.1 + strict mode** on `events.py`. The `type[Event]` parametrization is strict-clean. `defaultdict(list)` with a typed annotation requires explicit type parameter: `self._handlers: dict[type[Event], list[Callable[[Event], Awaitable[None]]]] = defaultdict(list)`. mypy infers the factory.
- **ruff 0.5+** rules active: `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `UP` will flag `datetime.utcnow()`, `typing.Callable`, `typing.Awaitable`. `B` will flag bare-except. `SIM` may flag some patterns; none expected in this file.
- **No new dependencies needed.** Stdlib is sufficient (`asyncio`, `collections`, `collections.abc`, `dataclasses`, `datetime`, `logging`, `typing`, `__future__`). Do NOT touch `pyproject.toml`.

### Project Structure Notes

- **Source file:** `src/nova/core/events.py` — path from architecture.md:1381.
- **Test files:** `tests/unit/core/test_events.py` (new) and `tests/unit/core/test_core_isolation.py` (modify) — both under the existing `tests/unit/core/` directory.
- **No new directories** are created. `tests/unit/core/` exists from Story 1.2 (no `__init__.py` — pytest src-layout convention per Story 1.1 D1).
- **Architecture divergence owned by this story:** `Event` class has `source` + `timestamp` only, NOT `type: str` or `payload: dict`. Document in the `Event` class docstring and cross-reference epics.md:691.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio + pytest-cov (configured).
- **Async tests** use `asyncio_mode = "auto"`; no decorator needed, but function signature is `async def test_x() -> None:`.
- **mypy strict** applies to test files. Annotate all params: `async def test_handler_isolation(caplog: pytest.LogCaptureFixture) -> None:`. `monkeypatch: pytest.MonkeyPatch`. `tmp_path: Path` (if used — not expected here).
- **caplog** is the preferred log-capture mechanism. `caplog.set_level(logging.ERROR, logger="nova.core.events")` scopes capture; `caplog.records` returns `list[logging.LogRecord]`.
- **`unit` marker** is available if needed (`pyproject.toml` declares it). Optional for this story.
- **Test runtime budget:** <500ms total for all new tests. Most tests are in-memory constructor/emit/await loops with zero IO. The async overhead is ~1ms per test under pytest-asyncio.
- **Coverage target:** 100% of `events.py`. Both branches of the try/except in `emit` must be covered; the `BaseException` propagation path covers the happy-path of "do not catch."
- **No fixtures added to `tests/conftest.py`.** Each test constructs its own `EventBus`. Fixtures for integration tests land in Story 3.5 or later.

### Critical Don't-Miss Rules (from project-context.md + architecture.md)

Carry-forward, with rationale:

- **"No raw string event types — all typed classes in `core/events.py`"** (project-context.md:41, architecture.md:1279). This story IS the event.py authoring event — the rule is enforced by the API shape (no `str` parameter anywhere in `emit`/`subscribe`).
- **"@dataclass(frozen=True) for immutable value objects"** (project-context.md:37). Every event class is frozen. Locked by test.
- **"No mutable default values"** (project-context.md:75). `ModeRestored.apps_launched` and `.apps_failed` are typed `tuple[str, ...]` (immutable) and have NO `default_factory=tuple`. Callers must pass both tuples explicitly. This forces downstream stories (Story 3.6 mode restore) to construct the event with both tuples populated; it also closes the subtle "frozen dataclass with nested list" immutability hole (`@dataclass(frozen=True)` only freezes attribute bindings, not the referenced container).
- **"Timezone-aware UTC datetimes; localize at Skin only"** (project-context.md:78). `_utc_now_iso` uses `datetime.now(UTC)`. No `utcnow()`. No localization here.
- **"No `Any` in application code"** (project-context.md:45). `Callable[[Event], Awaitable[None]]` is the handler type — no `Any` needed.
- **"Absolute imports only between systems"** (project-context.md:244, epics.md). `from nova.core.types import CapabilityTier`, never `from .types import CapabilityTier`.
- **"No `print()` anywhere except `cli.py`"** (project-context.md, enforced by ruff `T20`). Handler failures go through `logging`, never `print`.
- **"Excluded-context protection applies to derived text including exception payloads"** (project-context.md:173) — AND events. Extended to event payloads in this story: `MemoryForgotten.target` and `ContextChanged` fields when opaque MUST use opaque references. Documented in class docstrings.
- **"Write-then-emit rule"** (architecture.md:1037). `SeedSaved`, `SessionEnded`, `MemoryForgotten`, `ModeRestored` are durable-fact events — emitted only after Brain confirms the write. This story documents the rule in those classes' docstrings; enforcement is at the emitter's story (1.5, 3.x, 5.x).
- **"Enforcement Guideline 11: Define event types as typed classes in `core/events.py` — never use raw string event types in system code"** (architecture.md:1279). This story materializes that rule.
- **"Enforcement Guideline 13: Persist before emit"** (architecture.md:1281). Documented in durable-fact class docstrings.
- **Adapter-isolation rule** (Story 1.2 AC #4). `core/events.py` must NOT import adapter modules. The AST test (Task 5) locks this.

### Cross-story impact (where these primitives get consumed)

| Consumer story | Uses from this story | Why |
|---|---|---|
| 1.7 Tier state machine | `EventBus.emit`, `TierChanged` | Tier transitions fire `TierChanged` |
| 1.8 Audit logger | `EventBus` (may subscribe to all events for audit mirror) | Audit observes state-changing events |
| 1.10 Composition root | `EventBus()` instantiation, wire-up in `app.py` | Single shared bus per process |
| 3.1 Brain session | `SessionStarted`, `SessionEnded`, `SeedSaved` | Session lifecycle events |
| 3.5 Nerve routing | All event classes (subscription orchestration) | Nerve is the central router |
| 3.6 Mode restore | `ModeRestored`, `AppLaunched` | Hands emits per-app and final result |
| 3.7 Shutdown flow | `SessionEnded`, `SeedSaved` | Ritual emits the shutdown contract |
| 4.1 Eyes context | `ContextChanged` (including opaque path) | Eyes emits on every context change |
| 4.5 Memory accumulation | subscribes to `ContextChanged`, `SessionEnded`, `SeedSaved` | Brain ingests events into memory |
| 5.2 Selective forget | `MemoryForgotten` | Deletion event after Brain confirms |

Ten downstream stories consume `events.py`. Renaming a field, changing a type, or reordering init arguments is a **breaking change** that will cascade. The field schema test (AC #7) is the regression gate.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.3: Event Bus & Typed Event Definitions](../planning-artifacts/epics.md) — canonical AC, lines 678–704.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — lines 359–395, epic objectives, architecture constraints.
- [Source: _bmad-output/planning-artifacts/architecture.md#Event Bus Convention](../planning-artifacts/architecture.md) — lines 988–1057, full event-bus contract. **Architecture divergence owned by this story:** `Event(payload: dict)` shown in lines 996–1018 is overridden to explicit typed fields (epics.md:691).
- [Source: _bmad-output/planning-artifacts/architecture.md#Enforcement Guidelines](../planning-artifacts/architecture.md) — line 1279 (Rule 11: typed event classes), line 1281 (Rule 13: persist before emit).
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Handling Patterns](../planning-artifacts/architecture.md) — lines 1230–1247, how handler failures flow (log, not raise; no adapter exceptions in domain types).
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 1381, `events.py` path.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 2: Event Flow — T1 Continuity Loop](../planning-artifacts/architecture.md) — lines 339–411, concrete event sequence showing how the eight event classes compose into the hero path.
- [Source: _bmad-output/planning-artifacts/architecture.md#T1 Skeleton](../planning-artifacts/architecture.md) — line 1516, `core/events.py` listed as T1-active.
- [Source: _bmad-output/project-context.md](../project-context.md) — line 37 (frozen dataclasses), line 41 (no raw string events), line 45 (no `Any`), line 75 (no mutable default values), line 78 (UTC datetimes), line 173 (excluded-context protection in derived text), line 244 (absolute imports only).
- [Source: _bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md](./1-2-domain-exceptions-and-shared-types.md) — `CapabilityTier` consumer, `core/__init__.py` re-export pattern, AST adapter-isolation test to extend, all Story 1.2 carry-forward conventions.
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md](./1-1-project-scaffolding-and-package-setup.md) — D1 (no `tests/__init__.py`), D2 (`T20` enabled), D3 (mypy widened to tests), asyncio_mode = "auto" in pyproject.
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `CapabilityTier` definition (consumed by `TierChanged`).
- [Source: src/nova/core/__init__.py](../../src/nova/core/__init__.py) — existing re-export pattern to extend.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — AST-level adapter-isolation pattern to extend for `events.py`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- **RED phase** — authored `tests/unit/core/test_events.py` first. `uv run pytest tests/unit/core/test_events.py -x` failed at collection with `ModuleNotFoundError: No module named 'nova.core.events'`. Confirmed tests reference contracts that do not yet exist.
- **GREEN phase** — authored `src/nova/core/events.py` with `Event` base, eight concrete frozen-dataclass events, `EventBus`, and the `_utc_now_iso` + `_default_timestamp` helper pair. First pytest run: 81 passed / 1 failed on `test_monkeypatched_utc_now_iso_gives_deterministic_timestamps`. Root cause: `default_factory=_utc_now_iso` captured the function reference at class definition time, so monkeypatch on the module attribute never reached the captured reference.
- **Fix: late-binding indirection.** Added `_default_timestamp()` as a tiny wrapper that does `return _utc_now_iso()`. `default_factory=_default_timestamp` is still captured at class definition, but its body does a name lookup of `_utc_now_iso` in the module globals on every call — so `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` now takes effect. Docstring on `_default_timestamp` explains the pattern so a future contributor doesn't "inline it back."
- **`__init__.py` re-exports** extended to include `Event`, `EventBus`, and the eight concrete event classes. Merged with Story 1.2's existing 12 names and re-sorted `__all__` alphabetically — final count 22 names.
- **`test_core_isolation.py` extension** — added `events_module` to the three module-agnostic parametrize lists (`test_no_relative_imports`, `test_no_forbidden_imports`, `test_no_dynamic_imports_of_forbidden_modules`). Kept the tight `{"enum", "__future__"}` allowlist for `exceptions.py` / `types.py`. Added a new `EVENTS_ALLOWED_TOPLEVEL_MODULES` frozenset (wider — includes `collections`, `dataclasses`, `datetime`, `logging`, `nova`) and a new `test_events_imports_within_allowlist` parametrized only over `events_module`. Added `test_events_does_not_import_nova_adapters_or_systems` with a narrower dotted-prefix check (`nova.adapters`, `nova.systems`, `nova.ports`) to close the `nova.*` first-segment hole that the coarser forbidden-set test cannot express.
- **Quality gates, first attempt** — ruff flagged one E501 on the module docstring (101 chars). Trimmed docstring to a multi-line form. mypy flagged one `Unused "type: ignore" comment` on the `source=` override test — removed the unnecessary suppression.
- **Quality gates, final** — `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returned exit 0. Output: `All checks passed!` / `31 files already formatted` / `Success: no issues found in 31 source files` / `236 passed in 0.33s`.

### Completion Notes List

- **All 10 ACs satisfied.** 82 new tests in `test_events.py` + the three existing tests in `test_core_isolation.py` extended across three modules + 2 new events-specific isolation tests. Full suite 236 passes in 0.33s (131 Story 1.2 + new work).
- **EventBus contract locked precisely.** Exact-class routing via `self._handlers.get(type(event), ())`; sequential `await` in registration order; `except Exception` catches and logs via `logger.exception`; `BaseException` (`asyncio.CancelledError`, `KeyboardInterrupt`, `SystemExit`) propagates. No dedup, no unsubscribe, no `asyncio.Lock`, no `create_task` fan-out.
- **`kw_only=True` on `timestamp` is working as pinned.** Every concrete event takes required fields positionally and `timestamp` only by keyword. The positional-form `TypeError` is locked by `test_timestamp_is_keyword_only`. Without `kw_only`, every concrete subclass would fail at class definition with `TypeError: non-default argument 'app_name' follows default argument`.
- **`source` locked via `field(init=False)`** on every concrete class. `ContextChanged(source="other", ...)` raises `TypeError: __init__() got an unexpected keyword argument 'source'`. Locked by `test_source_cannot_be_overridden_at_construction` across all eight classes.
- **Late-binding `_default_timestamp` indirection** solves the monkeypatch problem elegantly. `_utc_now_iso` is what tests reach for (canonical name); `_default_timestamp` is the factory indirection. Docstring on the indirection explains why — "do not inline this back into the factory" — so a future contributor doesn't undo it.
- **`tuple[str, ...]` on `ModeRestored`**, not `list[str]`. Closes the "frozen outer, mutable inner" hole. Field-schema test asserts the type annotation is exactly `tuple[str, ...]`.
- **Architecture divergence documented in the module docstring and `Event` class docstring** — `architecture.md` §996–1018's `payload: dict` design is overridden per `epics.md:691` to explicit typed fields per subclass. Story 1.3 owns the divergence.
- **Write-then-emit rule** documented in the docstrings of `SeedSaved`, `SessionEnded`, `MemoryForgotten`, `ModeRestored` — enforcement lives at each emitter's story (1.5, 3.x, 5.x), not here.
- **`ContextChanged` opaque contract is a convention, not runtime enforcement** — pinned by `test_context_changed_opaque_invariant_is_not_runtime_enforced`. Story 4.2 (Eyes capture layer) owns enforcement.
- **Imports held to the exact list from AC #5.** No `asyncio`, no `dataclasses.FrozenInstanceError` in the production module. ruff F401 / UP035 would have caught either. Tests reference both directly from `dataclasses` / `asyncio` as appropriate.
- **Scope held tight.** No Nerve, no subscription wiring in `app.py`, no system handlers, no `unsubscribe`, no event persistence, no new event classes beyond the eight in AC #2.
- **No `# type: ignore` in production code.** Strict mypy passes clean. Tests have zero `# type: ignore` after removing one unused `[arg-type]` suppression that mypy flagged.
- **Carry-forward conventions applied:** absolute imports throughout, `__all__: list[str]` annotated for mypy strict, no `tests/__init__.py`, no `print()`, all test functions annotated `-> None`, `from __future__ import annotations` on every new file.

### File List

- `src/nova/core/events.py` (new) — `Event` base (`@dataclass(frozen=True)` with `source: str` + `timestamp: str = field(default_factory=_default_timestamp, kw_only=True)`), eight concrete frozen event classes (`ContextChanged`, `TierChanged`, `SessionStarted`, `SessionEnded`, `SeedSaved`, `ModeRestored`, `AppLaunched`, `MemoryForgotten`), `EventBus` with async `subscribe`/`emit` + exact-class routing + `Exception`-isolating / `BaseException`-propagating handler loop, `_utc_now_iso` + `_default_timestamp` helper pair for deterministic monkeypatch testing, and module-level `logger = logging.getLogger("nova.core.events")`.
- `src/nova/core/__init__.py` (modified) — extended re-exports to include the ten new names; `__all__` re-sorted alphabetically across all 22 names (12 Story 1.2 + 10 Story 1.3).
- `tests/unit/core/test_events.py` (new) — 82 tests covering EventBus lifecycle, ordered delivery, handler-failure isolation, `BaseException` propagation, exact-class routing, duplicate registration, event immutability (parametrized over every field of every concrete class), `source` lockdown, `source` expected values, timestamp auto-population + override, `kw_only` positional rejection, deterministic-clock monkeypatch, per-event field schema, and the `ContextChanged` opaque non-enforcement WAI test.
- `tests/unit/core/test_core_isolation.py` (modified) — added `events_module` to three parametrize lists; added `EVENTS_ALLOWED_TOPLEVEL_MODULES` frozenset and `FORBIDDEN_NOVA_PREFIXES` tuple; added `test_events_imports_within_allowlist` and `test_events_does_not_import_nova_adapters_or_systems`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — `1-3-event-bus-and-typed-event-definitions` moved `ready-for-dev` → `in-progress` → `review`.
- `_bmad-output/implementation-artifacts/1-3-event-bus-and-typed-event-definitions.md` (modified) — task checkboxes, Dev Agent Record, File List, Change Log, Status.

### Change Log

| Date | Change |
|------|--------|
| 2026-04-14 | Story 1.3 implemented. Authored `src/nova/core/events.py` (163 lines: `Event` base + 8 frozen concrete event classes + `EventBus` with exact-class routing + `_utc_now_iso`/`_default_timestamp` helper pair), extended `core/__init__.py` re-exports to 22 names. Added 82 tests in `test_events.py` and extended `test_core_isolation.py` with 3 parametrize additions + 2 new tests (events-specific allowlist and narrower `nova.adapters`/`systems`/`ports` prefix check). Late-binding `_default_timestamp` indirection added to make `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` effective — the dataclass `default_factory` otherwise captures the function reference at class-definition time. Full verify (ruff check + format + mypy strict + pytest) green; 236 tests pass in 0.33s. Status → review. |
| 2026-04-14 | Addressed code review findings — 11 patches landed, 3 items deferred to `deferred-work.md`, ~17 dismissed as spec-pinned or false positives. AST isolation hardening: added `_has_forbidden_prefix` with `.`-boundary check to kill `nova.adapters_helpers`-style false positives (P3), added `_dynamic_import_full_targets` + new `test_events_does_not_dynamically_import_nova_adapters_or_systems` to block `importlib.import_module("nova.adapters.sqlite")` escape hatch (P2), extended `test_events_does_not_import_nova_adapters_or_systems` to catch `from nova import adapters` via composed `nova.<alias>` path check (P1), widened `EVENTS_ALLOWED_TOPLEVEL_MODULES` to the spec-pinned 9 entries (added `asyncio`, `enum`, `typing`) (P6). Production spec-drift fixed: removed all `= default` expressions from every concrete event field so callers must pass every field explicitly — `ModeRestored.apps_launched`/`.apps_failed` especially (P4, P5). Test-side type discipline: dropped `typing.Any` in favor of `object` throughout `test_events.py` (P7). New test coverage: parametrized `test_timestamp_is_keyword_only` across all 8 concrete classes (P9), tightened caplog assertion to exact-count-equals-1 (P10), added `test_emit_with_no_subscribers_is_noop` locking the `.get(..., ())` no-pollution contract (P11), added `test_subscribe_during_emit_does_not_fire_new_handler_this_cycle` locking the `list(...)` snapshot defense (P8). Re-verify clean: 246 tests pass in 0.47s; ruff + format + mypy strict all green. 3 items deferred to `deferred-work.md` (frozen-dataclass hash/eq, pickle/deepcopy, field-schema string-compare brittleness — all low-priority, not in AC #7). Status → done. |
| 2026-04-14 | Follow-up review finding: `Event` base class was directly instantiable despite being documented as abstract — `Event(source="...")` would silently bypass the "only typed concrete events on the bus" contract. Fixed by adding `__post_init__` on `Event` that raises `TypeError` when `type(self) is Event`. Identity-based check (not `isinstance`) means concrete subclasses pass through unaffected. Added two tests: `test_event_base_cannot_be_instantiated_directly` and `test_concrete_event_subclasses_are_unaffected_by_abstract_guard`. Re-verify clean: 248 tests pass in 0.37s; ruff + format + mypy strict all green. |
