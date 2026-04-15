# Deferred Work

Items flagged during reviews that are real but not actionable in the story where they were found. Each entry notes the origin, the target story for pickup, and the one-liner reason.

---

## Deferred from: code review of story 1-0-define-yaml-config-schemas-spike (2026-04-14)

- **Duplicate YAML keys at same level.** PyYAML `safe_load` accepts duplicates silently (last-wins), causing silent data loss. **Target: Story 1.6 (config loader).** Loader can use `yaml.SafeLoader` subclass that rejects duplicates.
- **Unicode case-fold edge case (Turkish locale, dotted/dotless i).** `.casefold()` handles most Latin scripts correctly; Turkish is the documented failure mode. **Target: Story 1.6 (config loader) / Story 4.2 (exclusion matcher).** Address if a user reports the bug.
- **Modes directory exists as a file (AV quarantine / user error).** Undefined behavior when `%LOCALAPPDATA%/nova/modes` is not a directory. **Target: Story 1.6 (config loader).** Loader should detect and produce a user-facing error distinct from "no modes found".
- **CRLF trailing whitespace in string values.** Not applicable in current T1 schema (no multiline strings), but re-check if future fields add them. **Target: whichever story adds multiline string fields.**
- **First-run copy file-lock / antivirus interference.** Edge case during initial setup where target path is locked. **Target: Story 2.1 (setup.bat / first-run).** Setup should retry with clear error if locked.
- **Reserved Windows filenames (CON, NUL, AUX, etc.) in mode names.** Slugified mode names could collide with reserved names. **Target: Story 2.3 (guided mode wizard)** — validate at creation time; **Story 1.6 (loader)** — surface clear error if encountered on existing file.

---

## Deferred from: code review of story 1-1-project-scaffolding-and-package-setup (2026-04-14)

- **uv.lock `revision = 3` requires recent uv on CI.** Lockfile pins a revision older uv versions will reject. **Target: Story 1.11 (CI quality-gate automation).** Document a minimum `uv` version and pin it in CI runner setup.
- **Coverage config `[tool.coverage.*]` absent.** `pytest-cov` is installed but no thresholds or report config wired. **Target: Story 1.11 (CI quality-gate automation).** Original story design already defers this.
- **`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`.** Belt-and-suspenders for CI report artifacts that don't exist today. **Target: Story 1.11 (CI).** Add when CI actually generates these files.
- **Hatchling default sdist includes `_bmad-output/`, `design-artifacts/`, and the full planning tree.** Only matters if N.O.V.A. is ever published to PyPI — project-context rules this out for T1. **Target: whichever story turns on package publishing (none currently planned).**
- **PEP 735 `[dependency-groups]` migration.** uv and PDM are converging on `[dependency-groups]` over `[project.optional-dependencies]` for dev deps. **Target: monitor — revisit when uv's guidance stabilizes or when Story 1.11 touches dep config.**

---

## Deferred from: code review of story 1-2-domain-exceptions-and-shared-types (2026-04-14)

- **`NovaError.cause` is not preserved across `pickle` / `copy.deepcopy` round-trips.** `BaseException.__reduce__` only serializes `self.args`; the constructor's `cause=` kwarg is dropped on round-trip. **Target: whichever story introduces multiprocessing, subprocess workers, or remote-process IPC (none in T1 — architecture is single-process).** Document the limitation in `core/exceptions.py` module docstring; revisit only if cross-process exception transport ever becomes a requirement.
- **AST isolation test crashes on namespace / zipped / frozen module deployments.** `inspect.getsourcefile()` returns `None` (or a zip-internal path that `Path.read_text()` cannot open) under `pyinstaller --onefile`, `zipapp`, or namespace-package layouts. Today the test asserts non-None. **Target: whichever story introduces packaging beyond `uv sync` + `uv run nova` (none in T1).** When a packaging story lands, switch to `importlib.resources.files(module).joinpath(...).read_text()` or guard with `pytest.skip` when source is unavailable.

---

## Deferred from: code review of story 1-3-event-bus-and-typed-event-definitions (2026-04-14)

- **Frozen dataclass `__hash__` and `__eq__` contracts not locked by tests.** `@dataclass(frozen=True)` auto-generates `__hash__`; a future field of unhashable type (e.g. `dict`, `list`, a mutable dataclass) breaks `hash()` at runtime. **Target: whichever downstream story first uses events as dict keys or set members (Story 3.5 Nerve routing is the likely first consumer).** Add parametrized hash/equality tests then.
- **Pickle / `copy.deepcopy` round-trip of events not tested.** Not used in T1 (single-process, no IPC, no durable event persistence). **Target: Story 1.8 AuditLogger if it ever serializes event references.** Architecture currently records actions, not events, so this may never become relevant.
- **Field-schema string comparison is fragile under formatter changes.** `from __future__ import annotations` stores `f.type` as raw source text; reformatting or switching between `Optional[str]` and `str | None` breaks the test without a real type-contract change. **Target: revisit if the test starts churning during refactors.** Migration path: switch to `typing.get_type_hints(cls)` for runtime type resolution.

---

## Deferred from: code review of story 1-4-sqlite-storage-engine (2026-04-14)

- **`asyncio.get_running_loop()` drift between calls is not enforced.** The class docstring claims single-loop-per-engine-instance, but each async method re-reads the running loop. If an engine is (accidentally) driven across two different event loops — e.g., `asyncio.run(engine.start()); asyncio.run(engine.execute(...))` — the second `run_in_executor` dispatches onto the new loop while `self._executor`'s worker still holds the connection from loop #1. **Target: Story 1.10 (composition root).** That story wires the single-loop lifetime; add an assertion there if cross-loop misuse is ever a realistic risk. Not a current bug.
- **Corrupt-DB-that-opens-but-fails-on-pragma path not covered by tests.** `sqlite3.connect` is lazy — it can succeed on a corrupt file and the first pragma raises `sqlite3.DatabaseError`. The inner cleanup in `_open_and_configure_sync` is correct by inspection (closes conn before re-raise) but has no regression test. The WAL-silent-fallback test (`test_wal_verification_rejects_silent_fallback`) exercises an adjacent path but not the corrupt-DB one. **Target: Story 5.5 (SQLite corruption recovery flow) — naturally needs a corrupt-DB fixture.** Reuse the fixture to backfill the pragma-failure regression test here.
- **DDL + implicit-transaction failure-carryover edge cases not tested.** `isolation_level="DEFERRED"` auto-BEGINs on first DDL/DML; a failed `execute()` leaves the implicit transaction in a rolled-back state. Subsequent calls work (sqlite3 carries on), but there's no test. Happy path is locked. **Target: Story 3.1+ (Brain adapter) — first real multi-statement consumer.**
- **Windows long-path (`MAX_PATH` >260 char) and directory-as-db-path not tested.** Both produce distinct errors (OSError from mkdir vs. `sqlite3.OperationalError("unable to open database file")`), both translate to `StorageError("start failed")`, but neither is locked by a test. **Target: whichever story introduces packaging / user data dir hardening** (Story 2.1 setup.bat, Story 5.5 corruption recovery). Low priority — current error-translation is generic enough to handle both.

## Deferred from: code review of story 1-5-migration-runner-and-initial-schema (2026-04-14)

- **`_FILENAME_RE` only matches 3-digit prefixes — silent skip for v1000+ migrations.** Pattern `r"(?P<num>\d{3})_[a-z][a-z0-9_]*\.py"` requires exactly 3 digits. A future `1000_something.py` is silently ignored (regex returns None → file is treated as "not a migration") with no error. **Target: post-T1 (we will not author 1000+ migrations in T1).** Fix when refactoring the runner for future scale: change to `\d+` and let the file/version cross-check fire.
- **`test_transaction_rolls_back_on_cancellation` is timing-dependent (`asyncio.sleep(0.05)` race).** Test creates a task, sleeps 50ms, then cancels — expecting cancellation to land inside the inner `asyncio.sleep(10.0)` rather than during the `INSERT`. Under CI load, the cancel could fire during `INSERT` and the assertion path may differ. **Target: test-hygiene pass.** Replace the wall-clock sleep with an `asyncio.Event` set after the INSERT completes inside the task.
- **`_write_pkg` helper leaves synthetic packages in `sys.modules` after teardown.** `monkeypatch.syspath_prepend` reverts `sys.path` but `importlib.import_module` permanently caches the module. Per-test counter prevents collision in current usage. **Target: test-hygiene pass.** Add `monkeypatch.delitem(sys.modules, ...)` cleanup inside the helper so the cache is torn down with the test.

## Deferred from: code review of story 1-6-config-loader-and-immutable-novaconfig (2026-04-14)

- **`!!python/object` YAML tag rejection not test-locked.** `SafeLoader` rejects it by construction; adding an explicit regression test is belt-and-suspenders. **Target: whichever story next widens the loader surface, OR a test-hygiene pass.** Low risk — the constructor-map for unsafe tags is inherited from `SafeLoader`.
- **Broken-symlink mode files skipped silently.** `entry.is_file()` returns `False` on a broken symlink → `_load_modes` silently skips it with no warning. Debugging trap on first-run misconfigurations. **Target: Story 2.1 (setup.bat / first-run)** — that's where symlink-aware setup would first matter. Fix: add `entry.is_symlink() and not entry.exists()` branch with a warning.
- **URL validation allows embedded control chars / NUL bytes.** `"http://\x00malicious"` passes the scheme-prefix check. Downstream browser behavior is undefined. **Target: Story 3.6 (Mode Restore & App Launching)** — that's where URLs are actually opened by Hands; control-char screening belongs at the use site, not the load site. Fix: `any(ord(c) < 0x20 or ord(c) == 0x7F for c in entry)` rejection in whichever validator the restore path consults.

## Deferred from: code review of story 1-7-capability-tier-state-machine (2026-04-15)

- **Canonical reason string `"2 consecutive API failures"` hardcodes the default threshold.** If an operator sets `degrade_failure_threshold=5`, the emitted `TierChanged.reason` still says "2 consecutive", misleading Story 5.4's tier-notice renderer. The closed canonical set in AC #7 is load-bearing; threshold-aware interpolation would break the closed-set guarantee. Not exercised in T1 (no consumer sets a custom threshold). **Target:** whichever story first ships an operator-facing threshold knob. Fix options: (1) validate `degrade_failure_threshold == 2` at construction and require a story-level schema change to widen; (2) expand the canonical set to include threshold-parameterized variants as a deliberate schema update.
- **`CancelledError` mid-`event_bus.emit` leaves tier advanced with no observer event.** Tier state is mutated before the `emit` begins; if the coroutine is cancelled inside `emit`, downstream subscribers never learn of the transition. Edge case during process shutdown where the composition root is tearing down subscribers anyway. **Target:** Story 3.10 (Crash Recovery) or Story 8.3 (Tier Recovery & Catch-up Briefing). Fix options: `asyncio.shield` around the emit, or a catch-up replay on next boot that reconciles last-known-tier vs. current-state.
- **`test_emitted_event_has_source_nerve` tests a `TierChanged` field default rather than a behavior `TierManager` owns.** The test passes because `source="nerve"` is a `field(default=..., init=False)` in the event class, not because `TierManager` sets it. A future event-schema change would fail this test pointing at `TierManager` instead of the real cause. Test-quality polish, not a functional regression. **Target:** next test-hygiene pass.
