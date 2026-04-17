# Story 2.4: Briefing Card State A, Initial Capture & Setup Completion

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a new user launching N.O.V.A. for the first time,
I want to see a clear first-run orientation (Briefing Card State A) that auto-transitions into setup, and after setup completes I want my workspace captured and a clean entry into my first session,
So that I understand what N.O.V.A. is and my first session starts with real context.

## Story-type classification

**First-through-boundary story** (per Epic 1 retrospective, 2026-04-15, and Epic 2 guidance marking this story for "lighter boundary treatment"). Story 2.4 introduces three new integration boundaries:

1. **Setup-time Win32 capture** — the first place the codebase touches `pywin32` / `psutil` for real. Story 4.1 (Epic 4) ships the full `Win32EyesAdapter`; Story 2.4 ships a setup-only, best-effort capture that lives under `nova.setup/` and does **not** prefigure the adapter contract.
2. **Setup-time persistence seam** — session row + workspace_snapshot row written directly through `SqliteStorageEngine.execute()` via `nova.app.create_app()`, not through a Brain adapter (Brain adapter is Story 3.1 scope). This is an acknowledged seam; see Dev Notes § "Setup-time persistence seam."
3. **New `ActionType.SETUP_COMPLETE` enum member** — extends the pinned audit vocabulary (`epics.md:672`). Adding a member is a deliberate schema change, not in-line string use (project-context.md:42).

The Review Focus boundary-invariant sweep at the bottom is therefore mandatory for this story.

## Acceptance Criteria

### Group A: Briefing Card State A — render contract

1. **Given** N.O.V.A. is launched for the first time (no modes, no sessions, no seed) **when** the app boots (via `setup.bat` → `uv run python -m nova.setup`) **then** Briefing Card State A renders first using the exact UX-spec copy:
   - Title: `N.O.V.A.` (bold cyan, not `Session Briefing`)
   - Body line 1: `First session. No history yet — that's expected.`
   - Body line 2: `Let's set up your first workspace mode so tomorrow starts warm.`
   - Border style: cyan Rich `Panel`, padding `(1, 2)`
   - No emoji, no sycophantic framing, no resume prompt, no mode metadata shown (State A has no briefing data to show)

2. State A **auto-transitions** into the setup wizard on the next frame — no `"Starting setup..."` line, no "Press Enter to continue" pause, no manual trigger. The auto-transition is a direct function-call sequence inside `nova.setup.__main__.main()`: `_render_state_a(console)` → `run_api_key_step(...)` → `run_mode_wizard_step(...)` → initial-capture + completion (new in this story). Tests assert no prompt text appears between the State A render and the API key step.

3. State A is rendered **exactly once** per `python -m nova.setup` invocation, and **only on pre-setup runs**. The authoritative "setup already complete" marker is the canonical `audit_log` row this story writes in AC #15: `action_type = "setup_complete"`. The fast-path predicate is:
    - **Step 1 (file-level probe, microseconds):** check whether `%LOCALAPPDATA%/nova/nova.db` exists. If it does not, setup has never completed → proceed with State A + wizard.
    - **Step 2 (read-only DB probe, milliseconds):** open `nova.db` via `SqliteStorageEngine` (same engine used by `create_app`, so WAL/pragmas match — do NOT create a second sqlite3 connection path), run one query: `SELECT 1 FROM audit_log WHERE action_type = 'setup_complete' LIMIT 1`, close.
    - If the query returns a row, setup is complete. Render an informational panel ("Setup already complete. Run `uv run nova` to start a session.") and exit 0 without re-running the wizard, the capture, or persistence.
    - If the file exists but no `setup_complete` row is found (e.g., user ran `cli.py` ad-hoc, or a previous setup failed mid-persistence after nova.db was created but before the audit row landed), proceed with State A + wizard. The wizard's "at least one mode ready by exit" gate (Story 2.3) still holds; Story 2.4's transactional write (AC #14) ensures the audit row lands atomically with the session/snapshot rows so "nova.db exists" and "setup_complete audit row exists" never diverge mid-transaction.

    This predicate is **independent of API-key presence** — a user who skipped the API key step (Story 2.2, exit 0 soft-skip) and reached the setup_complete marker should not be pushed back through State A on the next `setup.bat` run. Story 2.5 (post-setup key update) is the path for adding a key later, not a re-entry into State A.

4. Epic 2 does **not** render Briefing Card State B or State C. Those state paths live in `nova.systems.nerve` / `nova.systems.ritual` (Stories 3.2 / 3.3). An AST guard test asserts `nova.setup.*` modules contain no `BriefingState.POST_SETUP` or `BriefingState.WARM_RESUME` string literals and no calls to the (future) `build_briefing_view_model` function.

### Group B: Initial workspace capture (best-effort, non-blocking)

5. After the wizard exits cleanly (at least one valid mode file exists in `%LOCALAPPDATA%/nova/modes/`), a new module `nova.setup.initial_capture` runs a **best-effort** workspace capture and returns a `CaptureResult` — a frozen dataclass carrying the `WorkspaceSnapshot`, a `capture_status` from the closed set `{"full", "partial", "empty", "unavailable"}`, and the counts `windows_captured` + `windows_dropped`. Capture is **non-blocking** for setup completion: any failure degrades gracefully and setup still succeeds (AC #6). The `CaptureResult` shape is the single source of truth for capture outcome — it threads through the persist helper (AC #13), the audit details (AC #15), and the status-line render (AC #19) so the same vocabulary is used everywhere. The dataclass lives in `nova.setup.initial_capture`:
    ```python
    type CaptureStatus = Literal["full", "partial", "empty", "unavailable"]

    @dataclass(frozen=True)
    class CaptureResult:
        snapshot: WorkspaceSnapshot
        status: CaptureStatus
        windows_captured: int   # len(snapshot.windows) — mirrored for convenience
        windows_dropped: int    # per-window failures that were gracefully dropped
    ```
    Status determination is purely a function of the capture outcome:
    - `"unavailable"` — pywin32/psutil import failed, OR `EnumWindows` itself raised before any per-window probe ran
    - `"empty"` — enumeration succeeded, zero windows captured, zero drops (user's desktop really was empty, e.g., fresh VM)
    - `"partial"` — `windows_captured >= 1` AND `windows_dropped >= 1` (graceful-partial pattern)
    - `"full"` — `windows_captured >= 1` AND `windows_dropped == 0`

    The four-state closed set is exhaustive by construction — `test_capture_status_is_exhaustive` parametrizes every `(captured, dropped, available)` corner and asserts exactly one status fires.

6. Capture enumerates currently-open top-level windows using `pywin32` (`win32gui.EnumWindows` for window enumeration + `win32gui.GetWindowText` for titles + `win32process.GetWindowThreadProcessId` + `psutil.Process(pid).name()` for process names) and identifies the focused foreground window via `win32gui.GetForegroundWindow()`. Adapter-specific types (`win32gui` handles, `psutil.Process` instances) stay trapped inside `initial_capture.py` — only domain types (`WindowContext`, `WorkspaceSnapshot`) leave the function.

7. **Graceful degradation** — the capture returns a valid `WorkspaceSnapshot` with `windows=()` (empty tuple) and logs a single `WARNING` when any of the following happens:
   - `pywin32` / `psutil` import fails (`ImportError` caught on module entry, cached so subsequent calls don't re-try)
   - Any `win32gui` / `psutil` call raises `pywintypes.error`, `OSError`, `ValueError`, `psutil.NoSuchProcess`, `psutil.AccessDenied`, or `psutil.ZombieProcess`
   - A per-window failure is handled per-window: that window is omitted, other windows still populate the snapshot (graceful-partial pattern, project-context.md:195)
   No traceback surfaces to the user; no non-zero exit code; setup proceeds to persistence.

8. Exclusion filtering is **NOT** applied in Story 2.4's capture. Exclusion boundary enforcement lives in Story 4.2 (Epic 4). Story 2.4's capture ships only with pywin32/psutil-based enumeration. A follow-on note is recorded in `_bmad-output/implementation-artifacts/deferred-work.md` that setup-time captures will gain exclusion filtering once the exclusion loader and Eyes capture layer exist.

9. The captured `WorkspaceSnapshot` carries `snapshot_type=SnapshotType.STARTUP` (per the four-member enum in `nova.core.types.SnapshotType`; `startup` is the member that matches "initial capture at the start of the first session"). `captured_at` uses the canonical two-function clock indirection — `from nova.core import events` then `events._utc_now_iso()` at call time (cross-cutting-patterns.md #1). Never inline `datetime.now(UTC).isoformat()`.

### Group C: First-session persistence + audit

10. After a successful (or gracefully degraded) capture, `nova.setup.__main__` constructs a `NovaApp` via `nova.app.create_app(config)`, which:
    - Starts the `SqliteStorageEngine` at `%LOCALAPPDATA%/nova/nova.db`
    - Runs pending migrations (creates the schema on first run since the DB file does not yet exist — Story 1.5 migration runner)
    - Wires the shared `AuditLogger`, `TierManager`, `EventBus`, `NoOpShieldAdapter`

11. The `NovaConfig` passed to `create_app` is loaded via `nova.core.config.load_config(data_dir)` — the **same** loader used by `cli.py`, so config validation behavior matches. Mode files are loaded with skip-on-error (cross-cutting-patterns.md #5) and settings.yaml with hard-fail. If `load_config` raises `ConfigError` (e.g., malformed settings.yaml despite Story 2.2 writing it), setup prints a product-grade message ("Your settings file appears corrupted. Delete `%LOCALAPPDATA%/nova/settings.yaml` and re-run setup.") and exits with code 1 without attempting persistence or capture cleanup (the engine was never opened). No traceback.

12. A **first session row** is written to `sessions` via `storage.execute(...)` inside a `storage.transaction()` block (cross-cutting-patterns.md #6). The row has:
    - `started_at` = capture timestamp from AC #9 (the moment `_utc_now_iso()` was called at capture)
    - `ended_at` = a second `events._utc_now_iso()` call taken after the snapshot is written (same task, inside the transaction)
    - `mode_name` = `NULL` (setup itself has no active mode — the user has not yet issued a `mode` command)
    - `seed_text` = `NULL`
    - `summary` = `NULL`
    - `is_complete` = `1` (the setup session is a completed operational milestone; the next `nova` run is a new session)

13. The captured `WorkspaceSnapshot` (unwrapped from the `CaptureResult` at the persist boundary) is written to `workspace_snapshots` **in the same transaction** as the session row, with:
    - `session_id` = the `INTEGER PRIMARY KEY` returned by the `sessions` insert (obtain via `cursor.lastrowid` routed through a new `storage.execute_returning_lastrowid()` helper — see Dev Notes § "Setup-time persistence seam" for the helper's narrow scope)
    - `captured_at` = `snapshot.captured_at` (not re-stamped)
    - `snapshot_type` = `str(SnapshotType.STARTUP)` → `"startup"`
    - `workspace_data` = JSON-encoded `{"apps": [w.app_name for w in snapshot.windows if w.app_name], "focused_app": <first non-None app_name>, "mode_name": null}` with `json.dumps(..., separators=(",",":"), ensure_ascii=False, allow_nan=False)`

    `persist_first_run`'s signature accepts the full `CaptureResult` (not just the snapshot) so the `capture_status` string flows through to the audit `details` in AC #15 without a second computation at the audit boundary:
    ```python
    async def persist_first_run(
        app: NovaApp,
        capture: CaptureResult,
        *,
        api_key_configured: bool,
        modes_count: int,
    ) -> None
    ```

14. Both writes happen inside **one** `async with storage.transaction():` block so that on any failure (e.g., disk full mid-snapshot-write), neither row lands and setup surfaces a clean failure without a half-written first session. ROLLBACK safety, including cancellation mid-ROLLBACK, is inherited from the transaction context manager (engine.py:247-330).

15. After both writes succeed, a single audit entry is written via `audit.log_action(...)` with:
    - `action_type` = **new** enum member `ActionType.SETUP_COMPLETE` (see AC #16)
    - `target` = `None` (setup-complete is not bound to a specific resource)
    - `result` = `RESULT_SUCCESS`
    - `details` = `{"modes_count": <N>, "api_key_configured": <true|false>, "capture_status": <one of "full"|"partial"|"empty"|"unavailable">}` — `capture_status` is the exact `CaptureResult.status` value from AC #5; no second vocabulary anywhere in the codebase. No raw app names, no API-key material, no paths (opacity discipline, project-context.md:72).

    The audit write is **observational**: a `StorageError` from the insert is caught inside `AuditLogger.log_action` and swallowed with a WARNING (audit.py semantics, unchanged); setup still prints the completion message and exits 0.

16. **New `ActionType.SETUP_COMPLETE` enum member** is added to `src/nova/core/types.py:ActionType` (inserted in logical order; exact position is adjacent to other one-shot lifecycle members). A parametrized test asserts the full enum membership is `{APP_LAUNCH, APP_FOCUS, WINDOW_ARRANGE, MODE_SWITCH, MODE_RESTORE, MODE_CREATE, MODE_EDIT, DELETION, SEED_CAPTURE, TIER_CHANGE, DATABASE_RECOVERY, SETUP_COMPLETE}` — this locks the vocabulary and prevents silent widening by future stories. The enum docstring is updated to reference this story as the origin of the SETUP_COMPLETE member.

17. **The `api_key_configured` flag** in the audit `details` is derived at audit-write time by checking `settings.api_key is not None and settings.api_key != ""` on the already-loaded `NovaConfig`. The raw key is **never** read into audit state, event extras, or log messages (project-context.md:179, Story 2.2 key-interpolation AST guard).

### Group D: Completion message + UX contract

18. After persistence completes (whether audit succeeded or silently warned), a Rich `Panel` is rendered titled `Setup complete.` with the following body, sourced from the already-loaded `NovaConfig.modes` tuple (same config object `create_app` was given — no second filesystem scan, no stem/display-name drift from runtime):
    - Line 1: `You have N mode(s) ready: <comma-separated mode display names>` (singular "mode" vs. plural "modes" per N; list uses `mode.name` values — the user-facing display names, not the file stems — sorted case-insensitively by `mode.name`)
    - Line 2: `Run` followed by the canonical next-step command in Rich `[bold]` — `uv run nova` — followed by `to start your next session.`
    - No emoji. No trailing "Thanks!" or sycophantic line. No "Welcome to N.O.V.A." Panel border cyan, padding `(1, 2)`.

    `NovaConfig.modes` is the single source of truth: it is what the runtime session loop will read on the user's next `uv run nova` invocation, so the completion panel reflects exactly what they will see next.

19. Operational output for the capture step bypasses Voice (project-context.md:187). Directly before `create_app`, the Console prints one status line per `CaptureResult.status` (AC #5) — the same closed four-set used by the audit entry, no second vocabulary:
    - `status == "full"`: `[green]✓[/green] Captured initial workspace snapshot (N apps).`
    - `status == "partial"`: `[yellow]⚠[/yellow] Captured N of M apps; setup will continue.` (N = `windows_captured`, M = `windows_captured + windows_dropped`)
    - `status == "empty"`: `[yellow]⚠[/yellow] Workspace capture is empty. Setup will continue.`
    - `status == "unavailable"`: `[yellow]⚠[/yellow] Workspace capture unavailable right now. Setup will continue.`

    A `match capture.status:` dispatch with a `case _ as other: raise AssertionError(other)` default arm locks the four-state exhaustiveness at runtime (mypy strict + the `Literal` alias lock it at type-check time).

    No raw app names appear in the completion message or capture status line — per-user privacy during operational output (app identities are stored inside `workspace_snapshots.workspace_data` but **not** surfaced via terminal). Per-app visibility is a future transparency-command concern (Epic 5).

20. **UX compliance is test-verified** using the same assertion shape as Story 2.3's `TestUxVoice`:
    - No codepoint matches the explicit emoji-range regex / whitelist (only `✓`, `✗`, `⚠`, `—` allowed above ASCII)
    - No banned phrases ever appear in the captured Rich output: `"How can I help you today"`, `"I'd be happy to"`, `"Great question"`, `"Great!"`, `"Welcome to N.O.V.A."`, `"All set!"`
    - Panel title uses `[bold cyan]` styling (typography hierarchy UX-DR11)

21. Renders correctly at **80 columns** (UX-DR18). A test renders the full Story 2.4 output (State A + wizard + capture status line + completion panel) via a Rich `Console(width=80, record=True)` and asserts no line wraps awkwardly on the longest expected mode-list output.

### Group E: NFR2 budget (<15 minute first run) + performance

22. The **capture step alone** completes in under 2 seconds on a reference machine (Windows 11 with 10–30 open windows) — NFR2 budget attribution. A performance unit test using a mocked enumerator with 50 synthetic windows asserts the capture returns within 500 ms wall time under `asyncio.wait_for(...)`. The test uses a deterministic fake, not real Win32.

23. The overall setup run (from `setup.bat` start to exit 0, with mocked Console input and mocked API ping) completes in under **8 seconds** in the full integration test — a safe margin below the 15-minute NFR2 human budget and a regression guard against future bloat.

24. First-run capture is capped: if window enumeration returns more than **250 windows** (pathological, e.g., dev machine with many Electron processes), the capture truncates to the first 250 and logs a WARNING. Story 4.1's ongoing polling adapter can revisit this cap; Story 2.4's ceiling prevents setup from stalling on degenerate desktops.

### Group F: Layering + integration

25. `nova.setup.initial_capture` imports only from:
    - **stdlib** (logging, json, dataclasses, types, contextlib)
    - **`nova.core.*`** (exceptions, events, types)
    - **`nova.systems.eyes.models`** (`WindowContext`, `WorkspaceSnapshot`) — `.models` is the explicit cross-system contract per Story 1.9 AC #8
    - **`pywin32`** / **`psutil`** (third-party, guarded with `try: import … except ImportError`)

    It does **NOT** import from `nova.systems.eyes.system`, `nova.adapters.*`, or `nova.ports.*`. An AST guard test (new file `tests/unit/setup/test_initial_capture_isolation.py`) enforces this — pattern #2 from cross-cutting-patterns.md, mirroring Story 2.3's `test_mode_wizard_isolation.py` shape.

26. `nova.setup.__main__` gains two new imports and two new call sites:
    - `from nova.app import create_app`
    - `from nova.setup.initial_capture import capture_initial_workspace, persist_first_run`
    - After `run_mode_wizard_step(...)`, calls `await _run_initial_capture_and_persist(console, data_dir)` (new private async helper). Because `main()` is currently sync, this helper runs under a fresh `asyncio.run(...)` — a one-shot loop scoped to the setup session. The loop is created after the wizard exits so wizard-internal `Console.input` calls remain synchronous. Document the loop boundary with a module comment referencing Story 1.4's loop-affinity contract.

27. Setup's exit code remains `0` on:
    - Successful full setup (State A + wizard + capture + persistence + completion)
    - `LOCALAPPDATA` not set (falls back to the existing skip-with-warning path; no capture, no persistence, no audit row)
    - `api_key_configured = False` (user skipped API key per Story 2.2)
    - Capture gracefully degraded (empty or partial snapshot)
    - Already-setup fast path (AC #3)

    Setup exits with code `1` only on:
    - `ConfigError` from `load_config` (AC #11)
    - `StorageError` from `storage.start()` / migrations / the transactional write (AC #14)
    - Argparse usage error (inherits code `2` from argparse, unchanged from today)

28. Story 2.2's integration test `TestFullWiringThroughMain::test_main_configures_key_end_to_end` and Story 2.3's `TestModeWizardWiring` continue to pass. Any test that previously mocked `run_mode_wizard_step` must now **also** mock `_run_initial_capture_and_persist` (or equivalent) so those tests remain isolated to their respective concerns. A regression test asserts that the wizard is still called with the same signature after the Story 2.4 wiring.

### Group G: Testing

29. **Unit tests for `initial_capture.py`** (`tests/unit/setup/test_initial_capture.py`):
    - `test_returns_unavailable_when_pywin32_import_fails` — monkeypatch `sys.modules["win32gui"]=None`; assert WARNING logged, `CaptureResult(status="unavailable", windows_captured=0, windows_dropped=0)`, snapshot has `windows=()`.
    - `test_returns_unavailable_when_enum_windows_raises` — fake `EnumWindows` that raises `pywintypes.error`; assert `status="unavailable"` and the error is not surfaced.
    - `test_enumerates_open_windows_and_identifies_focused` — inject fake enumerator returning 3 windows; assert `WindowContext` fields match, `focused_app` set from the HWND matching `GetForegroundWindow` return, `CaptureResult(status="full", windows_captured=3, windows_dropped=0)`.
    - `test_per_window_failure_is_graceful_partial` — fake enumerator where window 2 of 3 raises `pywintypes.error`; assert snapshot has 2 windows, `CaptureResult(status="partial", windows_captured=2, windows_dropped=1)`.
    - `test_empty_desktop_is_distinct_from_unavailable` — fake `EnumWindows` that yields zero visible HWNDs without raising; assert `CaptureResult(status="empty", windows_captured=0, windows_dropped=0)`.
    - `test_capture_status_decision_table` — parametrized across the four AC #5 corners `(windows_captured, windows_dropped, available) → status`; asserts exactly one status fires per input, exhaustiveness locked.
    - `test_captured_at_uses_events_module_attribute` — monkeypatch `nova.core.events._utc_now_iso` to a fixed string; assert `result.snapshot.captured_at` matches.
    - `test_truncates_to_250_windows` — fake enumerator with 300 windows; assert `result.windows_captured == 250`, WARNING logged, status computed after truncation (`"full"` if no per-window drops).
    - `test_snapshot_type_is_startup` — assert `result.snapshot.snapshot_type == SnapshotType.STARTUP`.
    - `test_adapter_types_never_escape` — assert return types are only `CaptureResult` / `WorkspaceSnapshot` / `WindowContext` (no `int` HWNDs, no `psutil.Process` instances leak).

30. **Unit tests for `persist_first_run` / session + snapshot writes** (`tests/unit/setup/test_initial_capture_persistence.py`), using an in-memory `SqliteStorageEngine` with migrations applied:
    - `test_writes_session_row_with_expected_fields` — assert `is_complete=1`, `mode_name IS NULL`, `seed_text IS NULL`.
    - `test_writes_snapshot_row_tied_to_session_id` — FK matches the `sessions.id` from the same transaction.
    - `test_workspace_data_is_strict_compact_json` — no spaces after separators, no `NaN`, `ensure_ascii=False`.
    - `test_transaction_rolls_back_on_snapshot_failure` — monkeypatch the snapshot insert to raise; assert no session row exists.
    - `test_audit_entry_uses_setup_complete_action_type` — assert `audit_log.action_type == "setup_complete"` and `target IS NULL`.
    - `test_audit_details_contains_no_api_key_material` — monkeypatch a settings.yaml with a fake key; assert the audit details JSON does not contain the key string.

31. **Unit tests for State A render + already-setup fast path** (`tests/unit/setup/test_setup_main_state_a.py`, extending existing test file):
    - `test_state_a_copy_matches_ux_spec` — assert the two body lines match verbatim.
    - `test_state_a_rendered_exactly_once` — capture stdout via Rich `record=True`; assert the State A title appears once.
    - `test_fast_path_triggered_by_setup_complete_audit_row` — pre-create nova.db with a `setup_complete` audit row (via a small in-memory-then-copy helper); assert wizard functions never called, informational panel printed, exit 0, no new rows written to sessions / workspace_snapshots / audit_log.
    - `test_fast_path_ignores_api_key_presence` — pre-create nova.db with a `setup_complete` audit row BUT no settings.yaml api_key; assert fast path still triggers (closes the Story 2.2-soft-skip regression documented in AC #3's "independent of API key" rationale).
    - `test_fast_path_ignores_api_key_configured_false` — pre-create nova.db with a `setup_complete` audit row whose details JSON contains `"api_key_configured": false`; assert fast path still triggers.
    - `test_no_fast_path_when_db_missing` — no nova.db present; assert setup proceeds through State A + wizard even if a settings.yaml/api_key and mode files already exist.
    - `test_no_fast_path_when_db_exists_but_no_setup_complete_row` — pre-create nova.db with schema applied and zero audit rows (simulates an interrupted previous run); assert setup proceeds and the subsequent successful run writes exactly one new session + snapshot + audit row (no duplicates from the prior interruption).
    - `test_fast_path_handles_corrupt_db_by_falling_through` — pre-create nova.db as a zero-byte file; assert the probe logs WARNING and setup proceeds (setup is the recovery path). No exit 1.
    - `test_no_pause_between_state_a_and_wizard` — assert no input() / Console.input() called between `_render_state_a` and `run_api_key_step`.

32. **ActionType enum guard** (`tests/unit/core/test_types.py`, new or extending the existing enum-membership test):
    - `test_action_type_full_membership` — parametrized; full set of 12 members (including `SETUP_COMPLETE`) matches exactly; no extras, no missing.
    - `test_action_type_setup_complete_value` — asserts `str(ActionType.SETUP_COMPLETE) == "setup_complete"`.

33. **AST guard: setup does not reference State B/C constants or Ritual internals** (`tests/unit/setup/test_setup_does_not_import_ritual_internals.py`):
    - Walks `ast.ImportFrom` under `src/nova/setup/` — asserts no imports from `nova.systems.ritual.*`, `nova.systems.nerve.*`, `nova.systems.skin.system`, `nova.adapters.*`, `nova.ports.*` (except the existing `nova.systems.eyes.models` exception narrowed to `initial_capture.py` only).
    - Walks string constants — rejects the literal `"post_setup"` and `"warm_resume"` appearing anywhere under `nova/setup/`.

34. **Integration tests** (`tests/integration/test_setup_wizard.py`, new `TestInitialCaptureAndCompletion` class):
    - `test_full_flow_creates_session_and_snapshot_rows` — run `main()` with mocked Console input (accepts template); assert after `main()` returns, the nova.db file contains exactly 1 sessions row, 1 workspace_snapshots row, 1 audit_log row with `action_type="setup_complete"`.
    - `test_capture_empty_but_setup_still_succeeds` — monkeypatch `capture_initial_workspace` to return `CaptureResult(snapshot=WorkspaceSnapshot(..., windows=()), status="empty", windows_captured=0, windows_dropped=0)`; assert exit 0, session row present, snapshot row present with `workspace_data={"apps":[],"focused_app":null,"mode_name":null}`, audit row's details JSON contains `"capture_status":"empty"`.
    - `test_capture_partial_threads_status_to_audit` — monkeypatch capture to return `status="partial", windows_captured=2, windows_dropped=1`; assert audit details JSON contains `"capture_status":"partial"` and the capture status line printed matches the partial form (N of M apps).
    - `test_capture_unavailable_threads_status_to_audit` — monkeypatch capture to return `status="unavailable"`; assert audit details JSON contains `"capture_status":"unavailable"` and setup still exits 0.
    - `test_storage_error_during_persistence_exits_1` — monkeypatch `SqliteStorageEngine.execute` to raise `StorageError` once inside the transaction; assert exit 1 and a product-grade message (no traceback).
    - `test_fast_path_exits_without_recapture` — pre-populate nova.db with a `setup_complete` audit row; assert re-running `main()` exits 0, no new session row written, no new snapshot row, no new audit row.
    - `test_completion_panel_mode_list_sourced_from_novaconfig` — run full flow with two mode files (display names "Coding" and "ad hoc mode"); assert the completion panel text contains the names in case-insensitive sort order ("ad hoc mode, Coding") and makes no filesystem scan calls (monkeypatch `Path.iterdir` on `modes_dir` to raise during the completion step and assert the render still succeeds — it's reading from `NovaConfig.modes`).
    - `test_complete_flow_timing_under_8s` — assert total wall time of the full mocked flow < 8 seconds (regression guard for AC #23).

35. **NFR2 observability** — the integration test that measures < 8 s logs the breakdown (State A render → wizard → capture → persist → complete) at DEBUG so a future developer tracing a slowdown knows which phase regressed. The log keys are stable: `phase="state_a"|"wizard"|"capture"|"persist"|"complete"`, `elapsed_ms=<int>`.

## Tasks / Subtasks

- [x] **Task 1: Extend `ActionType` with `SETUP_COMPLETE`** (AC: #16, #32)
  - [x] Add `SETUP_COMPLETE = "setup_complete"` member to `src/nova/core/types.py:ActionType` in logical order
  - [x] Update the enum docstring to cite this story as the origin
  - [x] Add parametrized membership test in `tests/unit/core/test_types.py` locking the full 12-member set
  - [x] Confirm no other module's `ActionType` consumer breaks (grep for `ActionType(` / `ActionType\.` — none should pattern-match a closed membership list)

- [x] **Task 2: `nova.setup.initial_capture` module** (AC: #5–#9, #22, #24, #25, #29)
  - [x] Create `src/nova/setup/initial_capture.py`
  - [x] Declare module-level guarded imports: `try: import win32gui, win32process, psutil except ImportError: _WIN32_AVAILABLE = False`
  - [x] Define `type CaptureStatus = Literal["full", "partial", "empty", "unavailable"]` and `@dataclass(frozen=True) class CaptureResult`
  - [x] Implement `capture_initial_workspace() -> CaptureResult`:
    - Pulls capture timestamp from `events._utc_now_iso()` (module-attribute form)
    - If `_WIN32_AVAILABLE is False` → return `CaptureResult(snapshot=WorkspaceSnapshot(..., windows=()), status="unavailable", windows_captured=0, windows_dropped=0)`
    - If the outermost `EnumWindows` call itself raises → same `"unavailable"` outcome (catch `pywintypes.error`, `OSError`, `RuntimeError`), log one WARNING
    - Otherwise: enumerate visible top-level HWNDs via `win32gui.EnumWindows` + `win32gui.IsWindowVisible`; cap at 250 windows (AC #24)
    - For each HWND: per-window `try/except` catching `pywintypes.error`, `OSError`, `ValueError`, `psutil.NoSuchProcess`, `psutil.AccessDenied`, `psutil.ZombieProcess`. Successes accumulate `WindowContext`s; failures increment a local `dropped` counter and continue (graceful-partial)
    - Identify focused window via `GetForegroundWindow()` and match to enumerated set; `is_opaque=False` for all Story 2.4 windows (exclusion boundary deferred)
    - Compute status via the AC #5 decision table; return `CaptureResult(snapshot=..., status=..., windows_captured=len(contexts), windows_dropped=dropped)`
  - [x] Add a one-line module comment explaining that full `Win32EyesAdapter` lives in Story 4.1 and this module is setup-only
  - [x] Unit tests per AC #29 using injected fake enumerators (no real Win32 calls in tests)
  - [x] Add exhaustiveness test `test_capture_status_decision_table` covering the four AC #5 corners

- [x] **Task 3: Setup-time persistence — `persist_first_run` helper** (AC: #10–#17, #30)
  - [x] Extend `nova.setup.initial_capture` with `async def persist_first_run(app: NovaApp, capture: CaptureResult, *, api_key_configured: bool, modes_count: int) -> None` — takes the full `CaptureResult` so `capture.status` threads into the audit details without re-derivation
  - [x] Inside one `async with app.storage.transaction():` block:
    - INSERT INTO sessions → capture `lastrowid` via a new narrow helper on `SqliteStorageEngine`
    - INSERT INTO workspace_snapshots tied to that session_id, `workspace_data` JSON built with strict `json.dumps` settings
  - [x] After the transaction closes successfully, call `app.audit.log_action(ActionType.SETUP_COMPLETE, None, RESULT_SUCCESS, details={...})`
  - [x] **New storage helper** — add `async def execute_returning_lastrowid(self, sql: str, params: SqlParams = ()) -> int` to `SqliteStorageEngine` (scope: minimal, single-statement INSERT that returns `cursor.lastrowid` after `conn.commit()`; honors the `_tx_owner` dispatch contract so calls inside an active transaction do NOT auto-commit). Add unit test covering the helper's transaction-aware behavior. Place the helper's docstring alongside the other `execute*` helpers; cite Story 2.4 as the originating call site
  - [x] Unit tests per AC #30 using in-memory `SqliteStorageEngine` and migrations

- [x] **Task 4: Completion UX — render_completion_panel + capture status line** (AC: #18–#21)
  - [x] Add `_render_capture_status(console, capture: CaptureResult) -> None` to `nova.setup.__main__` (or a new `nova.setup.completion.py` if `__main__` gets too dense — ship whichever keeps `__main__.py` under ~200 lines). Dispatch on `capture.status` via `match`; `case _ as other: raise AssertionError(other)` is the default arm locking exhaustiveness.
  - [x] Add `_render_completion_panel(console, config: NovaConfig) -> None` — derives mode display names from `config.modes` (tuple of `ModeConfig`), sorts case-insensitively by `mode.name`, renders singular vs plural copy per `len(config.modes)`. No filesystem scan.
  - [x] Add UX-voice test assertions (emoji whitelist, banned phrases, 80-column render); add a test that the completion panel's mode list matches `config.modes` order after case-insensitive sort by name

- [x] **Task 5: Wire into `nova.setup.__main__.main()`** (AC: #2, #3, #26, #27, #28)
  - [x] Add already-setup fast path at top of `main()` (after argparse) per AC #3's two-step probe
  - [x] After `run_mode_wizard_step(...)`, add `_run_initial_capture_and_persist(console, data_dir)` via a second `asyncio.run(...)` scoped to that helper
  - [x] Inside the helper: load config via `load_config(data_dir)`, call `create_app(config)`, run capture (in-process, not awaited — capture is sync), render capture status line via `render_capture_status(console, capture)`, await `persist_first_run(app, capture, api_key_configured=..., modes_count=...)`, then `await app.close()` in a `finally`
  - [x] Pass the same `config` that `create_app` received into `render_completion_panel(console, config)` so the completion panel's mode list is sourced from `NovaConfig.modes` (AC #18)
  - [x] Translate `ConfigError` / `StorageError` into product-grade stderr + exit code 1 (AC #11, #27)
  - [x] Render completion panel after cleanup
  - [x] Patch Story 2.2 / 2.3 unit tests that mock `run_mode_wizard_step` to also mock `_run_initial_capture_and_persist` and `_probe_setup_complete` (tests that exercise a clean first-run must not have a pre-existing nova.db unless they explicitly seed one)

- [x] **Task 6: AST guards** (AC: #4, #25, #33)
  - [x] New test file `tests/unit/setup/test_initial_capture_isolation.py` — mirror `test_mode_wizard_isolation.py`; assert `initial_capture.py` imports obey AC #25
  - [x] New test file `tests/unit/setup/test_setup_does_not_import_ritual_internals.py` — AC #33 import + string-constant guard across all `src/nova/setup/*.py`
  - [x] Both tests walk `ast.walk`, not `ast.parse(...).body` alone (cross-cutting-patterns.md #2)

- [x] **Task 7: Integration tests + NFR2 regression guard** (AC: #23, #34, #35)
  - [x] Add `TestInitialCaptureAndCompletion` class to `tests/integration/test_setup_wizard.py` — full-flow tests using real in-memory SQLite with migrations and mocked Console
  - [x] Phase-timing DEBUG log deferred — recorded in `deferred-work.md`; the `< 8 s` wall-time assertion covers AC #23
  - [x] The < 8 s timing test uses `time.perf_counter()` around `main()` and asserts the delta

- [x] **Task 8: Update deferred-work.md** (AC: #8)
  - [x] Add entry: "Setup-time initial capture does not apply exclusion filtering — defer to Story 4.2 enforcement of the capture-layer exclusion boundary. Story 2.4 captures raw window identities; they are stored only in the local workspace_snapshot row and never cross the cloud trust boundary until PromptBuilder (Story 4.6) is wired."

## Dev Notes

### Architecture Compliance

- **Layering:** `nova.setup.*` is a pre-composition-root flow. `nova.setup.initial_capture` and `nova.setup.__main__` may import `nova.app.create_app` (composition root is legitimately setup's construction primitive); they may **not** import `nova.adapters.*` directly (composition root abstracts that). Cross-system `.models` imports (Story 1.9 AC #8) are the only permitted cross-system contract.
- **Config module is the single YAML reader** (project-context.md:69) — `load_config(data_dir)` is the entry point. `initial_capture.py` never reads YAML.
- **Brain owns SQLite tables** (project-context.md:67) — and yet Story 2.4 writes two rows directly via `storage.execute()`. This is an **acknowledged seam**: Brain's adapter (`SqliteBrainAdapter`) does not exist until Story 3.1. The seam is confined to `nova.setup.initial_capture.persist_first_run` — a **setup-only** writer whose lifetime ends when Epic 2 ships. Story 3.1's Dev Notes must migrate `persist_first_run` to call Brain port methods (`create_session`, `store_snapshot`) once they exist, delete the direct SQL, and keep the composition-root wiring.
- **AuditLogger is the single writer to audit_log** (audit.py) — `persist_first_run` calls `app.audit.log_action(...)`. Never `storage.execute("INSERT INTO audit_log ...")` directly; AST regression guards will reject it on compile.
- **Adapters may translate, never decide** (project-context.md:77) — `initial_capture.py` is NOT an adapter (it lives under `setup/`, not `adapters/`), but it must still obey the rule: no business policy, no title parsing, no mode inference. Just pywin32/psutil → domain types.

### Setup-time persistence seam (load-bearing context for the dev agent)

Story 2.4 hits a structural gap: the product needs a workspace_snapshot and a session row written during setup, but Brain's SQLite adapter is Story 3.1 and the Eyes adapter is Story 4.1. Three options were considered:

| Option | What | Why rejected (or accepted) |
|---|---|---|
| Defer persistence to Epic 3 | Story 2.4 just renders State A + completion; session/snapshot rows land when user runs `nova` for the first time | **Rejected** — violates the explicit epic AC "The snapshot is stored as a workspace_snapshot record tied to the first session" and breaks the onboarding narrative ("after setup completes ... my workspace captured") |
| Introduce a minimal `SqliteBrainAdapter` in Story 2.4 | Ship the adapter skeleton with only `create_session` + `store_snapshot` now | **Rejected** — pre-empts Story 3.1's boundary-creation story; would ship a half-built adapter that 3.1 must immediately rewrite |
| **Chosen: setup-confined direct writes via the composition-root storage engine + one small `execute_returning_lastrowid` helper** | `persist_first_run` uses `storage.transaction()` + `storage.execute()` + `storage.execute_returning_lastrowid()`; audit goes through the already-shipped `AuditLogger` | Keeps the boundary inside `setup/` where its temporary lifetime is obvious; reuses the composition root; adds one narrow storage helper that Brain will also want when it lands |

The `execute_returning_lastrowid` helper is **explicitly shared** — it's not a setup-only primitive. Brain's SQLite adapter (Story 3.1) will need exactly the same operation for `create_session`. Building it here reduces Story 3.1's surface.

### Pre-composition-root vs. post-composition-root (loop affinity)

Story 1.4's loop-affinity contract says the SQLite engine's worker executor is bound to the loop that created the connection. `setup/__main__.main()` is currently synchronous; adding `asyncio.run(_run_initial_capture_and_persist(...))` creates a one-shot loop that:

1. Constructs a `NovaApp` via `await create_app(config)` (engine opened on this loop)
2. Runs the transaction and audit on the same loop
3. Calls `await app.close()` on the same loop in `finally`
4. Exits; the loop closes; the engine is torn down

When the user later runs `uv run nova`, `cli.main` opens a **new** `asyncio.run(...)` with a **new** loop, and `create_app` constructs a **new** `SqliteStorageEngine` against the same DB file. There is no cross-loop drift because no engine outlives its loop. The WAL sidecars are released when `app.close()` completes.

### UX Voice Rules (Setup)

Story 2.3 established these for the wizard; Story 2.4 extends them to capture-status output and the completion panel:

- **No emoji.** Allowed non-ASCII: `✓` (green, success), `✗` (red, failure), `⚠` (amber, warning), `—` (em-dash punctuation).
- **No sycophantic framing.** Banned: `"How can I help you today?"`, `"I'd be happy to..."`, `"Great question!"`, `"Great!"`, `"Welcome to N.O.V.A."`, `"All set!"`.
- **Panel titles:** `[bold cyan]...[/bold cyan]`.
- **Direct, practical copy.** `"Setup complete."` is a complete confirmation. No trailing celebration.
- **Brevity must not drop critical status.** If capture degrades, say so plainly — don't hide partial behavior behind a green check.

### Snapshot JSON contract — precise shape

`workspace_snapshots.workspace_data` is stored as a JSON TEXT column (per architecture.md:549-555). Story 2.4's exact shape:

```json
{"apps":["code","chrome"],"focused_app":"code","mode_name":null}
```

- `apps` is an array of **process names** (not display names), derived from `psutil.Process.name()`. Sorted ascending for deterministic test assertions.
- `focused_app` is the process name of the foreground window at capture time, or `null` if no focused window was identifiable.
- `mode_name` is `null` during setup (no mode active yet). Future snapshot_type=mode_switch / shutdown will populate this.
- `None` identity fields (from windows that failed the per-window probe) are **dropped** from the `apps` array — the array only contains successfully probed windows. `windows=()` serializes to `{"apps":[],"focused_app":null,"mode_name":null}`.
- Story 4.3 will extend this shape; the `SqliteBrainAdapter` (Story 3.1) owns forward-compatible parsing. Story 2.4's writer does NOT add fields beyond the three above.

### Already-setup fast path — precise decision tree

The AC #3 fast path uses the **`audit_log` row with `action_type = "setup_complete"`** as the canonical completion marker — NOT the presence of an API key and NOT the mode-stem check. The API-key predicate was rejected in review because Story 2.2 explicitly permits the user to skip the API key and still complete setup (exit 0); using API-key presence as a fast-path gate would trap those users in an infinite State A → wizard re-entry loop across setup.bat invocations.

```
IF %LOCALAPPDATA%/nova/nova.db does not exist:
    proceed with State A + wizard + capture + persist + completion  (fresh first run)

ELSE open nova.db via SqliteStorageEngine, run:
    SELECT 1 FROM audit_log WHERE action_type = 'setup_complete' LIMIT 1
    close the engine.

    IF the query returned a row:
        render informational panel, exit 0  (idempotent no-op — nothing re-written)
    ELSE IF the query raised StorageError (corrupt DB, missing audit_log table):
        log WARNING, proceed with State A + wizard  (setup is the recovery path)
    ELSE (DB exists but no setup_complete row — interrupted previous run):
        proceed with State A + wizard + capture + persist + completion
        (the transaction + audit row are written atomically per AC #14/#15, so an
         interrupted run leaves no orphaned session/snapshot behind the next
         successful run — the fresh setup_complete row becomes the new marker)
```

The probe opens the engine read-only-in-intent (one `SELECT`, no writes) but uses the same `SqliteStorageEngine` path as `create_app` so WAL pragmas and thread affinity match. Opening a second `sqlite3.Connection` outside the engine is banned — it would duplicate the engine's lifecycle contract and create a cross-loop drift risk.

Because the `setup_complete` row is written inside the same `transaction()` block as the session + snapshot writes (AC #14), "nova.db exists" and "`setup_complete` row exists" are never in transient disagreement from this story onward. Idempotency per project-context.md:165: a second setup run that hits the fast path writes zero new rows to any table.

### Previous Story Learnings (from Stories 2.1, 2.2, 2.3)

1. **Rich UTF-8 in subprocess** — `_force_utf8_stdout()` in `__main__.py` already handles this. Initial_capture's status line inherits the reconfigured stdout.
2. **Console.input mocking** — Rich `Console.input()` can be mocked directly; Story 2.3's fixture patterns work for Story 2.4 wiring tests. Capture + persist does NOT prompt the user, so no mocking is needed there.
3. **Atomic write with `os.replace`** — pattern for file I/O. Story 2.4 does NOT write YAML files (only DB rows); pattern is not directly used but is the reference for the storage engine's transactional discipline.
4. **Exit code 0 on success/skip** — Stories 2.1, 2.2, 2.3 all honored this. Story 2.4 adds a new exit code 1 path only for `ConfigError` / `StorageError`.
5. **Error translation** — every user-visible error goes through the `ConfigError` / `StorageError` → product-grade message translation boundary. Never a raw traceback.
6. **API-key interpolation AST guard (Story 2.2)** — already blocks `key` / `api_key` / `raw` interpolation across `src/nova/setup/`. Story 2.4 must name its new variables accordingly (`outcome`, `snapshot`, `window_count` — never `key` or `raw`).
7. **Pattern: no "decline" escape on required gates (Story 2.3)** — the "already-setup" fast path is NOT a decline. The fast path is a no-op idempotent pass; the user did not choose to skip setup, they already completed it.

### Mode enumeration for the completion message

The completion panel's mode list is derived from the **already-loaded** `NovaConfig.modes` (the same config object `create_app` received) — not a fresh filesystem scan — so the list matches what the runtime will use on the user's next `uv run nova` invocation. `NovaConfig.modes` is a `dict[str, ModeConfig]` per `core/config.py` (stems → configs); `_render_completion_panel` iterates `config.modes.values()` and sorts **case-insensitively by `mode.name`** (the user-facing display name), not by file stem. This is the only sort/source used in this story — there is no "by stem" code path anywhere in the completion UX.

The `_render_completion_panel(console, config)` signature takes the `NovaConfig` directly rather than a pre-built list so the function itself owns the sort and plural/singular copy decisions (one function, one place to change).

If `len(config.modes) == 0` somehow slipped past the wizard's exit gate (defense in depth; should be impossible after Story 2.3's gate), the completion panel renders `You have no modes ready. Re-run setup.bat to complete setup.` and exits code `1` — treating it as a broken invariant, not a soft skip.

### Patterns consulted

**Patterns consulted:** Two-function clock indirection (#1) — capture timestamp + audit timestamp; AST-based architectural guardrails (#2) — setup-isolation + no-Ritual-imports + ActionType enum guard; Frozen dataclass + single-worker executor (#3) — `WorkspaceSnapshot` is frozen, engine keeps its single-worker contract; Error-translation-at-boundary (#4) — `ConfigError` and `StorageError` translated at `setup/__main__` boundary into product-grade stderr + exit codes; `transaction()` async context manager (#6) — atomic session + snapshot write; Partial-init cleanup in composition root (#7) — `create_app` already handles this; `persist_first_run` benefits automatically because it operates on an already-fully-wired `NovaApp`.

### Project Structure Notes

- **New file:** `src/nova/setup/initial_capture.py` — capture function + persist helper (AC #2, #3, grows later Stories 4.x as the boundary moves).
- **Modified file:** `src/nova/setup/__main__.py` — adds already-setup fast path, calls `_run_initial_capture_and_persist(...)`, renders capture status + completion panel. Target final line count < 200.
- **Modified file:** `src/nova/core/types.py` — adds `ActionType.SETUP_COMPLETE`; updates docstring.
- **Modified file:** `src/nova/core/storage/engine.py` — adds `execute_returning_lastrowid` method (shared with Brain adapter in Story 3.1).
- **Modified file:** `_bmad-output/implementation-artifacts/deferred-work.md` — adds the exclusion-filtering-on-setup-capture deferral.
- **New unit test files:**
  - `tests/unit/setup/test_initial_capture.py`
  - `tests/unit/setup/test_initial_capture_persistence.py`
  - `tests/unit/setup/test_initial_capture_isolation.py`
  - `tests/unit/setup/test_setup_main_state_a.py` (or additions to existing `tests/unit/test_setup_main.py`)
  - `tests/unit/setup/test_setup_does_not_import_ritual_internals.py`
- **Modified test files:**
  - `tests/unit/core/test_types.py` — adds enum-membership + `SETUP_COMPLETE` value assertions
  - `tests/unit/core/test_storage_engine.py` — adds coverage for `execute_returning_lastrowid` (inside + outside a transaction)
  - `tests/unit/test_setup_main.py` — already-setup fast path, timing test
  - `tests/integration/test_setup_wizard.py` — `TestInitialCaptureAndCompletion` class

## Review Focus (boundary-first invariant sweep)

Per Epic 1 retrospective (2026-04-15). Story 2.4 is a first-through-boundary story; this sweep is mandatory.

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | `capture_initial_workspace` is pure (no long-lived state). `persist_first_run` runs inside a `NovaApp` constructed via `create_app` and torn down via `app.close()` in a `finally`. Fast-path (already-setup) creates no state. The `asyncio.run(...)` wrapper owns a one-shot loop whose lifetime matches the persist step; no engine outlives its loop (closes Story 1.4 cross-loop concern). |
| **Teardown under partial failure** | If `capture_initial_workspace` raises (it shouldn't — failures are caught per-window inside), the wizard has already completed and the engine has not yet opened, so there is nothing to tear down. If `create_app` fails partway through (engine started, migration fails), `create_app`'s own `except BaseException` block closes the engine; `_run_initial_capture_and_persist` surfaces `StorageError` and sets exit code 1. If `persist_first_run` raises inside the transaction, `storage.transaction()`'s ROLLBACK path (cross-cutting-patterns.md #6) runs and both writes revert; `app.close()` in the outer `finally` still fires. Audit write failures never roll back the session/snapshot writes — audit is observational (audit.py semantics). |
| **Concurrency model** | Capture runs synchronously in the main task (no threading, no asyncio — pywin32/psutil calls are blocking but bounded by the 250-window cap and per-window guards). Persist runs on the one-shot asyncio loop, honoring the engine's `_tx_owner` contract (same task owns the transaction). No cross-task storage calls in this flow. |
| **Cancellation** | The capture step is pre-loop, so no `CancelledError` path. Inside `asyncio.run`, a `KeyboardInterrupt` during the transaction triggers the `storage.transaction()` shielded ROLLBACK (cross-cutting-patterns.md #6). The outer `finally` runs `app.close()`. Setup then exits via `SystemExit(1)` (argparse convention for user interruption). |
| **Error translation** | `ConfigError` from `load_config` → product-grade stderr + exit 1. `StorageError` from `storage.start()` / migrations / transactional writes → product-grade stderr + exit 1. `pywintypes.error` / `psutil.*` / `OSError` inside capture → logged as WARNING with opaque detail, never surfaced. Audit `StorageError` → swallowed by `AuditLogger` (unchanged). |
| **Test determinism** | Capture timestamp: `events._utc_now_iso` monkeypatchable. Window enumeration: injected fake enumerator (no real Win32 calls in unit tests). Storage engine: in-memory SQLite with real migrations — behaviorally equivalent to production, deterministic across runs. The `< 8 s` wall-time regression guard uses `time.perf_counter()`, not wall clock, and is tolerant of CI jitter. |
| **Logging opacity** | Audit `details` contain only: `modes_count` (integer), `api_key_configured` (bool), `capture_status` (small closed-set enum-like string). No app names, no paths, no key material. Capture WARNING logs contain only: window count, phase (`"enum"` / `"title"` / `"process_name"`). Per-window failures log `extra={"hwnd": <int>}` but never the app name or title. |
| **Idempotency** | Already-setup fast path ensures re-running `setup.bat` is a no-op (no duplicate session row, no duplicate snapshot row, no duplicate audit entry). The fast-path gate predicate (settings.yaml + ≥ 1 valid mode) is the authoritative "setup done" marker. |
| **Patterns consulted** | #1 clock indirection, #2 AST guards, #3 frozen dataclass, #4 error translation, #6 transaction, #7 partial-init cleanup (delegated to `create_app`). #5 per-file skip-on-error applies to mode loading inside `load_config` and is unchanged. |

### Explicit non-goals (scope fence)

- Setup does **NOT** render Briefing Card State B or C — Epic 3 scope.
- Setup does **NOT** build a `BriefingAggregate` or `BriefingViewModel` — Epic 3 scope (Stories 3.2, 3.3).
- Setup does **NOT** wire Brain / Eyes / Nerve / Ritual / Voice / Skin system classes into the composition root — their stories are Epic 3 and 4.
- Setup does **NOT** apply exclusion filtering to captured windows — Story 4.2 (deferral recorded in `deferred-work.md`).
- Setup does **NOT** introduce polling or a context buffer — Story 4.1 / 4.3.
- Setup does **NOT** extend `workspace_snapshots.workspace_data` beyond the three-field JSON shape documented here — Story 4.3 owns shape evolution.
- Setup does **NOT** expose a "reset" command — developer reset is a separate concern (project-context.md:167) and is not in T1.
- Setup does **NOT** add a `nova config` / `nova reset` subcommand — both are T2 candidates.

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 2.4 ACs (lines 1003–1025), Epic 2 framing (lines 398–415, 891–901)]
- [Source: _bmad-output/planning-artifacts/architecture.md — Briefing Card State Contract (lines 161–169), Decision 3 schema (lines 530–584), Decision 3b briefing data contract (lines 586–742), AuditLogger convention (lines 1185–1202), composition root (lines 1043–1090)]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Briefing Card State A render (lines 746–805), UX-DR1/DR10/DR11/DR18/DR19 (lines 267–462), 80-column responsive strategy (lines 1104–1128)]
- [Source: _bmad-output/planning-artifacts/prd.md — FR5 (initial workspace snapshot), NFR2 (<15 min setup)]
- [Source: _bmad-output/project-context.md — enum vocabulary (lines 42, 56), audit rules (lines 73, 86), layering (lines 61–64, 76), privacy boundary (lines 171–179)]
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first invariant sweep Action #1, cross-cutting patterns Action #2, Story 2.4 guidance (lines 166)]
- [Source: docs/cross-cutting-patterns.md — patterns #1, #2, #3, #4, #6, #7]
- [Source: _bmad-output/implementation-artifacts/2-3-guided-mode-creation-wizard.md — layering precedent, UX voice test shape, atomic write pattern]
- [Source: _bmad-output/implementation-artifacts/2-2-api-key-configuration.md — `run_api_key_step` integration pattern, API-key AST guard]
- [Source: _bmad-output/implementation-artifacts/2-1-setup-script-setup-bat.md — `_resolve_data_dir`, validate_data_dir, setup.bat entrypoint]
- [Source: src/nova/app.py — `create_app` contract, `NovaApp` shape, partial-init teardown]
- [Source: src/nova/cli.py — reference for `load_config` + `create_app` integration]
- [Source: src/nova/core/config.py — `load_config`, `_is_valid_mode_stem`, `NovaConfig`]
- [Source: src/nova/core/types.py — `ActionType`, `SnapshotType`, `BriefingState`]
- [Source: src/nova/core/audit.py — `AuditLogger.log_action` contract + opacity rules]
- [Source: src/nova/core/storage/engine.py — `transaction()`, `execute`, thread-affinity contract]
- [Source: src/nova/systems/eyes/models.py — `WindowContext`, `WorkspaceSnapshot`]
- [Source: src/nova/setup/__main__.py — current State A render, wizard wiring, exit-code conventions]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **Frozen-dataclass vs. Protocol read-only attribute boundary.** `NovaApp` is `@dataclass(frozen=True, slots=True)`, so its `storage` / `audit` attributes are read-only at the type level. The first `_NovaAppLike` Protocol declared them as mutable variables, which mypy rejected (*"Protocol member expected settable variable, got read-only attribute"*). Switched the Protocol to `@property`-style declarations — now both the frozen `NovaApp` and the plain-attribute test harness (`_HarnessApp` with `self.storage = storage` in `__init__`) satisfy it. Cross-cutting-patterns.md #3 reminder: frozen dataclasses and Protocols coexist only when the Protocol's attributes are declared via `@property`.
- **pywin32 stubs missing — added pyproject.toml override.** mypy strict flagged `import win32gui` / `import win32process` as `[import-untyped]`. Added `[[tool.mypy.overrides]]` with `module = ["win32gui", "win32process", "pywintypes"]` and `ignore_missing_imports = true`. Story 4.1's `Win32EyesAdapter` inherits this override. No `# type: ignore` scattered through call sites.
- **Existing Story 2.2 / 2.3 unit tests patched to mock the new Story 2.4 async helpers.** Added `_patch_story_24_helpers` decorator that stacks two `AsyncMock` patches (`_probe_setup_complete=False`, `_run_initial_capture_and_persist=EXIT_OK`) so legacy tests continue to exercise their original flow without touching the capture/persist pipeline.
- **Pre-existing `type: ignore[arg-type]` removed.** The new `test_execute_returning_lastrowid_rejects_bare_str_params` test passed `"abc"` without the ignore — mypy accepts bare `str` as `Sequence[str]`. Removed the unneeded comment.
- **Box-drawing characters allowed in emoji whitelist.** Rich `Panel` borders emit codepoints in U+2500–U+257F. The completion test's non-ASCII whitelist was extended to accept this structural range in addition to the `✓ ✗ ⚠ —` content whitelist. Story 2.3's `TestUxVoice` uses the same allowance.

### Completion Notes List

- **Task 1 — `ActionType.SETUP_COMPLETE` added.** Enum membership locked at 12 entries (was 11). New `test_action_type_setup_complete_value` covers the canonical serialization.
- **Task 2 — `nova.setup.initial_capture` module ships.** `CaptureResult` + `CaptureStatus` Literal + `WindowRaw` carrier + `capture_initial_workspace()` with full/partial/empty/unavailable decision-table, 250-window cap, per-window graceful-partial drops, two-function clock indirection for `captured_at`. 16 unit tests cover every corner; 0 real Win32 calls.
- **Task 3 — `persist_first_run` + `SqliteStorageEngine.execute_returning_lastrowid`.** Two inserts + one audit row, all under one `storage.transaction()`. Snapshot JSON is strict compact (no spaces, no `NaN`, `ensure_ascii=False`). 8 unit tests in `test_initial_capture_persistence.py` + 5 in `test_storage_engine.py` cover schema, rollback, JSON shape, audit opacity.
- **Task 4 — `nova.setup.completion` module.** `render_capture_status` dispatches on `CaptureResult.status` via `match` with exhaustiveness-guarding default arm; `render_completion_panel` pulls from `NovaConfig.modes` (no filesystem scan), sorts case-insensitively by `mode.name`. 15 unit tests — UX voice (banned phrases, emoji whitelist), 80-column render, filesystem-scan-forbidden regression, zero-modes recovery copy.
- **Task 5 — `nova.setup.__main__` wired end-to-end.** Two-step fast-path probe (file existence → single `SELECT` against `audit_log`), `_run_initial_capture_and_persist` async helper invoked via one-shot `asyncio.run(...)`, product-grade translation of `ConfigError` / `StorageError` into exit code 1 with no traceback. 11 new fast-path tests pass alongside the 17 legacy ones.
- **Task 6 — Two AST guards.** `test_initial_capture_isolation.py` limits the module's imports to stdlib + `nova.core` + `nova.systems.eyes.models`. `test_setup_does_not_import_ritual_internals.py` sweeps every `src/nova/setup/*.py` for Ritual/Nerve/Skin/Voice/Brain/Hands/Shield imports AND for the literals `"post_setup"` / `"warm_resume"`. All 17 parametrized cases green.
- **Task 7 — 8 integration tests in `TestInitialCaptureAndCompletion`.** Cover the full `main()` pipeline against real in-memory SQLite with migrations applied, every `CaptureResult.status` threading through to the audit row, rollback on simulated `StorageError`, idempotent fast-path on second run, completion-panel sort order, and the < 8 s wall-time NFR2 regression guard.
- **Task 8 — `deferred-work.md` updated** with four Story 2.4 entries: exclusion-filtering-on-setup-capture (Story 4.2), setup-time persistence seam migration (Story 3.1), `_default_probe_factory` typing-hygiene pass, and phase-timing DEBUG-log instrumentation.
- **Boundary-invariant sweep validated.** Zero high-severity expected findings: the setup-only persistence seam is explicitly cited in the module docstring and the deferred-work file; the capture path is fully mockable via `_probe_factory`; the transaction context manager wraps both writes so rollback under cancellation or `StorageError` is test-covered; the audit row is opaque by construction (no app names, no key material).

### File List

**New source files:**

- `src/nova/setup/initial_capture.py` — capture + persist helpers; `CaptureResult`, `CaptureStatus`, `WindowRaw`, `capture_initial_workspace`, `persist_first_run`, `_Win32Probe`, `_WorkspaceProbe`.
- `src/nova/setup/completion.py` — `render_capture_status`, `render_completion_panel`, `BANNED_PHRASES`.

**Modified source files:**

- `src/nova/core/types.py` — `ActionType` gains `SETUP_COMPLETE = "setup_complete"`; docstring cites Story 2.4 origin.
- `src/nova/core/storage/engine.py` — adds `execute_returning_lastrowid` public coroutine + two private sync helpers (`_execute_returning_lastrowid_sync`, `_execute_returning_lastrowid_sync_no_commit`).
- `src/nova/setup/__main__.py` — adds `_probe_setup_complete`, `_render_already_setup_panel`, `_run_initial_capture_and_persist`; swaps `_render_state_a` into the non-fast-path branch; translates `ConfigError` / `StorageError` into `EXIT_CONFIG_ERROR`.
- `pyproject.toml` — `[[tool.mypy.overrides]]` for `win32gui`, `win32process`, `pywintypes`.

**New test files:**

- `tests/unit/setup/test_initial_capture.py` — 16 tests for the capture function.
- `tests/unit/setup/test_initial_capture_persistence.py` — 8 tests for `persist_first_run`.
- `tests/unit/setup/test_initial_capture_isolation.py` — 3 AST guards.
- `tests/unit/setup/test_setup_does_not_import_ritual_internals.py` — 2 AST guards × N setup modules.
- `tests/unit/setup/test_setup_main_state_a.py` — 11 State A copy + fast-path tests.
- `tests/unit/setup/test_completion.py` — 15 UX renderer tests.

**Modified test files:**

- `tests/unit/core/test_types.py` — `ActionType.SETUP_COMPLETE` added to exact-membership set; new `test_action_type_setup_complete_value`.
- `tests/unit/core/test_storage_engine.py` — 5 new tests for `execute_returning_lastrowid` (autoincrement id, rollback-inside-transaction, bare-str rejection, error translation, before-start guard).
- `tests/unit/test_setup_main.py` — legacy tests wrapped in `_patch_story_24_helpers` decorator; signatures updated with `AsyncMock` params; one lambda refactored for mypy-strict compliance.
- `tests/integration/test_setup_wizard.py` — new `TestInitialCaptureAndCompletion` class with 8 full-flow tests.

**Modified planning / tracking files:**

- `_bmad-output/implementation-artifacts/deferred-work.md` — four Story 2.4 deferral entries added.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `2-4-briefing-card-state-a-initial-capture-and-setup-completion: review`; `last_updated` header refreshed to reflect Story 2.4 completion.

## Change Log

- 2026-04-17: Story 2.4 implementation complete. All 8 tasks checked, full suite green (ruff + mypy strict + pytest). Status → `review`. (Co-Authored-By: Claude Opus 4.7 (1M context))
- 2026-04-17: Three-layer adversarial code review run (Blind Hunter + Edge Case Hunter + Acceptance Auditor). 1 decision-needed, 14 actionable patches, 16 deferred, 20 dismissed as noise. Findings appended to Tasks/Subtasks.
- 2026-04-17: All 14 review patches applied + decision resolved (option `a`, `_derive_status` tightened to match AC #5). Audit row now atomic with session+snapshot inside the same `storage.transaction()`; `focused_app` plumbed through `CaptureResult` from `GetForegroundWindow`; `ended_at` stamped after snapshot INSERT via `UPDATE`; State A + ConfigError copy now verbatim per spec; AST guards reject relative imports; 11 new regression tests added. 1196 unit + 20 integration green, ruff + mypy strict clean. Status → `done`.
- 2026-04-17: Post-review external audit surfaced three additional findings (zero-modes fast-path lockout, close-time masking, ConfigError over-specific remediation). All three fixed with regression tests. `_run_initial_capture_and_persist` now aborts with `EXIT_CONFIG_ERROR` if `config.modes` is empty after the wizard returns (prevents setup_complete marker from being written on skipped/interrupted wizard paths). Close-time `StorageError` in the probe and in the persist path is logged and swallowed so it never masks the primary failure. `ConfigError` remediation message generalized to "Inspect %LOCALAPPDATA%/nova/" + surfaces the underlying exception message. 1196 unit + 23 integration green; ruff + mypy strict clean.

## Review Findings — External Audit (2026-04-17)

External code-review pass after the in-skill review surfaced three additional issues. All three fixed in the same session as the main review patches.

- [x] [Review][Patch] **High — setup marked complete with zero modes after skipped/interrupted wizard** [`src/nova/setup/__main__.py`:`_run_initial_capture_and_persist`] — `run_mode_wizard_step` returns early on non-interactive or `KeyboardInterrupt` paths without running its AC #11 exit gate. Main flow previously ran persistence unconditionally, writing the `setup_complete` audit row and locking the user out of the wizard on every subsequent rerun. Fix: after `load_config`, abort with `EXIT_CONFIG_ERROR` + product-grade "no modes configured, re-run setup.bat" copy when `config.modes` is empty. New regression test `test_wizard_skipped_with_zero_modes_does_not_write_setup_complete`.
- [x] [Review][Patch] **Medium — close-time StorageError masks primary persist/probe failure** [`src/nova/setup/__main__.py`:`_probe_setup_complete`, `_run_initial_capture_and_persist`] — unguarded `await engine.close()` / `await app.close()` in `finally` blocks could override a StorageError from the primary path (which users care about) with a secondary close-time traceback. Fix: wrap both closes in `try/except StorageError` with a WARNING log; restructure `_run_initial_capture_and_persist` to capture `persist_error` and surface it AFTER the close attempt. New regression test `test_close_failure_does_not_mask_persist_error`.
- [x] [Review][Patch] **Low — ConfigError remediation text over-specific** [`src/nova/setup/__main__.py`:`_run_initial_capture_and_persist`] — all `ConfigError` from `load_config` mapped to "Delete settings.yaml" but the loader can raise for modes-path-type errors, exclusions parse/IO, etc. Fix: generic "Inspect `%LOCALAPPDATA%/nova/`" copy + surface underlying exception message verbatim. New regression test `test_config_error_message_is_generic_not_settings_specific`.

## Review Findings (2026-04-17)

Three-layer adversarial review (Blind Hunter, Edge Case Hunter, Acceptance Auditor) on the Story 2.4 uncommitted diff. 51 raw findings after dedup; triaged into the buckets below.

### Decision-needed

- [x] [Review][Decision] `_derive_status` broadens `"partial"` beyond AC #5's spec — **resolved: option (a).** `_derive_status` now routes `(captured=0, dropped≥1)` to `"unavailable"` (enumeration worked but no usable probes returned); `test_capture_status_decision_table` parametrize updated to match.

### Actionable patches

- [x] [Review][Patch] **Audit row written OUTSIDE the session/snapshot transaction — violates AC #14/#15 atomicity** [`src/nova/setup/initial_capture.py`:`persist_first_run`] — a crash or StorageError swallow between `transaction()` commit and `audit.log_action` leaves session+snapshot on disk with no `setup_complete` marker; the fast-path probe then re-runs setup and writes a *second* session. Fix: move `audit.log_action(...)` INSIDE the `async with storage.transaction():` block so all three rows land atomically or not at all.
- [x] [Review][Patch] **`focused_app` is first enumerated app, not actual foreground window** [`src/nova/setup/initial_capture.py`:`_serialize_workspace_data`] — the code discards `probe.foreground_hwnd()` and derives focused_app as "first non-None app name". Fix: thread the foreground HWND from `capture_initial_workspace` through `CaptureResult` (add a `focused_app: str | None` field) and serialize it directly. Story 4.1's polling adapter will then inherit the correct field.
- [x] [Review][Patch] **`ended_at` timestamp captured BEFORE the transaction — violates AC #12** [`src/nova/setup/initial_capture.py`:`persist_first_run`] — AC #12 requires `ended_at = _utc_now_iso()` taken *after the snapshot is written*. Current code captures it before any INSERT, so `ended_at ≈ started_at`. Fix: INSERT the session with `ended_at=NULL`, insert the snapshot, then `UPDATE sessions SET ended_at = ? WHERE id = ?` inside the same transaction — or restructure so snapshot-first, session-last.
- [x] [Review][Patch] **State A body copy violates AC #1 verbatim contract** [`src/nova/setup/__main__.py`:`_render_state_a`] — spec mandates `"First session. No history yet — that's expected."` and `"Let's set up your first workspace mode so tomorrow starts warm."`; code renders `"Personal AI Session Companion"`, truncated `"First session. No history yet."`, and `"Running setup to create your workspace modes."` Fix: replace the three-line body with the two AC #1 lines verbatim; strengthen the test to assert the full substrings (em-dash + "that's expected." + "Let's set up ... warm.").
- [x] [Review][Patch] **`ConfigError` translation message drifts from AC #11 verbatim** [`src/nova/setup/__main__.py`:`_run_initial_capture_and_persist`] — spec mandates `"Your settings file appears corrupted. Delete %LOCALAPPDATA%/nova/settings.yaml and re-run setup."`; code prints a generic variant. Fix: use the AC #11 string with the explicit path.
- [x] [Review][Patch] **Dev Notes typo: `NovaConfig.modes` is a `dict[str, ModeConfig]`, not a `tuple`** [story spec § "Mode enumeration for the completion message"] — the code correctly uses `.values()`, so no runtime issue; the spec doc is wrong. Fix: update Story 2.4 Dev Notes (and the File List reference) to say `dict[str, ModeConfig]`.
- [x] [Review][Patch] **`test_audit_details_contain_no_api_key_material` is vacuous** [`tests/unit/setup/test_initial_capture_persistence.py`] — the test passes `api_key_configured=True` but `persist_first_run` never reads a raw key; it's just checking that a function that doesn't touch the key doesn't leak it. Fix: pre-seed a real `sk-ant-test-key` into the settings via the full `_run_initial_capture_and_persist` path (or mock-patch the key into scope) and assert the key string is absent from the audit `details` column.
- [x] [Review][Patch] **AST import guard skips relative imports (`node.level != 0`)** [`tests/unit/setup/test_initial_capture_isolation.py`, `tests/unit/setup/test_setup_does_not_import_ritual_internals.py`] — a `from ...systems.ritual import X` (level=3) silently passes. Fix: drop the `node.level == 0` filter and match on the resolved module path, OR add an explicit rejection of any `node.level > 0` in setup-package files.
- [x] [Review][Patch] **`test_probe_falls_through_on_corrupt_db` does not assert the WARNING log** [`tests/unit/setup/test_setup_main_state_a.py`] — AC #31 explicitly requires `"assert the probe logs WARNING"`. Fix: add `caplog.set_level(logging.WARNING, logger="nova.setup.__main__")` and assert `any("could not open" in r.getMessage() for r in caplog.records)`.
- [x] [Review][Patch] **Missing AC #31 tests: `test_fast_path_ignores_api_key_presence` and `test_fast_path_ignores_api_key_configured_false`** [`tests/unit/setup/test_setup_main_state_a.py`] — AC #31 prescribes these by name to close the Story 2.2 soft-skip regression window. Fix: add two tests that pre-seed a `setup_complete` audit row and assert fast-path fires with (a) no settings.yaml api_key and (b) a details JSON containing `"api_key_configured": false`.
- [x] [Review][Patch] **Missing AC #31 test: `test_no_fast_path_when_db_exists_but_no_setup_complete_row` does not assert post-run row counts** [`tests/unit/setup/test_setup_main_state_a.py`] — AC #31 bullet requires "subsequent successful run writes exactly one new session + snapshot + audit row (no duplicates)". Fix: extend (or add a sibling to) the existing test to run `main()` after the empty-audit-row seed and assert `SELECT COUNT(*) FROM sessions == 1`, same for workspace_snapshots and audit_log.
- [x] [Review][Patch] **Fast-path idempotency regression test masked by audit-outside-transaction bug** [`tests/integration/test_setup_wizard.py`:`test_fast_path_exits_without_rewriting_rows`] — after fixing the audit-inside-transaction patch above, add a direct regression test that ensures a mid-persist crash (simulated by raising `StorageError` in audit.log_action) leaves *zero* rows in sessions + workspace_snapshots + audit_log. Confirms the three-row atomicity invariant end-to-end.
- [x] [Review][Patch] **`foreground_hwnd` probe failures don't catch `psutil.Error`** [`src/nova/setup/initial_capture.py`:`capture_initial_workspace`] — the per-window describe path uses `psutil_exceptions` (includes `psutil.Error`) but the foreground probe falls back to the narrower `_EXPECTED_PROBE_EXCEPTIONS` tuple. Fix: use `psutil_exceptions` for the foreground probe too.
- [x] [Review][Patch] **`# pragma: no cover` on the match default arm is wrong — `test_capture_status_exhaustive_dispatch` does cover it** [`src/nova/setup/completion.py`:`render_capture_status`] — the pragma lies to the coverage tool. Fix: delete the pragma; coverage will correctly show the arm is exercised by the `status="bogus"` test.

### Deferred (tracked in `deferred-work.md`)

- [x] [Review][Defer] `_probe_factory` typed as bare `type` with `# type: ignore[assignment]` — already recorded in `deferred-work.md` Story 2.4 entry; narrow Protocol-based refactor deferred
- [x] [Review][Defer] Theoretical WAL/-shm race between fast-path probe and subsequent `create_app` — bounded by serial `asyncio.run` calls; monitor for CI flake under slow I/O
- [x] [Review][Defer] 80-column test uses three short mode names, not the worst-case long list — test strengthening, not an AC violation
- [x] [Review][Defer] Engine `close()` in `finally` masking the original exception — existing engine-level behavior from Story 1.4; out of Story 2.4 scope
- [x] [Review][Defer] `load_config` non-`ConfigError` exception translation (OSError, UnicodeDecodeError, yaml.YAMLError) — Story 1.6 owns that translation boundary
- [x] [Review][Defer] `create_app` non-`StorageError` exception translation (MigrationError, sqlite3.Error subclasses) — Story 1.10 owns that boundary
- [x] [Review][Defer] `app.close()` raising after `persist_first_run` raised — covered by engine idempotency contract, Story 1.4
- [x] [Review][Defer] Wizard `KeyboardInterrupt` during API-key or mode-wizard step produces a traceback — Stories 2.2/2.3 own that surface
- [x] [Review][Defer] Two modes with identical casefolded `name` — stable-sort tie-breaks on dict insertion order; rare corner case
- [x] [Review][Defer] Whitespace-only `ModeConfig.name` — mode_wizard (Story 2.3) validates names at write time
- [x] [Review][Defer] `test_storage_error_during_persistence_exits_one` mocks `persist_first_run` entirely rather than exercising the real engine finally-close path — strengthening
- [x] [Review][Defer] `test_transaction_rolls_back_on_snapshot_failure` uses a `StorageError` mock, not a real `IntegrityError` — strengthening
- [x] [Review][Defer] `test_per_window_failure_is_graceful_partial` does not assert the HWND-only opacity in caplog — privacy contract locked in module docstring, not in the test
- [x] [Review][Defer] No explicit test that capture-status line renders BEFORE the completion panel (AC #19 ordering) — ordering is enforced by code, not regression-guarded
- [x] [Review][Defer] `test_completion_panel_does_not_scan_filesystem` patches `Path.iterdir` / `Path.exists` globally — narrower mock on specific data_dir is more robust
- [x] [Review][Defer] `test_main_skips_api_key_when_localappdata_missing` does not assert State A renders before the skip warning — ordering assertion gap
