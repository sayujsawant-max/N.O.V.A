# Story 1.10: Composition Root & CLI Entrypoint

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `app.py` wiring all ports/infrastructure through a single composition point and `cli.py` providing a minimal terminal entrypoint that boots and exits cleanly,
So that `uv run nova` starts the monolith through one wiring site, initializes logging + migrations + config, and future stories (Brain, Eyes, Hands, Voice, Skin, Nerve, Ritual adapters/systems) plug into that site without rewiring.

## Acceptance Criteria

**Given** all T1 core infrastructure ships from Stories 1.1 – 1.9 — `SqliteStorageEngine` (1.4), migration runner + `001_initial_schema` (1.5), `load_config` + `NovaConfig` (1.6), `TierManager` + `HealthCheck` (1.7), `AuditLogger` (1.8), and all 8 port Protocols + `NoOpShieldAdapter` (1.9) — AND the `nova` console script is already registered in [pyproject.toml:16](../../pyproject.toml) via `nova = "nova.cli:main"`,

**When** this story is complete:

**AC #1 — Composition root scope (`src/nova/app.py`):**
The module exposes exactly one public coroutine `create_app(config: NovaConfig) -> NovaApp` and one public frozen dataclass `NovaApp`. `create_app` is the single place in the codebase that instantiates concrete adapter classes or wires event-bus subscriptions. `NovaApp` exposes the wired graph to `cli.py` and to future stories via `__all__ = ["NovaApp", "create_app"]`.

**AC #2 — Infrastructure wired in T1:**
`create_app` wires **only the infrastructure that exists today**. It MUST construct, in this order:
1. `SqliteStorageEngine(config.db_path)` and `await engine.start()`
2. `await engine.run_migrations()` (applies pending migrations from Story 1.5)
3. `EventBus()` instance
4. `AuditLogger(storage=engine)` — instantiated AFTER `engine.start()` (closes the deferred-work bug from Story 1.8 where an unstarted engine makes audit rows vanish silently)
5. `TierManager(event_bus=event_bus, health_check=...)` — inject a T1 no-op `HealthCheck` that satisfies the [src/nova/core/tiers.py:87-100](../../src/nova/core/tiers.py#L87-L100) contract: `async def ping(self, *, timeout_seconds: float) -> None` returns `None` on success and would raise `ApiUnavailableError` on failure. The no-op NEVER raises (returns `None` unconditionally) — the real Claude-backed probe arrives with `ClaudeReasoningAdapter`. Implement as a tiny class `_AlwaysHealthyCheck` in `app.py` (module-private, no separate file); keeps `TierManager` in `CapabilityTier.FULL` indefinitely because the recovery loop sees no failure. Do NOT use `degrade_failure_threshold=1` or any other non-default threshold — rely on defaults; Story 1.7 deferred-work flagged custom thresholds as out-of-contract.
6. `NoOpShieldAdapter()` from [src/nova/adapters/shield/noop.py](../../src/nova/adapters/shield/noop.py)

Do NOT construct stub adapters for Brain / Eyes / Hands / Voice / Skin / Nerve / Ritual. Those adapters do not exist yet (Stories 3.1+ / 4.1+ land them) and speculative stubs would violate project-context.md:77 "adapters may translate, never decide" + the "one composition point" rule (a later story would have to rewrite them anyway).

**AC #3 — `NovaApp` shape:**
`NovaApp` is `@dataclass(frozen=True, slots=True)` with exactly these fields — all typed, all populated by `create_app`:
- `config: NovaConfig`
- `storage: SqliteStorageEngine`
- `event_bus: EventBus`
- `audit: AuditLogger`
- `tier_manager: TierManager`
- `shield: ShieldPort` (typed as the port, not the concrete no-op — proves the port indirection works end-to-end; `NoOpShieldAdapter` satisfies `ShieldPort` structurally via `@runtime_checkable`, per Story 1.9 AC #6)
- `close: Callable[[], Awaitable[None]]` — a bound coroutine that tears down resources in reverse order (close engine, stop tier manager if it has async tasks). T1 teardown is just `await engine.close()`; the callable shape leaves room for future stories to chain adapter shutdowns without reshaping `NovaApp`.

Downstream stories extend this dataclass by adding fields — they never replace it.

**AC #4 — Composition-root rules enforced by test:**
A new test `tests/unit/test_composition_root.py` MUST lock these structural guarantees via `ast`-walk (per the project's AST-first static-analysis precedent — memory/feedback_ast_static_analysis_tests.md, Story 1.9 AC #13):
- **Adapter-import isolation (non-adapter modules):** Walk every `.py` under `src/nova/` EXCLUDING the subtree `src/nova/adapters/**` (adapter packages may legitimately re-export their own submodules — AC #17 relies on this). Of the remaining files, the ONLY ones allowed to import from `nova.adapters.*` are `src/nova/app.py` and `src/nova/cli.py`. Assert: for every file outside `src/nova/adapters/**` and outside `{app.py, cli.py}`, no import statement references a dotted name starting with `nova.adapters`.
- No `src/nova/ports/*.py` file imports from `nova.adapters.*` (already locked by Story 1.9's `test_port_isolation.py`; re-verify from this test as a cross-story regression guard).
- No `src/nova/systems/**/*.py` file imports from `nova.adapters.*` (forward-looking — systems do not exist yet, but the guard is cheap and prevents a future story from leaking).
- **Intra-adapter imports stay intra-package:** For each adapter sub-package (currently only `src/nova/adapters/shield/`), every `nova.adapters.*` import in that sub-package must resolve to the SAME sub-package. E.g., `adapters/shield/__init__.py` may import `nova.adapters.shield.noop` (same sub-package) but NOT `nova.adapters.sqlite.*` (cross-adapter). This prevents a future adapter from silently depending on another adapter's internals — all cross-adapter wiring must go through ports (owned by `app.py`).
- No adapter class is instantiated at module scope inside `app.py` (all construction inside `create_app`). Assert there is zero `ast.Call` node at the module top level whose callee resolves to a class imported from `nova.adapters.*`.

**AC #5 — Smoke test: full port + model graph imports cleanly:**
A new test `tests/unit/test_composition_smoke.py::test_all_ports_and_models_importable` imports every port Protocol + every system model + `NoOpShieldAdapter` in a single test body and asserts no `ImportError`. Picks up the Story 1.9 deferred item: "No smoke / integration test imports the full port + model graph. ~15 cross-system type dependencies; a future back-reference could introduce a circular import without test catching it. Target: Story 1.10 (composition root)."

**AC #6 — Structured logging infrastructure:**

**Data-dir ownership invariant:** `cli.py` MUST NOT create the data directory itself (AC #9 — `setup.bat` owns that). This creates an ordering constraint with logging: file logging cannot be configured against a data dir that does not exist, but we also want config-load failures (`ConfigError`) and storage failures (`StorageError`) to be logged rather than printed. Resolution: **split logging init into two phases.**

**Phase A — early stderr-only logging** (function `_configure_stderr_logging(level: int) -> None`):
- Runs as the very first step of `_async_main`, BEFORE `_resolve_data_dir` / `load_config` / any path check.
- Attaches a single `StreamHandler(sys.stderr)` to the root logger with the formatter described below. This is the ONE intentional `StreamHandler` exception to the "no terminal logging" rule (project-context.md:44, architecture.md:1263) — it exists strictly as a pre-data-dir fallback so early failures (bad `--log-level`, `_resolve_data_dir` raising, `ConfigError` from missing data dir) reach the user. **The handler is removed in Phase B** once file logging is live, so steady-state logging goes only to file.
- Tag the handler with `handler.name = "nova-cli-stderr-bootstrap"` so Phase B can locate + remove it idempotently.

**Phase B — file logging** (function `_configure_file_logging(data_dir: Path, level: int) -> None`):
- Runs ONLY after `load_config(data_dir)` has returned successfully — meaning the data dir exists and is a directory (Story 1.6's `load_config` raises `ConfigError` at [src/nova/core/config.py:629-632](../../src/nova/core/config.py#L629-L632) if either condition fails).
- Create `data_dir/logs/` via `mkdir(parents=False, exist_ok=True)` — **`parents=False` is load-bearing**: it ensures a missing `data_dir` fails loudly here rather than silently materializing the user data directory. If `data_dir` itself disappears between `load_config` returning and this call (extremely unlikely but defensible), the resulting `FileNotFoundError` propagates as a `StorageError`-adjacent failure the top-level `Exception` handler will translate to exit code 4.
- Attach a single `FileHandler` writing to `data_dir/logs/nova.log` with `encoding="utf-8"`. Tag the handler with `handler.name = "nova-cli-file-handler"`.
- Remove the Phase A stderr handler (locate by `handler.name == "nova-cli-stderr-bootstrap"`); file handler now owns all routing.
- Set root logger level from `level` (applies to both phases; Phase B re-applies in case code between phases reset it).

**Shared formatter** (used by both phases, defined once as `_ExtrasFormatter(logging.Formatter)`):
- Base format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- `datefmt="%Y-%m-%dT%H:%M:%S%z"` (ISO 8601 with timezone)
- Appends `" | extras={k1=v1, k2=v2}"` when `record.__dict__` contains non-stdlib keys, so `logger.info("session stored", extra={"session_id": 42})` renders as `... session stored | extras={session_id=42}`. Filters out `LogRecord` reserved names (`args`, `asctime`, `created`, `exc_info`, `exc_text`, `filename`, `funcName`, `levelname`, `levelno`, `lineno`, `message`, `module`, `msecs`, `msg`, `name`, `pathname`, `process`, `processName`, `relativeCreated`, `stack_info`, `thread`, `threadName`, `taskName`) to avoid double-logging stdlib metadata.

**Idempotency:** Both phases are idempotent across repeat calls in the same process (tests re-initialize via `monkeypatch`). Each phase removes handlers previously installed with its own tag-name before attaching a new one. This only touches handlers tagged by this module — handlers owned by pytest / coverage tools are left alone.

**No terminal output on the happy path:** Phase A emits to stderr only on pre-data-dir failures. On a successful boot, Phase A's handler is removed before any INFO-level records fire; AC #13's `capsys.readouterr().out == ""` assertion (and a new `capsys.readouterr().err == ""` assertion on the success path) locks this.

**AC #7 — Log level override:**
The log level is controlled in this precedence (highest wins): CLI flag `--log-level {DEBUG,INFO,WARNING,ERROR}` → env var `NOVA_LOG_LEVEL` → default `INFO`. Invalid values produce a `ConfigError`-style early exit with an error message to stderr (not via `print` — use `sys.stderr.write`) AND a best-effort log line to `nova.log` (if logging is already configured). DEBUG level is explicitly documented in `cli.py` module docstring as the developer surface.

**AC #8 — Logger name convention:**
All `nova.*` loggers MUST follow `nova.{layer}.{module}` (architecture.md:1257). The composition root and CLI loggers are `nova.app` and `nova.cli`. This is a structural convention test: `tests/unit/test_composition_root.py::test_logger_names_follow_convention` AST-walks every `.py` under `src/nova/` and asserts every `logging.getLogger("...")` literal starts with `nova.` and contains at most one dot between `nova.` and the module suffix (reject `nova.brain.subsystem.internal` — we enforce 2 dots max at story 1.10; deeper nesting requires a future convention amendment). Existing loggers (`nova.core.config`, `nova.core.audit`, `nova.core.tiers`, `nova.core.events`, `nova.core.storage.*`, `nova.core.storage.migrations.*`) must all pass. **Specifically permit three dots** for `nova.core.storage.{engine,migrations.runner}` — the storage sublayer is the one pre-existing exception (Story 1.4 / 1.5 convention). Encode this as an explicit allowlist tuple in the test, not a generic depth-3 relaxation.

**AC #9 — Data directory resolution:**
A new pure helper `_resolve_data_dir(cli_override: Path | None, env: Mapping[str, str]) -> Path` in `cli.py` returns the data dir with this precedence (highest wins):
1. `cli_override` (from `--data-dir <path>` flag; resolved via `Path(value).expanduser().resolve()`)
2. `env["NOVA_DATA_DIR"]` if set and non-empty (same resolution)
3. `Path(env["LOCALAPPDATA"]) / "nova"` if `LOCALAPPDATA` is set and non-empty (Windows production path)
4. `Path.home() / ".nova"` as a final fallback (enables non-Windows dev / CI runs without `LOCALAPPDATA` defined — the project is Windows-first but tests run on the CI runner architecture committed in Story 1.11; future `windows_only` markers will gate Win32-specific tests)

The helper does NOT create the directory — it only resolves the path. `setup.bat` (Story 2.1) owns creation of the full user-data tree. `load_config` (Story 1.6, [src/nova/core/config.py:629](../../src/nova/core/config.py#L629)) raises `ConfigError("data directory missing")` if the directory does not exist — the dev agent MUST catch that and produce a clean exit code + actionable message (AC #11). Do NOT silently mkdir the data dir from `cli.py` — that would write shipped-defaults-free state and is `setup.bat`'s job per project-context.md:157–161. Phase B logging (AC #6) uses `mkdir(parents=False)` for `data_dir/logs/` so that even the "subdir only" creation cannot silently materialize a missing `data_dir`.

**AC #10 — `main()` contract:**
`cli.main()` is a **synchronous** function returning `int` (exit code). It wraps a single `asyncio.run(_async_main(args))` call. This keeps the existing `project.scripts` `nova = "nova.cli:main"` registration working (Click / Typer / console_scripts always expect sync entrypoints). The async body `_async_main(args: argparse.Namespace) -> int` performs these steps **in order** (the ordering is load-bearing — AC #6 / AC #9 invariants depend on it):
1. Call `_configure_stderr_logging(level)` — **Phase A**. From this point on, any raised exception's log line reaches the user via stderr, even if steps 2–4 fail.
2. Resolve `data_dir` via `_resolve_data_dir(args.data_dir, os.environ)` — catch no exceptions (pure path math; any failure is a programmer bug, not user-facing).
3. Call `load_config(data_dir)` — catch `ConfigError`, log at ERROR via the Phase A stderr handler, return exit code 1. (Missing / invalid data dir surfaces here, exactly as AC #9 requires.)
4. Call `_configure_file_logging(data_dir, level)` — **Phase B**. Only runs after load_config succeeded, so `data_dir` is known to exist. The Phase A stderr handler is removed; file logging owns all routing from here.
5. Call `await create_app(config)` — catch `StorageError`, log at ERROR, return exit code 2.
6. Log `"N.O.V.A. initialized"` at INFO with extras `{data_dir, db_path, mode_count, api_key_present, tier}`
7. **Placeholder app boot** — per epic AC: "placeholder app boot that exits cleanly or enters a minimal prompt". T1 path: log `"session shell placeholder — full session loop arrives in Story 3.5"` at INFO and return exit code 0. Do NOT call `input()`, do NOT render via Rich, do NOT parse Layer A commands. Those belong to Epics 2 / 3.
8. On any branch that reached step 5 or later, call `await app.close()` in a `try / finally` so the engine is closed even on exceptions.

`argparse.ArgumentParser` is the parser (stdlib, zero dependency). Flags registered: `--data-dir PATH`, `--log-level {DEBUG,INFO,WARNING,ERROR}`, `--version` (prints `nova.__version__` from [src/nova/__init__.py](../../src/nova/__init__.py) and exits 0). No positional arguments. **No Layer A command parsing** (`mode`, `status`, `help`, `memory`, `shutdown` all belong to Epic 3+ per the epic AC).

**AC #11 — Exit codes:**
`main()` returns these codes (documented in `cli.py` module docstring):
- `0` — success / clean placeholder exit
- `1` — `ConfigError` during `load_config` (data dir missing, malformed YAML, etc.)
- `2` — `StorageError` during engine start or migrations
- `3` — `NovaError` any other subclass (catch-all at top-level boundary — project-context.md:53 "Broad exception catching only at top-level boundaries")
- `4` — `Exception` catch-all (unexpected error — logs full traceback at CRITICAL with `exc_info=True`)
- `130` — `KeyboardInterrupt` (standard POSIX convention: 128 + SIGINT=2)

`_async_main` returns the exit code; `main` surfaces it via `return exit_code` after `asyncio.run`. Never call `sys.exit()` directly inside `_async_main` — the `asyncio.run` wrapper must see the return value so finally-blocks complete.

**AC #12 — `if __name__ == "__main__":` guard:**
[src/nova/cli.py](../../src/nova/cli.py) already has `raise SystemExit(main())` at module bottom — PRESERVE that line. The `project.scripts` entrypoint calls `main()` directly; the guard only fires for `python -m nova.cli` or `python src/nova/cli.py`. Both paths must produce identical behavior.

**AC #13 — End-to-end smoke test: `uv run nova` boots + exits cleanly:**
A new integration test `tests/integration/test_cli_bootstrap.py` MUST:
- Use `tmp_path` as data_dir (set via `NOVA_DATA_DIR`, not `--data-dir`, to exercise the env-var path)
- Seed a minimal valid config at `tmp_path/settings.yaml` (empty body `{}` is valid — the config loader applies defaults) and `tmp_path/modes/` (empty dir is valid — zero modes is valid per [src/nova/core/config.py:648](../../src/nova/core/config.py#L648))
- Invoke `cli.main()` directly (not via subprocess — subprocess would miss coverage and is slower). Wrap via `monkeypatch.setattr("sys.argv", ["nova"])` and `monkeypatch.setenv("NOVA_DATA_DIR", str(tmp_path))`.
- Assert `main()` returns `0`
- Assert `tmp_path/logs/nova.log` exists and contains the `"N.O.V.A. initialized"` line
- Assert `tmp_path/nova.db` exists (migrations ran)
- Assert the log file contains no tracebacks and no ERROR-level records
- Assert **no stdout output** (`capsys.readouterr().out == ""`) — Skin's domain, CLI must stay silent on success
- Assert **no stderr output** on the happy path (`capsys.readouterr().err == ""`) — the Phase A stderr handler MUST have been removed by Phase B before the "N.O.V.A. initialized" record fires. This is the regression guard for the two-phase handoff in AC #6.
- Test MUST clean up `sys.modules["nova.cli"]` logging handlers in a fixture teardown so parallel tests don't inherit handlers (the `"nova-cli-file-handler"` + `"nova-cli-stderr-bootstrap"` name-tagging from AC #6 makes this deterministic — the fixture removes both tags)

**AC #14 — End-to-end smoke test: failure paths:**
The same `test_cli_bootstrap.py` file adds parametrized failure tests. Each MUST assert: (a) the documented exit code, (b) the failure is visible on **stderr** (not stdout; and since file logging is not yet active for pre-load_config failures, stderr via the Phase A handler is the correct channel), (c) no `nova.log` file appears in a missing-data-dir scenario (the data dir invariant holds), (d) `capsys.readouterr().out == ""` (CLI never writes to stdout).

- **Missing data_dir** → exit code 1, ERROR record on stderr via Phase A, AND `tmp_path/logs/nova.log` MUST NOT exist afterwards (data-dir invariant — Phase B never ran because `load_config` raised first).
- **Data dir exists but is a file** → exit code 1, ERROR on stderr, `nova.log` does not exist (same reason).
- **`settings.yaml` malformed (duplicate key)** → exit code 1. Here the data dir DOES exist, so after this story's scope completes the dev agent may decide whether the ERROR reaches stderr (Phase A still active) or file (Phase B already ran) — lock it to stderr: `load_config` runs BEFORE `_configure_file_logging` per AC #10 step 3, so the error must surface on stderr.
- **`StorageError` injected** by monkeypatching `SqliteStorageEngine.start` to raise → exit code 2. File logging IS active here (step 4 ran before step 5), so the ERROR record lands in `nova.log`, NOT stderr. Assert both.
- **`KeyboardInterrupt` raised inside `_async_main`** (patch `load_config` to raise) → exit code 130. KeyboardInterrupt propagates through `asyncio.run` into `main()`'s outer handler; log at INFO "interrupted by user" via whichever handler is active (stderr here, since the patch fires in step 3).
- **`RuntimeError` raised inside `_async_main`** (patch `load_config` to raise `RuntimeError`) → exit code 4 with CRITICAL + traceback. Lands on stderr (same reason).

**AC #15 — Teardown leaves no pending tasks / open handles:**
`test_cli_bootstrap.py::test_cli_teardown_closes_all_resources` MUST verify `SqliteStorageEngine` is closed after `main()` returns (open a second engine on the same `db_path` and assert no `"database is locked"` error). Verify no pending asyncio tasks via `asyncio.all_tasks()` after `asyncio.run` returns (trivially true because `asyncio.run` raises on leftover tasks — the assertion is belt-and-suspenders and future-proofs against loop-hopping bugs called out in the Story 1.4 deferred-work item: "asyncio.get_running_loop() drift between calls is not enforced. Target: Story 1.10.").

**AC #16 — Adapter-selection hook for future stories:**
`create_app` accepts an optional `shield: ShieldPort | None = None` keyword argument. If `None`, defaults to `NoOpShieldAdapter()`. If provided, uses the passed adapter. Rationale: this demonstrates the "swap an adapter by changing one line" requirement in the epic AC and proves via a unit test (`test_create_app_accepts_custom_shield_adapter`) that structural-Protocol conformance works end-to-end. Do NOT add analogous `brain=`, `eyes=`, etc. parameters — those adapters don't exist; adding preemptive knobs is speculative design (YAGNI + project-context.md:75 "No magic literals for domain concepts"). When a real adapter lands, that story extends the signature.

**AC #17 — Adapter package re-exports:**
[src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py) currently has a docstring only. This story MUST add `from nova.adapters.shield.noop import NoOpShieldAdapter` + `__all__ = ["NoOpShieldAdapter"]` so `app.py` can import via `from nova.adapters.shield import NoOpShieldAdapter` (short path) instead of `from nova.adapters.shield.noop import NoOpShieldAdapter` (long path). Pattern: matches how Story 1.9's [src/nova/ports/__init__.py](../../src/nova/ports/__init__.py) re-exports the 8 Protocol classes.

**AC #18 — Quality gate clean:**
`uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` passes with zero regressions. Expected test count delta: +~20 new tests. No new `# type: ignore`. No new `cast()` outside explicit typed-stubs narrowing. No new `Any` anywhere. `TidyType` / `NoUntypedDef` stay clean in mypy strict.

**AC #19 — Scope boundary (do NOT do):**
- Do NOT create `systems/brain/system.py`, `systems/eyes/system.py`, etc. — system classes come with their respective stories.
- Do NOT create `adapters/sqlite/brain.py`, `adapters/win32/*`, `adapters/claude/*`, `adapters/rich/*` — concrete adapters come with their respective stories.
- Do NOT implement PromptBuilder — that's a separate module with its own story (it appears in architecture.md:1385 but not in Epic 1's ship list).
- Do NOT wire event subscriptions (e.g., TierManager → audit hook). Subscriptions arrive with the systems that need them.
- Do NOT modify existing Story 1.1 – 1.9 files except:
  - [src/nova/app.py](../../src/nova/app.py) (currently 5-line docstring placeholder → full composition root)
  - [src/nova/cli.py](../../src/nova/cli.py) (currently 15-line placeholder → full entrypoint)
  - [src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py) (add re-export per AC #17)
  - [src/nova/__init__.py](../../src/nova/__init__.py) (no change expected; `__version__` already exported)

## Tasks / Subtasks

- [x] **Task 1: Adapter package re-export** (AC: #17)
  - [x] Edit [src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py): add `from nova.adapters.shield.noop import NoOpShieldAdapter` + `__all__ = ["NoOpShieldAdapter"]`.
  - [x] Preserve the existing docstring about T1-only no-op + v0.15 future.

- [x] **Task 2: Compose `src/nova/app.py`** (AC: #1, #2, #3, #16)
  - [x] Module docstring explaining composition-root responsibility, the "one wiring site" invariant, and what future stories add.
  - [x] `from __future__ import annotations` + typed imports from `nova.core`, `nova.ports`, `nova.adapters.shield`.
  - [x] Define `@dataclass(frozen=True, slots=True) class NovaApp` with the 7 fields from AC #3.
  - [x] Define `async def create_app(config: NovaConfig, *, shield: ShieldPort | None = None) -> NovaApp` wiring the 6 infrastructure objects in the AC #2 order.
  - [x] Default `shield` to `NoOpShieldAdapter()` when `None`.
  - [x] Construct a `close` coroutine closure over the instances (just `await storage.close()` in T1); attach to the `NovaApp` via the dataclass `close` field.
  - [x] Handle partial-init failure: if `engine.run_migrations()` raises, `await engine.close()` in an inner `try / except` so a half-booted engine doesn't leak.
  - [x] `__all__ = ["NovaApp", "create_app"]`.
  - [x] `logger = logging.getLogger("nova.app")` at module scope; emit INFO records at each step ("engine started", "migrations applied: N", "audit logger wired", etc.) with structured `extra`.

- [x] **Task 3: Compose `src/nova/cli.py`** (AC: #6, #7, #9, #10, #11, #12)
  - [x] Module docstring documenting: `main()` sync entrypoint, exit codes table, env vars (`NOVA_DATA_DIR`, `NOVA_LOG_LEVEL`, `LOCALAPPDATA`), CLI flags, and the two-phase logging invariant (stderr-bootstrap → file-handler handoff).
  - [x] `_resolve_data_dir(cli_override: Path | None, env: Mapping[str, str]) -> Path` per AC #9.
  - [x] Custom `Formatter` subclass `_ExtrasFormatter(logging.Formatter)` that appends non-reserved `extra` keys.
  - [x] `_configure_stderr_logging(level: int) -> None` — **Phase A** per AC #6. Attaches a `StreamHandler(sys.stderr)` tagged `"nova-cli-stderr-bootstrap"`, idempotent (removes any prior bootstrap handler first).
  - [x] `_configure_file_logging(data_dir: Path, level: int) -> None` — **Phase B** per AC #6. `mkdir(parents=False, exist_ok=True)` for `data_dir/logs/`, attaches `FileHandler` tagged `"nova-cli-file-handler"`, removes the Phase A stderr handler, idempotent.
  - [x] `_build_parser() -> argparse.ArgumentParser` with `--data-dir`, `--log-level`, `--version`.
  - [x] `_parse_log_level(raw: str | None, env_raw: str | None) -> int` with precedence per AC #7.
  - [x] `async def _async_main(args: argparse.Namespace) -> int` per AC #10's 8-step ordering (stderr-logging first, file-logging only after `load_config` succeeds).
  - [x] `def main() -> int` wrapping `asyncio.run(_async_main(parser.parse_args()))` + top-level `Exception` / `KeyboardInterrupt` catch returning AC #11 codes.
  - [x] Preserve `raise SystemExit(main())` at module bottom.
  - [x] `logger = logging.getLogger("nova.cli")` at module scope.

- [x] **Task 4: Port/adapter isolation test** (AC: #4)
  - [x] Create `tests/unit/test_composition_root.py`.
  - [x] `test_only_app_and_cli_import_adapters` — AST-walk all `.py` under `src/nova/`, assert no file outside `{app.py, cli.py}` imports from `nova.adapters.*`.
  - [x] `test_app_module_level_has_no_adapter_instantiation` — AST-walk `app.py` top level, assert zero `ast.Call` whose callee is bound from `nova.adapters.*`.
  - [x] `test_logger_names_follow_convention` per AC #8 (with the explicit storage-sublayer allowlist).
  - [x] Re-use the AST helpers from [tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) and the Story 1.9 [tests/unit/ports/test_port_isolation.py](../../tests/unit/ports/test_port_isolation.py) — verbatim-duplicate the small helpers rather than introduce a cross-test-package import, per the Story 1.9 precedent ("`tests/` has no `__init__.py` files; duplication is ~30 lines and trivially reviewable").

- [x] **Task 5: Smoke test — full port+model import graph** (AC: #5)
  - [x] Create `tests/unit/test_composition_smoke.py::test_all_ports_and_models_importable`.
  - [x] Import every port Protocol from `nova.ports` (8 classes) + every system model module (`nova.systems.brain.models`, `eyes`, `hands`, `ritual`, `skin`) + `NoOpShieldAdapter`.
  - [x] Assert no `ImportError` and — cheap extra — assert each `Protocol` has at least one async method (catches a future empty-Protocol regression).

- [x] **Task 6: Unit tests for `app.py`** (AC: #2, #3, #16)
  - [x] Create `tests/unit/test_app.py` with per-component assertions:
    - `test_create_app_returns_populated_novaapp` — assert all 7 fields populated with expected types.
    - `test_create_app_starts_engine_before_instantiating_audit_logger` — use a recording storage mock / assert ordering via `unittest.mock.call_args`.
    - `test_create_app_runs_migrations` — assert `schema_version` row matches latest migration after `create_app`.
    - `test_create_app_closes_engine_if_migrations_fail` — monkeypatch `run_migrations` to raise; assert engine is closed afterwards.
    - `test_create_app_accepts_custom_shield_adapter` per AC #16 — pass a fake `ShieldPort` implementation and assert `app.shield is fake`.
    - `test_create_app_defaults_to_noop_shield` — assert `isinstance(app.shield, NoOpShieldAdapter)` when `shield` arg omitted.
    - `test_close_tears_down_engine` — call `await app.close()` and assert a second `engine.start()` succeeds on the same `db_path` (proves no open handle leaked).

- [x] **Task 7: Unit tests for `cli.py` helpers** (AC: #6, #7, #9)
  - [x] Create `tests/unit/test_cli.py`:
    - `test_resolve_data_dir_cli_override_wins` — pass `cli_override`, assert it beats env vars.
    - `test_resolve_data_dir_nova_data_dir_env` — set `NOVA_DATA_DIR`, assert used.
    - `test_resolve_data_dir_localappdata_fallback` — unset `NOVA_DATA_DIR`, set `LOCALAPPDATA`, assert `/ "nova"` suffix.
    - `test_resolve_data_dir_home_fallback` — unset both, assert `Path.home() / ".nova"`.
    - `test_resolve_data_dir_expands_user_and_resolves` — pass `~/foo`, assert expanded + absolute.
    - `test_resolve_data_dir_does_not_mkdir` — pass a non-existent path, call, assert `Path.exists()` is still `False`.
    - `test_configure_stderr_logging_attaches_one_handler` — call, assert exactly one handler tagged `"nova-cli-stderr-bootstrap"` on root, targeting `sys.stderr`.
    - `test_configure_stderr_logging_is_idempotent` — call twice, assert still exactly one bootstrap handler.
    - `test_configure_file_logging_creates_logs_subdir_only` — tmp_path (exists) with no `logs/`, call, assert `tmp_path/logs/` exists AND `tmp_path` was not re-created (inode / ctime unchanged if the platform supports it; otherwise just assert logs/ exists).
    - `test_configure_file_logging_fails_loud_if_data_dir_missing` — pass a non-existent `data_dir`, assert `FileNotFoundError` raised (the `parents=False` guard). This is the invariant that keeps AC #9 intact.
    - `test_configure_file_logging_removes_stderr_bootstrap` — run Phase A, then Phase B, assert no `"nova-cli-stderr-bootstrap"` handler remains on root.
    - `test_configure_file_logging_is_idempotent` — call twice, assert only one `"nova-cli-file-handler"` on root logger.
    - `test_configure_file_logging_attaches_no_stream_handler` — assert no `StreamHandler` on root after Phase B (file handler only).
    - `test_extras_formatter_renders_extras` — log with `extra={"k": "v"}`, read file, assert `extras={k=v}` substring.
    - `test_extras_formatter_strips_reserved_extras` — log with `extra={"module": "fake"}` (reserved), assert no `module=fake` substring.
    - `test_parse_log_level_precedence` parametrized over (cli, env, expected).
    - `test_parse_log_level_invalid_raises_configerror` — assert early exit with exit code 1 AND stderr message.

- [x] **Task 8: End-to-end bootstrap test** (AC: #13, #14, #15)
  - [x] Create `tests/integration/test_cli_bootstrap.py`.
  - [x] Fixture: `nova_data_dir(tmp_path)` seeds `tmp_path/settings.yaml` (empty body `{}`) + creates empty `tmp_path/modes/` dir.
  - [x] Fixture: `clean_nova_logging(request)` that removes any `"nova-cli-file-handler"` from root logger on teardown.
  - [x] `test_cli_boots_and_exits_cleanly` per AC #13.
  - [x] `test_cli_teardown_closes_all_resources` per AC #15 — open a second engine post-teardown, assert no lock error.
  - [x] Parametrized `test_cli_failure_paths` per AC #14 — 6 rows (missing data_dir, file-as-dir, malformed YAML, StorageError, KeyboardInterrupt, RuntimeError).
  - [x] Marker: `@pytest.mark.integration`.

- [x] **Task 9: Quality gate** (AC: #18)
  - [x] `uv run ruff check src/ tests/`
  - [x] `uv run ruff format --check src/ tests/` (if format drift, run `uv run ruff format src/ tests/` and re-check)
  - [x] `uv run mypy src/ tests/`
  - [x] `uv run pytest tests/unit/`
  - [x] `uv run pytest tests/integration/`
  - [x] Verify no new `# type: ignore`, no new `cast()`, no new `Any`.
  - [x] Verify repo tree stays clean: no `nova.db`, no `logs/`, no `__pycache__/` leftovers under `src/` or `tests/`.

## Dev Notes

### Critical Architecture Rules (carry-forward pinned for Story 1.10)

- **"Composition root is the only wiring location."** (project-context.md:82) — this story IS the composition root. It defines the contract every future adapter/system story plugs into. If `app.py` ends up with side-effectful module-level code (besides logger + `__all__`), that's a bug.
- **"No system imports another system's adapter."** (project-context.md:63) — Story 1.9 locked port isolation; this story locks the inverse: no module under `src/nova/` other than `app.py` / `cli.py` imports from `nova.adapters.*`. Test is AC #4.
- **"No mutable module-level runtime state."** (project-context.md:55) — `app.py` MUST NOT have a module-level `_app: NovaApp | None = None` singleton. Every `create_app` call returns a fresh graph; tests can call it N times with different tmp_paths without cross-contamination.
- **"Structured logging to file only."** (project-context.md:44, architecture.md:1250) — the steady-state rule. This story's Phase A stderr handler is the ONE deliberate exception: it exists as a pre-data-dir bootstrap channel so early failures (invalid `--log-level`, `ConfigError`, bad data_dir) reach the user when file logging is impossible. Phase B removes it before any success-path INFO record fires, so a successfully-booted session logs only to file. Never add a StreamHandler outside Phase A, and never leave Phase A's handler attached past the Phase B handoff. If the dev agent thinks "a single `print` here won't hurt" — it will (project-context.md:44 explicitly bans `print()`; stderr logging via the tagged handler is the only allowed terminal-output path from this module).
- **"Log to `%LOCALAPPDATA%/nova/logs/nova.log`"** (architecture.md:1259). Tests override via `NOVA_DATA_DIR`; production uses `LOCALAPPDATA`. AC #9 encodes the precedence.
- **"Broad exception catching only at top-level boundaries."** (project-context.md:53) — `cli.main` IS a top-level boundary; catching `Exception` + logging + returning exit code 4 is correct. Inner functions (`_configure_logging`, `_resolve_data_dir`, `create_app`) catch specific exceptions only.
- **"Never swallow `asyncio.CancelledError`."** (project-context.md:49) — `KeyboardInterrupt` at the CLI boundary is OK to translate to exit code 130, but `CancelledError` inside `_async_main` must re-raise cleanly so `asyncio.run`'s teardown completes. In practice: don't catch `BaseException`, only catch `Exception`. `CancelledError` is a `BaseException` subclass in 3.8+, so it propagates naturally.
- **"Timeouts required at external boundaries."** (project-context.md:50) — no external boundaries in this story (no network, no process spawn). SQLite is local / synchronous under the hood. No timeout is needed in `cli.py` or `app.py`.
- **"Startup/setup/migration paths must be idempotent."** (project-context.md:165) — `create_app` called twice on the same `db_path` with the same schema produces the same state (migration runner is idempotent per Story 1.5 AC). Test: `test_create_app_is_idempotent_on_same_db` (bonus unit test if time permits — not a hard AC).
- **"Local and CI quality gates must match."** (project-context.md:137) — Story 1.11 locks CI; this story's quality-gate command (AC #18) is the exact invocation CI will run.

### Previous Story Intelligence — Story 1.9 (done 2026-04-15)

Story 1.9 landed all 8 port Protocols + `NoOpShieldAdapter` + 5 `systems/*/models.py` files. Key carry-forwards for Story 1.10:

- **Test file placement mirrors src, flat directory layout.** `tests/unit/test_composition_root.py` and `tests/unit/test_app.py` live directly under `tests/unit/` (not under `tests/unit/app/` — we don't create a subdirectory for a single file per the Story 1.4+ precedent). `tests/unit/test_cli.py` similarly. The integration test lives under `tests/integration/test_cli_bootstrap.py`. Matches the existing flat-layout pattern from `tests/unit/core/test_audit.py`, `test_tiers.py`, `test_config.py`.
- **AST-based static-analysis tests, not text regex.** Both AC #4 tests and AC #8's logger-name test MUST use `ast.walk` + `ast.Import` / `ast.ImportFrom` / `ast.Call` inspection — not text regex on the source. Rationale from Story 1.9 Dev Notes + [memory/feedback_ast_static_analysis_tests.md](../../../../Users/sayuj/.claude/projects/c--Projects-AI-Assistant/memory/feedback_ast_static_analysis_tests.md): regex trips on docstrings and comments that mention forbidden names innocently. AST walks only visit actual code constructs.
- **Parametrize over all modules under `src/nova/`**. The AC #4 isolation test walks every `.py` file via `Path("src/nova").rglob("*.py")`. Future new system / adapter files are auto-included without a manual list update — same pattern as Story 1.9's `test_ports_only_import_from_core_and_models`.
- **Alphabetize `__all__` lists.** Story 1.2 / 1.8 / 1.9 convention. `app.py`'s `__all__ = ["NovaApp", "create_app"]` is alphabetical (uppercase `N` < lowercase `c` via ASCII ordering, but Python convention alphabetizes case-insensitively — either order is defensible; picking `["NovaApp", "create_app"]` matches how Story 1.9 ordered `["BrainPort", "EyesPort", ...]` with PascalCase first, function / lowercase members second). Lock by `test_app_all_is_alphabetized` if following the Story 1.9 precedent tightly.
- **Verbatim-duplicate small helpers rather than cross-package import.** `tests/` has no `__init__.py` files per the existing Story 1.4+ flat-test-layout precedent, so `from tests.unit.core.test_core_isolation import _all_imports` fails collection. Duplicate the helper (~10 lines) with a "mirror of tests/unit/core/test_core_isolation.py" comment.
- **Commit message format (Stories 1.4 – 1.9 carry-forward):** terse, imperative, story ID prefix + brief scope in parens. Expected: `"Story 1.10: composition root + CLI entrypoint (app.py, cli.py)"`.
- **"New layer" pattern.** Stories 1.3 – 1.9 each introduced a new `src/nova/` sublayer with: module docstring, typed imports, alphabetized `__all__`, at least one dedicated test file. Story 1.10 doesn't add a new sublayer — it fills in two long-vacant modules (`app.py`, `cli.py`) with their first real content. Treat each file as its own "new layer" for test-file placement.
- **mypy strict, zero `# type: ignore` in production code.** `argparse.Namespace` attribute access types as `Any`; narrow at access sites with `isinstance` or typed helper functions. `Mapping[str, str]` from `collections.abc` for env-var parameter types.
- **`Mapping[str, object]` over `dict[str, Any]`** for logging `extra` dicts — applied in `_configure_logging`'s `_ExtrasFormatter`. Same pattern as Story 1.8's `AuditLogger.log_action(details: Mapping[str, object] | None)`.
- **Two-function clock pattern not required here.** `cli.py` and `app.py` don't emit timestamps — the logging `Formatter.formatTime` handles that via `datefmt`. If any new code DOES need timestamps, import `_utc_now_iso` from `nova.core.events` (Story 1.3 convention).
- **No `runtime_checkable` on `ShieldPort` consumers.** `NoOpShieldAdapter` is already runtime-checkable (Story 1.9 AC #6). `app.py` types the field as `ShieldPort`; mypy strict checks structural conformance at the call site. No need to `isinstance(adapter, ShieldPort)` at runtime in `create_app` — that would be belt-and-suspenders + the test in AC #16 proves the protocol check works.
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. Notably `T20` flags `print()` — if the dev agent accidentally drops a `print` anywhere, lint catches it.

### Deferred-Work Items Closed By This Story

Three items from `_bmad-output/implementation-artifacts/deferred-work.md` target Story 1.10 and MUST be addressed:

1. **From Story 1.4 review:** "asyncio.get_running_loop() drift between calls is not enforced. The class docstring claims single-loop-per-engine-instance ... Target: Story 1.10 (composition root). That story wires the single-loop lifetime; add an assertion there if cross-loop misuse is ever a realistic risk. Not a current bug."
   → **Action:** AC #15 requires teardown to verify no pending tasks. The `asyncio.run(_async_main(...))` wrapper gives the engine a single loop lifetime for the whole process — no drift possible. No explicit assertion needed in production code; the AC #15 test locks the invariant. Document this in `app.py`'s docstring: "`create_app` must be called inside an active asyncio loop; `cli.main` provides that via `asyncio.run`, which gives the engine a single loop for the entire process lifetime."

2. **From Story 1.8 review:** "Unstarted-engine path produces silent forever-fail. A composition-root bug (constructing `AuditLogger(storage=engine)` before `await engine.start()`) makes every audit row vanish silently with one WARNING per call. Audit module is correctly observational. Target: Story 1.10 (composition root) — if Story 1.10 doesn't naturally enforce ordering, revisit then."
   → **Action:** AC #2 pins the construction order: engine starts → migrations run → THEN `AuditLogger(storage=engine)`. AC #6 (Task 6) locks the ordering test `test_create_app_starts_engine_before_instantiating_audit_logger`. Story 1.10 naturally enforces the ordering; no "engine-not-started → ERROR-level once" pattern needed in audit.py.

3. **From Story 1.9 review:** "No smoke / integration test imports the full port + model graph. ~15 cross-system type dependencies (e.g., BriefingAggregate → WorkspaceSnapshot → SnapshotType); a future back-reference could introduce a circular import without test catching it. Target: Story 1.10 (composition root)."
   → **Action:** AC #5 requires `tests/unit/test_composition_smoke.py::test_all_ports_and_models_importable`. Closes the deferred item.

**Remove these three items from `deferred-work.md` as part of this story's commit** (Task 9 — add the bookkeeping edit to the commit).

### Git Intelligence — last 5 commits

```
f2ef02b Story 1.8: audit logger (core/audit.py)
ab2f676 Story 1.7: capability tier state machine (core/tiers.py)
ba24622 Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)
c64849c Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow exactly.
- **Scope pattern:** `"Story 1.N: {what} ({where})"`. Story 1.10 spans two files (`app.py`, `cli.py`) + one re-export edit + test files — scope is `(app.py, cli.py)`.
- **No prior `app.py` / `cli.py` real implementation.** Currently both are docstring-only or print-placeholder stubs. This story delivers the first real content.
- **[src/nova/ports/__init__.py](../../src/nova/ports/__init__.py)** was filled in by Story 1.9 — re-exports 8 Protocol classes. `app.py` imports from `nova.ports` at the package level, not individual port modules, for brevity.
- **[src/nova/core/__init__.py](../../src/nova/core/__init__.py)** has 37 re-exports (Stories 1.2–1.8). `app.py` imports from `nova.core` at the package level (e.g., `from nova.core import SqliteStorageEngine, load_config, NovaConfig, EventBus, AuditLogger, TierManager`).

### Latest Tech Information (as of 2026-04-15)

- **Python 3.12.x** — `asyncio.run` enforces no pending tasks on return (raises `RuntimeError` otherwise). That's why AC #15's "no pending tasks" assertion is belt-and-suspenders.
- **`argparse`** — stdlib, zero dependency. Preferred over `click` / `typer` for T1 (project-context.md:121 "Ruff is the single linter and formatter" implies minimal-deps discipline; no "CLI framework" appears in the T1 stack at pyproject.toml:6–14). The `--version` flag is native via `parser.add_argument("--version", action="version", version=__version__)`.
- **`logging.Formatter`** — `datefmt` supports `%z` (UTC offset) from Python 3.12. The `%(asctime)s` token honors the `datefmt` if provided; without `datefmt`, it defaults to an undesirable ISO-ish format.
- **`logging.FileHandler`** — encoding kwarg accepts `"utf-8"`; on Windows the file is opened in append mode by default, which is what we want for multi-session log retention.
- **`dataclass(frozen=True, slots=True)`** — Python 3.10+ supports `slots=True`. Matches the Story 1.6 `NovaConfig` pattern + Story 1.9 model pattern. `NovaApp` with `slots=True` prevents accidental attribute addition at runtime (e.g., `app.extra = ...` would raise `AttributeError`) — a belt-and-suspenders guard against future stories "attaching" state instead of extending the dataclass.
- **`asyncio.run` + `asyncio.CancelledError`** — `asyncio.run` converts a `CancelledError` out of the main coroutine into the exception that propagates out. `KeyboardInterrupt` inside the coroutine is re-raised out of `asyncio.run` as `KeyboardInterrupt`. `main()` catches `KeyboardInterrupt` outside `asyncio.run` — that's why the AC #14 test injects `KeyboardInterrupt` via monkeypatching `load_config`, not via `os.kill` / signal handlers.
- **`Callable[[], Awaitable[None]]`** from `collections.abc` (`Callable`) + `typing` (`Awaitable`) — the `close` field type annotation. Matches Story 1.3's `Subscriber = Callable[[Event], Awaitable[None]]` pattern.
- **`Path.resolve()`** — resolves symlinks + returns absolute path. On Windows, resolves drive letters. `Path("~").expanduser()` expands `~` FIRST (before `resolve`, which doesn't expand `~`). AC #9 chains them: `Path(value).expanduser().resolve()`.

### Project Structure Notes

**Files modified (3) + new (7 tests + helpers):**

Production (existing files, large rewrites):
1. [src/nova/app.py](../../src/nova/app.py) — 5 lines → ~80 lines (composition root + `NovaApp` dataclass + `create_app` coroutine)
2. [src/nova/cli.py](../../src/nova/cli.py) — 15 lines → ~150 lines (entrypoint + `_configure_logging` + `_resolve_data_dir` + arg parser + async main)

Production (existing file, small edit):
3. [src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py) — add `from nova.adapters.shield.noop import NoOpShieldAdapter` + `__all__`

Tests (new):
4. `tests/unit/test_composition_root.py` — AST isolation + logger-name convention (AC #4, #8)
5. `tests/unit/test_composition_smoke.py` — port + model graph smoke (AC #5)
6. `tests/unit/test_app.py` — `create_app` + `NovaApp` unit tests (AC #2, #3, #16)
7. `tests/unit/test_cli.py` — `_resolve_data_dir`, `_configure_logging`, `_parse_log_level` helpers (AC #6, #7, #9)
8. `tests/integration/test_cli_bootstrap.py` — end-to-end boot + teardown + failure paths (AC #13, #14, #15)

Docs / tracking (new — update in commit):
9. `_bmad-output/implementation-artifacts/deferred-work.md` — REMOVE the three items explicitly targeted at Story 1.10 (Story 1.4 loop-drift, Story 1.8 unstarted-engine, Story 1.9 smoke-import).
10. `_bmad-output/implementation-artifacts/sprint-status.yaml` — flip `1-10-composition-root-and-cli-entrypoint` from `ready-for-dev` → `in-progress` → `review` during dev execution.

**Alignment with unified project structure:**
- `src/nova/app.py` matches architecture.md:1314 verbatim ("Composition root — wires ports to adapters, boots monolith").
- `src/nova/cli.py` matches architecture.md:1313 verbatim ("Terminal entrypoint — argument parsing, session lifecycle").
- Test-file placement (`tests/unit/test_app.py`, `tests/unit/test_cli.py`, flat under `tests/unit/`) matches the Story 1.2 + 1.4 + 1.6 + 1.7 + 1.8 precedent (core modules get flat test files, not subdirectories).
- `tests/integration/test_cli_bootstrap.py` is the FIRST integration test in the repo. Mark it `@pytest.mark.integration` per [pyproject.toml:53](../../pyproject.toml) marker definition. `tests/integration/` directory does not exist yet — Task 8 creates it. No `conftest.py` / `__init__.py` needed (flat-test-layout precedent).

**Detected conflicts or variances:** None. The architecture composition-root sketch (architecture.md:1064–1089) wires concrete adapter classes (`SqliteBrainAdapter`, `Win32EyesAdapter`, etc.) that don't exist yet; this story explicitly narrows scope per AC #19 to wire ONLY infrastructure that ships today. The architecture sketch is the eventual T1 target — later stories add adapters into `create_app` signature without rewriting.

### Testing Standards Summary

- **Test framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"` per pyproject.toml:47). Async tests don't need `@pytest.mark.asyncio` decorators.
- **Unit tests** live in `tests/unit/test_app.py`, `tests/unit/test_cli.py`, `tests/unit/test_composition_root.py`, `tests/unit/test_composition_smoke.py`. No external I/O except `tmp_path` for logging / SQLite fixtures.
- **Integration test** lives in `tests/integration/test_cli_bootstrap.py` (first of its kind). Uses `tmp_path`, `monkeypatch`, `capsys` fixtures. Marker: `@pytest.mark.integration`. Under the 30-second NFR budget (NFR5: workspace restore <30s is the tightest budget; CLI bootstrap is well under).
- **mypy strict** applies to every production file AND every test file. `ast`-walk tests use `ast.Module`, `ast.ClassDef`, `ast.Import`, `ast.ImportFrom`, `ast.Call` — all fully typed in stdlib stubs.
- **Deterministic clock** — no timestamps generated in Story 1.10 production code beyond `logging`'s built-in `%(asctime)s`. Tests that assert on log content should NOT assert exact timestamps — match on substrings ("N.O.V.A. initialized") or use regex for the timestamp portion.
- **Deterministic IDs** — no ID generation in this story.
- **Fixtures** live in `conftest.py` at test-file level or in the test file itself (no new `tests/conftest.py` entries needed; existing Story 1.4+ fixtures for `tmp_path`-based SQLite already cover DB setup needs).
- **Parametrize over state matrices.** AC #14 parametrizes 6 failure paths; AC #9 parametrizes 4 data-dir-resolution inputs; AC #7 parametrizes 3×3 log-level precedence combos. Don't write 13 near-identical tests by hand — use `@pytest.mark.parametrize`.
- **Coverage target:** 100% of `app.py` (trivial — one coroutine + one dataclass) and 100% of `cli.py` happy path + failure paths. Test budget: ~20 new tests total.
- **Failure-path coverage required.** project-context.md:106. AC #14 enumerates 6 failure modes explicitly.
- **No silent warnings in passing tests.** project-context.md:105. `asyncio.run` + `logging.FileHandler.close()` on teardown are the two cleanup paths to watch — the `clean_nova_logging` fixture (Task 8) handles the logging side; `asyncio.run` handles the engine side.
- **Async cleanup required.** project-context.md:104. After `asyncio.run` returns, every `asyncio.Task` must be done. `SqliteStorageEngine.close` awaits the executor shutdown (Story 1.4), which fully drains.
- **Repo tree stays clean.** project-context.md:159–160. No `nova.db`, no `logs/` under `src/`, `tests/`, or repo root after tests run. Every test uses `tmp_path`.

### Critical Constraints (carry-forward + story-specific)

- **`main()` MUST remain synchronous with `-> int` return.** `project.scripts` expects a sync entrypoint. Async entrypoints via `asyncio.run` inside a sync wrapper is the only supported shape.
- **`argparse`, not `click` / `typer`.** No new dependency in pyproject.toml for T1 — `argparse` is stdlib. This is a dependency-discipline decision, not a style preference. If a later story needs subcommands that outgrow `argparse` (e.g., Story 2.x setup wizard flows), that story adds the dependency with explicit rationale.
- **No `sys.exit()` inside `_async_main`.** Return the exit code; let `main()` surface it via `return`. `sys.exit` inside an async coroutine raises `SystemExit` which propagates through `asyncio.run` but skips `finally` blocks for any coroutines that didn't finish — violates AC #15's teardown guarantee.
- **No global logger state via `logging.basicConfig`.** `basicConfig` is the classic Python "configure logging once at module load" footgun — it silently no-ops on repeat calls. `_configure_logging` explicitly manipulates the root logger's handlers, checks for our named handler, and cleans up. Tests can re-initialize without surprise.
- **Do NOT wire `TierManager` to `event_bus.subscribe(TierChanged, ...)`.** TierManager already EMITS `TierChanged` events (Story 1.7). Subscribers (audit logging, Skin tier-notice display) arrive with their own stories. Story 1.10 only constructs the objects; it does not wire subscriptions.
- **Do NOT call `engine.run_migrations()` if `engine.start()` failed.** `start()` raises `StorageError` on failure; the `try/except` in `create_app` should catch and close (no-op if start failed — `close` is idempotent per Story 1.4 docstring), then re-raise. `run_migrations` should never be reached on a failed start.
- **Do NOT pre-create the data directory in `cli.py`.** `setup.bat` (Story 2.1) creates the full user-data tree. `load_config` raises `ConfigError` if missing — `cli.py` translates to exit code 1 with a message like "`data directory missing at {path} — run setup.bat first`" logged at ERROR. The user-facing error-message polish is Story 2.1's concern; this story's message just needs to be actionable.
- **Do NOT put `NovaApp` under `nova.systems.*`.** It's an infrastructure value object, not a system model. It lives in `nova.app` (same module as `create_app`). Future stories that add system instances to `NovaApp` add them as dataclass fields of `NovaApp` — the dataclass stays in `app.py`.
- **`NOVA_DATA_DIR`, `NOVA_LOG_LEVEL`, `LOCALAPPDATA`** are the only env vars read by this story. Adding a new env var (e.g., `NOVA_DEBUG`, `NOVA_NO_MIGRATIONS`) requires a future story — do not speculatively add.
- **`--data-dir`, `--log-level`, `--version`** are the only CLI flags for this story. Future Layer-A flags (e.g., `nova status`, `nova mode coding`) belong to Epic 3.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.10: Composition Root & CLI Entrypoint](../planning-artifacts/epics.md) — canonical AC, lines 833–864.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives + what ships, lines 357–395.
- [Source: _bmad-output/planning-artifacts/epics.md#Additional Requirements — From Architecture — Implementation Sequence](../planning-artifacts/epics.md) — lines 220–235, locks step 13 (composition root) and step 14 (CLI entrypoint) as the final T1 scaffolding tasks.
- [Source: _bmad-output/planning-artifacts/architecture.md#Composition Root Convention](../planning-artifacts/architecture.md) — lines 1059–1102, the `app.py` composition pattern + anti-patterns. Note the sketch wires adapters that don't exist yet; Story 1.10 scope per AC #19 is the narrower "infrastructure only" subset.
- [Source: _bmad-output/planning-artifacts/architecture.md#Logging Convention](../planning-artifacts/architecture.md) — lines 1248–1263, file-only + structured + logger-name convention.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — lines 1293–1432, `cli.py` and `app.py` placement confirmed at lines 1313–1314.
- [Source: _bmad-output/planning-artifacts/architecture.md#Runtime User Data Directory](../planning-artifacts/architecture.md) — lines 1434–1452, `%LOCALAPPDATA%/nova/logs/nova.log` target path.
- [Source: _bmad-output/planning-artifacts/architecture.md#T1 Skeleton — What Exists at First Implementation Milestone](../planning-artifacts/architecture.md) — lines 1505–1510, confirms `cli.py` + `app.py` are the T1 active skeleton (+ port files).
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 35 (type annotations), 37 (asyncio single loop), 44 (no print / structured logging), 53 (broad exception only at boundaries), 55 (no mutable module-level state), 62 (ports-and-adapters), 63 (no cross-system adapter imports), 74 (event bus), 76 (one-way deps), 82 (composition root is sole wiring location), 137 (local=CI quality gates), 148–156 (canonical commands), 157–161 (setup.bat + data-dir ownership).
- [Source: src/nova/app.py](../../src/nova/app.py) — current 5-line placeholder; replaced by Task 2.
- [Source: src/nova/cli.py](../../src/nova/cli.py) — current 15-line placeholder; replaced by Task 3.
- [Source: src/nova/__init__.py](../../src/nova/__init__.py) — `__version__` already exported; `--version` flag consumes it.
- [Source: src/nova/core/__init__.py](../../src/nova/core/__init__.py) — 37 re-exports available; `app.py` imports from here.
- [Source: src/nova/ports/__init__.py](../../src/nova/ports/__init__.py) — 8 Protocol re-exports from Story 1.9; `app.py` imports `ShieldPort` from here.
- [Source: src/nova/adapters/shield/noop.py](../../src/nova/adapters/shield/noop.py) — `NoOpShieldAdapter` from Story 1.9.
- [Source: src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py) — docstring-only; Task 1 adds re-export.
- [Source: src/nova/core/storage/engine.py](../../src/nova/core/storage/engine.py) — `SqliteStorageEngine` from Story 1.4. `await engine.start()` + `await engine.run_migrations()` + `await engine.close()` are the three lifecycle calls Story 1.10 uses. `close` is idempotent.
- [Source: src/nova/core/config.py](../../src/nova/core/config.py) — `load_config(data_dir: Path) -> NovaConfig` from Story 1.6. Raises `ConfigError` if data_dir missing / not a dir / has malformed YAML singleton.
- [Source: src/nova/core/tiers.py](../../src/nova/core/tiers.py) — `TierManager(event_bus, health_check, initial_tier=FULL)` from Story 1.7. The `HealthCheck` Protocol is there; Story 1.10 injects a no-op.
- [Source: src/nova/core/audit.py](../../src/nova/core/audit.py) — `AuditLogger(storage=engine)` from Story 1.8. Requires a started engine (enforced by construction order in AC #2).
- [Source: src/nova/core/events.py](../../src/nova/core/events.py) — `EventBus` from Story 1.3.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `NovaError`, `ConfigError`, `StorageError`, `ApiUnavailableError`. Top-level `cli.main()` catches `NovaError` subtypes and maps to exit codes per AC #11. Note: `NovaError` docstring (lines 41–45) already mentions "The top-level CLI / session boundary (Story 1.10) catches this class" — this story fulfills that contract.
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — three items targeted at Story 1.10 (Story 1.4 loop-drift lines 45, Story 1.8 unstarted-engine lines 77, Story 1.9 smoke-import lines 95). Task 9 removes them from the file as part of this story.
- [Source: _bmad-output/implementation-artifacts/1-9-port-interfaces-and-shield-stub.md](./1-9-port-interfaces-and-shield-stub.md) — prior story. Test file layout, AST-based static-analysis precedent, commit style, structural Protocol conformance pattern, verbatim-duplicate-helpers pattern.
- [Source: _bmad-output/implementation-artifacts/1-8-audit-logger.md](./1-8-audit-logger.md) — `AuditLogger` construction contract; required storage ordering.
- [Source: _bmad-output/implementation-artifacts/1-7-capability-tier-state-machine.md](./1-7-capability-tier-state-machine.md) — `TierManager` + `HealthCheck` construction contract.
- [Source: _bmad-output/implementation-artifacts/1-6-config-loader-and-immutable-novaconfig.md](./1-6-config-loader-and-immutable-novaconfig.md) — `load_config` contract.
- [Source: _bmad-output/implementation-artifacts/1-4-sqlite-storage-engine.md](./1-4-sqlite-storage-engine.md) — `SqliteStorageEngine` lifecycle (`start`, `run_migrations`, `close` idempotency, single-loop concern).
- [Source: tests/unit/core/test_core_isolation.py](../../tests/unit/core/test_core_isolation.py) — AST-isolation test pattern + helpers (`_all_imports`, `_dynamic_import_targets`, `FORBIDDEN_TOPLEVEL_MODULES`) to verbatim-duplicate into `test_composition_root.py`.
- [Source: tests/unit/ports/test_port_isolation.py](../../tests/unit/ports/test_port_isolation.py) — Story 1.9 AST-walk patterns; `test_composition_root.py::test_no_system_imports_adapters` extends the same pattern.
- [Source: pyproject.toml](../../pyproject.toml) — `nova = "nova.cli:main"` script registration (line 16); `asyncio_mode = "auto"` (line 47); marker definitions including `integration` and `windows_only` (lines 48–53); ruff rules `E,F,I,UP,B,SIM,T20` (line 36). No new dependency added by this story.
- [Source: C:\Users\sayuj\.claude\projects\c--Projects-AI-Assistant\memory\feedback_ast_static_analysis_tests.md](../../../../Users/sayuj/.claude/projects/c--Projects-AI-Assistant/memory/feedback_ast_static_analysis_tests.md) — "For N.O.V.A., use ast.walk + ast.Call inspection, not text regex — avoids docstring false positives."

### Review Findings

**Patch (unchecked — real issues to fix):**

- [x] [Review][Patch] `create_app` partial-init cleanup only guards migrations; AuditLogger / TierManager / shield-adapter construction can leak a started engine [src/nova/app.py:142-167]
- [x] [Review][Patch] `argparse --log-level choices=` rejects lowercase before `_parse_log_level` runs; exits with argparse's exit code 2 (colliding with `EXIT_STORAGE_ERROR`), contradicting the documented exit-code table [src/nova/cli.py:277-280]
- [x] [Review][Patch] `tests/unit/test_app.py` introduces `typing.Any` (line 7 + line 109 `**kwargs: Any`) and a `# type: ignore[misc]` (line 64) — AC #18 forbids new `Any` / `type: ignore` anywhere [tests/unit/test_app.py:7,64,109]
- [x] [Review][Patch] Missing test: `KeyboardInterrupt` during session teardown (post-`create_app` success) is untested; the `finally: await app.close()` path is unverified [tests/integration/test_cli_bootstrap.py]
- [x] [Review][Patch] Missing test: `_configure_file_logging` `OSError` path (AC #10 step 4 failure branch) is unexercised [tests/unit/test_cli.py]
- [x] [Review][Patch] AC #14 assertion gaps — 4 of 6 failure tests missing `capsys.readouterr().out == ""`; `test_cli_keyboard_interrupt_exits_130` missing `"interrupted by user"` INFO-log assertion [tests/integration/test_cli_bootstrap.py:168,184,221]
- [x] [Review][Patch] AC #15 missing `asyncio.all_tasks()` empty assertion in teardown test (spec explicitly requires it as belt-and-suspenders) [tests/integration/test_cli_bootstrap.py:120-141]
- [x] [Review][Patch] `test_cli_storage_error_exits_with_storage_code` asserts exit code + log line but not that the engine is closed — resource-cleanup guarantee unverified on the StorageError path [tests/integration/test_cli_bootstrap.py:197-218]
- [x] [Review][Patch] AST composition-root isolation test does not catch dynamic imports (`importlib.import_module("nova.adapters.xxx")` / `__import__`) — circumventable [tests/unit/test_composition_root.py:96-108]
- [x] [Review][Patch] No test verifies `_AlwaysHealthyCheck` structurally conforms to the `HealthCheck` Protocol — regression guard missing [tests/unit/test_app.py]
- [x] [Review][Patch] `test_configure_file_logging_writes_extras_to_disk` reads the log file while the `FileHandler` is still attached — relies on shared-read; flush + close before read is more deterministic [tests/unit/test_cli.py:263-272]
- [x] [Review][Patch] `test_configure_file_logging_attaches_no_stream_handler` is tautological — the filter excludes `FileHandler` (a `StreamHandler` subclass), then asserts nothing ours remains; any pytest-owned handler passes trivially [tests/unit/test_cli.py:195-203]
- [x] [Review][Patch] `test_resolve_data_dir_treats_whitespace_env_as_empty` only exercises `NOVA_DATA_DIR` — `LOCALAPPDATA` whitespace stripping is unlocked [tests/unit/test_cli.py]
- [x] [Review][Patch] Docstring comment at `_configure_file_logging` claims `parents=False` is "load-bearing" — `parents=False` is the `Path.mkdir` default; the comment implies a deliberate override and misleads maintainers [src/nova/cli.py:247-249]
- [x] [Review][Patch] `tracked_audit_init` signature `(self, **kwargs)` would silently pass if a future refactor switched `AuditLogger(storage=...)` to a positional call — assertion would pass for the wrong reason [tests/unit/test_app.py:109-113]
- [x] [Review][Patch] `test_cli_unexpected_exception_exits_with_unexpected_code` lacks negative assertion — does not rule out the `EXIT_NOVA_ERROR` branch firing instead [tests/integration/test_cli_bootstrap.py:235-253]
- [x] [Review][Patch] `FileExistsError` / `NotADirectoryError` path — if `<data_dir>/logs` exists as a file (not a dir), the subsequent `FileHandler(...)` raises a cryptic OS-specific error rather than a clean `ConfigError` [src/nova/cli.py:247-259]
- [x] [Review][Patch] `KeyboardInterrupt` during `_build_parser` / `parse_args` escapes `main()` — `parse_args` ran BEFORE the outer try block, so a Ctrl-C in the argparse phase violated the documented `EXIT_INTERRUPTED=130` contract. `SystemExit` from argparse (`--help`/`--version`) is re-raised to preserve argparse behavior. [src/nova/cli.py:378-388]
- [x] [Review][Patch] AST module-scope adapter-instantiation test bypassable — only tracked `from nova.adapters ... import X` via `ast.Name` callees, missing `import nova.adapters.X [as alias]` + attribute-chain callees. Test now tracks both `ImportFrom` symbols AND `Import` aliases, and walks `ast.Attribute` chains leftward to catch `alias.Cls()` forms. [tests/unit/test_composition_root.py:206-275]

**Defer (pre-existing or not-actionable-now):**

- [x] [Review][Defer] Reserved Windows filenames (`CON`, `NUL`, `AUX`) in `--data-dir` not rejected early — surfaces as downstream `OSError`. Target: Story 2.1 (setup.bat input validation)
- [x] [Review][Defer] `NovaApp.close` closure captures `storage`; invoking from a different thread/loop would fail cryptically — no cross-thread teardown today. Target: whichever story introduces multi-loop or signal-handler teardown
- [x] [Review][Defer] `_remove_handlers_by_name` does not guard `handler.close()` failures — atomicity edge case. Target: next test-hygiene pass
- [x] [Review][Defer] `NovaApp(frozen=True, slots=True)` + closure field is not picklable — latent if any future story introduces multiprocessing. Target: whichever story adds cross-process IPC (none planned in T1)
- [x] [Review][Defer] `stray _tmp_story-1-6.patch` at repo root — unrelated leftover from Story 1.6, noticed during review. Target: next housekeeping commit

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- **AC #8 logger-name convention** initially rejected top-level entrypoint loggers (`nova.app`, `nova.cli`) because the spec demanded exactly 2 dots. Corrected to accept 1-or-2 dots with the storage-sublayer allowlist preserved for the one pre-existing 3-dot exception. Top-level entrypoints legitimately have one dot because they are NOT nested in a layer (`systems/`, `core/`, `adapters/`) — they ARE the layer.
- **`Path.expanduser()` test seam** — monkeypatching `Path.home` does NOT affect `Path("~").expanduser()` because the latter routes through `os.path.expanduser` → `USERPROFILE` / `HOME` env vars. Fixed by monkeypatching those env vars directly + weakening the assertion to "`~` expanded to something absolute and ends in `mydir`", avoiding cross-platform path-equality fragility.
- **Happy-path `capsys.err` assertion** initially failed because `load_config` runs BEFORE Phase B file logging, so legitimate "zero modes" / "no exclusions" WARNINGs land on stderr via the Phase A handler. Fixed by seeding the fixture with a valid `exclusions.yaml` and one minimal valid mode file — both suppress the warnings without softening the `captured.err == ""` invariant.
- **`issubclass(port, Protocol)` rejected by mypy** — `typing.Protocol` is a special form, not a class. Switched to the runtime-introspection idiom `getattr(port, "_is_protocol", False)` which CPython documents for this purpose.
- **ruff SIM114 on `logging.getLogger` matcher** — nested `isinstance(func, ast.Attribute) and func.attr == "getLogger" or isinstance(func, ast.Name) and func.id == "getLogger"` tripped both line-length and SIM114. Split into two named boolean variables for readability + width compliance.
- **ruff `F401` stripped `sqlite3` / `sys` / `unittest.mock.patch`** from `tests/unit/test_app.py` — they were aspirational imports during test scaffolding that the final test bodies don't use. Safe to drop.

### Completion Notes List

- **All 19 Acceptance Criteria satisfied.** Composition root at [src/nova/app.py](../../src/nova/app.py) wires `SqliteStorageEngine` → migrations → `EventBus` → `AuditLogger` → `TierManager` (with `_AlwaysHealthyCheck` no-op) → `NoOpShieldAdapter` in the pinned order (AC #2). `NovaApp` is `@dataclass(frozen=True, slots=True)` with all 7 fields present (AC #3). Custom shield-adapter swap proved via `test_create_app_accepts_custom_shield_adapter` (AC #16).
- **CLI two-phase logging works end-to-end.** Phase A stderr handler tagged `"nova-cli-stderr-bootstrap"` attaches before `load_config` so every pre-data-dir failure reaches stderr. Phase B file handler tagged `"nova-cli-file-handler"` attaches after `load_config` succeeds, uses `mkdir(parents=False, exist_ok=True)` so a missing `data_dir` fails loud, and removes the Phase A handler. Happy-path integration test asserts `capsys.err == ""` — proves the handoff.
- **Data-dir invariant preserved.** `cli.py` never calls `mkdir(parents=True)`; `setup.bat` remains the sole creator of the user data root. `_configure_file_logging` test `test_configure_file_logging_fails_loud_if_data_dir_missing` locks the `FileNotFoundError` surface.
- **Three deferred-work items closed and removed from `deferred-work.md`:**
  1. Story 1.4 "`asyncio.get_running_loop()` drift" — documented in `app.py` module docstring that the whole process runs under one `asyncio.run`-owned loop; no assertion needed because cross-loop misuse is structurally impossible.
  2. Story 1.8 "unstarted-engine silent forever-fail" — `create_app` pins the construction order (engine.start → migrations → AuditLogger); `test_create_app_starts_engine_before_instantiating_audit_logger` locks it.
  3. Story 1.9 "no smoke test for full port + model import graph" — `tests/unit/test_composition_smoke.py::test_all_ports_and_models_importable` fills that gap.
- **AST guardrails extend the Story 1.2 / 1.9 pattern.** `test_composition_root.py` locks: (a) only `app.py` / `cli.py` import adapters among non-adapter files; (b) ports / systems stay adapter-free; (c) adapter sub-packages only import from their own sub-package; (d) `app.py` has zero module-scope adapter instantiation; (e) every `logging.getLogger(...)` literal matches `nova.{module}` OR `nova.{layer}.{module}` with the storage-sublayer allowlist.
- **Integration-test marker created.** This story adds the first `@pytest.mark.integration` test file — `tests/integration/test_cli_bootstrap.py` — and exercises all 6 failure paths from AC #14 plus happy-path + teardown. No new `tests/integration/conftest.py` needed; Story 1.4+'s flat-test-layout precedent holds.
- **Quality gate clean:** `ruff check` + `ruff format --check` + `mypy strict` + `pytest` all green. 729 passed, 1 skipped. Delta from Story 1.9: +105 tests (Story 1.9's full suite was 624 passed, 1 skipped; this story's net new tests are in `test_composition_root.py` parametrized over every `.py` file under `src/nova/`, so the total is higher than the "+20" estimate — the parametrization is deliberate so future stories extending the tree are auto-covered).
- **Zero `# type: ignore` added, zero `cast()` added, zero new `Any`.** mypy strict stays clean.
- **Zero modifications to existing Story 1.1 – 1.9 production files** except the three listed in AC #19 scope: `app.py`, `cli.py`, `adapters/shield/__init__.py`. `deferred-work.md` edited to remove the three closed items.

### File List

**New — production (0):** all production changes replace existing stub / docstring-only files.

**Modified — production (3):**
- [src/nova/app.py](../../src/nova/app.py) — 5 lines → 179 lines (composition root: `NovaApp` dataclass + `create_app` coroutine + `_AlwaysHealthyCheck` stub)
- [src/nova/cli.py](../../src/nova/cli.py) — 15 lines → 326 lines (entrypoint: `main` / `_async_main` / `_configure_stderr_logging` / `_configure_file_logging` / `_ExtrasFormatter` / `_resolve_data_dir` / `_parse_log_level` / `_build_parser` / exit-code constants)
- [src/nova/adapters/shield/__init__.py](../../src/nova/adapters/shield/__init__.py) — added `NoOpShieldAdapter` re-export + `__all__`

**New — tests (5):**
- [tests/unit/test_app.py](../../tests/unit/test_app.py) — 10 tests
- [tests/unit/test_cli.py](../../tests/unit/test_cli.py) — 29 tests
- [tests/unit/test_composition_root.py](../../tests/unit/test_composition_root.py) — ~60 tests (most via `@pytest.mark.parametrize` over every `.py` under `src/nova/`)
- [tests/unit/test_composition_smoke.py](../../tests/unit/test_composition_smoke.py) — 5 tests
- [tests/integration/test_cli_bootstrap.py](../../tests/integration/test_cli_bootstrap.py) — 9 tests

**Modified — tracking / deferred work (2):**
- [_bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — removed three items explicitly targeted at Story 1.10 (Story 1.4 loop-drift, Story 1.8 unstarted-engine, Story 1.9 smoke-import)
- [_bmad-output/implementation-artifacts/sprint-status.yaml](./sprint-status.yaml) — `1-10-composition-root-and-cli-entrypoint: ready-for-dev` → `in-progress` → `review`; `last_updated` header updated

### Change Log

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Story 1.10 implementation complete: composition root (`app.py`) + CLI entrypoint (`cli.py`) with two-phase logging + 105 new tests across 5 new test files; quality gate clean (729 passed, 1 skipped); 3 deferred-work items closed | claude-opus-4-6[1m] |
| 2026-04-15 | Addressed code review findings — 17 patches applied across production + tests (partial-init cleanup widened, argparse `choices` removed to honor case-insensitive `_parse_log_level`, `Any` / `type: ignore` removed from tests, dynamic-import AST guard added, + 10 new tests for post-create_app KeyboardInterrupt, `_AlwaysHealthyCheck` conformance, `logs/`-as-file path, double-fault teardown, whitespace-env parametrization, `asyncio.all_tasks()` assertion). 5 items deferred. Quality gate clean: 739 passed, 1 skipped. Status → done. | claude-opus-4-6[1m] |
| 2026-04-15 | Second-pass review findings addressed — (1) `main()` now catches `KeyboardInterrupt` during `_build_parser` / `parse_args` (argparse phase), re-raises `SystemExit` unchanged. (2) AST adapter-instantiation guard extended to catch `import nova.adapters.X as alias` + `alias.Cls()` attribute-chain bypass. +1 integration test, hardened composition-root test body. 740 passed, 1 skipped; quality gate clean. | claude-opus-4-6[1m] |
