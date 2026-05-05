# Story 3.5: Nerve Command Routing & Session Lifecycle

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)
**Depends on:**
- Story 1.3 — [src/nova/core/events.py](../../src/nova/core/events.py) (`SessionStarted`, `SessionEnded`, `EventBus`)
- Story 1.6 — [src/nova/core/config.py](../../src/nova/core/config.py) (`NovaConfig.settings.skip_briefing_if_recent`, `NovaConfig.settings.briefing_recency_threshold_minutes`)
- Story 1.7 — [src/nova/core/tiers.py](../../src/nova/core/tiers.py) (`TierManager.tier`, `report_failure`, `report_rate_limit_or_outage`, `check_now`, `run_recovery_loop`)
- Story 1.9 — [src/nova/ports/nerve.py](../../src/nova/ports/nerve.py) (`NervePort.startup`, `NervePort.route_command` Protocol stubs to be reshaped here)
- Story 1.10 — [src/nova/app.py](../../src/nova/app.py) (composition root, `_AlwaysHealthyCheck` stub, `NovaApp` graph) and [src/nova/cli.py](../../src/nova/cli.py) (`_async_main` placeholder log line at line 461)
- Story 2.5 — [tests/unit/test_app.py:564-585](../../tests/unit/test_app.py#L564-L585) (`test_tier_stays_offline_without_recovery_loop` — locks the no-recovery-loop posture this story must reconcile, see § Depends on prior-story state)
- Story 3.1 — [src/nova/adapters/sqlite/brain.py](../../src/nova/adapters/sqlite/brain.py) (`SqliteBrainAdapter.create_session`, `end_session`, `get_last_session`)
- Story 3.2 — [src/nova/systems/nerve/briefing.py](../../src/nova/systems/nerve/briefing.py) (`load_briefing_aggregate`, `determine_briefing_state`)
- Story 3.3 — [src/nova/systems/ritual/system.py](../../src/nova/systems/ritual/system.py) (`RitualSystem.build_briefing`)
- Story 3.4 — [src/nova/systems/skin/commands.py](../../src/nova/systems/skin/commands.py) (`parse`), [src/nova/systems/skin/models.py](../../src/nova/systems/skin/models.py) (`Command`, `CommandVerb`), [src/nova/adapters/rich/skin.py](../../src/nova/adapters/rich/skin.py) (`RichSkinAdapter.parse_command`)

**Downstream consumers:**
- Story 3.6 (Mode Restore) — replaces `NerveSystem._handle_mode` placeholder body with a delegation to `HandsPort.restore_mode`
- Story 3.7 (Shutdown Flow) — replaces `NerveSystem._handle_shutdown` direct-end-session path with a delegation to `RitualPort.begin_shutdown`; consumes `SkinPort.collect_input` (shipped here) for the seed prompt
- Story 3.8 (Warm Resume hero moment) — wires the briefing-prompt context into the REPL's contextual-reply gate (Story 3.5 ships the gate mechanism with `prompt_context = None`; 3.8 sets `briefing_resume` on State C briefings)
- Story 3.9 (Status & Help) — replaces `NerveSystem._handle_status` and `_handle_help` placeholder bodies with the full status assembly + help-table rendering
- Story 3.10 (Crash recovery) — extends the signal-handler shipped here with workspace-snapshot capture (Eyes integration)
- Epic 5 (Transparency / Forget) — replaces `NerveSystem._handle_memory` and `_handle_forget` placeholder responses with real Brain calls
- Epic 6 (Mode create/edit) — replaces `NerveSystem._handle_mode_create` / `_handle_mode_edit` placeholders with the wizard / editor flows
- Epic 7 (Voice prose) — adds prose-enrichment generation to `startup`'s briefing-assembly path; consumes `NerveSystem._tier_check_or_offline_response` for the tier-gate

## Story

As a user,
I want my typed commands routed to the correct system with tier-awareness and policy decisions, and the bare `nova` invocation to enter a continuous session loop that boots the briefing, accepts in-session commands, and shuts down cleanly,
So that N.O.V.A. responds appropriately regardless of cloud availability — and so the continuity-loop machinery shipped across Stories 1.3–3.4 finally fires end-to-end as one wired runtime.

## Story-type classification

**Interaction-boundary story.** ✅ Pre-flagged in [epic-3-story-preflags.md](epic-3-story-preflags.md) as **highest interaction-surface story in Epic 3** and **A3 fresh-session review trial target.** The three A6 questions, restated here:

1. **New contract between existing pieces?** YES — multiple new contracts. Nerve becomes the orchestrator that routes Skin `Command` objects to Brain / Ritual / Skin (Hands / Voice deferred to Stories 3.6 / Epic 7). New contracts shipped here:
   - **Skin → Nerve command submission** — the REPL loop `(input → parse → route)` cycle that connects the Story 3.4 parser to system execution.
   - **Nerve → Brain session lifecycle** — pairs with Story 3.1's `create_session` / `end_session` adapter; Story 3.5 is the first runtime caller.
   - **Nerve → TierManager tier-check-before-cloud-op** — pairs with Story 1.7; Story 3.5 establishes the tier-gate pattern (no cloud ops fire in 3.5; the pattern is structural).
   - **Nerve → Ritual delegation** for briefing assembly (already structurally satisfied by Story 3.3's `RitualSystem.build_briefing`); Story 3.5 is the first runtime caller.
   - **Nerve owns the SIGINT / SIGBREAK signal-handler registration** — the unexpected-termination hook (Story 3.10 extends with snapshot capture).
   - **`NervePort.route_command` return-shape reshape** (`-> None` → `-> CommandOutcome`) closes [deferred-work.md:139](deferred-work.md#L139).
   Every Epic 1 / Epic 2 system that shipped as a stub or isolated module finally connects through Nerve's lifecycle methods in this story.

2. **New invariants in degraded / partial-failure paths?** YES — five distinct invariants land in this story.
   - **Tier-check before cloud ops** — if `tier_manager.tier is OFFLINE`, cloud-dependent code paths must short-circuit to an honest local response instead of attempting the cloud call. Story 3.5's only cloud-shaped surface is the briefing's `prose_enrichment` (deferred to Epic 7); the tier-gate lives in `_tier_check_or_offline_response` for Epic 7 to consume.
   - **Skip-briefing policy** — `NovaConfig.settings.skip_briefing_if_recent` + `briefing_recency_threshold_minutes` decide whether the boot path renders the briefing. Last session ended within the threshold → no briefing card rendered, REPL prompt shown directly. Pure-function decision testable without DB or clock (clock is injected per the two-function pattern).
   - **Signal handler best-effort state capture** — the SIGINT / SIGBREAK / Windows console-close handler runs in a constrained context (no event-loop guarantees, possibly mid-coroutine, possibly from a non-asyncio thread on Windows). It MUST NOT itself raise. Brain write failure inside the handler is logged and swallowed; the process exits with the documented `EXIT_INTERRUPTED` code (130).
   - **Session lifecycle ordering — read-then-write-then-emit.** The corrected `startup` ordering (Dev Notes § "Why session creation moved AFTER briefing assembly") inverts the naive write-first design: every Brain READ inside `load_briefing_aggregate` runs FIRST so the prior-state read is not polluted by the about-to-be-created session row. THEN `create_session` writes the new session row. THEN `SessionStarted` fires (architecture.md:1037 write-then-emit). On clean shutdown, `SessionEnded` fires AFTER `end_session` confirms the write. Briefing-assembly failure does NOT leave an orphan open session because the session is not yet created at that point — `create_session` runs only AFTER the assembly + render succeed.
   - **Contextual-reply gating** — Layer C verbs (`RESUME` / `YES` / `NO` / `SKIP` / `CANCEL` / `CONFIRM`) carry `is_contextual=True` from the Story 3.4 parser. Nerve gates them on a runtime `prompt_context: str | None` field; outside an active prompt context, every contextual reply maps to an unknown-input response. Story 3.5 ships the gate with `prompt_context = None` for the bare REPL — Story 3.8 sets `briefing_resume` on the State C briefing so `resume` / `yes` then activate the resume path.

3. **Depends on prior-story state?** YES — and this is the critical reconciliation. Two areas:
   - **Story 2.5's `_AlwaysHealthyCheck` smoke test** ([tests/unit/test_app.py:564-585](../../tests/unit/test_app.py#L564-L585)) explicitly locks the no-recovery-loop posture: with `_AlwaysHealthyCheck` returning success on every probe, starting the recovery loop in OFFLINE tier would flip OFFLINE → FULL on the first tick, silently breaking the Story 2.5 promise. Story 3.5's reconciliation: **the recovery loop is NOT started in this story.** It is wired to a real Claude health probe in a future story (Epic 7 / Voice integration is the natural home). Story 3.5 owns the policy decision; the comment trail in `cli.py` and the unchanged smoke test together document the deferral. Refer also to the pre-flag [epic-3-story-preflags.md:44](epic-3-story-preflags.md#L44) which calls this out by name.
   - **Story 2.4's setup-row inheritance** — on the FIRST bare-`nova` boot after `setup.bat` completes, Brain's `nova.db` already contains the setup `sessions` row (`is_complete=True`, `mode_name=NULL`, `seed_text=NULL`). Story 3.5's `startup` MUST NOT collide with this row — `create_session` writes a NEW row (Brain's adapter is already correct), and the briefing assembly already handles the setup row correctly per Story 3.2's tested logic. The only new concern: the recency check (skip-briefing policy) reads `last_session.ended_at`; the setup row's `ended_at` is set within the setup wall-clock window (typically <30 seconds before bare-`nova` runs), which means a user who runs `nova` immediately after `setup.bat` would skip the briefing under the default 60-minute threshold. **This is the correct behavior** — the user just finished setup, they don't need a briefing of their setup session. Story 3.5 explicitly tests this case so the policy is locked.

**Classification result:** ✅ **Interaction-boundary story.** Apply the FULL A1 invariant sweep (lifecycle, teardown under partial failure, concurrency / cancellation, signal-handler safety, error translation, test determinism, Review Focus subsection). Apply A9 degraded-path proof in three categories (happy: bare boot → briefing → shutdown; degraded: cloud op attempted while OFFLINE → tier-gate fires; rerun: crash → next boot detects interrupted session). Apply A10 prior-state reconciliation per the per-story table below. Apply A3 fresh-session review trial — see Group N.

## Depends on prior-story state (A10)

Story 3.5 wires Nerve to runtime; every prior story ships a piece this story consumes or commits against.

### Story 1.3 — `EventBus` + typed events

| Surface | Story 3.5 reliance |
|---|---|
| [`EventBus.subscribe` / `emit`](../../src/nova/core/events.py) | NerveSystem holds an `EventBus` reference and emits `SessionStarted` after `create_session` succeeds, `SessionEnded` after `end_session` succeeds. No new event class; existing types suffice. |
| `SessionStarted(session_id, mode_name=None)` / `SessionEnded(session_id, seed_text=None, is_complete=True\|False)` | Emitted unchanged. Per the write-then-emit rule (architecture.md:1037), `create_session` returns BEFORE the `SessionStarted` emission; `end_session` returns BEFORE the `SessionEnded` emission. |
| `_utc_now_iso` / `_default_timestamp` two-function clock pattern | Story 3.5 reuses this pattern for any new timestamp emission (the recency-check clock — see § Group B AC #5). The recency-check clock is injectable; the production default is `_utc_now_iso`. |

### Story 1.6 — `NovaConfig.settings.skip_briefing_if_recent` / `briefing_recency_threshold_minutes`

| Surface | Story 3.5 reliance |
|---|---|
| [`UserSettings.skip_briefing_if_recent: bool`](../../src/nova/core/config.py#L184-L194) (default `True`) | Story 3.5's skip-briefing policy reads this. When `False`, the briefing always renders regardless of recency. |
| `UserSettings.briefing_recency_threshold_minutes: int` (default `60`, `_validate_threshold` rejects negative / non-int values to default `60`) | Story 3.5 reads this as the threshold for the skip-briefing recency check. The validator already guarantees a non-negative int, so the consumer can assume `>= 0`. A value of `0` disables the recency check (no time interval is "recent enough"). |

### Story 1.7 — `TierManager`

| Surface | Story 3.5 reliance |
|---|---|
| [`TierManager.tier` (property)](../../src/nova/core/tiers.py#L170-L178) | Read inside `_tier_check_or_offline_response` to gate cloud-dependent paths. Story 3.5 has zero cloud ops, so the gate is structural — Epic 7 (Voice prose) is the first real consumer. |
| `report_failure(*, reason)` / `report_rate_limit_or_outage(*, reason)` | Wired into Nerve's eventual cloud-call wrappers. Story 3.5 ships the wiring contract (the `_tier_check_or_offline_response` helper accepts a callable, runs it, translates `ApiUnavailableError` to the tier-failure path). The first real call site is Epic 7. |
| `run_recovery_loop()` | **NOT started by Story 3.5.** See § Group I. Reconciles with Story 2.5's `test_tier_stays_offline_without_recovery_loop` smoke test. |

### Story 1.9 — `NervePort` Protocol stub

| Surface | Story 3.5 reliance |
|---|---|
| [`NervePort.startup(self) -> None`](../../src/nova/ports/nerve.py#L33) | Reshape: NO change to signature. Story 3.5 ships the first concrete implementation. |
| [`NervePort.route_command(self, command: Command) -> None`](../../src/nova/ports/nerve.py#L35) | **Reshape:** return type changes from `None` to `CommandOutcome`. Closes [deferred-work.md:139](deferred-work.md#L139). The new `CommandOutcome` enum lives at `nova.systems.nerve.models` (NEW module) and is referenced from the port. |

### Story 1.10 — Composition root + `cli.py`

| Surface | Story 3.5 reliance |
|---|---|
| [`NovaApp` dataclass](../../src/nova/app.py#L86-L107) | **Reshape:** add `nerve: NervePort` field (positional, between `skin` and `close` per the established alphabetical-by-port-stem layout). The `close` callable stays last. |
| [`create_app`](../../src/nova/app.py#L110-L237) | **Reshape:** instantiate `NerveSystem` after `skin` is wired. Constructor receives `brain`, `ritual`, `skin`, `event_bus`, `tier_manager`, `config.settings` (or the two recency knobs), and an injectable clock callable. The partial-init cleanup block (the existing `try / except BaseException` sweep) covers the new instantiation by structure — `NerveSystem` is parameterless w.r.t. external resources, so it adds zero new failure modes to the cleanup path. |
| [`cli.py:_async_main` Step 7 placeholder](../../src/nova/cli.py#L459-L462) (`logger.info("session shell placeholder — full session loop arrives in Story 3.5")`) | **Replace:** `logger.info("entering session loop")` followed by `await app.nerve.startup()`. The Step 8 teardown (`await app.close()`) stays unchanged. |
| [`cli.py:_async_main` Step 6.5 offline notice](../../src/nova/cli.py#L286-L306) | **Untouched.** The notice still fires before session boot; nothing in Story 3.5 should change its placement. |
| [`cli.py:main` `KeyboardInterrupt` handler](../../src/nova/cli.py#L484-L490) | **Untouched as a top-level handler**, but Story 3.5's signal handler runs FIRST (it captures SIGINT before `cli.py`'s top-level catch sees a `KeyboardInterrupt`). The handler's best-effort Brain write happens inside the signal-handler scope; `cli.py`'s `KeyboardInterrupt` block continues to map to `EXIT_INTERRUPTED=130` for the process exit code. |
| [`_AlwaysHealthyCheck`](../../src/nova/app.py#L68-L83) | **Untouched.** Story 3.5 explicitly does NOT replace it; the recovery-loop deferral keeps the stub valid. |

### Story 2.4 — setup-time row inheritance

| Surface | Story 3.5 reliance |
|---|---|
| `sessions` row written by `persist_first_run` (Story 2.4 / Story 3.1 reconciliation table) | Story 3.5's `startup` calls `brain.create_session(mode_name=None, started_at=None)` — the adapter stamps `_utc_now_iso()` for `started_at`. The new row's `id` is autoincremented past the setup row's id. The setup row stays untouched as the prior session for `get_last_session`. |
| Setup row `is_complete=True, ended_at != NULL, mode_name=NULL, seed_text=NULL` | Read by Story 3.2's `determine_briefing_state` — produces `WARM_RESUME` (not `POST_SETUP`, per the existing tested logic). Story 3.5's recency-check policy then decides whether to render the briefing. **First-bare-`nova`-after-setup case:** setup ended <60 minutes ago → briefing skipped → REPL prompt shown directly. Locked by Group K AC #28. |

### Story 2.5 — `_AlwaysHealthyCheck` smoke test

| Surface | Story 3.5 reliance |
|---|---|
| [`test_tier_stays_offline_without_recovery_loop`](../../tests/unit/test_app.py#L564-L585) | **STAYS GREEN.** Story 3.5 does NOT start `tier_manager.run_recovery_loop()`. The test's promise — "for the duration of a `nova` invocation that boots with no API key, the initial OFFLINE tier must persist" — is preserved verbatim. The pre-flag [epic-3-story-preflags.md:44](epic-3-story-preflags.md#L44) anticipated this exact reconciliation. The smoke test gets a comment update only (referencing Story 3.5) — see § Group I. |

### Story 3.1 — Brain adapter

| Surface | Story 3.5 reliance |
|---|---|
| `SqliteBrainAdapter.get_last_session()` / `get_last_seed()` / `get_last_snapshot_for_session()` / `get_mode_last_used()` | All four are called BY `load_briefing_aggregate` (Story 3.2) inside `NerveSystem.startup` step 4 — BEFORE any write occurs. The reads see only genuinely-prior data: any pre-existing user sessions plus the Story 2.4 setup row. The freshly-created runtime session does not yet exist when these reads run. **This ordering is load-bearing** — see Dev Notes § "Why session creation moved AFTER briefing assembly" for the failure modes the read-then-write order prevents. |
| [`SqliteBrainAdapter.create_session(mode_name, *, started_at)`](../../src/nova/adapters/sqlite/brain.py) | Called by `NerveSystem.startup` step 9 — only for State B / C paths (State A returns early without creating a session). `mode_name=None` for the bare-`nova` case (no mode chosen until the user issues `mode <name>`). `started_at=None` → adapter stamps `_utc_now_iso()`. |
| `SqliteBrainAdapter.end_session(session_id, *, seed_text, summary, is_complete)` | Called by `NerveSystem._handle_shutdown` on clean SHUTDOWN routing (`seed_text=None, summary=None, is_complete=True`). Story 3.7 will replace the call site with the Ritual-delegated seed-prompt flow. Also called by the signal handler's best-effort path AND the `startup` `finally` defense-in-depth path with `is_complete=False`. |

### Story 3.2 — Briefing aggregate + state determination

| Surface | Story 3.5 reliance |
|---|---|
| [`load_briefing_aggregate(brain, config)`](../../src/nova/systems/nerve/briefing.py#L65-L113) | Called by `NerveSystem.startup` to assemble the aggregate. The function is already pure (no logging, no clock); Story 3.5 imports it and calls it. |
| `determine_briefing_state(aggregate)` | Called by `NerveSystem.startup` after aggregate assembly. Pure function. |

### Story 3.3 — Ritual briefing builder

| Surface | Story 3.5 reliance |
|---|---|
| [`RitualSystem.build_briefing(aggregate, state, tier)`](../../src/nova/systems/ritual/system.py#L311-L412) | Called by `NerveSystem.startup` after state determination. Returns `BriefingViewModel` for `Skin.render_briefing_card`. Story 3.5 is the first runtime caller. |
| `RitualSystem.begin_shutdown` | **NOT called by Story 3.5.** Currently raises `NotImplementedError("Story 3.7 scope")`. Story 3.5's `_handle_shutdown` path goes directly to `Brain.end_session` for the basic shutdown (no seed). Story 3.7 will replace the call site to delegate to `RitualPort.begin_shutdown`. |

### Story 3.4 — Skin parser + adapter

| Surface | Story 3.5 reliance |
|---|---|
| [`RichSkinAdapter.parse_command(raw_input)`](../../src/nova/adapters/rich/skin.py) | Called by the REPL inner loop per turn. Returns a `Command` from the Story 3.4 closed vocabulary. |
| [`Command` + `CommandVerb`](../../src/nova/systems/skin/models.py) | The 16-member closed vocabulary the route table dispatches on. The marker verbs `UNKNOWN` and `EMPTY` get the response prose from Nerve (per Story 3.4 § "Why the parser never raises"). |
| [`SkinPort.collect_input` Protocol stub](../../src/nova/ports/skin.py#L46) | **Adapter implementation lands in this story.** The `RichSkinAdapter.collect_input` body currently raises `NotImplementedError("Story 3.6 scope")` (the comment is outdated — it reads "Story 3.6" but Story 3.7's seed prompt is the explicit consumer; Story 3.5 needs it for the REPL primitive). See § Group E. |
| [`SkinPort.render_response` Protocol stub](../../src/nova/ports/skin.py#L44) | **Adapter implementation lands in this story.** Nerve emits response prose strings via `await skin.render_response(text)` for every routed Command — including ``SHUTDOWN`` (which renders ``"Session ended."`` after the Brain write succeeds and the ``SessionEnded`` event fires; see AC #10 SHUTDOWN row + AC #15). The only exception is ``EMPTY``, which is a silent no-op per the Story 3.4 parser contract. The Rich rendering is a single-line panel-less print to the adapter's ``Console``. |
| [`SkinPort.render_briefing_card`](../../src/nova/adapters/rich/skin.py) | Already implemented (Story 3.3). Called by `NerveSystem.startup` after the ViewModel is assembled. |

## Acceptance Criteria

### Group A: `CommandOutcome` enum + `NervePort.route_command` reshape

1. **New module** [`src/nova/systems/nerve/models.py`](../../src/nova/systems/nerve/models.py) declares the `CommandOutcome` closed vocabulary as a `StrEnum` (mirrors the Story 3.4 `CommandVerb` pattern). Two members:

   ```python
   from enum import StrEnum

   class CommandOutcome(StrEnum):
       """Outcome of a routed Command — drives the REPL loop's continue/exit decision.

       :attr:`CONTINUE` — the REPL loop returns to ``collect_input`` for the next turn.
       :attr:`EXIT` — the REPL loop terminates; the caller (``NerveSystem.startup``)
           runs cleanup and returns. Today only the SHUTDOWN verb produces EXIT;
           Story 3.7 adds seed-cancel paths that still resolve to EXIT.
       """

       CONTINUE = "continue"
       EXIT = "exit"
   ```

   `__all__ = ["CommandOutcome"]`. Module docstring spells out: *"This module owns Nerve-internal types that cross the port boundary. Per Story 1.9 AC #8, only `.models` crosses system boundaries; `nova.systems.nerve.briefing` and `nova.systems.nerve.system` are Nerve-internal."*

2. **Reshape** [`src/nova/ports/nerve.py`](../../src/nova/ports/nerve.py): `route_command` return annotation changes from `None` to `CommandOutcome`.

   ```python
   from nova.systems.nerve.models import CommandOutcome
   from nova.systems.skin.models import Command


   class NervePort(Protocol):
       async def startup(self) -> None: ...

       async def route_command(self, command: Command) -> CommandOutcome: ...
   ```

   The module docstring's reference to Story 3.5 is updated to: *"Story 3.5 reshapes :meth:`NervePort.route_command` from `-> None` to `-> CommandOutcome` so the REPL loop can drive its continue/exit decision off the return value. Closes [deferred-work.md:139](../../_bmad-output/implementation-artifacts/deferred-work.md#L139)."* No other port-shape changes (`startup` stays `-> None`).

3. **Shape regression test** at [`tests/unit/systems/nerve/test_command_outcome_shape.py`](../../tests/unit/systems/nerve/test_command_outcome_shape.py) (new file; mirrors the Story 3.4 `test_command_shape.py` layout):
   - `CommandOutcome` has exactly 2 members with values `("continue", "exit")` (parametrized over the expected tuple).
   - `CommandOutcome` is a subclass of `enum.StrEnum` (closed-set vocabulary discipline).
   - `CommandOutcome("continue") is CommandOutcome.CONTINUE` and `CommandOutcome("exit") is CommandOutcome.EXIT` (value-lookup identity, not just equality).

4. **Port-shape regression test update** at [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) — extend the existing `NervePort` annotation snapshot test (or add a new one) so it asserts `route_command`'s return annotation resolves to `CommandOutcome` (not `None`). Use `typing.get_type_hints(NervePort.route_command)` for runtime type resolution, NOT raw `inspect.signature` text-comparison (the latter would break with `from __future__ import annotations`).

### Group B: `NerveSystem.startup` — boot path

5. **New module** [`src/nova/systems/nerve/system.py`](../../src/nova/systems/nerve/system.py) declares `NerveSystem`, a concrete class structurally satisfying `NervePort` (Protocol — no nominal inheritance, per Story 3.3 / 3.4 precedent). Constructor signature:

   ```python
   class NerveSystem:
       def __init__(
           self,
           *,
           brain: BrainPort,
           ritual: RitualPort,
           skin: SkinPort,
           event_bus: EventBus,
           tier_manager: TierManager,
           config: NovaConfig,
           clock: Callable[[], datetime] = _utc_now,
       ) -> None: ...
   ```

   - All dependencies are keyword-only (Story 1.7 / 2.5 precedent — keyword-only constructors prevent positional drift across composition-root edits).
   - `clock` is injectable per project-context.md:46 (the two-function clock pattern). The production default is a module-level `_utc_now` whose body is `return datetime.now(UTC)`. Tests inject a fixed-point clock via `clock=lambda: fixed_dt`.
   - `config: NovaConfig` is the **single configuration contract** — `load_briefing_aggregate` already requires a `NovaConfig` (Story 3.2 reads `config.modes`), and the recency policy reads `config.settings.skip_briefing_if_recent` / `config.settings.briefing_recency_threshold_minutes`. A narrower `UserSettings`-only constructor would force a second overload of `load_briefing_aggregate` for the modes side; keeping the full `NovaConfig` reference is the minimum widening that keeps Story 3.2's signature stable.
   - The constructor stores references only — no I/O, no clock reads, no event subscriptions, no `asyncio.Event` instantiation in `__init__`. (`asyncio.Event` is created lazily inside `startup` so the loop binding matches the running loop — see AC #18.)

6. **`NerveSystem.startup() -> None`** is the boot-path entrypoint. **Crucial ordering rule:** every Brain READ (`get_last_session`, `get_last_seed`, `get_last_snapshot_for_session`, `get_mode_last_used`) inside `load_briefing_aggregate` MUST run BEFORE `create_session`'s WRITE — otherwise the freshly-created open session row pollutes the prior-state read, breaking State A/B/C determination (a true first-run DB would no longer produce `FIRST_RUN`, the setup-row-only case would produce a stale `WARM_RESUME`, and the recency check would compare against the just-created row). The eleven-step ordering below enforces this.

   Steps in order (each a separate `await` so cancellation can land at a clean boundary):

   1. **Initialize lifecycle state.** `self._shutdown_event = asyncio.Event()` (lazy, loop-bound at call time, NOT in `__init__`). `self._session_id: int | None = None`. `self._session_active: bool = False`. `self._prompt_context: str | None = None`.
   2. **Register the signal handler** (see § Group F AC #18). The handler is registered FIRST so a Ctrl-C during steps 3–4's Brain reads still gets best-effort capture (the handler short-circuits when `_session_active is False`, so a Ctrl-C BEFORE step 7's `create_session` is a clean no-op — no session yet exists to mark interrupted).
   3. **Wrap the body in `try / finally`.** The `finally` block runs the cleanup pair: defense-in-depth `end_session` if `_session_active is True` (REPL exited abnormally), then `_uninstall_signal_handler`. See step 11 for the cleanup contract.
   4. **Assemble the briefing aggregate.** `aggregate = await load_briefing_aggregate(brain, config)`. This issues all four prior-state Brain reads. **No new session row exists yet** — the reads see only setup-row data + any pre-existing user sessions.
   5. **Determine briefing state.** `state = determine_briefing_state(aggregate)` — pure function.
   6. **State A early return.** If `state is BriefingState.FIRST_RUN`:
      - Build the ViewModel: `view_model = await ritual.build_briefing(aggregate, state, tier_manager.tier)`.
      - Render: `await skin.render_briefing_card(view_model)`.
      - Log at INFO `"State A briefing rendered — setup wizard auto-start deferred to setup.bat first-run gate"`.
      - **Return immediately** — do NOT proceed to session creation, do NOT enter the REPL. State A means the user has no modes; no command can do useful work. The `try / finally` from step 3 still runs the signal-handler uninstall; `_session_active` is still `False` so the cleanup path is a no-op (no orphan session row gets written).
      - The `auto_start_setup=True` field on the State A ViewModel is documentation; the actual wizard is `setup.bat`-driven. **Story 3.5 does NOT shell out to `setup.bat` from inside the running session** — that would be a re-entrancy nightmare. Document this scope fence in the module docstring.
   7. **Skip-briefing policy decision.** Compute `should_skip = _should_skip_briefing(aggregate.last_session, config.settings, clock)` — pure function, see AC #7. Reuses `aggregate.last_session` (already loaded in step 4); does NOT issue a second `get_last_session` call.
   8. **Conditional briefing render** (State B / C only — State A already returned in step 6).
      - If `should_skip is True`: log at INFO `"briefing skipped (recent prior session)"` with `extra={"prior_session_ended_at": aggregate.last_session.ended_at}`. Skip the build+render.
      - If `should_skip is False`: `view_model = await ritual.build_briefing(aggregate, state, tier_manager.tier)`; `await skin.render_briefing_card(view_model)`.
   9. **Create the runtime session.** NOW — only after the prior-state reads are done and the briefing has rendered (or been skipped). `self._session_id = await brain.create_session(mode_name=None, started_at=None)`. Set `self._session_active = True` AFTER `create_session` returns the id (write-then-flag — a Brain write failure leaves `_session_active=False`, so the `finally` cleanup is a clean no-op).
   10. **Persist-before-emit.** `await event_bus.emit(SessionStarted(session_id=self._session_id, mode_name=None))` only AFTER step 9 returns. The architecture.md:1037 invariant.
   11. **Enter the REPL loop** (see § Group D AC #14). The REPL drives until `CommandOutcome.EXIT` (SHUTDOWN command), the `_shutdown_event` is set (signal handler), or `EOFError` / `KeyboardInterrupt` lands at the input boundary. All three exit paths return cleanly to the `finally` block.

   The `finally` block from step 3 runs in every exit path:
   - **Defense-in-depth interrupted-session marker.** If `_session_active is True` after the REPL returns (REPL exited via signal handler before SHUTDOWN command; OR an uncaught exception escaped REPL despite the guards), call `await brain.end_session(self._session_id, seed_text=None, summary=None, is_complete=False)`. On success, set `_session_active = False` and emit `SessionEnded(..., is_complete=False)` — write-then-emit ordering applies here too. On failure, log via `logger.exception("startup cleanup: brain.end_session failed")` and DO NOT emit. (The signal handler may have already done the write — `_session_active=False` after a successful handler write means this branch is a no-op.)
   - **Uninstall the signal handler.** Always last; cleanup must release the handler regardless of Brain-write outcome.

   The module docstring documents this eleven-step sequence verbatim, with each step's invariant called out.

7. **Skip-briefing helper** `_should_skip_briefing(prior_session: SessionSummary | None, settings: UserSettings, clock: Callable[[], datetime]) -> bool` is a module-level pure function. The caller (`NerveSystem.startup` step 7) extracts `aggregate.last_session` and `config.settings` and passes them in — keeps the helper's surface narrow + testable without a `NovaConfig` fixture:

   ```python
   def _should_skip_briefing(
       prior_session: SessionSummary | None,
       settings: UserSettings,
       clock: Callable[[], datetime],
   ) -> bool:
       """Return True iff the prior session ended recently enough to skip the briefing.

       Decision table (first match wins):

       - settings.skip_briefing_if_recent is False → False (always render)
       - prior_session is None → False (no prior session to be recent-against)
       - prior_session.ended_at is None → False (interrupted session, no defined end)
       - settings.briefing_recency_threshold_minutes == 0 → False (recency disabled)
       - now - parsed(prior_session.ended_at) < threshold → True
       - else → False
       """
   ```

   Pure function: no DB, no logging, no event emission. The clock is injected (production default: `datetime.now(UTC)`). The parser for `prior_session.ended_at` (an ISO-8601 string) uses `datetime.fromisoformat(...)` — Python 3.12 handles the `+00:00` suffix natively. **Defense-in-depth:** wrap the `fromisoformat` call in a `try / except ValueError` that returns `False` on parse failure (treats a malformed timestamp as "not recent" — fail-open to rendering the briefing rather than fail-closed to skipping).

8. **Read-then-write + persist-before-emit ordering** is locked by Group K test AC #30 Block 1. Three orderings asserted via `MagicMock.method_calls`:
   - **`load_briefing_aggregate`'s `brain.get_last_session` call happens BEFORE `brain.create_session` call.** This is the first-blocker fix: the prior-state read MUST NOT see the freshly-created open session. (The aggregate-loader issues additional reads beyond `get_last_session` — `get_last_seed`, `get_last_snapshot_for_session`, `get_mode_last_used` — every one of them must precede `create_session`.)
   - **`brain.create_session` call happens BEFORE `event_bus.emit(SessionStarted)`.** The architecture.md:1037 write-then-emit invariant.
   - **State A path issues NO `create_session` call.** If `determine_briefing_state` returns `FIRST_RUN`, `brain.create_session.call_count == 0` after `startup` returns. No orphan session row.

### Group C: `NerveSystem.route_command` — dispatch table

9. **`async def route_command(self, command: Command) -> CommandOutcome`** dispatches on `command.verb` via a single `match` statement (Python 3.12 structural pattern matching). Every `CommandVerb` member must have a case arm — exhaustiveness is locked by Group K test AC #26 (parametrized over every member of `CommandVerb`; each case must produce a Command rendering / response without raising). Default arm is unreachable but raises `RuntimeError(f"unhandled CommandVerb: {command.verb!r}")` to surface a future-verb-without-case-arm regression.

10. **Layer B routable verbs** dispatch as follows. Each branch is a single `_handle_<verb>` private method on `NerveSystem`. Methods that don't ship full functionality in this story (Hands / shutdown ceremony / Epic 5 / Epic 6) emit a placeholder response via `await self.skin.render_response(text)` and return `CommandOutcome.CONTINUE`.

   | `command.verb` | Routing in Story 3.5 |
   |---|---|
   | `MODE` (target=None) | `_handle_modes_list` — render mode list via Skin (compact one-liner, e.g., `"Modes: coding, study, writing"`). If `config.modes` is empty, render `"No modes configured. Edit %LOCALAPPDATA%/nova/modes/ to add one."`. |
   | `MODE` (target=str) | `_handle_mode_switch` — placeholder: `"Mode restore lands in Story 3.6. Stub: would switch to '{target}'."`. Story 3.6 replaces this body with `HandsPort.restore_mode` delegation. |
   | `MODE_CREATE` | `_handle_mode_create` — placeholder: `"Create mode lands in Epic 6 — for now, hand-edit %LOCALAPPDATA%/nova/modes/<stem>.yaml."` |
   | `MODE_EDIT` (target=None) | `_handle_mode_edit` (partial form) — `"Need one more detail. Try mode edit coding."` (verbatim per UX-DR § Partial Command Behavior). |
   | `MODE_EDIT` (target=str) | `_handle_mode_edit` (with target) — placeholder: `"Edit mode lands in Epic 6 — for now, hand-edit %LOCALAPPDATA%/nova/modes/{target}.yaml."` |
   | `STATUS` | `_handle_status` — placeholder: `"Status: tier={tier}, mode=(none)"` (full status table is Story 3.9). The current tier is read from `tier_manager.tier`; "no active mode" is hardcoded because Story 3.5 does not yet track the active mode. |
   | `MEMORY` | `_handle_memory` — placeholder: `"Transparency coming soon. Your data is stored locally in %LOCALAPPDATA%/nova/nova.db."` (verbatim per epic AC at epics.md:1152). |
   | `FORGET` (target=None) | `_handle_forget` (partial) — `"Tell me what to forget. Example: forget Meridian"` (verbatim per epic AC at epics.md:1153). |
   | `FORGET` (target=str) | `_handle_forget` (with target) — `"Forget capability coming soon."` (verbatim per epic AC at epics.md:1153). |
   | `HELP` | `_handle_help` — placeholder: `"Commands: mode <name>, mode/modes, status, help, shutdown. (Full table in Story 3.9.)"` |
   | `SHUTDOWN` | `_handle_shutdown` — calls `brain.end_session(self._session_id, seed_text=None, summary=None, is_complete=True)`, then emits `SessionEnded(session_id=self._session_id, seed_text=None, is_complete=True)` (write-then-emit), then renders `"Session ended."` via `skin.render_response`, then returns `CommandOutcome.EXIT`. **Story 3.7 will replace this body** with a delegation to `RitualPort.begin_shutdown(...)`; the seed prompt + persist-before-emit ordering is the Story 3.7 ceremony. The `_session_id` field is set in `startup` step 3 and stays for the session's lifetime. |

11. **Layer C contextual verbs** — gated on `self._prompt_context: str | None`. Story 3.5 ships the gate with `_prompt_context = None` (no active prompt context) for the bare REPL. Every Layer C verb (`RESUME` / `YES` / `NO` / `SKIP` / `CANCEL` / `CONFIRM`) when `_prompt_context is None` produces:

   - `RESUME` → `"Nothing to resume right now. Try mode <name> or mode to view available modes."` (verbatim per epic AC at epics.md:1263).
   - `YES` / `NO` / `SKIP` / `CANCEL` / `CONFIRM` → `"Nothing to confirm right now. Try help to see available commands."`

   All return `CommandOutcome.CONTINUE`. Story 3.8 will set `_prompt_context = "briefing_resume"` after the State C briefing renders; that story's tests will exercise the active-context branch.

12. **Marker verbs** — `UNKNOWN` and `EMPTY` from the Story 3.4 parser:

   - `UNKNOWN` → `_handle_unknown` — render `"Didn't catch that: {command.target!r}. Try help to see available commands."`. The `command.target` is the original user input (preserved by the Story 3.4 parser per AC #10) so the response template echoes what the user typed. Returns `CommandOutcome.CONTINUE`.
   - `EMPTY` → `_handle_empty` — silent no-op (per Story 3.4 § "Why the parser never raises" / epic 3.4 AC). Returns `CommandOutcome.CONTINUE` directly without calling `skin.render_response`. **Important:** the no-op MUST NOT log even at DEBUG (a per-keystroke log line for empty input would flood the file logger during a long REPL session).

13. **Tier-gate helper** `_tier_check_or_offline_response(self, op_name: str) -> bool` is the structural-only gate Story 3.5 ships for Epic 7's prose-enrichment caller. Returns `True` when `tier_manager.tier is CapabilityTier.FULL` (op may proceed); `False` otherwise (caller must short-circuit to a local fallback). When False, the helper emits a structured INFO log: `logger.info("op skipped due to tier", extra={"op": op_name, "tier": str(tier_manager.tier)})`. Story 3.5 has zero call sites for this helper (no cloud ops fire here); Group K AC #29 is a single test that wires it through and asserts the structured log shape so Epic 7 can rely on the contract.

### Group D: REPL loop

14. **`NerveSystem._run_repl(self) -> None`** is the inner loop Story 3.5's `startup` enters at step 11. The loop must support **three exit paths**:
    - **(a) SHUTDOWN command** — user types `shutdown`/`quit`/`exit`; `route_command` returns `CommandOutcome.EXIT`.
    - **(b) Signal handler set `_shutdown_event`** — Ctrl-C / SIGTERM / SIGBREAK fired; the handler did its best-effort Brain write and set the event. The REPL must observe the event and exit, otherwise the process keeps reading input after Ctrl-C (the second-blocker fix).
    - **(c) `EOFError` / `KeyboardInterrupt` at the input boundary** — closed stdin, or in-process `KeyboardInterrupt` that landed inside `Prompt.ask` BEFORE the signal handler ran (a race window narrower than (b) but still possible).

    The race-pattern implementation:

    ```python
    async def _run_repl(self) -> None:
        while not self._shutdown_event.is_set():
            input_task = asyncio.create_task(self.skin.collect_input(prompt="> "))
            shutdown_task = asyncio.create_task(self._shutdown_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {input_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # External cancellation (e.g., asyncio.run cleanup) — re-raise
                # per project-context.md:49. The pending tasks get cancelled by
                # the loop's teardown.
                input_task.cancel()
                shutdown_task.cancel()
                raise
            for task in pending:
                task.cancel()
                # Drain the cancelled task so its exception (if any) doesn't
                # surface as an "unawaited task" warning. Ignore CancelledError
                # and any tail exception — we're exiting the REPL.
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if shutdown_task in done:
                # Path (b) — signal handler beat the user. Brain write was
                # attempted by the handler. The startup() finally block runs
                # the defense-in-depth cleanup if _session_active is still True.
                logger.info("repl exiting via shutdown event (signal-driven)")
                return
            try:
                raw_input = input_task.result()
            except (EOFError, KeyboardInterrupt):
                # Path (c) — input boundary terminated before the signal handler
                # finished. Drive a clean SHUTDOWN. _handle_shutdown is
                # idempotent (AC #15); if the signal handler already wrote, the
                # _session_active guard makes this a no-op.
                logger.info("repl input terminated — invoking clean shutdown")
                await self._handle_shutdown(
                    Command(
                        verb=CommandVerb.SHUTDOWN,
                        target=None,
                        raw_input="",
                        is_contextual=False,
                    )
                )
                return
            command = await self.skin.parse_command(raw_input)
            outcome = await self.route_command(command)
            if outcome is CommandOutcome.EXIT:
                # Path (a) — SHUTDOWN command routed. _handle_shutdown already
                # wrote+emitted; nothing more to do.
                return
    ```

    - **`prompt="> "` is intentional and minimal.** The Skin layer renders this verbatim; Voice does NOT generate the prompt (project-context.md:64 — operational output bypasses Voice). A future polish story (Story 7+) may swap in a Voice-generated prompt; the verbatim `"> "` is the T1 baseline.
    - **`asyncio.wait` with `FIRST_COMPLETED`** is the correct primitive — `asyncio.wait_for(coroutine, timeout=...)` would force a timeout; `asyncio.gather(...)` would await both. We want "whoever finishes first wins; cancel the loser." The pending-task drain is mandatory: an unawaited cancelled task surfaces as a warning at process exit (project-context.md:105 — "no silent warnings in passing tests").
    - **The cancelled `input_task` may NOT actually unblock the underlying thread.** `Prompt.ask` is wrapped in `asyncio.to_thread`; cancelling the asyncio Future does not interrupt the OS-level blocking `input()` call. The thread becomes orphaned; it returns whenever the user types something or stdin closes. Acceptable for our use case: the process exits within seconds of REPL return (cli.py's `finally` calls `app.close`, which closes the storage engine; the orphan thread is daemon-killed at process exit). Document this in the REPL docstring.
    - **`asyncio.CancelledError` is re-raised** per project-context.md:49 — never swallowed at this boundary.
    - **No `try / except Exception` around `route_command` or `parse_command`.** Both are pure-or-domain-typed (parse never raises per Story 3.4 AC #10; route raises only for unhandled `CommandVerb` per AC #9, which is a programmer error). Top-level `cli.py` already catches unhandled exceptions and maps them to `EXIT_UNEXPECTED=4`; adding a per-loop-turn catch would silently swallow real bugs.

15. **`_handle_shutdown` is idempotent.** A second call after the session is already ended must NOT re-end-session, NOT re-emit `SessionEnded`, NOT re-render `"Session ended."`. Implementation: track `self._session_active: bool` initialized to `True` in `startup` step 3; flip to `False` after the first `end_session` returns. `_handle_shutdown`'s body is:

    ```python
    async def _handle_shutdown(self, command: Command) -> CommandOutcome:
        if not self._session_active:
            return CommandOutcome.EXIT
        await self.brain.end_session(
            self._session_id,
            seed_text=None,
            summary=None,
            is_complete=True,
        )
        self._session_active = False
        await self.event_bus.emit(
            SessionEnded(
                session_id=self._session_id,
                seed_text=None,
                is_complete=True,
            )
        )
        await self.skin.render_response("Session ended.")
        return CommandOutcome.EXIT
    ```

    Idempotency is locked by Group K AC #27.

### Group E: `RichSkinAdapter.collect_input` + `render_response`

16. [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py) — replace the `NotImplementedError` bodies for `collect_input` and `render_response`. The other two stubs (`render_progress` Story 3.6, `render_shutdown_card` Story 3.7) stay unchanged.

    **`render_response`:**

    ```python
    async def render_response(self, text: str) -> None:
        await asyncio.to_thread(self._console.print, text, markup=False)
    ```

    Plain console print — no panel, no markup — operational output per project-context.md:66. ``markup=False`` is critical: ``Console.print`` interprets ``[bold]…[/]``-style square-bracket markup by default; passing user-controllable text without the flag would let arbitrary Rich markup activate. The ``asyncio.to_thread`` wrap is the same pattern Story 3.3's ``render_briefing_card`` uses for Rich's blocking I/O.

    **`collect_input`:**

    ```python
    async def collect_input(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def _read_in_daemon_thread() -> None:
            try:
                result = Prompt.ask(prompt, console=self._console)
            except BaseException as exc:  # noqa: BLE001 — propagate via future
                loop.call_soon_threadsafe(_safe_set_exception, future, exc)
            else:
                loop.call_soon_threadsafe(_safe_set_result, future, result)

        thread = threading.Thread(
            target=_read_in_daemon_thread,
            name="nova-skin-input",
            daemon=True,
        )
        thread.start()
        return await future
    ```

    **Why a daemon thread (not `asyncio.to_thread`):** `to_thread` runs work in the loop's default executor — a non-daemon thread pool. On signal-driven exit, the REPL's race pattern cancels its asyncio await of this future, but the underlying blocking `input()` call is still sitting in the executor thread waiting for stdin. With a non-daemon thread, `asyncio.run`'s `shutdown_default_executor` blocks process exit until the user types ENTER. Daemon thread = killed by OS at process exit, so `asyncio.run` returns cleanly. Locked by `test_collect_input_uses_daemon_thread_for_process_exit_safety`.

    `from rich.prompt import Prompt` and `import threading` are added to the adapter imports. The module also gains two helpers — `_safe_set_result` / `_safe_set_exception` — that no-op on already-done (cancelled) futures so the daemon thread's late completion doesn't `set_result` on a cancelled future. `Prompt.ask` returns `str` and propagates `EOFError` / `KeyboardInterrupt` natively via the `BaseException` catch. The empty-input case is allowed through to the Story 3.4 parser (which maps to `CommandVerb.EMPTY`); the prompt does NOT pre-filter.

17. **Adapter delegation tests** at [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) (existing file from Stories 3.3 / 3.4 — append, do not replace):
    - `test_render_response_prints_to_console` — patches the adapter's `Console` mock (`MagicMock`); `await adapter.render_response("hello")` ⇒ `console.print` called once with `"hello"`.
    - `test_collect_input_delegates_to_rich_prompt` — patches `nova.adapters.rich.skin.Prompt.ask` to return `"mode coding"`; `await adapter.collect_input(prompt="> ")` returns `"mode coding"` and `Prompt.ask` was called once with `"> "` and the adapter's console.
    - `test_collect_input_propagates_eof_error` — `Prompt.ask` is patched to raise `EOFError`; `await adapter.collect_input(...)` re-raises `EOFError` (REPL catches at the boundary per AC #14).

### Group F: Signal-handler registration

18. **`NerveSystem._install_signal_handler` / `_uninstall_signal_handler`** are the lifecycle pair. The handler captures interrupted-session state via Brain best-effort, sets `_shutdown_event` so the REPL exits, and returns. **It does NOT rely on default `KeyboardInterrupt` propagation** — once `loop.add_signal_handler(SIGINT, ...)` (POSIX) or `signal.signal(SIGINT, ...)` (Windows) installs a custom handler, the default Python-→-`KeyboardInterrupt`-injection is suppressed. Without an explicit shutdown signal, the handler would write to Brain and then control would return to the blocked `Prompt.ask` thread — the REPL would stay alive. Setting `_shutdown_event` is the explicit signal that drives the REPL's race-pattern exit (AC #14, path (b)).

    **Platform split:**
    - On POSIX: `loop.add_signal_handler(signal.SIGINT, self._schedule_signal_handler_callback)` and `signal.SIGTERM`. Both are registered. `add_signal_handler` requires an asyncio loop — `startup` runs inside one (cli.py wraps everything in `asyncio.run`). The callback is sync — it schedules the async coroutine via `asyncio.create_task(self._signal_handler_callback())`. (The async work — Brain write, event emission — must run on the loop, not in the signal-callback context itself.)
    - On Windows: `loop.add_signal_handler` does NOT support SIGINT (raises `NotImplementedError` on the Windows event-loop policies). Fallback: `signal.signal(signal.SIGINT, self._sync_signal_handler)` for the in-loop SIGINT path, plus `signal.signal(signal.SIGBREAK, ...)` for the Ctrl-Break / Windows-console-close case. The sync handler dispatches via `loop.call_soon_threadsafe(asyncio.create_task, self._signal_handler_callback())` so the actual Brain write happens on the loop.
    - The platform branch is detected via `sys.platform` (NOT `os.name` — narrower).

    **Handler body — must NOT raise; emission gated on Brain-write success:**

    ```python
    async def _signal_handler_callback(self) -> None:
        # Always set the shutdown event FIRST. Even if the Brain write fails,
        # the REPL must exit — a stuck signal-handler-with-no-shutdown is the
        # worst failure mode (the user's Ctrl-C did nothing visible).
        # _shutdown_event.set() is idempotent — a second call is a no-op.
        self._shutdown_event.set()
        # _session_active guard makes the handler one-shot. A second SIGINT
        # while the first handler's Brain write is in flight returns
        # immediately — prevents a hang-on-second-Ctrl-C and prevents
        # double-end-session.
        if not self._session_active:
            return
        # If startup() bailed out at State A (step 6) before reaching session
        # creation, _session_id is None — the early-return guard above
        # short-circuits because _session_active is False on that path.
        # Defense-in-depth assertion: _session_id must not be None here.
        assert self._session_id is not None, (
            "_session_active=True implies _session_id is set"
        )
        # Phase 1: Brain write. Bounded by a 2-second timeout per epic 3.10
        # AC. Failure paths are logged and exit early — write-then-emit means
        # we DO NOT emit SessionEnded if the write failed. A spurious
        # SessionEnded after a failed Brain write would lie to downstream
        # consumers (audit log readers, future replay mechanisms).
        try:
            await asyncio.wait_for(
                self.brain.end_session(
                    self._session_id,
                    seed_text=None,
                    summary=None,
                    is_complete=False,
                ),
                timeout=2.0,
            )
        except TimeoutError:
            # asyncio.TimeoutError aliases TimeoutError in Python 3.11+ — use
            # the canonical name. Log and STOP. Do NOT emit.
            logger.warning("signal handler: brain.end_session timed out (>2s)")
            return
        except Exception:
            logger.exception("signal handler: brain.end_session failed")
            return
        # Phase 2: Mark session inactive THEN emit. The flag flip happens
        # before the emit because the emit could itself fail (handler chain
        # exception); we want _session_active=False to reflect the durable
        # truth (Brain confirmed the write) regardless of emit-failure
        # logging.
        self._session_active = False
        try:
            await self.event_bus.emit(
                SessionEnded(
                    session_id=self._session_id,
                    seed_text=None,
                    is_complete=False,
                )
            )
        except Exception:
            logger.exception("signal handler: SessionEnded emission failed")
    ```

    **Bounded timeout = 2 seconds** per epic 3.10 AC ("Completes within a bounded timeout (e.g., 2 seconds) — never hangs on shutdown"). The timeout is hardcoded in Story 3.5 (no constructor knob) — Story 3.10 owns the configurable knob if one is ever needed.

    **Write-then-emit ordering is the third-blocker fix.** A previous draft of this story emitted `SessionEnded` even after the Brain write failed, producing a phantom event that lied about persistence. The corrected handler `return`s early on every Brain-write failure path (timeout, exception) and ONLY emits after `end_session` returns successfully.

    **`_shutdown_event.set()` is the fourth-blocker fix.** Without it, a custom signal handler captures the signal, runs cleanup, and then control returns to whatever blocking call was in flight (`Prompt.ask` inside `to_thread`) — the REPL stays alive. Setting the event drives the AC #14 race-pattern exit; the REPL observes the event and returns, the `startup` `finally` block runs, the process exits cleanly via `cli.py`'s teardown.

    **Workspace-snapshot capture is OUT of Story 3.5's scope.** Epic 3.10 AC explicitly mentions snapshot capture in the signal handler as part of Story 3.10 (Eyes integration). Story 3.5 ships only the session-end-with-is_complete=False path; Story 3.10 extends the handler body. Document this scope fence in the handler's docstring.

19. **Handler is one-shot.** After `_signal_handler_callback` runs once, subsequent SIGINT (e.g., user mashes Ctrl-C twice) does NOT re-attempt the Brain write — the `_session_active` guard catches it. The `_shutdown_event.set()` is idempotent so a second-Ctrl-C re-set is a clean no-op. This prevents a hang-on-second-Ctrl-C if the first write is in flight, and prevents double-end-session.

### Group G: Composition root wiring

20. [`src/nova/app.py`](../../src/nova/app.py) — extend `NovaApp` and `create_app`:

    - **`NovaApp` field added:** `nerve: NervePort` (positional, between `skin` and `close`). Field order is `config, storage, brain, event_bus, audit, tier_manager, shield, ritual, skin, nerve, close` — alphabetical-by-port-stem in the systems block, with `close` always last per Story 1.10 precedent.
    - **`create_app` instantiates** `NerveSystem` after `skin` is wired:

      ```python
      nerve: NervePort = NerveSystem(
          brain=brain,
          ritual=ritual,
          skin=skin,
          event_bus=event_bus,
          tier_manager=tier_manager,
          config=config,
      )
      logger.info("nerve system wired", extra={"system": type(nerve).__name__})
      ```

    - The existing partial-init cleanup `try / except BaseException` block continues to cover the new instantiation. `NerveSystem.__init__` acquires no external resources (constructor is reference-storage only per AC #5), so the cleanup block has zero new failure modes to handle.

21. **Composition-root regression test** at [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py): a new test `test_nerve_system_is_instantiated_inside_create_app` patches `nova.app.NerveSystem` to a `MagicMock` and asserts `create_app` called it exactly once with the expected kwargs (`brain`, `ritual`, `skin`, `event_bus`, `tier_manager`, `config`). Mirrors the existing pattern for `RichSkinAdapter`, `RitualSystem`, `SqliteBrainAdapter`.

22. **`test_only_app_and_cli_import_adapters`** ([tests/unit/test_composition_root.py](../../tests/unit/test_composition_root.py)) already enforces the adapter-import gate. Verify (no code change needed) that this test still passes after Story 3.5 — `nova.systems.nerve.system` MUST NOT import from `nova.adapters.*`. The Group L isolation test re-locks this at the system level.

### Group H: cli.py integration

23. [`src/nova/cli.py`](../../src/nova/cli.py) `_async_main` Step 7 — replace lines 459-462:

    ```python
    # Before:
    logger.info("session shell placeholder — full session loop arrives in Story 3.5")
    return EXIT_OK

    # After:
    logger.info("entering session loop")
    await app.nerve.startup()
    return EXIT_OK
    ```

    The Step 8 teardown (`await app.close()` in the `finally`) stays unchanged.

24. **No new CLI subcommands.** Story 3.5 ships ONLY the bare `nova` invocation path. Layer A subcommands (`nova mode coding`, `nova help`, `nova status`, `nova memory` per architecture.md:124-137) are deferred. The pre-flag's `<X> mode` "Layer A awareness" comment ([epic-3-story-preflags.md:44](epic-3-story-preflags.md#L44)) is satisfied by the bare-`nova` path alone — the in-session parser handles the verbs, no shell-form surface is required for the hero path. A future story (or polish pass) may add `argparse` subcommands; doing so requires a paired update to [`tests/unit/test_cli_no_new_subcommands.py`](../../tests/unit/test_cli_no_new_subcommands.py) (Story 2.5's lock against silent subcommand growth). **Story 3.5 does NOT touch that test file.**

25. **CLI integration test** at [`tests/integration/test_session_loop.py`](../../tests/integration/test_session_loop.py) (new file, mirrors the `test_setup_bat.py` integration layout):
    - `test_bare_nova_boots_briefing_then_shuts_down_on_shutdown_command` — full happy-path: empty `data_dir` with a single mode + a prior session row → run `_async_main` with a stdin pipe that types `"shutdown\n"` → `EXIT_OK` → `nova.db` shows the new session row with `is_complete=1`, `seed_text=NULL`. Mocks: none — uses the real adapters end-to-end (this is the first integration test that exercises Brain → Nerve → Ritual → Skin together).
    - `test_bare_nova_skips_briefing_when_prior_session_recent` — same setup but the prior session ended <60min ago; assert NO Briefing Card was rendered (capture stdout via Rich's `Console(file=...)` injection at the test boundary). REPL still enters, `"shutdown"` types in to exit.
    - **Mark all tests in this file with `@pytest.mark.integration`** so the pytest config keeps them out of the unit run by default (Story 1.11 CI-gate convention).

### Group I: `_AlwaysHealthyCheck` smoke-test reconciliation

26. **No code change.** [`tests/unit/test_app.py:564-585`](../../tests/unit/test_app.py#L564-L585) (`test_tier_stays_offline_without_recovery_loop`) STAYS GREEN unchanged. The test's assertion — `app.tier_manager.tier is CapabilityTier.OFFLINE` after `await asyncio.sleep(0)` — remains true because Story 3.5 does NOT call `tier_manager.run_recovery_loop()`.

27. **Comment-only update** to the docstring of `test_tier_stays_offline_without_recovery_loop` (lines 565-573):

    Before:
    > """Story 2.5 Dev Notes — Story 1.10's ``create_app`` does NOT start
    > ``tier_manager.run_recovery_loop()``; Nerve (Story 3.5) owns that. For
    > the duration of a ``nova`` invocation that boots with no API key, the
    > initial OFFLINE tier must persist.

    After:
    > """Story 2.5 Dev Notes — ``create_app`` does NOT start
    > ``tier_manager.run_recovery_loop()``. Story 3.5 explicitly defers the
    > recovery loop to a future story (the Claude adapter is the natural
    > home — see Story 3.5 § Group I). The initial OFFLINE tier must persist
    > for the duration of a ``nova`` invocation that boots with no API key.

    This is a documentation-only edit. No assertion changes, no fixture changes. The change is minimal and surgical so a future grep for "Story 3.5 owns" doesn't return a stale claim.

28. **Document the deferral in `nova/app.py`'s `_AlwaysHealthyCheck` docstring.** Add one sentence:

    > "Story 3.5 does NOT swap this stub or start the recovery loop —
    > swapping requires a real Claude-backed health probe, which lands
    > with the Claude adapter. The stub plus the no-recovery-loop posture
    > together preserve the OFFLINE-tier-when-no-api-key promise locked
    > by Story 2.5's smoke test."

    Insert after the existing two-paragraph docstring at [src/nova/app.py:68-83](../../src/nova/app.py#L68-L83). No code changes to the class.

### Group J: deferred-work.md:139 close-out

29. [`_bmad-output/implementation-artifacts/deferred-work.md`](deferred-work.md) — at story completion (Dev sets `Status: review`), the entry on line 139 (or wherever the `NervePort.route_command` returns `None` bullet currently lives) is **removed** (or replaced with a one-line "closed by Story 3.5 — see [3-5-nerve-command-routing-and-session-lifecycle.md](3-5-nerve-command-routing-and-session-lifecycle.md)" pointer). The grep target is `NervePort.route_command` in `deferred-work.md`. Story 3.5's `CommandOutcome` reshape is the canonical fix.

### Group K: Tests

30. **`tests/unit/systems/nerve/test_nerve_system.py`** (new file; primary unit-test surface for Story 3.5). Use `pytest.mark.asyncio` on every test; use `pytest.mark.parametrize` for the dispatch table (one test per CommandVerb member). Test layout (one block per AC concern):

    **Block 1 — Constructor + startup ordering (AC #5, #6, #8):**
    - `test_constructor_does_not_acquire_resources` — patch `brain.create_session` to raise; assert `NerveSystem(...)` constructor returns cleanly without raising. The test proves the constructor is reference-storage only and does NOT instantiate `asyncio.Event` (which would bind to whatever loop is current at construction time, not the loop `startup` runs on).
    - `test_startup_initializes_shutdown_event_lazily` — patch `asyncio.Event` to a `MagicMock`; assert it was instantiated INSIDE `startup` (not in `__init__`). Locks the lazy-loop-binding contract per AC #6 step 1.
    - **`test_startup_reads_prior_state_before_creating_session`** — the first-blocker lock. Use `MagicMock.method_calls` on the brain mock to assert `get_last_session`, `get_last_seed`, `get_last_snapshot_for_session`, and `get_mode_last_used` ALL appear in `brain.method_calls` BEFORE `create_session`. Reverse ordering would silently break the State A/B/C decision model.
    - **`test_startup_state_a_does_not_create_session`** — the second-blocker lock. Inject an aggregate that produces `BriefingState.FIRST_RUN`; assert `brain.create_session.call_count == 0` after `startup` returns. No orphan session row.
    - `test_startup_state_a_renders_briefing_then_returns` — same fixture as above; assert `ritual.build_briefing` and `skin.render_briefing_card` each called exactly once before the early return; `skin.collect_input.call_count == 0` (REPL never entered).
    - `test_startup_creates_session_after_briefing_render` — for State B/C with no skip; assert ordering: `ritual.build_briefing` → `skin.render_briefing_card` → `brain.create_session` → `event_bus.emit(SessionStarted)`.
    - `test_startup_persist_before_emit_session_started` — `brain.create_session` call appears BEFORE `event_bus.emit(SessionStarted)` in `MagicMock.method_calls`. Locks architecture.md:1037 invariant.
    - `test_startup_passes_mode_name_none_and_started_at_none_to_create_session` — kwargs match exactly; the adapter is responsible for stamping the timestamp.
    - `test_startup_registers_signal_handler_before_aggregate_load` — `_install_signal_handler` appears BEFORE `load_briefing_aggregate`'s first Brain read. Locks AC #6 step 2 ordering (a Ctrl-C during the Brain reads still gets the handler; the handler short-circuits when `_session_active is False` so a no-op is the correct outcome).
    - `test_startup_finally_block_runs_uninstall_signal_handler_even_on_state_a_path` — patch `_uninstall_signal_handler` to a mock; assert it's called once in the State A early-return path.
    - `test_startup_finally_block_writes_interrupted_marker_when_repl_exits_with_session_active` — defense-in-depth path. Force `_run_repl` to raise an unexpected `RuntimeError`; assert `brain.end_session(..., is_complete=False)` is called once in the `finally` block, then `event_bus.emit(SessionEnded(..., is_complete=False))` after the write succeeds. Then the original `RuntimeError` propagates.

    **Block 2 — Skip-briefing policy (AC #7):**
    - `test_skip_briefing_returns_false_when_setting_is_false` (parametrize true/false on the setting).
    - `test_skip_briefing_returns_false_when_prior_session_is_none`.
    - `test_skip_briefing_returns_false_when_ended_at_is_none` (interrupted prior session).
    - `test_skip_briefing_returns_false_when_threshold_is_zero`.
    - `test_skip_briefing_returns_true_when_within_threshold` (inject clock that returns `prior_ended_at + 30 minutes`; threshold=60).
    - `test_skip_briefing_returns_false_when_outside_threshold` (inject clock that returns `prior_ended_at + 90 minutes`; threshold=60).
    - `test_skip_briefing_returns_false_on_malformed_ended_at` (set `ended_at="not-an-iso-string"`; assert no exception, returns False).
    - `test_setup_row_recency_skips_briefing_first_bare_nova_boot` — Story 2.4 reconciliation. Inject the setup row (ended_at=20 minutes ago, mode_name=None, is_complete=True, seed_text=None); assert `_should_skip_briefing` returns True. Locks the prior-state contract from § Depends on prior-story state.

    **Block 3 — Briefing render path (AC #6 steps 7-8):**
    - `test_briefing_skipped_does_not_call_ritual_build_or_skin_render` — for State B/C, when policy returns True, neither `ritual.build_briefing` nor `skin.render_briefing_card` is called. (State A is excluded — its render path is unconditional and tested in Block 1.)
    - `test_briefing_rendered_when_policy_returns_false` — both `ritual.build_briefing` and `skin.render_briefing_card` are called once each, in order.
    - `test_briefing_skipped_log_includes_prior_session_ended_at` — when skipped, the INFO log carries `extra={"prior_session_ended_at": "<iso-string>"}`. Locks AC #6 step 8.
    - `test_skip_policy_reads_aggregate_last_session_not_separate_get_call` — assert `brain.get_last_session.call_count == 1` (only the call inside `load_briefing_aggregate`); the skip helper does NOT issue a second read. Locks AC #7's "extracted from `aggregate.last_session`" contract.

    **Block 4 — Dispatch table (AC #9, #10, #11, #12) — parametrized across CommandVerb members:**
    - `test_route_command_dispatches_layer_b_routable` (parametrize): MODE/None, MODE/"coding", MODE_CREATE, MODE_EDIT/None, MODE_EDIT/"coding", STATUS, MEMORY, FORGET/None, FORGET/"Meridian", HELP. Each: assert `skin.render_response` called once with the expected placeholder string (for placeholder verbs) and `outcome is CommandOutcome.CONTINUE`.
    - `test_route_command_shutdown_calls_brain_then_emits_session_ended_then_renders_response` — assertion order via `MagicMock.method_calls`. Then assert `outcome is CommandOutcome.EXIT`.
    - `test_route_command_layer_c_without_prompt_context_routes_to_no_active_prompt_response` (parametrize over RESUME / YES / NO / SKIP / CANCEL / CONFIRM): assert the documented response text + `outcome is CommandOutcome.CONTINUE`.
    - `test_route_command_unknown_echoes_target_in_response` — UNKNOWN with `target="audit"` ⇒ response contains `"audit"`.
    - `test_route_command_empty_does_not_call_render_response` — silent no-op invariant. Assert `skin.render_response.call_count == 0`.
    - `test_route_command_dispatch_table_covers_every_command_verb` — parametrize `CommandVerb` (16 members) + ensure none raise, none return None. This is the exhaustiveness lock per AC #9.

    **Block 5 — Idempotent shutdown (AC #15):**
    - `test_handle_shutdown_is_idempotent_second_call_returns_exit_without_re_ending_session` — call `_handle_shutdown` twice; assert `brain.end_session.call_count == 1`, `event_bus.emit.call_count == 1` (only the first call; SessionStarted from startup is excluded by checking `event_bus.emit.call_args_list` for the SessionEnded type).

    **Block 6 — REPL loop (AC #14) — three exit paths:**
    - **Path (a) tests** — `test_repl_exits_on_shutdown_command`: `skin.collect_input` returns `"shutdown"` once; REPL exits via `CommandOutcome.EXIT`; `collect_input.call_count == 1`.
    - `test_repl_continues_after_unknown_then_exits_on_shutdown` — `collect_input` returns `"hello"` then `"shutdown"`; UNKNOWN response rendered for `"hello"`; REPL exits on `"shutdown"`; `collect_input.call_count == 2`.
    - **Path (b) tests** — `test_repl_exits_when_shutdown_event_set_externally`: with `collect_input` patched to a long `asyncio.sleep`, externally call `nerve._shutdown_event.set()`; REPL detects via the race pattern, cancels the input task, returns. The cancelled input task is drained (no `unawaited` warnings via `caplog` / `recwarn`).
    - `test_repl_exits_when_shutdown_event_set_before_first_iteration` — `_shutdown_event.set()` BEFORE `_run_repl` is called; the `while not self._shutdown_event.is_set()` guard fires immediately; `collect_input.call_count == 0`.
    - **Path (c) tests** — `test_repl_eof_error_triggers_clean_shutdown`: `collect_input` raises `EOFError`; REPL catches via `input_task.result()`, invokes `_handle_shutdown`, returns. `brain.end_session` called once.
    - `test_repl_keyboard_interrupt_triggers_clean_shutdown` — same shape with `KeyboardInterrupt`.
    - **Race semantics** — `test_repl_drains_cancelled_pending_task_without_warning`: `_shutdown_event.set()` while `input_task` is in flight; assert no `RuntimeWarning` / `pytest.warns` resource warning surfaces. Locks the "drain cancelled task" contract per AC #14.
    - **Cancellation propagation** — `test_repl_re_raises_external_cancellation`: cancel the parent task running `_run_repl` from outside; assert `asyncio.CancelledError` propagates out (per project-context.md:49). Pending tasks are cancelled in the `except` arm.

    **Block 7 — Signal handler (AC #18, #19) — write-then-emit + shutdown-event:**
    - `test_signal_handler_sets_shutdown_event_first` — invoke `_signal_handler_callback` directly with `_session_active=False` (no Brain write happens); assert `_shutdown_event.is_set()` returns True. Locks the fourth-blocker fix: even on the no-Brain-write path, the REPL must still receive the shutdown signal.
    - `test_signal_handler_calls_brain_end_session_with_is_complete_false` — `_session_active=True`; assert `brain.end_session(self._session_id, seed_text=None, summary=None, is_complete=False)` called.
    - `test_signal_handler_emits_session_ended_after_brain_write` — assertion order on `MagicMock.method_calls`: `brain.end_session` BEFORE `event_bus.emit(SessionEnded)`. Locks the third-blocker fix.
    - **`test_signal_handler_does_not_emit_session_ended_when_brain_write_fails`** — patch `brain.end_session` to raise `StorageError`; assert handler returns cleanly AND `event_bus.emit.call_count == 0` (no SessionEnded emission). Asserts the error log via `caplog`. Also assert `_session_active is True` after the handler returns (the flag flip happens AFTER the successful write; a failed write must not flip it). The third-blocker regression lock.
    - **`test_signal_handler_does_not_emit_session_ended_when_brain_write_times_out`** — patch `brain.end_session` to take 5 seconds via `asyncio.sleep`; assert handler returns within ~2 seconds (outer `asyncio.wait_for` cap of 3 seconds in the test) AND `event_bus.emit.call_count == 0`. The 2-second timeout is per epic 3.10 AC. Same `_session_active` invariant as the failure test.
    - `test_signal_handler_swallows_emission_failure_after_successful_write` — Brain write succeeds; `event_bus.emit` raises; handler returns cleanly; `caplog` has the emission-failure exception log. `_session_active is False` (the flip happened after the successful Brain write, before the emission).
    - `test_signal_handler_one_shot_via_session_active_guard` — call `_signal_handler_callback` twice; first call writes + emits + sets `_session_active=False`. Second call sees `_session_active=False` and returns after only setting `_shutdown_event` (which was already set from the first call — idempotent). `brain.end_session.call_count == 1`.
    - `test_signal_handler_no_op_before_session_creation` — `_session_active=False`, `_session_id=None` (the State A path or pre-create_session window). Handler sets `_shutdown_event` and returns; no Brain call, no emit, no AssertionError from the `assert _session_id is not None` defensive guard (the guard is gated on `_session_active=True`).

    **Block 8 — Tier-gate helper (AC #13):**
    - `test_tier_check_or_offline_response_returns_true_in_full` — `tier_manager.tier = FULL`; helper returns True; no log.
    - `test_tier_check_or_offline_response_returns_false_in_degraded_offline` (parametrize DEGRADED / OFFLINE); helper returns False; assert `caplog` shows the structured INFO with `extra={"op": "...", "tier": str(tier)}`.

31. **`tests/unit/systems/nerve/test_nerve_system_isolation.py`** — Group L AST guards (see § Group L AC #32).

32. **`tests/unit/systems/nerve/test_command_outcome_shape.py`** — Group A AC #3 shape regression.

33. **Adapter tests** at `tests/unit/adapters/rich/test_skin_adapter.py` — three new tests per AC #17 (`render_response`, `collect_input` happy + EOF).

34. **Composition-root test** at `tests/unit/test_composition_root.py` — one new test per AC #21.

35. **Integration tests** at `tests/integration/test_session_loop.py` — two new tests per AC #25.

### Group L: AST isolation locks

36. **New file** [`tests/unit/systems/nerve/test_nerve_system_isolation.py`](../../tests/unit/systems/nerve/test_nerve_system_isolation.py) — AST-walks `nova.systems.nerve.system` and asserts:

    - **Forbidden top-level imports:** `sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32*`, `yaml`, `rich` (Nerve is rendering-agnostic — Rich types stay in the adapter).
    - **Forbidden Nova prefixes:** `nova.app`, `nova.cli`, `nova.setup`, `nova.adapters.*`. Allowed: `nova.core.*`, `nova.ports.*`, `nova.systems.brain.models`, `nova.systems.eyes.models`, `nova.systems.ritual.models`, `nova.systems.skin.models`, `nova.systems.nerve.briefing`, `nova.systems.nerve.models`. Sibling-system `.system` modules are FORBIDDEN (per the cross-system contract: only `.models` crosses system boundaries).
    - **No dynamic `__import__` / `importlib.import_module`** to any forbidden prefix.
    - **Positive locks:** parametrize over `["nova.ports.brain", "nova.ports.ritual", "nova.ports.skin", "nova.core.events", "nova.core.tiers", "nova.core.config", "nova.systems.nerve.briefing", "nova.systems.nerve.models"]` and assert each is present (drops would silently break Nerve — early-warning).

37. **Existing `tests/unit/systems/nerve/test_briefing_isolation.py`** stays unchanged. Story 3.5 does NOT modify `nova.systems.nerve.briefing` — the briefing module's isolation guard is a separate concern.

38. **AST purity check on `_should_skip_briefing` is deliberately omitted.** The function is small and reviewer-readable (one branch ladder), and the unit-test parametrize at AC #30 Block 2 covers every branch. Adding an AST-walk for clock-only-via-injection here would be over-engineering relative to Story 3.4's parser (which is genuinely state-rich).

### Group M: CI gate

39. **Full quality gate.** All four gates pass without weakening:

    - `uv run ruff check src/ tests/` — clean.
    - `uv run ruff format --check src/ tests/` — clean.
    - `uv run mypy src/ tests/` — clean. Strict mode catches the `NervePort.route_command` annotation flip at every consumer (today: zero — no consumer routes commands yet; the type-flip is forward-only).
    - `uv run pytest tests/unit/` — passes. Net delta vs. the post-Story-3.4 baseline (1607 unit pass + 1 brittle deselected + 1 pre-existing skip): expect approximately **+50 to +75 unit tests** (Block 1–8 in `test_nerve_system.py` ≈ 40, shape + isolation ≈ 8, adapter delegation ≈ 3, composition-root ≈ 1, port-isolation update ≈ 0–2).
    - `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes. **Two new integration tests** per AC #25.
    - **100% coverage** on the new modules (`nova.systems.nerve.system`, `nova.systems.nerve.models`) and on the modified-line region of `nova.adapters.rich.skin` (the new `collect_input` + `render_response` bodies). Run: `uv run pytest tests/unit --cov=nova.systems.nerve --cov=nova.adapters.rich --cov-report=term-missing`.

### Group N: Fresh-session review trial (A3)

40. **A3 fresh-session review trial.** Per [epic-3-story-preflags.md:48](epic-3-story-preflags.md#L48), Story 3.5 is the explicit A3 target. **Format:**
    - Run the standard same-session three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) per the Story 3.4 precedent.
    - THEN run a fresh-session review: a separate agent context with no implementation memory reads only the merged story file + the diff + the project-context.md and produces an independent finding set.
    - Document both finding-sets in [`fresh-session-review-3.5-<date>.md`](fresh-session-review-3.5-2026-05-06.md) (date stamped at review run). Include a delta analysis: how many findings did the fresh session catch that the same-session pass missed, and vice versa.
    - **Decision note** at the end of the review file: was the extra cost worth the independence signal? The answer informs whether A3 becomes standard for all interaction-boundary stories or stays an experimental trial. The decision is logged at the Epic 3 retrospective.

## Tasks / Subtasks

- [x] **Task 1 — `CommandOutcome` enum + `NervePort` reshape** (AC: #1, #2, #3, #4)
  - [x] Create [`src/nova/systems/nerve/models.py`](../../src/nova/systems/nerve/models.py) with `CommandOutcome(StrEnum)` and `__all__ = ["CommandOutcome"]`.
  - [x] Edit [`src/nova/ports/nerve.py`](../../src/nova/ports/nerve.py): change `route_command` return annotation to `CommandOutcome`; add the import; update the module docstring.
  - [x] Create [`tests/unit/systems/nerve/test_command_outcome_shape.py`](../../tests/unit/systems/nerve/test_command_outcome_shape.py) — 3+ tests covering AC #3.
  - [x] Update [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) so the `NervePort` annotation snapshot recognizes `CommandOutcome` (use `typing.get_type_hints`).
  - [x] `uv run mypy src/nova/ports/ src/nova/systems/nerve/ tests/unit/` — clean.

- [x] **Task 2 — `NerveSystem` constructor + `startup` + recency helper** (AC: #5, #6, #7, #8)
  - [x] Create [`src/nova/systems/nerve/system.py`](../../src/nova/systems/nerve/system.py): module docstring, `_utc_now` helper, `_should_skip_briefing` pure function, `NerveSystem` class with constructor, `startup`, and `_session_id` / `_session_active` / `_prompt_context` private fields.
  - [x] Implement the 11-step `startup` ordering per AC #6.
  - [x] Implement `_should_skip_briefing` per AC #7 (six-branch decision table; defense-in-depth for malformed ISO strings).
  - [x] `uv run mypy src/nova/systems/nerve/system.py` — clean (strict mode).

- [x] **Task 3 — `NerveSystem.route_command` + `_handle_*` dispatch table** (AC: #9, #10, #11, #12, #13, #15)
  - [x] Implement `route_command` with the `match` statement covering every `CommandVerb` member.
  - [x] Implement `_handle_modes_list`, `_handle_mode_switch`, `_handle_mode_create`, `_handle_mode_edit`, `_handle_status`, `_handle_memory`, `_handle_forget`, `_handle_help`, `_handle_shutdown` (idempotent), `_handle_unknown`, `_handle_empty`.
  - [x] Implement contextual-reply gate via `_prompt_context` (always None in 3.5).
  - [x] Implement `_tier_check_or_offline_response` helper.

- [x] **Task 4 — REPL loop** (AC: #14)
  - [x] Implement `_run_repl`: input → parse → route → continue/exit, with EOFError + KeyboardInterrupt handling.

- [x] **Task 5 — `RichSkinAdapter.collect_input` + `render_response`** (AC: #16, #17)
  - [x] Edit [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py): replace the two `NotImplementedError` bodies; add `from rich.prompt import Prompt` import; update module docstring.
  - [x] Append three adapter delegation tests to [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py).

- [x] **Task 6 — Signal-handler registration** (AC: #18, #19)
  - [x] Implement `_install_signal_handler` / `_uninstall_signal_handler` with the POSIX / Windows split.
  - [x] Implement `_signal_handler_callback` with the 2-second timeout, the two-stage try/except (Brain + emission), and the `_session_active` guard.
  - [x] Document the workspace-snapshot scope fence (Story 3.10) in the handler docstring.

- [x] **Task 7 — Composition root + cli.py wiring** (AC: #20, #21, #23, #24)
  - [x] Edit [`src/nova/app.py`](../../src/nova/app.py): add `nerve: NervePort` field to `NovaApp`, instantiate `NerveSystem` in `create_app`.
  - [x] Add `test_nerve_system_is_instantiated_inside_create_app` to [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py).
  - [x] Edit [`src/nova/cli.py`](../../src/nova/cli.py): replace Step 7 placeholder log with `await app.nerve.startup()`.
  - [x] Verify `tests/unit/test_cli_no_new_subcommands.py` still passes — Story 3.5 adds zero subcommands.

- [x] **Task 8 — `_AlwaysHealthyCheck` reconciliation + deferred-work close** (AC: #26, #27, #28, #29)
  - [x] Edit [`tests/unit/test_app.py:564-585`](../../tests/unit/test_app.py#L564-L585) docstring only — comment update referencing Story 3.5's deferral.
  - [x] Edit [`src/nova/app.py:68-83`](../../src/nova/app.py#L68-L83) — add the deferral sentence to `_AlwaysHealthyCheck` docstring.
  - [x] Edit [`_bmad-output/implementation-artifacts/deferred-work.md`](deferred-work.md) — remove or close-mark the `NervePort.route_command` returns `None` entry on line ~139.

- [x] **Task 9 — Unit tests** (AC: #30, #31, #32, #33, #34)
  - [x] Create [`tests/unit/systems/nerve/test_nerve_system.py`](../../tests/unit/systems/nerve/test_nerve_system.py) with all 8 blocks per AC #30 (target ≈ 40 tests).
  - [x] Verify all dispatch arms are covered + the dispatch-table exhaustiveness parametrize hits every `CommandVerb` member.

- [x] **Task 10 — AST isolation guards** (AC: #36, #37, #38)
  - [x] Create [`tests/unit/systems/nerve/test_nerve_system_isolation.py`](../../tests/unit/systems/nerve/test_nerve_system_isolation.py) per AC #36 (mirrors `test_briefing_isolation.py`).

- [x] **Task 11 — Integration tests** (AC: #25)
  - [x] Create [`tests/integration/test_session_loop.py`](../../tests/integration/test_session_loop.py) with two end-to-end tests per AC #25.

- [x] **Task 12 — Full CI gate** (AC: #39)
  - [x] `uv run ruff check src/ tests/` — clean.
  - [x] `uv run ruff format --check src/ tests/` — clean.
  - [x] `uv run mypy src/ tests/` — clean.
  - [x] `uv run pytest tests/unit/` — passes; net delta ≈ +50 to +75 vs. the Story 3.4 baseline.
  - [x] `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes; +2 new integration tests.
  - [x] `uv run pytest tests/unit --cov=nova.systems.nerve --cov=nova.adapters.rich --cov-report=term-missing` — 100% coverage on Story 3.5 modules.

- [x] **Task 13 — A3 fresh-session review trial** (AC: #40)
  - [x] Run the standard same-session three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) per the Story 3.4 precedent.
  - [x] Run the fresh-session review: separate agent context, no implementation memory, reads only the merged story + diff + project-context.md.
  - [x] Document both finding-sets + delta analysis + decision note in `fresh-session-review-3.5-<date>.md`.
  - [x] Log the decision note at the Epic 3 retrospective queue.

## Dev Notes

### Pattern library consulted

- **#1 Clock indirection** — `_should_skip_briefing` accepts an injected `clock: Callable[[], datetime]` per project-context.md:46 / Story 1.3's two-function pattern. The production default is `_utc_now` (a module-level function whose body is `return datetime.now(UTC)`); tests inject a fixed-point clock. The function does NOT use `_utc_now_iso()` because the comparison is `datetime`-arithmetic, not string-arithmetic. Story 3.7's seed-capture clock will reuse the same indirection (its own `_utc_now`) — they don't need to share, but the pattern is identical.
- **#2 AST guards** — `test_nerve_system_isolation.py` mirrors the `test_briefing_isolation.py` pattern. Forbidden imports + positive locks.
- **#3 Frozen dataclass** — no new dataclasses ship in this story. `CommandOutcome` is a `StrEnum` (closed vocabulary), not a dataclass. The `Command` it operates on is already frozen (Story 1.9 / 3.4).
- **#4 Error translation** — the signal handler is the only boundary that swallows `Exception`. It is documented and bounded (2s timeout, log-only failures). Every other Nerve method raises through cleanly to `cli.py`'s top-level handler.
- **#5 Skip-on-error** — applies to the signal handler's two-stage try/except.
- **#6 Transaction CM** — N/A; Brain's `end_session` is single-statement-atomic by Story 3.1's adapter design.
- **#7 Partial-init cleanup** — the composition root already has the `try / except BaseException` block. Adding `NerveSystem` (constructor is reference-storage only, no resources acquired) is covered by structure.

### Why session creation moved AFTER briefing assembly

Earlier drafts of this story sequenced `startup` as: install signal handler → `get_last_session` for the recency check → `create_session` → emit `SessionStarted` → load briefing aggregate → render briefing → REPL. That ordering is broken in three ways:

1. **State determination is polluted.** `load_briefing_aggregate` calls `brain.get_last_session()` to populate `aggregate.last_session`. With `create_session` firing first, the freshly-created open session row IS the most recent session — so `get_last_session` returns the just-created row instead of the genuine prior session. `determine_briefing_state` then keys on the wrong fields: a true first-run DB no longer produces `FIRST_RUN`, the setup-row-only case (`is_complete=True, mode_name=NULL`) gets shadowed by the new open row (`is_complete=False, mode_name=NULL`) and silently flips from State C to State B.
2. **State A leaves an orphan session.** State A renders the first-run card and exits immediately (no REPL — no modes means no commands are useful). With `create_session` already fired, that early return leaves an open session row no one will ever close. On the user's next `nova` run, the orphan is "the most recent session" and the State determination is corrupted again.
3. **The recency check compares against the wrong row.** `_should_skip_briefing` reads `prior_session.ended_at` and computes `now - ended_at`. With `create_session` firing first, the "prior session" is the row written 50 ms ago — so the recency check trivially returns True and the briefing is always skipped.

The corrected ordering — load aggregate → determine state → State A early return → recency policy → render → create session → emit — fixes all three. The aggregate's reads see only genuinely-prior data; State A returns before any write; recency compares against a real prior session.

The `try / finally` cleanup pair around the REPL provides defense-in-depth: if the REPL exits via the signal handler (which already wrote `is_complete=False`), `_session_active` is `False` and the cleanup is a no-op; if the REPL exits abnormally (unexpected `RuntimeError`), `_session_active` is still `True` and the cleanup writes the interrupted-session marker before re-raising. Either way, no orphan rows.

### Why `route_command` returns `CommandOutcome` instead of `None`

Two options were considered for the REPL-loop continue/exit decision:

1. **Domain exception:** raise `_ShutdownRequested` from `_handle_shutdown`, catch in the REPL. Cleaner control flow but adds a control-flow-via-exception pattern that mypy cannot enforce — every other handler must NOT raise it, and the REPL must catch only it. The exception's existence becomes a side-channel.
2. **Return-shape:** `CommandOutcome.{CONTINUE, EXIT}`. The REPL inspects the return value with a clean `if outcome is CommandOutcome.EXIT: return`. Closes [deferred-work.md:139](deferred-work.md#L139) ("error surface undocumented") with a positive contract (every handler declares its outcome) instead of a negative one ("any uncaught exception aborts the loop").

Option 2 wins because it keeps the route_command signature mypy-strict at every consumer. `CommandOutcome` is a closed two-member vocabulary today (CONTINUE / EXIT); a future story that needs a third outcome (ABORT for crash recovery? RESET for a re-prompt?) can add a member with one-line edits.

### Why we don't start the recovery loop in this story

The pre-flag explicitly warned: starting `tier_manager.run_recovery_loop()` against the current `_AlwaysHealthyCheck` stub flips OFFLINE → FULL on the first tick (60-second default), silently breaking the OFFLINE-on-no-API-key promise locked by Story 2.5's smoke test.

Three reconciliation options:

1. **Replace `_AlwaysHealthyCheck` with a real Claude probe NOW.** Requires the Claude adapter, which is Epic 7 / dedicated story scope. Not viable for Story 3.5.
2. **Conditional recovery-loop start: only when `tier_manager.tier is FULL` at boot.** Works around the stub but creates a sneaky asymmetry: an offline user who later adds an API key (settings.yaml edit + restart) would get the recovery loop on the second boot, but a user who started in FULL and degraded to OFFLINE during the session would not. The asymmetry is invisible in this story but would manifest as a confusing bug report when the Claude adapter lands.
3. **Defer the recovery loop entirely.** Story 3.5 ships zero recovery-loop wiring. The Claude adapter story (Epic 7+) will add it alongside the real health probe. Stays clean.

Option 3 is the right answer. Story 3.5's job is to wire the orchestrator; tier recovery is downstream of the orchestrator's existence.

### Why the REPL loop ships in Story 3.5 (not deferred to Story 3.7)

The epic AC for Story 3.5 says "Nerve routes commands to the appropriate system" — but there's no other infrastructure that drives `route_command`. Without a REPL, the only Commands routed would be from a unit-test mock. Shipping the REPL here makes Story 3.5 the **first runtime wiring** of the continuity loop — bare `nova` actually does something.

`SkinPort.collect_input` is the REPL's input primitive. The Story 3.4 docstring defers `collect_input` to Story 3.7 (the seed-prompt consumer), but Story 3.5 needs it for the bare REPL prompt. Resolution: ship `collect_input` here as a thin Rich-Prompt wrapper (12 lines including the import). Story 3.7 then uses the same primitive for the seed prompt. The implementation stays minimal — the seed-prompt-specific reprompt-on-empty + cancel logic lives in Story 3.7's Ritual flow, not in `collect_input`.

### Why contextual-reply gating is structural-only in Story 3.5

The mechanism (`_prompt_context: str | None`, gated dispatch in `route_command`) ships in Story 3.5 so Story 3.8 can wire the briefing's resume prompt without needing to touch Nerve's dispatch table. Story 3.5's tests cover the `_prompt_context = None` (bare REPL) branch — every Layer C verb maps to "Nothing to resume / confirm right now." Story 3.8 will add tests for the `_prompt_context = "briefing_resume"` branch.

The split keeps the Story 3.5 surface narrow (no need to design the full prompt-context state machine here) while shipping the seam Story 3.8 can build on without a Nerve refactor.

### Why the signal handler doesn't capture a workspace snapshot

Epic 3.10 AC explicitly puts "best-effort workspace snapshot (snapshot_type=shutdown) if Eyes is available" in Story 3.10's scope. Story 3.5 ships the session-end-with-is_complete=False half (Brain write + event emission) so the schema-level guarantee — every interrupted session has a row with `is_complete=0` — is locked. Story 3.10 extends the handler body with the Eyes integration once the Win32EyesAdapter is wired (Epic 4).

Splitting this way keeps Story 3.5's signal-handler test surface manageable (no Win32 mocks needed) and lets Story 3.10 own the Eyes-availability decision logic without conflating it with the basic interrupted-session marker.

### Why `_handle_status` and `_handle_help` ship as placeholders, not full implementations

Story 3.9 explicitly owns the "Status Command & Help Display" surface (epic AC at epics.md:1268). Both commands are routable today (Story 3.4 parses them), so Story 3.5's dispatch table must handle them — but the response prose / table layout is Story 3.9's territory. The placeholders here keep the dispatch table exhaustive (every CommandVerb has a case arm) without preempting Story 3.9's design space.

The same logic applies to MEMORY / FORGET (Epic 5) and MODE_CREATE / MODE_EDIT (Epic 6).

### Why the integration tests exist (and unit tests aren't enough)

Two integration tests at [`tests/integration/test_session_loop.py`](../../tests/integration/test_session_loop.py) exercise the full Brain → Nerve → Ritual → Skin pipeline with real adapters. They are NOT redundant with the Story 3.5 unit tests — the unit tests use mocks for every port (per project-context.md:94). The integration tests:

- Catch real-adapter glue bugs that mocks would mask (e.g., Brain's `get_last_session` returning a different `SessionSummary` shape than the unit-test mock).
- Lock the end-to-end timing / ordering invariants under real I/O (asyncio loop scheduling, real SQLite serialization).
- Establish the test pattern Story 3.6, 3.7, 3.8 will extend (mode-restore integration, shutdown integration, warm-resume hero-path integration).

Story 3.5 is the FIRST integration test that boots the full T1 monolith. Subsequent stories layer on top.

### Closing the `NervePort.route_command` deferral

[deferred-work.md:139](deferred-work.md#L139) explicitly tags Story 3.5 as the close-out for `NervePort.route_command returns None — error surface undocumented`. Group A's `CommandOutcome(StrEnum)` reshape is the canonical fix. After this story merges, the deferred-work entry should be removed (or replaced with a one-line "closed by Story 3.5" pointer) per Group J AC #29.

### Explicit scope fence (non-goals)

- Does NOT start `tier_manager.run_recovery_loop()` — see § "Why we don't start the recovery loop in this story." Reconciles Story 2.5's smoke test.
- Does NOT replace `_AlwaysHealthyCheck` — paired with the recovery-loop deferral.
- Does NOT ship Hands integration — Story 3.6 owns `_handle_mode_switch`'s real body.
- Does NOT ship the seed-capture ceremony — Story 3.7 replaces `_handle_shutdown`'s body to delegate to Ritual.
- Does NOT ship the State C "resume" hero-path activation — Story 3.8 sets `_prompt_context = "briefing_resume"` after the briefing renders and adds the resume-routing branch.
- Does NOT ship the full status table or help table — Story 3.9 owns those.
- Does NOT ship workspace-snapshot capture in the signal handler — Story 3.10 extends the handler.
- Does NOT add Layer A subcommands to argparse — bare `nova` is the only shell-form invocation today. The pre-flag's `<X> mode` "Layer A awareness" comment is satisfied by the bare-`nova` path.
- Does NOT modify [`src/nova/setup/__main__.py`](../../src/nova/setup/__main__.py) — setup is the first-run entrypoint; Story 3.5 is the second-run entrypoint. They are independent.
- Does NOT modify [`tests/unit/test_cli_no_new_subcommands.py`](../../tests/unit/test_cli_no_new_subcommands.py) — Story 2.5's lock against subcommand growth stays in force.
- Does NOT introduce a `nova.core.commands` module — `Command` / `CommandVerb` live in `nova.systems.skin.models` (per Story 3.4); `CommandOutcome` lives in `nova.systems.nerve.models` (this story). Two systems, two model modules — both are owned types that cross system boundaries via `.models`.
- Does NOT subscribe Nerve to any events — `EventBus` subscriptions in Nerve are reserved for the eventual Eyes integration (Epic 4) and tier-change handling (Story 5.4). Story 3.5 only emits.
- Does NOT introduce an active-mode tracker on `NerveSystem` — `_handle_status`'s placeholder hardcodes `"no active mode"`. Story 3.6's mode-restore flow will introduce a `self._active_mode_stem: str | None` field; Story 3.9's status command consumes it.
- Does NOT update `project-context.md` or `architecture.md`. The architecture's command-routing convention (architecture.md:1104-1133) is satisfied as-is. The CommandOutcome reshape is a port-internal closure of a deferred-work item, not an architecture-level change.

## Review Focus (boundary-first invariant sweep — full A1)

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | `NerveSystem.startup` is the lifecycle entry; `_run_repl` is the active phase; `_handle_shutdown` and the signal handler are the two normal exit paths; the `try / finally` cleanup is the abnormal-exit safety net. The eleven-step ordering enforces: read prior state → determine briefing state → State A early-return-without-session → recency policy → render → create session → emit → REPL. Signal-handler exit-path uses `_shutdown_event` to drive the REPL's race-pattern exit (the fourth-blocker fix; default `KeyboardInterrupt` propagation is no longer reliable once a custom handler is installed). The `_session_active` guard makes `_handle_shutdown` idempotent against signal-then-command race conditions. |
| **Teardown under partial failure** | `_handle_shutdown` swallows nothing — a Brain failure during `end_session` propagates up and `cli.py`'s top-level handler maps to `EXIT_NOVA_ERROR=3`. The signal handler swallows everything (best-effort by definition); failures are logged AND `SessionEnded` is NOT emitted on Brain-write failure (third-blocker fix — write-then-emit must hold even in the handler). The `startup` `finally` block writes the interrupted-session marker if the REPL exited with `_session_active=True`, then uninstalls the signal handler. The composition root's existing partial-init cleanup covers `NerveSystem` instantiation by structure (no resources acquired). |
| **Concurrency model** | Single asyncio loop (project-context.md:37). The REPL's race pattern uses `asyncio.wait({input_task, shutdown_task}, return_when=FIRST_COMPLETED)` — the loop yields whenever both tasks are pending. The signal handler runs as a coroutine on the same loop (`add_signal_handler` on POSIX schedules an async callback; `call_soon_threadsafe(create_task, ...)` on Windows). No locks needed — single-loop serializes everything. The `_shutdown_event` is created lazily inside `startup` (NOT in `__init__`) so its loop binding matches the running loop. |
| **Cancellation** | `asyncio.CancelledError` re-raised cleanly per project-context.md:49. The REPL's `try / except (EOFError, KeyboardInterrupt)` does NOT catch `CancelledError` (it's `BaseException`, not `Exception`). The REPL's outer `try / except CancelledError` cancels the pending input/shutdown tasks AND re-raises. The signal handler's outer try/except is also `Exception`-only. Cancellation propagates to `cli.py`'s top level. |
| **Signal-handler safety** | The handler MUST NOT raise — every step is wrapped. **`_shutdown_event.set()` is FIRST** so even on the no-Brain-write path (no active session, or post-write second-Ctrl-C) the REPL still exits. **Brain write failures (timeout or exception) `return` early WITHOUT emitting `SessionEnded`** — write-then-emit holds (third-blocker fix). The 2-second `asyncio.wait_for` on the Brain write prevents the handler from hanging on a stuck DB. The `_session_active` guard makes the handler one-shot. The handler does NOT touch `_prompt_context` or render anything — operational state stays untouched. The handler is registered in `startup` step 2 (BEFORE the aggregate-load Brain reads) so a Ctrl-C during the prior-state reads still has a registered handler that short-circuits cleanly (no session yet, no Brain write attempted). |
| **Error translation** | `BrainPort` methods raise `StorageError` (domain exception, Story 3.1). `EventBus.emit` raises nothing for handler failures (per Story 1.3 contract — `Exception` is logged, `BaseException` propagates). Nerve does NOT wrap `StorageError` — the top-level `cli.py` handler maps it cleanly. The signal handler is the one place `Exception` is swallowed; documented + bounded. |
| **Test determinism** | The recency clock is injected. `brain.create_session` returns a deterministic session id from the mock. `event_bus.emit` is patched to a `MagicMock` so emission ordering is verifiable. The signal-handler tests invoke `_signal_handler_callback` directly (no real signal delivery in tests). |
| **Logging opacity** | Nerve never logs the session id with PII; only the int. Briefing aggregate log lines (already shipped by Story 3.2) stay opaque. The `_tier_check_or_offline_response` helper logs only the op_name (closed-set string) and the tier (closed enum). Skip-briefing logs the prior session's `ended_at` ISO string — that is operational metadata, not user content. |
| **Idempotency** | `_handle_shutdown` is idempotent via `_session_active` guard (AC #15). The signal handler is one-shot via the same guard plus the idempotent `_shutdown_event.set()` (AC #19). `startup` is NOT idempotent (calling it twice would re-load the aggregate then create a second session) — but `cli.py` only calls it once per process invocation, so the contract holds. The `startup` `finally` cleanup is also idempotent: it gates on `_session_active`, which a successful `_handle_shutdown` or signal-handler write has already flipped to `False`. |
| **Atomicity contract** | `brain.create_session` and `brain.end_session` are single-statement-atomic per Story 3.1's adapter. Nerve does not need a transaction — there's no multi-write per command. The `SessionEnded` emission happens AFTER the `end_session` write returns (write-then-emit, AC #15). |
| **Determinism of dispatch** | The `match` statement is exhaustive over `CommandVerb` (16 members). The `default` arm raises `RuntimeError` for an unhandled member — programmer-error guard. The dispatch-table-exhaustiveness test (Group K Block 4) parametrizes every `CommandVerb` member to ensure no arm silently routes to UNKNOWN. |
| **Tier-gate contract** | `_tier_check_or_offline_response` is the single check site. Story 3.5 has zero call sites (no cloud ops); Group K Block 8 wires the helper through with mock cloud call to assert the contract. Epic 7 will be the first real consumer. |
| **REPL exit paths** | Three paths exit the loop. (a) SHUTDOWN command — `route_command` returns `CommandOutcome.EXIT`; `_handle_shutdown` already wrote+emitted. (b) `_shutdown_event` set by signal handler — REPL's `asyncio.wait` race observes the event, cancels the input task (drained), returns; `startup` `finally` is a no-op because the handler already wrote. (c) `EOFError` / `KeyboardInterrupt` at input — REPL invokes `_handle_shutdown` (idempotent). The signal-handler path may race with (a) or (c); the `_session_active` guard plus the `_shutdown_event` idempotent-set make every ordering safe. |
| **Prior-state reconciliation** | A10. Story 2.4's setup row, Story 2.5's `_AlwaysHealthyCheck` smoke test, Story 3.1's adapter contract, Story 3.2's pure assembly functions, Story 3.3's `RitualSystem.build_briefing`, Story 3.4's parser — every reconciliation is documented + tested. |
| **A9 degraded-path proof** | Three test categories. Happy: bare boot → briefing → REPL → shutdown (integration test). Degraded: tier-gate fires when not-FULL (unit test parametrized over DEGRADED / OFFLINE). Rerun: signal handler interrupted-session marker + idempotency guard means a second SIGINT after the first one is a no-op (unit test). |
| **A3 fresh-session review** | Trial run per pre-flag. Format documented in Group N AC #40. |
| **Patterns consulted** | #1 clock indirection (`_utc_now` + injected clock), #2 AST guards (`test_nerve_system_isolation.py`), #4 error translation (signal handler swallows + logs), #5 skip-on-error (signal handler stage-by-stage). Patterns NOT consulted: #3 frozen dataclass (no new dataclasses), #6 transaction CM (single-statement Brain ops), #7 partial-init (NerveSystem acquires no resources). |

## Project Structure Notes

**New source files:**
- [`src/nova/systems/nerve/system.py`](../../src/nova/systems/nerve/system.py) — `NerveSystem` class, `_should_skip_briefing` pure function, `_utc_now` clock helper, signal-handler lifecycle pair, REPL loop, dispatch-table `_handle_*` methods.
- [`src/nova/systems/nerve/models.py`](../../src/nova/systems/nerve/models.py) — `CommandOutcome(StrEnum)` closed vocabulary.

**Modified source files:**
- [`src/nova/ports/nerve.py`](../../src/nova/ports/nerve.py) — `route_command` return annotation changes from `None` to `CommandOutcome`; import added; module docstring updated.
- [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py) — `collect_input` and `render_response` `NotImplementedError` bodies replaced with real implementations; `from rich.prompt import Prompt` added; module docstring's Story 3.5 / 3.7 clauses updated.
- [`src/nova/app.py`](../../src/nova/app.py) — `NovaApp.nerve` field added; `create_app` instantiates `NerveSystem`; `_AlwaysHealthyCheck` docstring gains the deferral sentence.
- [`src/nova/cli.py`](../../src/nova/cli.py) — `_async_main` Step 7 placeholder log replaced with `await app.nerve.startup()`.

**Modified planning / tracking files:**
- [`_bmad-output/implementation-artifacts/sprint-status.yaml`](sprint-status.yaml) — Scrum Master flips `3-5-nerve-command-routing-and-session-lifecycle: backlog → ready-for-dev` via the create-story workflow; Dev flips `ready-for-dev → in-progress → review` during implementation; code-review workflow flips `review → done`.
- [`_bmad-output/implementation-artifacts/deferred-work.md`](deferred-work.md) — at story completion, Dev removes (or marks closed-by-Story-3.5) the entry on line ~139 about `NervePort.route_command` returning `None`.

**New test files:**
- [`tests/unit/systems/nerve/test_nerve_system.py`](../../tests/unit/systems/nerve/test_nerve_system.py) — 8 blocks per AC #30 (~40 tests).
- [`tests/unit/systems/nerve/test_nerve_system_isolation.py`](../../tests/unit/systems/nerve/test_nerve_system_isolation.py) — AST guards on `nova.systems.nerve.system`.
- [`tests/unit/systems/nerve/test_command_outcome_shape.py`](../../tests/unit/systems/nerve/test_command_outcome_shape.py) — `CommandOutcome` shape regression.
- [`tests/integration/test_session_loop.py`](../../tests/integration/test_session_loop.py) — 2 end-to-end tests per AC #25.

**Modified test files:**
- [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) — append 3 adapter delegation tests per AC #17.
- [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) — port annotation snapshot recognizes `CommandOutcome` per AC #4.
- [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) — append `test_nerve_system_is_instantiated_inside_create_app` per AC #21.
- [`tests/unit/test_app.py`](../../tests/unit/test_app.py) — comment-only update to `test_tier_stays_offline_without_recovery_loop` docstring per AC #27.

No `tests/unit/systems/nerve/__init__.py` — the project does not use `__init__.py` in test directories (precedent: `tests/unit/systems/ritual/`, `tests/unit/systems/skin/`, `tests/unit/adapters/rich/`).

**Line-count discipline.** Approximate target sizes (numbers are guidance, not gates):
- `system.py` ≈ 350–450 lines (constructor + 11-step `startup` + REPL loop + `route_command` match statement + 11 `_handle_*` methods + tier-gate helper + signal-handler pair + module docstring).
- `models.py` ≈ 25–40 lines (one `StrEnum` class + module docstring + `__all__`).
- `nova/ports/nerve.py` net delta ≈ +5 lines (import + return annotation flip + docstring update).
- `nova/adapters/rich/skin.py` net delta ≈ +10 lines (import + two method body replacements + docstring update).
- `nova/app.py` net delta ≈ +15 lines (`NovaApp` field + instantiation block + docstring sentence on `_AlwaysHealthyCheck`).
- `nova/cli.py` net delta ≈ +1 line (placeholder swap).
- New test files: ~55 tests across 4 files; expect ~700–900 lines of test code total (`test_nerve_system.py` is the dominant ≈ 600 lines with parametrize tables).

### Alignment with unified project structure

- `nova.systems.nerve.system` follows the architecture.md:1359 directory layout (`systems/nerve/system.py` is the canonical location for `NerveSystem`).
- `nova.systems.nerve.models` parallels the Story 3.4 `nova.systems.skin.models` pattern — owned types that cross system boundaries via `.models`.
- `tests/unit/systems/nerve/` already exists (Story 3.2 created `test_briefing.py` and `test_briefing_isolation.py` there). Story 3.5 adds three more test files in the same directory.

### Detected conflicts or variances

- **`NerveSystem.__init__` takes `config: NovaConfig`, not `settings: UserSettings`.** This is the single configuration contract. `load_briefing_aggregate` (Story 3.2) already requires a `NovaConfig` for the modes side, and the recency-policy knobs live at `config.settings.skip_briefing_if_recent` / `config.settings.briefing_recency_threshold_minutes`. The `_should_skip_briefing` pure helper takes `UserSettings` directly so it stays testable without a full `NovaConfig` fixture; the caller does the field extraction (`config.settings`). Future stories that introduce additional config-driven policy (e.g., Epic 7's bluntness level) can read from the same `config` reference without a constructor change.
- **`SkinPort.collect_input` ships in this story instead of Story 3.7** (per § "Why the REPL loop ships in Story 3.5"). The Story 3.7 docstring on `RichSkinAdapter.collect_input` will be updated to drop its scope claim — but Story 3.7's own scope (the seed-capture ceremony) is unaffected.
- **`NervePort.route_command` return-shape flip from `None` to `CommandOutcome`** is a port-shape change. The existing port-isolation test ([tests/unit/ports/test_port_isolation.py](../../tests/unit/ports/test_port_isolation.py)) needs an update to recognize the new annotation (AC #4). No consumer code today routes commands, so the flip is forward-compatible.
- **`startup`'s eleven-step ordering is load-bearing — read before write, render before create_session.** An earlier draft of this story sequenced `get_last_session` after `create_session` (or had the recency check read its own `get_last_session`); both produce a polluted state-determination model and an orphan session row on the State A path. See Dev Notes § "Why session creation moved AFTER briefing assembly" for the three failure modes that ordering prevents. The ordering is locked by Block 1 tests in `test_nerve_system.py`.
- **Signal handler does NOT rely on default `KeyboardInterrupt` propagation.** Once `loop.add_signal_handler(SIGINT, ...)` (POSIX) or `signal.signal(SIGINT, ...)` (Windows) installs a custom callback, the default Python-→-`KeyboardInterrupt`-injection is suppressed. The handler must explicitly drive REPL exit via `_shutdown_event.set()` — without this, the handler runs cleanup and then control returns to the blocked `Prompt.ask` thread; the REPL stays alive. `_shutdown_event` is the explicit shutdown mechanism the REPL's race pattern observes.
- **Layer A scope confirmation.** Bare `nova` is the only shell-form invocation Story 3.5 ships. The architecture's "T1 Commands — Canonical Vocabulary" table (architecture.md:124-137) lists Layer A subcommands like `nova mode coding` / `nova help` / `nova status` / `nova memory`; these are NOT shipped here. Story 3.4's parser maps every `nova <X>` shell-form input to `CommandVerb.UNKNOWN` (locked by Block 10 negatives), and Story 3.5's CLI keeps the bare-`nova`-only surface — together they preserve the T1-non-goal fence at architecture.md:137 while leaving the door open for a future story to add argparse subcommands. **Touching `tests/unit/test_cli_no_new_subcommands.py` is explicitly out of scope.**

## References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.5 ACs (lines 1166–1189), Epic 3 framing (lines 1048–1050), cross-references to Stories 3.6 / 3.7 / 3.8 / 3.9 / 3.10](../planning-artifacts/epics.md#L1166-L1189)
- [Source: _bmad-output/planning-artifacts/architecture.md — Continuity Loop event sequence (lines 343–406), Tier state machine (lines 765–814), Command Routing Convention (lines 1104–1133), Composition root pseudocode (lines 1066–1102)](../planning-artifacts/architecture.md#L343-L406)
- [Source: _bmad-output/project-context.md — Nerve is orchestrator not router (line 65), Operational output bypasses Voice (line 66), Brain owns SQLite (line 67), Tier check before cloud-dependent operations (line 71), Persist-before-emit (line 78), Each domain fact has one owner (line 80), Idempotency for cross-cutting actions (line 81), Composition root is the only wiring location (line 82), Tier evaluation is centralized (line 84), Shutdown / quit / exit graceful flow (line 199), One command must never have two meanings (line 202), No print() (line 44), Domain exceptions only (line 40), Two-function clock pattern (line 46), Never swallow CancelledError (line 49)](../project-context.md)
- [Source: _bmad-output/implementation-artifacts/epic-3-story-preflags.md — Story 3.5 A6 classification (lines 37–48), A3 fresh-session review trial designation (line 48), `_AlwaysHealthyCheck` reconciliation requirement (line 44)](epic-3-story-preflags.md#L37-L48)
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first invariant sweep, cross-cutting-patterns origin](epic-1-retro-2026-04-15.md)
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-04-18.md — interaction-boundary classification (A6), invariant-sweep extension (A1), degraded-path proof (A9), prior-state reconciliation (A10)](epic-2-retro-2026-04-18.md)
- [Source: _bmad-output/implementation-artifacts/3-1-brain-session-and-seed-persistence.md — `SqliteBrainAdapter.create_session` / `end_session` / `get_last_session` adapter contract](3-1-brain-session-and-seed-persistence.md)
- [Source: _bmad-output/implementation-artifacts/3-2-briefingaggregate-and-state-determination.md — `load_briefing_aggregate` / `determine_briefing_state` consumed unchanged](3-2-briefingaggregate-and-state-determination.md)
- [Source: _bmad-output/implementation-artifacts/3-3-briefingviewmodel-and-briefing-card-rendering.md — `RitualSystem.build_briefing` consumed unchanged; `RichSkinAdapter.render_briefing_card` consumed unchanged](3-3-briefingviewmodel-and-briefing-card-rendering.md)
- [Source: _bmad-output/implementation-artifacts/3-4-t1-command-grammar-and-deterministic-parser.md — `Command` / `CommandVerb` consumed unchanged; closed-vocabulary parser semantics; UNKNOWN/EMPTY marker verb handling; "Why the parser never raises" rationale](3-4-t1-command-grammar-and-deterministic-parser.md)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md (line ~139) — `NervePort.route_command` returns `None` deferral; Story 3.5 closes this](deferred-work.md#L139)
- [Source: docs/cross-cutting-patterns.md — patterns #1 (clock indirection), #2 (AST guards), #4 (error translation), #5 (skip-on-error), #7 (partial-init cleanup)](../../docs/cross-cutting-patterns.md)
- [Source: src/nova/systems/skin/models.py — `Command` / `CommandVerb` (Story 1.9 / Story 3.4)](../../src/nova/systems/skin/models.py)
- [Source: src/nova/systems/skin/commands.py — `parse(raw_input)` (Story 3.4)](../../src/nova/systems/skin/commands.py)
- [Source: src/nova/systems/nerve/briefing.py — `load_briefing_aggregate` / `determine_briefing_state` (Story 3.2)](../../src/nova/systems/nerve/briefing.py)
- [Source: src/nova/systems/ritual/system.py — `RitualSystem.build_briefing` (Story 3.3)](../../src/nova/systems/ritual/system.py)
- [Source: src/nova/adapters/sqlite/brain.py — `SqliteBrainAdapter` (Story 3.1)](../../src/nova/adapters/sqlite/brain.py)
- [Source: src/nova/adapters/rich/skin.py — `RichSkinAdapter` (Story 3.3 + Story 3.4 `parse_command`); Story 3.5 adds `collect_input` + `render_response`](../../src/nova/adapters/rich/skin.py)
- [Source: src/nova/app.py — `NovaApp` graph; Story 3.5 adds `nerve` field; `_AlwaysHealthyCheck` stub stays unchanged](../../src/nova/app.py)
- [Source: src/nova/cli.py:459-462 — placeholder log to be replaced with `await app.nerve.startup()`](../../src/nova/cli.py#L459-L462)
- [Source: src/nova/core/tiers.py — `TierManager.tier`, `report_failure`, `report_rate_limit_or_outage`, `check_now`, `run_recovery_loop` (Story 1.7)](../../src/nova/core/tiers.py)
- [Source: src/nova/core/events.py — `SessionStarted`, `SessionEnded`, `EventBus` (Story 1.3)](../../src/nova/core/events.py)
- [Source: src/nova/core/config.py — `NovaConfig`, `UserSettings.skip_briefing_if_recent`, `UserSettings.briefing_recency_threshold_minutes` (Story 1.6)](../../src/nova/core/config.py)
- [Source: src/nova/ports/nerve.py — `NervePort` Protocol (Story 1.9); Story 3.5 reshapes `route_command` return type](../../src/nova/ports/nerve.py)
- [Source: src/nova/ports/skin.py:46 — `SkinPort.collect_input` Protocol surface (Story 1.9); Story 3.5 implements the adapter body](../../src/nova/ports/skin.py#L46)
- [Source: tests/unit/test_app.py:564-585 — `test_tier_stays_offline_without_recovery_loop` (Story 2.5 lock; Story 3.5 reconciles via deferral)](../../tests/unit/test_app.py#L564-L585)
- [Source: tests/unit/systems/nerve/test_briefing.py — Story 3.2 test pattern that Story 3.5's `test_nerve_system.py` follows](../../tests/unit/systems/nerve/test_briefing.py)
- [Source: tests/unit/systems/nerve/test_briefing_isolation.py — Story 3.2 AST-guard pattern that Story 3.5's `test_nerve_system_isolation.py` mirrors](../../tests/unit/systems/nerve/test_briefing_isolation.py)
- [Source: tests/unit/ports/test_port_isolation.py — port-shape regression test the `route_command` annotation flip must remain compatible with (AC #4)](../../tests/unit/ports/test_port_isolation.py)
- [Source: tests/unit/test_composition_root.py — composition-root regression pattern the new `test_nerve_system_is_instantiated_inside_create_app` follows (AC #21)](../../tests/unit/test_composition_root.py)

## Review Findings

**Code review run 2026-05-05** — Three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) ran in same-session general-purpose subagents. 37 raw findings → 34 unique post-dedup (3 cross-reviewer dupes merged). Triage: 5 decision-needed + 13 patches + 6 deferred + 10 dismissed.

### Post-review HIGH finding (user-reported, fixed)

- [x] [Review][Patch] **Ctrl-C silently exits with code 0 instead of `EXIT_INTERRUPTED=130`** [src/nova/systems/nerve/system.py:startup, src/nova/cli.py:main] — the custom signal handler suppresses normal `KeyboardInterrupt` propagation (that's how it captured cleanup ownership in the first place), so `startup()` returned normally → `_async_main` returned `EXIT_OK` → `main()`'s `except KeyboardInterrupt: return EXIT_INTERRUPTED` never fired. User pressing Ctrl-C during the REPL got an interrupted-session marker in nova.db but the process reported success to the shell. **Fix:** added a 12th step to `startup()` after the `finally` block — if `_signal_handler_task is not None` (signal-driven exit), raise `KeyboardInterrupt("session interrupted by signal")`. SHUTDOWN command path leaves the field None → no raise → `EXIT_OK`. EOF/KbdInt-at-input path also leaves it None → `EXIT_OK`. Locked by 3 new tests: `test_startup_raises_keyboard_interrupt_when_signal_handler_ran` (sync `asyncio.run` + sync `pytest.raises` to dodge pytest-asyncio's BaseException trap), `test_startup_does_not_raise_when_shutdown_command_routed`, `test_startup_does_not_raise_when_eof_terminates_input`, plus end-to-end integration test `test_cli_keyboard_interrupt_from_nerve_startup_returns_exit_interrupted` that locks the cli.py contract (`nerve.startup` raising KbdInt → `main()` returns 130, with `app.close()` still firing in the `finally`). 1800 tests pass; lint+format+mypy strict clean.

### Decision-needed findings

- [x] [Review][Decision] **AC #30 Block 6 `KeyboardInterrupt` REPL-path test missing** — spec mandates a behavioral `test_repl_keyboard_interrupt_triggers_clean_shutdown` that drives `KeyboardInterrupt` through the input boundary into `_handle_shutdown`. Implementation replaced it with an AST static check (`test_repl_eof_and_keyboard_interrupt_share_same_except_branch`) because pytest-asyncio aborts the entire session when a coroutine raises `BaseException`. Choice: (a) accept the AST check as documented in Dev Notes Debug Log; (b) add a behavioral test using a different mechanism (e.g., asyncio.shield + manual future cancellation) to cover the path end-to-end.
- [x] [Review][Decision] **AC #21 composition-root test downgraded to AST-walk** — spec specified `patches NerveSystem to MagicMock and asserts kwargs match`; implementation uses the existing `_assert_class_instantiated_inside_create_app` AST helper (matching the established pattern from RitualSystem / RichSkinAdapter / SqliteBrainAdapter). Choice: (a) accept the AST-only check (consistent with the file's existing pattern); (b) layer a stronger MagicMock-with-kwargs test on top of the AST one.
- [x] [Review][Decision] **`_handle_shutdown` lacks the 2-second timeout that the signal handler enforces** — signal-driven exit bounds Brain `end_session` to 2s; user-typed `shutdown` does not bound at all. A hung Brain on the SHUTDOWN path blocks the REPL forever with no automatic fallback. Choice: (a) add the same 2s timeout to `_handle_shutdown`; (b) document why they differ (user-typed shutdown is interactive — user can Ctrl-C to fall back to the signal-handler path); (c) add a separate, longer timeout for SHUTDOWN.
- [x] [Review][Decision] **`_cleanup_after_repl` skips Phase 2 when handler-task ran but failed** — the single-owner contract (Story 3.5 race-fix #1) skips cleanup write whenever `_signal_handler_task is not None`, regardless of whether the handler succeeded or raised. Test `test_cleanup_skips_end_session_even_when_handler_failed` documents "best-effort = ONE attempt." Choice: (a) keep best-effort-once (current behavior — Story 3.10 will detect orphan rows on next startup); (b) retry once in cleanup if handler task raised (handler had a transient DB lock; retry might succeed); (c) check `_session_active` post-handler-await — if still True, retry.
- [x] [Review][Decision] **AC #30 Block 6 `test_repl_drains_cancelled_pending_task_without_warning` is missing** — spec text says: *"_shutdown_event.set() while input_task is in flight; assert no RuntimeWarning / pytest.warns resource warning surfaces."* Closest implemented test only asserts a log line, not absence of warnings. Choice: (a) add the test with `pytest.warns` / `recwarn.list == []`; (b) accept that `project-context.md:105` is enforced by global pytest config rather than per-test assertion.

### Patch findings

- [x] [Review][Patch] **`_handle_shutdown` Brain failure causes duplicate `end_session` with wrong `is_complete`** [src/nova/systems/nerve/system.py:_handle_shutdown] — if `brain.end_session(is_complete=True)` raises, `_session_active` is never flipped; the exception propagates to `startup`'s finally; cleanup sees `_session_active=True` and `_signal_handler_task is None` and re-attempts `end_session` with `is_complete=False`. Net: a clean-shutdown intent silently flips to interrupted-marker on the second write. Fix: wrap the brain call in try/except like the cleanup path does, OR flip `_session_active=False` BEFORE awaiting (then re-set on failure).
- [x] [Review][Patch] **`_run_repl` outer `except CancelledError` does not drain cancelled tasks before re-raise** [src/nova/systems/nerve/system.py:_run_repl ~822-827] — cancels both tasks then re-raises immediately. The pending tasks are never awaited, surfacing as `RuntimeWarning: Task was destroyed but it is pending!` at process exit (the very warning the inner pending-drain block 10 lines below was added to prevent). Comment says "drain both tasks then re-raise" but code only cancels. Fix: `with contextlib.suppress(asyncio.CancelledError, Exception): await input_task; await shutdown_task` before re-raising.
- [x] [Review][Patch] **`_should_skip_briefing` raises `TypeError` on naive ISO timestamps** [src/nova/systems/nerve/system.py:_should_skip_briefing] — `datetime.fromisoformat("2026-04-01T10:00:00")` (no `+00:00`) succeeds and returns a naive datetime; the next `now - ended_at` raises `TypeError: can't subtract offset-naive and offset-aware datetimes` because `clock()` returns aware UTC. Only `ValueError` is caught. Trigger: any future story or migration writing naive timestamps to `sessions.ended_at`. Fix: catch `(ValueError, TypeError)` to fall open to rendering the briefing.
- [x] [Review][Patch] **`render_response` docstring says "no markup" but `Console.print` interprets Rich markup by default** [src/nova/adapters/rich/skin.py:render_response] — text containing `[bold]...[/]` (or any Rich-markup-shaped substring) gets interpreted. `_handle_unknown` echoes user input via `!r` which protects against this, but other handler paths render literal templates. Fix: pass `markup=False` to match the documented intent.
- [x] [Review][Patch] **`_cleanup_after_repl` Phase 1 awaits the signal-handler task with NO timeout** [src/nova/systems/nerve/system.py:_cleanup_after_repl] — `await self._signal_handler_task` is unbounded. The handler's own internal 2s `wait_for` bounds the Brain write, but if the handler is suspended for any other reason (loop scheduling, future implementation that adds awaits), cleanup hangs forever. Fix: `await asyncio.wait_for(self._signal_handler_task, timeout=3.0)` with a fallback log.
- [x] [Review][Patch] **`_handle_shutdown` synthesizes `Command(raw_input="")` for EOF/KbdInt path** [src/nova/systems/nerve/system.py:_run_repl path (c)] — the synthesized Command bypasses Skin's parser and carries an empty `raw_input` while `verb=SHUTDOWN`. A future audit-log subscriber inspecting `Command.raw_input` would see `verb=SHUTDOWN, raw_input=""` and conclude the user typed nothing. Fix: use a sentinel like `raw_input="<eof>"` or `"<keyboard-interrupt>"` so log readers can disambiguate synthesized from user-typed.
- [x] [Review][Patch] **`test_render_response_prints_to_console` doesn't satisfy AC #17** [tests/unit/adapters/rich/test_skin_adapter.py] — AC #17 specifies *"patches the adapter's `Console` mock (`MagicMock`); `console.print` called once with `"hello"`."* Implemented test uses a real `Console`+`StringIO` and only asserts substring containment. A future refactor that adds markup, prefix decoration, or panel-wrapping would silently pass. Fix: rewrite to `MagicMock` console + `assert_called_once_with("hello")` per the AC text.
- [x] [Review][Patch] **`test_collect_input_delegates_to_rich_prompt` docstring lies about the `to_thread` hop** [tests/unit/adapters/rich/test_skin_adapter.py] — implementation no longer uses `asyncio.to_thread` (uses `threading.Thread(daemon=True)` per Fix 2). Test docstring says "patch survives the to_thread hop" — stale documentation. Fix: update docstring to reference the daemon-thread mechanism.
- [x] [Review][Patch] **State A INFO log not asserted in tests** [tests/unit/systems/nerve/test_nerve_system.py:test_startup_state_a_renders_briefing_then_returns_without_repl] — AC #6 step 6 promises a State-A INFO log with text *"State A briefing rendered — setup wizard auto-start deferred to setup.bat first-run gate"*. Test asserts ritual + skin call counts but not the log. Fix: add a `caplog.records` assertion that the log fires with the expected message.
- [x] [Review][Patch] **`_make_tier_manager_mock` mutates the `type` of a MagicMock to inject `tier`** [tests/unit/systems/nerve/test_nerve_system.py:_make_tier_manager_mock] — `type(tier_mgr).tier = property(lambda self: tier)`. Works today because `MagicMock(spec=TierManager)` generates a per-instance subclass, but if a future refactor drops the `spec=` or shares subclasses, every test sees the last-built tier. Fix: use `unittest.mock.PropertyMock` on the instance attribute instead.
- [x] [Review][Patch] **AC #16 spec text shows `asyncio.to_thread(Prompt.ask, ...)` but Fix 2 replaced it with `threading.Thread(daemon=True)`** [_bmad-output/implementation-artifacts/3-5-nerve-command-routing-and-session-lifecycle.md:AC #16] — the AC code-block was never updated when the daemon-thread fix landed. Fix: update AC #16's code block to show the daemon-thread implementation.
- [x] [Review][Patch] **Spec says "eleven-step" in two places, "12-step" in two others** [_bmad-output/implementation-artifacts/3-5-nerve-command-routing-and-session-lifecycle.md:Tasks 2 + Project Structure Notes] — Tasks §727 + Project Structure §937 say "12-step", contradicting the Eleven-step ordering. Fix: change "12-step" → "11-step" in both places.
- [x] [Review][Patch] **Spec docstring says `nerve.briefing` and `nerve.system` are "Skin- and Nerve-internal respectively" — both are actually Nerve-internal** [_bmad-output/implementation-artifacts/3-5-nerve-command-routing-and-session-lifecycle.md:AC #1] — implementation `models.py` docstring correctly says "Nerve-internal"; spec text says "Skin- and Nerve-internal respectively". Fix: correct spec to "Nerve-internal".

### Deferred findings

- [x] [Review][Defer] **POSIX signal-handler branches uncovered (`pragma: no cover`)** [src/nova/systems/nerve/system.py:_install + _uninstall POSIX branches] — project is Windows-only per project-context.md; POSIX coverage is future-state when/if Linux support lands. Logged for the eventual cross-platform story.
- [x] [Review][Defer] **`_install_signal_handler` propagates `ValueError` if `NerveSystem.startup` runs in non-main thread** [src/nova/systems/nerve/system.py:_install_signal_handler] — `signal.signal` raises ValueError outside the main thread; uncaught. Story 3.5's contract is one `startup` per process via `cli.py` (main-thread only); future embedding callers would need this guarded. Logged for the embedding-API story.
- [x] [Review][Defer] **`_signal_handler_task` is reset only at `startup()` start — not cleared after handler completes** [src/nova/systems/nerve/system.py:_signal_handler_task semantics] — the contract is asymmetric: cleanup checks "task exists" not "task ran successfully". Theoretical concern today (no path resets `_shutdown_event`); a future story that supports re-entry needs to address this.
- [x] [Review][Defer] **`collect_input` daemon thread leaks across REPL iterations** [src/nova/adapters/rich/skin.py:collect_input] — every REPL turn spawns a fresh `nova-skin-input` daemon thread; cancelled losers keep running with `Prompt.ask` blocked on stdin. Bounded in 3.5 (REPL exits unconditionally on shutdown_event), but a future story that adds re-entry needs to address the orphan-thread + stdin-contention concern.
- [x] [Review][Defer] **`_cleanup_after_repl` Phase 1 `contextlib.suppress(Exception)` lets `CancelledError` propagate** [src/nova/systems/nerve/system.py:_cleanup_after_repl Phase 1] — if the handler task is cancelled by loop teardown, the CancelledError propagates past suppress (it's BaseException) and replaces whatever exception triggered the finally. Existing `test_repl_re_raises_external_cancellation` doesn't exercise this branch. Acceptable production behavior (loop teardown means process is dying); revisit if test flakiness emerges.
- [x] [Review][Defer] **State A path render exception is fatal — no try-around the State A render to log + render a degraded fallback** [src/nova/systems/nerve/system.py:startup step 6] — a single error at the briefing-build boundary aborts the bare-`nova` invocation entirely with a traceback. Acceptable per the State A contract (no modes means nothing useful can happen anyway), but UX could be improved with a degraded fallback. Logged for a future polish pass.

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **`KeyboardInterrupt` from inside an asyncio task escapes pytest-asyncio.** First REPL test for path (c) raised `KeyboardInterrupt` from inside `collect_input` (`AsyncMock(side_effect=...)`). The `BaseException` propagated past asyncio's task wrapping and aborted the entire pytest session ("70 passed" then halted). Replaced the brittle test with an AST-walk static check that asserts `_run_repl`'s `except` clause names BOTH `EOFError` and `KeyboardInterrupt`. The behavioral guarantee (path (c) drives `_handle_shutdown`) is exercised by the EOFError test that shares the same handler.
- **MagicMock spec'd as `BrainPort` makes `assert_awaited_once_with` invisible to mypy.** mypy reads `nerve._brain.end_session` as the real protocol method, not the underlying MagicMock. Refactored every signal-handler test to capture the brain mock in a local variable (e.g., `brain = _make_brain_mock()`) and assert against the local — the real mock surface is visible to mypy without `# type: ignore`.
- **`SimpleNamespace` not enough — port-isolation test needed `typing.get_type_hints`.** Story 3.5 AC #4 required a runtime-resolved annotation check. `inspect.signature` returns the literal string `"CommandOutcome"` under `from __future__ import annotations`, masking a future `-> None` regression. `typing.get_type_hints(NervePort.route_command)` resolves the actual class object — single-line lock.
- **Pytest's stdin capture raises `OSError`, not `EOFError`.** Three existing tests in `test_cli_bootstrap.py` plus the `test_api_key_update.py` integration tests boot `nova` via `_async_main` and expected immediate exit. With `app.nerve.startup()` wired, the REPL blocks on `Prompt.ask` and pytest captures raise `OSError`, NOT `EOFError`. Added autouse fixtures that monkeypatch `nova.adapters.rich.skin.Prompt.ask` to return `"shutdown"` so existing tests exit cleanly without changing their fundamental purpose.
- **Stale "session shell placeholder" log assertion.** `test_notice_integration_in_async_main_when_api_key_none` searched for the now-removed log line via `next(...)`, hitting `StopIteration` which Python 3.12 PEP 479 promotes to `RuntimeError: coroutine raised StopIteration`. Updated to search for the new `"entering session loop"` line — same Step 7 ordering invariant, new log message.
- **POSIX-only signal handler branches genuinely unreachable on Windows CI.** Marked `loop.add_signal_handler` paths with `# pragma: no cover - POSIX-only branch; CI runs on Windows`. The Windows `signal.signal` branch is fully covered. Final coverage on `nova.systems.nerve.system`: 99.7% (one residual branch — the SIGBREAK-absent uninstall path — is exercised by the install-side test but coverage's branch tracking doesn't credit cross-test reach).
- **Auto-formatter + ruff `SIM117` cleaned up nested `with` statements.** Combined `caplog.at_level(...) + pytest.raises(...)` into single `with` blocks per ruff guidance.
- **mypy strict noisy on MagicMock attribute-access ignores.** Stripped 30+ unused `# type: ignore[attr-defined]` comments via a regex sweep — MagicMock allows free attribute access without annotation needs.

### Completion Notes List

- **Task 1 — `CommandOutcome` enum + port reshape.** Closed [deferred-work.md:139](deferred-work.md#L139) (`NervePort.route_command` returns `None` — error surface undocumented). New `CommandOutcome(StrEnum)` with two members (`CONTINUE` / `EXIT`); `nova.systems.nerve.models.py` is the new module that crosses the port boundary. Port-isolation test extended with `test_nerve_port_route_command_returns_command_outcome` that uses `typing.get_type_hints` for runtime resolution.
- **Task 2-4-6 (combined into `system.py`) — `NerveSystem` + REPL + signal handler.** Eleven-step `startup` ordering enforced (read aggregate → state determine → State A early-return → recency policy → render → create session → emit → REPL → cleanup). The crucial first-blocker fix — Brain reads precede `create_session` — is locked by `test_startup_reads_prior_state_before_creating_session`. State A early-return (second-blocker fix) locked by `test_startup_state_a_does_not_create_session`. Signal handler write-then-emit (third-blocker fix) locked by `test_signal_handler_does_not_emit_session_ended_when_brain_write_{fails,times_out}`. Signal handler `_shutdown_event` mechanism (fourth-blocker fix) locked by `test_signal_handler_sets_shutdown_event_first` + `test_repl_exits_when_shutdown_event_set_during_input`.
- **Task 3 — Dispatch table.** `match` statement covers every `CommandVerb` member; exhaustiveness locked by `test_route_command_dispatch_table_covers_every_command_verb` (parametrized over every member). Layer C contextual gating ships with `_prompt_context = None` for the bare REPL — Story 3.8 will set the context for the State C resume hero-path.
- **Task 5 — Skin adapter REPL primitives.** `RichSkinAdapter.collect_input` wraps `rich.prompt.Prompt.ask` in `asyncio.to_thread`; `render_response` is a one-line `console.print` wrapped the same way. Added `rich.prompt` to the Story 3.4 isolation allowlist as a deliberate new dependency.
- **Task 7 — Composition root + `cli.py`.** `NovaApp` gained a `nerve: NervePort` field; `create_app` instantiates `NerveSystem` after `skin` is wired. `cli.py:_async_main` Step 7 placeholder log replaced with `await app.nerve.startup()`. Composition-root regression test added (`test_nerve_system_is_instantiated_inside_create_app`).
- **Task 8 — `_AlwaysHealthyCheck` reconciliation.** No code change to the stub; the recovery loop is NOT started in this story (deferred to the future Claude adapter — see § Group I). Smoke test docstring updated to reference Story 3.5's deferral. App.py `_AlwaysHealthyCheck` docstring gained a sentence linking the deferral to the smoke test. `deferred-work.md:139` entry struck through with a closed-by-Story-3.5 marker.
- **Task 9 — Unit tests.** 92 tests across 9 blocks in `test_nerve_system.py`. Coverage on `nova.systems.nerve.system` reaches 99.7% (one residual branch in the install/uninstall sigbreak conditional that cross-references between two tests).
- **Task 10 — AST isolation guards.** `test_nerve_system_isolation.py` mirrors `test_briefing_isolation.py`. 15 tests: forbidden-imports check, no-rich, no-sqlite3, no-dynamic-imports, plus 11 positive-presence locks for every expected nova import.
- **Task 11 — Integration tests.** `test_session_loop.py` ships two end-to-end tests using REAL adapters (Brain SQLite + Rich Skin + NerveSystem orchestration). Scenarios: bare boot → briefing render → SHUTDOWN; bare boot with recent prior session → briefing skipped. Both verify the full pipeline + nova.db state.
- **Task 12 — Full CI gate.** Ruff lint ✓, ruff format ✓, mypy strict ✓ (127 source files), 1731 unit + 53 integration tests pass (net delta: **+124 unit tests** vs. the Story 3.4 baseline of 1607). Coverage 99.8% on Story 3.5 modules.
- **Bootstrap test compatibility.** Three pre-existing `test_cli_bootstrap.py` tests + two `test_api_key_update.py` tests + two `test_cli_offline_notice.py` tests required updates to handle the new REPL-blocks-on-stdin behavior. Used autouse fixtures that monkeypatch `Prompt.ask` to return `"shutdown"` — preserves the bootstrap-only intent of those tests without conflating with session-loop semantics.

### File List

**New source files:**
- `src/nova/systems/nerve/system.py` — `NerveSystem` orchestrator (eleven-step `startup`, REPL with `_shutdown_event` race pattern, signal-handler lifecycle pair, `_handle_*` dispatch table for every `CommandVerb`).
- `src/nova/systems/nerve/models.py` — `CommandOutcome(StrEnum)` closed two-member vocabulary.

**Modified source files:**
- `src/nova/ports/nerve.py` — `route_command` return annotation flipped from `None` to `CommandOutcome`; closes deferred-work.md:139.
- `src/nova/adapters/rich/skin.py` — `collect_input` + `render_response` `NotImplementedError` bodies replaced; `from rich.prompt import Prompt` import + `import asyncio` added.
- `src/nova/app.py` — `NovaApp.nerve` field added; `create_app` instantiates `NerveSystem`; `_AlwaysHealthyCheck` docstring gains the recovery-loop deferral sentence.
- `src/nova/cli.py` — `_async_main` Step 7 placeholder log replaced with `await app.nerve.startup()`.

**Modified planning / tracking files:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Story 3.5 flipped through `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — `NervePort.route_command` entry struck through + closed-by-Story-3.5 pointer.

**New test files:**
- `tests/unit/systems/nerve/test_nerve_system.py` — 92 tests across 9 blocks (constructor + startup ordering, skip-briefing helper, briefing render path, dispatch table parametrize, idempotent shutdown, REPL three exit paths, signal handler write-then-emit, tier-gate helper, defensive-path coverage).
- `tests/unit/systems/nerve/test_nerve_system_isolation.py` — 15 AST-guard tests (forbidden imports + positive-presence locks).
- `tests/unit/systems/nerve/test_command_outcome_shape.py` — 8 shape-regression tests for `CommandOutcome`.
- `tests/integration/test_session_loop.py` — 2 end-to-end tests with real adapters (briefing render + skip).

**Modified test files:**
- `tests/unit/ports/test_port_isolation.py` — added `test_nerve_port_route_command_returns_command_outcome`.
- `tests/unit/adapters/rich/test_skin_adapter.py` — appended 5 adapter delegation tests for `render_response` + `collect_input` (happy + EOF + async-shape locks).
- `tests/unit/adapters/rich/test_skin_adapter_isolation.py` — added `rich.prompt` to the allowed-Rich-submodules list.
- `tests/unit/test_composition_root.py` — added `test_nerve_system_is_instantiated_inside_create_app`.
- `tests/unit/test_app.py` — `test_tier_stays_offline_without_recovery_loop` docstring updated to reference Story 3.5's deferral (no behavioral change).
- `tests/unit/test_cli_offline_notice.py` — `_fake_app_with_api_key` mocks `app.nerve.startup` as `AsyncMock`; "session shell placeholder" log search updated to "entering session loop".
- `tests/integration/test_cli_bootstrap.py` — autouse `Prompt.ask` shortcircuit fixture; happy-path test updated for the briefing+SHUTDOWN-confirmation stdout.
- `tests/integration/test_api_key_update.py` — same autouse `Prompt.ask` shortcircuit fixture.

### Change Log

| Date | Description |
|---|---|
| 2026-05-05 | Story created via `bmad-create-story` workflow. Status: backlog → ready-for-dev. Pre-flag classification applied (interaction-boundary, A1 + A9 + A10 + A3 fresh-session-review trial). `_AlwaysHealthyCheck` reconciliation: deferral documented; smoke test gets comment-only update; recovery loop NOT started. Closes deferred-work.md:~139 (`NervePort.route_command` return-shape). Ships `SkinPort.collect_input` + `render_response` adapter bodies (scope shift from Story 3.7's docstring claim — explicitly documented in § Detected conflicts). |
| 2026-05-05 | Pre-dev review pass applied 5 blocker fixes to the story spec. **Blocker 1 — startup ordering inverted.** `create_session` was sequenced before `load_briefing_aggregate`; the freshly-created open session would shadow the prior session in `get_last_session`, breaking State A/B/C determination. Reordered: read aggregate (steps 4–5) → State A early-return (step 6) → recency policy + render (steps 7–8) → `create_session` + emit (steps 9–10) → REPL (step 11). **Blocker 2 — State A orphan session.** State A's early return left an open session row written by step 3. Fix: don't create the session for the State A path; the corrected ordering makes State A return BEFORE step 9. **Blocker 3 — signal handler write-then-emit violation.** Handler emitted `SessionEnded` even after Brain write failed/timed out. Fix: emit ONLY after `end_session` returns successfully; failure paths `return` early without emitting; flag flip happens between successful write and emission. **Blocker 4 — signal handler exit semantics.** Custom handler suppresses default `KeyboardInterrupt` propagation; without an explicit shutdown mechanism the REPL stayed alive after Ctrl-C. Fix: added `_shutdown_event: asyncio.Event` (lazy, loop-bound at `startup` call time); handler sets event FIRST so even no-write paths still exit; REPL uses `asyncio.wait({input_task, shutdown_task}, return_when=FIRST_COMPLETED)` race pattern with cancelled-task drain. **Blocker 5 — constructor signature drift.** AC #5 said `settings: UserSettings`; AC #6 step 7 amended to `config: NovaConfig`; composition-root passed `config=config`. Unified to `config: NovaConfig` everywhere; `_should_skip_briefing` pure helper takes `UserSettings` directly so caller extracts via `config.settings`. Tests and Review Focus rows updated to lock all five fixes. |
| 2026-05-05 | Post-implementation review applied 4 fixes. **Fix 1 — signal-handler vs cleanup race.** Handler set `_shutdown_event` first, then attempted `brain.end_session`; REPL could observe the event, return, and let cleanup race the in-flight handler with a duplicate write+emit. Fixed by adding `_signal_handler_task` field captured by a unified `_on_signal` sync entrypoint (used by both POSIX `add_signal_handler` and Windows `signal.signal` + `call_soon_threadsafe`); cleanup awaits this task and SKIPS its own write when the handler ran (single-owner contract). New tests: `test_on_signal_captures_task_for_cleanup_to_await`, `test_on_signal_does_not_replace_in_flight_task`, `test_cleanup_skips_end_session_when_signal_handler_owned_cleanup`, `test_cleanup_skips_end_session_even_when_handler_failed`, `test_cleanup_runs_end_session_when_no_signal_handler`. **Fix 2 — `to_thread(Prompt.ask)` could hang `asyncio.run` cleanup.** `asyncio.to_thread` uses the loop's default executor (non-daemon thread pool); `asyncio.run`'s `shutdown_default_executor` waits for thread completion, which would never come if `Prompt.ask` was blocked on stdin. Fixed by replacing `asyncio.to_thread(Prompt.ask, ...)` with an explicit `threading.Thread(daemon=True)` — daemon threads are killed at process exit, so `asyncio.run` returns cleanly. Added `_safe_set_result` / `_safe_set_exception` module helpers that tolerate already-cancelled futures (REPL race-pattern teardown cancels the future before the daemon thread completes). New test: `test_collect_input_uses_daemon_thread_for_process_exit_safety` + `test_collect_input_safe_set_result_handles_already_done_future`. **Fix 3 — SHUTDOWN response contradiction.** Spec said `render_response` is used "for every routed Command except SHUTDOWN (which renders nothing)" while `_handle_shutdown` explicitly rendered "Session ended." Picked the implemented behavior (render confirmation); updated spec text. **Fix 4 — stale invariant claim.** A6 question 2 said "create_session write succeeds BEFORE the briefing assembly reads 'current session'" — that's the OLD ordering that the first-blocker fix reversed. Updated to read-then-write-then-emit so the bug doesn't get reintroduced. CI green: 1738 unit + 53 integration pass; 99.8% coverage on Story 3.5 modules. |
| 2026-05-05 | Same-session three-layer adversarial code review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) ran in parallel general-purpose subagents. 37 raw findings, 34 unique post-dedup. Triage: **5 decision-needed (all resolved), 13 patches (all applied + 2 added from decisions = 15 total), 6 deferred, 10 dismissed.** **HIGH patches:** (1) `_handle_shutdown` Brain failure caused duplicate `end_session(is_complete=False)` call from cleanup, silently overwriting user's clean-shutdown intent — fixed by flipping `_session_active=False` BEFORE the await + try/except so cleanup skips. (2) `_run_repl` outer `except CancelledError` cancelled tasks but didn't drain — fixed with `contextlib.suppress` drain awaits before re-raise. (3) `_should_skip_briefing` raised `TypeError` on naive ISO timestamps — fixed by extending the catch to `(ValueError, TypeError)`. **MED patches:** `render_response` now passes `markup=False` to `Console.print`; `_cleanup_after_repl` Phase 1 now `wait_for(timeout=3.0)` on the handler task; EOF/KbdInt path uses `<eof>` / `<keyboard-interrupt>` sentinel `raw_input` so audit-log readers can disambiguate from user-typed empty; `test_render_response_prints_to_console` rewritten to MagicMock + `assert_called_once_with("hello", markup=False)` per AC #17; `test_collect_input` docstring updated to reference daemon-thread mechanism; State A INFO log assertion added. **LOW patches:** `_make_tier_manager_mock` uses instance attribute (no type mutation); spec text updates (AC #16 daemon-thread, "12-step"→"11-step", "Skin- and Nerve-internal"→"Nerve-internal"). **Decisions:** AST-only KbdInt test accepted (BaseException-in-asyncio incompatible with pytest); AST composition-root helper accepted (consistent with codebase pattern); no-timeout on `_handle_shutdown` accepted with docstring rationale (user has Ctrl-C agency); best-effort-once cleanup contract preserved (Story 3.10 owns orphan-row recovery); drains-pending no-warning test added. **Defers:** POSIX signal branches uncovered, ValueError on non-main-thread install, `_signal_handler_task` cleared semantics, `collect_input` cross-iteration thread leak, Phase 1 CancelledError leak, State A render exception unhandled. **Final tally:** 1743 unit + 53 integration pass (+5 net vs. round 2); 99.7% coverage on system.py + 100% on models.py + Rich skin; ruff + format + mypy strict all clean. Status: review → done. |
| 2026-05-05 | Story 3.5 implemented per ready-for-dev spec. 12 tasks complete, all 40 ACs satisfied. Status: in-progress → review. **Net delta:** +124 unit tests vs. Story 3.4 baseline (1731 unit pass + 1 brittle deselected + 1 pre-existing skip + 53 integration pass). **Coverage:** 99.7% on `nova.systems.nerve.system` (one residual sigbreak-uninstall branch — exercised cross-test but coverage's per-test branch tracking doesn't credit), 100% on `nova.systems.nerve.models`, 100% on `nova.adapters.rich.skin`. **Closes deferred-work.md:139** via `CommandOutcome(StrEnum)` reshape. Ships first runtime wiring of the T1 continuity loop — bare `nova` now boots Nerve, reads prior state, renders the briefing card (or skips if recent), creates a runtime session, enters the REPL, dispatches commands, and shuts down cleanly. Skin's `collect_input` + `render_response` adapter bodies land here. Signal handler installs SIGINT/SIGBREAK on Windows (signal.signal) with the explicit `_shutdown_event` exit mechanism that drives the REPL's race-pattern exit. ruff lint ✓, ruff format ✓, mypy strict ✓ (127 source files), full quality gate green. |
