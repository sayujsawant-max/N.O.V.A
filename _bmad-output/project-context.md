---
project_name: 'AI Assistant'
user_name: 'Sayuj'
date: '2026-04-14'
sections_completed:
  ['technology_stack', 'language_rules', 'architecture_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'critical_rules']
status: 'complete'
rule_count: 151
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Language:** Python 3.12.x, fully typed, asyncio-based, single-process runtime
- **Package manager:** uv, with pyproject.toml as source config and uv.lock for reproducible installs. Compatible ranges in pyproject.toml, exact versions pinned in uv.lock.
- **Terminal UI:** Rich only in v0.1 — rendering panels, tables, prompts, progress, and structured terminal output. Rich is a rendering layer only, not a state manager. No Textual in v0.1.
- **Database:** sqlite3 (stdlib), single local DB file at `%LOCALAPPDATA%/nova/nova.db`. Single-user, single-process session ownership — no concurrent writers.
- **OS integration:** pywin32 / win32gui, psutil — Windows 11 only. Graceful degradation if pywin32 context capture fails (Eyes produces empty/partial snapshots, session continues).
- **AI reasoning:** Anthropic Python SDK, pinned via lockfile, prompt caching enabled where supported
- **Linting/formatting:** Ruff
- **Type checking:** mypy in strict mode on application code, with limited exclusions only for third-party integration boundaries
- **Testing:** pytest, pytest-asyncio, pytest-cov
- **Platform scope:** Windows 11 only, session-based, no background daemon in v0.1

## Critical Implementation Rules

### Python Language Rules

- **Type annotations on everything.** All function signatures, return types, class attributes. Use `X | None` union syntax (3.12+), not `Optional[X]`. Use `list[str]` not `List[str]`.
- **Type aliases use PEP 695 `type` keyword, not `typing.TypeAlias`.** `type SqlParams = Sequence[str | int | float | bytes | None]`, never `SqlParams: TypeAlias = ...`. ruff `UP040` enforces this on the py312 target. Convention established in Story 1.4 (first type alias in the codebase). Applies to both public and module-private (leading-underscore) aliases.
- **asyncio is the concurrency model.** Single event loop, async/await throughout. No threads, no multiprocessing, no `concurrent.futures` unless explicitly required for a blocking Win32 call (and then wrapped in `asyncio.to_thread()`).
- **Dataclasses for all domain types.** Use `@dataclass(frozen=True)` for immutable value objects (events, view models, config). Mutable dataclasses only where mutation is the explicit design.
- **No raw string event types.** All events are typed classes in `core/events.py`. Never dispatch or listen for a string event name.
- **Domain exceptions only.** Define in `core/exceptions.py`. Never let adapter-specific exceptions (sqlite3.Error, anthropic.APIError, pywintypes.error) cross a port boundary. Catch at the adapter, re-raise as a domain exception.
- **No raw SQL outside migrations.** Systems use the storage engine API. Schema changes go through the migration runner only.
- **Enum for constrained values.** Use `enum.Enum` or `enum.StrEnum` for: `BriefingState`, `CapabilityTier`, `SnapshotType`, `ActionType`, `MemoryCategory`, `BluntnessLevel`. Never use raw strings for these in system code.
- **Imports: absolute only.** `from nova.systems.brain.models import SessionSummary` — never relative imports between systems.
- **No `print()` anywhere.** Terminal output goes through Skin. Debugging goes through `logging`. These channels are strictly separated.
- **Timezone-aware datetimes only.** Use UTC-aware `datetime` in system and storage code. Localize to user timezone only at the rendering layer (Skin). Never mix naive and aware datetimes.
- **Timestamp helpers use the two-function clock pattern.** Story 1.3 pinned `_utc_now_iso()` (canonical, `datetime.now(UTC).isoformat()`) + `_default_timestamp()` (factory indirection whose body is `return _utc_now_iso()`). Tests monkeypatch `_utc_now_iso` for determinism; the indirection keeps the patch effective across every new instance. **Any future timestamp-emitting module — migration runner `schema_version.applied_at` (Story 1.5), AuditLogger `audit_log.timestamp` (Story 1.8), Brain `sessions.started_at` / `memory_items.created_at` (Story 3.1+) — MUST reuse this pattern** (either by importing `_utc_now_iso` from `nova.core.events` or by declaring a matching two-function pair in the owning module). Never inline `datetime.now(UTC).isoformat()` at the call site — that forecloses deterministic testing.
- **No `Any` in application code.** Restrict `Any` to third-party integration boundaries and narrow immediately. Prefer precise types, `TypedDict`, `Protocol`, dataclasses, or `Literal`/`Enum`.
- **Typed boundary parsing required.** External payloads (Anthropic API responses, Win32 adapter outputs, JSON blobs from SQLite) must be parsed into typed DTOs/domain models before entering system logic. Never pass raw `dict[str, object]` through the system.
- **Never swallow `asyncio.CancelledError`.** Cleanup is allowed, but cancellation must always be re-raised.
- **Timeouts required at external boundaries.** API calls, blocking adapter work, and file/database operations must have explicit timeout policy.
- **Use `pathlib.Path` for filesystem code.** Do not use `os.path` in application code.
- **No mutable default values.** Use `field(default_factory=...)` for dataclass collections and mutable members. Never use `[]`, `{}`, or mutable objects as parameter defaults.
- **Broad exception catching only at top-level boundaries.** Inner layers catch specific exceptions only. Global/session boundary may catch `Exception` to log, classify, and fail gracefully.
- **Ports use Protocols/ABCs.** System logic depends on interfaces (`Protocol` classes in `ports/`), never concrete adapter classes.
- **No mutable module-level runtime state.** Session state, config, and clients are injected via the composition root, not imported as mutable module-level singletons.
- **Stable serialization only.** Enums serialize as stable string values. Datetimes serialize as ISO 8601 UTC. No pickle-based persistence.
- **Formatting/parsing must be centralized.** Duration formatting, datetime formatting, mode-name normalization, and enum parsing each live in one shared utility — not reimplemented ad hoc across systems.

### Architecture Rules

- **Modular monolith, single asyncio process.** 8 named systems (Brain, Eyes, Hands, Shield, Ritual, Voice, Skin, Nerve) in one Python process. No microservices, no separate processes, no background daemon.
- **Ports-and-adapters is load-bearing.** Every system defines an abstract port (`ports/*.py`) and at least one concrete adapter (`adapters/`). Systems import ports, never adapters. All wiring happens in `app.py` (composition root).
- **No system imports another system's adapter.** Inter-system communication goes through the event bus via Nerve. Direct cross-system imports are forbidden.
- **Voice generates text; Skin renders it.** Voice owns personality and prose. Skin owns Rich components and terminal I/O. Voice never renders. Skin never generates prose. This separation is load-bearing for v0.2 voice adapter migration.
- **Nerve is an orchestrator, not a router.** Nerve makes policy decisions (skip briefing, degrade tier, suppress actions). Nerve never generates user-facing prose — that's Voice's job.
- **Operational output bypasses Voice.** Progress lines (✓/✗), tier notices, confirmation prompts, status tables, transparency trees go direct to Skin. Only personality-bearing responses route through Voice.
- **Brain owns all SQLite tables.** Other systems read/write through Brain's port interface. No system queries SQLite directly.
- **Ritual owns ceremony logic; Nerve decides when ceremonies run.** Ritual assembles briefings and shutdown flows. Nerve decides policy (e.g., skip briefing if session < 1h ago).
- **Config module is the single YAML reader.** No system reads YAML/JSON config directly. All config access goes through `core/config.py` → immutable `NovaConfig` dataclass.
- **PromptBuilder is a trust boundary, not part of Brain.** `core/prompt_builder.py` sits between Brain and the Claude adapter. It minimizes, strips excluded context, enforces token budget. The Claude adapter only receives PromptBuilder output.
- **Three capability tiers are real product behavior.** Full / degraded / offline-local-only. Every system exposes a capability map branching on current tier. Check tier state before cloud-dependent operations — never assume full connectivity.
- **Exclusion filtering happens at capture, not rendering.** Eyes filters at the capture layer. Excluded apps produce opaque events that propagate through all systems as placeholders. No excluded content enters storage, cloud prompts, audit trail, or transparency display.
- **Audit trail is append-only and cross-cutting.** Use `AuditLogger` for all automated action logging. Never write to audit_log directly. Deletion events log the action, never the deleted content.
- **Event bus for inter-system communication.** All events are typed, frozen dataclasses in `core/events.py`. Systems emit and subscribe through the bus. No direct method calls between systems.
- **Schema migrations are numbered and backup-enforced.** `001_initial_schema.py`, `002_...`, etc. Migration runner auto-backs up nova.db before applying. No raw DDL outside migration files.
- **Dependency direction is one-way.** `core` is the lowest layer — import-safe everywhere. Systems may depend on `core` and their own ports, but never upward on `app.py`, wiring code, or another system's internals. System boundaries are package boundaries — import path tells you whether a dependency is legal.
- **Adapters may translate, never decide.** Adapters normalize I/O, marshal data, and translate external errors to domain exceptions. No business policy, orchestration, ceremony logic, or user-intent inference in adapters.
- **Persist before emit.** Events representing durable facts (`session_ended`, `memory_forgotten`, `seed_saved`) are emitted only after Brain confirms the write succeeded. Never emit before persistence is confirmed.
- **Event bus is in-process only for T1.** Single-process, in-memory, async. No durable queue, no replay, no cross-process delivery, no persistence of events. Ordered delivery within current process.
- **Each domain fact has one owner.** Brain owns persisted memory/session facts, Skin owns terminal render state, Nerve owns runtime orchestration state. No duplicated writable copies across systems.
- **Idempotency for cross-cutting actions.** Shutdown, forget, migration, and restore flows must be safe against retries or double-submission. Re-running the same command must not duplicate audit entries or corrupt state.
- **Composition root is the only wiring location.** Dependency construction, adapter selection, tier bootstrapping, and environment-specific branching happen only in `app.py`. No lazy self-wiring inside systems.
- **One public entrypoint per system.** Each system exposes a narrow facade/service surface. Internal modules are not called across boundaries even if technically importable.
- **Tier evaluation is centralized.** Capability tier is determined by Nerve/TierManager, then injected or read consistently. Systems do not independently guess whether they are full/degraded/offline.
- **Fallback behavior preserves structure.** Degraded/offline mode reduces content richness (no prose enrichment, raw seed verbatim), but never changes command grammar, state machine shape, or event model.
- **Audit logging is observational, not transactional.** Audit write failure must not block the primary action. Action success does not depend on audit write success in T1.
- **No hidden persistence outside Brain.** File caches, temp state, prompt artifacts, and migration metadata are either owned by Brain or explicitly declared as non-authoritative ephemeral state.
- **PromptBuilder output is immutable.** Once PromptBuilder builds a cloud-safe prompt payload, adapters may transport it but must not append hidden context or reshape trust boundaries.
- **Rendering is a sink, not a source of truth.** Skin may format and truncate for display, but rendered output is never read back into system logic.

### Testing Rules

- **Test structure mirrors src.** `tests/unit/systems/`, `tests/unit/core/`, `tests/unit/adapters/`, `tests/integration/`. Test file naming: `test_{module}.py`.
- **Unit tests use mock adapters, not real infrastructure.** Brain unit tests use in-memory SQLite or mock, not the real DB path. Win32 and Claude tests use mock adapters. Unit tests must not require network, filesystem side effects, or Windows APIs.
- **Integration tests use real SQLite, mock external services.** Real in-memory SQLite with real migration runner. Mock Win32 and Claude adapters. Integration tests validate cross-system behavior through the event bus.
- **Test the boundaries independently.** Brain: given DB rows → correct aggregate. Nerve: given aggregate → correct state. Ritual: given aggregate + state + tier → correct view model. Skin: given view model → correct Rich output. Each boundary testable in isolation.
- **View-model tests are mandatory before render tests.** Ritual output must be validated structurally before Skin rendering tests. This prevents UI regressions from being misdiagnosed as logic bugs.
- **pytest-asyncio for all async tests.** Use `@pytest.mark.asyncio` and async fixtures. Never use `asyncio.run()` inside test bodies.
- **No test should depend on execution order.** Each test sets up its own state. Use fixtures for shared setup, never module-level mutable state.
- **Fixtures live in conftest.py.** Shared fixtures (test DB, mock adapters, event bus, sample config) in `tests/conftest.py`. System-specific fixtures in their respective test directories.
- **Assert behavior, not implementation.** Test what a system does (outputs, side effects, state changes), not how it does it internally.
- **Deterministic clock required.** Time-dependent logic (recency checks, durations, cooldowns, "last session" comparisons) must use an injectable clock/time provider. Tests must never depend on wall-clock time.
- **Deterministic IDs required.** Session IDs, correlation IDs, temp paths, and random/default-generated values must be injectable or mocked so assertions stay stable.
- **Async cleanup required.** Tests must leave no pending tasks, open connections, unclosed resources, or lingering event-bus subscribers after completion.
- **No silent warnings in passing tests.** A passing suite must not emit un-awaited coroutine warnings, resource warnings, or background task leakage.
- **Failure-path coverage required.** Every external boundary must have explicit tests for timeouts, adapter exceptions, corrupted/malformed persisted data, and degraded fallback behavior. Failure paths are not optional.
- **Migration behavior must be tested.** Test upgrade from empty DB, upgrade from previous schema version, idempotent re-run, and backup-on-migration behavior. Not just happy-path schema creation.
- **Rich output assertions should be normalized.** Assert on structured content, not fragile whitespace or ANSI escape sequences, unless ANSI rendering is the thing explicitly under test.
- **Command grammar edge cases must be tested.** Unknown commands, partial commands, empty input, contextual replies out of scope, and ambiguous inputs all require explicit test coverage.
- **Port contracts may use shared test suites.** If multiple adapters implement the same port, define shared behavioral tests so all adapters satisfy the same contract.
- **Event assertions should verify payload and ordering.** Especially for write-then-emit flows like shutdown, forget, and migration completion.
- **Integration tests cover the T1 continuity loop end-to-end.** Startup → briefing → mode → shutdown → resume. This is the hero path and must be tested as a complete flow.
- **Deletion propagation tested across all tables.** Forget command verified against sessions, memory_items, workspace_snapshots, and seeds — with audit log confirming the event.
- **Tier transition tests required.** Full → degraded → offline → recovery. Each transition tested for correct system behavior and UX output.
- **Exclusion boundary tested end-to-end.** Excluded context must stay opaque across capture, storage, transparency, audit, and cloud prompts.
- **Test markers separate suites.** Use markers: `unit`, `integration`, `e2e`, `windows_only`, `migration`. Keeps local and CI runs predictable.
- **Coverage emphasizes critical paths.** Require high coverage for: continuity loop, deletion flow, tier behavior, migrations, and exclusion boundary. Coverage targets serve safety, not vanity.

### Code Quality & Style Rules

- **Ruff is the single linter and formatter.** No pylint, no flake8, no black, no isort alongside ruff. Ruff handles all linting and formatting in one tool.
- **mypy strict mode on application code.** Full strict checking on `src/nova/`. Limited exclusions only for third-party integration boundaries (pywin32 stubs, etc.) documented explicitly in pyproject.toml.
- **snake_case everywhere except classes.** Modules, functions, variables, table names, column names: `snake_case`. Classes: `PascalCase`. Constants: `UPPER_SNAKE_CASE`. No exceptions.
- **Plural table names.** `sessions`, `memory_items`, `workspace_snapshots`, `audit_log`. Foreign keys: `{referenced_table_singular}_id` (e.g., `session_id`).
- **File naming: snake_case, one module per concern.** `system.py`, `models.py`, `commands.py`, `components.py`. Never PascalCase or kebab-case file names.
- **Docstrings on all public functions and classes.** Use imperative mood. No docstrings on private helpers unless the logic is genuinely non-obvious.
- **No comments that restate the code.** Comments explain _why_, not _what_. If the code needs a _what_ comment, the code should be clearer.
- **Structured logging, not print debugging.** `logging.getLogger("nova.systems.brain")` with stable event names and key-value context in `extra`. No free-form debug strings as primary signal. Log to file only — terminal is Skin's domain.
- **Log levels enforced.** DEBUG: development only. INFO: normal operations. WARNING: degraded behavior. ERROR: failures. Never log excluded/sensitive content — opaque references only.
- **No wildcard imports.** Always import specific names. Never `from module import *`.
- **No magic literals for domain concepts.** Repeated domain values (event names, snapshot labels, capability markers, thresholds) must use enums, constants, or shared normalization helpers. Never copy inline.
- **No broad type escapes without justification.** `Any`, `cast()`, and `# type: ignore` are allowed only at documented integration boundaries with explicit rationale. Every ignore must be narrow and justified.
- **Prefer small, single-purpose functions.** Split functions that mix orchestration, transformation, and rendering preparation. Each function should do one thing clearly.
- **Boolean flag parameters are discouraged.** Avoid `render_briefing(full=True)` or `forget(force=False)`. Prefer distinct functions, enums, or explicit option objects.
- **No dead or commented-out code.** Remove unused code rather than leaving it in place. Version control is the history.
- **Avoid catch-all utility modules.** No giant `utils.py` or `helpers.py` that bypasses system boundaries. Modules should have one clear reason to change.
- **Local and CI quality gates must match.** Ruff, mypy, and pytest rules are enforced identically in local dev and CI. No drift.
- **Naming consistency across agents.** Do not introduce alternate names for the same concept (`mode_name` vs `workspace_mode` vs `active_mode`) unless the distinction is intentional and documented.
- **Public-facing copy stays in Voice/Skin.** User-visible strings should live in Voice or Skin-owned locations, not be scattered through domain logic.
- **No hidden fallback behavior.** If code degrades, defaults, or skips work, it must do so explicitly and log the decision at the appropriate level.

### Development Workflow Rules

- **uv is the package manager.** All dependency operations use `uv`. No pip, no poetry, no conda.
- **All tooling runs through `uv run`.** `uv run ruff`, `uv run mypy`, `uv run pytest`, `uv run nova`. Do not rely on globally installed executables.
- **pyproject.toml is the single config source.** Dependencies, ruff config, mypy config, pytest config — all in pyproject.toml. No separate setup.cfg, tox.ini, or tool-specific config files unless the tool requires it.
- **uv.lock is committed and tool-managed.** Lockfile is checked in for reproducible installs. `uv sync` is the canonical install command. Do not hand-edit uv.lock.
- **Canonical commands (one per workflow step):**
  - Install: `uv sync`
  - Run: `uv run nova`
  - Test (unit): `uv run pytest tests/unit/`
  - Test (integration): `uv run pytest tests/integration/`
  - Lint: `uv run ruff check src/ tests/`
  - Format: `uv run ruff format src/ tests/`
  - Type check: `uv run mypy src/`
  - Full verify: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ && uv run pytest`
- **setup.bat is the first-run entrypoint.** Users run `setup.bat` once. Subsequent sessions: `uv run nova`. The setup script is idempotent — running twice must not corrupt state or overwrite user config.
- **User data lives in `%LOCALAPPDATA%/nova/`.** All runtime state, user config, and accumulated memory. Never in the repo working tree. The repo holds application code and shipped defaults only.
- **Repo tree stays clean.** Local runs, tests, logs, caches, and SQLite files must not write into the repository working directory. Branch changes must never produce user data files in the repo tree.
- **Environment separation must be explicit.** Development, test, and real user runtime use different data roots. Tests use isolated temp paths by default, never `%LOCALAPPDATA%/nova/`. No shared accidental DB/config path.
- **Shipped defaults live in `config/`.** Copied to user data directory on first run only (never overwrite existing user files). Post-install changes to shipped defaults must flow through explicit upgrade logic or migrations, not silent recopying.
- **Migrations run automatically on startup.** `cli.py` checks for pending migrations, auto-backs up nova.db, applies. No manual migration step for users.
- **Back up before every schema-affecting migration.** No exceptions, including local development. Migration runner enforces this.
- **Migration state files are app-managed.** Do not hand-edit migration version state except for deliberate recovery procedures.
- **Startup/setup/migration paths must be idempotent.** Re-running any of them must be safe — no duplicated state, no corrupted config, no repeated audit entries.
- **Local and CI workflows must match.** Same `uv run` commands, same Python version target, same lockfile expectations, same quality gates. No drift between environments.
- **Developer reset is distinct from user data reset.** If a reset script exists, it must clearly separate: reset app environment, reset test fixtures, delete local user data. These must never be conflated.

### Critical Don't-Miss Rules

**Trust & Privacy:**
- **No hidden secondary memory stores.** SQLite-backed truth must not be undermined by undeclared caches, temp artifacts, debug dumps, in-memory replay buffers, local JSON mirrors, or cached summaries containing user memory. If data exists outside the declared system of record, transparency is broken.
- **PromptBuilder is the only cloud egress path.** No system may call the Claude adapter directly with ad hoc context. All cloud-bound payloads must originate from PromptBuilder-approved contracts. No exceptions.
- **Never send raw memory content to the Claude API.** PromptBuilder minimizes and strips excluded context. The Claude adapter only receives PromptBuilder output.
- **Excluded-context protection applies to derived text as well as raw data.** Protected details must not reappear through prose generation, summaries, paraphrases, examples, logs, error messages, or exception payloads. Protection covers both raw data and any language derived from it.
- **No sensitive content in exception messages.** Errors raised across layers must not carry protected app names, window titles, or raw prompt fragments.
- **Deletion is atomic from the user's perspective.** Forget operations are user-visible only after all required deletions succeed across all tables. On partial failure, the system reports incomplete deletion and blocks false verification. Transparency command cannot be re-invoked until deletion is confirmed complete.
- **Transparency must reflect complete truth within the privacy boundary.** No hidden state, no omitted categories, no stale cache. What SQLite contains = what the user sees. What is excluded appears as opaque placeholders, never hidden entirely.
- **API key lives in settings.yaml in the user data directory.** Never hardcoded, never committed, never logged.

**Personality & UX:**
- **Never say "How can I help you today?" or any generic assistant framing.** N.O.V.A. opens with a briefing or stays silent. See the Personality Doctrine in the UX spec.
- **"Done." is a valid response.** Brevity is the default. Verbosity is on-demand only.
- **Brevity must not remove critical risk disclosure.** Short answers are default, but safety warnings, deletion state, degraded mode, and uncertainty must still be explicit.
- **Degraded/offline modes must never fabricate certainty.** When context is partial, stale, or unavailable, N.O.V.A. must say so plainly. Degraded behavior may summarize less, but must never invent continuity or imply fresh certainty it does not have.
- **Personality is in word choice, not formatting.** No special colors, bold, or styling for personality — that's for semantic structure only.
- **Operational output bypasses Voice.** Progress lines (✓/✗), tier notices, confirmations go direct to Skin. Only personality-bearing responses route through Voice.
- **The Briefing Card has three distinct states (A/B/C) with explicit render conditions.** Never render a hollow template with empty fields.
- **No apology spam.** Failures acknowledged cleanly and once, with next action or fallback. Never repeated.
- **Partial restore must be distinguishable from full restore.** "Workspace ready" and "Workspace partially ready" must not share the same success language.

**Safety & Resilience:**
- **Safe-only desktop actions in v0.1.** Launch, focus, arrange — nothing destructive. No file modification, no app closing, no keyboard/mouse simulation.
- **Safety boundaries fail closed, not open.** When capability, permission, or target certainty is ambiguous, desktop action execution must decline rather than guess.
- **Graceful partial is the default failure pattern.** If 2 of 3 apps launch, the session continues with the 2. Never block the session on a single failure.
- **Single malformed API response does NOT trigger tier degradation.** Fall back locally for that specific operation. Tier degrades only after 2+ consecutive failures.
- **SQLite corruption triggers an explicit user-facing recovery flow.** Restore from backup, start fresh, or exit. Never silently recreate the database.
- **Recovery flows must never destroy evidence silently.** Preserve the original broken state until the user chooses otherwise. Do not overwrite, auto-reset, or auto-heal in a way that destroys recoverable data or auditability.
- **Shutdown, quit, and exit all route through the same graceful shutdown flow.** No alias may bypass seed capture and session end.
- **Retries must be bounded and visible where user-impacting.** No silent retry loops that stall the session indefinitely.
- **Operational success messages must reflect actual completion state.** Never show "Saved," "Forgotten," "Shutdown complete," or similar confirmations before the durable operation has actually succeeded. Confirmations are reserved for confirmed outcomes only.
- **One command must never have two meanings in the same context.** Command interpretation is context-safe and deterministic. The same input must not map to multiple actions within the same state without an explicit disambiguation step.

**Performance Budgets (NFRs):**
- Workspace restore: < 30 seconds
- Session briefing: < 5 seconds
- Context poll: < 100ms
- Transparency query: < 3 seconds
- Shutdown: < 30 seconds active user time
- Memory usage: < 750MB
- CPU idle: < 2%
- SQLite storage: < 100MB after 6 months
- Claude API cost: < $2.50/month at 50 turns/day with prompt caching
- **Budgets are per hero path, not average-only.** Especially briefing, shutdown, and transparency.
- **Cost control is enforced by code, not just budget notes.** Token/cost budgets are runtime policy constraints. PromptBuilder and tier policy must actively enforce them.
- **Performance instrumentation must not capture sensitive content.** Metrics may measure duration and counts, not protected payload details.

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Cross-reference with architecture.md and ux-design-specification.md for detailed specs

**For Humans:**
- Keep this file lean and focused on agent needs
- Update when technology stack or architecture changes
- Remove rules that become obvious over time
- This file is the "rules of the road" — detailed specs live in the planning artifacts

Last Updated: 2026-04-14
