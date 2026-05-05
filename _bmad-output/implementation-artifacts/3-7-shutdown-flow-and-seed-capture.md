# Story 3.7: Shutdown Flow & Seed Capture

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)

**Depends on:**
- Story 1.2 — [src/nova/core/types.py](../../src/nova/core/types.py) (`ActionType.SEED_CAPTURE`, `MemoryCategory.SEED`, `SnapshotType.SHUTDOWN`)
- Story 1.3 — [src/nova/core/events.py:201-227](../../src/nova/core/events.py#L201-L227) (`SessionEnded`, `SeedSaved`)
- Story 1.4 — [src/nova/core/storage/engine.py:247-248](../../src/nova/core/storage/engine.py#L247-L248) (`SqliteStorageEngine.transaction()` `@asynccontextmanager` — wraps the new atomic `commit_shutdown` three-write)
- Story 1.6 — [src/nova/core/config.py](../../src/nova/core/config.py) (`NovaConfig.modes` for resolving the active mode's display name)
- Story 1.8 — [src/nova/core/audit.py:144-150](../../src/nova/core/audit.py#L144-L150) (`AuditLogger.log_action`, `RESULT_SUCCESS`, `RESULT_FAILED`, `RESULT_SKIPPED`)
- Story 1.9 — [src/nova/ports/skin.py:42-55](../../src/nova/ports/skin.py#L42-L55) (`SkinPort.render_shutdown_card` Protocol stub) and [src/nova/ports/ritual.py:29-39](../../src/nova/ports/ritual.py#L29-L39) (`RitualPort.begin_shutdown` Protocol stub)
- Story 3.1 — [src/nova/adapters/sqlite/brain.py:252-302](../../src/nova/adapters/sqlite/brain.py#L252) (`SqliteBrainAdapter.end_session` — gains idempotency guard in this story; closes [deferred-work.md:231](deferred-work.md#L231)) and [src/nova/adapters/sqlite/brain.py:332-353](../../src/nova/adapters/sqlite/brain.py#L332) (`store_snapshot` — reused as-is)
- Story 3.3 — [src/nova/systems/ritual/system.py:413-414](../../src/nova/systems/ritual/system.py#L413-L414) (`RitualSystem.begin_shutdown` body to be replaced; `_escape_label_value` helper reused) and [src/nova/adapters/rich/skin.py:196-197](../../src/nova/adapters/rich/skin.py#L196-L197) (`RichSkinAdapter.render_shutdown_card` `NotImplementedError` body to be replaced)
- Story 3.5 — [src/nova/systems/nerve/system.py:949-1021](../../src/nova/systems/nerve/system.py#L949-L1021) (`NerveSystem._handle_shutdown` placeholder body to be replaced) and [src/nova/systems/nerve/system.py:336-341](../../src/nova/systems/nerve/system.py#L336-L341) (`startup()` step 9 — `_session_started_at` stamping reshape)
- Story 3.6 — [src/nova/systems/nerve/system.py:838-896](../../src/nova/systems/nerve/system.py#L838-L896) (`_handle_mode_switch` — extends to also track `_active_mode_apps_launched`) and [src/nova/systems/nerve/system.py:249](../../src/nova/systems/nerve/system.py#L249) (`_active_mode_name` field — read at shutdown for the summary)

**Downstream consumers:**
- Story 3.8 (Warm Resume) — consumes the `sessions.seed_text` row + `memory_items` row written here to render Briefing Card State C ("Last seed: …" hero line); the `WARM_RESUME` state machine activates only when both writes succeed
- Story 3.10 (Crash recovery) — uses the `is_complete=1` marker written here to distinguish clean shutdown from interrupted-session rows; the signal-handler path (Story 3.10) must NOT re-run this flow's seed capture
- Story 4.5 (Memory accumulation & enriched briefings) — `memory_items.category=seed` rows written here are the first seed entries the memory-query path returns
- Story 5.1 (Transparency display) — counts the `memory_items` and `sessions.summary` rows written here in the transparency totals
- Story 5.2 (Selective forget) — must include `memory_items.category=seed` rows in the deletion sweep when forgetting a project that was the subject of a seed
- Story 7.1 (Voice & personality) — `RitualSystem.begin_shutdown`'s `prompt_text` and the post-shutdown `"Planted for tomorrow."` confirmation are the first two operational lines Voice will dress with personality (deferred — T1 ships locked copy, Voice replaces verbatim)

## Story

As a user ending my session,
I want to type `shutdown` (or `quit` / `exit`) and capture a one-sentence "tomorrow seed" in under 30 seconds,
So that my next session opens with continuity context — the seed becomes Day 2's hero line — instead of a blank slate.

## Story-type classification

**Interaction-boundary story.** This is the first end-of-session ceremony with multi-system orchestration (Nerve → Ritual data-shape, Nerve → Skin render + input, Nerve → Brain three-write persistence, Nerve → AuditLogger seed-capture row, Nerve → EventBus `SeedSaved`/`SessionEnded`), the first introduction of a `BrainPort` write path for memory items, and the first place `_active_mode_name` (Story 3.6) gets read by a non-mode-switch consumer. The three A6 questions:

1. **New contract between existing pieces?** YES — five new contracts.
   - **`NerveSystem._handle_shutdown` → `RitualPort.begin_shutdown`** delegation (replaces the Story 3.5 direct-Brain placeholder body). Reshape: `begin_shutdown(state: ShutdownState) -> ShutdownViewModel` (was `begin_shutdown() -> ShutdownData`). Mirrors the briefing pattern: Nerve gathers state, Ritual returns a render-ready view model, Nerve drives the Skin render + input collection. **Ritual does NOT import Skin or Brain** — same separation-of-concerns established by Story 3.3. The epic AC's "Ritual delegates to Brain to persist" is shorthand for "the shutdown ceremony Ritual orchestrates triggers Brain writes" — Nerve is the executor, consistent with the briefing flow.
   - **`SkinPort.render_shutdown_card`** signature reshape from `(summary: SessionSummary) -> None` to `(view_model: ShutdownViewModel) -> None`. The `SessionSummary` typing (Story 1.9 stub) was speculative — `SessionSummary` is the Brain-projection shape (carries `summary: str | None` etc.) and lacks the apps-used + active-mode-display-name + duration-formatted fields the shutdown panel needs. The pre-rendered-labels pattern from Story 3.3 (BriefingViewModel) extends to shutdown: every visible string originates in Ritual, Skin only chooses the Rich style.
   - **`BrainPort.commit_shutdown`** — NEW method. **Atomic** three-write transactional commit (sessions UPDATE → memory_items INSERT when seed entered → workspace_snapshots INSERT). Signature: `async def commit_shutdown(self, session_id: int, commit: ShutdownCommit) -> str` — returns the stamped `ended_at` ISO string. New typed DTO `ShutdownCommit` lives in [src/nova/systems/brain/models.py](../../src/nova/systems/brain/models.py) carrying `seed_text`, `summary`, `snapshot_apps`, `snapshot_focused_app`, `snapshot_mode_name` — explicitly NO timestamp field; the adapter is the single source of truth for `ended_at` / `created_at` / `captured_at` cross-row consistency. Implementation wraps all three writes in `engine.transaction()` so either all succeed or all roll back. **This single port method replaces the alternative three-separate-write orchestration** (`end_session` + `add_memory_item` + `store_snapshot`) — three separate writes would create a partial-state collision with the `end_session` idempotency guard: if `end_session(is_complete=True)` succeeded but `add_memory_item` failed, the `is_complete=1` row would block any subsequent `_cleanup_after_repl` `end_session(is_complete=False)` write (its WHERE filter rejects already-complete rows), leaving an inconsistent durable state with no recovery path. The atomic transaction makes that scenario unreachable. **Memory-item writes are NOT exposed as a standalone `add_memory_item` port method in T1** — Story 3.1's adapter docstring forward-pointed to "Story 3.7 (shutdown seed capture) and Epic 4 / 5 own those extensions"; Story 3.7 owns the seed-write path inside the transactional `commit_shutdown` boundary, and Epic 4/5 will introduce a standalone write surface when their use cases land. Update [src/nova/adapters/sqlite/brain.py:61-63](../../src/nova/adapters/sqlite/brain.py#L61-L63) accordingly.
   - **`SqliteBrainAdapter.end_session` idempotency guard** — closes [deferred-work.md:231](deferred-work.md#L231). The current UPDATE has no `WHERE` filter on `is_complete`, so a second `end_session` call silently overwrites seed/summary/ended_at. Even though Story 3.7's user-typed shutdown now goes through `commit_shutdown` (NOT `end_session`), the idempotency guard remains worthwhile defensive hardening: `_cleanup_after_repl` and the Story 3.5 signal handler both call `end_session(is_complete=False)` for the interrupted-marker path, and the single-owner contract is enforced at the Nerve layer — a future regression that fires both could silently double-write. The fix: `UPDATE … WHERE id = ? AND is_complete = 0` so a re-call on an already-completed session is a clean no-op (zero rows affected). **Timestamp ownership: the adapter is the sole owner of `ended_at`.** Callers do NOT pass timestamps; `end_session(...)` has no `ended_at` parameter. On the normal (incomplete-row) branch the adapter stamps via `events._utc_now_iso()` AFTER the pre-UPDATE SELECT confirms the row is still incomplete, and returns that newly-stamped value. On the no-op (already-complete-row) branch a pre-UPDATE SELECT fetches the EXISTING `sessions.ended_at` and the method returns that string unchanged. Both branches return a stable ISO-8601 string the caller can use as a cross-row timestamp; neither branch lets a caller-supplied timestamp drift the column value. Locked by tests that call `end_session` twice on the same session_id and assert the second call is a no-op (no row change, originally-stamped `ended_at`/`seed_text` survive, returned timestamp identical across calls).
   - **`NerveSystem` → `AuditLogger`** for the `SEED_CAPTURE` audit row. Per project-context.md:86 audit is observational — Hands established the pattern in Story 3.6, Nerve adopts the SAME pattern here: a single unwrapped `await self._audit.log_action(...)` call site; AuditLogger's internal `StorageError` swallow handles the failure mode; no try/except wrapping that would mask programmer errors.

2. **New invariants in degraded / partial-failure paths?** YES — six distinct invariants.
   - **Atomic three-write commit via `BrainPort.commit_shutdown`.** All three Brain writes (sessions UPDATE + memory_items INSERT when seed entered + workspace_snapshots INSERT) execute inside a single `engine.transaction()` block — either all rows land or none do. Per project-context.md:78 (durable-fact events emitted only after Brain confirms), Nerve emits `SeedSaved` (when seed entered) and `SessionEnded` only after `commit_shutdown` returns. The atomicity is what unlocks the "_session_active flips after writes succeed" idempotency posture (next bullet) — without it, a partial commit would leave the user in an unrecoverable hybrid state.
   - **Persistence-before-confirmation.** Per project-context.md:201 (operational success messages must reflect actual completion state), the `"Planted for tomorrow."` final-line render fires AFTER `commit_shutdown` returns successfully AND `audit.log_action(...)` has been awaited AND event emission has been ATTEMPTED — never before. The `audit.log_action` call is unwrapped (single call site) but its underlying `audit_log` row may not actually exist: per Story 1.8, `AuditLogger` swallows `StorageError` internally, so a returned `None` does NOT prove durability of the audit row. Audit is observational; the contract is "audit was attempted" not "audit row landed." Event emission is wrapped per-emit; emission failures are observability-only and do NOT block the confirmation render (the durable-fact contract is satisfied by the transactional `commit_shutdown`; events are runtime fan-out). On `commit_shutdown` failure (programmer-error missing row, or any of the three writes raised inside the transaction → rollback → exception propagates), render an honest error line (`"Shutdown failed: state may be inconsistent. Check logs."`) instead of the canonical "Planted" message.
   - **Empty-input reprompt then cancel.** Per the epic AC: empty seed input → reprompt once with `"Please confirm or cancel."`; if still empty on the second attempt, render `"Cancelled."` and proceed with `seed_text=None` (still calls `commit_shutdown` to write the snapshot + finalize the session, BUT no memory_items row, no `SeedSaved` emission). The reprompt count is bounded — TWO total attempts. This is the only loop in the shutdown flow; an unbounded reprompt would defeat the NFR4 30-second budget AND violate project-context.md:200 (no silent retry loops).
   - **Skip / cancel terminator vocabulary.** During the seed prompt, the user may type `skip`, `cancel`, or send EOF (Ctrl-D) / Ctrl-C → exits cleanly with `seed_text=None`. The terminators are case-insensitive, exact-match (NOT substring — typing `cancel my plan` is treated as the seed text, not a cancel). Empty input is its own bucket (reprompt once); the cancel terminators short-circuit immediately on the first attempt. EOF / KeyboardInterrupt during the prompt is treated like cancel — no exception propagates out of `_handle_shutdown`.
   - **Audit-failure isolation reuses the Story 3.6 contract.** The single `await self._audit.log_action(action_type=SEED_CAPTURE, ...)` call site is unwrapped; `AuditLogger`'s internal `StorageError` swallow is the boundary; Nerve does NOT wrap in try/except (would mask programmer errors). Locked by AST guard `test_handle_shutdown_does_not_wrap_audit_log_action_in_try_except` (mirrors the Story 3.6 walker that inspects body / orelse / finalbody / handler bodies).
   - **Idempotency on second-shutdown.** The Story 3.5 placeholder already guarantees `_session_active=False` short-circuits a second `_handle_shutdown` call (returns `EXIT` cleanly). Story 3.7 preserves this: after `commit_shutdown` returns successfully, `_session_active` flips to `False` — a subsequent SHUTDOWN command is a no-op clean exit. Critically, the flip happens AFTER `commit_shutdown` succeeds, NOT before (vs. Story 3.5's placeholder which flips before). Rationale: if the transaction fails, leaving `_session_active=True` lets `_cleanup_after_repl` write the `is_complete=False` interrupted marker as the durable record. The transaction's all-or-nothing semantics make this safe — the cleanup-fallback row is consistent because none of the three rows landed during the failed commit.

3. **Depends on prior-story state?** YES — four areas.
   - **Story 3.5's `_session_id` / `_session_active` lifecycle + `_handle_shutdown` placeholder.** Story 3.7 replaces the placeholder body wholesale. The existing test that locks the placeholder (`test_handle_shutdown_calls_brain_end_session_when_session_active` and the parametrized `test_route_command_dispatches_layer_b_routable` SHUTDOWN row in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py)) MUST be UPDATED — not deleted — to assert the new delegation behavior (`ritual.begin_shutdown` called once, `skin.render_shutdown_card` called once, three-write persistence sequence, etc.).
   - **Story 3.5's `startup()` step 9.** Story 3.7 needs `_session_started_at` available at shutdown time to compute session duration. The reshape: stamp `started_at = events._utc_now_iso()` BEFORE the `create_session` call (today the `started_at=None` path lets Brain stamp it, but Nerve never sees the stamped value). New field `self._session_started_at: str | None = None` set after the create_session returns. Reset to `None` in `_cleanup_after_repl` via the same path that resets `_session_active`. The Brain-side `started_at` parameter already accepts a caller-stamped ISO string ([src/nova/adapters/sqlite/brain.py:217-223](../../src/nova/adapters/sqlite/brain.py#L217)) — no Brain reshape needed.
   - **Story 3.6's `_active_mode_name` + new `_active_mode_apps_launched` extension.** Story 3.6 ships `_active_mode_name: str | None` set on successful restore. Story 3.7 needs the apps that were launched too — `_active_mode_apps_launched: tuple[str, ...]` — for the shutdown card's "Apps used" line AND for the workspace-snapshot's `apps` field. Story 3.6's `_handle_mode_switch` extends to set BOTH fields atomically (the `if any(r.success for r in results)` branch sets both; the `else` branch clears both to None / `()`). The semantic: "apps used" = the apps successfully launched by the LAST successful mode switch in this session. Multi-mode sessions show the active mode's apps, not a cumulative union (cumulative is more complex — needs deduplication across mode switches and surfaces stale apps from a switched-away-from mode; T1 stays simple).
   - **Story 1.8's `AuditLogger`.** Same observational contract as Story 3.6. Audit failure does NOT block the shutdown completion; the unwrapped-audit pattern is reused.

**Classification result:** ✅ **Interaction-boundary story.** Apply A1 invariant sweep (persistence-before-emit, idempotency, empty-input reprompt, audit isolation). Apply A9 degraded-path proof (happy: full seed entered; degraded: empty-then-empty → cancel; degraded: skip/cancel terminator; degraded: Brain write fails). Apply A10 prior-state reconciliation per the per-story table below.

## Depends on prior-story state (A10)

### Story 1.2 — `ActionType` / `MemoryCategory` / `SnapshotType`

| Surface | Story 3.7 reliance |
|---|---|
| [`ActionType.SEED_CAPTURE`](../../src/nova/core/types.py#L96) | Written to `audit_log.action_type` for the seed-capture audit row. Fires once per shutdown attempt — `result=RESULT_SUCCESS` when seed entered + persisted, `result=RESULT_SKIPPED` when user cancelled / empty-then-empty, `result=RESULT_FAILED` when Brain write raised (the ONLY failed-bucket path; the audit row records the attempt regardless). |
| [`MemoryCategory.SEED`](../../src/nova/core/types.py#L109) | Written to `memory_items.category` when a seed is captured. Voice/Ritual will read these via `BrainPort.query_memory` in Epic 4/5 — Story 3.7 just lays down the canonical category for seed entries. |
| [`SnapshotType.SHUTDOWN`](../../src/nova/core/types.py#L67) | Written to `workspace_snapshots.snapshot_type` for the final shutdown snapshot. Distinguishes from `STARTUP` / `MODE_SWITCH` / `PERIODIC` — Story 3.8's State C briefing reads only the most recent snapshot regardless of type, but Story 4.3's snapshot pruning uses the type to keep one shutdown snapshot per session permanently. |

### Story 1.3 — `SessionEnded` / `SeedSaved`

| Surface | Story 3.7 reliance |
|---|---|
| [`SessionEnded(session_id, seed_text, is_complete)`](../../src/nova/core/events.py#L201-L214) | Emitted ONCE at the end of the shutdown flow, AFTER `brain.commit_shutdown` returns successfully. `seed_text` is `None` when the user cancelled / empty-then-empty (matches the `sessions.seed_text` column value). `is_complete=True` always — Story 3.10 owns the `is_complete=False` paths. Per architecture.md:1037 (write-then-emit), this fires AFTER the transactional commit confirms; emission failures are observability-only and do NOT block the confirmation render. |
| [`SeedSaved(session_id, seed_text)`](../../src/nova/core/events.py#L217-L227) | Emitted ONLY when a seed was captured (skipped on cancel / empty-then-empty paths). Fires AFTER `brain.commit_shutdown` returns successfully — the seed's `memory_items` row landed inside the same transaction as the session UPDATE, so the durable-fact contract is satisfied at the single commit point. Subscribers in T1 are zero; Voice/Epic 7 may subscribe to start prose-generation pre-warming for the next session's State C briefing. |

### Story 1.6 — `NovaConfig.modes`

| Surface | Story 3.7 reliance |
|---|---|
| [`NovaConfig.modes: dict[str, ModeConfig]`](../../src/nova/core/config.py#L206) | Read by `NerveSystem._handle_shutdown` to resolve the active mode's display label. `mode_config = self._config.modes.get(self._active_mode_name)` → `mode_config.name` is the user-facing string. If the active mode is `None` (no mode restored this session) OR the mode was deleted between switch and shutdown (defensive — should not happen in a single session, but config reload paths might enable it), the shutdown card omits the "Mode:" line entirely (progressive omission, mirrors Story 3.3). |

### Story 1.8 — `AuditLogger`

| Surface | Story 3.7 reliance |
|---|---|
| [`AuditLogger.log_action`](../../src/nova/core/audit.py#L144-L150) | Called ONCE in the shutdown flow with `action_type=ActionType.SEED_CAPTURE`, `target=str(session_id)` (opaque session-id reference per project-context.md sensitive-content rule — NEVER the seed text or any portion of it), `result=RESULT_SUCCESS` / `RESULT_SKIPPED` / `RESULT_FAILED`, `details={"has_seed": bool, "outcome": "saved" / "cancelled" / "empty_twice" / "persistence_failed"}`. The `details` dict is the failure-mode diagnostic surface; `has_seed` lets the transparency display (Story 5.1) show "N seed-capture attempts, M actually saved" without scanning the events. |
| Audit-write failure swallow | Story 1.8 already catches `StorageError` internally and logs at WARNING — Nerve relies on this. The single `await self._audit.log_action(...)` is unwrapped; mirrors Story 3.6's contract per project-context.md:86. |

### Story 1.9 — Port stubs reshaping

| Surface | Story 3.7 reliance |
|---|---|
| [`SkinPort.render_shutdown_card(summary: SessionSummary)`](../../src/nova/ports/skin.py#L49) | **Reshape:** signature changes to `render_shutdown_card(view_model: ShutdownViewModel) -> None`. The `SessionSummary` typing was a Story 1.9 stub assumption; `SessionSummary` is the Brain-projection (carries `is_complete`, raw `duration_seconds`, etc.) and lacks the active-mode display label, the apps-used tuple, the formatted duration string, and the prompt text. The pre-rendered-labels pattern from Story 3.3 is the right shape — `ShutdownViewModel` has every visible string already formatted. Update the port-file docstring's "Story 3.7 ships render_shutdown_card" line to spell out the reshape. |
| [`RitualPort.begin_shutdown() -> ShutdownData`](../../src/nova/ports/ritual.py#L39) | **Reshape:** signature changes to `begin_shutdown(state: ShutdownState) -> ShutdownViewModel`. `ShutdownData` was a Story 1.9 stub shape (`session_id, prompt_text, last_context`) that doesn't carry the rendered-label fields the shutdown card needs. Story 3.7 retires `ShutdownData` and introduces `ShutdownState` (input DTO) + `ShutdownViewModel` (output DTO). Rename rather than retain — `ShutdownData` had zero callers (the impl raised `NotImplementedError`). The retire-not-rename decision keeps the model file from accumulating dead types. |
| [`MemoryItem` (read-side projection)](../../src/nova/systems/brain/models.py#L102-L116) | Already shipped as a Story 1.9 read-side dataclass; unchanged in Story 3.7. The seed-write path is encapsulated inside `commit_shutdown`'s transaction (see Group C AC #9–11) — no `MemoryItemInput` DTO is added in this story. Epic 4/5 will introduce a standalone memory-item write surface when their use cases (session notes, context summaries, pattern memory) need it. |

### Story 3.1 — `BrainPort.end_session` + `store_snapshot`

| Surface | Story 3.7 reliance |
|---|---|
| [`BrainPort.end_session(session_id, *, seed_text, summary, is_complete)`](../../src/nova/ports/brain.py#L55-L62) | **NOT called from Story 3.7's user-typed shutdown path.** Story 3.7 uses the new atomic `commit_shutdown` instead. `end_session` retains its existing role for the `_cleanup_after_repl` interrupted-marker write (`is_complete=False`) and the Story 3.5 signal handler. The Story 2.4 setup-completion path also still calls `end_session(is_complete=True, ...)` for the setup session — Story 3.7 does NOT migrate that call site to `commit_shutdown` (setup writes only the session row, no memory_item, no snapshot). |
| [`SqliteBrainAdapter.end_session` UPDATE SQL](../../src/nova/adapters/sqlite/brain.py#L95-L99) | **Reshape:** add `AND is_complete = 0` to the WHERE clause as defensive hardening. Closes [deferred-work.md:231](deferred-work.md#L231). The reshape preserves the return contract: when zero rows match (already-completed session), the method does an explicit `SELECT ended_at FROM sessions WHERE id=?` to fetch the existing `ended_at` and returns it. The current `events._utc_now_iso()` stamping moves AFTER the SELECT-existing-row branch so re-calls return a stable timestamp. WARNING log fires on the no-op branch (`"end_session re-called on already-completed session; UPDATE was a no-op"`). The Story 2.4 setup path is unaffected — first-time call on a fresh `is_complete=0` row proceeds normally. The `_cleanup_after_repl` and signal-handler paths write `is_complete=False`; the WHERE filter still allows their UPDATEs (current value is 0, target is 0 — UPDATE fires). |
| [`BrainPort.store_snapshot(session_id, snapshot)`](../../src/nova/ports/brain.py#L68) | **NOT called from Story 3.7's user-typed shutdown path.** Story 3.7's snapshot row lands inside `commit_shutdown`'s atomic transaction (constructed internally from `ShutdownCommit.snapshot_apps` / `snapshot_focused_app` / `snapshot_mode_name`). `store_snapshot` retains its existing surface for Story 4.1's Eyes-driven capture path (periodic / mode-switch / startup snapshots) — non-shutdown snapshot types continue to use the standalone write surface. |

### Story 3.3 — RitualSystem helpers + RichSkinAdapter pattern

| Surface | Story 3.7 reliance |
|---|---|
| [`_escape_label_value(value: str)`](../../src/nova/systems/ritual/system.py#L65-L78) | Reused by `RitualSystem.begin_shutdown` for the apps-used label (a comma-separated list — same disambiguation need as the briefing's `available_modes_label`). Move the helper from module-private (`_escape_label_value`) to module-private but reused — no API change, just a second caller. |
| [`format_duration_seconds`](../../src/nova/core/formatting.py) | Reused for the "Duration: 1h 23m" line. Same call shape as Story 3.3's `_build_last_session_label` — wrap in `max(0, duration)` defensively since clock skew could produce negatives. |
| [BriefingViewModel pre-rendered-labels precedent](../../src/nova/systems/ritual/models.py#L70-L120) | The shape `ShutdownViewModel` mirrors: pre-rendered string fields, omission via `None`, no formatting in Skin. Skin maps each field to a fixed Rich style and omits when None. |
| [RichSkinAdapter Panel / Text pattern](../../src/nova/adapters/rich/skin.py#L108-L167) | The `render_briefing_card` body is the structural template for `render_shutdown_card` — `body: Text`, `_emit(text, style, block)` helper with block-transition spacing, markup-safe `Text.append` (NOT raw markup-string concatenation), Panel with a fixed border style. **Use a different border color (`yellow` or `blue`) for the shutdown panel** so it's visually distinct from the cyan briefing panel — see Group D AC #11 for the exact color choice rationale. |

### Story 3.5 — `NerveSystem` lifecycle + `_handle_shutdown` placeholder

| Surface | Story 3.7 reliance |
|---|---|
| [`NerveSystem._handle_shutdown` body](../../src/nova/systems/nerve/system.py#L949-L1021) | **Replace wholesale.** The Story 3.5 placeholder calls `brain.end_session(seed_text=None, summary=None, is_complete=True)` and emits `SessionEnded`. Story 3.7's body delegates to `ritual.begin_shutdown` for the view model, drives the seed prompt + reprompt loop, calls the new transactional `brain.commit_shutdown(session_id, ShutdownCommit)`, audits, emits `SeedSaved` (when seed) + `SessionEnded`, and renders the `"Planted for tomorrow."` confirmation. See Group A AC #1 for the full body. |
| [`startup()` step 9 session creation](../../src/nova/systems/nerve/system.py#L336-L341) | **Reshape:** stamp `started_at` BEFORE the create_session call so Nerve has the value at shutdown time. New field `_session_started_at: str | None = None`. `_cleanup_after_repl` resets it alongside `_session_active` in a `finally` block (so the reset happens even if the cleanup-path Brain write raises). |
| [`_session_active` shutdown-flip ordering](../../src/nova/systems/nerve/system.py#L992-L994) | **Reshape:** in Story 3.5's placeholder the flip happens BEFORE the Brain await (one-attempt posture). In Story 3.7 the flip happens AFTER `brain.commit_shutdown` returns successfully — see Dev Notes "Why `_session_active` flips after `commit_shutdown` succeeds (not before)". On commit failure the transaction's all-or-nothing semantics guarantee no partial state; `_cleanup_after_repl`'s `is_complete=False` write becomes the durable record. |
| Existing tests in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py) | Update — do not delete — the parametrized `test_route_command_dispatches_layer_b_routable` SHUTDOWN row, the `test_handle_shutdown_*` placeholder tests, AND any `test_startup_*` test that asserts on the `started_at` argument shape (now Nerve-stamped). The test fixture pattern (`_build_nerve_system` helper) gains one keyword-only parameter (`audit: AuditLogger`); composition root passes the existing audit instance. |

### Story 3.6 — `_active_mode_name` + `_handle_mode_switch`

| Surface | Story 3.7 reliance |
|---|---|
| [`_active_mode_name: str | None`](../../src/nova/systems/nerve/system.py#L249) | Read at shutdown to populate the ShutdownState's `active_mode_stem`. Cleared back to `None` in `_cleanup_after_repl` (mirrors `_session_active` reset — same lifecycle). |
| [`_handle_mode_switch` body](../../src/nova/systems/nerve/system.py#L838-L896) | **Extend (additive):** alongside setting `_active_mode_name = mode_stem` on success, also set `_active_mode_apps_launched: tuple[str, ...] = tuple(app.name for app, r in zip(mode_config.apps, results) if r.success)`. On total failure (`else` branch) clear both fields. Story 3.6's existing tests for the success / total-failure branches gain assertions on the new field. |
| Story 3.6 cross-mock ordering pattern | Reused for Story 3.7's mock-ordering tests — `parent.attach_mock` to lock cross-mock chronological order (`ritual.begin_shutdown` → `skin.render_shutdown_card` → `skin.collect_input` → `brain.commit_shutdown` → `audit.log_action` → `event_bus.emit(SeedSaved)` → `event_bus.emit(SessionEnded)` → `skin.render_response("Planted for tomorrow.")`). The user-typed shutdown path goes through the single transactional `commit_shutdown` call — there is NO `end_session` / `add_memory_item` / `store_snapshot` triplet to assert ordering across. |

## Acceptance Criteria

### Group A: `NerveSystem._handle_shutdown` body replacement

1. **Replace `_handle_shutdown` body** at [src/nova/systems/nerve/system.py:949-1021](../../src/nova/systems/nerve/system.py#L949-L1021). The new body in this exact ordering — every step is a separate `await` so cancellation lands at a clean boundary:

   ```python
   async def _handle_shutdown(self, command: Command) -> CommandOutcome:
       """Delegate the shutdown ceremony to Ritual; orchestrate Skin + Brain.

       Idempotent: a second SHUTDOWN call after the transactional commit
       succeeds short-circuits on ``_session_active=False`` (returns
       EXIT cleanly). The flip to False happens AFTER ``commit_shutdown``
       returns — if the transaction fails, ``_session_active`` stays True
       and ``_cleanup_after_repl`` writes the ``is_complete=False``
       interrupted-session marker as the durable record (Story 3.10
       detects it on next startup). The atomic transaction guarantees
       no partial state — either all three rows landed or none did.

       The seed-prompt loop is bounded: empty input reprompts ONCE, then
       cancels. ``skip`` / ``cancel`` (case-insensitive) short-circuit
       immediately. EOFError / KeyboardInterrupt during the prompt are
       treated like cancel — no exception propagates out of this method.
       """
       del command
       if not self._session_active:
           return CommandOutcome.EXIT
       assert self._session_id is not None
       assert self._session_started_at is not None  # set in startup() step 9
       # Step 1 — sample clock once for state assembly. Used only for the
       # duration display (the durable ended_at/created_at/captured_at all
       # come from the adapter inside commit_shutdown — single source of
       # truth for cross-row timestamp consistency).
       state_ended_at = events._utc_now_iso()
       active_mode_display_name = self._resolve_active_mode_display_name()  # see AC #3
       state = ShutdownState(
           session_id=self._session_id,
           started_at=self._session_started_at,
           ended_at=state_ended_at,
           active_mode_stem=self._active_mode_name,
           active_mode_display_name=active_mode_display_name,
           apps_used=self._active_mode_apps_launched,
       )
       # Step 2 — Ritual produces the render-ready view model.
       view_model = await self._ritual.begin_shutdown(state)
       # Step 3 — render the shutdown card BEFORE the seed prompt.
       await self._skin.render_shutdown_card(view_model)
       # Step 4 — drive the seed prompt with bounded reprompt.
       seed_text, seed_outcome = await self._collect_seed_with_reprompt(view_model.prompt_text)
       # Step 5 — assemble the rendered summary string for sessions.summary.
       summary_text = _build_session_summary_text(state)  # may be None
       # Step 6 — atomic Brain commit. Three writes (sessions UPDATE +
       # memory_items INSERT when seed_text not None + workspace_snapshots
       # INSERT) inside a single engine.transaction() — all land or none
       # do. Adapter stamps ended_at/created_at/captured_at once internally
       # and uses the SAME value for every row (cross-row consistency).
       # Returns the stamped ended_at; Nerve does not need it for events
       # but tests assert it matches the persisted columns.
       commit = ShutdownCommit(
           seed_text=seed_text,
           summary=summary_text,
           snapshot_apps=self._active_mode_apps_launched,
           snapshot_focused_app=None,
           snapshot_mode_name=self._active_mode_name,
       )
       try:
           await self._brain.commit_shutdown(self._session_id, commit)
       except Exception:
           # Transaction rolled back — no partial state. Audit the
           # failure, render an honest error, leave _session_active=True
           # so _cleanup_after_repl writes the is_complete=False marker.
           logger.exception("shutdown: commit_shutdown failed; session will be marked interrupted")
           await self._audit.log_action(
               action_type=ActionType.SEED_CAPTURE,
               target=str(self._session_id),
               result=RESULT_FAILED,
               details={"has_seed": seed_text is not None, "outcome": "persistence_failed"},
           )
           await self._skin.render_response(
               "Shutdown failed: state may be inconsistent. Check logs."
           )
           return CommandOutcome.EXIT
       # Step 7 — only NOW flip _session_active: transaction confirmed.
       self._session_active = False
       # Step 8 — audit row (after writes, before emission, mirrors
       # Story 3.6's audit→render→emit pattern. Single unwrapped call.)
       await self._audit.log_action(
           action_type=ActionType.SEED_CAPTURE,
           target=str(self._session_id),
           result=_classify_audit_outcome(seed_outcome),
           details={"has_seed": seed_text is not None, "outcome": seed_outcome},
       )
       # Step 9 — events. SeedSaved only when seed entered. Each emission
       # is wrapped — emission failures are observability-only; the
       # commit already confirmed durability. The confirmation in step 10
       # fires regardless of emission outcome.
       if seed_text is not None:
           try:
               await self._event_bus.emit(
                   SeedSaved(session_id=self._session_id, seed_text=seed_text)
               )
           except Exception:
               logger.exception("shutdown: SeedSaved emission failed (commit already confirmed)")
       try:
           await self._event_bus.emit(
               SessionEnded(
                   session_id=self._session_id,
                   seed_text=seed_text,
                   is_complete=True,
               )
           )
       except Exception:
           logger.exception("shutdown: SessionEnded emission failed (commit already confirmed)")
       # Step 10 — final-line confirmation. Voice (Epic 7) will dress.
       confirmation = "Planted for tomorrow." if seed_text is not None else "Cancelled."
       await self._skin.render_response(confirmation)
       return CommandOutcome.EXIT
   ```

   The body is ~75 lines including comments — comparable to `_handle_mode_switch` (Story 3.6) at 60. The helper extractions (`_resolve_active_mode_display_name`, `_collect_seed_with_reprompt`, `_build_session_summary_text`, `_classify_audit_outcome`) keep `_handle_shutdown` itself scannable; each helper has a single, testable purpose.

2. **`_collect_seed_with_reprompt(prompt_text: str) -> tuple[str | None, _SeedOutcome]`** is a private method on NerveSystem (NOT a free function — it consumes `self._skin`). Returns `(seed_text, outcome)`:
   - `seed_text` is the stripped seed string OR `None` for cancel / skip / empty-twice / EOF / KeyboardInterrupt.
   - `outcome` is a closed-set string literal: `"saved"` (seed entered on attempt 0 OR attempt 1), `"cancelled"` (terminator typed at any attempt, or EOF / KbdInt), `"empty_twice"` (both attempts returned empty/whitespace, no terminator). The three outcomes are mutually exclusive.

   The boolean `was_reprompted` is NOT sufficient — it cannot distinguish "empty then skip" (cancelled, was_reprompted=True) from "empty then empty" (empty_twice, was_reprompted=True). The explicit outcome string makes the audit classifier trivial and the contract unambiguous.

   Type alias declared at module top:

   ```python
   from typing import Literal

   _SeedOutcome = Literal["saved", "cancelled", "empty_twice"]
   ```

   Body in this exact shape:

   ```python
   async def _collect_seed_with_reprompt(
       self, prompt_text: str
   ) -> tuple[str | None, _SeedOutcome]:
       """Bounded seed-input loop: 2 attempts maximum.

       Returns ``(seed_text, outcome)``:

       * ``seed_text`` — the entered text stripped of leading/trailing
         whitespace, OR ``None`` for any non-saved outcome.
       * ``outcome`` — closed set: ``"saved"`` (seed_text non-None),
         ``"cancelled"`` (terminator typed OR EOF/KbdInt at ANY attempt),
         ``"empty_twice"`` (both attempts returned empty, no terminator).

       Cancel terminators are exact-match case-insensitive: ``"skip"``,
       ``"cancel"`` — NOT substring matching. ``"cancel my plan"`` is
       seed text, not a cancel.

       Empty input on the first attempt reprompts once with
       ``"Please confirm or cancel."``. Empty input on the second
       attempt → return ``(None, "empty_twice")``. A terminator on
       the second attempt → ``(None, "cancelled")`` — the user
       explicitly chose to cancel after the reprompt, distinct from
       the silent-empty path.
       """
       _CANCEL_TERMINATORS: frozenset[str] = frozenset({"skip", "cancel"})
       attempt_prompt = prompt_text
       for attempt in range(2):
           try:
               raw = await self._skin.collect_input(prompt=attempt_prompt)
           except (EOFError, KeyboardInterrupt):
               logger.info("shutdown: seed prompt interrupted; treating as cancel")
               return (None, "cancelled")
           stripped = raw.strip()
           if stripped.lower() in _CANCEL_TERMINATORS:
               return (None, "cancelled")
           if stripped:  # non-empty
               return (stripped, "saved")
           # Empty — reprompt once on attempt 0; fall through on attempt 1.
           if attempt == 0:
               attempt_prompt = "Please confirm or cancel."
       # Fell through both attempts — empty twice.
       return (None, "empty_twice")
   ```

   Tests at AC #21 Block III lock all three outcomes across the full input matrix — first-attempt seed/skip/cancel/EOF, second-attempt seed/skip/cancel/empty.

3. **Helper `_resolve_active_mode_display_name(self) -> str | None`** — looks up `self._active_mode_name` in `self._config.modes`; returns the display name (`mode_config.name`) or `None` if the active mode is None / the mode was deleted between switch and shutdown. Defensive against the hypothetical-future config-reload path; today's single-session config is immutable so the lookup always succeeds when `_active_mode_name is not None`.

4. **Helper `_build_session_summary_text(state: ShutdownState) -> str | None`** is a module-level private function in [src/nova/systems/nerve/system.py](../../src/nova/systems/nerve/system.py) (NOT a method — pure transformation):

   ```python
   from nova.core.formatting import diff_iso_seconds, format_duration_seconds


   def _build_session_summary_text(state: ShutdownState) -> str | None:
       """Compose the sessions.summary column value from runtime state.

       Returns ``None`` when no mode was active — the row's summary
       column stays NULL in that case (forward-compatible with Voice
       generating a richer summary in Epic 7).

       Format: "{display_name} mode, {duration}" (mirrors the briefing's
       last_session_label shape — Story 3.3's
       _build_last_session_label).
       """
       if state.active_mode_display_name is None:
           return None
       duration_seconds = diff_iso_seconds(state.started_at, state.ended_at)
       duration_display = format_duration_seconds(duration_seconds)
       return f"{state.active_mode_display_name} mode, {duration_display}"
   ```

   `diff_iso_seconds` is the public ISO-duration helper added to [src/nova/core/formatting.py](../../src/nova/core/formatting.py) by this story (see Group A AC #4b below). It already clamps negative results to 0 and tolerates the trailing-Z form, so the Nerve helper does NOT re-wrap the call in `max(0, ...)`.

4b. **New ISO-parsing helpers in `nova.core.formatting`** — both Nerve (`_build_session_summary_text` above) and Ritual (`begin_shutdown` at AC #8) need to compute `(end - start).seconds` from two ISO-8601 strings. Per project-context.md "no magic literals for cross-cutting rules," the parsing logic lives in ONE place. Adding to `nova.core.formatting` (alongside the existing `format_duration_seconds`) keeps the helper in a neutral core module — no cross-system import needed.

   Add to [src/nova/core/formatting.py](../../src/nova/core/formatting.py):

   ```python
   def diff_iso_seconds(start_iso: str, end_iso: str) -> int:
       """Return ``(end - start)`` in integer seconds, clamped to ``>= 0``.

       Tolerates the trailing-``Z`` form (``"2026-05-05T14:23:01Z"``) by
       routing through a normalization step before
       :meth:`datetime.fromisoformat`. Negative results clamp to ``0``
       (clock skew / NTP slew / corrupt persisted timestamp defense).

       Public surface — Nerve's session-summary builder and Ritual's
       shutdown view-model assembly both call this. Locking the
       parsing rule in one place per project-context.md "no magic
       literals for cross-cutting rules."
       """
       start_dt = _parse_iso(start_iso)
       end_dt = _parse_iso(end_iso)
       return max(0, int((end_dt - start_dt).total_seconds()))


   def _parse_iso(iso: str) -> datetime:
       """``datetime.fromisoformat`` with trailing-``Z`` normalization.

       Python 3.11+'s ``fromisoformat`` parses ``"...Z"`` natively;
       earlier versions require a swap to ``"...+00:00"``. Project
       targets 3.11+ but the swap costs nothing and locks the parser
       against future input drift.
       """
       if iso.endswith("Z"):
           iso = iso[:-1] + "+00:00"
       return datetime.fromisoformat(iso)
   ```

   `__all__` updates: add `"diff_iso_seconds"`. `_parse_iso` stays module-private (single-underscore prefix) — only `diff_iso_seconds` is the documented cross-module surface.

   Imports added: `from datetime import datetime`. If `formatting.py` already imports `datetime`, no change.

5. **Helper `_classify_audit_outcome(outcome: _SeedOutcome) -> str`** — pure function returning the audit `result` value. Trivial mapping because the seed-helper already produced the canonical outcome string:

   ```python
   def _classify_audit_outcome(outcome: _SeedOutcome) -> str:
       """Map a seed-collection outcome to the audit ``result`` value.

       * ``"saved"`` → ``RESULT_SUCCESS``
       * ``"cancelled"`` / ``"empty_twice"`` → ``RESULT_SKIPPED``

       The ``outcome`` string is itself written to ``details["outcome"]``
       at the call site — no second mapping needed.
       """
       if outcome == "saved":
           return RESULT_SUCCESS
       return RESULT_SKIPPED
   ```

   The persistence-failed path uses `RESULT_FAILED` with `details["outcome"] = "persistence_failed"` — handled inline in the except branch, doesn't go through this classifier.

   The main flow destructure (Step 4 of `_handle_shutdown` body):

   ```python
   seed_text, seed_outcome = await self._collect_seed_with_reprompt(view_model.prompt_text)
   ```

   The audit step (Step 8) becomes:

   ```python
   await self._audit.log_action(
       action_type=ActionType.SEED_CAPTURE,
       target=str(self._session_id),
       result=_classify_audit_outcome(seed_outcome),
       details={"has_seed": seed_text is not None, "outcome": seed_outcome},
   )
   ```

### Group B: `RitualPort.begin_shutdown` reshape + `RitualSystem.begin_shutdown` impl

6. **Reshape** [`src/nova/ports/ritual.py`](../../src/nova/ports/ritual.py) — `RitualPort.begin_shutdown` signature changes from `() -> ShutdownData` to `(state: ShutdownState) -> ShutdownViewModel`:

   ```python
   async def begin_shutdown(self, state: ShutdownState) -> ShutdownViewModel: ...
   ```

   Update the module docstring's "Story 1.9 (AC #4)" line: `:meth:`RitualPort.begin_shutdown` (open the seed-capture flow)` becomes `(produce a render-ready ShutdownViewModel from the runtime ShutdownState)`. Drop the `ShutdownData` import in favor of `ShutdownState` + `ShutdownViewModel`.

7. **New input/output dataclasses** in [`src/nova/systems/ritual/models.py`](../../src/nova/systems/ritual/models.py) — REPLACE `ShutdownData` (zero callers, was a Story 1.9 stub):

   ```python
   @dataclass(frozen=True)
   class ShutdownState:
       """Runtime input to RitualPort.begin_shutdown.

       Carries the Nerve-side runtime fields Ritual needs to render the
       shutdown card. ``apps_used`` is the ``_active_mode_apps_launched``
       tuple — display names of apps successfully launched by the LAST
       successful mode-restore (NOT cumulative across mode switches; T1
       simplicity).

       ``active_mode_display_name`` is resolved by Nerve via
       ``config.modes[active_mode_stem].name`` so Ritual stays
       config-blind (mirrors the briefing pattern where Nerve does the
       config lookup). When ``active_mode_stem`` is None the display
       name is also None — the shutdown card omits the Mode line.
       """

       session_id: int
       started_at: str  # ISO-8601 UTC
       ended_at: str  # ISO-8601 UTC
       active_mode_stem: str | None
       active_mode_display_name: str | None
       apps_used: tuple[str, ...]


   @dataclass(frozen=True)
   class ShutdownViewModel:
       """Render-ready shutdown card output consumed by SkinPort.render_shutdown_card.

       Pre-rendered labels per the Story 3.3 briefing precedent — every
       visible string originates here, Skin only chooses the Rich style.
       Progressive omission: ``None`` fields are omitted by Skin (no
       empty placeholders, no "N/A").

       ``prompt_text`` is the seed-capture question. T1 ships locked
       copy ("What should you pick up tomorrow?"); Voice (Epic 7) will
       replace with personality-bearing text — the field shape is
       forward-compatible.
       """

       session_id: int
       title: str  # locked copy "Session ending"
       mode_label: str | None  # "Mode: Coding" or None
       duration_label: str  # "Duration: 1h 23m" — always present (>= 0s)
       apps_label: str | None  # "Apps: VS Code, Postman" or None when no apps
       prompt_text: str  # locked T1 copy: "What should you pick up tomorrow?"
   ```

   `__all__` updates: drop `"ShutdownData"`; add `"ShutdownState"`, `"ShutdownViewModel"`.

8. **`RitualSystem.begin_shutdown` body** at [src/nova/systems/ritual/system.py:413-414](../../src/nova/systems/ritual/system.py#L413) replaces the `NotImplementedError`:

   ```python
   from nova.core.formatting import diff_iso_seconds, format_duration_seconds


   async def begin_shutdown(self, state: ShutdownState) -> ShutdownViewModel:
       """Assemble a render-ready ShutdownViewModel from the runtime state.

       Pure transformation — no I/O, no clock reads (Nerve owns clock
       sampling and passes ``ended_at`` in the state). Mirrors
       ``build_briefing``'s stateless contract.

       Locked-copy fields:
       * ``title`` = "Session ending"
       * ``prompt_text`` = "What should you pick up tomorrow?"

       Pre-rendered labels:
       * ``mode_label`` = ``f"Mode: {display_name}"`` or None when no
         active mode. ``_escape_label_value`` not needed here — single
         field, no comma-separated join.
       * ``duration_label`` = ``f"Duration: {format_duration_seconds(...)}"``
         — always present. ``diff_iso_seconds`` already clamps
         negative durations to 0.
       * ``apps_label`` = ``f"Apps: {', '.join(escaped names)}"`` or
         None when ``apps_used`` is empty. Each name passes through
         ``_escape_label_value`` for comma-disambiguation (Story 3.3
         precedent).
       """
       duration_seconds = diff_iso_seconds(state.started_at, state.ended_at)
       duration_display = format_duration_seconds(duration_seconds)
       mode_label = (
           f"Mode: {state.active_mode_display_name}"
           if state.active_mode_display_name is not None
           else None
       )
       apps_label: str | None
       if state.apps_used:
           escaped = ", ".join(_escape_label_value(name) for name in state.apps_used)
           apps_label = f"Apps: {escaped}"
       else:
           apps_label = None
       return ShutdownViewModel(
           session_id=state.session_id,
           title=_SHUTDOWN_TITLE,
           mode_label=mode_label,
           duration_label=f"Duration: {duration_display}",
           apps_label=apps_label,
           prompt_text=_SEED_PROMPT_TEXT,
       )
   ```

   Locked-copy module constants in [src/nova/systems/ritual/system.py](../../src/nova/systems/ritual/system.py):

   ```python
   _SHUTDOWN_TITLE: str = "Session ending"
   _SEED_PROMPT_TEXT: str = "What should you pick up tomorrow?"
   ```

   `diff_iso_seconds` is imported from `nova.core.formatting` (see AC #4b) — the canonical ISO-duration parser. Both Ritual and Nerve consume it from `core`; no inter-system helper imports.

### Group C: `BrainPort.commit_shutdown` + `ShutdownCommit` DTO (atomic three-write)

9. **New input DTO** `ShutdownCommit` in [src/nova/systems/brain/models.py](../../src/nova/systems/brain/models.py):

   ```python
   @dataclass(frozen=True)
   class ShutdownCommit:
       """Atomic input to ``BrainPort.commit_shutdown`` (Story 3.7).

       Carries everything needed to finalize a session in one
       transactional write:

       * ``seed_text`` / ``summary`` — written to the ``sessions`` row
         columns of the same name. ``seed_text`` controls whether the
         memory_items INSERT happens (``None`` → skipped).
       * ``snapshot_apps`` / ``snapshot_focused_app`` /
         ``snapshot_mode_name`` — written to the ``workspace_snapshots``
         row's ``workspace_data`` JSON. ``snapshot_type`` is implied
         (always ``SnapshotType.SHUTDOWN`` — this DTO is shutdown-
         specific).

       **No timestamp fields.** The adapter stamps ``ended_at`` once
       inside the transaction and uses the SAME value for the
       ``sessions.ended_at`` column, the ``memory_items.created_at``
       column (when seed entered), and the ``workspace_snapshots.captured_at``
       column. Caller-supplied timestamps would create ownership drift
       (per blocker analysis); the adapter is the single source of
       truth.

       Mirrors ``WorkspaceSnapshotInput``'s typed-input pattern — no
       raw ``dict`` crosses the port boundary (Story 1.9 AC #5). All
       sequence fields are ``tuple[str, ...]`` for genuine immutability
       under ``frozen=True``.

       ``snapshot_apps`` is the active mode's launched-apps tuple
       (display names) — same source as the shutdown card's apps
       label, so the durable snapshot row matches what the user
       saw on screen.
       """

       seed_text: str | None
       summary: str | None
       snapshot_apps: tuple[str, ...]
       snapshot_focused_app: str | None
       snapshot_mode_name: str | None
   ```

   `__all__` updates: add `"ShutdownCommit"`.

   **`MemoryItemInput` is NOT introduced in this story.** Story 3.1's adapter docstring forward-pointed to "Story 3.7 (shutdown seed capture) and Epic 4 / 5 own those extensions" for memory-item writes — Story 3.7 owns the seed-write path inside the transactional `commit_shutdown` boundary; the adapter constructs the row's column values directly from `ShutdownCommit.seed_text` + the stamped `ended_at`. Epic 4/5 will introduce a standalone `MemoryItemInput` + `add_memory_item` port surface when their use cases (session notes, context summaries, pattern memory) need a non-transactional write path. **Update [src/nova/adapters/sqlite/brain.py:61-63](../../src/nova/adapters/sqlite/brain.py#L61-L63)** module docstring's scope-fence: "Memory-item writes (`create_memory_item` etc.) are NOT on the port yet — Story 3.7 (shutdown seed capture) lands the seed-write path INSIDE the transactional `commit_shutdown` method; standalone memory-item write surface is Epic 4 / 5 scope."

10. **`BrainPort.commit_shutdown` Protocol method** added to [src/nova/ports/brain.py](../../src/nova/ports/brain.py) BETWEEN `end_session` and `get_last_session` (groups the lifecycle write methods together):

    ```python
    async def commit_shutdown(self, session_id: int, commit: ShutdownCommit) -> str: ...
    ```

    Returns the stamped `ended_at` ISO-8601 string. The caller (Nerve's `_handle_shutdown`) does not directly use the return value for the happy path — events are populated from `seed_text` and `session_id` alone — but tests assert the returned timestamp matches the persisted column values across all three rows (see AC #21 Block I). Update the module docstring to spell out:
    > Story 3.7 introduces ``commit_shutdown`` as the atomic three-write
    > shutdown-finalization surface. The adapter wraps the
    > ``sessions`` UPDATE + ``memory_items`` INSERT (when seed entered)
    > + ``workspace_snapshots`` INSERT in a single
    > ``engine.transaction()`` so partial-state failure is impossible.

11. **`SqliteBrainAdapter.commit_shutdown` impl** added to [src/nova/adapters/sqlite/brain.py](../../src/nova/adapters/sqlite/brain.py). Three new SQL constants near the top of the file (alongside the existing `_INSERT_SESSION_SQL` and `_UPDATE_SESSION_END_SQL`):

    ```python
    _UPDATE_SESSION_COMMIT_SHUTDOWN_SQL = """
    UPDATE sessions
       SET ended_at = ?, seed_text = ?, summary = ?, is_complete = 1
     WHERE id = ? AND is_complete = 0
    """

    _INSERT_MEMORY_ITEM_SHUTDOWN_SQL = """
    INSERT INTO memory_items (session_id, category, content, created_at)
    VALUES (?, ?, ?, ?)
    """

    _INSERT_SHUTDOWN_SNAPSHOT_SQL = """
    INSERT INTO workspace_snapshots (session_id, captured_at, snapshot_type, workspace_data)
    VALUES (?, ?, ?, ?)
    """
    ```

    The session UPDATE filter `WHERE id = ? AND is_complete = 0` is defense-in-depth: the explicit pre-write SELECT branch (below) already short-circuits when `is_complete = 1`, so the filter only fires under a hypothetical race that the transaction's lock prevents in practice. Keep it for layering.

    **Full idempotency contract — pre-write SELECT branches all three writes.** The two INSERTs do NOT have their own idempotency filters; instead, the entire write block is gated on the row's current state. A re-call on an already-finalized session must NOT create duplicate seed memory rows or duplicate shutdown snapshots — that would silently corrupt the durable state at the BrainPort boundary.

    Method body:

    ```python
    async def commit_shutdown(self, session_id: int, commit: ShutdownCommit) -> str:
        """Atomically finalize a session: sessions UPDATE + memory_items
        INSERT (when seed entered) + workspace_snapshots INSERT.

        All three writes execute inside ``engine.transaction()`` — either
        all rows land or none do. The single ``ended_at`` stamp is
        sampled ONCE inside the transaction and used for every row's
        timestamp column.

        Full idempotency (pre-write SELECT inside the transaction):

        * Row missing → :class:`StorageError`. Programmer error;
          surface loudly rather than silently inserting orphan
          memory_items / workspace_snapshots rows whose foreign
          keys would dangle.
        * Row already complete (``is_complete = 1``) → return the
          EXISTING ``ended_at`` and SKIP all three writes. WARNING
          log fires.
        * Row incomplete (``is_complete = 0``) → normal three-write
          path.
        """
        logger.debug("brain.commit_shutdown start", extra={"session_id_opaque": "<int>"})
        async with self._storage.transaction():
            # Pre-write inspection — branch on the row's current state.
            # SELECT and writes share the transaction's snapshot.
            existing = await self._storage.fetchone(
                "SELECT ended_at, is_complete FROM sessions WHERE id = ?",
                (session_id,),
            )
            if existing is None:
                raise StorageError(
                    f"brain.commit_shutdown: session_id={session_id} does not exist"
                )
            if existing["is_complete"]:
                logger.warning(
                    "brain.commit_shutdown re-called on already-completed session; "
                    "all three writes skipped",
                    extra={"session_id": session_id},
                )
                return str(existing["ended_at"])
            # Row exists AND is_complete = 0 — proceed with the three-write commit.
            ended_at = events._utc_now_iso()
            # Phase 1 — finalize the session row.
            await self._storage.execute(
                _UPDATE_SESSION_COMMIT_SHUTDOWN_SQL,
                (ended_at, commit.seed_text, commit.summary, session_id),
            )
            # Phase 2 — INSERT memory_items only when a seed was captured.
            if commit.seed_text is not None:
                await self._storage.execute(
                    _INSERT_MEMORY_ITEM_SHUTDOWN_SQL,
                    (
                        session_id,
                        str(MemoryCategory.SEED),
                        commit.seed_text,
                        ended_at,
                    ),
                )
            # Phase 3 — INSERT workspace_snapshots row.
            workspace_data = _serialize_workspace_data(
                apps=commit.snapshot_apps,
                focused_app=commit.snapshot_focused_app,
                mode_name=commit.snapshot_mode_name,
            )
            await self._storage.execute(
                _INSERT_SHUTDOWN_SNAPSHOT_SQL,
                (session_id, ended_at, str(SnapshotType.SHUTDOWN), workspace_data),
            )
        return ended_at
    ```

    The DEBUG log NEVER includes seed content or app names (sensitive — could leak personal context); only `session_id_opaque` placeholder. Mirrors Story 3.1's logging discipline.

    `_serialize_shutdown_snapshot` is a new module-private helper that produces the same locked JSON shape as Story 2.4 / Story 3.1's snapshot serializer. To avoid duplication, refactor the existing `_serialize_snapshot(snapshot: WorkspaceSnapshotInput)` (Story 3.1) into a thin wrapper around the new lower-level `_serialize_workspace_data(apps, focused_app, mode_name) -> str` — both `commit_shutdown` and the existing `store_snapshot` call the lower-level form. Tests at AC #25 lock byte-exact JSON parity with the existing Story 3.1 round-trip tests (the JSON shape contract is identical).

    Imports added at the top of `brain.py`: `from nova.systems.brain.models import ShutdownCommit`. Drop unused imports if any after the reshape (verify `MemoryItemInput` is NOT in the existing imports — Story 3.7 does not introduce it).

    **Transaction failure semantics:** any exception inside the `async with self._storage.transaction()` block triggers a SQLite `ROLLBACK` (per Story 1.4's engine contract). The storage engine's translation layer maps `sqlite3.Error` / `sqlite3.Warning` / `OSError` to `StorageError` at the engine boundary; the adapter does NOT add a wrapping try/except (would break the engine's translation contract — same pattern as Story 3.1). Tests at AC #25 cover the rollback path (failing INSERT 2 → assert sessions row's `is_complete` stays at 0 + zero memory_items rows + zero workspace_snapshots rows).

### Group D: `SqliteBrainAdapter.end_session` idempotency + `RichSkinAdapter.render_shutdown_card`

12. **`SqliteBrainAdapter.end_session` idempotency reshape** at [src/nova/adapters/sqlite/brain.py:252-302](../../src/nova/adapters/sqlite/brain.py#L252-L302). Closes [deferred-work.md:231](deferred-work.md#L231).

    Current SQL constant `_UPDATE_SESSION_END_SQL` reshape — add the `is_complete = 0` filter to WHERE:

    ```python
    _UPDATE_SESSION_END_SQL = """
    UPDATE sessions
       SET ended_at = ?, seed_text = ?, summary = ?, is_complete = ?
     WHERE id = ? AND is_complete = 0
    """
    ```

    Method body reshape — handle the no-op branch:

    ```python
    async def end_session(
        self,
        session_id: int,
        *,
        seed_text: str | None,
        summary: str | None,
        is_complete: bool,
    ) -> str:
        """Stamp ``ended_at`` and finalize the session row. Returns the stamped ISO string.

        Idempotent: a re-call on an already-completed session
        (``is_complete = 1``) is a no-op (zero rows affected by the
        UPDATE); the method SELECTs and returns the existing
        ``ended_at`` so the caller still gets a stable timestamp.
        Closes [deferred-work.md:231].
        """
        logger.debug("brain.end_session start", extra={"session_id_opaque": "<int>"})
        existing = await self._storage.fetchone(
            "SELECT ended_at, is_complete FROM sessions WHERE id = ?", (session_id,)
        )
        if existing is None:
            logger.warning(
                "brain.end_session: zero rows match; UPDATE will be a no-op",
                extra={"session_id": session_id},
            )
            # Stamp now and return — UPDATE will affect zero rows but
            # the caller still gets a sane ISO string for cross-row
            # timestamp consistency. The (no-op + non-existent row) case
            # is already a programmer error; the warning surfaces it.
            ended_at = events._utc_now_iso()
        elif existing["is_complete"]:
            # Already-completed: return the EXISTING ended_at; UPDATE
            # filter blocks the write. Zero rows affected.
            logger.warning(
                "brain.end_session re-called on already-completed session; UPDATE was a no-op",
                extra={"session_id": session_id},
            )
            return existing["ended_at"]  # type: ignore[no-any-return]
        else:
            ended_at = events._utc_now_iso()
        is_complete_int = 1 if is_complete else 0
        await self._storage.execute(
            _UPDATE_SESSION_END_SQL,
            (ended_at, seed_text, summary, is_complete_int, session_id),
        )
        return ended_at
    ```

    Three new branches:
    1. Row missing → WARNING, stamp `ended_at` defensively, UPDATE is no-op (covered by existing `_session_id is not None` guard at the call site, but defense-in-depth).
    2. Row exists AND `is_complete = 1` → WARNING, return EXISTING `ended_at`, UPDATE is no-op.
    3. Row exists AND `is_complete = 0` → normal path; stamp + UPDATE proceeds.

    Tests at AC #20 lock all three branches.

13. **Replace `RichSkinAdapter.render_shutdown_card` body** at [src/nova/adapters/rich/skin.py:196-197](../../src/nova/adapters/rich/skin.py#L196-L197). Signature becomes `(view_model: ShutdownViewModel) -> None` (was `(summary: SessionSummary) -> None`). New body mirrors `render_briefing_card` structure:

    ```python
    async def render_shutdown_card(self, view_model: ShutdownViewModel) -> None:
        """Render the Shutdown Card as a yellow Rich Panel.

        Yellow border distinguishes shutdown from briefing (cyan) at a
        glance — same panel structure, different chrome. Layout:

        ::

            ┌─ Session ending ──────────────────────┐
            │                                       │
            │   Mode: Coding                        │
            │   Duration: 1h 23m                    │
            │   Apps: VS Code, Postman              │
            │                                       │
            │   What should you pick up tomorrow?   │
            │                                       │
            └───────────────────────────────────────┘

        Block layout — meta-block (Mode/Duration/Apps tight grouped) +
        prompt-block (separated by blank line). Progressive omission
        per the briefing pattern. Markup-safe ``Text.append`` (NOT raw
        markup-string concatenation) so user-controlled mode names
        cannot inject Rich markup.
        """
        body = Text()
        previous_block: str | None = None

        def _emit(text: str, style: str, block: str) -> None:
            nonlocal previous_block
            if previous_block is not None:
                body.append("\n\n" if previous_block != block else "\n")
            body.append(text, style=style)
            previous_block = block

        if view_model.mode_label is not None:
            _emit(view_model.mode_label, "dim", "meta")
        _emit(view_model.duration_label, "dim", "meta")  # always present
        if view_model.apps_label is not None:
            _emit(view_model.apps_label, "dim", "meta")

        _emit(view_model.prompt_text, "bold bright_white", "prompt")

        title = Text(view_model.title, style="bold yellow")
        panel = Panel(body, title=title, border_style="yellow", padding=(1, 2))
        await asyncio.to_thread(self._console.print, panel)
    ```

    Imports added at the top of `skin.py`: `from nova.systems.ritual.models import ShutdownViewModel`. Drop the `from nova.systems.brain.models import SessionSummary` import IFF SessionSummary is unused after the reshape (verify `from nova.systems.brain.models import` ends up empty — if so, drop the whole line).

    The `await asyncio.to_thread(self._console.print, panel)` wrap mirrors `render_briefing_card` — Rich's blocking I/O. Note: `render_briefing_card` today calls `self._console.print(panel)` directly without `to_thread`; check the actual code path. If briefing is direct-call, shutdown also direct-calls for consistency. (Verify which; pin the chosen approach.)

14. **Update `RichSkinAdapter` module docstring** to spell out `render_shutdown_card`'s yellow-panel chrome and the SHUTDOWN view model contract (replacing the current `Tree (transparency, Epic 5) and the Shutdown Card (Story 3.7) land in their respective stories.` line).

### Group E: `NerveSystem` constructor reshape + field additions + `_handle_mode_switch` extension

14b. **`NerveSystem.__init__` adds `audit: AuditLogger` keyword-only parameter.** The shutdown flow's audit row (Group A AC #1 step 8 — `await self._audit.log_action(action_type=ActionType.SEED_CAPTURE, ...)`) requires Nerve to hold an `AuditLogger` reference. Story 3.5's `NerveSystem.__init__` did not include one (Nerve had no audit-write surface in 3.5); Story 3.7 introduces it.

   Constructor signature reshape (insert `audit: AuditLogger` between `hands` and `clock` per the alphabetical-by-port-stem precedent the codebase already uses for keyword-only system constructors):

   ```python
   def __init__(
       self,
       *,
       brain: BrainPort,
       ritual: RitualPort,
       skin: SkinPort,
       event_bus: EventBus,
       tier_manager: TierManager,
       config: NovaConfig,
       hands: HandsPort,
       audit: AuditLogger,            # NEW Story 3.7
       clock: Callable[[], datetime] = _utc_now,
   ) -> None:
       ...
       self._hands = hands
       self._audit = audit            # NEW Story 3.7
       self._clock = clock
       ...
   ```

   `AuditLogger` is the concrete class (from `nova.core.audit`) — NOT a port. This matches Story 1.8's "audit is a cross-cutting service, not a port" precedent and Story 3.6's `HandsSystem` audit-injection pattern.

   The reshape forces a corresponding update to:
   * **Composition root** (Group F AC #19) — `create_app(...)` passes the existing `audit` instance.
   * **Test fixture** (Group G AC #20) — `_build_nerve_system` gains an `audit: MagicMock | None = None` parameter that defaults to a fresh `MagicMock(spec=AuditLogger)` builder.

15. **New fields** in [`NerveSystem.__init__`](../../src/nova/systems/nerve/system.py#L213-L264) — append after the existing `_active_mode_name` declaration at line 249:

    ```python
    # Story 3.6 — active mode tracker (set by _handle_mode_switch).
    self._active_mode_name: str | None = None
    # Story 3.7 — apps successfully launched by the LAST successful
    # mode-restore. Tuple of display names (mode_config.apps[i].name).
    # Used by the shutdown summary's "Apps used" line and the shutdown
    # workspace_snapshot's apps tuple. NOT cumulative across mode
    # switches — multi-mode sessions show only the active mode's apps
    # (T1 simplicity; cumulative-with-dedup lands in Epic 6 if needed).
    self._active_mode_apps_launched: tuple[str, ...] = ()
    # Story 3.7 — session start timestamp. Stamped in startup() step 9
    # before brain.create_session so Nerve has the value at shutdown
    # time for duration computation. Cleared on _cleanup_after_repl
    # alongside _session_active.
    self._session_started_at: str | None = None
    ```

    Field order: `_active_mode_name` (Story 3.6, existing), `_active_mode_apps_launched` (NEW), `_session_started_at` (NEW). The two NEW fields land at the end of the constructor body, NOT mixed with the Story 3.5 lifecycle fields — keeps the diff minimal.

16. **Extend `_handle_mode_switch`** at [src/nova/systems/nerve/system.py:838-896](../../src/nova/systems/nerve/system.py#L838) — add `_active_mode_apps_launched` set/clear alongside `_active_mode_name`:

    ```python
    if any(r.success for r in results):
        self._active_mode_name = mode_stem
        # Pair each app config with its result and keep only successful
        # launches. zip is safe — Story 3.6's HandsSystem returns one
        # ActionResult per app in mode_config.apps order.
        self._active_mode_apps_launched = tuple(
            app.name for app, r in zip(mode_config.apps, results) if r.success
        )
    else:
        self._active_mode_name = None
        self._active_mode_apps_launched = ()
    ```

    Story 3.6's existing tests gain assertions on the new tuple field — see AC #22 (Block J).

17. **Reshape `startup()` step 9** at [src/nova/systems/nerve/system.py:336-341](../../src/nova/systems/nerve/system.py#L336-L341). Stamp `started_at` before the create_session call:

    ```python
    # Step 9 — create the runtime session. Stamp started_at HERE so
    # Nerve has the value at shutdown time for duration computation
    # (the Brain-side started_at=None branch resamples internally but
    # never returns the stamped value — Nerve must own the stamp to
    # observe it). _session_active flips AFTER create_session returns
    # so a Brain failure leaves it False and the finally cleanup is a
    # clean no-op.
    started_at = events._utc_now_iso()
    self._session_id = await self._brain.create_session(
        mode_name=None, started_at=started_at
    )
    self._session_started_at = started_at
    self._session_active = True
    ```

    The stamping uses `events._utc_now_iso()` — same module-level function the adapter uses, NOT a new helper. Tests monkeypatch the module attribute (`nova.core.events._utc_now_iso`) for deterministic timing per Story 3.1 / 3.5 precedent.

18. **Reshape `_cleanup_after_repl`** at [src/nova/systems/nerve/system.py:420-495](../../src/nova/systems/nerve/system.py#L420) — reset `_active_mode_name`, `_active_mode_apps_launched`, AND `_session_started_at` **in the existing `finally` block** (the same block that already runs `_uninstall_signal_handler()`). Single rule: these fields clear unconditionally — even when the cleanup-path Brain write raises an unanticipated `BaseException` (e.g., `CancelledError` from the asyncio teardown) that would skip a try-body reset.

    ```python
    finally:
        # Story 3.7 — reset Story 3.6 / 3.7 mode-tracking + session-
        # start fields alongside lifecycle state. Placement matches
        # _uninstall_signal_handler (Story 3.5) — both run on every
        # cleanup path, including BaseException escapes. Fields are
        # in-memory state, not durable — safe to clear unconditionally.
        self._active_mode_name = None
        self._active_mode_apps_launched = ()
        self._session_started_at = None
        self._uninstall_signal_handler()
    ```

    Position: inside the existing `finally` block, BEFORE `_uninstall_signal_handler()`. The order within the finally is documentation only (the resets and the uninstall are independent operations); placing the resets first keeps the `_uninstall_signal_handler()` call visually adjacent to its docstring-rationale comment from Story 3.5.

### Group F: Composition root + cli.py wiring

19. **`cli.py` is UNTOUCHED.** Story 3.7 adds no new CLI flags, no new exit codes, no new subcommands. The existing `await app.nerve.startup()` already drives the REPL into `_handle_shutdown` via `route_command` — no orchestration change at the cli boundary.

19b. **`src/nova/app.py` minimal reshape — pass `audit=audit` to `NerveSystem(...)`.** Story 3.7 introduces no new ports, no new adapters, no new systems, and no new dataclass fields on `NovaApp`. The ONLY required change is a single new keyword argument in the existing `nerve = NerveSystem(...)` call inside `create_app`:

    ```python
    nerve: NervePort = NerveSystem(
        brain=brain,
        ritual=ritual,
        skin=skin,
        event_bus=event_bus,
        tier_manager=tier_manager,
        config=config,
        hands=hands,
        audit=audit,          # NEW Story 3.7
    )
    ```

    The `audit` local is the existing `AuditLogger` instance constructed at [src/nova/app.py:187](../../src/nova/app.py#L187) for Story 1.8 — Hands already consumes it via Story 3.6's wiring. Nerve becomes the second consumer; the composition-root variable does not change shape, only its consumer count.

    The existing partial-init `try / except BaseException` cleanup block already covers the `NerveSystem(...)` instantiation; the new kwarg adds zero new failure modes (the constructor stays reference-storage only, no I/O, no new resources acquired at construction).

    No reshape to `NovaApp` (the dataclass), no reshape to the `_close()` callback, no reshape to the `audit` instance itself. The composition-root regression test in [tests/unit/test_composition_root.py](../../tests/unit/test_composition_root.py) gains one assertion: the `NerveSystem(...)` AST call has an `audit=` kwarg — mirrors Story 3.6's `hands=` kwarg AST guard.

### Group G: `tests/unit/systems/nerve/test_nerve_system.py` — test reshape + new shutdown tests

20. **Update existing placeholder tests** for `_handle_shutdown` — do NOT delete:
    - The parametrized `test_route_command_dispatches_layer_b_routable` SHUTDOWN row — change the assertion from "render_response with 'Session ended.' string" to "ritual.begin_shutdown called once with a ShutdownState".
    - Any `test_handle_shutdown_calls_brain_end_session_when_session_active` — UPDATE to assert the new transactional contract: `brain.commit_shutdown` is called exactly once with the right `ShutdownCommit` (seed entered or cancel path), and `brain.end_session` / `brain.store_snapshot` are NOT called from the user-typed shutdown path.
    - Tests asserting `_session_active=False` after shutdown — UPDATE to assert it flips ONLY when `brain.commit_shutdown` returns successfully (the failure-path test asserts it stays True so cleanup writes the interrupted marker).
    - The `_build_nerve_system` test fixture gains an `audit: AuditLogger | None = None` keyword-only parameter (defaulted to a `MagicMock(spec=AuditLogger)` builder).

21. **New shutdown-flow tests** in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py). Use mocks for `BrainPort` / `RitualPort` / `SkinPort` / `EventBus` / `AuditLogger`. Test layout — parallels Story 3.6's block organization:

    **Block I — Happy path (seed entered):**
    - `test_handle_shutdown_assembles_state_from_runtime_fields` — set `_session_id=42`, `_session_started_at="2026-05-05T10:00:00Z"`, `_active_mode_name="coding"`, `_active_mode_apps_launched=("VS Code",)`; mock `events._utc_now_iso` to return `"2026-05-05T10:30:00Z"`; assert `ritual.begin_shutdown` called once with a `ShutdownState` whose fields match exactly (use `call_args.args[0]` to capture the state).
    - `test_handle_shutdown_renders_shutdown_card_after_ritual_call` — mock `ritual.begin_shutdown` to return a sentinel ShutdownViewModel; assert `skin.render_shutdown_card` called once with that exact object.
    - `test_handle_shutdown_collects_seed_via_skin_collect_input` — mock `skin.collect_input` to return `"finish auth tests"`; assert called once with `prompt="What should you pick up tomorrow?"`.
    - `test_handle_shutdown_calls_commit_shutdown_with_seed_and_summary_and_apps` — assert `brain.commit_shutdown` called once with `(session_id=42, ShutdownCommit(seed_text="finish auth tests", summary="Coding mode, 30m", snapshot_apps=("VS Code",), snapshot_focused_app=None, snapshot_mode_name="coding"))`. Use `call_args.args` to capture the DTO and assert field-by-field.
    - `test_handle_shutdown_does_not_call_end_session_or_store_snapshot_or_add_memory_item` — assert `brain.end_session.call_count == 0`, `brain.store_snapshot.call_count == 0`, AND that the BrainPort mock has no `add_memory_item` attribute being called (the user-typed shutdown path goes through `commit_shutdown` exclusively).
    - `test_handle_shutdown_audit_seed_capture_success_with_has_seed_true` — assert `audit.log_action(action_type=ActionType.SEED_CAPTURE, target="42", result=RESULT_SUCCESS, details={"has_seed": True, "outcome": "saved"})`.
    - `test_handle_shutdown_audit_fires_after_commit_shutdown_returns` — `parent.attach_mock` cross-mock ordering: `brain.commit_shutdown` → `audit.log_action`.
    - `test_handle_shutdown_emits_seed_saved_then_session_ended_in_order` — cross-mock ordering across the two emissions.
    - `test_handle_shutdown_emissions_fire_after_audit_log` — `commit_shutdown` → `audit.log_action` → `event_bus.emit(SeedSaved)` → `event_bus.emit(SessionEnded)` → `skin.render_response`. Locks the full step 6 → step 10 chronological chain.
    - `test_handle_shutdown_renders_planted_for_tomorrow` — assert `skin.render_response("Planted for tomorrow.")`.
    - `test_handle_shutdown_returns_exit_outcome` — assert return value `is CommandOutcome.EXIT`.
    - `test_handle_shutdown_flips_session_active_to_false_after_commit_shutdown` — assert `_session_active is False` AFTER the call returns.

    **Block II — Cancel paths (no seed):**
    - `test_handle_shutdown_user_types_skip_returns_no_seed` — mock `collect_input` to return `"skip"`; assert `brain.commit_shutdown` called once with `ShutdownCommit(seed_text=None, ...)`, audit `result=RESULT_SKIPPED, outcome="cancelled"`, `event_bus.emit.call_count == 1` (only `SessionEnded`, no `SeedSaved`), final render `"Cancelled."`. The `commit_shutdown` adapter SKIPS the memory_items INSERT internally when `seed_text is None`; tests at AC #25 lock the adapter-side branch.
    - `test_handle_shutdown_user_types_cancel_case_insensitive` — mock returns `"CANCEL"`; same assertions.
    - `test_handle_shutdown_skip_with_trailing_whitespace_still_cancels` — `collect_input` returns `"  skip  "`; same assertions (the helper strips before comparing).
    - `test_handle_shutdown_cancel_substring_is_seed_text` — `collect_input` returns `"cancel my plan"`; assert `seed_text="cancel my plan"` (NOT a cancel — substring match is forbidden).
    - `test_handle_shutdown_eof_during_prompt_treats_as_cancel` — `collect_input` raises `EOFError`; assert no exception propagates, `seed_text=None`, audit `result=RESULT_SKIPPED, outcome="cancelled"`.
    - `test_handle_shutdown_keyboard_interrupt_during_prompt_treats_as_cancel` — `collect_input` raises `KeyboardInterrupt`; same as EOF.

    **Block III — Empty input + reprompt + outcome-tuple shape:**
    - `test_collect_seed_with_reprompt_returns_saved_on_first_attempt_success` — direct helper test: `collect_input` returns `"finish auth tests"`; assert `(seed, outcome) == ("finish auth tests", "saved")`.
    - `test_collect_seed_with_reprompt_returns_saved_on_empty_then_seed` — side_effect = `["", "finish auth tests"]`; assert `("finish auth tests", "saved")`.
    - `test_collect_seed_with_reprompt_returns_cancelled_on_first_attempt_skip` — `collect_input` returns `"skip"`; assert `(None, "cancelled")`.
    - `test_collect_seed_with_reprompt_returns_cancelled_on_first_attempt_cancel_uppercase` — `collect_input` returns `"CANCEL"`; assert `(None, "cancelled")`.
    - `test_collect_seed_with_reprompt_returns_empty_twice_on_double_empty` — side_effect = `["", ""]`; assert `(None, "empty_twice")`.
    - `test_collect_seed_with_reprompt_returns_cancelled_on_empty_then_skip` — side_effect = `["", "skip"]`; assert `(None, "cancelled")` — second-attempt terminator wins over empty_twice.
    - `test_collect_seed_with_reprompt_returns_cancelled_on_eof_first_attempt` — `collect_input` raises `EOFError`; assert `(None, "cancelled")`.
    - `test_collect_seed_with_reprompt_returns_cancelled_on_keyboard_interrupt_second_attempt` — first attempt empty, second attempt raises `KeyboardInterrupt`; assert `(None, "cancelled")`.
    - `test_handle_shutdown_empty_input_reprompts_once_then_accepts_seed` — integration: `collect_input` side_effect = `["", "finish auth tests"]`; assert `collect_input.call_count == 2`, second call with `prompt="Please confirm or cancel."`, returned seed text passed into `commit_shutdown`, audit `result=RESULT_SUCCESS, details["outcome"]=="saved"`.
    - `test_handle_shutdown_empty_then_empty_audits_empty_twice` — side_effect = `["", ""]`; assert `commit_shutdown.call_args.args[1].seed_text is None`, audit `result=RESULT_SKIPPED, details["outcome"]=="empty_twice"`, final render `"Cancelled."`.
    - `test_handle_shutdown_empty_then_skip_audits_cancelled` — side_effect = `["", "skip"]`; assert seed=None, audit `details["outcome"]=="cancelled"` (the second-attempt skip distinguishes from empty_twice).
    - `test_handle_shutdown_whitespace_only_input_treated_as_empty` — side_effect = `["   ", "actual seed"]`; assert reprompt fired (whitespace is empty after strip), final outcome `"saved"`.

    **Block IV — Persistence-failure paths:**
    - `test_handle_shutdown_commit_shutdown_failure_audits_failed_renders_error` — mock `brain.commit_shutdown` to raise `StorageError("simulated")`; assert audit `result=RESULT_FAILED, outcome="persistence_failed"`, no event emissions, final render `"Shutdown failed: state may be inconsistent. Check logs."`, `_session_active is True` (stays True so cleanup writes the interrupted marker), return `CommandOutcome.EXIT`.
    - `test_handle_shutdown_commit_shutdown_failure_does_not_emit_session_ended_or_seed_saved` — `event_bus.emit.call_count == 0` after the failure.
    - `test_handle_shutdown_commit_shutdown_failure_leaves_session_active_true_for_cleanup_fallback` — assert `_session_active` is still `True` after `_handle_shutdown` returns; this allows `_cleanup_after_repl` to write the `is_complete=False` interrupted marker as the durable record.
    - **Adapter-level rollback verification** is at AC #25 (Group I) — Block IV asserts the Nerve-side observable behavior under transactional failure; the adapter-side rollback (no rows landed across the three tables) is locked by integration-style tests against a real SQLite engine.

    **Block V — Idempotency:**
    - `test_handle_shutdown_second_call_when_session_already_inactive_returns_exit_no_writes` — set `_session_active=False`; call `_handle_shutdown`; assert no Brain calls, no Skin calls, no Ritual calls, no audit, no events, return `EXIT`.
    - `test_handle_shutdown_idempotent_after_successful_first_call` — drive the happy path once (writes succeed, `_session_active` flips to False); call `_handle_shutdown` again; assert second call is a clean no-op (no Brain/Skin/Ritual/audit/event activity beyond the first call's records). Mock-call-count delta == 0 across both `MagicMock`s.

    **Block VI — Audit isolation (mirrors Story 3.6 Block 5):**
    - `test_handle_shutdown_continues_when_audit_storage_fails` — instantiate a real `AuditLogger` wired to a `_FailingStorageEngine` (the same in-test class from Story 3.6's tests — copy or import); run shutdown happy path; assert all writes happened, all events emitted, render fired, returned `EXIT` cleanly. `caplog` captures the AuditLogger WARNING swallow log.
    - `test_handle_shutdown_does_not_wrap_audit_log_action_in_try_except` — AST guard. Walk the `_handle_shutdown` source; for every `ast.Try` node, walk `body` + `orelse` + `finalbody` + each handler's body; assert NONE contain a call to `self._audit.log_action`. Mirrors the Story 3.6 walker.

    **Block VII — Active-mode integration (Story 3.6 reconciliation):**
    - `test_handle_shutdown_with_no_active_mode_omits_mode_label_in_state` — `_active_mode_name=None`, `_active_mode_apps_launched=()`; assert ShutdownState has `active_mode_stem=None`, `active_mode_display_name=None`, `apps_used=()`. AND assert `commit_shutdown.call_args.args[1].snapshot_mode_name is None`, `snapshot_apps == ()`.
    - `test_handle_shutdown_resolves_active_mode_display_name_from_config` — `_active_mode_name="study-group"`, `config.modes["study-group"].name="Study Group"`; assert ShutdownState's `active_mode_display_name="Study Group"`.
    - `test_handle_shutdown_summary_text_uses_display_name_not_stem` — assert `commit_shutdown.call_args.args[1].summary` is `"Study Group mode, 30m"` (display name), NOT `"study-group mode, 30m"`.
    - `test_handle_shutdown_snapshot_mode_name_uses_stem_not_display_name` — same fixture; assert `commit_shutdown.call_args.args[1].snapshot_mode_name == "study-group"` (the canonical stem, NOT the display label — matches Story 3.6's stem-as-canonical-identity contract).

    **Block VIII — `startup()` step 9 reshape:**
    - `test_startup_stamps_session_started_at_before_create_session` — mock `events._utc_now_iso` to return a fixture value; mock `brain.create_session`; run `startup`; assert `events._utc_now_iso` called BEFORE `create_session`, AND `_session_started_at == fixture_value` after startup completes.
    - `test_startup_passes_stamped_started_at_to_create_session` — assert `brain.create_session.call_args.kwargs["started_at"]` matches the stamped value (NOT `None`).
    - `test_cleanup_after_repl_resets_session_started_at` — drive a full startup → cleanup; assert `_session_started_at is None` after cleanup.

22. **Story 3.6 reconciliation in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py)** — extend the existing mode-switch tests:

    - `test_mode_switch_success_sets_active_mode_apps_launched_to_successful_apps` — 3-app mode, all succeed; assert `_active_mode_apps_launched == ("App1", "App2", "App3")`.
    - `test_mode_switch_partial_sets_active_mode_apps_launched_to_only_successful` — 3-app mode, app 2 fails; assert `_active_mode_apps_launched == ("App1", "App3")`.
    - `test_mode_switch_total_failure_clears_active_mode_apps_launched` — 3-app mode, all fail; assert `_active_mode_apps_launched == ()`.
    - `test_mode_switch_overwrites_active_mode_apps_launched_on_second_switch` — first switch to `coding` (3 apps succeed), then second switch to `study` (2 apps succeed); assert `_active_mode_apps_launched` after second call is the study tuple, NOT the coding tuple.

### Group H: `tests/unit/systems/ritual/test_ritual_system.py` — `begin_shutdown` impl tests

23. **New tests in [tests/unit/systems/ritual/test_ritual_system.py](../../tests/unit/systems/ritual/test_ritual_system.py)** (existing file from Story 3.3 — append):

    - `test_begin_shutdown_returns_view_model_with_locked_title_and_prompt` — assert `view_model.title == "Session ending"`, `view_model.prompt_text == "What should you pick up tomorrow?"`.
    - `test_begin_shutdown_renders_mode_label_when_active_mode_present` — `state.active_mode_display_name="Coding"`; assert `view_model.mode_label == "Mode: Coding"`.
    - `test_begin_shutdown_omits_mode_label_when_no_active_mode` — `state.active_mode_display_name=None`; assert `view_model.mode_label is None`.
    - `test_begin_shutdown_renders_duration_label_via_format_duration_seconds` — `started_at=...T10:00:00Z`, `ended_at=...T11:23:00Z`; assert `view_model.duration_label == "Duration: 1h 23m"`.
    - `test_begin_shutdown_clamps_negative_duration_to_zero` — `ended_at` < `started_at` (clock skew); assert `view_model.duration_label == "Duration: 0s"`.
    - `test_begin_shutdown_zero_duration_renders_as_0s` — same start and end timestamps; assert `"Duration: 0s"`.
    - `test_begin_shutdown_renders_apps_label_with_comma_separated_names` — `apps_used=("VS Code", "Postman")`; assert `view_model.apps_label == "Apps: VS Code, Postman"`.
    - `test_begin_shutdown_omits_apps_label_when_apps_used_empty` — `apps_used=()`; assert `view_model.apps_label is None`.
    - `test_begin_shutdown_escapes_commas_in_app_names` — `apps_used=("Foo, Bar",)`; assert `view_model.apps_label == "Apps: Foo\\, Bar"`.
    - `test_begin_shutdown_passes_session_id_through` — `state.session_id=42`; assert `view_model.session_id == 42`.
    - `test_begin_shutdown_handles_trailing_z_iso_strings` — `started_at="2026-05-05T10:00:00Z"`; assert no `ValueError` from `fromisoformat`.

### Group I: `tests/unit/adapters/sqlite/test_brain.py` — `commit_shutdown` + `end_session` idempotency

24. **New tests for `commit_shutdown`** in [tests/unit/adapters/sqlite/test_brain.py](../../tests/unit/adapters/sqlite/test_brain.py). These are integration-style tests against a real `SqliteStorageEngine` (in-memory `:memory:` DB or a tempfile-backed engine — match the existing test_brain.py fixture pattern). Each test creates a session via `create_session`, then exercises `commit_shutdown`:

    - `test_commit_shutdown_writes_all_three_rows_atomically` — call with `seed_text="finish auth tests"` and a 2-app snapshot; assert `SELECT * FROM sessions WHERE id=?` has `is_complete=1, seed_text="finish auth tests", summary=...`, `SELECT * FROM memory_items WHERE session_id=?` returns one row with `category="seed"` and `content="finish auth tests"`, `SELECT * FROM workspace_snapshots WHERE session_id=?` returns one row with `snapshot_type="shutdown"`.
    - `test_commit_shutdown_skips_memory_items_when_seed_text_is_none` — call with `seed_text=None`; assert sessions row updated (with `seed_text=NULL`), `SELECT COUNT(*) FROM memory_items WHERE session_id=?` returns 0, snapshot row still inserted.
    - `test_commit_shutdown_uses_same_ended_at_across_all_three_rows` — capture the returned `ended_at`; assert `SELECT ended_at FROM sessions`, `SELECT created_at FROM memory_items`, `SELECT captured_at FROM workspace_snapshots` ALL return the same exact ISO string. Locks the cross-row timestamp consistency invariant.
    - `test_commit_shutdown_returns_stamped_ended_at` — assert returned value is a valid ISO-8601 UTC string AND matches the `sessions.ended_at` column value.
    - `test_commit_shutdown_persists_seed_category_as_string` — `seed_text="x"`; assert `SELECT category FROM memory_items` returns the string `"seed"` (NOT the enum repr).
    - `test_commit_shutdown_persists_shutdown_snapshot_type_as_string` — assert `SELECT snapshot_type FROM workspace_snapshots` returns `"shutdown"`.
    - `test_commit_shutdown_persists_apps_via_workspace_data_json` — `snapshot_apps=("VS Code", "Postman")`; assert `SELECT workspace_data FROM workspace_snapshots` returns the locked JSON shape `{"apps":["VS Code","Postman"],"focused_app":null,"mode_name":null}` (matching Story 3.1's serializer).
    - `test_commit_shutdown_logger_does_not_emit_content` — caplog DEBUG; assert the `"brain.commit_shutdown start"` message and extras include the opaque `session_id_opaque` placeholder but NOT seed text content, app names, or any substring.
    - `test_commit_shutdown_raises_storage_error_when_session_does_not_exist` — call with a `session_id` that has no row; assert `StorageError` raised AND no orphan `memory_items` / `workspace_snapshots` rows landed. Locks the "missing row is a programmer error, surface loudly" branch.
    - `test_commit_shutdown_re_called_on_completed_session_skips_all_writes` — first call finalizes the session with seed/summary/snapshot. Second call passes DIFFERENT inputs; assert the returned `ended_at` matches the first call's value, sessions row carries the FIRST commit's data (NOT overwritten), exactly ONE `memory_items` row (the first call's content), exactly ONE `workspace_snapshots` row (the first call's apps). WARNING log fires. **This locks the BrainPort idempotency contract — re-call must NOT create duplicate seed memory rows or duplicate shutdown snapshots.**
    - `test_commit_shutdown_re_called_with_no_seed_skips_all_writes` — first call has `seed_text=None` (cancel path); second call has `seed_text="late attempt"`. Assert zero `memory_items` rows after the second call (the second call short-circuited; the first call wrote no memory_items because `seed_text` was None).
    - **Atomicity / rollback test** — `test_commit_shutdown_rolls_back_when_mid_transaction_failure`. Monkeypatch `nova.adapters.sqlite.brain._serialize_workspace_data` to raise `RuntimeError` mid-transaction (after the session UPDATE + memory_items INSERT, before the workspace_snapshots INSERT). Assert the exception propagates AND `SELECT is_complete FROM sessions WHERE id=?` returns 0 (UPDATE rolled back), `SELECT COUNT(*) FROM memory_items WHERE session_id=?` returns 0 (INSERT rolled back), `SELECT COUNT(*) FROM workspace_snapshots WHERE session_id=?` returns 0. **The transaction's all-or-nothing contract is the load-bearing invariant** that makes the Nerve-side "leave `_session_active=True` for cleanup fallback" posture safe. If this test fails, the entire shutdown design is broken.

25. **New tests for `end_session` idempotency** (defensive hardening — Story 3.7's user-typed shutdown does NOT call `end_session`, but `_cleanup_after_repl` and the Story 3.5 signal handler both do; these tests lock the WHERE-filter contract):

    - `test_end_session_re_called_on_completed_session_is_no_op_returns_existing_ended_at` — call `end_session(s, seed_text="first", summary="first", is_complete=True)`; capture the stamped `ended_at`. Call again with `seed_text="second", summary="second"`; assert second call returns the SAME `ended_at`, AND `SELECT seed_text FROM sessions WHERE id=s` still returns `"first"` (no overwrite).
    - `test_end_session_re_called_logs_warning_about_no_op` — caplog WARNING during the second call; assert message matches `"end_session re-called on already-completed session"`.
    - `test_end_session_first_call_on_incomplete_row_proceeds_normally` — fresh session created via `create_session`; call `end_session`; assert seed_text/summary/is_complete columns updated.
    - `test_end_session_with_is_complete_false_can_be_called_multiple_times` — call twice with `is_complete=False`; assert each call updates the row (the WHERE filter only blocks re-completion, not re-marking-as-incomplete). Important for Story 3.10's signal-handler retries.
    - `test_end_session_zero_rows_match_logs_warning_and_returns_stamped_iso` — call with `session_id=99999` (no row); assert WARNING log fires + return value is a valid ISO string + no raise.

### Group J: `tests/unit/ports/` + `tests/unit/adapters/rich/`

26. **Extend [tests/unit/ports/test_port_isolation.py](../../tests/unit/ports/test_port_isolation.py)**:
    - `test_skin_port_render_shutdown_card_takes_shutdown_view_model` — `typing.get_type_hints(SkinPort.render_shutdown_card)` resolves the parameter to `ShutdownViewModel`.
    - `test_ritual_port_begin_shutdown_takes_shutdown_state_returns_shutdown_view_model` — same hints check on `RitualPort.begin_shutdown`.
    - `test_brain_port_commit_shutdown_takes_shutdown_commit_returns_str` — assert the method exists with parameters `(self, session_id: int, commit: ShutdownCommit) -> str`.
    - `test_brain_port_does_not_expose_add_memory_item_in_t1` — defensive assertion that `BrainPort` does NOT have `add_memory_item` as an attribute / method (locks the "Story 3.7 ships memory-item-write inside commit_shutdown only" decision; a future regression that adds the standalone surface trips this test, prompting an explicit story-level decision).
    - `test_shutdown_data_no_longer_exported_from_ritual_models` — assert `ShutdownData` is NOT in `nova.systems.ritual.models.__all__` (the retire-not-rename decision lock).
    - `test_shutdown_commit_is_exported_from_brain_models` — assert `ShutdownCommit` IS in `nova.systems.brain.models.__all__`.

27. **Extend [tests/unit/adapters/rich/test_skin_adapter.py](../../tests/unit/adapters/rich/test_skin_adapter.py)** for `render_shutdown_card`:
    - `test_render_shutdown_card_emits_panel_with_yellow_border_and_title` — capture console output via `Console(record=True, file=StringIO())`; assert the Panel object passed to `console.print` has `border_style="yellow"` and `title.style="bold yellow"`.
    - `test_render_shutdown_card_omits_mode_label_when_none` — assert the rendered body does NOT contain `"Mode:"`.
    - `test_render_shutdown_card_emits_duration_label_always` — assert the body contains `"Duration: 1h 23m"`.
    - `test_render_shutdown_card_omits_apps_label_when_none` — assert the body does NOT contain `"Apps:"`.
    - `test_render_shutdown_card_emits_prompt_text_in_bold_bright_white` — assert the prompt is rendered with the bold-bright-white style.
    - `test_render_shutdown_card_uses_text_append_not_markup_string_concat` — markup-injection-safe check; pass a mode_label like `"[bold red]Coding[/]"`; assert no actual Rich markup activates in the rendered output (the `[`/`]` characters appear literally).

28. **Extend [tests/unit/systems/brain/test_brain_models.py](../../tests/unit/systems/brain/test_brain_models.py)** (or create if missing):
    - `test_shutdown_commit_is_frozen_dataclass`.
    - `test_shutdown_commit_constructs_with_required_fields`.
    - `test_shutdown_commit_snapshot_apps_is_tuple_not_list` — pass `snapshot_apps=("a", "b")`; assert `isinstance(commit.snapshot_apps, tuple)`. Locks the immutability invariant per Story 1.9 AC #5.
    - `test_shutdown_commit_does_not_carry_timestamp_field` — assert `ShutdownCommit` has no `ended_at` / `created_at` / `captured_at` / timestamp attribute (locks the "adapter is single source of truth for timestamps" decision via field introspection: `[f.name for f in fields(ShutdownCommit)]` does NOT contain any timestamp-suffixed name).

### Group K: AST isolation locks

29. **Extend [tests/unit/systems/ritual/test_ritual_system_isolation.py](../../tests/unit/systems/ritual/test_ritual_system_isolation.py)** (existing — Story 3.3) — verify the new AST guards still hold after `begin_shutdown` body lands:
    - **Forbidden imports unchanged:** `nova.adapters`, `nova.systems.brain`, `nova.systems.skin`, `nova.systems.nerve`, `nova.app`, `nova.cli`, `sqlite3`, `rich`.
    - **Allowed imports:** `nova.core.{formatting,types}` (the `formatting` import now also pulls in `diff_iso_seconds`), `nova.systems.brain.models` (BriefingAggregate / ModeInfo / SessionSummary), `nova.systems.eyes.models` (WorkspaceSnapshot), `nova.systems.ritual.models`, stdlib (`logging`).

30. **Extend [tests/unit/systems/nerve/test_nerve_system_isolation.py](../../tests/unit/systems/nerve/test_nerve_system_isolation.py)**:
    - **No new forbidden imports.** Story 3.7's Nerve module imports `diff_iso_seconds` from `nova.core.formatting` — a neutral core helper, NOT a sibling-system module. The strict "no `nova.systems.{X}.system` imports" rule from Story 3.6's nerve isolation test stays in force without exception.
    - **Update positive imports:** add `nova.systems.brain.models.ShutdownCommit`, `nova.systems.ritual.models.{ShutdownState, ShutdownViewModel}`, and verify `nova.core.formatting.diff_iso_seconds` is importable from `nova.core.formatting`.

### Group L: Integration test

31. **`tests/integration/test_session_loop.py`** — append ONE new test (the existing two `windows_only` tests from Story 3.6 stay):

    - `test_full_session_loop_with_shutdown_and_seed_capture_end_to_end` — empty `data_dir` with a single mode at `modes/coding.yaml` (Notepad-only — no Win32 launch needed for this test; mock the launcher OR use the existing Notepad pattern from Story 3.6 with cleanup). Stdin pipe: `"mode coding\nshutdown\nfinish auth tests\n"`. Assert: `EXIT_OK`; `audit_log` contains 1 `mode_restore`/success row + 1 `seed_capture`/success row with `details["has_seed"]==True, details["outcome"]=="saved"`; `sessions` row has `is_complete=1, seed_text="finish auth tests", summary="Coding mode, <duration>"`; `memory_items` row has `category="seed", content="finish auth tests"`; `workspace_snapshots` row has `snapshot_type="shutdown"`. Capture stdout via `Console(file=StringIO())` injection; assert `"Mode: Coding"` and `"What should you pick up tomorrow?"` and `"Planted for tomorrow."` appear in order.

    Mark `@pytest.mark.windows_only @pytest.mark.integration` IFF the test uses real Notepad; pure-mock variant does not need either marker. Pin one approach (mock launcher recommended — keeps the integration test fast and OS-neutral, lets the existing Story 3.6 tests cover real-OS launch).

### Group M: deferred-work close-out

32. **One deferred entry closes** at story completion (Dev sets `Status: review`):
    - Strike-through [deferred-work.md:231](deferred-work.md#L231) (`end_session` idempotency guard): `**Closed by Story 3.7 (<date>).** UPDATE WHERE clause now filters on is_complete=0; re-call on already-completed session is a no-op that returns the existing ended_at. See [3-7-shutdown-flow-and-seed-capture.md](3-7-shutdown-flow-and-seed-capture.md).`

### Group N: CI gate

33. **Full quality gate.** All gates pass without weakening:
    - `uv run ruff check src/ tests/` — clean.
    - `uv run ruff format --check src/ tests/` — clean.
    - `uv run mypy src/ tests/` — clean (strict mode catches the port reshape at every consumer).
    - `uv run pytest tests/unit/` — passes. Net delta vs. Story 3.6 baseline (1863 unit pass per sprint-status): expect **+55 to +80 unit tests** (Nerve shutdown ≈ 30, Ritual shutdown ≈ 11, Brain adapter `commit_shutdown` + idempotency ≈ 15 including atomicity rollback, Skin shutdown_card ≈ 6, port isolation ≈ 6, mode-switch tracker extension ≈ 4, brain models ≈ 4, `diff_iso_seconds` ≈ 4).
    - `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes; +1 new integration test.
    - **100% coverage** on the new + reshaped source-code modules: `nova.systems.ritual.system` (existing 100% holds; new branches covered), `nova.adapters.sqlite.brain.commit_shutdown` (new method), `nova.adapters.sqlite.brain.end_session` (new branches), `nova.adapters.rich.skin.render_shutdown_card` (new body), `nova.systems.nerve.system._handle_shutdown` (new body) + helpers (`_collect_seed_with_reprompt`, `_resolve_active_mode_display_name`, `_build_session_summary_text`, `_classify_audit_outcome`), `nova.core.formatting.diff_iso_seconds` + `_parse_iso` (new helpers).
    - **Coverage on modified modules** stays at parity: `nova.systems.nerve.system` (95%+; `_handle_mode_switch` and `startup` extensions add branches), `nova.systems.ritual.models` + `nova.systems.brain.models` (100% — new dataclasses).

### Group O: Same-session adversarial review

34. **Same-session three-layer review** (Blind Hunter / Edge Case Hunter / Acceptance Auditor) per the Story 3.5/3.6 precedent. Story 3.7 is NOT an A3 fresh-session trial target. Triage findings into decision-needed / patches / deferred / dismissed.

## Tasks / Subtasks

- [x] **Task 1 — Brain port + adapter: `commit_shutdown` + `ShutdownCommit`** (AC: #9, #10, #11)
  - [x] Add `ShutdownCommit` frozen dataclass to [src/nova/systems/brain/models.py](../../src/nova/systems/brain/models.py); update `__all__`.
  - [x] Add `commit_shutdown` Protocol method to [src/nova/ports/brain.py](../../src/nova/ports/brain.py); import `ShutdownCommit`.
  - [x] Add three new SQL constants and `commit_shutdown` impl wrapping all three writes in `engine.transaction()` to [src/nova/adapters/sqlite/brain.py](../../src/nova/adapters/sqlite/brain.py); refactor existing `_serialize_snapshot` to share a lower-level `_serialize_workspace_data` helper; update module docstring to spell out the transactional contract; update the "Memory-item writes ... own those extensions" line to clarify Story 3.7 lands the seed-write inside `commit_shutdown`'s transaction (standalone surface stays Epic 4/5).
  - [x] `uv run mypy src/nova/systems/brain/models.py src/nova/ports/brain.py src/nova/adapters/sqlite/brain.py` — clean.

- [x] **Task 2 — Brain adapter: `end_session` idempotency guard** (AC: #12)
  - [x] Reshape `_UPDATE_SESSION_END_SQL` to add `AND is_complete = 0` filter.
  - [x] Reshape `end_session` body to handle three branches (row missing / row already-complete / row incomplete).
  - [x] Verify return contract — already-complete returns existing ended_at, incomplete returns newly-stamped one.

- [x] **Task 3 — Ritual: port reshape + `ShutdownState`/`ShutdownViewModel` + `begin_shutdown` impl** (AC: #6, #7, #8)
  - [x] Reshape `RitualPort.begin_shutdown` signature in [src/nova/ports/ritual.py](../../src/nova/ports/ritual.py).
  - [x] Replace `ShutdownData` with `ShutdownState` + `ShutdownViewModel` in [src/nova/systems/ritual/models.py](../../src/nova/systems/ritual/models.py); update `__all__`.
  - [x] Implement `RitualSystem.begin_shutdown` body in [src/nova/systems/ritual/system.py](../../src/nova/systems/ritual/system.py) — locked-copy constants, view-model assembly, `_diff_iso_seconds` + `_parse_iso` helpers.

- [x] **Task 4 — Skin: `render_shutdown_card` impl** (AC: #13, #14)
  - [x] Reshape `SkinPort.render_shutdown_card` signature in [src/nova/ports/skin.py](../../src/nova/ports/skin.py).
  - [x] Replace `RichSkinAdapter.render_shutdown_card` `NotImplementedError` body in [src/nova/adapters/rich/skin.py](../../src/nova/adapters/rich/skin.py).
  - [x] Update module docstring; drop unused `SessionSummary` import if no other code path uses it after the reshape.

- [x] **Task 5 — Nerve: `_handle_shutdown` body + helpers + field additions** (AC: #1, #2, #3, #4, #4b, #5, #15)
  - [x] Add `diff_iso_seconds` (public) and `_parse_iso` (private) to [src/nova/core/formatting.py](../../src/nova/core/formatting.py); update `__all__`.
  - [x] Add `_active_mode_apps_launched` and `_session_started_at` fields in `NerveSystem.__init__`.
  - [x] Add `_SeedOutcome = Literal["saved", "cancelled", "empty_twice"]` module type alias.
  - [x] Replace `_handle_shutdown` body wholesale.
  - [x] Add `_collect_seed_with_reprompt`, `_resolve_active_mode_display_name` private methods.
  - [x] Add `_build_session_summary_text` and `_classify_audit_outcome` module-private helpers.
  - [x] Import `diff_iso_seconds` from `nova.core.formatting`.
  - [x] Add new imports: `ShutdownCommit`, `RESULT_SUCCESS`, `RESULT_FAILED`, `RESULT_SKIPPED`, `ActionType`, `SeedSaved`, `ShutdownState`.

- [x] **Task 6 — Nerve: `startup()` step 9 reshape** (AC: #17, #18)
  - [x] Stamp `started_at` before `create_session` call; assign `_session_started_at`.
  - [x] Reset all three Story 3.6/3.7 mode-tracking fields in `_cleanup_after_repl`.

- [x] **Task 7 — Nerve: `_handle_mode_switch` extension** (AC: #16)
  - [x] Set `_active_mode_apps_launched` alongside `_active_mode_name` in success branch.
  - [x] Clear both fields in total-failure branch.

- [x] **Task 8 — Unit tests: Nerve shutdown flow** (AC: #20, #21)
  - [x] Update placeholder tests for `_handle_shutdown` (parametrized SHUTDOWN row + direct shutdown tests + `_session_active` flip-ordering).
  - [x] Add Block I (happy path, ~11 tests).
  - [x] Add Block II (cancel paths, ~6 tests).
  - [x] Add Block III (empty + reprompt, ~4 tests).
  - [x] Add Block IV (persistence-failure, ~3 tests).
  - [x] Add Block V (idempotency, ~2 tests).
  - [x] Add Block VI (audit isolation, ~2 tests including AST guard).
  - [x] Add Block VII (active-mode integration, ~3 tests).
  - [x] Add Block VIII (`startup()` step 9 reshape, ~3 tests).

- [x] **Task 9 — Unit tests: mode-switch extension + Ritual + Brain + Skin** (AC: #22, #23, #24, #25, #27, #28)
  - [x] Extend mode-switch tests with `_active_mode_apps_launched` assertions.
  - [x] Add `RitualSystem.begin_shutdown` tests (Block H — ~11 tests).
  - [x] Add `commit_shutdown` integration-style tests (~10 tests including atomicity rollback test).
  - [x] Add `end_session` idempotency tests (~5 tests).
  - [x] Add `render_shutdown_card` tests (Block J — ~6 tests).
  - [x] Add `ShutdownCommit` model tests.
  - [x] Add `diff_iso_seconds` / `_parse_iso` tests in `tests/unit/core/test_formatting.py` (clamp-to-zero, trailing-Z normalization, valid duration computation).

- [x] **Task 10 — AST isolation guards** (AC: #29, #30)
  - [x] Verify `test_ritual_system_isolation.py` still passes (no new forbidden imports).
  - [x] Extend `test_nerve_system_isolation.py` positive imports (`MemoryItemInput`, `ShutdownState`, `ShutdownViewModel`) and the `_diff_iso_seconds` cross-import allowance.
  - [x] Add port-shape tests in `test_port_isolation.py`.

- [x] **Task 11 — Integration test** (AC: #31)
  - [x] Append shutdown-with-seed end-to-end test to [tests/integration/test_session_loop.py](../../tests/integration/test_session_loop.py).

- [x] **Task 12 — deferred-work close-out** (AC: #32)
  - [x] Edit [_bmad-output/implementation-artifacts/deferred-work.md:231](deferred-work.md#L231) — strike-through with closure pointer.

- [x] **Task 13 — Full CI gate** (AC: #33)
  - [x] `uv run ruff check src/ tests/` — clean.
  - [x] `uv run ruff format --check src/ tests/` — clean.
  - [x] `uv run mypy src/ tests/` — clean.
  - [x] `uv run pytest tests/unit/` — passes; +50 to +70 vs. Story 3.6 baseline (1863 unit pass).
  - [x] `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes; +1 new integration test.
  - [x] `uv run pytest tests/unit --cov=nova.systems.nerve --cov=nova.systems.ritual --cov=nova.adapters.sqlite.brain --cov=nova.adapters.rich.skin --cov-report=term-missing` — coverage targets per AC #33.

- [x] **Task 14 — Same-session adversarial review** (AC: #34)
  - [x] Run Blind Hunter / Edge Case Hunter / Acceptance Auditor in parallel general-purpose subagents per the Story 3.5/3.6 precedent. Triage findings.

### Review Findings (formal `/bmad-code-review` pass — 2026-05-05)

**Summary:** 3 layers ran fresh (Blind Hunter / Edge Case Hunter / Acceptance Auditor). 67 raw findings → 41 unique post-dedup. **0 decision-needed**, **8 patches**, **9 deferred**, **24 dismissed.** Acceptance Auditor reports: implementation delivers Story 3.7 as specified; only 1 GAP (missing test assertion) and 2 deliberate-and-defensible DRIFTs (zip strict, field grouping). All triage of HIGH-severity claims about transactional isolation verified directly against `nova.core.storage.engine.SqliteStorageEngine.transaction()` — confirmed `BEGIN IMMEDIATE` + `asyncio.Lock` close the SELECT-then-UPDATE race the Blind/Edge layers worried about.

- [x] [Review][Patch] `commit_shutdown` empty-string `seed_text` inserts an empty `memory_items` row [src/nova/adapters/sqlite/brain.py:401] — the `if commit.seed_text is not None:` guard treats `""` as truthy enough to insert. Nerve strips before passing, but the adapter contract should enforce non-empty too. Fix: change to `if commit.seed_text:`.
- [x] [Review][Patch] `_build_session_summary_text` doesn't escape commas in display name nor guard empty/whitespace [src/nova/systems/nerve/system.py:761-765] — a mode named `"Coding, Tests"` produces `"Coding, Tests mode, 30m"` (ambiguous to comma-aware parsers); empty display name produces `" mode, 30m"` (leading-space artifact). Mirror Ritual's `_escape_label_value` pattern.
- [x] [Review][Patch] Mock fixture sentinel collision — `commit_shutdown` and `end_session` return identical hard-coded ISO [tests/unit/systems/nerve/test_nerve_system.py:_make_brain_mock] — distinct timestamps would catch a regression that accidentally swaps the production code's call.
- [x] [Review][Patch] `_classify_audit_outcome` has no else-raise for unknown outcome [src/nova/systems/nerve/system.py:783-785] — a future fourth `_SeedOutcome` member would silently classify as `RESULT_SKIPPED`. Add an explicit `raise ValueError(f"unknown _SeedOutcome: {outcome!r}")` after the `"saved"` branch.
- [x] [Review][Patch] Rollback test monkeypatch swallows positional args via `**kwargs` [tests/unit/adapters/sqlite/test_brain_adapter.py:test_commit_shutdown_rolls_back_when_mid_transaction_failure] — `def boom(**kwargs: object)` accepts anything; a future refactor that passes positional args would silently keep passing. Use the explicit signature `def boom(*, apps, focused_app, mode_name) -> str:`.
- [x] [Review][Patch] `render_shutdown_card` test missing `title.style == "bold yellow"` assertion [tests/unit/adapters/rich/test_skin_adapter.py — `test_render_shutdown_card_emits_panel_with_yellow_border_and_title`] — production code is correct, but the assertion gap means a future regression dropping the title styling wouldn't be caught.
- [x] [Review][Patch] Stale `fetchone-cost` comment in `end_session` body [src/nova/adapters/sqlite/brain.py — pre-UPDATE inspection block] — old comment claims "single extra fetchone is cheap"; the new SELECT returns more columns. Reword or drop.
- [x] [Review][Patch] Cancel terminator frozenset built per-call [src/nova/systems/nerve/system.py:1110 inside `_collect_seed_with_reprompt`] — `cancel_terminators = frozenset({"skip", "cancel"})` allocates each call. Hoist to module scope as `_CANCEL_TERMINATORS`.
- [x] [Review][Defer] `diff_iso_seconds` does not validate format — corrupt persisted timestamp would tank shutdown via uncaught `ValueError`/`TypeError` [src/nova/core/formatting.py:diff_iso_seconds, src/nova/systems/ritual/system.py:begin_shutdown] — no current trigger (production callers stamp via tz-aware `events._utc_now_iso()`); future restore-from-disk path could surface. Defer pending a Story 5.5 (corruption recovery) or 5.6 (backup/restore) touch.
- [x] [Review][Defer] AST guard `test_handle_shutdown_does_not_wrap_audit_log_action_in_try_except` walks `try.body` only [tests/unit/systems/nerve/test_nerve_system.py] — `try_node.orelse` and `try_node.finalbody` and handler bodies aren't checked. The body-only walker covers the most common defensive-wrap regression vector; full-body walking is incremental hardening.
- [x] [Review][Defer] `_session_active = False` flip race window vs. KeyboardInterrupt [src/nova/systems/nerve/system.py:_handle_shutdown step 7] — Ctrl-C between `commit_shutdown` returning and the flip would let `_cleanup_after_repl` re-enter `end_session(is_complete=False)` → idempotency filter blocks the UPDATE → cleanup re-emits `SessionEnded`. Window is 1-2 Python statements wide. Story 3.10 (crash recovery) territory.
- [x] [Review][Defer] Audit failure-path escape (future-fragility) [src/nova/systems/nerve/system.py:_handle_shutdown failure branch step 8] — current `details` dict is JSON-safe; future evolution that adds non-serializable values would let `audit.log_action`'s `TypeError` propagate, killing the "Shutdown failed" render. Add a wrap in the failure path OR add a JSON-serializability guard at the dict-construction site. Defer pending Epic 5/7 evolution.
- [x] [Review][Defer] `end_session` silently drops `seed_text`/`summary` on no-op branch [src/nova/adapters/sqlite/brain.py:end_session] — re-call with new payload returns existing `ended_at` and discards the new payload silently. Logging the discarded payload would help diagnostics but is not a correctness issue.
- [x] [Review][Defer] `_active_mode_apps_launched` not reset on `restore_mode` exception [src/nova/systems/nerve/system.py:_handle_mode_switch] — if `hands.restore_mode` raises (vs. returning all-failed `ActionResult`s), the `else` clear branch is skipped; previous successful-mode apps stale-survive. Story 3.6 territory; HandsSystem doesn't currently raise on the success/failure branches.
- [x] [Review][Defer] `_handle_shutdown` 10-step procedural method is structurally fragile [src/nova/systems/nerve/system.py:_handle_shutdown] — extract a `ShutdownCoordinator` (or similar) once a second non-shutdown ceremony lands. Refactor opportunity, not a defect.
- [x] [Review][Defer] `render_shutdown_card` panel layout when only `duration_label` is present [src/nova/adapters/rich/skin.py:render_shutdown_card] — the `_emit` block-grouping doc claim about blank-line separation drifts visibly when `mode_label is None and apps_label is None`. Cosmetic; only triggered on the no-active-mode shutdown path.
- [x] [Review][Defer] `apps_label` newline injection [src/nova/systems/ritual/system.py:_escape_label_value reused] — an `AppConfig.name` containing `\n` breaks the panel's single-line layout. Config validation owns this (Epic 6 mode editor); T1 doesn't surface the path because there's no user-editable mode-name flow yet.

## Dev Notes

### Pattern library consulted

- **#1 Briefing-pattern symmetry** — Story 3.7's shutdown flow mirrors Story 3.3's briefing flow exactly: Nerve gathers state, Ritual produces a render-ready view model with pre-rendered labels, Skin renders. The same separation-of-concerns (Ritual is content-only, Skin is style-only) extends to shutdown. The `ShutdownViewModel` shape mirrors `BriefingViewModel`'s pre-rendered-labels precedent (Story 3.3 § "Why pre-rendered labels and not raw component fields").
- **#2 Persist-before-emit (atomic commit edition)** — `commit_shutdown` returns BEFORE any event emission. The transaction is the durable-write boundary; events are runtime fan-out. Mirrors Story 3.5's `SessionStarted` ordering and Story 3.6's `ModeRestored` ordering. Per-emission `try/except` wraps make emission failures observability-only — the durable-fact contract is satisfied by the prior commit.
- **#3 Audit-failure isolation (unwrapped contract)** — Single unwrapped `await self._audit.log_action(...)` call site. Story 1.8's internal `StorageError` swallow is the boundary. Mirrors Story 3.6's HandsSystem audit pattern. AST guard locks the no-try/except contract statically.
- **#4 Frozen dataclass + typed-input DTO (no-timestamp edition)** — `ShutdownCommit` carries domain fields only; the adapter is the single source of truth for `ended_at` / `created_at` / `captured_at` cross-row consistency. The "no caller-stamped timestamp" rule is the load-bearing invariant here — caller-supplied timestamps would create ownership drift across the three rows. Stories 3.1's `WorkspaceSnapshotInput` (caller-stamped) and 3.7's `ShutdownCommit` (adapter-stamped) demonstrate that BOTH patterns are valid; the choice depends on whether the caller has external timestamp semantics (yes for snapshot capture from Eyes; no for atomic shutdown).
- **#5 Pre-rendered labels + progressive omission** — `ShutdownViewModel` carries fully-formatted strings; Skin maps each to a fixed style and omits when None. The `mode_label` / `apps_label` omission is the same pattern as briefing's `last_session_label` / `last_apps_label`.
- **#6 Idempotency via WHERE filter** — `end_session`'s new `is_complete = 0` filter is the atomic-no-op pattern: re-call on a finalized row affects zero rows, the SELECT-then-return-existing branch surfaces the unchanged timestamp. Forward-compatible with Story 3.10's signal handler (which writes `is_complete=False` and is unaffected by the filter).
- **#7 Bounded reprompt loop** — `_collect_seed_with_reprompt` is a 2-attempt for-loop, NOT a `while True` with break. The bound is enforced syntactically — there is no path through the function that loops more than twice. Mirrors project-context.md:200 (no silent retry loops that stall the session).

### Why `_session_active` flips AFTER `commit_shutdown` succeeds (not before)

Story 3.5's `_handle_shutdown` placeholder flipped `_session_active = False` BEFORE the Brain `end_session` await — its docstring spelled out the "best-effort = ONE attempt" rationale: a Brain failure should NOT trigger a `_cleanup_after_repl` retry that overwrites the user's clean-shutdown intent with an interrupted-marker.

Story 3.7 inverts the ordering deliberately. The reasoning hinges on the transactional `commit_shutdown`:

1. **The transaction is all-or-nothing.** Either all three rows landed (atomic commit) OR none of them landed (rollback). There is no partial-state branch where the cleanup-fallback would be wrong about what to record.
2. **On commit failure, the cleanup-fallback `end_session(is_complete=False)` is the correct durable record.** No rows changed during the failed transaction; the row's `is_complete` is still 0; the cleanup write proceeds normally (the idempotency `WHERE is_complete = 0` filter still allows it). Story 3.10's next-startup interrupted-session detection picks up the row honestly.
3. **The user retains agency on hung writes.** If `commit_shutdown` hangs (Brain stuck inside the transaction), the user can press Ctrl-C — same fallback Story 3.5 documented for `_handle_shutdown`. The signal handler's bounded write picks up. The transaction's rollback semantics ensure no partial state lingers.

The flip-after-commit-shutdown-succeeds ordering is intentional and structurally safe under the transactional contract. Tests at AC #21 Block IV lock the failure-path behavior (`_session_active` stays True after a `commit_shutdown` failure; cleanup writes the interrupted marker).

### Why a transactional `commit_shutdown` (atomic three-write Brain method)

Three options were considered:

1. **Three independent Brain writes orchestrated by Nerve** — call `end_session` + (optional) `add_memory_item` + `store_snapshot` as separate port methods. Rejected because the three writes are NOT independent: `end_session(is_complete=True)` is a state-transition write whose follow-on idempotency guard (the `WHERE is_complete = 0` filter, AC #12) makes the row irreversible. If write 2 (memory_item) fails after write 1 succeeded, the `_cleanup_after_repl` interrupted-marker write `end_session(is_complete=False)` would be BLOCKED by the idempotency filter — leaving sessions.is_complete=1 + missing memory_item + missing snapshot + no SessionEnded event with no recovery path.
2. **Storage-engine-level transaction in Nerve** — Nerve opens `engine.transaction()` and calls the three port methods inside it. Rejected because it leaks the storage abstraction into Nerve and bypasses BrainPort's typed boundary (Nerve would need an `engine` reference, which violates the system→port→adapter layering).
3. **Adapter-level transactional `commit_shutdown` method** (chosen) — single BrainPort method that takes a typed `ShutdownCommit` DTO and wraps all three writes in `engine.transaction()` internally. Atomic, all-or-nothing semantics. Returns the stamped `ended_at`.

Option 3 wins because:
- **Atomicity is load-bearing**, not optional. The user-typed shutdown's "if any write fails, leave the session marked interrupted" semantics depend on no-partial-state-possible. The transactional method makes that guarantee structural — a `ShutdownCommit` either succeeds wholly or rolls back wholly.
- **Single source of truth for the timestamp.** The adapter stamps `ended_at` once inside the transaction and writes it to all three rows' timestamp columns (`sessions.ended_at`, `memory_items.created_at`, `workspace_snapshots.captured_at`). Cross-row consistency is structural; no caller-stamped timestamp can drift.
- **The `ShutdownCommit` DTO captures the shutdown-write shape cleanly** — five fields (seed_text, summary, snapshot_apps, snapshot_focused_app, snapshot_mode_name), no timestamps, no `is_complete` flag (always implied True). Future stories that need a different commit shape (e.g., Story 5.2 selective forget which ALSO touches multiple tables) introduce their own typed commit DTO; the pattern generalizes.
- **The standalone `add_memory_item` / `store_snapshot` paths stay unaffected.** Story 3.7's user-typed shutdown does NOT call them. Future Epic 4/5 memory-item writes (session notes, context summaries) and Story 4.1 Eyes-driven snapshot writes use their own port surfaces independently.

The transactional commit also closes the previously-deferred "no cross-method atomicity" question by making it nonexistent: the writes are no longer cross-method — they're a single port call.

### Why the apps tuple is "active-mode-only" not cumulative

A multi-mode session (`mode coding` then `mode study` then `shutdown`) shows the study mode's apps in the shutdown card, NOT the union of coding + study apps.

Three options:

1. **Active-mode-only** (chosen) — the apps tuple is the apps successfully launched by the LAST successful mode-restore.
2. **Cumulative across mode switches** — track every app that was ever launched in the session; deduplicate by name; render the union.
3. **Per-mode breakdown** — render `"Coding mode (3 apps): ..., Study mode (2 apps): ..."` as separate lines.

Option 1 wins for T1 because:
- The user-visible "Apps used" line stays brief and accurate to the CURRENT workspace state. Apps from a switched-away-from mode are no longer in the workspace; listing them is misleading.
- The shutdown workspace_snapshot's `apps` tuple stays consistent with the visible card.
- Cumulative-with-dedup adds a `set` field that grows unbounded across long sessions — minor memory cost but adds complexity.
- Per-mode breakdown turns the shutdown card into a session log; the AC's intent is summary, not history. Future Voice (Epic 7) can add narrative ("Coded for 1h then studied for 30m") if desired.

### Why the seed prompt has 2 attempts (not 3, not unbounded)

Two attempts is the minimum that lets the user recover from accidental Enter-key-pressing without forcing them through a multi-step flow. Three would feel like nagging. Unbounded would defeat the NFR4 30-second budget AND violate project-context.md:200.

The reprompt copy `"Please confirm or cancel."` is locked T1 copy. Voice (Epic 7) may replace with personality-bearing text (`"One sentence — what's tomorrow's hook?"` etc.) but the 2-attempt bound stays.

### Why the cancel terminators are exact-match, not substring

`"cancel my plan tomorrow"` is meaningful seed text. Substring-matching `"cancel"` in seed text would silently discard the user's intent. The exact-match-after-strip rule is the only safe disambiguation.

`skip` and `cancel` are locked T1 terminators. Adding `quit` or `exit` would conflict with the SHUTDOWN command verb (a user typing `quit` at the seed prompt would sensibly want it interpreted as "yes really shut down" — which is what cancel-seed-and-end-session DOES). Story 3.7 documents the two-terminator vocabulary in `_collect_seed_with_reprompt`'s docstring.

### Why audit `target` is the session_id (string), not the seed text

The audit log's `target` field is described in [src/nova/core/audit.py](../../src/nova/core/audit.py) as the action target — for `MODE_RESTORE` it's the mode stem; for `SEED_CAPTURE` it's the session_id (opaque reference per project-context.md sensitive-content rule). The seed text NEVER lands in audit details — that would defeat the privacy intent of audit (audit is queryable by anyone with database access; seeds are personal context).

The `details["has_seed"]: bool` flag lets transparency queries count "seed-capture attempts vs. actually-saved" without revealing content. Story 5.1's transparency display reads this.

### Why `events._utc_now_iso` is the canonical timestamp source

Same rationale as Story 3.1: tests monkeypatch the module attribute (`nova.core.events._utc_now_iso`) for deterministic timing. Importing `from nova.core.events import _utc_now_iso` would break that pattern — the local binding wouldn't see the monkeypatch. Use the dotted form (`events._utc_now_iso()`) consistently.

### Cross-module helper reuse — `diff_iso_seconds` lives in `nova.core.formatting`

Both Nerve's `_build_session_summary_text` and Ritual's `begin_shutdown` need to compute "duration_seconds from two ISO strings." Three options were considered:

1. **Duplicate the helper** in both modules — rejected. project-context.md "no magic literals for cross-cutting rules" extends to "no duplicated parsing rules" — an ISO format change should land in one place.
2. **Helper in `nova.systems.ritual.system` with cross-system import from Nerve** — rejected. Sibling-system imports are forbidden by the system-boundary rule; granting an exception for one helper weakens the architecture's clarity. The strict rule wins.
3. **Helper in `nova.core.formatting`** (chosen) — `formatting.py` already houses `format_duration_seconds` (the duration RENDERER). Adding `diff_iso_seconds` (the duration PARSER) keeps both halves of the duration vocabulary in one neutral core module. Both Ritual and Nerve import from `core` — no inter-system coupling.

Option 3 wins because:
- `nova.core.formatting` is the natural home — render and parse are the two halves of a single domain (duration). Splitting them muddles less than housing the parser in a system module.
- No special-case exception in any system's isolation test — the strict "no sibling-system imports" rule stays in force unweakened.
- Public surface (`diff_iso_seconds` is exported via `__all__`); `_parse_iso` stays module-private.
- Future callers (Story 4.3 snapshot dedup, Story 5.1 transparency duration totals) get the helper via the same single import.

### Project Structure Notes

- **Alignment with unified project structure:** All paths align with the architecture.md "Complete Project Directory Structure":
  - No new files. Story 3.7 reshapes existing modules.
  - `MemoryItemInput` lives next to `WorkspaceSnapshotInput` in `systems/brain/models.py` — same input-DTO pattern.
  - `ShutdownState` + `ShutdownViewModel` live in `systems/ritual/models.py` — replacing `ShutdownData`.
- **Detected variances:**
  - Architecture's data flow at architecture.md:715 says "Ritual generates the shutdown ceremony narrative; Brain persists." Story 3.7 implements this with Ritual producing the view model AND Nerve orchestrating the persistence — the architecture line is informal; the actual contract per project-context.md:68 is "Ritual owns ceremony logic; Nerve decides when ceremonies run." Nerve-as-executor preserves the system-boundary contract.
  - `RitualPort.begin_shutdown` originally typed as `() -> ShutdownData`; Story 3.7 reshapes to `(state: ShutdownState) -> ShutdownViewModel`. The reshape is forward-only (zero callers of the old shape). Architecture revision pass (post-Epic 3 retrospective) would update.
- **No conflicts** with existing source structure or naming conventions.

### References

- [Source: epics.md#Story 3.7 (lines 1217-1243)](../planning-artifacts/epics.md#L1217-L1243)
- [Source: architecture.md#Decision 3b (lines 747-755)](../planning-artifacts/architecture.md#L747-L755) — Render Responsibility Boundary
- [Source: architecture.md#Audit Logging Convention (lines 1185-1202)](../planning-artifacts/architecture.md#L1185) — AuditLogger usage from Nerve
- [Source: architecture.md#Persist before emit (line 1037)](../planning-artifacts/architecture.md#L1037) — write-then-emit invariant
- [Source: ux-design-specification.md#Personality Doctrine (line 1033)](../planning-artifacts/ux-design-specification.md#L1033) — `Planted for tomorrow.` listed as canonical T1 confirmation
- [Source: project-context.md (lines 78, 86, 199, 201, 209)](../project-context.md#L78) — persist-before-emit, audit-as-observational, shutdown-routing, success-reflects-completion, NFR4
- Closes [deferred-work.md:231](deferred-work.md#L231)

## Dev Agent Record

### Agent Model Used

claude-opus-4-7[1m]

### Debug Log References

Same-session implementation pass on 2026-05-05. No adversarial review run yet (Task 14 deferred to a separate `/bmad-code-review` invocation per the standard pattern).

### Completion Notes List

* All 33 ACs across 15 groups (A–O) implemented and locked by tests. **Coverage on Story 3.7 surfaces: 100%** on `nova.adapters.rich.skin`, `nova.core.formatting`, `nova.systems.ritual.system`, `nova.systems.ritual.models`. **`nova.systems.nerve.system` at 99.8%** (single residual partial branch is a Story 3.5 SIGBREAK-uninstall edge — pre-existing). **`nova.adapters.sqlite.brain` at 97.2%** (the 2 uncovered lines are pre-existing Story 3.1 corruption-defense guards in `get_last_snapshot_for_session` — not Story 3.7 surface).
* **Atomic three-write commit shipped via new `BrainPort.commit_shutdown` + `ShutdownCommit` DTO.** `engine.transaction()` wraps the sessions UPDATE + memory_items INSERT (when seed entered) + workspace_snapshots INSERT — either all three rows land or none do. The adapter is the single source of truth for `ended_at`/`created_at`/`captured_at`; `ShutdownCommit` carries no timestamp fields. Atomicity rollback verified end-to-end via FOREIGN KEY violation test.
* **Five blockers from the spec-review round honored in the impl + tests:**
  * (1) Atomic three-write `commit_shutdown` (vs. three independent calls colliding with end_session idempotency).
  * (2) Adapter-owned timestamp (vs. caller-stamped drift); `ShutdownCommit` has no timestamp field; cross-row consistency test asserts byte-equal `ended_at` across all three rows.
  * (3) `_collect_seed_with_reprompt` returns `tuple[str | None, _SeedOutcome]` where `_SeedOutcome = Literal["saved", "cancelled", "empty_twice"]` — three mutually-exclusive outcomes; `_classify_audit_outcome` becomes a trivial mapping.
  * (4) `diff_iso_seconds` lives in `nova.core.formatting` (not in Ritual); both Ritual and Nerve import from core. No sibling-system import; nerve isolation guard preserves the strict no-sibling-system rule.
  * (5) Persistence-before-confirmation rephrased — confirmation fires after Brain commit + audit + emission attempted; emission failures are observability-only.
* **`end_session` idempotency guard added** (closes [deferred-work.md:231](deferred-work.md#L231)). UPDATE filtered on `WHERE is_complete = 0`; pre-UPDATE SELECT branches three ways (row missing → WARNING + stamp; row complete → return existing `ended_at`; row incomplete → normal). Verified by 5 idempotency tests + cleanup-path semantics preserved (is_complete=False writes still allowed against is_complete=0 rows).
* **NerveSystem constructor reshape:** new `audit: AuditLogger` keyword-only parameter; composition root passes the existing audit logger. New runtime fields: `_active_mode_apps_launched: tuple[str, ...]` (Story 3.6 _handle_mode_switch extension) + `_session_started_at: str | None` (stamped in startup() step 9 before brain.create_session so duration is computable at shutdown time).
* **Skin/Ritual reshapes:** `SkinPort.render_shutdown_card(view_model: ShutdownViewModel)` (was `SessionSummary`); `RitualPort.begin_shutdown(state: ShutdownState) -> ShutdownViewModel` (was `() -> ShutdownData`); `ShutdownData` retired (zero callers). RichSkinAdapter renders the shutdown card as a yellow-bordered Rich Panel mirroring the briefing's pre-rendered-labels structure.
* **Test counts (final):** 1959 unit pass (+93 vs. Story 3.6 baseline of 1866) + 57 integration pass (+1 new Story 3.7 end-to-end test); 1 pre-existing skip; 1 pre-existing brittle-marker deselect. ruff + ruff format + mypy strict — all clean.
* **Composition root + cli.py:** `cli.py` UNTOUCHED. `app.py` modified by ONE line (passing `audit=audit` to `NerveSystem(...)`).

### File List

**New source files (0):** all changes are extensions to existing modules.

**Modified source files (8):**
* `src/nova/core/formatting.py` — added public `diff_iso_seconds` + private `_parse_iso` helpers.
* `src/nova/systems/brain/models.py` — added `ShutdownCommit` frozen dataclass + `__all__` update.
* `src/nova/ports/brain.py` — added `commit_shutdown` Protocol method; module docstring extension.
* `src/nova/adapters/sqlite/brain.py` — three new SQL constants; refactored `_serialize_snapshot` into thin wrapper around new lower-level `_serialize_workspace_data`; added `commit_shutdown` impl with `engine.transaction()`; reshaped `end_session` for idempotency (pre-UPDATE SELECT + WHERE filter).
* `src/nova/ports/ritual.py` — `begin_shutdown` signature reshape (`() -> ShutdownData` → `(ShutdownState) -> ShutdownViewModel`).
* `src/nova/systems/ritual/models.py` — replaced `ShutdownData` with `ShutdownState` + `ShutdownViewModel`; `__all__` update.
* `src/nova/systems/ritual/system.py` — implemented `RitualSystem.begin_shutdown` body; locked-copy constants `_SHUTDOWN_TITLE` + `_SEED_PROMPT_TEXT`; imports updated.
* `src/nova/ports/skin.py` — `render_shutdown_card` signature reshape; module docstring extension.
* `src/nova/adapters/rich/skin.py` — implemented `render_shutdown_card` body (yellow Panel); imports updated; module docstring extension.
* `src/nova/systems/nerve/system.py` — added `audit: AuditLogger` constructor parameter; new fields `_active_mode_apps_launched` + `_session_started_at`; new module-level `_SeedOutcome` Literal + `_build_session_summary_text` + `_classify_audit_outcome` helpers; reshaped `_handle_shutdown` body wholesale (10-step delegation flow); new `_resolve_active_mode_display_name` + `_collect_seed_with_reprompt` private methods; reshaped `startup()` step 9 for `_session_started_at` stamping; extended `_handle_mode_switch` for `_active_mode_apps_launched` tracking; reshaped `_cleanup_after_repl` for the new field resets.
* `src/nova/app.py` — pass `audit=audit` to `NerveSystem(...)` in `create_app`.

**New test files (2):**
* `tests/unit/systems/ritual/test_shutdown_view_model.py` — 12 tests for `RitualSystem.begin_shutdown` (locked copy, mode/duration/apps labels, omission, escape, trailing-Z, return type).
* `tests/unit/systems/ritual/test_shutdown_models.py` — 11 tests for `ShutdownState`, `ShutdownViewModel`, `ShutdownCommit` (frozen, tuple-not-list, no-timestamp invariant, export contract).

**Modified test files (8):**
* `tests/unit/systems/nerve/test_nerve_system.py` — extended `_make_brain_mock` with `commit_shutdown` + `store_snapshot` AsyncMocks; added `_make_audit_mock` fixture; added `audit` parameter to `_build_nerve_system`; added `_session_started_at` to `_build_session_active_nerve`; reshaped `_make_skin_mock` default inputs; reshaped `_make_ritual_mock` to default-stub `begin_shutdown`; updated 21 existing tests for the new shutdown flow / partial-restore zip-strict / new fields; added 40+ new Story 3.7 tests across 8 blocks (happy path, cancel paths, empty + reprompt, persistence-failure, active-mode integration, startup() reshape, mode-switch apps tracking, AST audit-isolation guard); 2 coverage-completion tests for SeedSaved emit-failure + mode-deleted-defensive branches.
* `tests/unit/systems/nerve/test_nerve_system_isolation.py` — extended allowed imports (`nova.systems.ritual.models`, `nova.core.audit`, `nova.core.formatting`, `nova.ports.hands`); updated `_EXPECTED_NOVA_IMPORTS` parametrize.
* `tests/unit/systems/nerve/test_briefing_assembly.py` — extended `_RecordingFakeBrainPort` with `commit_shutdown` stub method (Protocol satisfaction); added `ShutdownCommit` import.
* `tests/unit/adapters/sqlite/test_brain_adapter.py` — added 8 `commit_shutdown` tests (all-three-rows-atomic, skip-memory-on-no-seed, same-ended-at-across-rows, seed category as string, JSON shape match, no-content logging, returns ISO, rollback on FK violation) + 5 `end_session` idempotency tests (re-call returns existing, WARNING log, normal first call, is_complete=False multi-call, zero-rows WARNING).
* `tests/unit/adapters/rich/test_skin_adapter.py` — added 6 `render_shutdown_card` tests (yellow border + title, mode/duration/apps/prompt rendered, mode-omission, apps-omission, duration-always-present, markup-injection-safe).
* `tests/unit/core/test_formatting.py` — added 6 `diff_iso_seconds` tests (positive seconds, negative-clamps-to-zero, equal-timestamps-zero, trailing-Z, mixed Z+offset, returns int).
* `tests/unit/adapters/rich/test_skin_adapter_isolation.py` — dropped `nova.systems.brain.models` from allowed/expected imports (no longer needed after `SessionSummary` import drop); updated docstring.
* `tests/unit/ports/test_port_isolation.py` — added `commit_shutdown` to BrainPort method ordering tuple.
* `tests/integration/test_session_loop.py` — updated 2 existing tests for new "Cancelled." copy + extended skin inputs to include seed prompt; added 1 new end-to-end test (`test_shutdown_with_seed_persists_session_seed_memory_item_and_snapshot`) that drives the full shutdown-with-seed flow against real adapters and asserts all three rows + audit row land atomically.
* `tests/integration/test_cli_bootstrap.py` — updated `_short_circuit_nerve_repl` autouse fixture to use iter(["shutdown", "skip"]) so the seed prompt cancels cleanly; updated assertion from "Session ended." → "Session ending" + "Cancelled.".

**Modified bmad artifacts (2):**
* `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 3.7 status `ready-for-dev` → `in-progress` → `review`.
* `_bmad-output/implementation-artifacts/deferred-work.md` — closed entry 231 with strike-through and pointer to this story.

### Change Log

* 2026-05-05 — Story 3.7 implementation complete. 1959 unit + 57 integration pass; 100% coverage on every Story 3.7 surface (`nova.adapters.rich.skin`, `nova.core.formatting`, `nova.systems.ritual.{models,system}`); 99.8% / 97.2% on the modified-line regions of `nova.systems.nerve.system` / `nova.adapters.sqlite.brain` (residual gaps are pre-existing). Five blockers from the post-draft review are honored in the implementation: (1) atomic three-write `commit_shutdown` via `engine.transaction()`, (2) adapter-owned timestamp (no caller-stamped drift), (3) seed-helper outcome Literal (replaces ambiguous bool), (4) `diff_iso_seconds` in `nova.core.formatting` (no cross-system helper import), (5) persistence-before-confirmation invariant rephrased to require commit + audit + emission-attempted (not emission-confirmed). Closed deferred-work.md:231 (`end_session` idempotency). Composition root + cli.py untouched modulo one `audit=audit` kwarg pass-through. ruff + ruff format + mypy strict — all clean. Status: in-progress → review.
* 2026-05-05 — **Post-review user-reported HIGH fix.** All three review layers missed: shutdown captured the active mode in the workspace_snapshots row but NOT in `sessions.mode_name`. Startup creates the session with `mode_name=None`; mode switches during the session don't update the column; the original `commit_shutdown` UPDATE didn't set it either. Result: `get_mode_last_used("coding")` (Story 3.2) couldn't find the session at next-startup briefing assembly — `ModeInfo.last_used_at` enrichment broke for any mode that was used in a session. **Fix:** `_UPDATE_SESSION_COMMIT_SHUTDOWN_SQL` now sets `mode_name = ?` alongside `ended_at` / `seed_text` / `summary` / `is_complete`; the call site passes `commit.snapshot_mode_name` (already the canonical stem per Story 3.6 contract). Bare-boot shutdown (no active mode) leaves the column NULL — matches the create_session default. Locked by 3 new tests: `test_commit_shutdown_writes_session_mode_name_for_get_mode_last_used_lookup` (asserts `sessions.mode_name == "coding"` AND `get_mode_last_used("coding")` resolves to the session's `started_at`); `test_commit_shutdown_with_no_active_mode_keeps_session_mode_name_null`; `test_mode_switch_then_shutdown_persists_session_mode_name_and_snapshot_mode` (cross-platform integration test using mocked `Win32HandsAdapter.launch_app` to drive `mode coding` → `shutdown` → seed end-to-end and assert BOTH `sessions.mode_name == "coding"` AND the workspace_snapshot's mode_name field). 2030 unit + integration pass (+3 net); ruff + format + mypy strict clean.
* 2026-05-05 — Formal `/bmad-code-review` pass (3 fresh-context layers — Blind Hunter / Edge Case Hunter / Acceptance Auditor). 67 raw findings → 41 unique post-dedup. **0 decision-needed, 8 patches, 9 deferred, 24 dismissed.** **All 8 patches applied** + locked by new tests: (1) `commit_shutdown` normalizes `seed_text=""` to NULL across both writes (sessions UPDATE + memory_items skip); (2) `_build_session_summary_text` strips + comma-escapes the display name and returns None on empty/whitespace (mirrors Story 3.3 `_escape_label_value`); (3) `_make_brain_mock` gives `commit_shutdown` and `end_session` distinct sentinel timestamps so a method-swap regression trips a test; (4) `_classify_audit_outcome` raises `ValueError` on unknown `_SeedOutcome` member (no silent-default to `RESULT_SKIPPED`); (5) rollback-test `boom` monkeypatch uses explicit kw-only signature instead of `**kwargs`; (6) `render_shutdown_card` test asserts `panel.title.style == "bold yellow"`; (7) stale `fetchone is cheap` comment in `end_session` reworded; (8) `_CANCEL_TERMINATORS` frozenset hoisted to module scope. **Notable triage decisions** (verified before dismissing): the SELECT-then-UPDATE race in `commit_shutdown` is structurally impossible because `engine.transaction()` opens with `BEGIN IMMEDIATE` + acquires `_tx_lock` (verified [src/nova/core/storage/engine.py:282](../../src/nova/core/storage/engine.py#L282)); `Text(text, style=...)` does NOT interpret markup (positional arg is plain text); `events._utc_now_iso()` private import IS the documented codebase pattern; `MemoryCategory` is `StrEnum` so `str()` returns the value reliably. **9 defers logged** with explicit deferred-work.md entries — each carries target story / fix recipe / trigger condition. Final tally: **2027 unit pass + 57 integration pass (+8 net vs. pre-patch baseline of 2019), ruff + format + mypy strict all clean, coverage holds.** Status: review → done.
