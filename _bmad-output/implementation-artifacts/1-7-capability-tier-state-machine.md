# Story 1.7: Capability Tier State Machine

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing cloud-dependent features,
I want a tier state machine in `core/tiers.py` that tracks full / degraded / offline state with a tolerant-degrade model, driven by injected health-check and event-bus dependencies,
so that every system can check tier state synchronously before cloud operations and tier transitions fire `TierChanged` events reliably — without any system importing the Claude adapter directly.

## Acceptance Criteria

1. **`src/nova/core/tiers.py` is the single owner of the capability tier state machine.** Public surface is intentionally small:
   - One `Protocol` class: `HealthCheck` — the adapter-facing contract for the cloud reachability probe.
   - One class: `TierManager` — owns the mutable tier state, emits `TierChanged` events, runs the recovery loop.
   - Public methods on `TierManager` (exact signatures in AC #4): `tier` (property), `report_success`, `report_failure`, `report_rate_limit_or_outage`, `check_now`, `run_recovery_loop`.
   - **No class-level `@classmethod` factories**, no builder pattern, no registry, no module-level singletons. Composition root (Story 1.10) instantiates `TierManager(...)` once and threads the instance through; Nerve (Story 3.5) is the only caller that drives its public methods.
   - `__all__` declares exactly: `HealthCheck`, `TierManager`. The `CapabilityTier` enum (already in `core/types.py`, Story 1.2) and the `TierChanged` event (already in `core/events.py`, Story 1.3) are NOT re-declared here and NOT re-exported from this module — consumers import them from their canonical homes.

2. **Do NOT create or duplicate any of the following — they already exist and are pinned:**
   - `CapabilityTier` StrEnum (FULL / DEGRADED / OFFLINE) — lives in [src/nova/core/types.py:31-41](src/nova/core/types.py#L31-L41) per Story 1.2. Import it; do not redefine with different values, do not add a fourth member, do not change the string serialization.
   - `TierChanged(Event)` frozen dataclass — lives in [src/nova/core/events.py:174-185](src/nova/core/events.py#L174-L185) per Story 1.3. Fields are `previous_tier: CapabilityTier`, `new_tier: CapabilityTier`, `reason: str`, plus the `source="nerve"` and `timestamp` fields inherited from `Event`. Import it; do not redeclare.
   - `ApiUnavailableError(NovaError)` — lives in [src/nova/core/exceptions.py:92-100](src/nova/core/exceptions.py#L92-L100) per Story 1.2. The Claude adapter (future story) catches `anthropic.APIError` / `anthropic.APIStatusError` / network errors and raises this. `TierManager` expects this exception type out of `HealthCheck.ping()` failures; it does NOT re-raise it, does NOT wrap it, does NOT import `anthropic`.
   - `EventBus` — lives in [src/nova/core/events.py:279-358](src/nova/core/events.py#L279-L358) per Story 1.3. `TierManager` accepts an `EventBus` instance via constructor injection and calls `await event_bus.emit(TierChanged(...))` on every transition. Do NOT instantiate a second `EventBus`, do NOT shadow the dispatch logic, do NOT build a parallel observer registry.
   - `ActionType.TIER_CHANGE` — lives in [src/nova/core/types.py:90](src/nova/core/types.py#L90) per Story 1.2 (`action_type = "tier_change"`). Audit-log wiring for tier transitions is Story 1.8's job (`AuditLogger`), NOT this story. Do NOT write to `audit_log` directly from `TierManager`.

3. **`HealthCheck` is a minimal `Protocol` — one method, one contract:**
   ```python
   class HealthCheck(Protocol):
       async def ping(self, *, timeout_seconds: float) -> None: ...
   ```
   - Return value is `None` on success. Any failure raises `ApiUnavailableError` (domain exception — the Claude adapter translates `anthropic.*` exceptions and network/timeout errors at the adapter boundary per project-context.md:40). `TierManager` catches `ApiUnavailableError` specifically — never `Exception`, never `anthropic.*`, never `OSError`.
   - `timeout_seconds` is **keyword-only** and **required**. Callers (including `TierManager.check_now`) MUST pass the value explicitly; there is no default. The keyword-only marker prevents positional-arg confusion with any future `HealthCheck` variant that adds more knobs.
   - **Why a `Protocol` and not a full port file?** Story 1.9 ships the eight **system** ports (`ports/brain.py`, `ports/eyes.py`, `ports/hands.py`, `ports/shield.py`, `ports/voice.py`, `ports/ritual.py`, `ports/skin.py`, `ports/nerve.py`). A Claude-adapter reasoning port is NOT in that list; Voice consumes the Claude adapter as a `reasoning` dependency (architecture.md:1080). `HealthCheck` here is the narrow tier-facing slice of what the Claude adapter will later expose — it is declared inline in `core/tiers.py` as the one method `TierManager` cares about. Structural typing (PEP 544) means the Claude adapter satisfies it without an explicit `class ClaudeAdapter(HealthCheck)` declaration.
   - **Tests inject a mock.** Tests pass a small async double (a class or an async-method class) that implements `ping`. There is no real network, no real Claude adapter, in unit tests. Locked by the isolation check (`tiers_module` does not import `anthropic`, does not import `adapters`).

4. **`TierManager` is the tier state machine. Construction contract:**
   ```python
   class TierManager:
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
       ) -> None: ...
   ```
   - **All parameters are keyword-only** (leading `*`). Positional-arg mistakes are silently ordering-sensitive disasters when a tuning constant moves; keyword-only forecloses the failure mode.
   - `health_check: HealthCheck` — injected per the "no direct adapter imports in core" rule (AC #5 of Story 1.2 / project-context.md:71).
   - `event_bus: EventBus` — injected per project-context.md:74 ("Event bus for inter-system communication").
   - `initial_tier` — defaults to `FULL`. The composition root (Story 1.10) passes `OFFLINE` when `NovaConfig.api_key is None` (per the Story 1.6 cross-story impact table, row 1.7). Passing `DEGRADED` as an initial tier is nonsensical (we have no prior failures) but the API accepts it for symmetry; it is NOT validated against "degraded only reachable via transition". No `TierChanged` is emitted for the initial value — the bus has no subscribers yet at construction time, and Ritual/Skin render the initial state as "current tier" not "transitioned to tier".
   - `recovery_interval_seconds = 60.0` — pinned by epics.md:790 / architecture.md:800 ("periodic health check every 60s"). Float (not int) so tests can pass `0.001` for speed.
   - `health_check_timeout_seconds = 5.0` — pinned by NFR17 ("tier transitions must be detected and communicated within 5 seconds of connectivity change", prd.md:707). Passed through to `health_check.ping(timeout_seconds=...)` on every probe.
   - `degrade_failure_threshold = 2` — pinned by epics.md:788 ("full → degraded (2+ consecutive API failures)") + project-context.md:196 ("Single malformed API response does NOT trigger tier degradation"). The `report_failure` method counts consecutive failures and degrades when `count >= threshold`. Counter resets on `report_success` OR on any successful `check_now`.
   - `offline_health_failure_threshold = 3` — how many consecutive health-check failures in DEGRADED before transitioning to OFFLINE. Architecture.md:799 says "health checks consistently fail"; the story pins `consistently = 3` so the test harness can assert deterministically. If a future arch amendment changes this, the knob is already injected. Tests exercise `threshold=3` by default plus at least one custom-threshold case.
   - `clock: Callable[[], float]` — monotonic clock for recovery-loop scheduling. Defaults to `time.monotonic` (NOT `time.time` — wall-clock drift would break the 60s cadence). Tests inject a controllable fake.
   - `sleep: Callable[[float], Awaitable[None]]` — defaults to `asyncio.sleep`. Tests inject a no-op async sleep (or a sleep that counts invocations) so `run_recovery_loop` doesn't block the test for 60 seconds.
   - **Internal state** (all private, all typed):
     - `_tier: CapabilityTier` — current tier.
     - `_consecutive_operation_failures: int` — counter for the FULL → DEGRADED transition. Incremented by `report_failure`. Reset on `report_success` and on any successful `check_now`.
     - `_consecutive_health_check_failures: int` — counter for the DEGRADED → OFFLINE transition. Incremented by a failed `check_now` when `_tier == DEGRADED`. Reset on any successful `check_now` and on the transition itself.
     - `_lock: asyncio.Lock` — guards every read-modify-write on `_tier` and the two counters. All public methods that mutate state acquire this lock. Single-event-loop or not, an `await event_bus.emit(...)` inside a transition can yield control; without the lock a second caller could observe torn state.
   - **Construction is synchronous** (`__init__` is a plain `def`, not `async def`). State assignment only; no I/O, no event emission, no `create_task`. The composition root wires the manager BEFORE the event bus has any subscribers, so emitting on `__init__` would fire into the void.

5. **Public API on `TierManager` — six methods + one property:**
   - `@property def tier(self) -> CapabilityTier` — synchronous, lock-free read of `_tier`. Returns the current tier. Every system calls this before a cloud-dependent operation per project-context.md:71 ("Check tier state before cloud-dependent operations"). No await, no side effects.
   - `async def report_success(self) -> None` — a cloud operation just succeeded. Resets `_consecutive_operation_failures` to 0. Does NOT trigger a transition on its own (transitions out of DEGRADED/OFFLINE happen via `check_now`, not via opportunistic operation success — architecture.md pins `check_now` as the canonical recovery gate). Idempotent: calling when already at zero is a no-op.
   - `async def report_failure(self, *, reason: str) -> None` — a cloud operation failed with a tolerable failure (e.g., single malformed response, single timeout). Increments `_consecutive_operation_failures`. If `_tier == FULL and _consecutive_operation_failures >= degrade_failure_threshold`: transitions `FULL → DEGRADED` and emits `TierChanged(previous_tier=FULL, new_tier=DEGRADED, reason="2 consecutive API failures")` — the **canonical** reason string, NOT the caller's `reason` argument. If already DEGRADED/OFFLINE: counter still increments (future-proofing for multi-counter telemetry) but NO tier change (DEGRADED → OFFLINE is health-check-driven, not operation-driven; see epics.md:788). The caller-supplied `reason` is **log context only** — it lands on the `logger.warning("operation failure reported", extra={"caller_reason": reason, ...})` record and nowhere else. Never propagates to `TierChanged.reason`, never crosses the event-bus boundary. See AC #7 for the rationale (opaque event contract).
   - `async def report_rate_limit_or_outage(self, *, reason: str) -> None` — an explicit upstream signal (HTTP 429 / 503) per architecture.md:798 ("clear upstream signal"). Bypasses the 2-failure counter — immediately transitions `FULL → DEGRADED` on first call and emits `TierChanged(previous_tier=FULL, new_tier=DEGRADED, reason="rate limit or outage signal")` — the **canonical** reason string, NOT the caller's `reason` argument. Idempotent when already DEGRADED/OFFLINE (logs at DEBUG, does not emit a second event). Caller-supplied `reason` is log-only (same contract as `report_failure`).
   - `async def check_now(self) -> None` — runs `health_check.ping(timeout_seconds=self._health_check_timeout_seconds)` and updates tier based on the outcome. **Never raises `ApiUnavailableError` to its caller** — the exception is caught internally, state is updated, event is emitted (if a transition occurred), and the method returns `None`. Nerve (Story 3.5) treats `check_now()` as a state-updating probe whose only observable side effects are the tier change, the emitted event, and log records.
     - **On success**: resets both counters. If `_tier in (DEGRADED, OFFLINE)`: transitions to FULL and emits `TierChanged(previous_tier=<old>, new_tier=FULL, reason="health check succeeded")`. If already FULL: no-op (no event).
     - **On `ApiUnavailableError` (caught internally)**: if `_tier == DEGRADED`, increments `_consecutive_health_check_failures`; if `count >= offline_health_failure_threshold`, transitions `DEGRADED → OFFLINE` and emits `TierChanged(..., reason="health check consistently failing")`. If `_tier == FULL`: does NOT degrade from a single health-check miss (tolerant-degrade rule — only operation-facing failures increment the FULL→DEGRADED counter). If `_tier == OFFLINE`: no transition, no event, log at DEBUG. The exception is then swallowed — `check_now` returns `None` normally.
     - **Any exception type OTHER than `ApiUnavailableError`** escaping `ping` is a bug in the adapter (adapter failed to translate at its boundary). `check_now` lets it propagate — does NOT swallow, does NOT wrap as `ApiUnavailableError`. The composition root's top-level `except Exception` (Story 1.10) is the safety net. Locked by `test_check_now_propagates_non_domain_exception`. **`TimeoutError` specifically** — whether raised by the `asyncio.wait_for` wrapper around `ping` (when the adapter ignores its `timeout_seconds` arg and the defensive budget trips) OR leaked by an adapter that failed to translate a raw network timeout — falls under this "propagate" rule. It is treated as an adapter-contract violation (the adapter was supposed to translate timeouts to `ApiUnavailableError` per project-context.md:40). Silently swallowing `TimeoutError` would hide that bug class. The `wait_for` wrapper's job is to CAP the damage — converting "infinite lock-hold" into "loud task-death" — not to disguise the signal as a cloud-unavailable event. Locked by `test_check_now_propagates_asyncio_timeout_error`.
     - `check_now` is the "opportunistic" probe per epics.md:790 — Nerve calls it before every cloud-requiring action. It is ALSO what the recovery loop invokes on each tick.
   - `async def run_recovery_loop(self) -> None` — long-lived background coroutine started by Nerve (Story 3.5) via `asyncio.create_task(tier_manager.run_recovery_loop())`. Structure:
     ```
     while True:
         try:
             await self._sleep(self._recovery_interval_seconds)
         except asyncio.CancelledError:
             raise  # project-context.md:49 — never swallow CancelledError
         await self.check_now()
     ```
     - Ticks every `recovery_interval_seconds`. On each tick, awaits `check_now()`, which returns `None` in every domain-expected case (see AC above). No `contextlib.suppress` is needed — `ApiUnavailableError` is swallowed inside `check_now`, not here. Any exception that reaches this call site is necessarily a non-domain adapter bug; the loop lets it propagate and the task ends — the loop dying on unexpected adapter bugs is better than a silently-dead recovery thread.
     - **Runs unconditionally — no "only run when DEGRADED/OFFLINE" branch.** A tick in FULL state calls `check_now` → success → no-op. The cost is one `ping()` per 60s, inside the 5s timeout budget, cheap enough that the conditional complexity isn't worth the flake-window savings.
     - **`asyncio.CancelledError` is re-raised explicitly** per project-context.md:49. Cleanup is allowed (via `try/finally` if needed — none needed here), but cancellation always propagates. Locked by `test_recovery_loop_re_raises_cancelled_error`.

6. **Transition rules — complete state-machine definition (locked by tests):**

   | From | Event | To | Threshold | Emits TierChanged (canonical `reason`) |
   |------|-------|----|-----------|------|
   | FULL | `report_failure` (1st) | FULL | — | NO |
   | FULL | `report_failure` (2nd consecutive) | DEGRADED | `degrade_failure_threshold=2` | YES — `"2 consecutive API failures"` |
   | FULL | `report_rate_limit_or_outage` | DEGRADED | immediate | YES — `"rate limit or outage signal"` |
   | FULL | `report_success` | FULL | resets counter | NO |
   | FULL | `check_now` success | FULL | — | NO |
   | FULL | `check_now` fails | FULL | — | NO (tolerant — operation failures degrade, not health-check flakes) |
   | DEGRADED | `report_success` | DEGRADED | resets operation-failure counter | NO (recovery is health-check-driven, not operation-driven) |
   | DEGRADED | `report_failure` / `report_rate_limit_or_outage` | DEGRADED | — | NO (already degraded) |
   | DEGRADED | `check_now` success | FULL | — | YES — `"health check succeeded"` |
   | DEGRADED | `check_now` fails (1st, 2nd) | DEGRADED | — | NO |
   | DEGRADED | `check_now` fails (3rd consecutive) | OFFLINE | `offline_health_failure_threshold=3` | YES — `"health check consistently failing"` |
   | OFFLINE | `check_now` success | FULL | — | YES — `"health check succeeded"` |
   | OFFLINE | `check_now` fails | OFFLINE | — | NO |
   | OFFLINE | `report_*` | OFFLINE | — | NO |

   **Never emitted**: FULL→FULL, DEGRADED→DEGRADED, OFFLINE→OFFLINE self-events. Exactly one `TierChanged` per genuine transition (epics.md:791 "once per transition, not repeated"). Each row in the table above is a dedicated unit test.

7. **`TierChanged` payload — exact field contract:**
   - `previous_tier: CapabilityTier` — the tier BEFORE the transition completes.
   - `new_tier: CapabilityTier` — the tier AFTER.
   - `reason: str` — short, opaque, non-sensitive, and **strictly drawn from a closed set of canonical strings owned by this module**. The complete set of values `TierManager` is permitted to emit:
     - `"2 consecutive API failures"` — FULL → DEGRADED via `report_failure` threshold hit.
     - `"rate limit or outage signal"` — FULL → DEGRADED via `report_rate_limit_or_outage`.
     - `"health check succeeded"` — DEGRADED → FULL or OFFLINE → FULL via `check_now`.
     - `"health check consistently failing"` — DEGRADED → OFFLINE via `check_now` threshold hit.
     
     **Caller-supplied `reason` arguments to `report_failure` / `report_rate_limit_or_outage` do NOT appear on the event payload.** They are log-only context (carried in `extra={"caller_reason": reason}` on the corresponding `logger.warning` record). Rationale: upstream failure reasons may contain sensitive content (API response bodies, endpoint paths, token fragments, user input echoed back in an error), and the `TierChanged` event is consumed by subscribers that render user-facing strings (Story 5.4 tier notice), write audit rows (Story 1.8), and transport the payload across the event bus. Keeping the event `reason` canonical + closed-set means downstream consumers can safely render it without scrubbing; the detailed upstream context stays in structured logs where the log handler (not the event) is the privacy boundary. Story 5.4 renders `reason` verbatim; this contract keeps that safe by construction.
     
     **Do NOT** put the Claude API response body, error message, HTTP status code raw text, upstream exception string, or caller-supplied `reason` into `TierChanged.reason`. The `from err` chain on `ApiUnavailableError` carries upstream detail; `logger.warning(..., extra={"caller_reason": ...})` captures caller context. Locked by `test_tier_changed_reason_is_canonical` (positive — emitted reason is in the allowed set) and `test_caller_reason_never_appears_on_emitted_event` (negative — even when caller passes a sensitive-looking reason, the emitted event's `reason` field does not contain it).
   - `source: str = "nerve"` — hardcoded by the dataclass (`field(default="nerve", init=False)`). `TierManager` lives in `core/`, not in `systems/nerve/`, but the event's `source` field reflects the **orchestrator** that owns tier state per architecture.md:769 ("Global tier state lives in Nerve"). Do NOT override it to `"core"` or `"tiers"`.
   - `timestamp: str` — ISO 8601 UTC, auto-populated via `_default_timestamp` (Story 1.3 two-function clock pattern). Tests that need a deterministic timestamp monkeypatch `nova.core.events._utc_now_iso` per the Story 1.3 contract ([src/nova/core/events.py:46-58](src/nova/core/events.py#L46-L58)). Do NOT introduce a second clock parameter on `TierManager` for timestamps — `_utc_now_iso` is the project-wide single clock.

8. **Event emission is the LAST step of a transition — not the first.** Ordering in every transition branch:
   1. Update `_tier` (and reset relevant counters).
   2. Build the `TierChanged` instance.
   3. `await self._event_bus.emit(event)`.
   
   Rationale: if a handler raises and the `EventBus` logs-and-continues (Story 1.3 AC), the tier state is already consistent. If step 2 or 3 raises, state is still consistent. Emitting FIRST would let a handler observe a stale tier and make a wrong decision (e.g., Skin rendering "you are FULL" during the OFFLINE transition). The Story 1.3 bus already isolates handler exceptions; `TierManager` does NOT need its own `try/except` around `emit`. Locked by `test_tier_updated_before_event_emitted`.

9. **Constructor injection only — no direct adapter imports in core:** (project-context.md:71, architecture.md:1271, epics.md:794)
   - `core/tiers.py` MUST NOT import `anthropic`, MUST NOT import any `nova.adapters.*`, MUST NOT import any `nova.systems.*`. Locked by the `TIERS_FORBIDDEN_TOPLEVEL_MODULES` frozenset in `test_core_isolation.py` (AC #12).
   - `HealthCheck` is a `Protocol`, not a concrete class. The Claude adapter satisfies it structurally. Tests use handwritten async mocks. `unittest.mock.AsyncMock` is permitted in test code but not required.
   - The composition root (Story 1.10) instantiates: `TierManager(health_check=claude_adapter, event_bus=event_bus)`. The Claude adapter lives outside this story; its construction and the `ping` implementation belong to a later Epic. For T1, the Claude adapter is stubbed/mocked — the tier manager wiring is real.

10. **Concurrency — single asyncio event loop, lock-guarded state:** (project-context.md:37)
    - Every public mutator acquires `self._lock` via `async with self._lock:` for the read-modify-(emit) sequence. The `tier` property reads `self._tier` without the lock (Python attribute reads are atomic at the bytecode level for simple instance attributes).
    - The lock is held during `event_bus.emit(...)`. This serializes transition events — two concurrent `report_failure` calls cannot produce two `TierChanged(FULL→DEGRADED)` events. The emit itself yields to the event loop; other awaiters on the lock block until emit completes. This is intentional: the "once per transition" guarantee (epics.md:791) is lock-enforced.
    - **No `asyncio.create_task` inside `TierManager`.** The recovery loop is a coroutine method; the composition root owns the `create_task` call and the task lifecycle. `TierManager` never spawns tasks itself.
    - Tests that exercise concurrent paths use `asyncio.gather(report_failure(...), report_failure(...))` against a single `TierManager` instance and assert exactly one event was emitted.

11. **Logging — structured, never to terminal:** (project-context.md:128, architecture.md:1250-1263)
    - Module logger: `logger = logging.getLogger("nova.core.tiers")`.
    - Every state transition logs at INFO: `logger.info("tier changed", extra={"previous_tier": str(previous), "new_tier": str(new), "reason": reason})` — where `reason` is the same canonical string emitted on the `TierChanged` event (AC #7 closed set). `str(enum_member)` yields the canonical value per Story 1.2's `StrEnum` contract.
    - `report_failure` and `report_rate_limit_or_outage` log at WARNING: `logger.warning("operation failure reported", extra={"tier": str(self._tier), "caller_reason": caller_reason, "consecutive_failures": self._consecutive_operation_failures, "threshold": self._degrade_failure_threshold})`. **This is the only surface where the caller-supplied `reason` argument lives.** It does not propagate to the event payload (AC #7).
    - Health-check failure via `check_now` logs at WARNING with `extra={"tier": str(current), "consecutive_failures": count, "threshold": threshold}`. No raw exception body in the message; log handlers capture the chained `ApiUnavailableError` via standard `exc_info` handling if needed — but `TierManager` itself does NOT format the exception into the message string.
    - Recovery loop lifecycle logs at DEBUG (start, each tick). Cancellation logs at INFO once and propagates.
    - **No `print()` anywhere** (project-context.md:44, ruff `T20`).
    - **Event `reason` strings are canonical and closed-set (AC #7).** Log `caller_reason` values are whatever the caller passed; the log handler (not this module) is the privacy boundary for log content per project-context.md:129.

12. **`tests/unit/core/test_core_isolation.py` — register `core/tiers.py` as a new isolated module.** Follow the pattern Story 1.6 set for the `config_module` carve-out:
    - Add `import nova.core.tiers as tiers_module` to the alphabetized imports.
    - Add `TIERS_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES` — tiers.py has NO carve-out (does not import `sqlite3`, `yaml`, `anthropic`, `rich`, or any other adapter module). Every entry in the global forbidden set is forbidden here.
    - Add `TIERS_ALLOWED_TOPLEVEL_MODULES: frozenset[str]`: `__future__`, `asyncio`, `collections`, `logging`, `nova`, `time`, `typing`. **No `datetime`** — timestamp generation happens in `core/events.py`, not here. **No `os`** — no filesystem. **No `pathlib`** — no path ops. **No `enum`** — `CapabilityTier` is imported from `nova.core.types`, not redeclared. **No `contextlib`** — `check_now` catches `ApiUnavailableError` with a plain `try/except`, no suppression primitive. **No `dataclasses`** — `TierManager` is a plain class, not a dataclass.
    - Add tests: `test_tiers_forbidden_imports`, `test_tiers_imports_within_allowlist`, `test_tiers_does_not_import_nova_adapters_or_systems`, `test_tiers_does_not_dynamically_import_nova_adapters_or_systems`.
    - Extend the parametrize lists in `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules` to include `tiers_module`.
    - **No change to the global `FORBIDDEN_TOPLEVEL_MODULES` frozenset.**

13. **`src/nova/core/__init__.py` re-export update.** Match the pattern Stories 1.2 / 1.3 / 1.4 / 1.5 / 1.6 set:
    - Add to the import block: `from nova.core.tiers import HealthCheck, TierManager`.
    - Extend `__all__` alphabetically: add `HealthCheck`, `TierManager`. Story 1.6 took the re-export count to 30 names; this story takes it to **32 names**.
    - Alphabetical ordering locked by the existing Story 1.2 monotonic-ordering test.

14. **Quality gate passes clean (Story 1.6 carry-forward):** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0.
    - mypy strict succeeds on `tiers.py`, the modified `core/__init__.py`, `test_tiers.py`, and the modified `test_core_isolation.py`.
    - **No `Any`, no `# type: ignore` in production code.** The `Callable[[], float]` and `Callable[[float], Awaitable[None]]` type hints for `clock` and `sleep` use `collections.abc.Callable` + `collections.abc.Awaitable` (ruff `UP035` enforces this on py312).
    - Repo tree stays clean after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`.
    - **Expected test count delta:** `tests/unit/core/test_tiers.py` adds ~30–40 tests (see AC #16); `test_core_isolation.py` adds 4 tests + 2 parametrize entries. Firm number is whatever the run produces — don't over-fit a target. Prior total: 418 passed + 1 skipped (419 collected) at end of Story 1.6.

15. **No consumer wiring in this story.** Specifically:
    - Do NOT modify `src/nova/app.py` — wiring `TierManager(health_check=claude_adapter, event_bus=event_bus)` into the composition root is Story 1.10's job.
    - Do NOT modify `src/nova/cli.py` — cli startup is also Story 1.10.
    - Do NOT create `src/nova/ports/nerve.py` or any other port file — Story 1.9 owns the port layer.
    - Do NOT create `src/nova/systems/nerve/` — Story 3.5 owns the Nerve system.
    - Do NOT create any Claude adapter or stub. Tests use inline mocks.
    - Do NOT wire `AuditLogger` from here (Story 1.8 creates `AuditLogger`; Story 3.5's Nerve is the caller that logs `ActionType.TIER_CHANGE` when it catches `TierChanged` off the bus). `core/tiers.py` does NOT write to `audit_log`.
    - Do NOT emit a `TierChanged` event on construction for the initial tier. Composition root subscribers are not yet wired; emitting would be into the void. Ritual/Skin read the initial tier via the `tier` property (Story 3.3 / 3.4).
    - Do NOT implement a "capability map per system" (architecture.md:769) — that is per-system branching inside each system's own code (Ritual skips prose in DEGRADED, Voice returns empty in OFFLINE, etc.). This story provides the tier state they branch on; it does not centralize the branching.

16. **Test file `tests/unit/core/test_tiers.py` — coverage expectations (~30–40 tests):**
    - **Shape tests** (~3): `TierManager` instantiation with minimum args succeeds; `HealthCheck` is a `Protocol` with exactly one method named `ping`; `tier` property returns the `initial_tier` passed in.
    - **FULL → DEGRADED transitions** (~5):
      - Single `report_failure` does NOT transition (tolerant-degrade rule; no event emitted; `tier == FULL`). [CRITICAL — locks project-context.md:196.]
      - Second consecutive `report_failure` transitions `FULL → DEGRADED` and emits exactly one `TierChanged` with `reason == "2 consecutive API failures"` (canonical string, NOT the caller's `reason` argument).
      - `report_failure` with a `report_success` in between resets the counter — e.g., fail, fail-but-success-before-second, fail, fail → DEGRADED on the second of the second pair.
      - `report_rate_limit_or_outage` transitions immediately on first call (bypasses counter) and emits `reason == "rate limit or outage signal"` (canonical string).
      - Caller-supplied `reason` argument to `report_failure` / `report_rate_limit_or_outage` appears in the `logger.warning` record's `extra["caller_reason"]` field and is ABSENT from the emitted `TierChanged.reason` field. Locks the "canonical-only event payload" contract.
    - **DEGRADED → OFFLINE transitions** (~4):
      - One failed `check_now` in DEGRADED does NOT transition.
      - Three consecutive failed `check_now` calls in DEGRADED transition to OFFLINE on the third.
      - Custom `offline_health_failure_threshold=5` pushes the transition to the 5th failure, not the 3rd.
      - A successful `check_now` in between resets the counter.
    - **Recovery transitions** (~4):
      - `check_now` success from DEGRADED → FULL, emits `TierChanged(DEGRADED, FULL, "health check succeeded")`.
      - `check_now` success from OFFLINE → FULL, emits `TierChanged(OFFLINE, FULL, ...)`.
      - `check_now` success in FULL → no event, `_consecutive_operation_failures` reset.
      - `check_now` failure in OFFLINE → no event, no transition.
    - **Never-emit-self-event guarantees** (~3):
      - `report_success` in FULL → zero `TierChanged` emits.
      - `report_failure` in DEGRADED → zero emits (already degraded; the DEGRADED→OFFLINE path is health-check-driven only).
      - `report_rate_limit_or_outage` twice in a row from FULL → exactly ONE emit (the second is a no-op on DEGRADED).
    - **`run_recovery_loop` tests** (~4):
      - Loop ticks once, calls `check_now` once, `_sleep` was awaited with `recovery_interval_seconds`.
      - Loop re-raises `asyncio.CancelledError` without swallowing (use `asyncio.create_task` + `task.cancel()` + `await task` asserting `CancelledError` is raised by the awaited task). [CRITICAL — locks project-context.md:49.]
      - Loop continues across multiple ticks when the underlying health check keeps failing — `check_now` swallows the `ApiUnavailableError` internally and returns `None`, so nothing reaches the loop body to interrupt it. Assert state still advances (e.g., DEGRADED → OFFLINE after 3 failed ticks) and the loop is still running.
      - Loop DOES NOT continue after a `check_now` that raised a non-domain exception — the raw exception propagates from `check_now` and the task ends. Locks the "adapter bugs are surfaced, not swallowed" rule AND confirms `check_now` truly swallows only `ApiUnavailableError` (not every exception).
    - **Concurrency tests** (~2):
      - `asyncio.gather(report_failure(...), report_failure(...))` against FULL with threshold=2 produces exactly ONE `TierChanged` event (lock enforces at-most-once).
      - `tier` property is callable from a synchronous context (no await needed), returns current state.
    - **Contract tests — TierChanged payload** (~4):
      - Emitted event has `source == "nerve"` (hardcoded by dataclass).
      - Emitted event has `previous_tier` strictly different from `new_tier`.
      - `test_tier_changed_reason_is_canonical` — every emitted event's `reason` field is a member of the exact set `{"2 consecutive API failures", "rate limit or outage signal", "health check succeeded", "health check consistently failing"}`. Assertion covers all five transition paths in AC #6.
      - `test_caller_reason_never_appears_on_emitted_event` — trigger both `report_failure` (threshold hit) and `report_rate_limit_or_outage` with a sensitive-looking caller `reason` (e.g., `"sk-ant-REDACTED-SECRET via https://api.anthropic.com/v1/messages"`); assert the emitted `TierChanged.reason` does NOT contain the caller substring anywhere (substring search on the full canonical string). Locks the "canonical-only event payload" contract against future edits.
    - **Event-ordering test** (~1): `test_tier_updated_before_event_emitted` — handler inspects `tier_manager.tier` inside the subscribed handler and asserts it matches `new_tier` (not `previous_tier`). Locks AC #8.
    - **`check_now` contract tests** (~4):
      - `test_check_now_swallows_api_unavailable_error` — `HealthCheck.ping` raises `ApiUnavailableError`; `check_now` returns `None` normally (no exception propagates). Covers the FULL / DEGRADED / OFFLINE starting-state cases (parametrized).
      - `test_check_now_updates_state_before_swallowing` — on the 3rd consecutive DEGRADED failure, `check_now` emits `TierChanged(DEGRADED, OFFLINE, ...)` AND returns `None` — asserting both the side effect fires and no exception escapes.
      - `test_check_now_propagates_non_domain_exception` — `HealthCheck.ping` raises a `RuntimeError`; `check_now` does NOT swallow it, does NOT wrap as `ApiUnavailableError`. Locks the narrow-suppression rule: only `ApiUnavailableError` is caught.
      - `test_check_now_passes_timeout_to_ping` — fake `HealthCheck` records its `timeout_seconds` arg; assert it equals `health_check_timeout_seconds` from construction.
    - **Deterministic clock and async sleep** — no test calls real `asyncio.sleep` with a positive value. Every test injects a `sleep` that either returns immediately or counts invocations. Locks "tests use a deterministic clock and mock health check — no wall-clock or network dependencies" (epics.md:796).
    - **Each test is `async def test_...(...)` where `asyncio` is exercised.** Use pytest-asyncio auto mode (pyproject.toml already enables this).
    - **Helper factories** (top of test file, not in conftest per Story 1.5/1.6 precedent):
      - `_make_manager(...) -> TierManager` — builds a `TierManager` with a recording `EventBus` and a programmable mock `HealthCheck`. Returns both the manager and its collaborators so tests can introspect.
      - `_RecordingEventBus` — subclass of `EventBus` (or a simple wrapper) that records every emitted event in order. Do NOT mock `EventBus`; use the real class per "integration-style unit tests" precedent.
      - `_ProgrammableHealthCheck` — implements `HealthCheck`; configurable sequence of outcomes (success / ApiUnavailableError / custom exception).

17. **Cross-story impact reference (for reviewers — not consumed in code):**

    | Consumer story | Uses from this story | Why |
    |---|---|---|
    | 1.8 Audit Logger | `TierChanged` event on the bus → `ActionType.TIER_CHANGE` audit row | Nerve subscribes to `TierChanged` and calls `AuditLogger.log_action(...)` — the tie-in is Story 3.5 wiring, not this story. |
    | 1.10 Composition root & CLI entrypoint | `TierManager(health_check=claude_adapter, event_bus=event_bus, initial_tier=OFFLINE_if_no_api_key)` | Composition root is the only place `TierManager` is instantiated. Initial tier decision based on `NovaConfig.api_key`. |
    | 3.4 T1 command grammar | `tier_manager.tier` check before cloud-requiring commands | Nerve checks tier before routing a command that needs Claude. |
    | 3.5 Nerve command routing | `tier_manager.report_*`, `check_now`, `run_recovery_loop` (via `create_task`) | Nerve is the single driver of the public API. |
    | 4.5 Memory accumulation | `tier_manager.tier` check before Brain → PromptBuilder → Claude | Brain falls back to local-only if tier is OFFLINE. |
    | 5.4 Tier status display | `TierChanged` event on the bus + `tier_manager.tier` | Skin renders the once-on-change tier notice. |
    | 7.1 Voice system | `tier_manager.tier` | Voice returns empty/cached in DEGRADED/OFFLINE. |
    | 8.1–8.5 Tier integration testing | All of the above, exercised end-to-end | Integration tests for the three-tier behavior. |

    **Eight downstream stories** consume Story 1.7's primitives.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/tiers.py` — `HealthCheck` Protocol + `TierManager` class** (AC: #1–#11)
  - [x] Module docstring: purpose (capability tier state machine), pins the tolerant-degrade model from architecture.md:765–813 + epics.md:776–796, cites NFR17 (5-second detection window) + project-context.md:71/196 (tier-check-before-cloud, single-malformed-response-does-not-degrade).
  - [x] `from __future__ import annotations`.
  - [x] Imports (exact — the isolation-test allowlist matches these): `asyncio`, `logging`, `time`, `collections.abc.Awaitable`, `collections.abc.Callable`, `typing.Protocol`. First-party: `nova.core.events.EventBus`, `nova.core.events.TierChanged`, `nova.core.exceptions.ApiUnavailableError`, `nova.core.types.CapabilityTier`. (`contextlib` dropped during dev — suppression is a plain `try/except ApiUnavailableError` inside `check_now`; allowlist narrowed to match.)
  - [x] Module-level `logger = logging.getLogger("nova.core.tiers")`.
  - [x] `HealthCheck(Protocol)` with single method `async def ping(self, *, timeout_seconds: float) -> None`.
  - [x] `TierManager` class per AC #4–#11. Keyword-only constructor, all defaults explicit, `asyncio.Lock` initialized in `__init__`.
  - [x] Public methods: `tier` (property), `report_success`, `report_failure`, `report_rate_limit_or_outage`, `check_now`, `run_recovery_loop`.
  - [x] Private helpers: `_transition_to(new_tier, reason)` (asserts `new_tier != self._tier`, resets relevant counters, emits event, logs INFO) — consolidates the 3 transition call sites so the "update-then-emit" order (AC #8) is enforced in exactly one place. Also `_handle_health_check_success` / `_handle_health_check_failure` split out of `check_now` so the method itself stays a thin dispatcher (acquire lock → probe → dispatch).
  - [x] `__all__ = ["HealthCheck", "TierManager"]`.

- [x] **Task 2: Update `src/nova/core/__init__.py` — re-export `HealthCheck` and `TierManager`** (AC: #13)
  - [x] Add `from nova.core.tiers import HealthCheck, TierManager` to the import block (alphabetized).
  - [x] Extend `__all__` alphabetically: add `HealthCheck`, `TierManager`.
  - [x] Verify: `from nova.core import TierManager, HealthCheck` resolves (import exercised indirectly via the re-export and every tier test).

- [x] **Task 3: Extend `tests/unit/core/test_core_isolation.py` — register tiers.py as a fully-isolated core module** (AC: #12)
  - [x] Alphabetized import: `import nova.core.tiers as tiers_module`.
  - [x] `TIERS_FORBIDDEN_TOPLEVEL_MODULES = FORBIDDEN_TOPLEVEL_MODULES` (no carve-out).
  - [x] `TIERS_ALLOWED_TOPLEVEL_MODULES = frozenset({"__future__", "asyncio", "collections", "logging", "nova", "time", "typing"})` — 7 entries (trimmed `contextlib` and `dataclasses` from the story's draft since the implementation uses neither).
  - [x] Add `test_tiers_forbidden_imports`, `test_tiers_imports_within_allowlist`, `test_tiers_does_not_import_nova_adapters_or_systems`, `test_tiers_does_not_dynamically_import_nova_adapters_or_systems`.
  - [x] Extend the `test_no_relative_imports` parametrize list + the `test_no_dynamic_imports_of_forbidden_modules` parametrize list to include `tiers_module`. Added a `tiers_module is X` branch in `test_no_dynamic_imports_of_forbidden_modules` that selects `TIERS_FORBIDDEN_TOPLEVEL_MODULES` (== full global set).

- [x] **Task 4: Author `tests/unit/core/test_tiers.py` — ~30–40 tests per AC #16** (AC: #16)
  - [x] Header: `from __future__ import annotations`, imports matching Story 1.4/1.5/1.6 conventions.
  - [x] Helpers at top of file (not in conftest): `_RecordingEventBus`, `_FakeHealthCheck` (renamed from `_ProgrammableHealthCheck` — programmable via `responses` list + `default_response` fallback), `_make_manager(...)` returning `(manager, bus, health_check)` 3-tuple.
  - [x] Every test `async def test_...(...)` returns `None`. Auto-mode asyncio (no `@pytest.mark.asyncio` decorators).
  - [x] No fixtures added to `tests/conftest.py`.
  - [x] AST-level guardrail `test_tiers_test_file_uses_no_real_sleep_or_network` walks the test file's own AST and asserts: no `time.sleep` calls, no `asyncio.sleep(n)` for positive `n` (allows `asyncio.sleep(0)` yield-to-loop idiom), no `anthropic` imports. Carry-forward from Story 1.6 review lesson: AST inspection > text regex for forbidden-pattern guards.

- [x] **Task 5: Full verify run** (AC: #14)
  - [x] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0.
  - [x] Test count: **419 → 462** (+43): 37 new `test_tiers.py` tests + 4 new isolation tests + 1 AST guard + 1 parametrized three-way test expansion counted as 3. Actual pytest line: `461 passed, 1 skipped in 3.83s`.
  - [x] `git status` clean — only intentional edits (tiers.py, test_tiers.py, test_core_isolation.py, core/__init__.py, sprint-status.yaml, story file). No stray caches, backups, or DBs.

- [x] **Task 6: Sprint status + commit** (AC: #14, post-implementation)
  - [x] Update `_bmad-output/implementation-artifacts/sprint-status.yaml` → `1-7-capability-tier-state-machine: in-progress` on dev start, `review` on handoff.
  - [ ] Commit message (Story 1.4/1.5/1.6 style): `"Story 1.7: capability tier state machine (core/tiers.py)"` — to be applied by the user after review approval.

### Review Findings (2026-04-15)

**Code review summary:** 3 adversarial layers (Blind Hunter, Edge-Case Hunter, Acceptance Auditor). 1 decision-needed, 6 patches, 3 deferred, 9 dismissed.

- [x] [Review][Decision] **`check_now` holds `asyncio.Lock` across the `ping()` await** — Resolved 2026-04-15: **option (a) — keep single-lock design.** Rationale: worst-case stall is ~5s every 60s (~8% window), predictable and test-deterministic; flap risk in option (b) and extra primitive in option (c) both trade simplicity for marginal gain. F13 patch (`asyncio.wait_for` wrapper around `ping`) caps the worst case so an adapter that ignores its `timeout_seconds` argument cannot hold the lock longer than configured. No code change from this decision beyond F13.
- [x] [Review][Patch] **Constructor does not validate threshold / interval bounds** [src/nova/core/tiers.py:__init__] — Applied 2026-04-15. `__init__` now raises `ValueError` for `recovery_interval_seconds <= 0`, `health_check_timeout_seconds <= 0`, `degrade_failure_threshold < 1`, `offline_health_failure_threshold < 1`. One boundary test per knob (`test_init_rejects_zero_*`) locks the checks.
- [x] [Review][Patch] **`_transition_to` uses `assert` for a critical invariant — stripped under `python -O`** [src/nova/core/tiers.py:_transition_to] — Applied 2026-04-15. Replaced the `assert` with `if new_tier is self._tier: raise RuntimeError(...)` so the self-transition invariant survives `python -O` / `PYTHONOPTIMIZE=1`.
- [x] [Review][Patch] **No defensive timeout around `ping()` — adapter that ignores `timeout_seconds` can hold the lock forever** [src/nova/core/tiers.py:check_now] — Applied 2026-04-15, then corrected the same day after a follow-up review flagged a contract regression. `check_now` wraps `self._health_check.ping(...)` in `asyncio.wait_for(..., timeout=self._health_check_timeout_seconds)` BUT only catches `ApiUnavailableError`. `TimeoutError` (from the wrapper tripping OR from the adapter leaking a raw network timeout) propagates per AC #5's "only ApiUnavailableError is swallowed" rule — surfacing adapter-translation bugs instead of silently relabeling them as cloud-unavailable signals. The `wait_for` wrapper still caps the damage (converts infinite lock-hold into loud task-death at the composition-root top-level handler). Locked by `test_check_now_propagates_asyncio_timeout_error`.
- [x] [Review][Patch] **`test_recovery_loop_re_raises_cancelled_error` has a scheduling race** [tests/unit/core/test_tiers.py:test_recovery_loop_re_raises_cancelled_error] — Applied 2026-04-15. Replaced `await asyncio.sleep(0)` with an `asyncio.Event` (`entered_sleep`) set inside the fake sleep; the test now `await entered_sleep.wait()` for a deterministic synchronization point before cancelling.
- [x] [Review][Patch] **Canonical reason strings duplicated between module and tests** [tests/unit/core/test_tiers.py] — Applied 2026-04-15. Test file now imports `_REASON_OPERATION_THRESHOLD`, `_REASON_RATE_LIMIT_OR_OUTAGE`, `_REASON_HEALTH_CHECK_OK`, `_REASON_HEALTH_CHECK_FAILING` directly from `nova.core.tiers`; the local `_CANONICAL_REASONS` frozenset is built from the imported constants so a drift in production strings fails tests immediately.
- [x] [Review][Patch] **AC #6 row "FULL + `check_now` fails → FULL, no event" lacks dedicated state-invariance assertions** [tests/unit/core/test_tiers.py:test_check_now_swallows_api_unavailable_error] — Applied 2026-04-15. Extended the parametrized test with `assert manager.tier is start_tier` and `assert bus.events == []` so all three start-tier cases (FULL / DEGRADED / OFFLINE) lock the "single health-check failure never transitions" invariant.

- [x] [Review][Defer] **Canonical reason string `"2 consecutive API failures"` hardcodes the default threshold** — if an operator sets `degrade_failure_threshold=5`, the emitted reason still says "2 consecutive", misleading Story 5.4's renderer. Closed canonical set is a load-bearing AC #7 contract; interpolating the threshold would break the closed-set guarantee. Not exercised in T1 (no consumer sets a custom threshold). **Target:** whichever story first ships an operator-facing threshold knob. Fix options then: (1) validate `degrade_failure_threshold == 2` at construction and require a story-level schema change to widen; (2) expand the canonical set to include threshold-parameterized variants as a deliberate schema update.
- [x] [Review][Defer] **`CancelledError` mid-`event_bus.emit` leaves tier advanced with no observer event** — Tier state is already mutated when `emit` begins; if the coroutine is cancelled inside `emit`, downstream never learns of the transition. Edge case during process shutdown; the composition root is tearing down subscribers at the same time. **Target:** Story 3.10 (Crash Recovery) or Story 8.3 (Tier Recovery & Catch-up Briefing). Fix options then: `asyncio.shield` around `emit`, or a catch-up replay on next boot that reconciles last-known-tier vs. current-state.
- [x] [Review][Defer] **`test_emitted_event_has_source_nerve` couples to `TierChanged.source` default rather than a behavior `TierManager` owns** — The test passes because `TierChanged(source="nerve", init=False)` is fixed by the event class, not because `TierManager` sets it. If a future event-schema change alters the default, the test fails in a way that points at `TierManager` instead of the real cause. Test-quality polish, not a functional regression. **Target:** next test-hygiene pass.

**Dismissed (not noise-worthy to log in-story, but recorded for trace):** `str(CapabilityTier.FULL)` returns `"full"` (StrEnum overrides `__str__`) — false positive about log format; counter unbounded in DEGRADED/OFFLINE — AC #5 explicitly allows; WARNING log level on `report_failure` — AC #11 pins WARNING; counter reset masks flapping — tolerant-degrade design intent; op counter not reset on DEGRADED→OFFLINE — correct behavior (reset on recovery to FULL); `report_rate_limit_or_outage` in DEGRADED idempotent — AC #5 explicit; no TierChanged on construction — AC #4 explicit; no recovery-loop reentrancy guard — AC #15 composition-root-only rule; EventBus.emit raises — Story 1.3 catches handler exceptions by contract.

---

## Dev Notes

### Story Type: Foundational infrastructure — the tier state machine

This story ships the **only** tier state machine in N.O.V.A. Every other system (Brain, Voice, Ritual, Nerve, Skin) reads the tier via `tier_manager.tier` before a cloud-dependent decision, and every system that detects a cloud failure calls back into `report_failure` / `report_rate_limit_or_outage`. No system owns a parallel tier variable; no system guesses its own tier locally. Enforcement: `core/tiers.py` is the only owner; cross-story references to "the tier" route through the injected `TierManager`.

The story ALSO materializes the `HealthCheck` protocol that the Claude adapter will later satisfy structurally. This is the "narrow interface at the core boundary" per the ports-and-adapters rule (project-context.md:62): `core/` declares exactly what it needs (one `async ping`); `adapters/` implements that + whatever else the adapter does for Voice / Brain.

### Scope guard (hard stop)

- **Do NOT touch `app.py`, `cli.py`, or the composition root.** Wiring is Story 1.10. This story delivers the module + tests.
- **Do NOT create any Claude adapter.** The Claude adapter is a later Epic's concern. Tests use inline mocks.
- **Do NOT create `ports/nerve.py`, `ports/brain.py`, or any port file.** Story 1.9 owns the port layer.
- **Do NOT create `systems/nerve/`.** Story 3.5 owns the Nerve system.
- **Do NOT write to `audit_log` directly from `TierManager`.** `AuditLogger` is Story 1.8. Nerve (Story 3.5) is the subscriber that turns `TierChanged` events into audit rows.
- **Do NOT emit a `TierChanged` on construction** even if `initial_tier != FULL`. The event bus has no subscribers at construction time; the initial tier is read via the `tier` property by Ritual/Skin on their first render.
- **Do NOT implement tier-aware behavior in any consuming system.** This story provides the tier state; "Brain returns read-only in OFFLINE", "Voice returns empty in OFFLINE", "Ritual skips prose in DEGRADED" all belong to their own stories (Epic 3/4/7).
- **Do NOT implement a user-facing tier notice.** Story 5.4 owns the notice rendering; this story ships the event + the log record it consumes.
- **Do NOT add a "tier history" buffer, event replay, or tier-change metrics.** T1 is stateless-between-transitions — once emitted, the `TierChanged` event is gone (Story 1.3 contract: no event persistence). Audit rows (Story 1.8) capture history if needed.
- **Do NOT add a tier-forcing CLI command** (`nova tier force offline`). The tier is derived state; forcing it hides bugs. A future debug shim may expose this, but not in T1.
- **Do NOT implement circuit-breaker sophistication** — exponential backoff, jittered retries, sliding-window failure counts, per-endpoint tier. The spec is "2 consecutive failures" + "3 consecutive health-check failures" + "60s periodic recovery". Anything richer is scope creep and un-specced behavior.
- **Do NOT persist tier state across restarts.** Every process boot starts at `initial_tier` (FULL or OFFLINE based on `api_key`). No disk file, no SQLite row, no cache. "Events exist only in-flight" (Story 1.3) applies by extension to derived state.
- **If `tiers.py` grows past ~250 lines of production code, you are over-building.** Target: ~60 lines for `HealthCheck` + class skeleton, ~120 lines for the 6 methods + `_transition_to` helper, ~30 lines for docstrings. ~200–220 production lines total.

### Critical constraints and gotchas

- **Single malformed API response does NOT degrade.** Project-context.md:196 pins this explicitly. The `FULL → DEGRADED` transition requires `_consecutive_operation_failures >= 2`. A lone `report_failure` leaves `_tier == FULL` with `counter == 1`. This is THE single most important behavior in the story; the dedicated test `test_single_failure_does_not_degrade` is load-bearing. LLMs frequently "helpfully" fire the transition on the first failure — the test locks out the regression.
- **`report_success` does NOT recover from DEGRADED/OFFLINE.** Only `check_now` (health-check-driven) restores up-tier. Architecture.md pins the recovery gate as the health check, not operational success. LLM failure mode: "user just had a successful operation, so we must be FULL again" — wrong; a single successful operation in DEGRADED might be the local-fallback path, not actual cloud recovery.
- **`report_failure` in DEGRADED does NOT drop to OFFLINE.** The DEGRADED → OFFLINE transition is health-check-driven (3 consecutive failed `check_now`). An operation-facing failure while already DEGRADED is already-degraded behavior; counting it toward OFFLINE would triple-count (operation fails, then falls back locally, then user retries, then fails again — all three increment a hypothetical counter that then trips OFFLINE for what is really one upstream hiccup).
- **`asyncio.CancelledError` MUST propagate.** Project-context.md:49 is explicit: "Never swallow `asyncio.CancelledError`." Cleanup is permitted via `try/finally`, but the exception re-raises. The recovery loop's `try/except asyncio.CancelledError: raise` is mandatory. A bare `except Exception: ...` that accidentally catches `CancelledError` (which used to inherit from `Exception` pre-3.8) is a bug — in 3.12 `CancelledError` inherits from `BaseException`, so a blind `except Exception:` WOULD skip it, but the recovery loop catches NOTHING broadly at all: `ApiUnavailableError` is already swallowed inside `check_now`, and the loop body has no surrounding `except` over the `check_now` call site. Test: `test_recovery_loop_re_raises_cancelled_error`.
- **`time.monotonic` not `time.time`.** Wall-clock time is subject to NTP corrections, DST changes (on non-UTC time_t systems), and user-initiated clock changes. A 60s interval measured across an NTP step can become 60s - 2min = negative, which `asyncio.sleep` interprets as "sleep forever on a clamp" (it doesn't — but the scheduling is broken either way). Monotonic clock is the right primitive for intervals.
- **Keyword-only arguments via `*` marker.** Python's keyword-only syntax (`def f(*, a, b):`) forecloses positional-arg mistakes. `TierManager(health_check, event_bus)` (positional) is a call-site trap when the next author adds `initial_tier` in between; keyword-only (`TierManager(health_check=..., event_bus=...)`) is call-site-safe. This project's precedent: Story 1.3 events use kw-only for `timestamp`; Story 1.4 storage engine uses kw-only for some params.
- **`Protocol` + structural typing.** `HealthCheck` does NOT require the Claude adapter to inherit from it. `class ClaudeAdapter:` with an `async def ping(self, *, timeout_seconds: float) -> None` method satisfies the Protocol structurally. This is how ports-and-adapters works in Python; do NOT add `class ClaudeAdapter(HealthCheck)` explicit inheritance — it's unnecessary and couples the adapter to this module.
- **`ApiUnavailableError` is swallowed inside `check_now`, NOT in the recovery loop.** The public contract of `check_now` is "state-updating probe that returns `None`"; it catches `ApiUnavailableError` with a narrow `try/except ApiUnavailableError:` around the `ping` + state-update block and returns normally. The recovery loop does NOT wrap the `check_now` call in its own `except` — any exception that reaches the loop is, by construction, a non-domain adapter bug and should end the task. This single-owner-of-suppression rule prevents the double-swallow / wrong-layer-catches-the-wrong-thing bug class. Locked by `test_check_now_swallows_api_unavailable_error` + `test_check_now_propagates_non_domain_exception` together.
- **`asyncio.Lock` acquire-before-emit.** The lock is held during `event_bus.emit(TierChanged(...))`. Two concurrent `report_failure` calls serialize on the lock; the second call observes the already-updated tier on entry and sees it's not FULL anymore (via `if self._tier != FULL: return`), so it no-ops. Without the lock, both could observe `FULL` and both attempt the transition, producing two events. Locked by `test_concurrent_failures_emit_exactly_one_event`.
- **`TierChanged.source = "nerve"` is hardcoded by the event class.** It is NOT "core" or "tiers" — the field reflects the architectural owner (Nerve) per architecture.md:769, not the file location. `field(default="nerve", init=False)` means you cannot even pass `source="core"` to the constructor. Don't "fix" this.
- **`str(CapabilityTier.FULL) == "full"`** — StrEnum from Story 1.2 makes `str(member)` yield the canonical value without `.value` boilerplate. Use `str(tier)` in log `extra` and in any string-formatted context; `.value` is unnecessary noise and `f"{tier}"` works identically.
- **Tests MUST NOT use real `asyncio.sleep`, real `time.monotonic`, real `anthropic`, or real network.** Epics.md:796 pins this: "tests use a deterministic clock and mock health check — no wall-clock or network dependencies." `_ProgrammableHealthCheck` is inline code in the test file; `_make_manager` injects a `sleep` that increments a counter without actually sleeping. The AST gate `test_tiers_test_file_uses_no_real_sleep_or_network` enforces this at test-file-structure level (Story 1.6 Debug Log carry-forward: AST inspection > text regex for this kind of guard).
- **No Any, no cast, no # type: ignore in production code.** Story 1.6's YAML narrowing is the only case in the codebase where `cast` is allowed — there's no analogous boundary here. Every type is concrete: `CapabilityTier` enum, `int` counters, `float` intervals, `asyncio.Lock`, `EventBus`, `HealthCheck` Protocol. mypy strict passes without any escapes.
- **`Callable[[], float]` for `clock`, not `Callable[[], int]` or `Callable[[], Any]`.** `time.monotonic` returns `float`; the type hint matches. Tests inject a `lambda: 0.0` or a closure-based fake-clock — both are `float`-returning. Don't widen to `Any`.
- **`logger.info("tier changed", extra={...})` uses the structured-logging pattern.** Free-form interpolation (`logger.info(f"tier changed from {prev} to {new}")`) is forbidden by project-context.md:128. The `extra` payload becomes `LogRecord` attributes accessible via `getattr(record, "previous_tier")`. Story 1.5 and Story 1.6 established this pattern.
- **`str.startswith(("http://", "https://"))`** — N/A here (no URLs).
- **`os` is NOT imported.** Nothing in this module touches the filesystem, environment variables, or paths. If `os` appears during implementation, `test_tiers_imports_within_allowlist` fires; the fix is to remove the import, not to widen the allowlist.

### Repo shape at time of this story

After Stories 1.0–1.6 the repo contains:

- `src/nova/core/__init__.py` (re-exports 30 names; this story takes it to 32)
- `src/nova/core/events.py` — `Event`, `EventBus`, `TierChanged`, `ContextChanged`, `SessionStarted`, `SessionEnded`, `SeedSaved`, `ModeRestored`, `AppLaunched`, `MemoryForgotten` (Story 1.3)
- `src/nova/core/exceptions.py` — `NovaError`, `StorageError`, `ConfigError`, `ApiUnavailableError`, `ModeNotFoundError`, `AdapterError` (Story 1.2)
- `src/nova/core/types.py` — `CapabilityTier`, `BriefingState`, `SnapshotType`, `ActionType`, `MemoryCategory`, `BluntnessLevel` (Story 1.2)
- `src/nova/core/config.py` — `load_config` + 6 frozen dataclasses (Story 1.6)
- `src/nova/core/storage/engine.py` + migrations (Stories 1.4–1.5)
- `src/nova/core/tiers.py` does NOT exist yet — this story creates it
- `src/nova/{app,cli}.py` are Story 1.1 placeholders — NOT touched here
- `src/nova/adapters/*`, `src/nova/systems/*`, `src/nova/ports/*`, `src/nova/setup/*` are empty package shells
- `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py`, `test_events.py`, `test_storage_engine.py`, `test_migration_runner.py`, `test_config.py` exist
- No `tests/unit/core/test_tiers.py` — this story creates it
- Tests pass: 418 at Story 1.6 end + 1 skipped (419 collected)

This story **adds**:

- `src/nova/core/tiers.py` (new — `HealthCheck` Protocol + `TierManager` class)
- `tests/unit/core/test_tiers.py` (new — ~30–40 tests per AC #16)

This story **modifies**:

- `src/nova/core/__init__.py` — add 2 re-exports (`HealthCheck`, `TierManager`), alphabetized
- `tests/unit/core/test_core_isolation.py` — add `tiers_module` allowlist frozenset + 4 tests + 2 parametrize-list extensions
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story lifecycle transitions

This story does NOT modify:

- `pyproject.toml` (no new deps; all needed imports are stdlib or already-present first-party)
- `src/nova/app.py`, `src/nova/cli.py`
- Any file under `config/`, `adapters/`, `systems/`, `ports/`, `setup/`
- `docs/config-schemas.md`
- `src/nova/core/events.py` (TierChanged is already there from Story 1.3)
- `src/nova/core/types.py` (CapabilityTier is already there from Story 1.2)
- `src/nova/core/exceptions.py` (ApiUnavailableError is already there from Story 1.2)

### Previous Story Intelligence — Story 1.6 (done 2026-04-14)

Story 1.6 landed the single YAML config reader. Key carry-forwards for Story 1.7:

- **Test file placement — `tests/unit/core/test_tiers.py`, flat under `unit/core/`.** Mirrors `test_config.py`, `test_events.py`, `test_migration_runner.py`. No subdirectory, no `__init__.py`.
- **Helper factories at top of test file, not in conftest.** `_RecordingEventBus`, `_ProgrammableHealthCheck`, `_make_manager` are module-level functions/classes in the test file. No fixtures added to `tests/conftest.py` (Story 1.4/1.5/1.6 precedent — conftest stays minimal).
- **Structured-logging `extra={...}` pattern.** Every `logger.info` / `logger.warning` carries a typed dict in `extra=`. Free-form interpolation is forbidden. `caplog.records[i].previous_tier` is the test assertion style.
- **Opaque messages — schema-level, not data-level.** `TierChanged.reason = "health check consistently failing"` not `"ping to https://api.anthropic.com/v1/messages timed out after 5.0s at 2026-04-15T04:12:33Z"`. Detail lives in the `from err` chain / log `extra`, not in user-visible event fields.
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `SIM105` (prefer `contextlib.suppress`) does NOT fire on this module because the only domain-exception catch is inside `check_now` and does real work in the `except` body (updating state via `_handle_health_check_failure`) — `SIM105` only fires on `try/except/pass` (empty handler). `B008` (function call in default argument) does NOT fire on `default=time.monotonic` / `default=asyncio.sleep` because those are module-level attribute references, not calls.
- **mypy strict, zero `# type: ignore` in production code.** Story 1.6's single `cast` was a YAML boundary; this module has no such boundary. Everything narrows naturally.
- **Commit convention (Story 1.4/1.5/1.6 carry-forward):** terse, imperative, story ID prefix. Expected: `"Story 1.7: capability tier state machine (core/tiers.py)"`.
- **AST-based static-analysis tests, not text regex.** Story 1.6 Debug Log carry-forward explicit: "static-analysis tests that check for forbidden call patterns should walk the AST (`ast.walk` + `ast.Call` inspection), not grep source text — text regex false-positives on docstrings and comments." The "no real sleep / no network in test file" gate follows this rule.
- **`CONFIG_FORBIDDEN_TOPLEVEL_MODULES` / `CONFIG_ALLOWED_TOPLEVEL_MODULES` pattern** is the template for `TIERS_FORBIDDEN_TOPLEVEL_MODULES` / `TIERS_ALLOWED_TOPLEVEL_MODULES`. Story 1.6's carve-out was `- {"yaml"}`; Story 1.7 has NO carve-out (tiers.py imports nothing from the forbidden set).

### Git Intelligence — last 5 commits

```
ba24622 Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)
c64849c Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
7278eb9 Story 1.3: event bus + typed event classes (core/events.py)
ac1790c Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow.
- **"New core module" pattern established by Stories 1.3 / 1.4 / 1.5 / 1.6.** Every new core module ships with: the production file, a dedicated test file under `tests/unit/core/`, an isolation-test carve-out frozenset in `test_core_isolation.py`, and a re-export entry in `core/__init__.py`. Story 1.7 follows the same shape — the only module-level difference is NO carve-out from the forbidden-imports set (tiers.py has no adapter-boundary imports; `{sqlite3}` and `{yaml}` carve-outs both relax a forbidden set, but tiers.py needs no relaxation).
- **No prior `tiers.py` or `test_tiers.py`** in the tree. Greenfield for this story.

### Latest Tech Information (as of 2026-04-15)

- **Python 3.12.x** — `asyncio.Lock`, `asyncio.sleep`, `asyncio.CancelledError` are stable. `CancelledError` inherits from `BaseException` (since 3.8), so `except Exception` does NOT catch it. Explicit `except asyncio.CancelledError: raise` is the idiomatic marker for "I know this exists and I'm re-raising it."
- **`asyncio_mode = "auto"` in pyproject.toml** — pytest-asyncio auto-mode means every `async def test_*` is automatically run as asyncio without `@pytest.mark.asyncio`. Matches the style used in `test_events.py` (Story 1.3), `test_storage_engine.py` (Story 1.4), `test_migration_runner.py` (Story 1.5).
- **`collections.abc.Callable` / `collections.abc.Awaitable`** are the PEP 585 canonical homes (since 3.9). `ruff UP035` enforces this on py312; do NOT import `Callable`/`Awaitable` from `typing`.
- **`typing.Protocol` (PEP 544)** — structural typing, no explicit inheritance required. Stable since 3.8. `HealthCheck` uses `@runtime_checkable` — NO, do NOT add `@runtime_checkable` here; we don't need `isinstance(adapter, HealthCheck)` at runtime, and `@runtime_checkable` loosens the type check to name-existence (no signature verification). Pure `Protocol` declaration + mypy strict is the right level.
- **`time.monotonic()`** returns `float` seconds from an unspecified starting point; only differences are meaningful. Does not go backward, immune to NTP steps.
- **`dataclasses.dataclass(frozen=True, slots=True)` with inheritance pitfalls** — N/A here (`TierManager` is a plain class, not a dataclass; internal state is mutable by design).
- **`ApiUnavailableError` — inherits from `NovaError`** which in Python's exception hierarchy is `Exception` (via `NovaError(Exception)`). So `except Exception` catches it. `TierManager` uses a narrower `except ApiUnavailableError:` inside `check_now` — deliberately so, to let non-domain exceptions propagate and surface adapter bugs rather than silently swallowing them alongside the domain failure.

### Project Structure Notes

- **Production file:** `src/nova/core/tiers.py` — path pinned by architecture.md:273 + 1383 ("tiers.py — TierManager — health check, state machine, transitions").
- **Test file:** `tests/unit/core/test_tiers.py` — flat under `unit/core/`, mirrors every other core-module test file.
- **Architecture.md divergence check:** architecture.md:1085 sketches `TierManager(claude_adapter)` (positional). This story's `TierManager(health_check=..., event_bus=...)` is keyword-only and also takes the `EventBus`. The architecture sketch is pseudocode; the keyword-only + event-bus-injected form is the pinned production shape. Any reader comparing the two should treat the keyword-only constructor as the source of truth.
- **Integration test file `tests/integration/test_tier_transitions.py`** (architecture.md:1426) is NOT this story's concern. Story 8.1–8.5 ship integration coverage across all three tiers. This story's unit tests fully cover the state machine in isolation; integration tests layer on real Nerve + real Ritual + mocked adapter.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio (auto mode, already enabled) + pytest-cov. All tests in this story are `async def` because `TierManager`'s mutating methods are `async`.
- **Unit tests** live in `tests/unit/core/test_tiers.py`. ~30–40 tests per AC #16.
- **No integration tests in this story.** Integration coverage is Epic 8.
- **mypy strict** applies to both the production module and the test file. Annotate every async fixture / return type. `-> None` on every test. Helper types: `async def _programmable_ping(self, *, timeout_seconds: float) -> None:` — the `*` matches the `HealthCheck` Protocol.
- **Deterministic clock + mock sleep + mock health check — NO wall-clock, NO network, NO real `asyncio.sleep`.** Epics.md:796 is the rule; the AST-based `test_tiers_test_file_uses_no_real_sleep_or_network` locks it.
- **Each test constructs its own `TierManager` via `_make_manager(...)`.** No shared state, no cross-test contamination.
- **No fixtures added to `tests/conftest.py`** (Story 1.4/1.5/1.6 carry-forward).
- **Coverage target:** 100% of `tiers.py`. Every branch of every method, every edge of the state machine, every counter-reset path.
- **Failure-path coverage — every transition has at least one test:**
  - FULL → DEGRADED via `report_failure` (threshold hit)
  - FULL → DEGRADED via `report_rate_limit_or_outage` (immediate)
  - DEGRADED → OFFLINE via `check_now` (threshold hit)
  - DEGRADED → FULL via `check_now` success
  - OFFLINE → FULL via `check_now` success
  - No-event self-transitions
  - Counter resets on success
  - Concurrent call coalescing (lock-enforced at-most-once event)
  - `CancelledError` propagation through `run_recovery_loop`
  - Non-domain exception propagation through `check_now`

### Critical Don't-Miss Rules (from project-context.md + architecture.md + epics.md)

Carry-forward with rationale for this story:

- **"Check tier state before cloud-dependent operations — never assume full connectivity."** (project-context.md:71, architecture.md:1277) — this story materializes the check surface: `tier_manager.tier`.
- **"Single malformed API response does NOT trigger tier degradation."** (project-context.md:196, epics.md:792, architecture.md:797) — locked by `test_single_failure_does_not_degrade`. THE single most important behavior in the story.
- **"Tier transitions detected and communicated within 5 seconds."** (prd.md:707 / NFR17) — `health_check_timeout_seconds = 5.0` constant; every probe respects the budget. The recovery-loop cadence (60s) is independent of the per-probe timeout.
- **"Tier-change event emitted once per transition; Skin renders once, then silent."** (architecture.md:801, epics.md:791) — `_transition_to` helper is the single emit call site, guarded by the assertion `new_tier != self._tier`; lock enforces serialization. Locked by `test_concurrent_failures_emit_exactly_one_event`.
- **"Never swallow `asyncio.CancelledError`."** (project-context.md:49) — recovery loop explicitly re-raises. Locked by `test_recovery_loop_re_raises_cancelled_error`.
- **"Timeouts required at external boundaries."** (project-context.md:50) — `HealthCheck.ping` requires `timeout_seconds` keyword arg; `TierManager` passes `health_check_timeout_seconds` on every call.
- **"Ports use Protocols/ABCs."** (project-context.md:54) — `HealthCheck` is a `Protocol`.
- **"No `print()` anywhere."** (project-context.md:44; ruff `T20`) — logger only.
- **"Structured logging."** (project-context.md:128) — every log call uses `extra={...}` with typed keys.
- **"Stable serialization only — enums serialize as stable string values."** (project-context.md:56) — `str(CapabilityTier.FULL) == "full"`; log extras use `str(tier)` not `tier.name`.
- **"No Any in application code."** (project-context.md:47) — everything is concrete-typed. No `cast`, no `# type: ignore`.
- **"Absolute imports only."** (project-context.md:43) — `from nova.core.events import EventBus, TierChanged`, `from nova.core.exceptions import ApiUnavailableError`, `from nova.core.types import CapabilityTier`.
- **"No mutable module-level runtime state."** (project-context.md:55) — all state is per-`TierManager`-instance. Module-level has only `logger` + imports.
- **"Adapters may translate, never decide."** (project-context.md:77) — the Claude adapter (future story) translates `anthropic.*` to `ApiUnavailableError`; the tier DECISION is here, in `TierManager`.
- **"Opaque references for sensitive content in exception/log messages."** (project-context.md:176) — `TierChanged.reason` is a canonical, closed-set string owned by this module (AC #7); caller-supplied `reason` arguments never cross the event boundary. Locked by `test_tier_changed_reason_is_canonical` and `test_caller_reason_never_appears_on_emitted_event`.
- **"Schema changes route through a new numbered story."** — `CapabilityTier` membership (FULL, DEGRADED, OFFLINE) is pinned by Story 1.2. This story does NOT add a new tier.

### Project Structure Notes

- Alignment with unified project structure: `core/tiers.py` sits alongside `core/config.py`, `core/events.py`, `core/exceptions.py`, `core/types.py`. All lowest-layer cross-cutting infrastructure. Import path: `from nova.core.tiers import TierManager`.
- No conflicts or variances detected. The module fits the established "core is cross-cutting, no adapters, no systems" shape exactly.

### Cross-story impact (what depends on this story's primitives)

See AC #17 for the consumer table. Eight downstream stories consume `TierManager` or `TierChanged`. The biggest risk vector is Story 3.5 (Nerve) — it is the single driver of the public API and the subscriber that converts `TierChanged` events into `AuditLogger.log_action` calls. Changes to `TierManager`'s public surface after this story reach all eight consumers; keeping the surface minimal (six methods + one property) is load-bearing.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.7: Capability Tier State Machine](../planning-artifacts/epics.md) — canonical AC, lines 776–796.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives, lines 359–890.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 4: Capability Tier Detection and Enforcement](../planning-artifacts/architecture.md) — lines 765–813, the tolerant-degrade model + per-system tier behavior table.
- [Source: _bmad-output/planning-artifacts/architecture.md#Composition Root Convention](../planning-artifacts/architecture.md) — lines 1059–1102, `TierManager(claude_adapter)` construction sketch (positional form; this story refines to keyword-only).
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 273 + 1383, `core/tiers.py` location.
- [Source: _bmad-output/planning-artifacts/architecture.md#Event Bus](../planning-artifacts/architecture.md) — lines 996–1057, event-bus semantics and the typed-event model.
- [Source: _bmad-output/planning-artifacts/prd.md#NFR17](../planning-artifacts/prd.md) — line 707, "Capability tier transitions must be detected and communicated within 5 seconds of connectivity change."
- [Source: _bmad-output/planning-artifacts/prd.md#FR58](../planning-artifacts/prd.md) — three capability tiers with local-ops-never-depend-on-cloud principle.
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 38 (dataclasses), 40 (domain exceptions), 44 (no print), 47 (no Any), 49 (never swallow CancelledError), 50 (timeouts at boundaries), 54 (Protocols/ABCs for ports), 55 (no module-level mutable state), 56 (stable enum serialization), 71 (check tier before cloud), 74 (event bus), 128 (structured logging), 176 (no sensitive content in exceptions/logs), 196 (single malformed response does NOT degrade).
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `CapabilityTier` StrEnum (FULL / DEGRADED / OFFLINE) + `ActionType.TIER_CHANGE`.
- [Source: src/nova/core/events.py](../../src/nova/core/events.py) — `Event` base, `TierChanged` (lines 174–185), `EventBus` (lines 279–358), `_utc_now_iso` + `_default_timestamp` clock pattern.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `ApiUnavailableError` (lines 92–100), `NovaError` base.
- [Source: _bmad-output/implementation-artifacts/1-6-config-loader-and-immutable-novaconfig.md](./1-6-config-loader-and-immutable-novaconfig.md) — prior story. Test file layout, `test_core_isolation.py` carve-out pattern, AST-based static-analysis precedent, commit style.
- [Source: _bmad-output/implementation-artifacts/1-3-event-bus-and-typed-event-definitions.md](./1-3-event-bus-and-typed-event-definitions.md) — event bus + `TierChanged` origin; two-function clock pattern; `source="nerve"` default on TierChanged.
- [Source: _bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md](./1-2-domain-exceptions-and-shared-types.md) — `ApiUnavailableError` + `CapabilityTier` origin; `from err` chaining contract; StrEnum serialization rule.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — the carve-out pattern and AST inspection helpers this story extends.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- Ruff first pass flagged 5 issues: `SIM108` (ternary preferred over if-else in `_FakeHealthCheck.ping`), `E501` (docstring line 100+ chars), three `SIM102` (nested-if flatten in the AST guard test). All resolved by tightening the helper + collapsing nested isinstance checks with `and`-chained conditionals.
- Ruff format applied automatic whitespace adjustments to `src/nova/core/tiers.py` + `tests/unit/core/test_tiers.py` (long-line parameter layouts compacted).
- Mypy strict initially flagged two `comparison-overlap` / `func-returns-value` errors:
  1. `test_custom_offline_threshold_pushes_transition` — after `assert manager.tier is CapabilityTier.DEGRADED`, mypy narrowed the property return to `Literal[DEGRADED]` and flagged the subsequent `is OFFLINE` check. Even `== OFFLINE` stayed narrowed. Fix: read through a widely-typed local `final_tier: CapabilityTier = manager.tier` immediately before the subsequent assert. The explicit annotation widens the narrowed type back to the base enum. Adding a code comment so a future reader doesn't "simplify" the local back out.
  2. `test_check_now_swallows_api_unavailable_error` — assigning `result = await manager.check_now()` when `check_now` returns `None`. Fix: drop the assignment; the implicit assertion is that the `await` raises nothing. Re-read the comment to confirm intent.
- Carry-forward from Story 1.6 review lesson: AST-based static-analysis tests (not text regex) for forbidden-call-pattern guards. The new `test_tiers_test_file_uses_no_real_sleep_or_network` walks `ast.Call` / `ast.Import` / `ast.ImportFrom` nodes inside the test file's own AST.
- Minor spec refinement during implementation (noted here for transparency): story AC #4 listed `clock: Callable[[], float] = time.monotonic` as a construction knob. The implementation keeps the parameter (keyword-only, defaulted to `time.monotonic`) and gives it a single call site in `run_recovery_loop`'s DEBUG log record (`extra={"monotonic_seconds": self._clock()}`) — satisfies "no dead plumbing" while preserving the future-telemetry surface the AC reserves. Tests do not pass `clock=` since the default is correct for every test path.
- Minor allowlist trim relative to the story draft: `CONFIG_ALLOWED_TOPLEVEL_MODULES` precedent includes `dataclasses`; tiers.py uses neither `dataclasses` nor `contextlib` so those were trimmed from `TIERS_ALLOWED_TOPLEVEL_MODULES`. The 7-entry allowlist is the minimum superset of the actual imports (`__future__`, `asyncio`, `collections`, `logging`, `nova`, `time`, `typing`). Future changes that introduce new imports fail the allowlist test and force a deliberate widening.

### Completion Notes List

- Shipped `src/nova/core/tiers.py` (~260 lines including docstrings). Exposes exactly `HealthCheck` (single-method `Protocol`) and `TierManager`. No other names; `__all__` is the public contract.
- Tolerant-degrade model fully implemented and test-locked:
  - `report_failure` requires 2 consecutive hits to transition FULL → DEGRADED. Locked by `test_single_failure_does_not_degrade` — the load-bearing regression gate for project-context.md:196.
  - `report_rate_limit_or_outage` transitions immediately (bypasses the counter).
  - DEGRADED → OFFLINE is health-check-driven only: 3 consecutive failed `check_now` probes. Operation-failure reports in DEGRADED still increment the operation counter (future telemetry surface) but never transition to OFFLINE.
  - Any successful `check_now` from DEGRADED or OFFLINE restores straight to FULL (no DEGRADED intermediate).
- `TierChanged.reason` is canonical-only. The closed set: `"2 consecutive API failures"`, `"rate limit or outage signal"`, `"health check succeeded"`, `"health check consistently failing"`. Caller-supplied `reason` arguments are log-only (in `extra={"caller_reason": ...}`); `test_caller_reason_never_appears_on_emitted_event` locks the contract by passing a sensitive-looking caller string and asserting it does not reach the event payload.
- `check_now` swallows `ApiUnavailableError` after updating state; recovery loop has NO `contextlib.suppress` around it because the exception never escapes `check_now`. Non-domain exceptions propagate from `check_now` up through the recovery loop and end the task — surfaced as bugs, not silently dead threads.
- `asyncio.CancelledError` is explicitly re-raised in the recovery loop (project-context.md:49). Locked by `test_recovery_loop_re_raises_cancelled_error` (uses `asyncio.create_task` + a `blocker` Event + `task.cancel()` and asserts the awaited task raises `CancelledError`).
- `asyncio.Lock` serializes every state-mutating method. `test_concurrent_failures_emit_exactly_one_event` uses `asyncio.gather(report_failure(...), report_failure(...))` on a single manager and asserts exactly one `TierChanged` emission (at-most-once property).
- Update-then-emit ordering (AC #8) enforced in the single `_transition_to` helper. `test_tier_updated_before_event_emitted` subscribes a handler that reads `manager.tier` at fire time and asserts it matches `new_tier` — locks the ordering against future "emit first, update later" refactors.
- Isolation guardrail extended: `core/tiers.py` is fully isolated (no sqlite3, no yaml, no anthropic, no rich, no Win32). `TIERS_FORBIDDEN_TOPLEVEL_MODULES == FORBIDDEN_TOPLEVEL_MODULES` (no carve-out); `TIERS_ALLOWED_TOPLEVEL_MODULES` is a 7-entry stdlib + `nova` allowlist.
- Re-export count: `src/nova/core/__init__.py` 30 → 32 names (added `HealthCheck`, `TierManager`). Alphabetized (Story 1.2 monotonic-ordering test passes).
- Test-file AST guard `test_tiers_test_file_uses_no_real_sleep_or_network` enforces epics.md:796 ("tests use a deterministic clock and mock health check — no wall-clock or network dependencies") at the test-file-structure level. Walks the module AST, rejects `time.sleep(...)` calls, `asyncio.sleep(n)` calls for non-zero constant `n` (allows `asyncio.sleep(0)` yield idiom), and any `anthropic` import form.
- Quality gate green end-to-end: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0. Final pytest line: `461 passed, 1 skipped in 3.83s` (baseline 418 + 1 skipped; +43 new tests).
- Repo tree clean: no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.yaml.bak` introduced.
- Scope guards respected: no modifications to `app.py`, `cli.py`, any port file, any system/adapter stub, any `config/` shipped default, or `docs/config-schemas.md`. No `AuditLogger` wiring (Story 1.8 concern); no tier-notice UI (Story 5.4 concern); no `audit_log` writes from `TierManager`.
- Story 1.10 handoff: composition root calls `TierManager(health_check=claude_adapter, event_bus=event_bus, initial_tier=<from NovaConfig.api_key>)`. `HealthCheck` is structurally satisfied by the Claude adapter — no explicit inheritance required.

### File List

**New files:**

- `src/nova/core/tiers.py` — `HealthCheck` Protocol + `TierManager` class. ~280 lines (grew from ~260 after code-review patches added constructor validation + `asyncio.wait_for` defense).
- `tests/unit/core/test_tiers.py` — 42 tests (37 original + 5 code-review additions: 4 constructor-validation boundary tests + 1 `asyncio.TimeoutError` failure-path test) covering shape, all transition paths (FULL ↔ DEGRADED ↔ OFFLINE), recovery-loop lifecycle, concurrency (lock-enforced at-most-once), TierChanged payload contract (canonical closed-set), event-ordering, `check_now` exception contract, construction preconditions, timeout wrapping, and the AST-based "no real sleep or network" gate.

**Modified:**

- `src/nova/core/__init__.py` — added 2 re-exports (`HealthCheck`, `TierManager`); `__all__` re-alphabetized (32 names).
- `tests/unit/core/test_core_isolation.py` — added `tiers_module` import, `TIERS_FORBIDDEN_TOPLEVEL_MODULES` + `TIERS_ALLOWED_TOPLEVEL_MODULES` frozensets, 4 tiers-specific isolation tests, extended 2 parametrize lists (`test_no_relative_imports`, `test_no_dynamic_imports_of_forbidden_modules`) to include `tiers_module`, and added a `tiers_module` branch in the dynamic-import forbidden-set dispatch.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story key transitioned `ready-for-dev` → `in-progress` → `review` → `done`; `last_updated` bumped.
- `_bmad-output/implementation-artifacts/deferred-work.md` — 3 code-review deferred items appended under "code review of story 1-7 (2026-04-15)".
- `_bmad-output/implementation-artifacts/1-7-capability-tier-state-machine.md` — this file: task checkboxes, Dev Agent Record, Review Findings, File List, Status.

**Not modified (verified clean):**

- `pyproject.toml` — no new deps; all needed imports are stdlib or already-present first-party.
- `src/nova/app.py`, `src/nova/cli.py` — composition-root wiring is Story 1.10.
- `src/nova/core/events.py`, `src/nova/core/types.py`, `src/nova/core/exceptions.py` — `TierChanged`, `CapabilityTier`, `ApiUnavailableError` were already pinned by Stories 1.2/1.3; consumed, not modified.
- `src/nova/core/config.py`, `src/nova/core/storage/*` — unrelated.
- Any file under `config/`, `adapters/`, `systems/`, `ports/`, `setup/`.
- `docs/config-schemas.md`.
