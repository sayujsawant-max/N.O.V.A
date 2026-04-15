# Story 1.8: Audit Logger

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing auditable actions,
I want a single audit logging interface in `core/audit.py` that writes to the `audit_log` table via the injected `SqliteStorageEngine`,
so that all automated actions (app launches, mode switches, deletions, tier changes, etc.) are recorded consistently — append-only, with opaque targets for excluded contexts, observational (never blocking the primary action), and with no system writing to `audit_log` directly.

## Acceptance Criteria

1. **`src/nova/core/audit.py` is the single owner of the audit-write surface.** Public surface is intentionally tiny:
   - One class: `AuditLogger`.
   - Public method: `async def log_action(self, action_type: ActionType, target: str | None, result: str, details: Mapping[str, object] | None = None) -> None`.
   - Module-level result constants for the canonical closed set: `RESULT_SUCCESS = "success"`, `RESULT_FAILED = "failed"`, `RESULT_SKIPPED = "skipped"` (architecture.md:574 pins these three values for the `audit_log.result` column).
   - Module-level type alias: `type ActionResult = Literal["success", "failed", "skipped"]` — exported via `__all__` so future callers can type-narrow their `result` arg without re-declaring the literal set. The public method parameter still types as `result: str` per the epics AC #4 contract; `ActionResult` is the **recommended-for-callers** alias, not an enforced narrowing on the method.
   - `__all__` declares exactly: `AuditLogger`, `RESULT_SUCCESS`, `RESULT_FAILED`, `RESULT_SKIPPED`, `ActionResult`. The `ActionType` enum (already in `core/types.py`, Story 1.2) is NOT re-declared here and NOT re-exported from this module — consumers import it from its canonical home.
   - **No class-level `@classmethod` factories**, no builder pattern, no module-level singletons. Composition root (Story 1.10) instantiates `AuditLogger(storage=...)` once and threads the instance through; auditable systems (Hands, Brain-for-deletions, Nerve-for-tier-changes) receive it via constructor injection.
   - **No public `read`/`query` methods.** The audit-trail read path (Story 5.3 "Audit Trail Inspection") goes through Brain's transparency model, which queries `audit_log` via the `SqliteBrainAdapter` (Story 3.x). `AuditLogger` is **write-only by design**. Adding a `query_recent()` method here would split ownership of the audit read model between two modules.

2. **Do NOT create or duplicate any of the following — they already exist and are pinned:**
   - `ActionType` StrEnum — lives in [src/nova/core/types.py:72-91](src/nova/core/types.py#L72-L91) per Story 1.2. Members are `APP_LAUNCH`, `APP_FOCUS`, `WINDOW_ARRANGE`, `MODE_SWITCH`, `MODE_RESTORE`, `MODE_CREATE`, `MODE_EDIT`, `DELETION`, `SEED_CAPTURE`, `TIER_CHANGE`, `DATABASE_RECOVERY` (11 members). Import it; do not redefine, do not extend the enum from this module, do not add a 12th member here. The enum is the single audit-action vocabulary; widening it is a deliberate Story 1.2 schema change, not an in-line addition.
   - `audit_log` table (columns: `id`, `timestamp`, `action_type`, `target`, `result`, `details`) — created by [src/nova/core/storage/migrations/001_initial_schema.py:62-73](src/nova/core/storage/migrations/001_initial_schema.py#L62-L73) per Story 1.5. Do NOT issue any `CREATE TABLE`, `ALTER TABLE`, or `CREATE INDEX` from this story. Schema mods route through a NEW numbered migration (`002_*.py`), which is not in scope here.
   - `SqliteStorageEngine` — lives in [src/nova/core/storage/engine.py](src/nova/core/storage/engine.py) per Story 1.4. `AuditLogger` accepts a `SqliteStorageEngine` instance via constructor injection and calls `await storage.execute(...)` to insert rows. Do NOT instantiate a second engine, do NOT open a second `sqlite3.Connection`, do NOT bypass the engine to call `sqlite3` directly.
   - `StorageError` — lives in [src/nova/core/exceptions.py:73-80](src/nova/core/exceptions.py#L73-L80) per Story 1.2. The storage engine's `execute` method raises `StorageError` on persistence failure; `AuditLogger.log_action` catches `StorageError` specifically — never `Exception`, never `sqlite3.*`.
   - `_utc_now_iso` — lives in [src/nova/core/events.py:74-89](src/nova/core/events.py#L74-L89) per Story 1.3. **AuditLogger MUST reuse this canonical clock** per project-context.md:46 ("Any future timestamp-emitting module … MUST reuse this pattern"). **Import the module, NOT the symbol**: `from nova.core import events` (or `import nova.core.events as events_module`), then call `events._utc_now_iso()` at the timestamp call site. Doing `from nova.core.events import _utc_now_iso` would bind the function name locally inside `audit.py` at import time, freezing the reference and silently defeating the monkeypatch contract — `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` would update the events-module attribute but the audit module would keep calling the originally-bound function. Calling through the module (`events._utc_now_iso()`) does a name lookup in `nova.core.events`'s globals on every call, which is exactly what `_default_timestamp` does inside `events.py` itself (lines 92–107) — same indirection pattern, same monkeypatch behavior. Tests that need a deterministic timestamp monkeypatch `nova.core.events._utc_now_iso` per the Story 1.3 contract; the module-call indirection makes the patch take effect inside `audit.py` automatically. Do NOT inline `datetime.now(UTC).isoformat()` at the call site (forecloses deterministic testing). Do NOT introduce a second clock parameter on `AuditLogger`.

3. **`AuditLogger` construction contract:**
   ```python
   class AuditLogger:
       def __init__(self, *, storage: SqliteStorageEngine) -> None: ...
   ```
   - **Single keyword-only parameter** (leading `*`). Story 1.7 precedent — keyword-only forecloses positional-arg mistakes when future tuning knobs are added.
   - `storage: SqliteStorageEngine` — injected per project-context.md:67 ("Brain owns all SQLite tables. Other systems read/write through Brain's port interface. No system queries SQLite directly.") — wait: `AuditLogger` is the documented exception per architecture.md:1187 ("Every automated action is logged through a single audit interface. No system writes to audit_log directly."). The audit boundary is `AuditLogger` itself; it goes through the storage engine (not Brain) because the audit table is cross-cutting infrastructure, not Brain's domain memory.
   - **Construction is synchronous** (`__init__` is a plain `def`, not `async def`). Reference assignment only; no I/O, no DB calls, no `create_task`. The composition root wires the logger AFTER `await storage.start()` and `await storage.run_migrations()` complete; this story does NOT call those (Story 1.10 does).
   - **No internal mutable state beyond `self._storage`.** No counters, no queues, no in-memory buffer. Every `log_action` call is a fire-and-forget single insert. Audit batching is explicitly NOT in scope (T2 concern at the earliest).
   - **No `asyncio.Lock`.** `SqliteStorageEngine.execute` already serializes writes via its own `_tx_lock`; layering a second lock here would be redundant and could deadlock if a future caller is itself inside an `engine.transaction()` block.

4. **`log_action` method — exact contract:**
   ```python
   async def log_action(
       self,
       action_type: ActionType,
       target: str | None,
       result: str,
       details: Mapping[str, object] | None = None,
   ) -> None: ...
   ```
   - **Positional parameters** for `action_type`, `target`, `result` — matches the epics AC verbatim and reads naturally at call sites (`audit.log_action(ActionType.APP_LAUNCH, app_name, "success")`). `details` keeps its `= None` default and is the only optional param.
   - `action_type: ActionType` — must be an `ActionType` enum member. **No raw strings accepted** — mypy strict rejects `audit.log_action("app_launch", ...)` at the type level. The 11-member `ActionType` enum (Story 1.2) is the only permitted vocabulary. Persists as `str(action_type)` (e.g., `"app_launch"`) into the `audit_log.action_type TEXT` column — the StrEnum gives us the canonical wire value without `.value` boilerplate.
   - `target: str | None` — opaque reference identifying what was acted on. **For excluded contexts, callers MUST pass `"protected_app"` (or another opaque sentinel), NEVER the actual app name, window title, or process name** (architecture.md:583, project-context.md:72). `AuditLogger` does NOT validate this — the exclusion-boundary enforcement happens upstream at the Eyes capture layer (Story 4.2). What `AuditLogger` DOES enforce: `target` is typed `str | None`, never a structured object that could smuggle excluded fields. `None` is allowed (e.g., `seed_capture` has no specific target). Persists as-is into `audit_log.target TEXT` (nullable).
   - `result: str` — short outcome label. Canonical closed set (per architecture.md:574): `"success"`, `"failed"`, `"skipped"`. Use the module-level `RESULT_SUCCESS` / `RESULT_FAILED` / `RESULT_SKIPPED` constants at call sites — never inline string literals (no-magic-literals rule, project-context.md:131). The method parameter is typed `str` per the epics AC, NOT `Literal[...]` — this leaves room for future result kinds (e.g., `"partial"` for graceful-partial mode-restore in Story 3.6) without churn through every caller. The `ActionResult` type alias is the **recommendation** for callers; the method **accepts** any `str`. **Empty string `""` is rejected** with `ValueError("result must be a non-empty string")` — the column is `NOT NULL` and an empty string is a footgun-shaped row that breaks downstream rendering. Whitespace-only strings are also rejected (`result.strip() == ""` → reject).
   - `details: Mapping[str, object] | None = None` — JSON-serializable additional context. **Never contains raw excluded content** (architecture.md:1200) — same enforcement boundary as `target`: the caller is responsible; `AuditLogger` does not introspect values for sensitivity. What `AuditLogger` DOES do: serializes via `json.dumps(details, separators=(",", ":"), ensure_ascii=False)`. If serialization raises `TypeError` (non-JSON-serializable value like a `datetime`, `Path`, `set`), `AuditLogger` does NOT swallow it — propagates as `TypeError` to the caller. **Rationale:** silent drop would hide the bug at the audit boundary; a loud `TypeError` at the call site forces the caller to convert to a primitive at their boundary. **`None` and empty-dict `{}` semantics differ:** `None` writes `NULL` into the `details` column; `{}` writes the literal string `"{}"`. Tests cover both paths. **`ensure_ascii=False`** so unicode app names (e.g., `"日本語"`) round-trip without `\uXXXX` escaping; the column is TEXT (UTF-8 by sqlite3 default).
   - **Return type `-> None`.** Audit logging is observational; there is nothing for the caller to consume. The method does NOT return a row id, does NOT return the inserted timestamp, does NOT return a success/failure bool. The caller's success continues regardless of the audit outcome (see AC #5).
   - **Timestamp generation:** `timestamp = events._utc_now_iso()` is captured at the **start** of `log_action`, NOT inside the `try/except StorageError` block. Rationale: the timestamp records when the action **happened** (i.e., when the caller called `log_action`), not when sqlite finished writing the row. If the `execute` retries or queues, the timestamp is still the moment of the action — the audit table is a record of actions, not a record of writes. The call goes through the imported `events` module reference (NOT a locally-bound symbol) so monkeypatching `nova.core.events._utc_now_iso` propagates into this call site — see AC #2 for the binding-vs-lookup rationale.

5. **Audit logging is observational — write failure MUST NOT block the primary action.** This is THE single most important behavior in the story (epics AC, project-context.md:86, architecture.md):
   - The `await self._storage.execute(...)` call is wrapped in `try/except StorageError:`.
   - On `StorageError`, the caller's exception is **caught and logged at WARNING with `exc_info=True`** (NOT at ERROR — audit-write failure is a degraded behavior, not a system-level failure per project-context.md:129). **Use `logger.warning("audit write failed; primary action continues", extra={...}, exc_info=True)` — do NOT use `logger.exception(...)`.** `logger.exception()` is a convenience shorthand that logs at ERROR level (Python stdlib contract); using it here would silently upgrade audit-write failures from WARNING to ERROR, contradicting the log-level rule. The `exc_info=True` kwarg is what attaches the chained traceback (the `from err` chain on `StorageError`) to the LogRecord; combined with `logger.warning(...)`, that gives WARNING level + full traceback. Log payload uses `extra={"action_type": str(action_type), "result": result}` — only these two fields, so analysts can correlate the lost row with the caller's traceback (in `exc_info`) without leaking caller-supplied content. **Neither `target` nor `details` is included in `extra`.** `target` is opaque-by-caller-contract and `AuditLogger` does NOT validate that contract — a buggy upstream caller (Hands / Eyes) that passed a raw app name instead of an opaque sentinel would otherwise leak the raw identity into the log file, exactly the failure mode the `audit_log` schema was designed to prevent. Dropping `target` from the failure log preserves opacity by construction. `details` is dropped for the same privacy reason — it may carry caller context the audit row was structured to handle; mirroring it into the log without ceremony risks the excluded-content footgun. Some diagnostic signal is lost ("which row got lost"), but the WARNING + traceback still gives an analyst what they need to find the caller. (Resolved 2026-04-15 via D1 review decision: option (b) — drop `target` to preserve opacity by construction.)
   - After logging, `log_action` returns `None` normally. **The exception does NOT propagate to the caller.** A failed audit write is swallowed; the calling system's primary action proceeds.
   - **`asyncio.CancelledError` is NOT caught.** Per project-context.md:49, cancellation always propagates. The narrow `except StorageError:` does not catch `CancelledError` (which inherits from `BaseException` in py3.12), so this is correct by construction. Locked by `test_log_action_propagates_cancelled_error`.
   - **Any exception type OTHER than `StorageError`** escaping `execute` is a bug in the engine (engine failed to translate at its boundary). `log_action` lets it propagate — does NOT swallow, does NOT wrap as `StorageError`. Locked by `test_log_action_propagates_non_domain_exception`.
   - **`TypeError` from `json.dumps(details)` is also NOT caught** — that is a caller-supplied bad-input bug (passed a non-JSON-serializable value), not an audit-infrastructure failure. Surfacing it loudly at the call site is the right behavior. Audit-write **infrastructure** failures (DB locked, disk full, storage engine not started) are the only failure mode that gets swallowed. Locked by `test_log_action_propagates_typeerror_from_non_serializable_details`.

6. **Append-only — no updates, no deletes:** (architecture.md:854, epics AC)
   - `AuditLogger` exposes ONLY `log_action` (insert). There is NO `update_action`, `delete_action`, `clear_*`, `purge_*`, or `truncate_*` method on the class. Locked by `test_audit_logger_has_no_update_or_delete_methods` (introspects `dir(AuditLogger)` and asserts the only public method is `log_action`).
   - The SQL is exactly `INSERT INTO audit_log (timestamp, action_type, target, result, details) VALUES (?, ?, ?, ?, ?)`. **No `INSERT OR REPLACE`, no `INSERT ... ON CONFLICT DO UPDATE`.** Plain INSERT only.
   - The story does NOT add a UNIQUE constraint, does NOT add an index, does NOT add a foreign key. The `audit_log` schema as defined in `001_initial_schema.py` is the source of truth; this story consumes it as-is.
   - Deletion of audit rows (e.g., for the `forget` flow) is explicitly out of scope. Story 5.2 ("Selective Forget") deletes rows from `sessions`, `memory_items`, `workspace_snapshots` but **logs a deletion event** to `audit_log` — it does not delete prior audit rows. The audit trail is a log of actions, not a mirror of current data.

7. **Excluded-context opacity contract — what AuditLogger does and does not do:**
   - `AuditLogger` does NOT inspect `target` or `details` for excluded-context content. **Enforcement is upstream**: Eyes (Story 4.2) filters at the capture layer; Hands/Nerve (Stories 3.5+, 4.x) call `AuditLogger.log_action(target="protected_app", ...)` for excluded apps. `AuditLogger` is the **boundary**, not the policy.
   - What `AuditLogger` DOES do: types `target` as `str | None`, types `details` as `Mapping[str, object] | None`. There is no struct-shaped parameter that could smuggle a `WindowContext` (which carries `app_name`, `window_title`, `process_name`) into the row. Callers pass already-opaque primitives.
   - The canonical opaque target sentinel for excluded apps is `"protected_app"` (architecture.md:583). Document this in the module docstring and in the `target` parameter docstring so callers know the convention. `AuditLogger` does NOT export `"protected_app"` as a constant — that constant lives in the exclusion-policy module (Story 4.2 territory). Documenting the convention is sufficient for T1.
   - **Tests verify the contract by inspection:** `test_target_is_typed_as_optional_str_only` uses `inspect.signature` to assert `target` parameter annotation is exactly `str | None` (no `WindowContext`, no `dict`, no `Any`). `test_details_is_typed_as_optional_mapping_str_object_only` does the same for `details`.

8. **Constructor injection only — no direct adapter imports in core:** (project-context.md:62/76, architecture.md:1271, epics AC)
   - `core/audit.py` MUST NOT import `sqlite3`, MUST NOT import any `nova.adapters.*`, MUST NOT import any `nova.systems.*`. The only persistence-layer touch is `SqliteStorageEngine` (a `core/` module, not an adapter).
   - Locked by the new `AUDIT_FORBIDDEN_TOPLEVEL_MODULES` and `AUDIT_ALLOWED_TOPLEVEL_MODULES` frozensets in `test_core_isolation.py` (AC #11). `AUDIT_FORBIDDEN_TOPLEVEL_MODULES = FORBIDDEN_TOPLEVEL_MODULES` (no carve-out — `audit.py` reaches sqlite3 only **transitively** through the engine, never directly).
   - Tests use a real `SqliteStorageEngine` against a per-test `tmp_path`-based on-disk DB — **not a mock storage, not `:memory:`**. The storage engine is core infrastructure (not an adapter), and project-context.md:95 ("Unit tests use mock adapters, not real infrastructure") refers to *adapter* boundaries; the engine itself is the layer this story sits on, and mocking it would lose the actual `INSERT` semantics, NOT NULL enforcement, and JSON round-trip behavior the audit boundary depends on. Stories 1.4/1.5 set the precedent: real engine, real migration runner, `tmp_path / "test.db"` per test. `:memory:` is rejected because sqlite's in-memory mode requires `check_same_thread=False` to share across threads (the engine's executor pool is single-worker but still a different thread from the test), and the engine's WAL-mode pragma may not behave identically against `:memory:` as against an on-disk file. `_make_logger(tmp_path)` constructs a real engine + runs migrations against `tmp_path / "test_audit.db"` and returns `(logger, engine)`. See the Critical Constraints section ("Use `tmp_path` fixture, not `Path(":memory:")`") for the same rationale.

9. **Concurrency — single asyncio event loop, engine-level serialization:** (project-context.md:37)
   - `AuditLogger` does NOT acquire its own lock. `SqliteStorageEngine.execute` already serializes writes via `_tx_lock` (Story 1.4/1.5).
   - **No `asyncio.create_task` inside `AuditLogger`.** Audit writes are awaited by the caller. Spawning a background task would (a) decouple the write from the caller's lifetime, leading to lost audits if the process exits before the task runs, (b) defeat the observational-but-blocking-during-the-write semantics that AC #5 carefully defines, and (c) violate "events exist only in-flight" (Story 1.3) by introducing a parallel-task lifecycle for audit work.
   - Tests that exercise concurrent paths use `asyncio.gather(log_action(...), log_action(...))` against a single `AuditLogger` instance + real engine and assert: (a) all rows landed in `audit_log`, (b) order may interleave but each row is intact (no torn writes), (c) no exception propagated.

10. **Logging — structured, never to terminal:** (project-context.md:128, architecture.md:1250-1263)
    - Module logger: `logger = logging.getLogger("nova.core.audit")`.
    - **Successful audit write logs at DEBUG**: `logger.debug("audit row written", extra={"action_type": str(action_type), "result": result})`. Production runs at INFO so this is silent in normal operation; DEBUG runs (development, troubleshooting) get the trail.
    - **Audit-write failure logs at WARNING with `exc_info=True`**: `logger.warning("audit write failed; primary action continues", extra={"action_type": str(action_type), "result": result}, exc_info=True)`. **Do NOT use `logger.exception(...)`** — that convenience method logs at ERROR level (Python stdlib contract), which would contradict the log-level rule. The `exc_info=True` kwarg attaches the chained `StorageError` traceback to the LogRecord at WARNING level, giving WARNING severity + full traceback in one call. **Log level is WARNING, NOT ERROR** — the primary action succeeds; only the audit row is lost. Per project-context.md:129, ERROR is reserved for system failures.
    - **No raw exception body in the message string** — `exc_info=True` handles the traceback. Do NOT format the exception into the WARNING message (e.g., `f"audit write failed: {err}"`) — that risks leaking SQL params into the log message body.
    - **Neither `target` nor `details` is ever logged** (see AC #5 rationale — both are dropped to preserve opacity by construction; the caller-opacity contract for `target` is unenforced and `details` may carry caller content). Both successful-write DEBUG and failure WARNING use the same opacity discipline so the two log paths cannot drift — a future contributor cannot accidentally surface caller-supplied content via one path that the other path screens out.
    - **No `print()` anywhere** (project-context.md:44, ruff `T20`).

11. **`tests/unit/core/test_core_isolation.py` — register `core/audit.py` as a new isolated module.** Follow the pattern Stories 1.6 / 1.7 set:
    - Add `import nova.core.audit as audit_module` to the alphabetized imports (alphabetically BEFORE `config_module`).
    - Add `AUDIT_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES` — `audit.py` has NO carve-out (it does NOT import `sqlite3`, `yaml`, `anthropic`, `rich`, or any other adapter module — sqlite3 access is transitive via the engine).
    - Add `AUDIT_ALLOWED_TOPLEVEL_MODULES: frozenset[str]`: `__future__`, `collections`, `json`, `logging`, `nova`, `typing`. **No `asyncio`** — no `Lock`, no `create_task`, no `sleep` in this module. **No `datetime`** — timestamp generation reuses `_utc_now_iso` from `core/events.py`. **No `os`/`pathlib`** — no filesystem. **No `sqlite3`** — engine handles it. **No `dataclasses`** — `AuditLogger` is a plain class, not a dataclass.
    - Add tests: `test_audit_forbidden_imports`, `test_audit_imports_within_allowlist`, `test_audit_does_not_import_nova_adapters_or_systems`, `test_audit_does_not_dynamically_import_nova_adapters_or_systems`.
    - Extend the parametrize lists in `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules` to include `audit_module`. Add an `audit_module is X` branch in `test_no_dynamic_imports_of_forbidden_modules` that selects `AUDIT_FORBIDDEN_TOPLEVEL_MODULES` (== full global set).
    - **No change to the global `FORBIDDEN_TOPLEVEL_MODULES` frozenset.**

12. **`src/nova/core/__init__.py` re-export update.** Match the pattern Stories 1.2 / 1.3 / 1.4 / 1.5 / 1.6 / 1.7 set:
    - Add to the import block: `from nova.core.audit import RESULT_FAILED, RESULT_SKIPPED, RESULT_SUCCESS, ActionResult, AuditLogger`.
    - Extend `__all__` alphabetically: add `ActionResult`, `AuditLogger`, `RESULT_FAILED`, `RESULT_SKIPPED`, `RESULT_SUCCESS`. Story 1.7 took the re-export count to 32 names; this story takes it to **37 names**.
    - Alphabetical ordering locked by the existing Story 1.2 monotonic-ordering test.
    - Update the module docstring's first line to reflect the new content (Story 1.6 changed it to mention `tiers`; this story adds `audit`): `"""Shared infrastructure - events, config, tiers, audit, storage."""` — **already current** (verified in current `__init__.py:1`); no change required to the docstring line itself, only the imports + `__all__`.

13. **Quality gate passes clean (Story 1.7 carry-forward):** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0.
    - mypy strict succeeds on `audit.py`, the modified `core/__init__.py`, `test_audit.py`, and the modified `test_core_isolation.py`.
    - **No `Any`, no `# type: ignore` in production code.** `Mapping[str, object]` (from `collections.abc`) is the precise type for `details`; `object` (not `Any`) is what the JSON serializer accepts and what callers narrow at the call site. `Literal["success", "failed", "skipped"]` is the precise type for the `ActionResult` alias.
    - Repo tree stays clean after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm`.
    - **Expected test count delta:** `tests/unit/core/test_audit.py` adds ~22–28 tests (see AC #15); `test_core_isolation.py` adds 4 tests + 2 parametrize entries. Firm number is whatever the run produces — don't over-fit a target. Prior total: **461 passed, 1 skipped** (462 collected) at end of Story 1.7.

14. **No consumer wiring in this story.** Specifically:
    - Do NOT modify `src/nova/app.py` — wiring `AuditLogger(storage=engine)` into the composition root is Story 1.10's job.
    - Do NOT modify `src/nova/cli.py` — cli startup is also Story 1.10.
    - Do NOT create `src/nova/ports/*` — Story 1.9 owns the port layer.
    - Do NOT create `src/nova/systems/hands/`, `systems/nerve/`, or any system module — Stories 3.5+ own those.
    - Do NOT subscribe `AuditLogger` to the event bus from this story. Cross-story note (architecture.md:797 + Story 1.7's cross-story table): Nerve (Story 3.5) is the subscriber that catches `TierChanged` off the bus and calls `audit_logger.log_action(ActionType.TIER_CHANGE, ...)`. `AuditLogger` itself does NOT know about the event bus, does NOT import `EventBus`. It is a passive write-side primitive driven by callers.
    - Do NOT add a "verify" / "self-test" / "ping" method that does a no-op INSERT to confirm the engine is healthy. Composition root (Story 1.10) verifies the engine via `await storage.run_migrations()`; `AuditLogger` does not duplicate that check.
    - Do NOT introduce a `core/audit.py` migration. The `audit_log` table is created by `001_initial_schema.py`. Schema changes are out of scope.

15. **Test file `tests/unit/core/test_audit.py` — coverage expectations (~22–28 tests):**
    - **Shape tests** (~3): `AuditLogger` instantiation with the keyword-only `storage=` arg succeeds; `dir(AuditLogger)` exposes exactly one public method (`log_action`); module exposes `RESULT_SUCCESS == "success"`, `RESULT_FAILED == "failed"`, `RESULT_SKIPPED == "skipped"`, and `ActionResult` is a `Literal[...]` of those three.
    - **Happy-path insert tests** (~5):
      - `log_action(ActionType.APP_LAUNCH, "code.exe", RESULT_SUCCESS)` writes one row with the correct `action_type` (`"app_launch"`), `target` (`"code.exe"`), `result` (`"success"`), `details` (NULL), and a non-empty `timestamp`. Verify by `await engine.fetchall("SELECT * FROM audit_log")`.
      - `log_action(ActionType.SEED_CAPTURE, None, RESULT_SUCCESS)` writes a row with `target` NULL.
      - `log_action(ActionType.DELETION, "topic_id_42", RESULT_SUCCESS, {"items_deleted": 7, "tables": ["sessions", "memory_items"]})` writes the JSON blob to `details` correctly (round-trip via `json.loads` after read).
      - `log_action` with `details={}` (empty dict) writes `"{}"` (literal string) to `details`, NOT NULL — locks the empty-vs-None distinction.
      - `log_action` invoked twice in sequence produces two rows with monotonically-non-decreasing `id` and `timestamp` values (the timestamp clock is monkeypatched to return advancing values).
    - **Action-type vocabulary tests** (~3, parametrized):
      - All 11 `ActionType` members can be passed as `action_type` and serialize as their canonical `str(member)` string. Parametrize over `list(ActionType)` so adding a 12th member (e.g., a future `ActionType.PASSWORD_CHANGE`) auto-extends the test without manual update. The parametrize id is `member.name` for readable failure messages.
      - Passing a raw string (e.g., `audit.log_action("app_launch", ...)`) is rejected by mypy strict — verify via a comment-only mypy expectation, OR by a runtime-isinstance assertion: `isinstance(action_type, ActionType)` — actually NO, do NOT add a runtime isinstance check in `log_action` (it's redundant with mypy strict and adds dead defensive code). Instead, the test asserts the `inspect.signature(AuditLogger.log_action).parameters["action_type"].annotation is ActionType` (sentinel proves mypy will reject raw strings at the call site).
      - Passing a non-`ActionType` enum member (e.g., `BriefingState.FIRST_RUN`) is rejected by mypy strict (same signature-inspection check).
    - **Result validation tests** (~3):
      - `log_action(..., result="")` raises `ValueError` (empty string rejected).
      - `log_action(..., result="   ")` raises `ValueError` (whitespace-only rejected).
      - `log_action(..., result="partial")` succeeds (custom-but-non-empty result accepted — locks the AC #4 "method accepts any non-empty str" decision).
    - **Details serialization tests** (~3):
      - `log_action(..., details=None)` writes `NULL` to the `details` column (verify with `row["details"] is None`).
      - `log_action(..., details={"unicode": "日本語", "nested": {"a": 1}})` writes JSON without `\uXXXX` escaping (verify by reading back and checking for the literal `日本語` substring in the persisted string).
      - `log_action(..., details={"bad": datetime.now(UTC)})` raises `TypeError` from `json.dumps` (NOT swallowed); the row is NOT inserted (verify `await engine.fetchall("SELECT COUNT(*) FROM audit_log")` returns 0).
    - **Observational-failure-mode tests** (~4) — THE most important behavior:
      - `log_action` against an engine that is NOT started raises `StorageError` from the engine, but `AuditLogger` swallows it and returns `None`. Caller does NOT see an exception. Verify via `caplog.records` that one WARNING was logged carrying `extra["action_type"]` and `extra["result"]` — and assert NO `extra["target"]` and NO `extra["details"]` (per the AC #5 / #10 opacity-by-construction rule; both fields are dropped from the log path).
      - `log_action` against an engine whose `execute` is monkeypatched to raise `StorageError("simulated DB lock contention")`: same behavior — swallowed, WARNING logged, returns `None`.
      - `log_action` against an engine whose `execute` is monkeypatched to raise a non-domain exception (e.g., `RuntimeError("some unrelated bug")`): exception propagates to caller (NOT swallowed). Locks the AC #5 "narrow suppression" rule.
      - `log_action` against an engine whose `execute` is monkeypatched to raise `asyncio.CancelledError`: cancellation propagates (NOT swallowed). Locks project-context.md:49.
    - **Append-only contract tests** (~2):
      - `dir(AuditLogger)` contains no public attribute starting with `update`, `delete`, `clear`, `purge`, `truncate`, or `remove`. Locked by introspection: `[m for m in dir(AuditLogger) if not m.startswith("_")] == ["log_action"]`.
      - The SQL string in `audit.py` (read via `inspect.getsource(audit_module)`) is plain `INSERT INTO audit_log` — does NOT contain `INSERT OR REPLACE`, `ON CONFLICT`, `UPDATE audit_log`, `DELETE FROM audit_log`. AST-walk the module source per the Story 1.6 / 1.7 carry-forward (AST > regex on docstrings).
    - **Type signature tests** (~2):
      - `inspect.signature(AuditLogger.log_action).parameters["target"].annotation` is `str | None`. Locks the excluded-context boundary contract (no struct-shaped target sneaking in).
      - `inspect.signature(AuditLogger.log_action).parameters["details"].annotation` is `Mapping[str, object] | None`. Same rationale.
    - **Concurrency test** (~1):
      - `asyncio.gather(log_action(...), log_action(...), log_action(...))` against one logger + one engine produces exactly 3 rows, no exception, no torn writes (each row's fields match what was passed).
    - **Helper factories** (top of test file, not in conftest per Story 1.5/1.6/1.7 precedent):
      - `_make_logger(tmp_path: Path) -> tuple[AuditLogger, SqliteStorageEngine]` — opens a real `SqliteStorageEngine` against `tmp_path / "test_audit.db"`, runs migrations, constructs `AuditLogger(storage=engine)`. Returns the tuple. Tests are responsible for `await engine.close()` — use a try/finally OR an async fixture wrapping `_make_logger`.
      - `_FailingExecuteEngine` — a thin subclass / wrapper of `SqliteStorageEngine` whose `execute` raises a configurable exception. Used in the failure-mode tests. Subclass approach keeps the rest of the engine real (so `start()` / `run_migrations()` work as usual); only `execute` is overridden.

16. **Cross-story impact reference (for reviewers — not consumed in code):**

    | Consumer story | Uses from this story | Why |
    |---|---|---|
    | 1.10 Composition root & CLI entrypoint | `AuditLogger(storage=engine)` | Composition root is the only place `AuditLogger` is instantiated. Wired AFTER `await engine.start()` and `await engine.run_migrations()`. |
    | 3.5 Nerve command routing | Subscribes to `TierChanged` events on the bus, calls `audit_logger.log_action(ActionType.TIER_CHANGE, ...)` | Nerve is the canonical bridge from `TierChanged` (Story 1.7) to the audit row. `AuditLogger` itself does NOT know about the event bus. |
    | 3.6 Mode restore & app launching | `audit_logger.log_action(ActionType.APP_LAUNCH, app_name_or_protected_app, "success" / "failed")` per app launch attempt | First production caller. AC #4 of Story 3.6: "audit logging is observational: audit write failure must NOT block the restore or prevent event emission" — AC #5 of THIS story makes that guarantee. |
    | 3.7 Shutdown flow & seed capture | `audit_logger.log_action(ActionType.SEED_CAPTURE, target=None, result="success")` | AC of Story 3.7: "audit failure does not block shutdown completion" — same observational contract. |
    | 5.2 Selective forget | `audit_logger.log_action(ActionType.DELETION, target=opaque_topic_ref, result="success", details={"items_deleted": N, "tables": [...]})` | Deletion event records the action and scope, NOT the deleted content. The `target` and `details` are caller-supplied opaque references. |
    | 5.3 Audit trail inspection | (Read path) — Brain reads `audit_log` via `SqliteBrainAdapter`, NOT via `AuditLogger` | `AuditLogger` is write-only. Read goes through Brain's transparency model. |
    | 5.4 Tier status display | (No direct dependency) — Skin renders the once-on-change tier notice from the event bus; the audit row is a separate path written by Nerve (Story 3.5). | Decoupled. |
    | 6.1 Window focus & arrange | `audit_logger.log_action(ActionType.APP_FOCUS / WINDOW_ARRANGE, ...)` | Same observational contract. |
    | 6.2 Mode state bookmarking | `audit_logger.log_action(ActionType.MODE_SWITCH, ...)` | Same. |
    | 6.3 Ad-hoc mode creation | `audit_logger.log_action(ActionType.MODE_CREATE, ...)` | Mode lifecycle is auditable. |
    | 6.4 Mode editing | `audit_logger.log_action(ActionType.MODE_EDIT, ...)` | Mode lifecycle is auditable. |
    | 5.5 SQLite corruption recovery | `audit_logger.log_action(ActionType.DATABASE_RECOVERY, ...)` | Recovery event is a proper typed action. |

    **Twelve downstream stories** consume `AuditLogger`. The biggest risk vector is Stories 3.5–3.7 (the first production callers), which is why AC #5 (observational-failure) is the most carefully test-locked behavior in this story.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/audit.py` — `AuditLogger` class + module-level result constants** (AC: #1–#10)
  - [x] Module docstring: purpose (single audit interface for `audit_log` writes), pins the architecture rules (architecture.md:1185–1202 + project-context.md:73/86), documents the opaque-target convention (`"protected_app"` for excluded apps), explains the observational-failure semantics (AC #5).
  - [x] `from __future__ import annotations`.
  - [x] Imports (exact — the isolation-test allowlist matches these): `json`, `logging`, `collections.abc.Mapping`, `typing.Literal`. First-party: **`from nova.core import events`** (module import — NOT `from nova.core.events import _utc_now_iso`; see AC #2 for the binding-vs-lookup rationale that preserves the monkeypatch contract), `nova.core.exceptions.StorageError`, `nova.core.storage.engine.SqliteStorageEngine`, `nova.core.types.ActionType`.
  - [x] Module-level `logger = logging.getLogger("nova.core.audit")`.
  - [x] Module-level constants: `RESULT_SUCCESS = "success"`, `RESULT_FAILED = "failed"`, `RESULT_SKIPPED = "skipped"`.
  - [x] Module-level type alias: `type ActionResult = Literal["success", "failed", "skipped"]` (PEP 695 syntax — matches Story 1.4 precedent for `SqlParams`; do NOT use `typing.TypeAlias`).
  - [x] `AuditLogger` class per AC #3–#10. Keyword-only constructor (`def __init__(self, *, storage: SqliteStorageEngine) -> None`). Single instance attribute `self._storage`.
  - [x] Public method `log_action` per AC #4–#5. Runtime guards: `if not isinstance(action_type, ActionType): raise TypeError(...)`; `if not isinstance(result, str) or not result.strip(): raise ValueError("result must be a non-empty string")`. Timestamp captured at start via `events._utc_now_iso()` (module-call form so the monkeypatch on `nova.core.events._utc_now_iso` propagates here — see AC #2). JSON serialization via `json.dumps(details, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` wrapped in `try/except (TypeError, ValueError)` that re-raises as `TypeError(...) from err` so all serialization-failure modes (non-serializable values, circular refs, non-finite floats) normalize to one documented exception class. Insert via `await self._storage.execute(_INSERT_SQL, (timestamp, str(action_type), target, result, details_json))` wrapped in `try/except StorageError:` per AC #5. The except block calls `logger.warning("audit write failed; primary action continues", extra={"action_type": str(action_type), "result": result}, exc_info=True)` — only `action_type` + `result` in `extra` (per AC #5 / #10 opacity-by-construction), and `logger.warning(..., exc_info=True)` not `logger.exception(...)` since the latter logs at ERROR.
  - [x] Module-level `_INSERT_SQL: str = "INSERT INTO audit_log (timestamp, action_type, target, result, details) VALUES (?, ?, ?, ?, ?)"` — extracted to a private constant so the SQL appears in exactly one place (no-magic-literals rule). Underscore-prefixed because it is an implementation detail, NOT exported from `__all__`.
  - [x] `__all__ = ["ActionResult", "AuditLogger", "RESULT_FAILED", "RESULT_SKIPPED", "RESULT_SUCCESS"]` (alphabetized).

- [x] **Task 2: Update `src/nova/core/__init__.py` — re-export `AuditLogger`, `ActionResult`, `RESULT_*`** (AC: #12)
  - [x] Add `from nova.core.audit import RESULT_FAILED, RESULT_SKIPPED, RESULT_SUCCESS, ActionResult, AuditLogger` to the import block (alphabetized — goes between `from nova.core import` first existing line and `from nova.core.config import ...`).
  - [x] Extend `__all__` alphabetically: add `ActionResult`, `AuditLogger`, `RESULT_FAILED`, `RESULT_SKIPPED`, `RESULT_SUCCESS`. Total: 32 → 37 names.
  - [x] Verify: `from nova.core import AuditLogger, RESULT_SUCCESS` resolves (import exercised indirectly via the re-export and every audit test).

- [x] **Task 3: Extend `tests/unit/core/test_core_isolation.py` — register audit.py as a fully-isolated core module** (AC: #11)
  - [x] Alphabetized import: `import nova.core.audit as audit_module` (placed alphabetically BEFORE `import nova.core.config as config_module`).
  - [x] `AUDIT_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES` (no carve-out — sqlite3 access is transitive via the engine).
  - [x] `AUDIT_ALLOWED_TOPLEVEL_MODULES: frozenset[str] = frozenset({"__future__", "collections", "json", "logging", "nova", "typing"})` — 6 entries.
  - [x] Add `test_audit_forbidden_imports`, `test_audit_imports_within_allowlist`, `test_audit_does_not_import_nova_adapters_or_systems`, `test_audit_does_not_dynamically_import_nova_adapters_or_systems`. Mirror the Story 1.7 `test_tiers_*` block in shape and docstrings.
  - [x] Extend the `test_no_relative_imports` parametrize list and the `test_no_dynamic_imports_of_forbidden_modules` parametrize list to include `audit_module`. Add an `elif module is audit_module: forbidden = AUDIT_FORBIDDEN_TOPLEVEL_MODULES` branch in the dynamic-import dispatch (AUDIT == FORBIDDEN; the elif is for symmetry with `tiers_module` even though the assignment is identical to the `else` default — keeps the dispatch table's intent legible).

- [x] **Task 4: Author `tests/unit/core/test_audit.py` — ~22–28 tests per AC #15** (AC: #15)
  - [x] Header: `from __future__ import annotations`, imports matching Story 1.4/1.5/1.6/1.7 conventions.
  - [x] Helpers at top of file (not in conftest): `_make_logger(tmp_path)` async factory, `_FailingExecuteEngine` subclass for failure-mode tests.
  - [x] Every test `async def test_...(...)` returns `None`. Auto-mode asyncio (no `@pytest.mark.asyncio` decorators).
  - [x] No fixtures added to `tests/conftest.py`.
  - [x] AST-level guardrail `test_audit_module_uses_only_insert_sql` walks `audit.py`'s AST, finds the `_INSERT_SQL` constant assignment, and asserts: the literal SQL string matches the expected `INSERT INTO audit_log` shape exactly; no other SQL constants are defined in the module (introspect via `ast.walk` for `ast.Assign` nodes whose value is a `Constant(str)` matching `(?i)\b(UPDATE|DELETE|REPLACE|TRUNCATE|ALTER|DROP)\b\s+audit_log`). Carry-forward from Story 1.6/1.7 review lesson: **AST inspection > text regex** for forbidden-pattern guards inside production code (the rule "no UPDATE/DELETE on audit_log" is locked at module-structure level, not by grepping source text).
  - [x] AST-level guardrail `test_audit_logger_class_has_no_mutating_methods_beyond_log_action` walks `audit.py`'s AST for the `AuditLogger` class body, collects all `ast.FunctionDef` / `ast.AsyncFunctionDef` names, asserts that the only public name is `log_action` (everything else starts with `_` — the only legal private method is `__init__`).
  - [x] Every test that opens an engine `await engine.close()` in a try/finally OR via an async fixture. **No leaked connections** per project-context.md:104.

- [x] **Task 5: Full verify run** (AC: #13)
  - [x] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0.
  - [x] Test count: **462 → ~488** (+~26): ~22–28 new `test_audit.py` tests + 4 new isolation tests + 2 parametrize-list extensions counted as 4 (parametrized over the 7 modules now). Actual pytest line will be whatever the run produces.
  - [x] `git status` clean — only intentional edits (audit.py, test_audit.py, test_core_isolation.py, core/__init__.py, sprint-status.yaml, story file). No stray caches, backups, `.db` / `.db-wal` / `.db-shm` files.

- [x] **Task 6: Sprint status + commit** (AC: #13, post-implementation)
  - [x] Update `_bmad-output/implementation-artifacts/sprint-status.yaml` → `1-8-audit-logger: in-progress` on dev start, `review` on handoff.
  - [x] Commit message (Story 1.4/1.5/1.6/1.7 style): `"Story 1.8: audit logger (core/audit.py)"` — to be applied by the user after review approval.

### Review Findings (2026-04-15)

**Code review summary:** 3 adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). 23 raw findings → 2 decision-needed, 5 patches, 12 deferred, 4 dismissed (after dedup/merge).

- [x] [Review][Patch] **Drop `target` from the failure-log `extra={...}` to preserve opacity by construction** [src/nova/core/audit.py:295-321] — Blind F4. **Applied 2026-04-15** (D1 option b). The `except StorageError` block's `extra` now carries only `action_type` + `result`; `target` and `details` are both dropped. Inline comment explains the rationale (caller opacity contract is unenforced; failure-log path must not leak an unvalidated `target`). Successful-write DEBUG log path also tightened to match the same opacity discipline so the two log paths can't drift. Test `test_log_action_swallows_storage_error_logs_warning` updated to assert `target` and `details` are NOT on the LogRecord.
- [x] [Review][Patch] **Rephrase `ActionResult` docstring to "advisory only — not enforced at the method boundary"** [src/nova/core/audit.py:91-110] — Blind F5. **Applied 2026-04-15** (D2 option c). Docstring now explicitly says `ActionResult` does NOT narrow `log_action`'s accepted inputs and shows callers how to opt in via local annotations. Method-parameter docstring on `result` updated to match — references `ActionResult` as advisory-only and clarifies non-`str` / empty inputs raise `ValueError`. `result: str` signature unchanged per AC.
- [x] [Review][Patch] **`json.dumps(details, ...)` allows NaN / Infinity, persists invalid JSON** [src/nova/core/audit.py:243-272] — Edge E1. **Applied 2026-04-15.** Added `allow_nan=False` to the `json.dumps` call. Combined with the E2 fix (catch ValueError + re-raise as TypeError), non-finite floats now surface as one consistent `TypeError` at the audit boundary. New test `test_log_action_rejects_nan_and_infinity_in_details` covers `nan`, `inf`, `-inf`.
- [x] [Review][Patch] **`json.dumps` raises `ValueError` on circular refs but docstring only mentions `TypeError`** [src/nova/core/audit.py:243-272] — Edge E2. **Applied 2026-04-15.** The `json.dumps` call is now wrapped in `try/except (TypeError, ValueError)` that re-raises as `TypeError("details must be JSON-serializable with finite numeric values and no circular references") from err`. One consistent failure-exception class for any caller-supplied bad payload; original exception preserved via `__cause__`. `Raises:` block updated to document the normalization. New test `test_log_action_rejects_circular_reference_in_details_as_typeerror` locks the contract.
- [x] [Review][Patch] **`result=None` / non-`str` raises raw `AttributeError`, not the documented `ValueError`** [src/nova/core/audit.py:217-225] — Edge E4 + E5. **Applied 2026-04-15.** Boundary check now reads `if not isinstance(result, str) or not result.strip():` — `isinstance` fires before `.strip()` so non-`str` inputs (including `None` / `True` / `int`) hit the documented `ValueError`. New test `test_log_action_rejects_non_str_result_with_value_error` parametrically covers `None` / `True` / `42`.
- [x] [Review][Patch] **`str(action_type)` accepts non-`ActionType` `StrEnum` members at runtime — silently widens the audit vocabulary** [src/nova/core/audit.py:202-216] — Edge E6. **Applied 2026-04-15.** Added `if not isinstance(action_type, ActionType): raise TypeError(...)` at the method boundary BEFORE any other work. Closes the `# type: ignore` path for both raw strings and other `StrEnum` members (e.g. `BriefingState.FIRST_RUN`). New test `test_log_action_rejects_non_actiontype_at_runtime` covers both vectors.
- [x] [Review][Patch] **`test_audit_imports_events_module_not_utc_now_iso_symbol` over-restricts** [tests/unit/core/test_audit.py:649-704] — Blind F1. **Applied 2026-04-15.** The AST guard is now narrow: rejects only `from nova.core.events import _utc_now_iso` specifically; other symbols (`Event`, event dataclasses, etc.) are explicitly allowed. Module-import recognition extended to also accept `import nova.core.events as <name>`. Failure messages now name the specific contract concern instead of misleadingly suggesting all events imports are forbidden.
- [x] [Review][Defer] **AST regex on SQL constant misses string-concat / f-string violations** [tests/unit/core/test_audit.py:537-540] — Blind F2. Fundamental limit of static-analysis on string literals; `f"DELETE FROM {TABLE}"` where `TABLE = "audit_log"` is a separate constant would slip through. Document the limitation in the test docstring; not actionable beyond that. **Reason for defer:** fundamental AST-scan limitation, no clean fix without a real SQL parser; current guard catches the realistic regression class (literal-string mutation SQL).
- [x] [Review][Defer] **`dir()`-based `test_audit_logger_only_public_method_is_log_action` is brittle and redundant with the AST guard** [tests/unit/core/test_audit.py:142-151] — Blind F3. The companion AST guard (`test_audit_logger_class_has_no_mutating_methods_beyond_log_action`) is the real lock. The `dir()` test would break on harmless future additions (public properties, class-level type aliases). **Reason for defer:** redundant scaffolding; safe to keep until a future test-hygiene pass; not blocking.
- [x] [Review][Defer] **`_FailingExecuteEngine.execute` accepts `params: Any` — doesn't enforce real engine's parameter-shape contract** [tests/unit/core/test_audit.py:94-103] — Blind F7. If `audit.py` regressed to passing `params` as a bare string, the real engine would raise `StorageError` (which audit swallows!) and the failure tests would still pass because the fake raises its pre-canned exception. Add `_reject_scalar_string_params(params)` in the armed branch to close the loophole. **Reason for defer:** test-quality polish; real engine enforces this on every production call so audit can't actually regress in production without breaking elsewhere first.
- [x] [Review][Defer] **`test_log_action_signature_pins_action_type_to_enum` doesn't catch `ActionType | str` widening** [tests/unit/core/test_audit.py:278-289] — Blind F9. Catches loosen-to-`str` but not loosen-to-`ActionType | str`. **Reason for defer:** subsumed by E6 patch (runtime `isinstance(action_type, ActionType)` guard) — once that lands, runtime widening is impossible regardless of declared annotation, so this gap stops mattering.
- [x] [Review][Defer] **`json.dumps` non-string-key error message is misleading ("non-serializable" when the real issue is the `Mapping[str, object]` contract)** [src/nova/core/audit.py:137, 217-221] — Edge E3. Caller passing `details={1: "x"}` gets a `TypeError` whose message mentions serialization, not the schema violation. **Reason for defer:** diagnostic quality only; same propagation surface as the documented `TypeError` path; better-handled as a docstring tweak in a future polish pass.
- [x] [Review][Defer] **Future LogRecord reserved-name collision risk in `extra={...}`** [src/nova/core/audit.py:247-252, 258-262] — Edge E7. Today's keys (`action_type`, `result`) are safe; a future maintainer adding `extra={"message": ...}` or `extra={"name": ...}` would hit `KeyError` from the logging module, in the WARNING path which is precisely where you least want a secondary exception. **Reason for defer:** purely future-defensive; mention in code comment when a contributor next edits this `extra` dict.
- [x] [Review][Defer] **Concurrency test gap: cancellation mid-`execute` while engine `_tx_lock` is held** [tests/unit/core/test_audit.py:598-616] — Blind F6 + Edge E9. `test_log_action_concurrent_writes_all_land` only verifies `gather` of three writes lands; it does NOT exercise the realistic shape (outer transaction holds the engine lock + audit task is cancelled mid-acquisition). Behavior is correct today (engine releases the lock on `CancelledError`); test gap only. **Reason for defer:** working as designed; would be a useful regression lock for a future edit but not blocking review approval.
- [x] [Review][Defer] **Unstarted-engine path produces silent forever-fail (every `log_action` swallows `StorageError`)** [src/nova/core/audit.py:129-130] — Edge E10. A composition-root bug (constructing `AuditLogger(storage=engine)` before `await engine.start()`) makes every audit row vanish silently with one WARNING per call. **Reason for defer:** composition-root concern (Story 1.10); audit module is correctly observational. If Story 1.10 doesn't naturally enforce ordering, revisit then with an "engine-not-started → ERROR-level once" pattern.
- [x] [Review][Defer] **"Engine not started" failure mode test is merged into the generic StorageError-swallow test rather than landed as its own case** [tests/unit/core/test_audit.py:401-443] — Auditor A1. AC #15 lists 4 distinct observational-failure tests; the implementation has 4, but bullet #1 ("engine not started") and bullet #2 ("execute monkeypatched to raise StorageError") are merged into one. The semantic coverage is identical — `StorageError` swallow path is locked. **Reason for defer:** semantic coverage is complete; splitting into two near-duplicate tests is low-value test inflation.
- [x] [Review][Defer] **Test-name drift from spec's "Locked by `<name>`" anchors** [tests/unit/core/test_audit.py multiple] — Auditor A2 + A3. Three tests exist under different names than the spec referenced (`test_log_action_propagates_typeerror_from_non_serializable_details` → `test_log_action_details_non_serializable_raises_typeerror_and_writes_no_row`; `test_audit_logger_has_no_update_or_delete_methods` → `test_audit_logger_class_has_no_mutating_methods_beyond_log_action`; `test_audit_module_uses_only_insert_sql` → `test_audit_module_uses_only_insert_sql_no_update_or_delete`). Semantics match exactly. **Reason for defer:** cosmetic — only affects grep-ability of the spec's "Locked by" anchors; renaming is low-value once the spec is shipped.

**Dismissed (not noise-worthy to log in-story, but recorded for trace):**
- Edge E5 (boolean `True` as `result`) — same root cause as E4; folded into the E4 patch.
- Edge E8 (clock errors propagate from `events._utc_now_iso()`) — working as documented (only `StorageError` is swallowed by the observational contract).
- Auditor A4 (`_INSERT_SQL` lacks explicit `: str` annotation) — mypy infers `str` from the literal; no type-correctness issue.
- Blind F8 (TypeError test's "no row inserted" assertion is trivially true since `json.dumps` runs before the insert) — assertion is harmless even if redundant.

---

## Dev Notes

### Story Type: Foundational infrastructure — the single audit-write boundary

This story ships the **only** writer to `audit_log`. Every system that performs an auditable action (Hands launching apps, Brain logging deletions, Nerve transitioning tiers, Ritual capturing seeds) calls `audit_logger.log_action(...)` — and no system writes to `audit_log` directly. Enforcement: `AuditLogger` is the only module that imports the engine for an `INSERT INTO audit_log` purpose; the read path goes through Brain's `SqliteBrainAdapter` (Story 3.x).

The story is small and tightly scoped — one class, one method, one SQL string, ~80–110 lines of production code. The carefulness lives in the **observational-failure semantics** (AC #5), the **excluded-context opacity contract** (AC #7), and the **append-only guarantee** (AC #6). Each of those is locked by dedicated tests because each represents a class of bug that, if introduced, would silently degrade the trust model the audit trail exists to support.

### Scope guard (hard stop)

- **Do NOT touch `app.py`, `cli.py`, or the composition root.** Wiring is Story 1.10. This story delivers the module + tests.
- **Do NOT subscribe to the event bus.** `AuditLogger` is a passive write-side primitive driven by callers. Nerve (Story 3.5) is the subscriber that maps `TierChanged` events to `log_action(ActionType.TIER_CHANGE, ...)` calls.
- **Do NOT create `ports/brain.py` or any port file.** Story 1.9 owns the port layer.
- **Do NOT create `systems/hands/`, `systems/nerve/`, or any system module.** Stories 3.5+ own those.
- **Do NOT create or modify a migration.** The `audit_log` table is created by `001_initial_schema.py` (Story 1.5). This story consumes the schema as-is.
- **Do NOT add a `query_recent()` / `read_audit_trail()` / `get_actions_since(...)` method.** The read path lives in Brain (Story 3.x → Story 5.3). `AuditLogger` is write-only by design.
- **Do NOT add an `update_action()` / `delete_action()` / `clear()` method.** The audit trail is append-only (architecture.md:854). Locked by `test_audit_logger_has_no_update_or_delete_methods`.
- **Do NOT add a background-task batcher** (`asyncio.create_task(self._background_writer())`). T1 audit writes are inline-awaited; batching is at the earliest a T2 concern. AC #9 spells out why.
- **Do NOT add an in-memory ring buffer / fallback queue** for failed writes. AC #5 says: log the failure, return None, primary action continues. Trying to retry from a buffer (a) breaks the observational guarantee (the buffered write might land much later, with a stale timestamp), (b) introduces a parallel write path that is exempt from the engine's serialization, and (c) is un-specced behavior.
- **Do NOT validate `target` against the exclusion list.** That is upstream policy. `AuditLogger` accepts whatever the caller passes; the caller is responsible for opacity.
- **Do NOT inspect `details` for sensitive field names** (`api_key`, `password`, etc.). That is also upstream policy. `AuditLogger` JSON-serializes whatever the caller passes (and propagates `TypeError` if it isn't serializable).
- **Do NOT add an `audit_log` index from this story.** No callers query `audit_log` yet (Story 5.3 is the first reader). When indexes become measurably needed, they go in a new numbered migration, not in this story.
- **Do NOT add an `audit_log` retention/rotation policy.** Architecture.md and the epics defer rotation to T2 (epics.md:1422 — "Audit log entries are retained indefinitely in T1 (rotation is a T2 concern)").
- **If `audit.py` grows past ~150 lines of production code, you are over-building.** Target: ~30 lines for module-level constants + type alias + docstring, ~10 lines for `__init__`, ~50 lines for `log_action` (including docstring + the try/except + the json-serialize block), ~10 lines for the `_INSERT_SQL` constant + module imports. ~80–110 production lines total.

### Critical constraints and gotchas

- **Audit write failure does NOT block the primary action.** This is THE single most important behavior in the story. The `try/except StorageError:` around `await self._storage.execute(...)` is load-bearing. LLMs frequently "helpfully" re-raise the exception "for visibility" — re-raising would invert the contract and let a single DB hiccup take down a successful app launch / mode switch / deletion. Tests `test_log_action_swallows_storage_error_*` lock the regression.
- **`asyncio.CancelledError` MUST propagate.** Project-context.md:49 is explicit. The narrow `except StorageError:` does not catch `CancelledError` (which inherits from `BaseException` in py3.12). Do NOT broaden to `except Exception:` — that would still skip `CancelledError` (because `CancelledError` is `BaseException`, not `Exception`), but it would also swallow non-domain bugs in the engine, hiding them. The narrow `except StorageError:` is correct.
- **`TypeError` from `json.dumps` MUST propagate.** It's a caller-supplied bad-input bug, not an audit-infrastructure failure. Surfacing it loudly at the call site forces the caller to convert non-serializable values (like `datetime`, `Path`, `set`) at their boundary. Locked by `test_log_action_propagates_typeerror_from_non_serializable_details`.
- **Non-`StorageError` exceptions from `execute` MUST propagate.** If the engine raises something other than `StorageError`, that's an engine bug (it failed to translate at its boundary). Hiding it inside `AuditLogger` would mask the real issue. Locked by `test_log_action_propagates_non_domain_exception`.
- **Empty / whitespace-only `result` is rejected with `ValueError`.** The `audit_log.result` column is `NOT NULL` and an empty value is a render footgun. Reject at the API boundary, not at the DB boundary (which would surface as a `StorageError` and get swallowed — leaving NO row and NO clear caller signal).
- **`details` is JSON-serialized with `ensure_ascii=False`.** Unicode targets / values (e.g., a Japanese app name in a future Eyes capture path) round-trip without `\uXXXX` escaping. The column is TEXT; sqlite3 stores UTF-8 by default; the JSON string is UTF-8 too. **`separators=(",", ":")`** for compact serialization (no whitespace between tokens) — saves a few bytes per row on a column that may grow large under heavy use.
- **`None` and `{}` produce different rows.** `details=None` → `NULL` in the column. `details={}` → `"{}"` (literal string) in the column. Tests cover both. Document the distinction in the method docstring.
- **Reuse `_utc_now_iso` from `core/events.py`.** Project-context.md:46 mandates this for every timestamp-emitting module. Inlining `datetime.now(UTC).isoformat()` here would (a) violate the rule, (b) defeat the deterministic-clock test pattern (tests monkeypatch `nova.core.events._utc_now_iso`), and (c) drift the timestamp serialization format from `events.py` over time. Tests for this story monkeypatch the SAME `_utc_now_iso` to assert deterministic timestamp values appear in the persisted row.
- **Capture the timestamp at the START of `log_action`, NOT inside the try/except.** The timestamp records when the action **happened** (call time), not when sqlite finished writing. If the engine's write queues or retries, the timestamp must remain pinned to the moment of the action.
- **Type `details` as `Mapping[str, object] | None`, NOT `dict[str, Any] | None`.** `Mapping` is read-only at the type level — callers can pass any concrete mapping (`dict`, `MappingProxyType`, frozen-dict-like wrappers) without `AuditLogger` requiring mutation rights. `object` (not `Any`) is what `json.dumps` accepts; `Any` would defeat mypy strict at every call site (project-context.md:47). Callers narrow at their boundary.
- **No `asyncio.Lock` in `AuditLogger`.** The storage engine already serializes writes via `_tx_lock` (Story 1.4/1.5). A second lock here would (a) be redundant, (b) deadlock if a future caller is itself inside `engine.transaction()` while calling `log_action`. Trust the engine's serialization.
- **Logging `target` or `details` in the failure WARNING is forbidden.** The audit row contains both because that is the audit table's purpose; the log file is a separate trust boundary, and the log handler — not `AuditLogger` — is the privacy boundary for log content (per project-context.md:129). Mirroring either field into the log `extra` would risk leaking caller-supplied content the audit row was structured to handle. `target` is dropped because the caller-opacity contract (`"protected_app"` for excluded apps) is unenforced at this boundary; `details` is dropped because it carries arbitrary structured caller context. The WARNING includes only `action_type` + `result`; combined with the chained `StorageError` traceback (via `exc_info=True`), an analyst can find the caller without seeing the payload.
- **Successful audit write logs at DEBUG, not INFO.** Production runs at INFO (per Story 1.10's logging defaults); successful audit writes happen on every app launch, mode switch, and seed capture — emitting them at INFO would drown the log file in successful-audit noise. DEBUG runs (development, troubleshooting) get the trail.
- **WARNING (not ERROR) for audit-write failure.** Per project-context.md:129, ERROR is "failures" (system-level); WARNING is "degraded behavior" (the system continues but a sub-feature is missing). Audit-write failure is the latter — the primary action succeeds; only the audit row is lost.
- **Tests use real SQLite (`tmp_path`-based path), not a mock.** Stories 1.4/1.5 set the precedent for storage tests: real engine, real migrations. The audit logger sits directly on top of the engine, and a mock engine would lose the actual `INSERT` semantics — including the column-not-null enforcement, the parameter binding behavior, and the JSON round-trip. Real engine tests catch real issues.
- **Use `tmp_path` fixture (pytest stdlib), not `Path(":memory:")`.** Sqlite's `:memory:` requires `check_same_thread=False` to share across threads, and the storage engine's WAL-mode pragma may not behave identically against `:memory:` as against an on-disk file. Stories 1.4/1.5 used `tmp_path` — follow that. Pytest auto-cleans `tmp_path` between tests.
- **Engine `await engine.close()` in every test.** Per project-context.md:104 ("Tests must leave no pending tasks, open connections, unclosed resources"). Use a try/finally OR an async fixture. The `_make_logger(tmp_path)` helper returns the engine so the test can close it.
- **No `print()` anywhere** (project-context.md:44, ruff `T20`) — logger only.

### Repo shape at time of this story

After Stories 1.0–1.7 the repo contains:

- `src/nova/core/__init__.py` (re-exports 32 names; this story takes it to 37)
- `src/nova/core/events.py` — `Event`, `EventBus`, all typed events including `TierChanged`, `_utc_now_iso` (Story 1.3)
- `src/nova/core/exceptions.py` — `NovaError`, `StorageError`, `ConfigError`, `ApiUnavailableError`, `ModeNotFoundError`, `AdapterError` (Story 1.2)
- `src/nova/core/types.py` — `CapabilityTier`, `BriefingState`, `SnapshotType`, `ActionType` (11 members), `MemoryCategory`, `BluntnessLevel` (Story 1.2)
- `src/nova/core/config.py` — `load_config` + 6 frozen dataclasses (Story 1.6)
- `src/nova/core/storage/engine.py` + migrations including `001_initial_schema.py` (Stories 1.4–1.5) — the `audit_log` table is already created
- `src/nova/core/tiers.py` — `HealthCheck` Protocol + `TierManager` class (Story 1.7)
- `src/nova/core/audit.py` does NOT exist yet — this story creates it
- `src/nova/{app,cli}.py` are Story 1.1 placeholders — NOT touched here
- `src/nova/adapters/*`, `src/nova/systems/*`, `src/nova/ports/*`, `src/nova/setup/*` are empty package shells
- `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py`, `test_events.py`, `test_storage_engine.py`, `test_migration_runner.py`, `test_config.py`, `test_tiers.py` exist
- No `tests/unit/core/test_audit.py` — this story creates it
- Tests pass: 461 + 1 skipped (462 collected) at Story 1.7 end

This story **adds**:

- `src/nova/core/audit.py` (new — `AuditLogger` class + `RESULT_*` constants + `ActionResult` type alias)
- `tests/unit/core/test_audit.py` (new — ~22–28 tests per AC #15)

This story **modifies**:

- `src/nova/core/__init__.py` — add 5 re-exports (`ActionResult`, `AuditLogger`, `RESULT_FAILED`, `RESULT_SKIPPED`, `RESULT_SUCCESS`), alphabetized
- `tests/unit/core/test_core_isolation.py` — add `audit_module` allowlist frozenset + 4 tests + 2 parametrize-list extensions + 1 dispatch branch in `test_no_dynamic_imports_of_forbidden_modules`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story lifecycle transitions

This story does NOT modify:

- `pyproject.toml` (no new deps; all needed imports are stdlib or already-present first-party)
- `src/nova/app.py`, `src/nova/cli.py`
- Any file under `config/`, `adapters/`, `systems/`, `ports/`, `setup/`
- `docs/config-schemas.md`
- `src/nova/core/events.py` (`_utc_now_iso` is consumed, not modified)
- `src/nova/core/types.py` (`ActionType` is consumed, not modified)
- `src/nova/core/exceptions.py` (`StorageError` is consumed, not modified)
- `src/nova/core/storage/engine.py` (`SqliteStorageEngine` is consumed, not modified)
- `src/nova/core/storage/migrations/001_initial_schema.py` (the `audit_log` table is already correctly defined)

### Previous Story Intelligence — Story 1.7 (done 2026-04-15)

Story 1.7 landed the capability tier state machine. Key carry-forwards for Story 1.8:

- **Test file placement — `tests/unit/core/test_audit.py`, flat under `unit/core/`.** Mirrors `test_tiers.py`, `test_config.py`, `test_events.py`, `test_migration_runner.py`. No subdirectory, no `__init__.py`.
- **Helper factories at top of test file, not in conftest.** `_make_logger`, `_FailingExecuteEngine` are module-level functions/classes in the test file. No fixtures added to `tests/conftest.py` (Story 1.4/1.5/1.6/1.7 precedent — conftest stays minimal).
- **Structured-logging `extra={...}` pattern.** Every `logger.debug` / `logger.warning(..., exc_info=True)` carries a typed dict in `extra=`. Free-form interpolation is forbidden. `caplog.records[i].action_type` is the test assertion style.
- **Opaque messages — schema-level, not data-level.** WARNING log says `"audit write failed; primary action continues"`, NOT `f"audit write failed for app {target} with details {details}"`. Detail lives in the chained exception (`exc_info=True` captures the traceback) and the structured `extra`, not in the message body.
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `SIM105` (prefer `contextlib.suppress`) does NOT fire because the `except StorageError:` block does real work (logs the failure with structured `extra`) — `SIM105` only fires on `try/except/pass` (empty handler).
- **mypy strict, zero `# type: ignore` in production code.** No `cast`, no `Any`. `Mapping[str, object]` is the precise type for `details`; `Literal["success", "failed", "skipped"]` is the precise type for `ActionResult`.
- **Commit convention (Story 1.4/1.5/1.6/1.7 carry-forward):** terse, imperative, story ID prefix. Expected: `"Story 1.8: audit logger (core/audit.py)"`.
- **AST-based static-analysis tests, not text regex.** Story 1.6/1.7 carry-forward explicit. The `test_audit_module_uses_only_insert_sql` and `test_audit_logger_class_has_no_mutating_methods_beyond_log_action` tests follow this rule.
- **`TIERS_FORBIDDEN_TOPLEVEL_MODULES` / `TIERS_ALLOWED_TOPLEVEL_MODULES` pattern** is the template for `AUDIT_FORBIDDEN_TOPLEVEL_MODULES` / `AUDIT_ALLOWED_TOPLEVEL_MODULES`. Story 1.7's carve-out was none; Story 1.8 also has none (audit.py imports nothing from the forbidden set — sqlite3 access is transitive via the engine).
- **Two-function clock pattern** (Story 1.3) is the timestamp-generation convention. `AuditLogger` reuses `_utc_now_iso` from `core/events.py` rather than declaring its own pair, because the audit row's timestamp semantically aligns with the event-bus timestamps (both record "when did this happen in the system"). Tests monkeypatch `nova.core.events._utc_now_iso` and verify the patched value appears in the persisted `audit_log.timestamp` column.
- **`SqliteStorageEngine` consumption pattern** (Stories 1.4/1.5): construct via `SqliteStorageEngine(db_path)`, `await engine.start()`, `await engine.run_migrations()`, then use. Test cleanup: `await engine.close()` in try/finally. Story 1.5's `test_migration_runner.py` and Story 1.4's `test_storage_engine.py` are the references for the test scaffolding shape.
- **`Mapping[str, object]` over `dict[str, Any]` for typed JSON-shaped inputs.** This is the project's general pattern: `Mapping` for read-only contract, `object` (not `Any`) at the value position, callers narrow at their boundary. Story 1.6's `NovaConfig` used `Mapping[str, object]` for raw YAML payloads with the same rationale.

### Git Intelligence — last 5 commits

```
ab2f676 Story 1.7: capability tier state machine (core/tiers.py)
ba24622 Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)
c64849c Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
7278eb9 Story 1.3: event bus + typed event classes (core/events.py)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow exactly.
- **"New core module" pattern established by Stories 1.3 / 1.4 / 1.5 / 1.6 / 1.7.** Every new core module ships with: the production file, a dedicated test file under `tests/unit/core/`, an isolation-test allowlist frozenset in `test_core_isolation.py`, and a re-export entry in `core/__init__.py`. Story 1.8 follows the same shape — the only module-level difference is NO carve-out from the forbidden-imports set (audit.py reaches sqlite3 transitively via the engine, never directly).
- **No prior `audit.py` or `test_audit.py`** in the tree. Greenfield for this story.

### Latest Tech Information (as of 2026-04-15)

- **Python 3.12.x** — `json.dumps` with `ensure_ascii=False` is stable. `collections.abc.Mapping` (PEP 585) is the canonical home; do NOT import `Mapping` from `typing`. ruff `UP035` enforces this on py312.
- **`asyncio_mode = "auto"` in pyproject.toml** — pytest-asyncio auto-mode means every `async def test_*` is automatically run as asyncio without `@pytest.mark.asyncio`. Matches the style used in `test_events.py` (Story 1.3), `test_storage_engine.py` (Story 1.4), `test_tiers.py` (Story 1.7).
- **PEP 695 `type` keyword** for `type ActionResult = Literal[...]`. Established in Story 1.4 (`type SqlParams = Sequence[...]`). ruff `UP040` enforces this on py312; do NOT use `typing.TypeAlias`.
- **`typing.Literal[...]`** is stable since 3.8. `Literal["success", "failed", "skipped"]` narrows the return-type / parameter-type to exactly those three string values at the type level.
- **`collections.abc.Mapping` vs `dict`** — `Mapping` is the read-only contract; `dict` is the concrete mutable implementation. Type parameters as `Mapping` to accept the widest set of caller types (`dict`, `MappingProxyType`, `OrderedDict`, frozen-dict-like wrappers).
- **`json.dumps` with `separators=(",", ":")`** — compact serialization, no whitespace. Default `(", ", ": ")` adds spaces; for storage rows (which can grow large), compact is cheaper.
- **`json.dumps` with `ensure_ascii=False`** — emits raw UTF-8 instead of `\uXXXX` escape sequences for non-ASCII characters. Sqlite3 TEXT columns are UTF-8 by default; round-tripping unicode without escaping is human-readable AND smaller.
- **`StorageError` inherits from `NovaError`** which inherits from `Exception`. `except StorageError:` is precise narrowing — does NOT catch `CancelledError` (which is `BaseException` in py3.12), does NOT catch unrelated `RuntimeError` from the engine.
- **`logger.warning(..., exc_info=True)` vs `logger.exception(...)`** — both attach `exc_info` (the traceback) to the LogRecord, but `logger.exception()` is hard-coded to **ERROR level** in the Python stdlib (`Logger.exception` calls `self.error(...)` internally). This story logs audit-write failures at WARNING (per project-context.md:129 — degraded behavior, not system failure), so the correct call is `logger.warning("...", extra={...}, exc_info=True)`. Reaching for `logger.exception(...)` here would silently upgrade the severity. The chained traceback (the `from err` chain on `StorageError`) is preserved in the record either way; the choice is purely about severity level.

### Project Structure Notes

- **Production file:** `src/nova/core/audit.py` — path pinned by architecture.md:1190 + 1384 (`audit.py — AuditLogger — single audit interface`).
- **Test file:** `tests/unit/core/test_audit.py` — flat under `unit/core/`, mirrors every other core-module test file.
- **Architecture.md sketch alignment:** architecture.md:1190–1194 sketches `class AuditLogger: async def log_action(self, action_type: str, target: str | None, result: str, details: dict | None = None) -> None`. This story's `log_action` signature **strengthens the types**: `action_type: ActionType` (not `str`), `details: Mapping[str, object] | None` (not `dict | None`). Both strengthenings are deliberate per the no-magic-literals + Mapping-over-dict project rules. The architecture sketch is pseudocode; the strict-typed form is the pinned production shape. Any reader comparing the two should treat the strict-typed signature as the source of truth.
- **Integration test file `tests/integration/test_exclusion_boundary.py`** (architecture.md:1427) is NOT this story's concern — that test exercises the full capture → storage → audit → transparency chain with real Eyes/Brain/AuditLogger. Story 4.x ships integration coverage. This story's unit tests fully cover `AuditLogger` in isolation against a real engine.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio (auto mode, already enabled) + pytest-cov. All tests in this story are `async def` because `AuditLogger.log_action` is `async`.
- **Unit tests** live in `tests/unit/core/test_audit.py`. ~22–28 tests per AC #15.
- **No integration tests in this story.** Integration coverage of the audit → exclusion boundary is Epic 4.
- **mypy strict** applies to both the production module and the test file. Annotate every helper return type. `-> None` on every test.
- **Real SQLite engine + tmp_path** — no mocks for the engine itself. Failure-mode tests use the `_FailingExecuteEngine` subclass that overrides only `execute`.
- **`await engine.close()` in every test** (project-context.md:104). Use try/finally OR an async fixture wrapping `_make_logger`.
- **Each test constructs its own engine + logger via `_make_logger(tmp_path)`.** No shared state, no cross-test contamination.
- **No fixtures added to `tests/conftest.py`** (Story 1.4/1.5/1.6/1.7 carry-forward).
- **Coverage target:** 100% of `audit.py`. Every branch (None vs empty-dict details, success vs failure path, validation rejection, type propagation).
- **Failure-path coverage — every exception class has at least one test:**
  - `StorageError` from engine → swallowed, WARNING logged, returns None
  - `RuntimeError` (non-domain) from engine → propagates
  - `asyncio.CancelledError` from engine → propagates
  - `TypeError` from `json.dumps(details)` → propagates
  - `ValueError` from result validation (empty / whitespace-only) → propagates
- **Deterministic timestamp via `monkeypatch.setattr("nova.core.events._utc_now_iso", lambda: "2026-04-15T12:00:00+00:00")`** for tests that assert the persisted timestamp value. The patch works because `audit.py` imports the events **module** (`from nova.core import events`) and calls `events._utc_now_iso()` at use time, so the lookup happens against the patched module attribute on every call. If `audit.py` had done `from nova.core.events import _utc_now_iso` instead, the patch would silently no-op (audit's local binding would still point at the original function). One dedicated test — `test_persisted_timestamp_uses_monkeypatched_clock` — asserts a row written under the monkeypatch contains the patched timestamp value, locking the indirection contract against accidental import refactors. Tests that don't care about the value just assert it's a non-empty string with the `+00:00` suffix.

### Critical Don't-Miss Rules (from project-context.md + architecture.md + epics.md)

Carry-forward with rationale for this story:

- **"Use the AuditLogger for all automated action logging — never write to audit_log directly."** (project-context.md:73, architecture.md:1187/1274) — this story materializes the boundary. Future stories that need to log an action import `AuditLogger`, never reach into the `audit_log` table.
- **"Audit logging is observational, not transactional."** (project-context.md:86, architecture.md:262) — locked by AC #5 + tests. Audit-write failure does not block the primary action.
- **"Append-only — no updates, no deletes of audit entries."** (architecture.md:854, epics AC) — locked by `test_audit_logger_has_no_update_or_delete_methods` + AST guard on the SQL constant.
- **"Excluded context details never enter audit trail."** (architecture.md:583, project-context.md:72) — `AuditLogger` enforces by **typing** (no struct-shaped `target`/`details` parameters that could smuggle excluded fields). Upstream callers enforce by **policy** (Eyes filters at capture; Hands/Nerve pass `"protected_app"` opaque references).
- **"Never swallow `asyncio.CancelledError`."** (project-context.md:49) — the narrow `except StorageError:` does not catch `CancelledError` (which is `BaseException` in py3.12). Locked by `test_log_action_propagates_cancelled_error`.
- **"Timeouts required at external boundaries."** (project-context.md:50) — N/A here; the storage engine handles its own timeout/locking boundary, and `AuditLogger` is one layer above it.
- **"No `print()` anywhere."** (project-context.md:44; ruff `T20`) — logger only.
- **"Structured logging."** (project-context.md:128) — every log call uses `extra={...}` with typed keys. Neither `target` nor `details` is ever in `extra` (opacity by construction — see AC #5 / #10).
- **"Stable serialization only — enums serialize as stable string values."** (project-context.md:56) — `str(ActionType.APP_LAUNCH) == "app_launch"`; persists exactly that string into `audit_log.action_type`.
- **"No Any in application code."** (project-context.md:47) — `Mapping[str, object]` for `details`, `Literal[...]` for `ActionResult`. No `cast`, no `# type: ignore`.
- **"Absolute imports only."** (project-context.md:43) — `from nova.core import events` (module form, NOT `from nova.core.events import _utc_now_iso` — see AC #2 for the monkeypatch-preservation rationale), `from nova.core.exceptions import StorageError`, `from nova.core.storage.engine import SqliteStorageEngine`, `from nova.core.types import ActionType`.
- **"No mutable module-level runtime state."** (project-context.md:55) — module-level has only `logger`, `RESULT_*` constants, `ActionResult` type alias, `_INSERT_SQL` constant. All instance state lives in `self._storage` on each `AuditLogger`.
- **"Adapters may translate, never decide."** (project-context.md:77) — N/A here; `AuditLogger` is core, not an adapter.
- **"Opaque references for sensitive content in exception/log messages."** (project-context.md:176) — WARNING log message is the canonical opaque string `"audit write failed; primary action continues"`; structured `extra` carries only `action_type` + `result`; `target` and `details` are dropped from the log path entirely (opacity by construction — see AC #5 / #10).
- **"Two-function clock pattern."** (project-context.md:46) — reuse `_utc_now_iso` from `core/events.py` rather than declaring a second pair. Test determinism via monkeypatch on the canonical name.
- **"Schema changes route through a new numbered story."** — `audit_log` columns and types are pinned by Story 1.5's `001_initial_schema.py`. This story does NOT add a column, does NOT add an index, does NOT add a constraint.
- **"Persist before emit."** (project-context.md:78) — N/A here; `AuditLogger` is the persist call, not an event emitter.
- **"Each domain fact has one owner."** (project-context.md:80) — `AuditLogger` owns the `audit_log` write surface. Brain owns the `audit_log` read surface (Story 5.3). Clean ownership split.

### Project Structure Notes

- Alignment with unified project structure: `core/audit.py` sits alongside `core/config.py`, `core/events.py`, `core/exceptions.py`, `core/types.py`, `core/tiers.py`. All lowest-layer cross-cutting infrastructure. Import path: `from nova.core.audit import AuditLogger`.
- No conflicts or variances detected. The module fits the established "core is cross-cutting, no adapters, no systems" shape exactly.

### Cross-story impact (what depends on this story's primitives)

See AC #16 for the consumer table. **Twelve downstream stories** consume `AuditLogger`. The first production callers are Stories 3.5–3.7 (Nerve, mode restore, shutdown flow) — they will exercise the observational-failure contract heavily. Keeping `log_action`'s contract minimal (one method, one signature, one observational-failure semantic) is load-bearing for all twelve.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.8: Audit Logger](../planning-artifacts/epics.md) — canonical AC, lines 798–815.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives, lines 359–890.
- [Source: _bmad-output/planning-artifacts/architecture.md#Audit Logging Convention](../planning-artifacts/architecture.md) — lines 1185–1202, the AuditLogger class sketch + rules.
- [Source: _bmad-output/planning-artifacts/architecture.md#Audit Trail (cross-cutting)](../planning-artifacts/architecture.md) — lines 848–854, the cross-cutting role + queryability + append-only contract.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 3: SQLite Schema Design](../planning-artifacts/architecture.md) — lines 567–584, the `audit_log` table definition + design rules + opaque-target rule.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 1384, `core/audit.py` location.
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 41 (no raw SQL outside migrations), 43 (absolute imports), 44 (no print), 46 (two-function clock pattern), 47 (no Any), 49 (never swallow CancelledError), 55 (no module-level mutable state), 56 (stable enum serialization), 67 (Brain owns SQLite tables — with the documented AuditLogger exception), 72 (exclusion at capture), 73 (AuditLogger is the only writer to audit_log), 86 (audit logging is observational), 128 (structured logging), 129 (log levels), 131 (no magic literals), 176 (no sensitive content in exceptions/logs).
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `ActionType` StrEnum (11 members), lines 72–91.
- [Source: src/nova/core/events.py](../../src/nova/core/events.py) — `_utc_now_iso` canonical clock, lines 74–89; the two-function clock pattern.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `StorageError`, lines 73–80.
- [Source: src/nova/core/storage/engine.py](../../src/nova/core/storage/engine.py) — `SqliteStorageEngine.execute`, lines 332–372 (the call this story makes); engine lifecycle (`start`, `close`) for test scaffolding.
- [Source: src/nova/core/storage/migrations/001_initial_schema.py](../../src/nova/core/storage/migrations/001_initial_schema.py) — `audit_log` table DDL, lines 62–73.
- [Source: _bmad-output/implementation-artifacts/1-7-capability-tier-state-machine.md](./1-7-capability-tier-state-machine.md) — prior story. Test file layout, `test_core_isolation.py` allowlist pattern, AST-based static-analysis precedent, commit style.
- [Source: _bmad-output/implementation-artifacts/1-5-migration-runner-and-initial-schema.md](./1-5-migration-runner-and-initial-schema.md) — `audit_log` table is created here; engine + tmp_path test pattern.
- [Source: _bmad-output/implementation-artifacts/1-4-sqlite-storage-engine.md](./1-4-sqlite-storage-engine.md) — `SqliteStorageEngine` API + lifecycle; PEP 695 `type` alias precedent.
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — the allowlist pattern and AST inspection helpers this story extends.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- Pytest first pass surfaced one signature-test failure: `inspect.signature(...).parameters["action_type"].annotation is ActionType` returned the **string** `'ActionType'`, not the class. Cause: `from __future__ import annotations` makes every annotation a lazy string. Fix: switch the three signature-locking tests to `typing.get_type_hints(AuditLogger.log_action)` which resolves the strings against the function's globals. Now the equality check is against the actual class object.
- Pytest second pass surfaced two further bug clusters in the test scaffolding:
  1. `_FailingExecuteEngine` raised on **every** `execute` call. The factory called `await engine.run_migrations()` after `start()`, which itself dispatches multiple `execute` calls into the engine — those tripped the failure mode before the test ever got to invoke `log_action`. Fix: introduced an `arm()` flag on the subclass; the factory runs migrations first, then arms. `_armed=False` short-circuits to `super().execute(...)` so setup paths behave normally.
  2. `get_origin(str | None)` returns `types.UnionType` (PEP 604 syntax), not `typing.Union`. The two type-signature tests were comparing against `Union` only and failed with `<class 'types.UnionType'> is Union`. Fix: imported `types.UnionType` and accept either origin in the assertion.
- Ruff first pass flagged 9 issues: 1 `I001` (mis-sorted import block — `import nova.core.audit as audit_module` was at the bottom past the `from`-imports; consolidated into the alphabetized block), 1 `F401` (`from nova.core import events` was imported into the test file but never referenced — removed; only the production module needs the live import, tests reach the patched function via `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` string targeting), and 7 `E501` line-too-long. Most long lines were broken on natural seams (list-comp expansions, multi-statement docstring splits); the AST class-walk got rewritten as a list comprehension to satisfy `SIM102` after the initial `if/if` split.
- Ruff format pass reformatted the long `_INSERT_SQL` string into a single line (under 100 chars without the implicit concatenation) and tightened the `assert public_methods == ["log_action"]` failure message into one f-string. Both changes were autonomic and left the production semantics unchanged.
- Mypy first pass flagged 3 errors: 2 `unused-ignore` (`# type: ignore[override]` on `_FailingExecuteEngine.execute` and `# type: ignore[attr-defined]` on `ActionResult.__value__` — both turned out unnecessary; the override matches the parent signature now that `params: Any = ()` is used, and PEP 695 type aliases expose `__value__` natively in py312) and 1 `func-returns-value` (`result = await logger.log_action(...)` assigned the return of a `-> None` method). Fix: dropped the two ignores, dropped the assignment, added a comment explaining the implicit-assertion intent.
- Carry-forward from Story 1.6/1.7 review lessons honored: AST-based static-analysis tests (not text regex) for the append-only SQL guard and the "only `log_action` is public" guard. The new `test_audit_imports_events_module_not_utc_now_iso_symbol` AST gate locks the module-call indirection that preserves the deterministic-clock monkeypatch contract — catches future refactors that would silently break Story 1.3's two-function pattern at this consumer.
- One refinement vs. the story draft (recorded for transparency): `_INSERT_SQL` ended up on a single line (`"INSERT INTO audit_log (timestamp, action_type, target, result, details) VALUES (?, ?, ?, ?, ?)"`, 93 chars) rather than the two-piece concatenation in the original draft. Ruff's auto-format consolidated it; the concatenation was a pre-emptive line-length fix that turned out unnecessary.

### Completion Notes List

- Shipped `src/nova/core/audit.py` (~205 lines including docstrings). Exposes exactly `AuditLogger`, `ActionResult`, `RESULT_SUCCESS`, `RESULT_FAILED`, `RESULT_SKIPPED`. No other names; `__all__` is the public contract.
- Observational-failure semantics fully implemented and test-locked:
  - `StorageError` from `engine.execute` is caught, logged at WARNING with `exc_info=True` (NOT `logger.exception(...)` — that would log at ERROR), and swallowed. The caller's `await` returns `None` normally.
  - `RuntimeError` and any other non-domain exception propagates — surfaces engine translation bugs instead of hiding them.
  - `asyncio.CancelledError` propagates (the narrow `except StorageError:` does not catch it; `CancelledError` is `BaseException` in py3.12 anyway).
  - `TypeError` from `json.dumps(details)` propagates — caller-supplied non-serializable values surface loudly at the call site, not silently as a missing audit row.
  - `ValueError("result must be a non-empty string")` raised at the API boundary for empty / whitespace-only `result`. Locks the NOT-NULL-but-meaningful invariant before it reaches sqlite.
- Append-only contract enforced at module-structure level by two AST guards:
  - `test_audit_logger_class_has_no_mutating_methods_beyond_log_action` walks the class body and asserts the only public method defined is `log_action`. Catches a future edit that bolts on `update_action` / `delete_action` / `clear` / etc.
  - `test_audit_module_uses_only_insert_sql_no_update_or_delete` walks all `ast.Constant(str)` nodes in the module and rejects any that match `\b(UPDATE|DELETE|REPLACE|TRUNCATE|ALTER|DROP)\b\s+(?:.*?)?audit_log` (case-insensitive). Positively asserts at least one `INSERT INTO audit_log` constant exists.
- Excluded-context opacity by typing: `target` is `str | None`, `details` is `Mapping[str, object] | None`. Two `get_type_hints`-based tests (`test_log_action_target_is_typed_as_optional_str_only` and `test_log_action_details_is_typed_as_optional_mapping_str_object_only`) lock the annotations against accidental loosening. Verifying via `get_type_hints` (not raw `inspect.signature` string compare) handles `from __future__ import annotations` lazy resolution correctly.
- Two-function clock pattern preserved through module-call indirection. `audit.py` imports the events MODULE (`from nova.core import events`) and calls `events._utc_now_iso()` at use time — `monkeypatch.setattr("nova.core.events._utc_now_iso", ...)` propagates into this call site automatically. `test_persisted_timestamp_uses_monkeypatched_clock` writes a row under the monkeypatch and asserts the persisted column equals the sentinel. `test_audit_imports_events_module_not_utc_now_iso_symbol` walks the audit.py AST and rejects any `from nova.core.events import _utc_now_iso` form that would freeze the local binding and silently defeat the patch — locks the indirection against accidental refactor.
- JSON serialization: `json.dumps(details, separators=(",", ":"), ensure_ascii=False)`. Compact form (no whitespace between tokens) saves bytes on a column that may grow large; `ensure_ascii=False` round-trips unicode (`日本語`) into the TEXT column without `\uXXXX` escaping. Both behaviors test-locked.
- `None` vs `{}` distinction preserved: `details=None` writes SQL `NULL` (column is nullable); `details={}` writes the literal string `"{}"`. `test_log_action_distinguishes_none_from_empty_dict_details` locks both branches.
- Action-type vocabulary fully covered: `@pytest.mark.parametrize("member", list(ActionType))` exercises all 11 enum members against a real engine + INSERT round-trip. Adding a 12th member to `ActionType` (a future Story 1.2 schema change) auto-extends this coverage without touching the test file.
- Concurrency: `asyncio.gather` of 3 concurrent `log_action` calls produces 3 intact rows. `SqliteStorageEngine`'s internal `_tx_lock` (Story 1.4/1.5) handles serialization; `AuditLogger` does NOT layer a second lock per AC #9.
- Isolation guardrail extended: `core/audit.py` is fully isolated (no sqlite3, no yaml, no anthropic, no rich, no Win32). `AUDIT_FORBIDDEN_TOPLEVEL_MODULES == FORBIDDEN_TOPLEVEL_MODULES` (no carve-out — sqlite3 access is transitive via `SqliteStorageEngine`); `AUDIT_ALLOWED_TOPLEVEL_MODULES` is a 6-entry stdlib + `nova` allowlist (`__future__`, `collections`, `json`, `logging`, `nova`, `typing`).
- Re-export count: `src/nova/core/__init__.py` 32 → 37 names (added `ActionResult`, `AuditLogger`, `RESULT_FAILED`, `RESULT_SKIPPED`, `RESULT_SUCCESS`). Alphabetized (Story 1.2 monotonic-ordering test passes).
- Quality gate green end-to-end: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0. Final pytest line: `510 passed, 1 skipped in 5.09s` (baseline 466 + 1 skipped from Story 1.7; +44 new tests).
- Repo tree clean: no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.db-wal`, `*.db-shm` introduced.
- Scope guards respected: no modifications to `app.py`, `cli.py`, any port file, any system/adapter stub, any `config/` shipped default, the `001_initial_schema.py` migration, or `events.py` / `types.py` / `exceptions.py` / `storage/engine.py` (all consumed, none modified). No event-bus subscription (Nerve is the future bridge per Story 3.5); no `query_recent` / `read_audit_trail` method (read path is Brain's per Story 5.3); no batching, no background tasks, no in-memory ring buffer.
- Story 1.10 handoff: composition root calls `AuditLogger(storage=engine)` AFTER `await engine.start()` and `await engine.run_migrations()`. Story 3.5+ injects this single instance into Nerve / Hands / Brain-for-deletions / Ritual-for-seed-captures.

### File List

**New files:**

- `src/nova/core/audit.py` — `AuditLogger` class + `RESULT_*` constants + `ActionResult` PEP 695 type alias + `_INSERT_SQL` private constant. ~205 lines including docstrings.
- `tests/unit/core/test_audit.py` — 38 tests covering shape (3), happy-path inserts (5), action-type vocabulary (13 — 11 parametrized + 2 signature), result validation (3), details serialization (3), observational-failure semantics (4), append-only AST guards (2), type-signature opacity (2), concurrency (1), monkeypatch-contract (1), and module-call indirection AST guard (1). ~565 lines.

**Modified:**

- `src/nova/core/__init__.py` — added 5 re-exports (`ActionResult`, `AuditLogger`, `RESULT_FAILED`, `RESULT_SKIPPED`, `RESULT_SUCCESS`); `__all__` re-alphabetized (37 names).
- `tests/unit/core/test_core_isolation.py` — added `audit_module` import, `AUDIT_FORBIDDEN_TOPLEVEL_MODULES` + `AUDIT_ALLOWED_TOPLEVEL_MODULES` frozensets, 4 audit-specific isolation tests (`test_audit_forbidden_imports`, `test_audit_imports_within_allowlist`, `test_audit_does_not_import_nova_adapters_or_systems`, `test_audit_does_not_dynamically_import_nova_adapters_or_systems`), extended 2 parametrize lists (`test_no_relative_imports`, `test_no_dynamic_imports_of_forbidden_modules`) to include `audit_module`, and added an `audit_module` branch in the dynamic-import forbidden-set dispatch.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story key transitioned `ready-for-dev` → `in-progress` → `review`; `last_updated` bumped to 2026-04-15.
- `_bmad-output/implementation-artifacts/1-8-audit-logger.md` — this file: task checkboxes, Dev Agent Record, File List, Status.

**Not modified (verified clean):**

- `pyproject.toml` — no new deps; all needed imports are stdlib or already-present first-party.
- `src/nova/app.py`, `src/nova/cli.py` — composition-root wiring is Story 1.10.
- `src/nova/core/events.py`, `src/nova/core/types.py`, `src/nova/core/exceptions.py`, `src/nova/core/storage/engine.py`, `src/nova/core/storage/migrations/001_initial_schema.py` — `_utc_now_iso`, `ActionType`, `StorageError`, `SqliteStorageEngine`, `audit_log` table all consumed; none modified.
- `src/nova/core/config.py`, `src/nova/core/tiers.py` — unrelated.
- Any file under `config/`, `adapters/`, `systems/`, `ports/`, `setup/`.
- `docs/config-schemas.md`.
