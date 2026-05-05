# Story 3.6: Mode Restore & App Launching

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)

**Depends on:**
- Story 1.2 — [src/nova/core/types.py](../../src/nova/core/types.py) (`ActionType.APP_LAUNCH`, `ActionType.MODE_RESTORE`)
- Story 1.3 — [src/nova/core/events.py](../../src/nova/core/events.py) (`AppLaunched`, `ModeRestored`, `EventBus`)
- Story 1.6 — [src/nova/core/config.py](../../src/nova/core/config.py) (`NovaConfig.modes`, `ModeConfig`, `AppConfig` — already validated/frozen)
- Story 1.8 — [src/nova/core/audit.py](../../src/nova/core/audit.py) (`AuditLogger.log_action`, `RESULT_SUCCESS`, `RESULT_FAILED`)
- Story 1.9 — [src/nova/ports/hands.py](../../src/nova/ports/hands.py) (`HandsPort.restore_mode` Protocol stub) and [src/nova/systems/hands/models.py](../../src/nova/systems/hands/models.py) (`ActionRequest`, `ActionResult`)
- Story 3.5 — [src/nova/systems/nerve/system.py:827-832](../../src/nova/systems/nerve/system.py#L827-L832) (`NerveSystem._handle_mode_switch` placeholder body to be replaced) and the SkinPort `render_progress` stub at [src/nova/adapters/rich/skin.py:162-163](../../src/nova/adapters/rich/skin.py#L162-L163)

**Downstream consumers:**
- Story 3.7 (Shutdown Flow) — uses `_active_mode_name` set by `_handle_mode_switch` here for the shutdown summary's "current mode" line; consumes the same `_session_id` / `_session_active` lifecycle wired in 3.5
- Story 3.8 (Warm Resume) — the State C "resume" contextual reply dispatches into `_handle_mode_switch(target=suggested_mode)` shipped here
- Story 3.9 (Status command) — `_handle_status` reads `_active_mode_name` set here
- Story 4.1 (Eyes context capture) — subscribes to `ModeRestored` to trigger workspace-snapshot capture
- Story 6.1 (Window focus & arrange) — extends `HandsPort` with `focus_window` / `arrange_windows`; the `restore_mode` flow built here will then chain `launch → focus → arrange` instead of launch-only
- Story 6.3/6.4 (Mode create/edit) — the wizard writes `modes/<stem>.yaml`; the next `mode <stem>` invocation uses the launcher shipped here
- Story 8.1/8.2 (Tier integration testing) — re-uses the launcher's `tier_manager.tier`-agnostic semantics (mode restore is a local op; never gated on tier)

## Story

As a user,
I want to type `mode coding` and have my configured apps launch automatically with per-app progress feedback (`✓ VS Code` / `✗ Postman (not found — is it installed?)`),
So that I am in my workspace in one command instead of manually opening five apps, and so that a single missing app does not block the rest of the workspace from coming up.

## Story-type classification

**Interaction-boundary story.** This is the first cross-system delegation Nerve → Hands at runtime, the first OS-action surface (subprocess.Popen / os.startfile via the new Win32 adapter), the first per-app event-emission loop, and the first invocation of the `AuditLogger` from a non-Nerve system. The three A6 questions:

1. **New contract between existing pieces?** YES — five new contracts.
   - **`NerveSystem._handle_mode_switch` → `HandsPort.restore_mode`** delegation (replaces the Story 3.5 placeholder body).
   - **`HandsSystem` → `AppLauncherPort`** — a NEW adapter-facing port for the OS-level per-app launch primitive. The Win32 adapter implements it; HandsSystem orchestrates per-mode loops over it. The split is mandatory because `HandsSystem` does business logic (graceful-partial, per-app event emission, audit ordering, render-progress streaming) and per project-context.md:77 *"Adapters may translate, never decide."*
   - **`HandsSystem` → `SkinPort.render_progress`** for inline per-app feedback. The port currently stubs `render_progress(results: Sequence[ActionResult]) -> None`; this story RESHAPES the signature to `render_progress(result: ActionResult) -> None` (single result per call) so Hands streams feedback as each launch lands rather than batching at the end.
   - **`HandsSystem` → `EventBus`** — first non-Nerve emitter of `AppLaunched` (per-app) and `ModeRestored` (once after the loop). Per architecture.md:1037 (write-then-emit) the events fire only after the launch attempt completes (success or failure).
   - **`HandsSystem` → `AuditLogger`** — first non-Nerve caller of `audit.log_action(...)`. Per project-context.md:86 (audit is observational), audit-write failure must NEVER block restore progression or event emission.

2. **New invariants in degraded / partial-failure paths?** YES — six distinct invariants.
   - **Graceful-partial is the default failure pattern** (project-context.md:195). If 2 of 3 apps launch, the session continues with 2; the failed app shows up as `✗ <name> (<reason>)` and the final line distinguishes partial (`"Workspace partially ready. <app> was skipped."`) from full (`"Workspace ready."`) success per project-context.md:190.
   - **Total-failure path is its own bucket.** If ALL apps fail, the final line is `"No apps could be launched. Check mode config: mode edit <stem>"` — distinct from partial AND distinct from full. The session still continues and `_active_mode_name` is still set (the user can manually open the apps).
   - **Audit-write failure NEVER blocks the primary action.** `AuditLogger` already swallows `StorageError` internally (Story 1.8) — but Hands MUST NOT add its own broader try/except that would silently swallow a real bug. The single `await audit.log_action(...)` call site is the boundary; if it raises (non-`StorageError`), that's a programmer error and propagates.
   - **Event emission is per-app AND aggregate.** Per-app `AppLaunched(app_name, executable, success, reason)` fires once per app as each launch attempt completes. `ModeRestored(mode_name, apps_launched, apps_failed)` fires ONCE after the loop. Both follow write-then-emit (the launch attempt is the "write"; the event is the emit).
   - **Tier-gating does NOT apply to mode restore.** Mode restore is a purely-local OS action — no cloud surface. Even in OFFLINE tier, `mode coding` must work normally. Document this scope fence so Epic 7's tier-gate refactor doesn't accidentally pull mode restore through `_tier_check_or_offline_response`.
   - **Unknown mode name → friendly error, no exception.** If `command.target` is not a key in `config.modes`, the response is `"No mode named '<target>'. Try mode to see available modes."` and the verb returns `CommandOutcome.CONTINUE` (REPL continues). NO call to Hands; NO partial state mutation.

3. **Depends on prior-story state?** YES — three areas.
   - **Story 3.5's `_handle_mode_switch` placeholder.** The current body at [src/nova/systems/nerve/system.py:827-832](../../src/nova/systems/nerve/system.py#L827-L832) renders a stub string. This story replaces the body wholesale; the existing test that locks the placeholder string (`test_route_command_dispatches_layer_b_routable` parametrized over `MODE/"coding"` in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py)) MUST be updated to assert the new delegation behavior, NOT deleted.
   - **Story 3.5's `_session_id` / `_session_active` lifecycle.** Mode restore happens MID-SESSION (after `startup` step 9 created the session, before `_handle_shutdown` ends it). The new `_active_mode_name: str | None` field is set on successful restore; cleared on shutdown by Story 3.7. **Story 3.6 owns the field declaration AND the set; Story 3.7 owns the read for the shutdown summary; Story 3.9 owns the read for `status`.**
   - **Story 1.6's mode config validators.** `NovaConfig.modes[stem]` returns a frozen `ModeConfig` whose `apps: tuple[AppConfig, ...]` is already non-empty (the loader rejects modes with zero valid apps — see [src/nova/core/config.py:382-389](../../src/nova/core/config.py#L382-L389)). Hands can rely on `len(mode_config.apps) >= 1` without re-validating. The loader does NOT validate executable paths or whether they exist on PATH — those checks belong to the launcher at use time (Story 3.6 owns them).

**Classification result:** ✅ **Interaction-boundary story.** Apply the FULL invariant sweep (graceful-partial under each failure mode, audit-failure isolation, event-ordering, per-app vs. aggregate test coverage). Apply A9 degraded-path proof in three categories (happy: 3/3 launch; partial: 2/3 launch; total: 0/3 launch). Apply A10 prior-state reconciliation per the per-story table below.

## Depends on prior-story state (A10)

Story 3.6 wires the first cross-system OS-action delegation. Every prior story ships a piece this story consumes or commits against.

### Story 1.2 — `ActionType` enum

| Surface | Story 3.6 reliance |
|---|---|
| [`ActionType.APP_LAUNCH`](../../src/nova/core/types.py#L88) | Written to `audit_log.action_type` for each per-app launch attempt (success or failure). Persists as the literal string `"app_launch"`. |
| [`ActionType.MODE_RESTORE`](../../src/nova/core/types.py#L92) | Written to `audit_log.action_type` for the aggregate-restore audit row that fires once after the per-app loop. Persists as `"mode_restore"`. The aggregate row carries the mode stem in `target` and the per-app result tuples in `details`. |

### Story 1.3 — `EventBus` + typed events

| Surface | Story 3.6 reliance |
|---|---|
| [`AppLaunched(app_name, executable, success, reason)`](../../src/nova/core/events.py#L249-L262) | Emitted per app as each launch attempt completes. `reason` is `None` on success; `"not found"` / `"permission denied"` / `"timed out"` / `"already running"` / `"unknown error"` on failure (see Group D AC #13 for the canonical reason set). |
| [`ModeRestored(mode_name, apps_launched, apps_failed)`](../../src/nova/core/events.py#L230-L246) | Emitted ONCE after the per-app loop completes. `apps_launched` and `apps_failed` are `tuple[str, ...]` (immutable) carrying the `app.name` (display name) of each app — NOT the executable. The display name is what the user wrote in `modes/<stem>.yaml` and is what subscribers (Story 4.1's snapshot trigger, Story 6.1's focus chain) use for cross-referencing. |
| `EventBus.emit` | Per architecture.md:1037 (write-then-emit), each `AppLaunched` is emitted AFTER the launch attempt resolves (success or failure); `ModeRestored` is emitted AFTER the loop is fully drained. `ModeRestored.mode_name` carries the **mode stem** (the YAML-file basename / `config.modes` dict key — kebab-case, stable identity), NOT the user-facing `ModeConfig.name` display label. Subscribers (Story 4.1's snapshot trigger, Story 6.1's focus chain, Story 3.7's shutdown summary) all key on the stem. The `AuditLogger.log_action` call is independent — audit is observational, not part of the write-then-emit ordering. |

### Story 1.6 — `NovaConfig.modes` / `ModeConfig` / `AppConfig`

| Surface | Story 3.6 reliance |
|---|---|
| [`NovaConfig.modes: dict[str, ModeConfig]`](../../src/nova/core/config.py#L206) | Read by `NerveSystem._handle_mode_switch` to look up `command.target`. Missing key → friendly error response, NO call to Hands. |
| [`ModeConfig.apps: tuple[AppConfig, ...]`](../../src/nova/core/config.py#L161) | Iterated by `HandsSystem.restore_mode`. The loader guarantees `len(apps) >= 1` (Story 1.6 dropped modes with zero valid apps). Story 3.6 does NOT re-validate emptiness at the boundary — it asserts via a defensive `assert len(mode_config.apps) >= 1, "loader contract"` on entry. |
| [`AppConfig.name` / `AppConfig.executable` / `AppConfig.args`](../../src/nova/core/config.py#L138-L149) | `name` → display in progress lines + audit `details["app_name"]` + `AppLaunched.app_name`. `executable` → passed to `subprocess.Popen([executable, *args])`. `args` is already validated as `tuple[str, ...]` by the loader. |
| `ModeConfig.urls: tuple[str, ...]` | **Out of scope for Story 3.6.** Epic 6 (Story 6.5) ships URL opening. Story 3.6 launches `mode_config.apps` only; `mode_config.urls` is read and ignored, with a one-time INFO log per restore: `"mode urls present but URL opening lands in Story 6.5"` if `len(urls) > 0`. The deferred-work entry on URL control-char screening ([deferred-work.md:56](deferred-work.md#L56)) is moved forward to Story 6.5 by an explicit bullet update — see Group H AC #25 (third bullet). |
| `ModeConfig.folders: tuple[str, ...]` | **Out of scope.** Per epic spec, folders are for Eyes context awareness in T1, not auto-opened. Story 3.6 ignores the field entirely. |

### Story 1.7 — `TierManager`

| Surface | Story 3.6 reliance |
|---|---|
| [`TierManager.tier`](../../src/nova/core/tiers.py#L170-L178) | **NOT read.** Mode restore is a purely-local OS op — no cloud surface, no degraded-mode branch. Even in OFFLINE tier, `mode coding` must work normally. The `NerveSystem._tier_check_or_offline_response` helper is NOT called from `_handle_mode_switch`. Documented scope fence in `_handle_mode_switch`'s docstring AND in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py) (a positive-lock test asserts `tier_manager.tier` is not consulted during mode restore). |

### Story 1.8 — `AuditLogger`

| Surface | Story 3.6 reliance |
|---|---|
| [`AuditLogger.log_action(action_type, target, result, details)`](../../src/nova/core/audit.py#L144) | Called by `HandsSystem`: per-app row (`ActionType.APP_LAUNCH`, `target=app.name` — the in-mode display identity stays at app level), `result=RESULT_SUCCESS or RESULT_FAILED`, `details={"executable": ..., "reason": ...}`) AND aggregate-restore row (`ActionType.MODE_RESTORE`, `target=mode_stem` — the canonical mode identity, NOT the display label), `result="success" / "partial" / "failed"`, `details={"apps_launched": [...], "apps_failed": [...]}`). Per the audit module's "result" field doc, the loose `str` typing tolerates `"partial"` without signature change. **Note on already-running:** the adapter returns `success=True, reason=None` for already-running apps (see Group A AC #3 step 2 rationale); the per-app audit row reflects that successful outcome. The "this app was a no-op launch" fact is captured in the adapter's DEBUG log, not in the audit row — keeping audit semantics aligned with the user-visible workspace outcome. |
| Audit-write failure swallow | Story 1.8 already catches `StorageError` internally and logs at WARNING — Hands relies on this. Hands does NOT add a wrapping try/except — that would silently absorb non-`StorageError` bugs. The single `await audit.log_action(...)` is the boundary. |
| Audit timestamp = call time | Story 1.8 captures `events._utc_now_iso()` BEFORE the SQLite write so the persisted timestamp records when the action HAPPENED. Hands gets correct ordering for free. |

### Story 1.9 — `HandsPort` + `ActionResult` / `ActionRequest` model

| Surface | Story 3.6 reliance |
|---|---|
| [`HandsPort.restore_mode(mode_config: ModeConfig) -> list[ActionResult]`](../../src/nova/ports/hands.py#L33) | **Reshape (signature change):** the new shape is `restore_mode(mode_stem: str, mode_config: ModeConfig) -> list[ActionResult]`. The stem must be passed explicitly because `ModeConfig` carries only the **display name** (`name: str`, may contain spaces — see [src/nova/core/config.py:160](../../src/nova/core/config.py#L160)) — there is no back-pointer to the YAML stem. Hands needs the stem for the canonical mode identity (audit `target`, `ModeRestored.mode_name`, the `mode edit <stem>` user-facing hint). Update the Protocol's docstring to spell this out. Story 3.6 ships the first concrete implementation (`HandsSystem`). |
| [`ActionResult(action_type, target, success, reason)`](../../src/nova/systems/hands/models.py#L41-L53) | **Reshape (validator added):** `__post_init__` enforces the tri-state invariant `success=True ⇒ reason is None` and `success=False ⇒ reason is not None and reason != ""`. Closes [deferred-work.md:146](deferred-work.md#L146). The validator raises `ValueError` at construction so a programmer-error caller (`ActionResult(success=True, reason="failed")`) fails immediately rather than at the consumer. |
| [`ActionRequest(action_type, target, details)`](../../src/nova/systems/hands/models.py#L27-L38) | **Reshape (immutability):** `__post_init__` wraps `details` in `types.MappingProxyType` (when not None) using `object.__setattr__` to enforce frozen-dataclass-promise at runtime. Closes [deferred-work.md:137](deferred-work.md#L137). `ActionRequest` is not directly used in the Story 3.6 flow (Hands constructs `ActionResult` directly from launch outcomes), but the model file reshape is bundled here because both Hands models live in one file and the linked deferred entries both target Story 3.6. |

### Story 3.5 — `NerveSystem` / `RichSkinAdapter`

| Surface | Story 3.6 reliance |
|---|---|
| [`NerveSystem._handle_mode_switch`](../../src/nova/systems/nerve/system.py#L827-L832) | **Replace body.** Today: renders a stub string. After this story: validates `command.target` exists in `config.modes`, then `await self._hands.restore_mode(mode_config)`, then sets `self._active_mode_name = command.target`. See AC #11 for the full body. |
| `NerveSystem.__init__` constructor | **Reshape:** add `hands: HandsPort` keyword parameter (alphabetical-by-port-stem position, between `event_bus` and `ritual`). Composition root passes the new `HandsSystem` instance. The constructor stays reference-storage only — no I/O additions. |
| `NerveSystem._active_mode_name` field | **NEW field**, declared in `__init__` as `self._active_mode_name: str | None = None`. Set by `_handle_mode_switch` after a successful restore (even partial — partial is still "the mode is active"). Read by Story 3.7's shutdown summary; Story 3.9's status command. Reset to `None` only by `_handle_shutdown` in Story 3.7 (Story 3.6 does NOT reset it on the second-mode-switch case — typing `mode coding` then `mode study` simply overwrites). |
| `NerveSystem._tier_check_or_offline_response` | **NOT called from `_handle_mode_switch`.** Mode restore is local. Documented in the new handler's docstring. |
| [`RichSkinAdapter.render_progress`](../../src/nova/adapters/rich/skin.py#L162-L163) (currently `raise NotImplementedError("Story 3.6 scope")`) | **Implementation lands in this story** with a RESHAPED signature — see Group E AC #16. The `Sequence[ActionResult]` parameter from Story 1.9 was speculative; the epic AC's "per-app progress renders inline as it happens" requires per-app calls. The new signature is `async def render_progress(self, result: ActionResult) -> None`. |
| [`RichSkinAdapter.render_response`](../../src/nova/adapters/rich/skin.py#L168) | Already implemented (Story 3.5). Used by `HandsSystem` for the final-line summary (`"Workspace ready."` etc.). |
| `NerveSystem` test in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py) | The parametrized `test_route_command_dispatches_layer_b_routable` row for `MODE/"coding"` currently asserts the placeholder string. **Update**, do not delete: now assert `hands.restore_mode` was called once with the correct `ModeConfig` AND `_active_mode_name == "coding"` after the call returns. |

### Story 3.5 — composition root posture

| Surface | Story 3.6 reliance |
|---|---|
| [`NovaApp` dataclass](../../src/nova/app.py#L95-L117) | **Reshape:** add `hands: HandsPort` field (positional, alphabetical-by-port-stem between `event_bus`/`audit` block and `ritual`/`skin`/`nerve` block — final order: `config, storage, brain, event_bus, audit, tier_manager, shield, hands, ritual, skin, nerve, close`). |
| [`create_app`](../../src/nova/app.py#L120-L262) | **Reshape:** instantiate `Win32HandsAdapter` (the new `AppLauncherPort` impl), then `HandsSystem` with the launcher + `event_bus` + `skin` + `audit` injected. Pass `hands` into `NerveSystem(...)`. The partial-init cleanup block already covers them — `Win32HandsAdapter.__init__` and `HandsSystem.__init__` are reference-storage only (no I/O, no resources acquired at construction). |

## Acceptance Criteria

### Group A: New `AppLauncherPort` + `Win32HandsAdapter` (OS-level launch primitive)

1. **New port file** [`src/nova/ports/app_launcher.py`](../../src/nova/ports/app_launcher.py) declares `AppLauncherPort` as a `typing.Protocol` (structural subtyping, mirrors the rest of `nova.ports.*`) AND owns the canonical failure-reason vocabulary:

   ```python
   from __future__ import annotations
   from typing import Final, Protocol
   from nova.core.config import AppConfig
   from nova.systems.hands.models import ActionResult


   # Closed failure-reason vocabulary — only emitted when ActionResult.success is False.
   # Already-running is NOT here: it returns success=True per AC #3 step 2 (workspace
   # outcome is "ready" whether we launched or it was already up).
   REASON_NOT_FOUND: Final[str] = "not found"
   REASON_PERMISSION_DENIED: Final[str] = "permission denied"
   REASON_TIMED_OUT: Final[str] = "timed out"
   REASON_UNKNOWN_ERROR: Final[str] = "unknown error"


   class AppLauncherPort(Protocol):
       """Per-app OS-launch primitive consumed by HandsSystem.

       Adapters translate OS-level launch outcomes into typed
       :class:`ActionResult`. Per project-context.md:77 adapters do
       NOT decide policy — graceful-partial, audit ordering, and
       event emission live in :class:`HandsSystem`.
       """

       async def launch_app(self, app: AppConfig) -> ActionResult: ...


   __all__: list[str] = [
       "AppLauncherPort",
       "REASON_NOT_FOUND",
       "REASON_PERMISSION_DENIED",
       "REASON_TIMED_OUT",
       "REASON_UNKNOWN_ERROR",
   ]
   ```

   The module docstring spells out the trust-boundary: only domain types (`AppConfig`, `ActionResult`) cross this port — `subprocess.Popen` / `os.startfile` / `psutil.Process` handles stay trapped in `nova.adapters.win32.actions` per architecture.md:1462.

2. **New adapter file** [`src/nova/adapters/win32/actions.py`](../../src/nova/adapters/win32/actions.py) declares `Win32HandsAdapter`, a concrete `AppLauncherPort` implementation:

   ```python
   class Win32HandsAdapter:
       """Concrete :class:`AppLauncherPort` — subprocess.Popen + os.startfile launches.

       Constructor stores ``timeout_seconds`` (defaults to 5.0). Tests
       inject a smaller value (e.g. 0.05) for the "timed out" path and a
       larger value for the happy path. The launch primitive itself is
       :func:`subprocess.Popen` wrapped in :func:`asyncio.to_thread`; the
       timeout bounds the to_thread call via :func:`asyncio.wait_for`.
       """

       def __init__(self, *, timeout_seconds: float = 5.0) -> None: ...

       async def launch_app(self, app: AppConfig) -> ActionResult: ...
   ```

3. **Launch sequence** inside `Win32HandsAdapter.launch_app`, in this exact order:

   1. **Capture start time.** `start = time.monotonic()` (NOT `time.time()` — wall-clock jumps would corrupt the duration measurement; `monotonic` is immune to NTP slews and DST transitions).
   2. **Already-running pre-check → SUCCESS.** `is_running = await self._is_already_running(app.executable)` via `psutil.process_iter(["name", "exe"])` matching case-insensitively on the executable basename. If True, log at DEBUG `"app already running, skipping launch"` with `extra={"executable": app.executable}`, then return `ActionResult(action_type=ActionType.APP_LAUNCH, target=app.name, success=True, reason=None)`. **Already-running is a successful workspace outcome** — the user wanted "this app available in my workspace", and it already is. Returning `success=False, reason="already running"` would corrupt the partial / total-failure counters: a mode where every configured app happens to already be running would render `"No apps could be launched. Check mode config: mode edit <stem>"` even though the workspace is fully ready. The fact that the launch was a no-op is recorded by the caller (HandsSystem) in the per-app audit row's `details["already_running"] = True` field — visible in the audit trail without polluting the user-facing UX.
   3. **Launch attempt.** Wrap `subprocess.Popen([app.executable, *app.args], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)` in `asyncio.to_thread`. Bound by `asyncio.wait_for(..., timeout=self._timeout_seconds)`.
      - `DETACHED_PROCESS` + `CREATE_NEW_PROCESS_GROUP` are the canonical "launch and forget" flags on Windows: the launched child does NOT share the parent's console (so closing N.O.V.A.'s terminal doesn't kill the launched apps) and does NOT receive the parent's CTRL_C / CTRL_BREAK.
      - `close_fds=True` is the security default per Python 3.7+ — file descriptors don't leak into the child.
      - `Popen` returns immediately once the child process is spawned; the `to_thread` await resolves in milliseconds for a healthy launch. The `wait_for` timeout exists to bound the case where the OS itself stalls (rare; antivirus interference, disk thrash).
   4. **Fallback to `os.startfile` on `FileNotFoundError` — ONLY when `app.args` is empty.** If `Popen` raises `FileNotFoundError` AND `len(app.args) == 0`, retry with `await asyncio.to_thread(os.startfile, app.executable)`. This handles `.lnk` shortcuts and apps registered via Windows app paths (e.g., `chrome` resolves via `App Paths\chrome.exe` registry key). The args-empty gate is load-bearing: `os.startfile`'s `arguments=` parameter (Python 3.10+) has historically inconsistent behavior across Windows versions and shell associations — silently dropping the user's configured args would launch the app in a misleading state ("Chrome opened, but my `--new-window` arg was ignored — why?"). When `app.args` is non-empty and `Popen` fails with `FileNotFoundError`, return the canonical `"not found"` reason directly: the user's executable isn't reachable via PATH, and the args-aware fallback isn't safe enough to attempt. Document this in the adapter docstring as the two-stage launch strategy with the args-gating constraint.
   5. **Error mapping.** Translate caught exceptions to the canonical reason vocabulary (closed set — see AC #13):
      - `FileNotFoundError` (after Popen, AND after os.startfile when applicable) → `REASON_NOT_FOUND` (`"not found"`)
      - `PermissionError` OR `OSError` with `winerror == 5` → `REASON_PERMISSION_DENIED` (`"permission denied"`)
      - `asyncio.TimeoutError` (`TimeoutError` in Python 3.11+) from the outer `wait_for` → `REASON_TIMED_OUT` (`"timed out"`)
      - Any other `OSError` → `REASON_UNKNOWN_ERROR` (`"unknown error"`) (with full exception details logged at WARNING via `logger.warning("launch_app failed", extra={"executable": app.executable, "winerror": getattr(exc, "winerror", None)}, exc_info=True)`)
      - **Never let pywin32-specific or `subprocess`-specific exception classes leak across the port boundary** (project-context.md:40 — adapter exceptions translated at the adapter).
   6. **Success path.** On a successful spawn (Popen returns OR os.startfile returns OR already-running pre-check matched at step 2), compute `elapsed_ms = int((time.monotonic() - start) * 1000)`, log at DEBUG `"app launched"` (or `"app already running, skipping launch"` per step 2) with `extra={"executable": app.executable, "duration_ms": elapsed_ms}`, and return `ActionResult(action_type=ActionType.APP_LAUNCH, target=app.name, success=True, reason=None)`. The caller (HandsSystem) is responsible for distinguishing the already-running case via the per-app audit row's `details["already_running"]` field — the `ActionResult` itself stays a clean success.

4. **Adapter is rendering-agnostic.** `Win32HandsAdapter` MUST NOT import `rich`, `nova.adapters.rich.*`, or any port other than `nova.ports.app_launcher`. Locked by an AST guard in [tests/unit/adapters/win32/test_actions_isolation.py](../../tests/unit/adapters/win32/test_actions_isolation.py) (Group K AC #28).

5. **Adapter is one-app-at-a-time.** No batching, no internal asyncio.gather — `launch_app` handles ONE app and returns. Sequential vs. parallel orchestration is HandsSystem's call (see Group B AC #6).

### Group B: `HandsSystem` — orchestration of the per-mode loop

6. **New module** [`src/nova/systems/hands/system.py`](../../src/nova/systems/hands/system.py) declares `HandsSystem`, a concrete `HandsPort` impl. Constructor signature (keyword-only, mirrors NerveSystem precedent):

   ```python
   class HandsSystem:
       def __init__(
           self,
           *,
           launcher: AppLauncherPort,
           skin: SkinPort,
           event_bus: EventBus,
           audit: AuditLogger,
       ) -> None: ...
   ```

   Constructor stores references only — no I/O, no event subscriptions. `audit: AuditLogger` is the concrete class import (from `nova.core.audit`), NOT a port — `AuditLogger` has no port (it's a cross-cutting service per architecture.md:1187). Other systems that audit (Hands now, Brain in Story 5.2, Nerve in future tier-change paths) all inject the concrete instance directly. This is the existing Story 1.8 pattern — do NOT introduce an `AuditPort` for Story 3.6.

7. **`HandsSystem.restore_mode(mode_stem: str, mode_config: ModeConfig) -> list[ActionResult]`** is the primary public method (the `HandsPort` Protocol satisfaction). The `mode_stem` is the canonical mode identity (the YAML file basename / `config.modes` dict key — kebab-case, stable). Body in this exact ordering — every step is a separate `await` so cancellation lands at a clean boundary:

   1. **Defensive precondition.** `assert len(mode_config.apps) >= 1, "loader contract: ModeConfig.apps is non-empty"` — locks the Story 1.6 invariant. The assertion message documents the reliance. Also assert `mode_stem`: `assert mode_stem and not mode_stem.isspace(), "mode_stem is required for canonical mode identity"`.
   2. **URL-deferral notice.** If `len(mode_config.urls) > 0`, log at INFO once: `logger.info("mode urls present but URL opening lands in Story 6.5", extra={"mode_stem": mode_stem, "url_count": len(mode_config.urls)})`. URL count only; never the URLs themselves (avoids any surprise leak of user-typed URLs into the log file). Use `mode_stem` (stable identity) in the log extras, NOT `mode_config.name` (display label).
   3. **Initialize accumulators.** `results: list[ActionResult] = []` and `apps_launched: list[str] = []`, `apps_failed: list[str] = []`.
   4. **Sequential per-app loop.** For each `app in mode_config.apps`:
      - `result = await self._launcher.launch_app(app)` — single launch attempt.
      - `results.append(result)`.
      - **Audit log per app** (BEFORE event emission, BEFORE render): `await self._audit.log_action(action_type=ActionType.APP_LAUNCH, target=app.name, result=RESULT_SUCCESS if result.success else RESULT_FAILED, details={"executable": app.executable, "reason": result.reason})`. Audit is observational — Story 1.8 swallows `StorageError` internally; Hands does NOT wrap this in a try/except (see Group F AC #19 for the contract clarification and the test pattern that uses a real AuditLogger over a failing storage engine).
      - **Per-app render** (BEFORE event emission so the user sees the line as it happens, not after the event handler chain finishes): `await self._skin.render_progress(result)`.
      - **Per-app event** (AFTER the audit + render so write-then-emit holds against the audit row too — though audit failure is swallowed inside AuditLogger, the ordering is consistent): `await self._event_bus.emit(AppLaunched(app_name=app.name, executable=app.executable, success=result.success, reason=result.reason))`.
      - Append to the appropriate accumulator: `apps_launched.append(app.name)` if `result.success` else `apps_failed.append(app.name)`.
   5. **Aggregate audit row** AFTER the loop: `await self._audit.log_action(action_type=ActionType.MODE_RESTORE, target=mode_stem, result=_aggregate_result(apps_launched, apps_failed), details={"apps_launched": list(apps_launched), "apps_failed": list(apps_failed)})`. **`target=mode_stem`** — the canonical identity, NOT `mode_config.name`. The `_aggregate_result` helper returns `RESULT_SUCCESS` when `not apps_failed`, `_RESULT_PARTIAL` when both lists non-empty, `RESULT_FAILED` when `not apps_launched` (all failed). `_RESULT_PARTIAL` is a module-level constant `_RESULT_PARTIAL = "partial"` in `hands/system.py` per project-context.md:131. Audit's `result` field is loose-typed `str` (Story 1.8's deliberate design — see [src/nova/core/audit.py:91-110](../../src/nova/core/audit.py#L91-L110)) so no signature widening is needed.
   6. **Aggregate `ModeRestored` event** ONCE: `await self._event_bus.emit(ModeRestored(mode_name=mode_stem, apps_launched=tuple(apps_launched), apps_failed=tuple(apps_failed)))`. **`mode_name=mode_stem`** — the field is named `mode_name` for historical reasons (Story 1.3 declared the event before this story tightened the stem-vs-display distinction), but its value is the stem. Subscribers (Story 4.1, Story 6.1) key on the stem. The `tuple(...)` conversion respects the event's `tuple[str, ...]` typing per [src/nova/core/events.py:230-246](../../src/nova/core/events.py#L230-L246).
   7. **Final-line summary** via `await self._skin.render_response(_summary_text(mode_stem, apps_launched, apps_failed))`. The `_summary_text` helper takes the stem (used only for the `mode edit <stem>` hint — the rest of the copy is mode-name-free). It returns:
      - `"Workspace ready."` when `not apps_failed`.
      - `"Workspace partially ready. {first_failed} was skipped."` when both lists non-empty (use the FIRST failed app's display name; if multiple failed, the audit row carries the full list — the user-facing line stays brief per project-context.md:183 "Brevity by default"). When `len(apps_failed) > 1`, the helper appends `f" ({len(apps_failed) - 1} more skipped — see status for details.)"` so the user knows there's more behind the headline.
      - `f"No apps could be launched. Check mode config: mode edit {mode_stem}"` when `not apps_launched`. The `mode edit <stem>` form matches what the user types — the stem is what `mode edit <X>` resolves against (Story 6.4), so the hint must use the stem identity, not the display label.
   8. **Return** the `results` list.

8. **Sequential, NOT parallel.** Per-app launches happen in `mode_config.apps` order, one at a time. Reasons:
   - The user sees the progress lines in the order they wrote the apps in the YAML — predictable, scannable.
   - `subprocess.Popen` with antivirus interference can briefly stall; serializing avoids hammering the OS with three concurrent launches that all hit the same I/O path.
   - The NFR1 budget (< 30 seconds for the full restore) is not threatened: a typical 3-app launch sequentially completes in 1–3 seconds; the 5s per-app timeout caps the worst case at `5 * len(apps)` which for the typical 3–6 app modes stays under 30s. If a future mode with 10+ apps regularly approaches the budget, parallelism becomes worth the complexity cost; T1 sticks with sequential.

9. **Voice deferral.** The final-line summary (`"Workspace ready."` etc.) is operational copy, NOT personality-bearing prose, per project-context.md:66 + the UX spec line 591. Story 3.6 routes it through `skin.render_response` directly (no Voice involvement). Epic 7 may later add a Voice-generated dressing on top (e.g., `"Workspace ready. Last thread: auth tests."` per architecture.md:383); the canonical brief form lives in Hands and Voice prepends to it. Document the deferral in `_summary_text`'s docstring + a scope-fence comment.

### Group C: Render-progress port reshape

10. **Reshape** [`src/nova/ports/skin.py`](../../src/nova/ports/skin.py) — `SkinPort.render_progress` signature changes from `Sequence[ActionResult] -> None` to `ActionResult -> None`:

    ```python
    async def render_progress(self, result: ActionResult) -> None: ...
    ```

    The module docstring's reference to "Progress (mode restore, Story 3.6)" updates to spell out the per-call semantics: *"Story 3.6 reshapes render_progress from `Sequence[ActionResult]` to a single `ActionResult` per call so HandsSystem streams per-app feedback inline rather than batching at the end. The Sequence form was speculative (Story 1.9 stub); single-result is what the epic AC requires (`✓ VS Code` / `✗ Postman` lines render as each launch lands)."*

    Update the unused `Sequence` import in `nova/ports/skin.py` if it becomes unreferenced after the reshape.

11. **Replace `NerveSystem._handle_mode_switch` body** at [src/nova/systems/nerve/system.py:827-832](../../src/nova/systems/nerve/system.py#L827-L832):

    ```python
    async def _handle_mode_switch(self, command: Command) -> CommandOutcome:
        """Delegate to HandsPort.restore_mode for the user-named mode.

        Mode restore is purely-local (no cloud surface) — does NOT
        consult ``_tier_check_or_offline_response``. Even in OFFLINE
        tier, ``mode coding`` works. Documented scope fence; locked by
        ``test_mode_switch_does_not_consult_tier_manager``.

        ``command.target`` IS the canonical mode stem — the Story 3.4
        parser produces lowercased identifiers from the user's input
        (``mode coding`` → ``target="coding"``), and ``config.modes``
        is keyed by stem. Pass the stem explicitly into Hands so
        downstream identity (``ModeRestored.mode_name``, audit
        ``target``, ``mode edit <stem>`` hints) is stable regardless
        of the user-facing display label in ``ModeConfig.name``.

        Tracks the active mode in ``_active_mode_name`` (the stem) for
        Story 3.7's shutdown summary and Story 3.9's status command.
        Set on successful restore (even partial — partial is still
        "active"); Story 3.7 owns the reset to None on shutdown.
        """
        assert command.target is not None  # parser guarantees for MODE/<target>
        mode_stem = command.target
        mode_config = self._config.modes.get(mode_stem)
        if mode_config is None:
            await self._skin.render_response(
                f"No mode named '{mode_stem}'. Try mode to see available modes."
            )
            return CommandOutcome.CONTINUE
        await self._hands.restore_mode(mode_stem, mode_config)
        self._active_mode_name = mode_stem
        return CommandOutcome.CONTINUE
    ```

    The `assert command.target is not None` documents the Story 3.4 parser contract: `MODE` with `target=None` routes to `_handle_modes_list` (the bare `mode` / `modes` form); `MODE` with `target=str` routes here. The route_command match arm at [src/nova/systems/nerve/system.py:371-374](../../src/nova/systems/nerve/system.py#L371-L374) already enforces this branch. **Naming note:** the field is called `_active_mode_name` for grep-continuity with the existing `mode_name` parameter on `BrainPort.create_session` and the `ModeRestored.mode_name` event field, but its value is always the stem.

12. **`NerveSystem.__init__` adds `hands: HandsPort`** keyword parameter and `_active_mode_name: str | None = None` field:

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
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        ...
        self._hands = hands
        ...
        self._active_mode_name: str | None = None  # set by _handle_mode_switch; reset by Story 3.7
    ```

    Position in the constructor: `hands` is keyword-only (the whole constructor is keyword-only), so position is documentation only. Sort alphabetically among the system-port references for readability: `brain, hands, ritual, skin` — but the existing constructor sorts by type-stem grouping (brain/ritual/skin then event_bus/tier_manager/config). Keep the existing pattern: `brain, ritual, skin, event_bus, tier_manager, config, hands` (hands appended after `config` keeps the diff minimal and matches the chronological order in which systems land in the codebase).

### Group D: Per-app launch result vocabulary

13. **Canonical reason strings** for `ActionResult.reason` are exactly four members (all failure-only — `success=True` always pairs with `reason=None`, including the already-running case per AC #3 step 2):
    - `None` — only when `success=True` (covers fresh launch AND already-running)
    - `"not found"` — `FileNotFoundError` after Popen (and after os.startfile when args is empty)
    - `"permission denied"` — `PermissionError` OR `OSError(winerror=5)`
    - `"timed out"` — outer `wait_for` `TimeoutError`
    - `"unknown error"` — any other `OSError` (catch-all; full traceback in WARNING log)

    Declare in [`src/nova/ports/app_launcher.py`](../../src/nova/ports/app_launcher.py) (per AC #15 — the constants live in the port file, not the adapter, so `RichSkinAdapter` can import the `REASON_NOT_FOUND` constant for the `"is it installed?"` hint without crossing into a sibling adapter):

    ```python
    REASON_NOT_FOUND: Final[str] = "not found"
    REASON_PERMISSION_DENIED: Final[str] = "permission denied"
    REASON_TIMED_OUT: Final[str] = "timed out"
    REASON_UNKNOWN_ERROR: Final[str] = "unknown error"
    ```

    **No `REASON_ALREADY_RUNNING` constant.** Already-running is a successful workspace outcome (AC #3 step 2), not a failure reason. The adapter logs the no-op-launch fact at DEBUG; the public reason vocabulary stays a closed four-member failure-only set. Per project-context.md:131 ("no magic literals for domain concepts"). Tests reference the constants, NOT the string literals — a future rename to `"app not found"` (etc.) for clarity stays a one-line change.

14. **Reason string is the user-facing line.** The progress renderer formats it as `f"✗ {result.target} ({result.reason})"` for failure (the Win32 reason vocabulary IS the user-visible line — no translation layer). For `REASON_NOT_FOUND` the spec adds extra context per the UX spec line 698 (`"not found — is it installed?"`); the renderer applies that extra hint inline:

    ```python
    if result.reason == REASON_NOT_FOUND:
        line = f"✗ {result.target} ({result.reason} — is it installed?)"
    else:
        line = f"✗ {result.target} ({result.reason})"
    ```

    Success (including already-running) renders as `f"✓ {result.target}"` — same checkmark either way. The user perceives the workspace outcome ("this app is now ready"), not the implementation detail of whether it had to be launched. Locked at the renderer (Group E AC #16), NOT at the adapter — keeps the adapter rendering-agnostic. (The adapter's `result.reason` stays the canonical four-member vocabulary; the user-facing hint is presentation logic.)

### Group E: `RichSkinAdapter.render_progress` implementation

15. **Replace the `NotImplementedError` body** at [src/nova/adapters/rich/skin.py:162-163](../../src/nova/adapters/rich/skin.py#L162-L163). New signature + body:

    ```python
    async def render_progress(self, result: ActionResult) -> None:
        """Render a single per-app launch result inline (Story 3.6).

        The line shape:
          ``✓ {result.target}`` on success.
          ``✗ {result.target} ({result.reason})`` on failure, with
          ``"not found"`` extended to ``"(not found — is it installed?)"``
          per UX spec line 698.

        Operational output per project-context.md:66 — direct to Skin,
        no Voice. ``markup=False`` is critical for the same reason as
        ``render_response``: Rich's default markup parsing would
        interpret ``[`` / ``]`` in app names or reasons. The
        ``asyncio.to_thread`` wrap mirrors ``render_response`` /
        ``render_briefing_card`` for Rich's blocking I/O.
        """
        if result.success:
            line = f"✓ {result.target}"
        elif result.reason == REASON_NOT_FOUND:
            line = f"✗ {result.target} ({result.reason} — is it installed?)"
        else:
            line = f"✗ {result.target} ({result.reason})"
        await asyncio.to_thread(self._console.print, line, markup=False)
    ```

    Import added: `from nova.ports.app_launcher import REASON_NOT_FOUND`. Both `Win32HandsAdapter` (writes the constants) and `RichSkinAdapter` (reads `REASON_NOT_FOUND` for the `"is it installed?"` hint branch) import from `nova.ports.app_launcher` — the port file is the canonical reason-vocabulary owner. This sidesteps the no-cross-adapter rule (project-context.md:62 — adapters depend on ports, not other adapters); same pattern as `nova.core.audit.RESULT_SUCCESS` living at the port-of-record for the audit-result vocabulary.

16. **Update the `RichSkinAdapter` module docstring** to spell out the per-call render-progress semantics (`render_progress(result)` is per-app, NOT batched) and the `markup=False` security rationale.

### Group F: Audit ordering invariants

17. **Per-app audit row fires BEFORE the per-app event AND BEFORE the next app's launch.** This means:
    - For a 3-app mode where app 2 fails:
      - app 1 launch → app 1 audit row → app 1 render → app 1 `AppLaunched` event → app 2 launch → app 2 audit row → app 2 render → app 2 `AppLaunched` event → app 3 launch → ...
    - The audit row records the attempt regardless of success. Audit is the durable trail; events are runtime fan-out.
    - Locked by a mock-based ordering test in [tests/unit/systems/hands/test_hands_system.py](../../tests/unit/systems/hands/test_hands_system.py) using `MagicMock.method_calls` across the audit/event_bus/skin mocks (see Group K AC #29 — Block 2 ordering test).

18. **Aggregate `MODE_RESTORE` audit row fires AFTER the per-app loop but BEFORE the `ModeRestored` event AND BEFORE the final-line render.** Order: `(per-app loop) → MODE_RESTORE audit → ModeRestored event → render_response("Workspace ready.")`. Rationale: the audit row IS the durable record of "the whole restore attempt happened with these outcomes." Emitting `ModeRestored` after audit gives subscribers (Story 4.1's snapshot trigger) a chance to query the audit log and see the full trail.

19. **Audit-write failure must NOT block emission, render, or return.** The contract is layered:

    - **AuditLogger swallows `StorageError` internally** (Story 1.8 — see [src/nova/core/audit.py:286-322](../../src/nova/core/audit.py#L286-L322)). The `await audit.log_action(...)` call simply returns `None` when the underlying storage write fails — Hands sees no exception and proceeds.
    - **HandsSystem does NOT wrap `audit.log_action` in a try/except.** A wrapping `except Exception` would also catch programmer errors (`TypeError` from a bad `details` payload, `ValueError` from an empty `result` string — both raised by `AuditLogger`'s own boundary checks). Those bugs MUST surface. The unwrapped `await self._audit.log_action(...)` IS the contract.

    **Test pattern (locks the layered contract correctly).** The Block 5 tests at AC #29 instantiate a **real** `AuditLogger` wired to a **failing storage engine** (a tiny in-test `_FailingStorageEngine` that raises `StorageError` on every `execute`). This exercises the real swallow path inside `AuditLogger`; HandsSystem stays unwrapped; the test asserts that all per-app events still fire, `render_progress` runs for every app, `ModeRestored` emits, and the `results` list comes back complete. **Patching `audit.log_action` directly to raise** would create a contradiction (the spec forbids HandsSystem from catching, so a raise would propagate) — the failing-storage pattern is the only way to test the swallow without breaking the contract. See AC #29 Block 5 for the implementation shape.

### Group G: Composition root + cli.py wiring

20. [`src/nova/app.py`](../../src/nova/app.py) — extend `NovaApp` and `create_app`:

    - **`NovaApp` field added:** `hands: HandsPort` (positional, between `shield` and `ritual` per the alphabetical-by-port-stem block layout the codebase has settled into). Final field order: `config, storage, brain, event_bus, audit, tier_manager, shield, hands, ritual, skin, nerve, close`.
    - **`create_app` instantiates** the new launcher + system AFTER `audit` is wired and BEFORE `ritual`:

      ```python
      from nova.adapters.win32.actions import Win32HandsAdapter
      from nova.ports.app_launcher import AppLauncherPort
      from nova.systems.hands.system import HandsSystem
      ...
      launcher: AppLauncherPort = Win32HandsAdapter()
      logger.info("app launcher adapter wired", extra={"adapter": type(launcher).__name__})

      hands: HandsPort = HandsSystem(
          launcher=launcher,
          skin=skin,  # already wired above
          event_bus=event_bus,
          audit=audit,
      )
      logger.info("hands system wired", extra={"system": type(hands).__name__})
      ```

      But `skin` is wired AFTER `ritual` today (Story 3.3 ordering at [src/nova/app.py:215-219](../../src/nova/app.py#L215-L219)). **Resolution:** move `skin` instantiation up to BEFORE `hands` — the only constraint is that `nerve` must come last (it depends on all of brain/ritual/skin/hands). New order in `create_app`: `storage → brain → event_bus → audit → tier_manager → shield → skin → launcher → hands → ritual → nerve`. Update the partial-init cleanup comment block to reflect the new order. The reordering is tested by `test_composition_root_construction_order` (Group K AC #29).

    - **Pass `hands` into `NerveSystem`:**

      ```python
      nerve: NervePort = NerveSystem(
          brain=brain,
          ritual=ritual,
          skin=skin,
          event_bus=event_bus,
          tier_manager=tier_manager,
          config=config,
          hands=hands,
      )
      ```

    - The existing partial-init `try / except BaseException` block continues to cover the new instantiations. `Win32HandsAdapter.__init__` is reference-storage (stores `timeout_seconds`, no I/O), `HandsSystem.__init__` is reference-storage (stores port refs only). Zero new failure modes added to the cleanup path.

21. **Composition-root regression tests** at [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) (existing file — append, do not replace):
    - `test_win32_hands_adapter_is_instantiated_inside_create_app` — patches `nova.app.Win32HandsAdapter` to a `MagicMock`; assert `create_app` called it exactly once.
    - `test_hands_system_is_instantiated_inside_create_app` — patches `nova.app.HandsSystem`; assert called exactly once with `launcher`/`skin`/`event_bus`/`audit` kwargs.
    - `test_nerve_system_receives_hands_port` — patches `nova.app.NerveSystem`; assert `hands=...` kwarg present in the call args.
    - `test_only_app_and_cli_import_adapters` — already exists; verify (no code change) that it still passes after the new `Win32HandsAdapter` import lands. The test gate covers `nova.adapters.win32` already implicitly (the pattern is package-prefix).

22. **`cli.py` is UNTOUCHED.** Story 3.6 ships no new CLI flags, no new subcommands, no new exit codes. The `await app.nerve.startup()` call already drives the REPL into `_handle_mode_switch` via `route_command` — no orchestration change at the cli boundary.

### Group H: Hands models reshape (closes deferred-work)

23. **`ActionResult.__post_init__` validator** in [src/nova/systems/hands/models.py](../../src/nova/systems/hands/models.py):

    ```python
    @dataclass(frozen=True)
    class ActionResult:
        action_type: ActionType
        target: str
        success: bool
        reason: str | None

        def __post_init__(self) -> None:
            if self.success and self.reason is not None:
                raise ValueError("ActionResult: success=True requires reason=None")
            if not self.success and (self.reason is None or not self.reason):
                raise ValueError("ActionResult: success=False requires non-empty reason")
    ```

    Closes [deferred-work.md:146](deferred-work.md#L146). The error messages name `ActionResult` explicitly so test failures point at the dataclass, not the call site.

24. **`ActionRequest.__post_init__` validator + frozen-mapping wrap** in the same file:

    ```python
    from types import MappingProxyType

    @dataclass(frozen=True)
    class ActionRequest:
        action_type: ActionType
        target: str | None
        details: Mapping[str, object] | None

        def __post_init__(self) -> None:
            if self.details is not None and not isinstance(self.details, MappingProxyType):
                # Wrap caller-supplied dict to enforce frozen-promise at runtime.
                # Use object.__setattr__ to bypass the frozen-dataclass restriction
                # (legitimate per the Python dataclasses docs for __post_init__
                # field rewrites on frozen instances).
                object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
    ```

    The `dict(self.details)` defensive copy is critical — wrapping the caller's dict directly with `MappingProxyType` would still let the caller mutate via the original reference. The copy isolates the proxy.

    Closes [deferred-work.md:137](deferred-work.md#L137). `ActionRequest` is not used in the Story 3.6 hot path (Hands constructs `ActionResult` directly), but the reshape is bundled here because both deferred entries target Story 3.6 and both models live in one file.

25. **Update `deferred-work.md`** at story completion (Dev sets `Status: review`):
    - Strike-through line 137 (`ActionRequest.details` MappingProxyType): `**Closed by Story 3.6 (<date>).** Frozen-promise enforced via __post_init__ + MappingProxyType(dict(...)) defensive copy. See [3-6-mode-restore-and-app-launching.md](3-6-mode-restore-and-app-launching.md).`
    - Strike-through line 146 (`ActionResult` tri-state validator): `**Closed by Story 3.6 (<date>).** __post_init__ enforces success=True⇒reason=None and success=False⇒non-empty reason. See [3-6-mode-restore-and-app-launching.md](3-6-mode-restore-and-app-launching.md).`
    - **Update line 56** (URL control-char screening) from `Target: Story 3.6` to `Target: Story 6.5 (URL opening)` — Story 3.6 is launch-only (no URLs); the use site for URLs is the future story that actually opens them.

### Group I: Performance budget (NFR1)

26. **Workspace restore completes in under 30 seconds** for any T1-realistic mode (1–6 apps). Per-app launch timeout caps at 5 seconds (the `Win32HandsAdapter._timeout_seconds` default — see AC #2). Sequential 6-app launch worst case: `6 × 5 = 30s` if every app times out — acceptable as the absolute ceiling. Typical case (3 apps, all launch in <500ms each) completes in <2s.

    The NFR is implicitly tested by the integration test at AC #31 (Group K) — if the test ever exceeds 30s wall clock, the test framework's default timeout fails it. **No explicit `time.monotonic()` budget assertion in unit tests** — that would be flaky (CI runners vary). The integration test's wall-clock is the only enforcement.

### Group J: AST isolation locks

27. **New file** [`tests/unit/systems/hands/test_hands_system_isolation.py`](../../tests/unit/systems/hands/test_hands_system_isolation.py) — AST-walks `nova.systems.hands.system` and asserts:

    - **Forbidden top-level imports:** `sqlite3`, `anthropic`, `subprocess`, `pywin32`, `pywintypes`, `psutil`, `win32api`, `win32gui`, `win32com`, `win32con`, `rich`, `yaml`, `os` (the system never spawns processes itself; the launcher does it). The forbidden `subprocess` and `os` blocks are the strongest signal that the system stays orchestration-only.
    - **Forbidden Nova prefixes:** `nova.adapters`, `nova.app`, `nova.cli`, `nova.setup`. Allowed: `nova.core.*` (audit, config, events, types), `nova.ports.{app_launcher,skin}`, `nova.systems.hands.models`. Sibling-system `.system` modules are FORBIDDEN.
    - **Positive locks:** parametrize over `["nova.core.audit", "nova.core.events", "nova.core.types", "nova.ports.app_launcher", "nova.ports.skin", "nova.systems.hands.models"]` and assert each is present.
    - Mirrors the [test_nerve_system_isolation.py](../../tests/unit/systems/nerve/test_nerve_system_isolation.py) shape.

28. **New file** [`tests/unit/adapters/win32/test_actions_isolation.py`](../../tests/unit/adapters/win32/test_actions_isolation.py) — AST-walks `nova.adapters.win32.actions` and asserts:

    - **Forbidden top-level imports:** `rich`, `nova.adapters.rich`, `nova.adapters.sqlite`, `nova.adapters.shield`, `nova.adapters.claude` (no cross-adapter imports per project-context.md:62), `nova.systems.*.system` (adapters don't reach into system internals), `nova.app`, `nova.cli`.
    - **Allowed:** stdlib (`subprocess`, `os`, `sys`, `asyncio`, `time`, `logging`), `psutil` (for the already-running pre-check), `nova.core.config` (for `AppConfig`), `nova.core.types` (for `ActionType`), `nova.ports.app_launcher` (the port + reason constants), `nova.systems.hands.models` (for `ActionResult` — adapter→model is allowed per project-context.md:62 since models are part of the system's published cross-boundary surface).
    - **Positive locks:** parametrize over the allowed set; assert each is present.

### Group K: Tests

29. **`tests/unit/systems/hands/test_hands_system.py`** (new file — primary unit-test surface for HandsSystem). Use `pytest.mark.asyncio` on every test; use mocks for `AppLauncherPort` / `SkinPort` / `EventBus` / `AuditLogger`. Test layout:

    **Block 1 — Constructor (AC #6):**
    - `test_constructor_is_reference_storage_only` — instantiate with mocks; assert no method calls land on any mock.
    - `test_constructor_keyword_only_signature` — positional construction raises `TypeError`.

    **Block 2 — Happy path (AC #7 — all apps succeed):**
    - `test_restore_mode_launches_each_app_in_order` — 3-app mode; call signature `restore_mode(mode_stem="coding", mode_config=...)`; assert `launcher.launch_app` called 3 times in app-list order. Assert `results` list returned with `len==3`, all `success=True`.
    - `test_restore_mode_per_app_audit_then_render_then_event_ordering` — `MagicMock.method_calls` across the audit/skin/event_bus mocks proves: for each app, audit row → render → event, in that order, before the next app's launch begins.
    - `test_restore_mode_aggregate_audit_after_per_app_loop_before_mode_restored_event` — aggregate `MODE_RESTORE` audit appears AFTER the last per-app audit AND BEFORE the `ModeRestored` event emission.
    - `test_restore_mode_final_render_response_is_workspace_ready` — happy path emits exactly `"Workspace ready."` via `render_response`.
    - `test_restore_mode_aggregate_audit_result_is_success_when_all_launched` — assert the `result` kwarg passed to `audit.log_action` for the `MODE_RESTORE` row is `RESULT_SUCCESS`.
    - `test_restore_mode_aggregate_audit_target_is_mode_stem_not_display_name` — `mode_stem="study-group"`, `mode_config.name="Study Group"`; assert the `MODE_RESTORE` audit row's `target` kwarg is `"study-group"`, NOT `"Study Group"`. Locks the canonical-stem identity invariant.
    - `test_restore_mode_emits_mode_restored_with_stem_as_mode_name` — same fixture; assert `ModeRestored.mode_name == "study-group"`.
    - `test_restore_mode_emits_mode_restored_with_full_apps_launched_tuple` — assert the `ModeRestored.apps_launched` tuple contains every app's `name` in order; `apps_failed` is the empty tuple.
    - `test_restore_mode_treats_already_running_as_success` — launcher mock returns 3 results all with `success=True, reason=None` (representing a mix of fresh launches and already-running outcomes — indistinguishable at the HandsSystem boundary); assert final line is `"Workspace ready."`, aggregate audit `result=RESULT_SUCCESS`, `ModeRestored.apps_failed == ()`. Locks the AC #3 step 2 contract end-to-end through Hands.

    **Block 3 — Partial path (AC #7 — some apps fail):**
    - `test_restore_mode_partial_2_of_3_succeed_continues` — launcher mock returns success/failure/success in sequence; assert all 3 launches were attempted (failure does NOT abort), assert `results[0].success and not results[1].success and results[2].success`.
    - `test_restore_mode_partial_final_line_names_first_failure` — `"Workspace partially ready. Postman was skipped."` (Postman is the first failure).
    - `test_restore_mode_partial_final_line_appends_count_when_multiple_fail` — 4-app mode where 3 fail; assert `"Workspace partially ready. <first> was skipped. (2 more skipped — see status for details.)"`.
    - `test_restore_mode_partial_aggregate_audit_result_is_partial` — assert `result="partial"` on the `MODE_RESTORE` audit row.
    - `test_restore_mode_partial_emits_mode_restored_with_split_tuples` — `ModeRestored.apps_launched` and `apps_failed` partition correctly.

    **Block 4 — Total-failure path (AC #7 — every app fails):**
    - `test_restore_mode_total_failure_final_line_includes_mode_edit_stem_hint` — `mode_stem="coding"`, `mode_config.name="Coding"`; final line is `"No apps could be launched. Check mode config: mode edit coding"` (lowercased stem, NOT the display label `"Coding"`). Locks the AC #7 step 7 stem-in-hint contract.
    - `test_restore_mode_total_failure_aggregate_audit_result_is_failed` — assert `result=RESULT_FAILED` on the aggregate row.
    - `test_restore_mode_total_failure_still_emits_mode_restored_event` — even with zero successful launches, `ModeRestored` fires once with the full failed-list tuple AND `mode_name == mode_stem`.

    **Block 5 — Audit-failure isolation (AC #19) — uses real `AuditLogger` over a failing storage engine:**

    Test fixture (in-file, NOT a module-level conftest entry — keep the failure-injection plumbing local to this test block):

    ```python
    class _FailingStorageEngine:
        """Minimal SqliteStorageEngine stand-in that raises StorageError on every execute.

        Mirrors only the surface AuditLogger touches (``execute(sql, params)``).
        Used to exercise AuditLogger's internal StorageError-swallow path
        without patching audit.log_action directly (which would contradict
        the AC #19 "Hands does not wrap" contract).
        """

        async def execute(self, sql: str, params: tuple[object, ...]) -> None:
            del sql, params
            raise StorageError("simulated storage failure")
    ```

    - `test_restore_mode_continues_when_audit_storage_fails_for_every_call` — construct `audit = AuditLogger(storage=_FailingStorageEngine())`; pass into `HandsSystem(...)`; run `restore_mode("coding", mode_config_with_3_apps)`; assert all 3 per-app launches happened (mock `launcher.launch_app.call_count == 3`), all 3 `AppLaunched` events fired, `render_progress` called 3 times, `ModeRestored` emitted once, `render_response("Workspace ready.")` called, and the returned `results` list has length 3. The `caplog` fixture captures `AuditLogger`'s WARNING-level swallow logs (one per failed audit row, both per-app + aggregate).
    - `test_restore_mode_does_not_wrap_audit_log_action_in_try_except` — AST-walk the `restore_mode` source: walk every `ast.Try` node, assert that none of their `body` blocks contain a call to `self._audit.log_action`. (Closes the "is the contract actually unwrapped?" question via static analysis.) Mirrors the AST-style guards the codebase uses for the audit module's no-UPDATE-no-DELETE invariant (Story 1.8 precedent).
    - `test_restore_mode_propagates_audit_value_error_from_bad_result_string` — programmer-error path. Construct a HandsSystem and force the aggregate-row `result` value to `""` (empty string) by patching `_aggregate_result` to return `""`; assert that the `ValueError` raised by `AuditLogger`'s boundary check (Story 1.8 — see [src/nova/core/audit.py:244-245](../../src/nova/core/audit.py#L244-L245)) propagates out of `restore_mode`. Locks the "Hands does not catch programmer errors" half of the contract.

    **Block 6 — URL deferral notice (AC #7 step 2):**
    - `test_restore_mode_logs_url_count_when_mode_has_urls` — mode_config has 2 URLs; assert `caplog` at INFO with `extra={"mode_stem": ..., "url_count": 2}`. Assert the URLs THEMSELVES are NOT in the log message or extras.
    - `test_restore_mode_no_url_log_when_zero_urls` — empty URL tuple; no INFO log fires.

    **Block 7 — Defensive preconditions (AC #7 step 1):**
    - `test_restore_mode_raises_assertion_error_on_empty_apps_tuple` — construct an invalid `ModeConfig(apps=())`; assert `AssertionError` with "loader contract" in the message.
    - `test_restore_mode_raises_assertion_error_on_empty_mode_stem` — call `restore_mode(mode_stem="", mode_config=valid_config)`; assert `AssertionError` with "mode_stem" in the message.
    - `test_restore_mode_raises_assertion_error_on_whitespace_mode_stem` — call `restore_mode(mode_stem="   ", mode_config=valid_config)`; assert `AssertionError`.

    **Block 8 — Single-app mode (boundary):**
    - `test_restore_mode_single_app_success_renders_workspace_ready` — 1-app mode succeeds; final line is `"Workspace ready."` (no special-casing).
    - `test_restore_mode_single_app_failure_renders_total_failure_line` — 1-app mode fails; final line is the `mode edit` hint.

30. **`tests/unit/adapters/win32/test_actions.py`** (new file — primary unit-test surface for `Win32HandsAdapter`). **Platform-neutral via mocking** — no `@pytest.mark.windows_only` on this file. Every Windows-specific surface (`subprocess.Popen`, `os.startfile`, `psutil.process_iter`, `time.monotonic`) is patched at the module-attribute level (`monkeypatch.setattr("nova.adapters.win32.actions.subprocess.Popen", mock_popen)` etc.) so the tests run on any platform. The 100% coverage gate at AC #37 depends on this — `windows_only` would skip the tests on non-Windows runners and break coverage. **Note on `subprocess.DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`:** these constants only exist on Windows in the real `subprocess` module. The adapter source MUST guard the import: `if sys.platform == "win32": _CREATIONFLAGS = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP else: _CREATIONFLAGS = 0` (the no-op fallback is for cross-platform import safety in tests; the adapter is documented as Windows-only at runtime). Tests assert the flag value via the module-level `_CREATIONFLAGS` constant, NOT via direct `subprocess.DETACHED_PROCESS` reference.

    **Block A — Happy launch:**
    - `test_launch_app_subprocess_popen_success` — mock `Popen` to return a `MagicMock` (the spawned-process handle); assert `ActionResult(success=True, reason=None, target=app.name, action_type=ActionType.APP_LAUNCH)`.
    - `test_launch_app_uses_detached_creationflags_constant` — assert `Popen.call_args.kwargs["creationflags"]` equals the module-level `_CREATIONFLAGS` constant. (On Windows this is `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; cross-platform test reads the module's constant directly, sidestepping the platform-conditional import.)
    - `test_launch_app_uses_close_fds_true` — assert `Popen.call_args.kwargs["close_fds"] is True`.
    - `test_launch_app_passes_args_in_argv` — `app = AppConfig(name="Chrome", executable="chrome", args=("--new-window",))`; assert `Popen.call_args.args[0] == ["chrome", "--new-window"]`.

    **Block B — Already-running pre-check (now success-returning):**
    - `test_launch_app_returns_success_when_already_running` — patch `psutil.process_iter` to return a mock process whose `name()` matches the executable basename; assert `ActionResult(success=True, reason=None)` AND `Popen.call_count == 0` (no launch attempted) AND `caplog` at DEBUG contains `"app already running, skipping launch"`. Locks the AC #3 step 2 contract: already-running is a successful workspace outcome, the no-op-launch fact lives in the DEBUG log only.
    - `test_launch_app_case_insensitive_already_running_match` — `executable="Code.exe"`, running process named `"code.exe"` → `success=True, reason=None`.
    - `test_launch_app_proceeds_to_popen_when_psutil_access_denied` — patch `psutil.process_iter` to raise `psutil.AccessDenied` (some processes refuse introspection); assert the adapter falls through to `Popen` (the false-negative-is-correct fail-mode documented in Dev Notes "Already-running detection is best-effort").

    **Block C — Error mapping (canonical four-reason vocabulary):**
    - `test_launch_app_file_not_found_with_empty_args_falls_back_to_startfile` — `app.args == ()`; `Popen` raises `FileNotFoundError`; `os.startfile` returns `None` cleanly; assert `success=True`, assert `os.startfile` called once with `app.executable`.
    - `test_launch_app_file_not_found_with_args_skips_startfile_fallback` — `app.args == ("--new-window",)`; `Popen` raises `FileNotFoundError`; assert `os.startfile.call_count == 0` (fallback gated off because args are present) AND `ActionResult(success=False, reason=REASON_NOT_FOUND)`. Locks the AC #3 step 4 args-gating contract.
    - `test_launch_app_returns_not_found_when_both_popen_and_startfile_fail` — `Popen` raises `FileNotFoundError`, `args == ()`, `os.startfile` raises `FileNotFoundError`; assert `reason == REASON_NOT_FOUND`.
    - `test_launch_app_returns_permission_denied_on_permission_error` — `Popen` raises `PermissionError`; assert `reason == REASON_PERMISSION_DENIED`.
    - `test_launch_app_returns_permission_denied_on_os_error_winerror_5` — `Popen` raises an `OSError` instance with `.winerror = 5` set via `setattr` (Windows `OSError` has `winerror` natively; on POSIX we set the attribute manually for the test); same reason. Documented as a POSIX-test-only `setattr` workaround.
    - `test_launch_app_returns_timed_out_when_wait_for_exceeds_timeout` — patch the `to_thread`-wrapped `Popen` to await an `asyncio.sleep` longer than `_timeout_seconds`; assert `reason == REASON_TIMED_OUT`. Use `Win32HandsAdapter(timeout_seconds=0.05)` for fast test.
    - `test_launch_app_returns_unknown_error_for_other_os_errors` — `Popen` raises `OSError(99, "...")`; assert `reason == REASON_UNKNOWN_ERROR` AND `caplog` at WARNING with the executable name + `winerror=99` in `extra`.

    **Block D — Adapter is one-app-at-a-time (AC #5):**
    - `test_launch_app_returns_single_action_result_not_a_list` — type-check the return: `isinstance(result, ActionResult)`.

    **Block E — Domain-exception boundary (AC #3 step 5):**
    - `test_launch_app_does_not_leak_subprocess_specific_exceptions` — patch `Popen` to raise `subprocess.SubprocessError` (the `subprocess`-module-specific base); assert the caught exception is translated to `ActionResult(success=False, reason=REASON_UNKNOWN_ERROR)` and the original is logged at WARNING — never re-raised. Mirror an analogous test for any `OSError` subclass that might leak.

31. **`tests/integration/test_session_loop.py`** — append two new tests to the existing file (Story 3.5 created it). **Both `@pytest.mark.windows_only @pytest.mark.integration`** — these are the only Story 3.6 tests that exercise real Win32 surfaces; the unit tests at AC #30 are platform-neutral via mocking.

    - `test_mode_restore_full_workspace_ready_end_to_end` — empty `data_dir` with a single mode at `modes/coding.yaml` (stem `coding`) containing one app `notepad.exe` (present on every Windows install). Run `_async_main` with stdin pipe `"mode coding\nshutdown\n"`; assert `EXIT_OK`, assert `audit_log` has rows: 1 `app_launch`/success, 1 `mode_restore`/success with `target="coding"` (the stem), plus the session lifecycle rows. Capture stdout via `Console(file=StringIO())` injection at the test boundary; assert `"✓ Notepad"` and `"Workspace ready."` appear in order. **Notepad cleanup:** capture the spawned `Popen` PID via a fixture that monkeypatches the adapter's `Popen` reference to also stash spawned PIDs in a list, then iterate and terminate at test teardown (target by spawned PID, never by name, to avoid killing user notepads). The fixture lives in this test file's local scope, not the project conftest.
    - `test_mode_restore_partial_failure_workspace_partially_ready` — `modes/coding.yaml` with two apps: `notepad.exe` (real) + `bogus_xyz_app.exe` (fake). Run with `"mode coding\nshutdown\n"`; assert `EXIT_OK`, assert audit_log shows 1 `app_launch`/success + 1 `app_launch`/failed (with `details["reason"] == "not found"`), 1 `mode_restore`/`partial` with `target="coding"`. Assert stdout contains `"✓ Notepad"`, `"✗ Bogus XYZ (not found — is it installed?)"` (the `name:` was set to `"Bogus XYZ"` to make the assertion stable), and `"Workspace partially ready. Bogus XYZ was skipped."` Same Notepad cleanup as above.
    - **Both tests use the real `Win32HandsAdapter`** — this is the integration-test value. Unit tests at AC #30 cover the mocked-launcher paths; integration covers real OS interaction. The `windows_only` marker scopes the real-Win32 cost to Windows runners only; the unit-test coverage gate at AC #37 is independent of the integration-test outcome.

32. **`tests/unit/systems/nerve/test_nerve_system.py`** — extend the existing file with new tests for the reshaped `_handle_mode_switch`:

    - `test_mode_switch_unknown_target_renders_friendly_error_and_does_not_call_hands` — config has modes `["coding", "study"]`; user types `mode unknown`; assert `skin.render_response` called once with `"No mode named 'unknown'. Try mode to see available modes."` AND `hands.restore_mode.call_count == 0` AND `_active_mode_name is None` after the call.
    - `test_mode_switch_known_target_delegates_to_hands_with_stem_and_mode_config` — assert `hands.restore_mode` called once with positional / keyword args `(mode_stem="coding", mode_config=<config.modes["coding"]>)` (assert via `call_args.kwargs` OR `call_args.args` depending on the chosen call style — pin one style and document). Locks the new two-arg signature.
    - `test_mode_switch_passes_command_target_as_mode_stem` — `mode_config.name == "Coding Display"`, `command.target == "coding"`; assert `hands.restore_mode.call_args.kwargs["mode_stem"] == "coding"` (the stem comes from the command, NOT from `mode_config.name`).
    - `test_mode_switch_sets_active_mode_name_to_stem_after_successful_restore` — assert `nerve._active_mode_name == "coding"` (the stem) after the call returns.
    - `test_mode_switch_sets_active_mode_name_even_on_partial_restore` — `hands.restore_mode` returns a list with mixed success/failure; assert `_active_mode_name == "coding"` (partial is still "active").
    - `test_mode_switch_does_not_consult_tier_manager` — replace `nerve._tier_manager` with a `MagicMock(spec=TierManager)` whose `tier` property is itself a `PropertyMock`; call `_handle_mode_switch`; assert the `PropertyMock` was NOT accessed (locks the no-tier-gate scope fence). The `PropertyMock` pattern is the only way to detect property *access*; a plain `MagicMock` attribute would silently allow reads.
    - `test_mode_switch_returns_continue_outcome` — assert return value `is CommandOutcome.CONTINUE`.
    - **Update existing parametrized test** `test_route_command_dispatches_layer_b_routable` row for `MODE/"coding"`: change the assertion from "render_response with stub string" to "delegates to hands.restore_mode with mode_stem='coding' AND _active_mode_name == 'coding' AND skin.render_response NOT called for the placeholder string." Other parametrize rows unchanged.

33. **`tests/unit/systems/hands/test_hands_models.py`** (new file) — locks the new `__post_init__` validators:

    - `test_action_result_success_true_with_reason_raises_value_error` — `ActionResult(action_type=..., target=..., success=True, reason="failed")` raises `ValueError` matching `"success=True requires reason=None"`.
    - `test_action_result_success_false_with_none_reason_raises_value_error` — `ActionResult(success=False, reason=None)` raises `ValueError`.
    - `test_action_result_success_false_with_empty_reason_raises_value_error` — `ActionResult(success=False, reason="")` raises `ValueError`.
    - `test_action_result_success_true_with_none_reason_constructs_cleanly`.
    - `test_action_result_success_false_with_non_empty_reason_constructs_cleanly`.
    - `test_action_request_wraps_dict_details_in_mapping_proxy` — pass a mutable dict as `details`; assert `isinstance(req.details, MappingProxyType)`; assert mutating the original dict does NOT mutate `req.details`.
    - `test_action_request_none_details_stays_none`.
    - `test_action_request_existing_mapping_proxy_passes_through_unwrapped` — passing an already-`MappingProxyType` value does NOT double-wrap. Assert via identity check (`req.details is mapping_proxy_input`).

34. **`tests/unit/ports/test_port_isolation.py`** — add a test or extend the existing `SkinPort` annotation snapshot to assert `render_progress`'s parameter type resolves to `ActionResult` (single, not `Sequence[ActionResult]`). Use `typing.get_type_hints(SkinPort.render_progress)` for runtime resolution per the Story 3.5 precedent (NOT raw `inspect.signature` text — that breaks under `from __future__ import annotations`). Add a new test `test_app_launcher_port_has_single_method_launch_app` parametrized over the expected method tuple for the new `AppLauncherPort`.

35. **`tests/unit/adapters/rich/test_skin_adapter.py`** — append render-progress tests to the existing file:
    - `test_render_progress_success_renders_check_mark_plus_target` — `await adapter.render_progress(ActionResult(success=True, target="VS Code", reason=None, action_type=...))` ⇒ `console.print` called with `"✓ VS Code"` and `markup=False`.
    - `test_render_progress_failure_not_found_appends_is_it_installed_hint` — `reason=REASON_NOT_FOUND` ⇒ console line `"✗ Postman (not found — is it installed?)"`.
    - `test_render_progress_failure_other_reason_renders_plain_parens` — `reason=REASON_PERMISSION_DENIED` ⇒ `"✗ App (permission denied)"` (no extra hint).
    - `test_render_progress_uses_markup_false` — assert the `markup=False` kwarg is passed.

### Group L: deferred-work close-out

36. **Three deferred entries close** (Group H AC #25):
    - [deferred-work.md:137](deferred-work.md#L137) — `ActionRequest.details` MappingProxyType freeze.
    - [deferred-work.md:146](deferred-work.md#L146) — `ActionResult` tri-state validator.
    - [deferred-work.md:56](deferred-work.md#L56) — URL control-char screening **moved forward** to Story 6.5 (Hands implementation in 3.6 is launch-only, no URLs opened here).

### Group M: CI gate

37. **Full quality gate.** All gates pass without weakening:

    - `uv run ruff check src/ tests/` — clean.
    - `uv run ruff format --check src/ tests/` — clean.
    - `uv run mypy src/ tests/` — clean. Strict mode catches the `SkinPort.render_progress` annotation flip and the `NerveSystem.__init__` `hands` parameter addition at every consumer (composition root + tests). Forward-only: zero existing consumers of `render_progress` outside the adapter (the stub raised `NotImplementedError` so nothing called it before).
    - `uv run pytest tests/unit/` — passes. Net delta vs. the post-Story-3.5 baseline (1743 unit pass per the sprint-status entry): expect **+45 to +60 unit tests** (Hands system ≈ 25, Win32 adapter ≈ 12, hands models ≈ 8, nerve _handle_mode_switch ≈ 7, isolation guards ≈ 4, render_progress ≈ 4, composition root ≈ 3).
    - `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes. **Two new integration tests** per AC #31 (both `@pytest.mark.windows_only @pytest.mark.integration`).
    - **100% coverage** on the new modules: `nova.systems.hands.system`, `nova.adapters.win32.actions`, `nova.systems.hands.models` (after the validators land), `nova.ports.app_launcher`. Run: `uv run pytest tests/unit --cov=nova.systems.hands --cov=nova.adapters.win32 --cov=nova.ports.app_launcher --cov-report=term-missing`.
    - **Coverage on modified modules** stays at parity: `nova.systems.nerve.system` (95%+ — was 99.7%; the new `_handle_mode_switch` body adds branches), `nova.adapters.rich.skin` (100% — render_progress added).

### Group N: Same-session adversarial review (no fresh-session trial in 3.6)

38. **Same-session adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) per the established pattern.** Story 3.6 is NOT an A3 fresh-session trial target — the trial was Story 3.5's experiment (epic-3-story-preflags.md:48). Story 3.6 runs the standard three-layer review only; the Epic 3 retrospective will decide whether to re-run the fresh-session trial on a future story based on Story 3.5's results.

## Tasks / Subtasks

- [x] **Task 1 — `AppLauncherPort` + reason constants** (AC: #1, #13, #15)
  - [x] Create [`src/nova/ports/app_launcher.py`](../../src/nova/ports/app_launcher.py) with the `Protocol` + the five `REASON_*` `Final[str]` constants.
  - [x] Add `__all__ = ["AppLauncherPort", "REASON_NOT_FOUND", "REASON_PERMISSION_DENIED", "REASON_TIMED_OUT", "REASON_ALREADY_RUNNING", "REASON_UNKNOWN_ERROR"]`.
  - [x] `uv run mypy src/nova/ports/app_launcher.py` — clean.

- [x] **Task 2 — `Win32HandsAdapter`** (AC: #2, #3, #4, #5)
  - [x] Create [`src/nova/adapters/win32/actions.py`](../../src/nova/adapters/win32/actions.py) with `Win32HandsAdapter` class, `__init__(*, timeout_seconds=5.0)`, `launch_app` body, `_is_already_running` helper.
  - [x] Implement the two-stage launch (Popen → os.startfile fallback) + error mapping per AC #3 step 5.
  - [x] `uv run mypy src/nova/adapters/win32/actions.py` — clean (strict mode).

- [x] **Task 3 — `HandsSystem` + `HandsPort` reshape** (AC: #6, #7, #8, #9, #17, #18, #19)
  - [x] Edit [`src/nova/ports/hands.py`](../../src/nova/ports/hands.py): change `restore_mode` signature to `restore_mode(mode_stem: str, mode_config: ModeConfig) -> list[ActionResult]`; update the module docstring to spell out the stem-vs-display-name distinction (the stem is the canonical identity per the Dev Notes section); update `__all__` if needed.
  - [x] Create [`src/nova/systems/hands/system.py`](../../src/nova/systems/hands/system.py) with the `HandsSystem` class, two-arg `restore_mode(mode_stem, mode_config)` body (8-step ordering), `_aggregate_result` helper, `_summary_text(mode_stem, ...)` helper, `_RESULT_PARTIAL` constant.
  - [x] Replace the `__init__.py` placeholder docstring at [src/nova/systems/hands/__init__.py](../../src/nova/systems/hands/__init__.py) with one that points at `system.py`.
  - [x] Verify the per-app order (audit → render → event) is correct per AC #17.

- [x] **Task 4 — `ActionResult` + `ActionRequest` validators** (AC: #23, #24)
  - [x] Edit [`src/nova/systems/hands/models.py`](../../src/nova/systems/hands/models.py): add `__post_init__` to both dataclasses; add `from types import MappingProxyType` import.
  - [x] Verify all existing call sites of `ActionResult` / `ActionRequest` continue to construct cleanly under the validators.

- [x] **Task 5 — `SkinPort.render_progress` reshape + `RichSkinAdapter` impl** (AC: #10, #15, #16)
  - [x] Edit [`src/nova/ports/skin.py`](../../src/nova/ports/skin.py): change `render_progress` signature; update module docstring.
  - [x] Edit [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py): replace `render_progress` body; import `REASON_NOT_FOUND` from `nova.ports.app_launcher`; update module docstring.

- [x] **Task 6 — `NerveSystem._handle_mode_switch` + `__init__` reshape** (AC: #11, #12)
  - [x] Edit [`src/nova/systems/nerve/system.py`](../../src/nova/systems/nerve/system.py): replace `_handle_mode_switch` body; add `hands: HandsPort` parameter to `__init__`; add `_active_mode_name: str | None = None` field; add `import` for `nova.ports.hands.HandsPort`.

- [x] **Task 7 — Composition root + cli.py wiring** (AC: #20, #21, #22)
  - [x] Edit [`src/nova/app.py`](../../src/nova/app.py): add `hands: HandsPort` field to `NovaApp`; reorder `create_app` so skin is wired before launcher/hands; instantiate `Win32HandsAdapter` and `HandsSystem`; pass `hands` to `NerveSystem`.
  - [x] Add 3 new tests to [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) per AC #21.
  - [x] Verify [`src/nova/cli.py`](../../src/nova/cli.py) is untouched.

- [x] **Task 8 — Unit tests** (AC: #29, #30, #32, #33, #34, #35)
  - [x] Create [`tests/unit/systems/hands/test_hands_system.py`](../../tests/unit/systems/hands/test_hands_system.py) with all 8 blocks per AC #29 (target ≈ 25 tests).
  - [x] Create [`tests/unit/adapters/win32/test_actions.py`](../../tests/unit/adapters/win32/test_actions.py) with blocks A–E per AC #30 (target ≈ 12 tests, `@pytest.mark.windows_only`).
  - [x] Create [`tests/unit/systems/hands/test_hands_models.py`](../../tests/unit/systems/hands/test_hands_models.py) per AC #33.
  - [x] Extend [`tests/unit/systems/nerve/test_nerve_system.py`](../../tests/unit/systems/nerve/test_nerve_system.py) per AC #32 (update existing parametrize row + add 6 new tests).
  - [x] Extend [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) per AC #34.
  - [x] Extend [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) per AC #35 (4 new render_progress tests).

- [x] **Task 9 — AST isolation guards** (AC: #27, #28)
  - [x] Create [`tests/unit/systems/hands/test_hands_system_isolation.py`](../../tests/unit/systems/hands/test_hands_system_isolation.py) per AC #27.
  - [x] Create [`tests/unit/adapters/win32/test_actions_isolation.py`](../../tests/unit/adapters/win32/test_actions_isolation.py) per AC #28.

- [x] **Task 10 — Integration tests** (AC: #31)
  - [x] Append two tests to [`tests/integration/test_session_loop.py`](../../tests/integration/test_session_loop.py); both `@pytest.mark.windows_only @pytest.mark.integration`.
  - [x] Verify cleanup terminates spawned Notepad processes by spawned PID.

- [x] **Task 11 — deferred-work close-out** (AC: #25, #36)
  - [x] Edit [`_bmad-output/implementation-artifacts/deferred-work.md`](deferred-work.md): close lines 137 + 146 with strike-through-and-pointer; update line 56 to retarget Story 6.5.

- [x] **Task 12 — Full CI gate** (AC: #37)
  - [x] `uv run ruff check src/ tests/` — clean.
  - [x] `uv run ruff format --check src/ tests/` — clean.
  - [x] `uv run mypy src/ tests/` — clean.
  - [x] `uv run pytest tests/unit/` — passes; net delta ≈ +45 to +60 vs. the Story 3.5 baseline (1743 unit pass).
  - [x] `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` — passes; +2 new integration tests.
  - [x] `uv run pytest tests/unit --cov=nova.systems.hands --cov=nova.adapters.win32 --cov=nova.ports.app_launcher --cov-report=term-missing` — 100% on new modules.

- [x] **Task 13 — Same-session adversarial review** (AC: #38)
  - [x] Run Blind Hunter / Edge Case Hunter / Acceptance Auditor in parallel general-purpose subagents per the Story 3.4 + Story 3.5 precedent. Triage findings into decision-needed / patches / deferred / dismissed.

### Review Findings (formal /bmad-code-review pass — 2026-05-05)

**Summary:** 3 layers ran fresh; 18 raw findings → 13 unique post-dedup. 0 decision-needed, 6 patches, 6 deferred, 1 dismissed. Acceptance Auditor reported 0 AC gaps. **All 6 patches applied; 1861 unit pass (+7) + 56 integration pass; 100% coverage on every Story 3.6 module holds.**

**Post-review user-reported HIGH (2026-05-05):** the three review layers all missed a Story 3.4 contract regression — `_handle_mode_switch` used `command.target` verbatim as the `NovaConfig.modes` dict key, breaking the case-insensitive-lookup contract spelled out in the Story 3.4 spec line 406 (*"the lookup against `NovaConfig.modes` is case-insensitive at Nerve's level, but the audit log and the user-facing echo should reflect what the user typed"*). User-typed `mode Coding` or `Switch to Coding mode` → parser produces `target="Coding"` (preserved casing per Story 3.4 contract) → my dict lookup `self._config.modes.get("Coding")` returned None → friendly error rendered instead of restoring the mode. **Fix applied:** lookup key is `command.target.lower()` (canonical kebab-case stems); error template still echoes `command.target` (original casing) so the user recognizes their input; downstream identity (`hands.restore_mode`, `_active_mode_name`, `ModeRestored.mode_name`, audit `target`) all use the lowercased canonical stem. Locked by `test_mode_switch_lookup_is_case_insensitive_and_passes_lowercased_stem_to_hands` (full delegation: target="Coding" → `hands.restore_mode("coding", config.modes["coding"])`) AND `test_mode_switch_unknown_target_error_echoes_original_user_casing` (target="UnknownMode" → error renders `"No mode named 'UnknownMode'"`). 1863 unit pass (+2) + 56 integration pass; ruff/format/mypy clean.

- [x] [Review][Patch] `_iter_processes_for_match` per-proc except misses `psutil.ZombieProcess` and `OSError` from `proc.info` access [src/nova/adapters/win32/actions.py:_iter_processes_for_match inner try] — fixed: per-proc except now `(NoSuchProcess, AccessDenied, ZombieProcess, OSError)`; outer except unchanged. Locked by `test_iter_processes_for_match_skips_zombie_process` and `test_iter_processes_for_match_skips_per_proc_oserror`.
- [x] [Review][Patch] `_normalize_exe_basename` empty-string collision: `("")` and `(".exe")` return `""` and match empty-named processes [src/nova/adapters/win32/actions.py:_normalize_exe_basename and _iter_processes_for_match] — fixed: early `if not target: return False` guard at top of `_iter_processes_for_match`. Locked by `test_iter_processes_for_match_returns_false_for_empty_target` (parametrized over both `""` and `".exe"`).
- [x] [Review][Patch] `ActionRequest.__post_init__` MappingProxyType passthrough breaks frozen-promise; caller-retained dict still mutates through proxy; test even enforces the broken behavior [src/nova/systems/hands/models.py:__post_init__] — fixed: dropped the `isinstance(self.details, MappingProxyType): return` passthrough; the wrap is now ALWAYS `MappingProxyType(dict(self.details))`. Test renamed + reshaped: `test_action_request_existing_mapping_proxy_is_still_re_wrapped_for_isolation` asserts `req.details is not proxy` AND that mutating the source dict does not leak.
- [x] [Review][Patch] `_active_mode_name` not cleared on second-restore total-failure [src/nova/systems/nerve/system.py:_handle_mode_switch] — fixed: added `else: self._active_mode_name = None` branch so the field reflects the LATEST restore outcome. Locked by `test_mode_switch_clears_active_mode_name_on_second_restore_total_failure` (full first-restore → total-failure-second-restore sequence).
- [x] [Review][Patch] AST guard `test_restore_mode_does_not_wrap_audit_log_action_in_try_except` only walks `try.body`, not `handlers`/`orelse`/`finalbody` [tests/unit/systems/hands/test_hands_system.py] — fixed: walker now iterates `body`, `orelse`, `finalbody`, AND each handler's body. Asserts on a labelled `(line, slot_name)` tuple list so a future violation reports WHICH slot.
- [x] [Review][Patch] No regression test asserting `except Exception` (not `BaseException`) propagates `CancelledError` / `SystemExit` from inside skin/event isolation try-blocks [tests/unit/systems/hands/test_hands_system.py] — fixed: added parametrized `test_restore_mode_propagates_cancelled_error_from_isolated_surfaces` over `["render_progress", "per_app_emit", "render_response"]`. Each injects `asyncio.CancelledError` from one isolated surface and asserts `restore_mode` propagates (does NOT swallow). Locks the `except Exception` choice against future widening.
- [x] [Review][Defer] Mid-loop exception (audit `ValueError` or `CancelledError`) leaves orphan per-app rows + no aggregate audit + no `ModeRestored` event [src/nova/systems/hands/system.py:restore_mode] — deferred: intentional consequence of AC #19's unwrapped-audit contract; programmer errors propagate by design. Softening requires a Nerve-layer design decision (catch + render friendly error) — outside this story's scope.
- [x] [Review][Defer] `_normalize_exe_basename` only strips single trailing `.exe` (compound `.exe.bak` not handled — undocumented) [src/nova/adapters/win32/actions.py] — deferred: theoretical (`.exe.bak` is not a real Windows pattern).
- [x] [Review][Defer] URL-deferral INFO log spams once per restore (no per-instance dedup) [src/nova/systems/hands/system.py:restore_mode step 2] — deferred: minor log noise; not a correctness issue.
- [x] [Review][Defer] `render_progress` doesn't sanitize ANSI escape sequences in app names (e.g. `"\x1b[2J"` clears terminal) [src/nova/adapters/rich/skin.py:render_progress] — deferred: requires malicious user-written YAML; loader-side validation is the right fix (Story 6.4 mode editor).
- [x] [Review][Defer] No kebab-case validator precondition on `restore_mode(mode_stem, ...)` (defensive; `command.target` already lowercased by parser) [src/nova/systems/hands/system.py:restore_mode step 1] — deferred: no live exploit path; existing assert covers empty/whitespace.
- [x] [Review][Defer] Audit-log payload size for 50-app modes (defer with the existing large-mode item) [src/nova/systems/hands/system.py:aggregate audit] — deferred: bundle with the existing "large-mode psutil scan" deferred entry.

**Dismissed (1):** Exception from `restore_mode` propagates to REPL — `cli.py` top-level handler (Story 3.5) catches `Exception` → maps to `EXIT_UNEXPECTED=4`; not a Story 3.6 concern.

## Dev Notes

### Pattern library consulted

- **#1 Port + adapter split** — The `HandsPort` ↔ `HandsSystem` (system port) and `AppLauncherPort` ↔ `Win32HandsAdapter` (adapter port) split is the canonical "system has logic, adapter has translation" layering. Mirrors the Story 1.4 `StoragePort` ↔ `SqliteStorageEngine` (adapter port for storage I/O) plus Story 3.1 `BrainPort` ↔ `SqliteBrainAdapter` (system port for domain access).
- **#2 Two-function clock pattern** — N/A in Story 3.6. The `time.monotonic()` measurement in `Win32HandsAdapter.launch_app` is a duration probe, not a wall-clock timestamp; the duration is logged at DEBUG only (no event payload, no audit row, no test reliance). No injectable clock needed.
- **#3 Frozen dataclass + post_init validator** — `ActionResult.__post_init__` (Group H) is the classic pattern for tri-state invariant enforcement. `ActionRequest.__post_init__` uses the `object.__setattr__` escape hatch documented in the Python dataclasses reference (the only legitimate use case for bypassing frozen).
- **#4 Error translation at adapter** — `Win32HandsAdapter.launch_app`'s error-mapping block (AC #3 step 5) is the canonical adapter pattern: catch concrete OS-level exceptions, map to a closed string vocabulary, log with full traceback at WARNING for diagnosis, return a domain `ActionResult`. NEVER let a `subprocess` / `pywin32` exception class cross the port boundary.
- **#5 Skip-on-error / observational logging** — The `audit.log_action` call in HandsSystem follows Story 1.8's observational pattern. Audit failure is swallowed BY AuditLogger; HandsSystem must NOT add a wrapping try/except (would also catch programmer errors).
- **#6 Transaction CM** — N/A; per-app launches don't share a transaction. Each `launch_app` call is independent at the OS level.
- **#7 Partial-init cleanup** — Composition root already has the `try / except BaseException` block. Adding `Win32HandsAdapter` (constructor stores `timeout_seconds`, no I/O) and `HandsSystem` (constructor stores port refs, no I/O) is structurally covered.
- **#8 Sequential-not-parallel orchestration** — Story 3.6 explicitly chooses sequential per-app launches over `asyncio.gather`. See AC #8 for the rationale (predictability, antivirus interference avoidance, NFR1 budget headroom).

### Why mode restore does NOT consult `tier_manager.tier`

Two readings of the architecture left this ambiguous:

1. The Story 3.5 spec calls `_tier_check_or_offline_response` "the structural seam Epic 7 will consume" and notes Story 3.5 has zero call sites for it. This implies that *every* future operation should at least consider whether to consult tier — including mode restore.
2. The epic AC for Story 3.6 says nothing about tier — it lists graceful-partial as the only failure pattern, no degraded-mode branch. The architecture's tier table at architecture.md:809 says: `Hands | All safe actions | All safe actions | All safe actions` — meaning Hands runs at full capability across all three tiers (FULL / DEGRADED / OFFLINE).

Reading 2 is correct. Mode restore is purely-local (subprocess.Popen + os.startfile + psutil — no cloud surface). Tier-gating exists to prevent cloud calls when the tier doesn't permit them; mode restore has no cloud call to gate. The scope-fence is documented in `_handle_mode_switch`'s docstring (AC #11) AND locked by a positive test (`test_mode_switch_does_not_consult_tier_manager` at AC #32).

A future Voice-driven post-restore prose enrichment ("Last thread: auth tests.") WILL be tier-gated — that's Voice's prose enrichment, which depends on the Claude API. Story 3.6's local final-line operational copy stays tier-agnostic.

### Why the launcher is a separate port from `HandsPort`

Three options were considered:

1. **Single port** — `HandsPort` keeps `restore_mode` AND adds `launch_app`. The Win32 adapter implements both. Rejected because the orchestration logic (graceful-partial, audit ordering, event emission, render-progress streaming) is genuinely business policy and would land inside the adapter — violates project-context.md:77 *"Adapters may translate, never decide."*
2. **No new port** — `HandsSystem` imports `Win32HandsAdapter` directly. Rejected because it violates the no-system-imports-adapters rule (architecture's port-and-adapter convention).
3. **Two ports** (chosen) — `HandsPort` (system-facing, `restore_mode`) + `AppLauncherPort` (adapter-facing, per-app `launch_app`). HandsSystem implements `HandsPort` and depends on `AppLauncherPort`. Win32HandsAdapter implements `AppLauncherPort` only.

Option 3 makes the layering crisp: Nerve sees only `HandsPort` (the high-level "restore this mode"); Hands sees only `AppLauncherPort` (the low-level "launch this app"). Future Story 6.1's window-focus + window-arrange add either as new methods on `HandsPort` (if Nerve consumes them) or as new methods on `AppLauncherPort` (if HandsSystem orchestrates them too — likely the latter for a future "restore_and_arrange" that chains launch → focus → arrange).

### Why `_active_mode_name` lives on `NerveSystem`, not in Brain

Two options:

1. **Brain field** — `BrainPort.set_active_mode(mode_name)` writes to a new `current_mode` column on `sessions`. Persists across crashes (Story 3.10 could read it on next-startup briefing).
2. **NerveSystem field** (chosen) — in-memory `self._active_mode_name: str | None`. Dies on process exit.

Option 2 wins for T1 because:
- The active mode is a runtime fact, not a durable one. The user's "current mode" is meaningful only within an active session.
- The `sessions.mode_name` column already exists (Story 3.1) but is set ONCE at session creation (currently always `None` per Story 3.5). A mid-session mode switch could update it, but that's a Brain widening Story 3.6 doesn't need.
- Story 3.7's shutdown flow can read `_active_mode_name` from Nerve and pass it to Brain on `end_session(..., summary={"mode": active_mode_name, ...})` — keeps the durable record at the natural boundary (shutdown).
- Story 3.10's crash recovery uses the LAST workspace snapshot (Eyes integration) for "what mode were they in" — the Eyes capture is the canonical source for that question, not a Nerve in-memory field.

If T2 ever needs persistent active-mode tracking (e.g., for a system tray indicator), Brain widens then. Today the in-memory field is sufficient.

### Why `_summary_text` lives in HandsSystem and not in a Voice helper

The final-line summary (`"Workspace ready."` / `"Workspace partially ready. {app} was skipped."` / `"No apps could be launched. ..."`) is operational copy per project-context.md:66 + UX spec line 591 + 1033 (`Workspace ready.` listed under "Brevity by default"). It's not personality-bearing — it's the canonical brief response that ships in T1 even before Voice exists.

If Epic 7's Voice integration wants to add personality dressing ("Workspace ready. Last thread: auth tests."), the seam is: Voice subscribes to `ModeRestored`, generates the prose extension, and calls `skin.render_response(extension)` AFTER HandsSystem's bare summary. The two render_response calls land sequentially; the user sees the bare line followed by the prose line. Or Voice could call `skin.render_response` with a fully-replaced line — either works without Story 3.6 needing to anticipate the wiring.

The scope fence is: Story 3.6 owns the bare summary; Voice (Epic 7) owns optional dressing. Document in `_summary_text`'s docstring.

### Why per-app order is `audit → render → event` (not `event → audit → render`)

Three options:
1. `event → audit → render` — emit first so subscribers fire fastest. Rejected: `AppLaunched` subscribers (none in T1, but Eyes in Story 4.1) might query the audit log to correlate with the launch. If the event fires before the audit row lands, the subscriber sees an empty audit query.
2. `render → audit → event` — show the user first for snappiest perceived latency. Rejected: render is `asyncio.to_thread(console.print)` which is blocking I/O; audit is a single SQLite write that's also blocking but typically faster (<1ms for in-memory or warmed cache). The two are comparable in latency; ordering audit first preserves the durable-record-first invariant that Story 1.8 established.
3. `audit → render → event` (chosen) — durable record first (audit row exists if anyone queries), then user-visible render (now the user sees the line), then runtime fan-out (event subscribers fire knowing the audit log has the row).

Option 3 trades ~1ms of perceived latency for invariant clarity. The user-visible perception is unchanged (the difference is sub-millisecond).

### Already-running is a successful workspace outcome

The adapter's `_is_already_running` pre-check returns `success=True, reason=None` when a matching process is found, NOT `success=False, reason="already running"`. Rationale: the user said `mode coding` because they want their workspace ready — whether N.O.V.A. spawned the app or just observed it already up is implementation detail. Returning failure would corrupt the partial / total-failure counters: a mode where every configured app happens to already be running would render `"No apps could be launched. Check mode config: mode edit <stem>"` even though the workspace is fully ready. That's a misleading UX.

The no-op-launch fact lives in the adapter's DEBUG log (`"app already running, skipping launch"` with the executable in `extra`) for diagnosis; it does NOT propagate through `ActionResult` or the audit row. If a future story needs to surface the distinction (e.g., a `status` line that shows "3 apps in coding mode, 1 was already running"), Brain or Eyes would track running-process state via `ContextChanged` events from Story 4.1's polling adapter — Hands stays a launch-time-only system.

`psutil.process_iter([...])` matches by executable basename. Edge cases (acceptable known limitations for T1):

- **Different installations of the same exe.** A user with `C:\Program Files\Mozilla Firefox\firefox.exe` AND `C:\Users\sayuj\AppData\Local\Mozilla Firefox\firefox.exe` would have either one match the basename `firefox.exe` and skip the launch — even if the user wanted the second instance. The user can manually launch the second one or remove the duplicate.
- **Renamed processes.** Some apps rename themselves at runtime (e.g., game launchers). The pre-check would miss them and we'd launch a duplicate. Acceptable: launching a duplicate is the worse-but-recoverable outcome.
- **psutil access denied on protected processes.** Some processes (system services, AV) refuse `process_iter` introspection. The `psutil.AccessDenied` exception is caught and the process is treated as "not running" — false-negative is the correct fail-mode (we'd rather attempt the launch than skip it).

Documented in the adapter docstring as best-effort detection.

### Why the mode stem is the canonical mode identity (not `ModeConfig.name`)

`ModeConfig` carries a `name: str` field that's the user-facing **display label** — it can have spaces (`name: "Study Group"`), mixed case, or even special characters (within the YAML loader's tolerance). The **stem** is the YAML file basename (kebab-case, validated by `_MODE_STEM_RE = r"[a-z0-9][a-z0-9-]*"` in [src/nova/core/config.py:47](../../src/nova/core/config.py#L47)) and is the dict key in `NovaConfig.modes`. Three downstream surfaces need a stable identity:

1. **`ModeRestored.mode_name`** — Story 4.1's snapshot trigger keys on this to write `workspace_snapshots.mode_name`; later transparency queries (Story 5.1) and Brain's `get_mode_last_used` (Story 3.2 — already shipping in `_handle_modes_list`) all key on the stem. If half the events carried display labels and half carried stems, the cross-table joins would silently drop rows.
2. **`audit_log.target` for `MODE_RESTORE` rows** — the audit trail's "what mode was restored" answer must match what the user typed (`mode coding` → `target="coding"`). Display labels in the audit log would force a reverse lookup at query time.
3. **The `mode edit <stem>` user-facing hint** — Story 6.4's `mode edit <X>` parser will resolve `X` against `config.modes.get(X)`, which is keyed by stem. If the total-failure summary said `"mode edit Coding Display"`, the user's literal typing of `mode edit Coding Display` would fail (case-sensitivity, spaces). The hint must be runnable verbatim.

`HandsPort.restore_mode` accepts both: `mode_stem` (the canonical identity, used for events / audit / hints) and `mode_config` (carries the apps + URLs + folders the launcher iterates). NerveSystem's `_handle_mode_switch` passes both because it has both — `command.target` IS the stem (the parser only emits lowercased identifiers), and `config.modes.get(command.target)` returns the matching `ModeConfig`. The two-arg call is documentation: future callers (Story 3.8's resume path, Story 6.3's auto-switch-after-create) must be explicit about the stem they're claiming, not derive it from a lookup.

### Audit-failure isolation: the layered contract + how to test it

The contract has two layers:

1. `AuditLogger.log_action` swallows `StorageError` internally (Story 1.8's observational-not-transactional contract — see [src/nova/core/audit.py:286-322](../../src/nova/core/audit.py#L286-L322)). The `await audit.log_action(...)` call returns `None` cleanly when the underlying SQLite write raises `StorageError`.
2. `HandsSystem` does NOT wrap `audit.log_action` in a try/except. A wrapping `except Exception` would also catch programmer errors that AuditLogger raises by design: `TypeError` from non-`ActionType` `action_type` arguments, `TypeError` from non-JSON-serializable `details`, `ValueError` from empty / whitespace-only `result` strings ([src/nova/core/audit.py:234-245](../../src/nova/core/audit.py#L234-L245)). Those are bugs and MUST surface.

The naive test pattern — "patch `audit.log_action` to raise `StorageError` and assert Hands continues" — would CONTRADICT the contract: the spec forbids HandsSystem from catching, so a raise from `log_action` would propagate, and the assertion that "Hands continues" would fail. The correct test pattern uses a real `AuditLogger` wired to a tiny in-test failing storage engine (`_FailingStorageEngine` per Block 5 at AC #29). The failure happens **inside** `AuditLogger.log_action` where the swallow lives; HandsSystem sees a clean `None` return; the contract is exercised end-to-end without violating it. The complementary AST test (`test_restore_mode_does_not_wrap_audit_log_action_in_try_except`) is the static-analysis lock that catches a future regression where someone adds a try/except "to be defensive" around the audit call.

### Why os.startfile fallback is gated on empty args

`subprocess.Popen([executable, *args])` honors the user's `args:` field cleanly — the args are passed through as `argv[1:]` to the child process. `os.startfile(path)` uses Windows ShellExecute under the hood; its `arguments=` parameter (added in Python 3.10) has historically inconsistent behavior:

- ShellExecute interprets `arguments` per the file association, not per a literal argv. A `.bat` file's arguments map to `%1, %2, ...`, but a `.lnk` file's arguments may be appended after the shortcut's pre-configured args, or replace them entirely, depending on the registry entry shape.
- Quoting rules differ from CommandLineToArgvW (the standard Win32 argv parser). Args with spaces need different escaping under ShellExecute vs. `subprocess.Popen` — `os.startfile`'s docs note "the arguments must be properly quoted by the caller."
- Some Windows versions (older Server SKUs, Wine, Windows-on-ARM under emulation) silently ignore the `arguments` parameter on certain file types.

If the user wrote `executable: chrome args: ["--new-window"]` and Chrome isn't on PATH (so Popen raises `FileNotFoundError`), the fallback paths split:

- **Args-empty mode (the original use case for fallback):** `os.startfile("chrome")` resolves via App Paths registry, launches Chrome with default flags. User gets Chrome — correct behavior.
- **Args-present mode:** `os.startfile("chrome", "open", arguments="--new-window")` would *probably* work on modern Windows, but the `--new-window` flag would be appended to whatever Chrome's App Paths registry says about default args — silently, with no diagnostic. If the launch silently dropped the `--new-window` arg, the user would think their config worked but get the wrong window behavior.

The args-gated fallback (Group A AC #3 step 4) trades a small loss in fallback coverage (apps with `.lnk` shortcuts AND args lose the fallback path) for a trustworthy `args` contract: when `args` is set, either Popen launches with the args correctly or the launch fails honestly with `REASON_NOT_FOUND`. The user can then either install the app properly (so Popen works) or write the `.lnk` path explicitly as the executable. Tests at Group K Block C lock both branches.

### Why `subprocess.Popen` over `os.startfile` as primary

`subprocess.Popen([executable, *args])`:
- Returns a `Popen` object with `.pid` attribute (useful for testing — integration test cleanup uses the pid to terminate spawned processes).
- Honors the `args` list cleanly (the user's `args:` field in the mode YAML maps 1:1).
- Fails with `FileNotFoundError` on missing executable — easy to map to canonical reason.

`os.startfile(path)`:
- Uses Windows ShellExecute under the hood — handles `.lnk` shortcuts, registered file associations, App Paths registry entries.
- Returns `None`; no PID handle.
- `arguments=` parameter added in Python 3.10 has historically inconsistent behavior — see the "Why os.startfile fallback is gated on empty args" Dev Note for the full rationale on why we don't use it.

The two-stage strategy (Popen first, args-empty-gated `os.startfile` fallback on `FileNotFoundError`) gets the best of both: Popen handles args + PID-tracking for the common case (executable on PATH); startfile handles `.lnk` shortcuts + App Paths for the niche case where the user wrote `chrome` and the system has Chrome registered but not on PATH AND the user's mode YAML left `args` empty.

### Why `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`

- `DETACHED_PROCESS` — the child process does NOT inherit the parent's console. Critical because N.O.V.A. is a console app; without this flag, launched apps would either share N.O.V.A.'s console (visual mess) or fail to launch GUI apps (they expect no console).
- `CREATE_NEW_PROCESS_GROUP` — the child does not receive Ctrl-C / Ctrl-Break signals delivered to N.O.V.A.'s process group. Critical because the user pressing Ctrl-C in N.O.V.A. should NOT close the apps they just launched. The signal-handler in NerveSystem (Story 3.5) handles N.O.V.A.'s shutdown; the launched apps stay running.

The flag combination is the canonical "launch and forget" recipe on Windows. Documented in the adapter docstring + locked by `test_launch_app_uses_detached_process_creationflags` at Group K.

### Story-3.5 reconciliation: the `_handle_mode_switch` placeholder

The current placeholder body at [src/nova/systems/nerve/system.py:827-832](../../src/nova/systems/nerve/system.py#L827-L832) renders `f"Mode restore lands in Story 3.6. Stub: would switch to {command.target!r}."`. Three reconciliations:

1. **Body replacement** — wholesale replace with the new delegation logic per AC #11. Documentation comment "Story 3.6 replaces this body" is removed (the body now IS Story 3.6's).
2. **Test update** — the existing `test_route_command_dispatches_layer_b_routable` parametrized test in [tests/unit/systems/nerve/test_nerve_system.py](../../tests/unit/systems/nerve/test_nerve_system.py) has a row for `MODE/"coding"` that currently asserts the placeholder string. **Update**, not delete: the test now asserts `hands.restore_mode` was called once AND `_active_mode_name == "coding"`. Other rows (MODE_CREATE, MODE_EDIT, etc.) stay unchanged because their placeholders are still in scope (Epic 5/6).
3. **Forward-only typing** — the `NerveSystem.__init__` `hands` parameter addition is keyword-only, no default. Every existing test that constructs `NerveSystem(...)` directly (not via `create_app`) must add `hands=MagicMock()` to its construction. Estimate: ~10 tests in `test_nerve_system.py`. Use a shared fixture.

### Why `os.startfile` doesn't get the timeout treatment

`subprocess.Popen` has the `to_thread` + `wait_for(timeout=...)` wrap because the process spawn can stall under antivirus interference. `os.startfile` is a SHELL operation — it returns immediately once the shell accepts the launch request; it doesn't wait for the launched process to start. There's nothing to time out: either ShellExecute accepts the request (success — the app may or may not actually start, but that's beyond our scope) or it raises immediately (fail-fast). The fallback path skips the timeout wrapper for that reason.

### Project Structure Notes

- **Alignment with unified project structure:** All paths align with the architecture.md §"Complete Project Directory Structure":
  - `src/nova/ports/app_launcher.py` — new port (architecture lists `ports/hands.py` only; the new file is a layering refinement consistent with the adapter port pattern from `core/storage/`).
  - `src/nova/adapters/win32/actions.py` — exactly per architecture.md:1370 (`Win32HandsAdapter — app launch, focus, arrange`); Story 3.6 ships the launch portion only (focus + arrange land in Story 6.1).
  - `src/nova/systems/hands/system.py` — exactly per architecture.md:1339 (`HandsSystem — action execution, result reporting`).
- **Detected variances:**
  - Architecture sketches `HandsPort` as the only Hands-related port. Story 3.6 introduces `AppLauncherPort` as a NEW adapter-facing port. Rationale documented in the "Why the launcher is a separate port from HandsPort" Dev Notes section.
  - Architecture's data flow line at 379 says `"Nerve → Brain: get_mode_config('coding')"`. This story reads `command.target` against `self._config.modes` directly (no Brain involvement). Reason: `NovaConfig` is already injected into NerveSystem (Story 3.5); routing the lookup through Brain would add a Brain method `get_mode_config(stem) -> ModeConfig | None` that just calls `config.modes.get(stem)`. The Brain detour adds zero value and one extra port surface. The architecture's data flow diagram is informal (not load-bearing); the actual contract per project-context.md:69 is that the Config module owns YAML reads, and `NovaConfig` IS the config module's output — Nerve reading `config.modes` directly is consistent.
- **No conflicts** with existing source structure or naming conventions.

### References

- [Source: epics.md#Story 3.6 (lines 1191-1216)](../planning-artifacts/epics.md#L1191-L1216)
- [Source: architecture.md#Decision 2 (line 376-383)](../planning-artifacts/architecture.md#L376-L383) — Mode restore data flow
- [Source: architecture.md#Architectural Boundaries (line 1462)](../planning-artifacts/architecture.md#L1462) — Hands ↔ Win32 trust boundary
- [Source: architecture.md#FR Category to Structure Mapping (line 1495)](../planning-artifacts/architecture.md#L1495) — Workspace Modes maps to systems/nerve, systems/hands
- [Source: architecture.md#Audit Logging Convention (line 1185-1202)](../planning-artifacts/architecture.md#L1185) — AuditLogger usage from Hands
- [Source: ux-design-specification.md#Critical Error Scenarios > Scenario 3 (line 691-709)](../planning-artifacts/ux-design-specification.md#L691) — Workspace partial-restore behavior rules
- [Source: ux-design-specification.md#Personality Doctrine (line 1033)](../planning-artifacts/ux-design-specification.md#L1033) — `Workspace ready.` listed as canonical brief response
- [Source: project-context.md#Critical Don't-Miss Rules (line 190, 195)](../project-context.md#L190) — Partial restore distinguishable from full; graceful partial is the default failure pattern
- [Source: project-context.md#Performance Budgets (line 205)](../project-context.md#L205) — NFR1: Workspace restore < 30 seconds
- Closes [deferred-work.md:137](deferred-work.md#L137), [deferred-work.md:146](deferred-work.md#L146); retargets [deferred-work.md:56](deferred-work.md#L56) to Story 6.5

## Dev Agent Record

### Agent Model Used

claude-opus-4-7[1m]

### Debug Log References

Same-session adversarial review (3 layers in parallel general-purpose subagents):

* **Blind Hunter:** 15 findings (3 HIGH, 5 MED, 6 LOW, 1 INFO). Patches applied: skin/event isolation in HandsSystem (HIGH #4 / #8 — render and emit are now wrapped in try/except around the loop and around the final summary, with structured ERROR logs); `_active_mode_name` only set when at least one app launched (LOW #13 — Nerve consumes the result list and partitions on `any(r.success for r in results)`); psutil basename normalization (MED #5 — strip `.exe` and lowercase both sides via new `_normalize_exe_basename` helper, so `chrome` config matches `chrome.exe` running); `os.startfile` POSIX `hasattr` guard (HIGH #1 — production stays Windows-only but the guard makes the import + accidental POSIX call cleanly return `REASON_NOT_FOUND`). Deferred (10 entries logged at deferred-work.md): Popen handle leak; wait_for + Popen race; .lnk + args fallback; extended permission winerrors (740 / 1314); executable-path PII in audit details; redundant-null-reason cosmetic; markup-injection regression test; render_progress markup test; concurrent restore lock; large-mode psutil bound.
* **Edge Case Hunter:** 15 findings (2 HIGH, 4 MED, 9 LOW). Patches applied: Skin/event isolation also covers EC #3 (UnicodeEncodeError on legacy console) and EC #4 (Skin/Rich exception aborts loop); psutil.Error broader catch (EC #10 — outer `except (psutil.Error, OSError)` returning False, fail-safe). Deferred: whitespace-only executable validation; concurrent restore lock; mode name in summary; large-mode psutil scan; plural form; Ctrl-C `_active_mode_name` cleanup; defensive non-empty target assert; two-instance race; parens-in-name visual ambiguity.
* **Acceptance Auditor:** 7 items (0 BLOCKERs, 2 GAPs, 5 INFO/spec-cleanup). Patches applied: cross-mock chronological-order tests via `parent.attach_mock` pattern for both per-app AND aggregate ordering (closes AA #4 + #5 — the previous tests asserted per-mock counts but did not actually lock `audit < render < emit < launch[i+1]` cross-mock); per-app audit `details` shape test (AA #7 — locks `{"executable": ..., "reason": ...}` for both success and failure rows). Deferred: spec-text cleanup (AA #1, #2, #3, #6 — spec stale text; impl is correct).

Net effect: 7 substantive patches landed in same-session review; 13 follow-ups deferred with explicit deferred-work.md entries (each with target story / next-touch trigger).

### Completion Notes List

* All 38 ACs across 14 groups (A–N) implemented and locked by tests. 100% coverage on every Story 3.6 module: `nova.ports.app_launcher`, `nova.adapters.win32.actions`, `nova.systems.hands.{models,system}`. Coverage on the modified-line region of `nova.systems.nerve.system` and `nova.adapters.rich.skin` stays at parity.
* **Five blockers from the spec-review round (in the Stories 3.6 spec, before implementation) are honored in the impl + tests:**
  * (1) `mode_stem` flows through `restore_mode(mode_stem, mode_config)` to `ModeRestored.mode_name`, the `MODE_RESTORE` audit `target`, the `mode edit <stem>` total-failure hint, and `_active_mode_name`. Locked by dedicated stem-vs-display fixture tests using `mode_stem="study-group"` / `mode_config.name="Study Group"`.
  * (2) Already-running maps to `success=True, reason=None`; the canonical reason vocabulary stays four members (no `REASON_ALREADY_RUNNING`). Locked by `test_launch_app_returns_success_when_already_running` + `test_restore_mode_treats_already_running_as_success`.
  * (3) Audit-failure isolation uses a real `AuditLogger` over a tiny in-test `_FailingStorageEngine`, exercising the swallow path inside AuditLogger. The complementary AST guard `test_restore_mode_does_not_wrap_audit_log_action_in_try_except` walks the source and rejects any `try/except` containing a `log_action` call — locks the unwrapped contract statically.
  * (4) `Win32HandsAdapter` unit tests run platform-neutral via mocking (`subprocess.Popen`, `os.startfile`, `psutil.process_iter`), no `@pytest.mark.windows_only`. The two real-Notepad integration tests in `test_session_loop.py` are the only `@windows_only @integration` surface.
  * (5) `os.startfile` fallback gated on `len(app.args) == 0` so the user's args contract is trustworthy — Popen failure with args present returns `REASON_NOT_FOUND` directly rather than silently dropping args via ShellExecute.
* **Three deferred-work entries closed:** [deferred-work.md:137](deferred-work.md#L137) (`ActionRequest.details` MappingProxyType freeze), [deferred-work.md:146](deferred-work.md#L146) (`ActionResult` tri-state validator), AND [deferred-work.md:56](deferred-work.md#L56) retargeted to Story 6.5 (URL control-char screening — Story 3.6 is launch-only, no URLs opened).
* **Composition-root reorder:** `create_app` now wires `skin → launcher → hands → ritual → nerve` (was `ritual → skin → nerve`). The reorder is mandatory because `HandsSystem(skin=skin, ...)` needs the Skin reference at construction. Locked by the existing partial-init cleanup block (Win32HandsAdapter + HandsSystem are reference-storage only — no new failure modes).
* **Tier-gating fence:** mode restore does NOT consult `tier_manager.tier`. Locked by `test_mode_switch_does_not_consult_tier_manager` using a `PropertyMock` (the only way to detect property *access* — a plain MagicMock attribute would silently allow reads).
* **Composition-root logger-allowlist update:** added `nova.adapters.win32.actions` to the three-dot logger-name exception set in `tests/unit/test_composition_root.py` (mirrors the existing `nova.adapters.rich.skin` and `nova.adapters.sqlite.brain` precedent for `adapters/{driver}/{system}` nesting).
* **Final test counts:** 1854 unit pass (was 1748 baseline = +106 net), 56 integration pass (+2 new windows_only); 1 pre-existing skip; 1 pre-existing brittle-marker deselect. ruff + ruff format + mypy strict — all clean.
* **All 13 same-session-review-deferred items have explicit deferred-work.md entries** with target story / next-touch trigger.

### File List

**New source files (4):**
* `src/nova/ports/app_launcher.py`
* `src/nova/adapters/win32/actions.py`
* `src/nova/systems/hands/system.py`
* (deferred-work close-out — `_bmad-output/implementation-artifacts/deferred-work.md`)

**Modified source files (7):**
* `src/nova/systems/hands/__init__.py` — package docstring update.
* `src/nova/systems/hands/models.py` — added `__post_init__` validators on `ActionResult` + `ActionRequest`.
* `src/nova/ports/hands.py` — `restore_mode(mode_stem, mode_config)` signature reshape; module docstring rewrite.
* `src/nova/ports/skin.py` — `render_progress(result: ActionResult)` signature reshape.
* `src/nova/adapters/rich/skin.py` — replaced `render_progress` `NotImplementedError` body; added `REASON_NOT_FOUND` import.
* `src/nova/systems/nerve/system.py` — added `hands: HandsPort` constructor parameter, `_active_mode_name` field, replaced `_handle_mode_switch` body with HandsPort delegation.
* `src/nova/app.py` — added `hands: HandsPort` field to `NovaApp`; reordered `create_app` (skin before launcher/hands); instantiated `Win32HandsAdapter` + `HandsSystem`; passed `hands` to `NerveSystem`.

**New test files (5):**
* `tests/unit/systems/hands/test_hands_system.py` — 10 blocks (~38 tests).
* `tests/unit/systems/hands/test_hands_models.py` — 8 tests for the new validators.
* `tests/unit/systems/hands/test_hands_system_isolation.py` — AST guards.
* `tests/unit/adapters/win32/test_actions.py` — 5 blocks (~24 tests, platform-neutral via mocking).
* `tests/unit/adapters/win32/test_actions_isolation.py` — AST guards.

**Modified test files (5):**
* `tests/unit/systems/nerve/test_nerve_system.py` — extended `_build_nerve_system` fixture with `hands` kwarg; updated parametrized `test_route_command_dispatches_layer_b_routable` (dropped MODE/coding row, replaced by Block I); added Block I (8 mode_switch tests including total-failure-stays-None).
* `tests/unit/adapters/rich/test_skin_adapter.py` — added 4 `render_progress` tests.
* `tests/unit/ports/test_port_isolation.py` — added 3 tests (render_progress reshape, AppLauncherPort method shape, reason-vocabulary `__all__` membership).
* `tests/unit/test_composition_root.py` — added 3 wiring tests (Win32HandsAdapter + HandsSystem + NerveSystem hands= kwarg) AND added `nova.adapters.win32.actions` to the logger-allowlist.
* `tests/integration/test_session_loop.py` — appended 2 windows_only mode-restore integration tests with PID-tracked Notepad cleanup.

**Modified bmad artifacts (2):**
* `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 3.6 status `ready-for-dev` → `in-progress` → `review`.
* `_bmad-output/implementation-artifacts/deferred-work.md` — closed entries 137 + 146; retargeted entry 56; added 13 new deferred entries from same-session review.

### Change Log

* 2026-05-05 — Story 3.6 implementation complete; 1854 unit + 56 integration pass; 100% coverage on new modules. Same-session adversarial review (3 layers in parallel) ran; 7 patches applied (skin/event isolation, `_active_mode_name` total-failure handling, psutil basename normalization, psutil.Error broader catch, `os.startfile` POSIX guard, cross-mock ordering tests, per-app audit details shape test); 13 follow-ups deferred with explicit deferred-work.md entries.
* 2026-05-05 — Formal `/bmad-code-review` pass (3 fresh-context layers — Blind Hunter / Edge Case Hunter / Acceptance Auditor). 18 raw findings → 13 unique post-dedup. 0 decision-needed, 6 patches, 6 deferred, 1 dismissed. **All 6 patches applied + locked by new tests:** psutil per-proc except widened to include `ZombieProcess`/`OSError`; empty-target guard in `_iter_processes_for_match`; `ActionRequest.__post_init__` MappingProxyType always-wraps for isolation; `_active_mode_name` cleared on second-restore total-failure; AST guard now walks `body`/`orelse`/`finalbody`/handler bodies; new parametrized regression test asserts `CancelledError` propagates from each isolated surface. Final: 1861 unit pass + 56 integration pass; 100% coverage holds; ruff + format + mypy strict all clean. Status: review → done.
* 2026-05-05 — **User-reported HIGH post-review fix.** All three /bmad-code-review layers missed a Story 3.4 contract regression: `_handle_mode_switch` used `command.target` verbatim as the `NovaConfig.modes` dict key, breaking the case-insensitive-lookup contract (Story 3.4 spec line 406). User-typed `mode Coding` → parser preserves casing in `target="Coding"` → my lookup missed the lowercase `"coding"` config key → "No mode named 'Coding'" instead of restoring. **Fix:** `mode_stem = command.target.lower()` for the canonical lookup + downstream identity; error template echoes `command.target` (original casing) so the user recognizes their input. Locked by 2 new tests covering both halves of the contract. 1863 unit pass + 56 integration pass.
