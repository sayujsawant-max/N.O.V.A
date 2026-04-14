# Story 1.2: Domain Exceptions & Shared Types

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing any system,
I want a central set of domain exception types and shared types available,
so that adapter-specific exceptions never cross port boundaries and all systems use consistent domain types.

## Acceptance Criteria

1. **`src/nova/core/exceptions.py` defines the T1 domain exception hierarchy** with at minimum:
   - `NovaError(Exception)` — abstract-feeling base for every domain exception raised inside `nova`. All other domain exceptions inherit from it. Document in the class docstring that this is the catch-all for "anything raised by Nova business logic" — outer boundaries (CLI/session top level in Story 1.10) catch this.
   - `StorageError(NovaError)` — raised when persistence fails (sqlite errors, IO errors, lock contention). Adapter wraps `sqlite3.Error` / `OSError` and re-raises as `StorageError`. Used by `core/storage/engine.py` (Story 1.4) and `adapters/sqlite/*` (Story 1.4+).
   - `ConfigError(NovaError)` — raised when YAML config is missing, malformed, or fails schema validation. Used by `core/config.py` (Story 1.6) and the `setup` flow (Epic 2).
   - `ApiUnavailableError(NovaError)` — raised when an external API (Claude) is unreachable, rate-limited, or returns an error status. Adapter wraps `anthropic.APIError` / `anthropic.APIStatusError` / network errors and re-raises as `ApiUnavailableError`. Triggers tier transition signals in Nerve (Story 1.7).
   - `ModeNotFoundError(NovaError)` — raised when the user references a mode that does not exist in `%LOCALAPPDATA%/nova/modes/`. Used by Nerve / Hands (Epic 3+).
   - `AdapterError(NovaError)` — generic re-raise for adapter failures that do not fit a more specific type. Adapters catch the upstream exception, translate, and raise `AdapterError` (or a more specific subclass) so the original adapter exception type **never crosses the port boundary**.
2. **All exceptions follow these contract rules**:
   - Each exception is documented with a one-paragraph docstring stating: who raises it, what conditions trigger it, what the catcher should do (log + degrade, prompt user, surface via Voice, etc.).
   - Each exception accepts a **required** `message: str` (positional) and an optional `cause: BaseException | None = None` keyword-only argument. Constructor signature: `def __init__(self, message: str, *, cause: BaseException | None = None) -> None`. The constructor calls `super().__init__(message)` and stores `cause` as `self.cause` for ergonomic introspection. **The `cause=` argument does NOT itself perform exception chaining** — callers must still use `raise StorageError("...") from underlying`. Python sets `__cause__` only via the `from` keyword. The `cause=` kwarg exists purely for callers that want to retain the underlying exception for later inspection without re-raising. Document this contract precisely in the module docstring (one paragraph) so downstream stories don't rely on `cause=` alone.
   - **No sensitive content in exception messages.** Per project-context.md "No sensitive content in exception messages." Exception messages must not include excluded app names, window titles, raw prompt fragments, API keys, or DB row payloads. Use opaque references (e.g., `"mode 'opaque'"`, `"row id=42"`). Add a one-line comment at the top of `exceptions.py` restating this rule for downstream story authors.
   - Exceptions are **unfrozen** plain `Exception` subclasses (NOT `@dataclass(frozen=True)`) — Python's exception machinery requires mutable instances and `Exception.__init__` semantics. Do not decorate with `@dataclass`.
3. **`src/nova/core/types.py` defines the T1 shared enums** as `enum.StrEnum` subclasses with stable string values (snake_case). Each enum member's value must equal the canonical serialization that will appear in YAML, SQLite, and the event bus. Round-trip stability is non-negotiable — these strings end up in user data files and the database.
   - `CapabilityTier` — members: `FULL = "full"`, `DEGRADED = "degraded"`, `OFFLINE = "offline"`. Used by `core/tiers.py` (Story 1.7) and every event/log that carries tier state.
   - `BriefingState` — members: `FIRST_RUN = "first_run"`, `POST_SETUP = "post_setup"`, `WARM_RESUME = "warm_resume"`. Determines which Briefing Card render path Skin uses (per architecture.md §"State Determination: Nerve Decides", lines 648–675). Note: architecture.md §653 shows this as `Enum` — **override to `StrEnum`** to satisfy the project-context "stable string serialization" rule. Document the divergence in the docstring.
   - `SnapshotType` — members: `STARTUP = "startup"`, `SHUTDOWN = "shutdown"`, `MODE_SWITCH = "mode_switch"`, `PERIODIC = "periodic"`. Persisted into `workspace_snapshots.snapshot_type` (Story 1.5 schema).
   - `ActionType` — members: `APP_LAUNCH = "app_launch"`, `APP_FOCUS = "app_focus"`, `WINDOW_ARRANGE = "window_arrange"`, `MODE_SWITCH = "mode_switch"`, `MODE_RESTORE = "mode_restore"`, `MODE_CREATE = "mode_create"`, `MODE_EDIT = "mode_edit"`, `DELETION = "deletion"`, `SEED_CAPTURE = "seed_capture"`, `TIER_CHANGE = "tier_change"`, `DATABASE_RECOVERY = "database_recovery"`. Persisted into `audit_log.action_type` (Story 1.5 schema). The enum is the single source of truth — `core/audit.py` (Story 1.8) accepts only `ActionType`, never raw strings.
   - `MemoryCategory` — members: `SEED = "seed"`, `SESSION_NOTE = "session_note"`, `CONTEXT_SUMMARY = "context_summary"`, `PATTERN = "pattern"`. Persisted into `memory_items.category` (Story 1.5 schema).
   - `BluntnessLevel` — members: `CALM = "calm"`, `DIRECT = "direct"` only. **Ruthless is explicitly deferred to T2**; do NOT add a `RUTHLESS` member in this story even though the doctrine names it. Add a module-level comment recording the deferral so a later contributor does not casually add it. Used by Voice (Epic 7) and `settings.yaml`.
4. **No adapter-specific types appear in `core/exceptions.py` or `core/types.py`**:
   - No `import sqlite3`, `import anthropic`, `import pywin32`, `import psutil`, `import win32gui`, `import win32com`, or any adapter-tier dependency. Imports are restricted to Python stdlib (`enum`, optionally `typing`).
   - No type hints reference adapter exception classes (e.g., do not type a parameter as `sqlite3.Error`). The whole point of `core/` is that it is adapter-agnostic.
   - This contract is verified by an **AST-level import test** (see Task 5) that walks the parsed module and inspects `ast.Import` / `ast.ImportFrom` nodes — NOT a raw text/regex search. Comments and docstrings that mention adapter names (e.g., a docstring that says `"sqlite3.OperationalError → StorageError"` to explain the chaining contract) are explicitly allowed and must not fail the test.
5. **Both files are exported from the `core` package** via `core/__init__.py`:
   - Update `src/nova/core/__init__.py` to re-export the public names: `NovaError`, `StorageError`, `ConfigError`, `ApiUnavailableError`, `ModeNotFoundError`, `AdapterError`, `CapabilityTier`, `BriefingState`, `SnapshotType`, `ActionType`, `MemoryCategory`, `BluntnessLevel`.
   - Define `__all__` listing exactly the re-exported names so `from nova.core import *` is well-defined and so mypy / ruff `F401` is happy.
   - The existing `core/__init__.py` docstring (`"""Shared infrastructure - events, config, tiers, audit, storage."""`) stays; append the re-exports below it.
6. **Unit tests verify enum serialization round-trips** for **every** enum and member:
   - `tests/unit/core/test_types.py` — for each enum, parametrize over all members and assert: `EnumClass(member.value) is member` (string → enum), `member.value == "<expected_string>"` (member → string), and `str(member)` returns the value (StrEnum behavior — important because YAML/SQL serialization may go through `str()`). Use `pytest.mark.parametrize` with `(EnumClass, expected_value)` tuples; one test function per enum, not one per member.
   - Also assert `member.name` is uppercase snake (`FULL`, `FIRST_RUN`, etc.) — defends against accidental rename of the Python identifier.
   - Test that **invalid string** → `ValueError` (e.g., `CapabilityTier("ruthless")` raises). This guards against an accidental future addition without round-trip stability.
   - Test that the `BluntnessLevel` enum has **exactly two members** (length assertion). Documents the "ruthless deferred" rule as a regression gate.
7. **Unit tests verify exception construction and chaining** in `tests/unit/core/test_exceptions.py`:
   - For each exception class, assert it is a subclass of `NovaError` and ultimately of `Exception`.
   - Assert `str(exc)` returns the message passed to the constructor.
   - Assert `from cause` chaining works: catch a `ValueError`, raise `StorageError("db unreachable") from cause`, assert `caught.__cause__ is cause`.
   - Assert that catching `NovaError` catches every subclass (one parametrized test enumerating all six exception classes).
   - Note: do NOT test exception messages for sensitive-content scrubbing here — that is the **caller's** responsibility (per AC 2). This story tests the contract; downstream stories test their own scrubbing.
8. **Test layout follows the existing convention**: `tests/unit/core/` directory exists (created lazily by pytest — no `__init__.py` per Story 1.1 D1). Place the two new test files there. They are `unit`-marker eligible; if you need to add markers for filtering, use `pytest.mark.unit` (already declared in `[tool.pytest.ini_options].markers`).
9. **Quality gates pass clean**: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0. mypy strict must succeed on both new files **and** the new tests. Use precise type annotations everywhere — no `Any`, no `# type: ignore`, no implicit `Optional`. The `BaseException | None` typing in the exception constructors must satisfy strict mode (use `from __future__ import annotations` if needed for Python 3.12 forward-compat).
10. **Repo tree stays clean** after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, or `*.egg-info/` artifacts staged by `git status`. Same standard as Story 1.1 AC #13.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/exceptions.py`** (AC: #1, #2, #4)
  - [x] Module docstring states purpose ("All domain exception types — adapter-specific exceptions never cross port boundaries; adapters translate and re-raise.") and the chaining contract (`raise SubError("...") from cause`).
  - [x] Add a one-line constant comment at the top: `# RULE: never embed sensitive content (excluded app names, window titles, prompt fragments, API keys, DB row payloads) in exception messages. Use opaque references.`
  - [x] Define `NovaError(Exception)` with constructor signature `def __init__(self, message: str, *, cause: BaseException | None = None) -> None`. `message` is required (positional). The constructor calls `super().__init__(message)` and assigns `self.cause = cause` for callers that want to retain the underlying exception object without re-raising. Do **not** set `self.__cause__` manually — `__cause__` is Python's official chaining slot and is populated only by `raise ... from cause` at the call site. The `cause=` kwarg is **ergonomic introspection only, not chaining** — callers MUST still write `raise StorageError("…") from underlying` to get a chained traceback. Restate this rule in the class docstring so it is impossible to miss. Class docstring also describes the "anything raised by Nova business logic" contract.
  - [x] Define `StorageError`, `ConfigError`, `ApiUnavailableError`, `ModeNotFoundError`, `AdapterError` as direct subclasses of `NovaError`. Each has a docstring covering: who raises, when, what catcher does. Inherit constructor from `NovaError` — do not re-define unless adding new fields (do not add new fields in T1).
  - [x] No imports beyond stdlib. `from __future__ import annotations` added defensively (kept module imports clean and consistent with the project style); allowlisted by the AST isolation test.

- [x] **Task 2: Author `src/nova/core/types.py`** (AC: #3, #4)
  - [x] Module docstring lists all six enums and references the architecture/epic source: "Shared domain enums per epics.md Story 1.2 + architecture.md §"State Determination" + project-context.md "Enum for constrained values."
  - [x] `from enum import StrEnum` (Python 3.11+ stdlib; project pins 3.12 — use it directly).
  - [x] Define `CapabilityTier(StrEnum)` with the three members above. Class docstring references `core/tiers.py` (Story 1.7) and the architecture's tolerant-degrade model.
  - [x] Define `BriefingState(StrEnum)` with the three members above. Class docstring **explicitly notes the divergence from architecture.md §653 (Enum → StrEnum)** and the rationale (stable string serialization to YAML/SQLite/events). State that this story owns the divergence.
  - [x] Define `SnapshotType(StrEnum)` with the four members above. Class docstring references the `workspace_snapshots.snapshot_type` column (Story 1.5).
  - [x] Define `ActionType(StrEnum)` with all eleven members above. Class docstring states this is the **only** valid set for `audit_log.action_type`; `core/audit.py` (Story 1.8) will type its API with this enum, never `str`.
  - [x] Define `MemoryCategory(StrEnum)` with the four members above. Class docstring references `memory_items.category` (Story 1.5).
  - [x] Define `BluntnessLevel(StrEnum)` with **exactly two** members (`CALM`, `DIRECT`). Module-level comment block above the class body cites epics.md line 674 and forbids adding `RUTHLESS`.
  - [x] No imports beyond stdlib (`enum` + `__future__` only).

- [x] **Task 3: Update `src/nova/core/__init__.py` to re-export public names** (AC: #5)
  - [x] Keep the existing one-line module docstring.
  - [x] Add `from nova.core.exceptions import (...)` listing the six exception classes (one per line, alphabetized).
  - [x] Add `from nova.core.types import (...)` listing the six enum classes (one per line, alphabetized).
  - [x] Define `__all__: list[str] = [...]` containing all twelve names. Alphabetized for diff stability.
  - [x] Use absolute imports (`from nova.core.exceptions import ...`), not relative — per project-context "Absolute imports only between systems."
  - [x] `__all__: list[str]` annotated for mypy strict.

- [x] **Task 4: Author `tests/unit/core/test_exceptions.py`** (AC: #7, #8)
  - [x] Module docstring states "Story 1.2 contract tests for `nova.core.exceptions`."
  - [x] Test: each exception class is a subclass of `NovaError` and `Exception` (parametrize over the six classes).
  - [x] Test: `str(StorageError("db down"))` returns `"db down"` (parametrized over all six classes for symmetry — same data shape as the other tests).
  - [x] Test: `raise StorageError("db down") from underlying` chains correctly — `caught.__cause__ is underlying`. Parametrized over all six classes.
  - [x] Test: `StorageError("db down", cause=underlying).cause is underlying` AND `.__cause__ is None` — regression gate for the `cause=` ≠ chaining contract. Parametrized over all six classes.
  - [x] Test: `message` is a required positional argument — `with pytest.raises(TypeError): exc_cls()`. Parametrized over all six classes.
  - [x] Test: a `try / except NovaError` block catches each subclass instance (parametrized over all six). Wires the Story 1.10 top-level catch contract.
  - [x] No production-code mocking — pure constructor / inheritance tests. Total runtime < 50ms.

- [x] **Task 5: Author `tests/unit/core/test_types.py`** (AC: #6, #8)
  - [x] Module docstring states "Story 1.2 round-trip serialization tests for `nova.core.types`."
  - [x] Build a parametrize set per enum: `[(member, member.value) for member in EnumClass]`. One test function per enum keeps failure messages legible.
  - [x] Round-trip test: `assert EnumClass(value) is member`, `assert member.value == value`, `assert str(member) == value`.
  - [x] Name discipline assertion folded into the round-trip body: `NAME_PATTERN.match(member.name)` against `^[A-Z][A-Z0-9_]*$`.
  - [x] Invalid-value test: `with pytest.raises(ValueError): EnumClass("__definitely_not_a_member__")`. Parametrized over all six enums.
  - [x] **`BluntnessLevel` regression gate**: `assert len(list(BluntnessLevel)) == 2` AND `{m.value for m in BluntnessLevel} == {"calm", "direct"}`. Docstring cites the deferral rationale.
  - [x] Exact-membership tests added for every enum (defends against silent additions or removals beyond the regression gate's narrow scope).
  - [x] **Adapter-isolation AST import test** (AC #4) authored at `tests/unit/core/test_core_isolation.py`. Walks `ast.Import` / `ast.ImportFrom` nodes, derives top-level module name, asserts each is in the allowlist `{"enum", "typing", "__future__"}`. Explicitly forbids `sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32api`, `win32gui`, `win32com`, `win32con`, `rich`, `yaml`. Comments and docstrings mentioning adapter names are allowed (AST walk only inspects imports).
  - [x] Source-file lookup uses `inspect.getsourcefile(module)` + `Path(...).read_text(encoding="utf-8")` with a `None` guard for mypy strict. Parametrized over both `nova.core.exceptions` and `nova.core.types`.

- [x] **Task 6: Run quality gates and verify clean tree** (AC: #9, #10)
  - [x] `uv run ruff format --check src/ tests/` — `29 files already formatted` / exit 0.
  - [x] `uv run ruff check src/ tests/` — `All checks passed!` after fixing one E501 long-line in an `ApiUnavailableError` docstring header. No `T20` fires.
  - [x] `uv run mypy src/ tests/` — `Success: no issues found in 29 source files` / exit 0. `BaseException | None` accepted under strict mode without complaint.
  - [x] `uv run pytest` — `80 passed in 0.15s`. Adds 79 new passing tests across `test_exceptions.py` (36), `test_types.py` (39), `test_core_isolation.py` (4). Scaffold smoke test still passes.
  - [x] Full verify one-liner exit 0.
  - [x] `git status` clean: only the six intended files (`src/nova/core/exceptions.py`, `src/nova/core/types.py`, `src/nova/core/__init__.py`, three test files in `tests/unit/core/`) plus sprint-status edit and the story file. No cache artifacts.

## Dev Notes

### Story Type: Foundational primitives — every subsequent 1.x story depends on this

This story produces the project's **vocabulary**: the exception types every adapter must translate to, and the enum types every system must use for constrained values. After this story, downstream stories import from `nova.core` and never invent ad-hoc strings or exception subclasses outside `core/`.

### Scope guard (hard stop)

- **Do NOT implement the event bus, config loader, tier state machine, audit logger, or any system logic.** Each has its own story (1.3, 1.6, 1.7, 1.8). This story is exceptions + enums + their tests. Nothing else.
- **Do NOT add new exception classes beyond the six in AC #1.** If a downstream story needs a more specific exception (e.g., `BackupFailedError`), it adds it in *its* story as a subclass — not here. Resist "while I'm here" expansion.
- **Do NOT add new enum members or new enums.** The six enums and their exact membership lists are pinned by epics.md §"Story 1.2" lines 657–676. Especially: `BluntnessLevel` has exactly two members in T1; `RUTHLESS` is deferred.
- **Do NOT add `frozen=True` to exception classes.** Exceptions are not dataclasses. Python's exception machinery requires mutable instances and standard `Exception.__init__` semantics. The "frozen dataclass" rule applies to **events** and **value objects**, not exceptions (project-context.md line 37 wording is precise about this).
- **Do NOT modify `pyproject.toml`, `.gitignore`, `.gitattributes`, `.python-version`, or any Story 1.0 / 1.1 deliverable.** Those are frozen.
- **Do NOT touch `config/`, `docs/config-schemas.md`, `cli.py`, or `app.py`.** Out of scope.
- **If you write more than ~150 lines of Python total across the two source files, you are probably reinventing scope.** Exceptions and enums are short by design.

### Critical constraints and gotchas

- **`StrEnum`, not `Enum`, for every enum in AC #3.** Architecture.md §653 shows `BriefingState(Enum)` with explicit `.value` strings. Project-context.md line 41 lists `StrEnum` as a permitted choice. The decisive constraint is "stable string serialization" (project-context line 54): `StrEnum` makes the string value the canonical serialization automatically, including under `json.dumps(member)` and `f"{member}"` interpolation. **Override architecture.md §653 to `StrEnum`.** Document the divergence in the `BriefingState` class docstring; cite this story as the owner of the divergence.
- **`StrEnum` was added in Python 3.11.** The project pins 3.12. Use it directly: `from enum import StrEnum` — no shim, no fallback.
- **`StrEnum.value` returns the string; `str(member)` returns the same string.** This is the load-bearing behavior — it means a Skin component or YAML serializer can do `f"tier: {tier}"` and get `"tier: full"` without thinking. Test this contract explicitly (AC #6).
- **Snake_case stable string values** for every enum member. These strings end up in YAML config files (`bluntness: direct`), in SQLite `audit_log.action_type` (`"app_launch"`), and in event payloads. Renaming a value later is a **schema migration** event. Get them right now.
- **`ActionType` must list all eleven members** even though only some land in T1. Stories 1.7 (tier_change), 1.8 (audit logger consumer), 3.x (session/seed actions), 5.x (deletion, transparency), and 6.x (mode actions) all consume from this enum. Listing them all here means later stories add audit calls, not enum members. The eleven members come from epics.md line 672.
- **Exception messages must be sanitization-safe.** Per project-context line 174 ("No sensitive content in exception messages") and line 173 ("Excluded-context protection applies to derived text…including exception payloads"). Adapters that translate to domain exceptions are responsible for stripping app names, window titles, prompt fragments. The exception classes themselves don't enforce this — the **rule is documented** so callers don't paste raw strings into `StorageError(f"failed to update {window_title}")`.
- **Exception chaining via `from cause`, not constructor magic.** The constructor accepts `cause: BaseException | None = None` and stores it as `self.cause` for ergonomic introspection, **but passing `cause=` alone does NOT chain the exception.** Callers MUST write `raise StorageError("db unreachable") from underlying_sqlite_error` to populate `__cause__` and produce a chained traceback. Doing `raise StorageError("db down", cause=underlying)` without `from` leaves `__cause__` as `None` and Python prints the exceptions as unrelated. The constructor kwarg is a convenience slot, not a substitute for `from`. Do NOT manually `self.__cause__ = cause` in `__init__` — that bypasses `__suppress_context__` and surprises traceback formatting. Document this distinction in the `NovaError` class docstring AND in the module docstring so downstream story authors don't reach for `cause=` and assume chaining.
- **mypy strict scope already includes `tests/`** (Story 1.1 D3). New test files must satisfy strict mode. Annotate all test function parameters: `def test_round_trip(enum_class: type[StrEnum], expected: str) -> None:`. Annotate fixtures. No `Any`. The parametrize decorator's `argvalues` types are inferred — fine.
- **`# noqa: T201`** is only allowed in `cli.py` (Story 1.1). Do NOT add `print()` statements anywhere in this story; the ruff `T20` rule will catch them.
- **`tests/` has no `__init__.py`** (Story 1.1 D1). Create test files directly under `tests/unit/core/` — pytest discovers them via its standard collection logic. If pytest cannot find them, it is a `pyproject.toml` `testpaths` configuration question, not an `__init__.py` question.

### Repo shape at time of this story

After Story 1.1, the repo contains:
- `src/nova/__init__.py` (with `__version__` derived via `importlib.metadata.version("nova")`)
- `src/nova/cli.py` (placeholder banner, Story 1.10 replaces)
- `src/nova/app.py` (empty composition-root placeholder)
- `src/nova/ports/__init__.py` (empty — Story 1.9 fills)
- `src/nova/systems/{brain,eyes,hands,shield,voice,ritual,skin,nerve}/__init__.py` (empty packages)
- `src/nova/adapters/{claude,win32,sqlite,rich}/__init__.py` (empty packages)
- `src/nova/core/__init__.py` (one-line docstring — **this story extends it**)
- `src/nova/core/storage/__init__.py`, `src/nova/core/storage/migrations/__init__.py` (empty — Stories 1.4/1.5 fill)
- `src/nova/setup/__init__.py` (empty — Epic 2 fills)
- `tests/conftest.py` (empty — Stories 1.4/1.6 add fixtures)
- `tests/unit/test_scaffold.py` (single import smoke test)
- `pyproject.toml` (hatchling, ruff with `T20`, mypy strict on `src/` + `tests/` with `explicit_package_bases = true`, pytest with `--strict-markers`)
- `uv.lock` (committed, 39 packages including `nova==0.1.0`)
- `.gitattributes` (forces LF line endings)

This story **adds**:
- `src/nova/core/exceptions.py` (new)
- `src/nova/core/types.py` (new)
- `src/nova/core/__init__.py` (modify — append re-exports + `__all__`)
- `tests/unit/core/test_exceptions.py` (new)
- `tests/unit/core/test_types.py` (new — also hosts the adapter-isolation grep test, or split into `test_core_isolation.py`)

### Previous Story Intelligence — Story 1.1 (done 2026-04-14)

Code-review patches that landed in Story 1.1 directly affect this story's authoring conventions:

- **mypy scope is `src/nova` + `tests`** (Story 1.1 D3, applied via `[tool.mypy] files = [...]`). Test files MUST be type-annotated. `def test_x() -> None:` everywhere. Fixtures need annotations. `pytest.mark.parametrize` decorators infer types from argvalues — usually fine, but if mypy complains, annotate the wrapped function parameters explicitly.
- **`__version__` is derived via `importlib.metadata.version("nova")`** (Story 1.1 D4). Single source of truth pattern. **Apply the same idea here**: there is one canonical home for each domain exception (in `core/exceptions.py`) and one canonical home for each enum (in `core/types.py`). Re-exports from `core/__init__.py` are a shortcut for callers, not a second definition.
- **Ruff rule `T20` (flake8-print) is enabled** (Story 1.1 D2). `print()` is banned outside `cli.py`. Do not add `print()` to test files even for debug — use `pytest -s` or `caplog` if you need output.
- **`tests/` has no `__init__.py`** (Story 1.1 D1, pytest goodpractices for `src/` layout). Place new test files directly under `tests/unit/core/`. Pytest discovers them through `[tool.pytest.ini_options].testpaths = ["tests"]`.
- **`.gitattributes` forces LF line endings.** Do not author files with CRLF (`\r\n`); editors should respect `eol=lf` for `*.py`. If `git status` shows phantom whitespace diffs, the editor settings are wrong.
- **`addopts = ["--strict-markers"]` is on.** If you add a `@pytest.mark.unit` marker, it works (declared in `markers`). If you typo it (`@pytest.mark.unti`), pytest fails fast — that is intentional.
- **`anthropic>=0.94,<1`, `pywin32>=306`, `psutil>=5.9`, `pyyaml>=6.0`, `rich>=13`** are pinned. **None of these adapter dependencies should be imported in this story.** AC #4 explicitly forbids it — the whole point of `core/` is adapter-agnostic. The deps are listed here so you know they exist; the discipline is to NOT touch them.
- **Story 1.0's `docs/config-schemas.md` already references `bluntness: calm | direct`** (settings schema). Your `BluntnessLevel` enum must produce those exact strings. Cross-check by opening `docs/config-schemas.md` and searching for `bluntness` if uncertain.
- **Story 1.0's `config/exclusions.yaml` and `config/modes/coding.yaml` are frozen.** Do not touch them.
- **Scope discipline carried Story 1.1.** The same discipline applies here — the story is **exceptions + enums + tests**. Resist the urge to "while I'm here" add `events.py` (Story 1.3), `tiers.py` (Story 1.7), or anything else.

### Git Intelligence — last 3 commits

```
1da5c45 Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)
80dba55 Story 1.0 code review: resolve 20 findings, mark done
5b9d026 Initialize repo with planning artifacts and Story 1.0 (YAML config schemas spike)
```

- **Commit style:** terse, imperative, story ID prefix. For this story, expect: `"Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)"` or similar.
- **Story 1.1 commit added 35 files (~+942 lines).** This story is much smaller — expect ~5 new/modified files and well under 300 lines including tests. Do not let the diff balloon.
- **No prior Python files in `core/` beyond `__init__.py` shells.** This story is the first to put logic in `src/nova/core/`. The pattern set here informs Stories 1.3 (events), 1.6 (config), 1.7 (tiers), 1.8 (audit), 1.9 (ports). Get it right.

### Latest Tech Information (as of 2026-04-14)

- **Python 3.12.13** is the resolved managed interpreter (Story 1.1 debug log). `StrEnum` (PEP 663-adjacent, added in 3.11) is available — use it directly with `from enum import StrEnum`.
- **`enum.StrEnum` semantics**: members are `str` subclasses. `BluntnessLevel.CALM == "calm"` is `True`. `f"{BluntnessLevel.CALM}"` produces `"calm"` (NOT `"BluntnessLevel.CALM"` — that is plain `Enum` behavior). This is exactly what we want for YAML/SQL serialization and prevents the `.value` boilerplate everywhere.
- **`enum.StrEnum` and JSON**: `json.dumps(BluntnessLevel.CALM)` works without a custom encoder — produces `'"calm"'`. Useful for future event-bus serialization.
- **`mypy 1.20.1` + strict mode**: `StrEnum` is fully understood. No special hints needed. `EnumClass(value)` is typed as returning `EnumClass`.
- **`pytest 9.0.3` + `pytest-asyncio 1.3.0`**: synchronous tests need no marker. `asyncio_mode = "auto"` is set but irrelevant here (no async code in this story).
- **No new dependencies needed.** stdlib (`enum`) is sufficient. Do not add anything to `pyproject.toml`.

### Project Structure Notes

- **Source files land at `src/nova/core/exceptions.py` and `src/nova/core/types.py`** — exact paths from architecture.md §1386–1387.
- **Test files land at `tests/unit/core/test_exceptions.py` and `tests/unit/core/test_types.py`** — mirrors `src/nova/core/`. The architecture's test-tree spec (architecture.md §1411–1421) explicitly lists `tests/unit/core/` as a directory; it does not list `test_exceptions.py` or `test_types.py` by name (the spec lists `test_events.py`, `test_config.py`, etc. — those come in their own stories). Adding the two new files is consistent with the convention; no architecture deviation.
- **`core/__init__.py` re-export pattern** is the project's first use of `__all__`. Establish the pattern carefully so Stories 1.3/1.6/1.7/1.8 can copy it: alphabetized imports, alphabetized `__all__: list[str]`, no relative imports.
- **No new directories** are created in this story. `tests/unit/core/` already exists implicitly (no `__init__.py` needed). `src/nova/core/` already exists from Story 1.1.

### Testing standards summary

- **Test framework: pytest + pytest-asyncio + pytest-cov** (already configured). This story adds **only synchronous** tests; no async.
- **Markers available**: `unit`, `integration`, `e2e`, `windows_only`, `migration` (`--strict-markers` is on). Use `pytest.mark.unit` if you want to enable `pytest -m unit` filtering — optional. The new tests are all unit-tier.
- **mypy strict applies to test files.** Annotate function params and return types: `def test_round_trip(enum_class: type[StrEnum], expected: str) -> None: ...`. The parametrize argvalues themselves don't need annotation but the wrapped function signature does.
- **Coverage gate not enforced** (deferred to Story 1.11). Aim for 100% coverage of `exceptions.py` and `types.py` anyway — both files are small and trivially testable.
- **No fixtures needed in this story.** Do not add anything to `tests/conftest.py` — fixtures arrive with Stories 1.4 (test DB) and 1.6 (test config).
- **One assertion per logical claim where possible**, but parametrize aggressively over enum members and exception classes — both lend themselves to data-driven tests.
- **Test runtime budget**: <100ms total for both new modules. These are pure constructor / inheritance / serialization tests with no IO.

### Critical Don't-Miss Rules (from project-context.md)

Carry-forward, with rationale:
- **Domain exceptions only — adapter exceptions never cross port boundaries** (line 39, line 173, line 174, line 1275 of architecture.md). This story creates the vocabulary that makes that rule enforceable.
- **Enum for constrained values** (line 41). The six enums in AC #3 ARE the constrained values listed in project-context.md line 41 — same names, same purpose.
- **Stable serialization** (line 54). `StrEnum` enforces this for enums automatically.
- **No sensitive content in exception messages** (line 174). Documented as a rule in `exceptions.py`'s comment header.
- **Absolute imports only** (line 244 of epics.md, also project-context). `core/__init__.py` re-exports use `from nova.core.exceptions import ...`, never relative.
- **No `print()` anywhere except `cli.py`** (project-context, enforced by ruff `T20` since Story 1.1).
- **No `Any` in application code** (line 45 of project-context). The `cause: BaseException | None` constructor parameter is precise enough; do not weaken to `Any`.

### Cross-story impact (where these primitives get consumed)

| Consumer story | Uses from this story | Why |
|---|---|---|
| 1.3 Event bus | none directly, but events will reference `CapabilityTier`, `BriefingState` (in later epics) | Typed events compose with shared enums |
| 1.4 SQLite storage | `StorageError` | sqlite3 errors translate at the adapter |
| 1.5 Migration runner | `StorageError` | migration failures bubble as `StorageError` |
| 1.6 Config loader | `ConfigError` | YAML parse / schema errors |
| 1.7 Tier state machine | `CapabilityTier` (mandatory), `ApiUnavailableError` (input signal) | Tier enum is the state space |
| 1.8 Audit logger | `ActionType` (mandatory) | `audit_log.action_type` only accepts the enum |
| 1.9 Port interfaces | `NovaError` (declared in `Raises:` doc strings) | Ports promise domain exceptions |
| 1.10 CLI / composition root | `NovaError` (top-level catch) | Outer boundary catches Nova exceptions for graceful exit |
| 2.x setup wizard | `ConfigError` | first-run YAML validation |
| 3.x session loop | `ModeNotFoundError`, `ActionType.SEED_CAPTURE`, `MemoryCategory.SEED` | session ceremonies |
| 4.x context awareness | `ActionType` (audit), `MemoryCategory` (memory writes) | exclusion + memory plumbing |
| 5.x transparency / forget | `ActionType.DELETION`, `MemoryCategory` | deletion propagation |
| 6.x mode orchestration | `ActionType.MODE_*`, `ModeNotFoundError` | mode CRUD + restore |
| 7.x personality | `BluntnessLevel` | configurable bluntness in Voice |

This table is included to make the **breadth of impact** unmistakable to the dev agent: the two files this story produces are imported by ~12 future stories. Naming and value-string mistakes are expensive to undo because they leak into user data files (`settings.yaml`, `audit_log` rows, `memory_items` rows).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.2: Domain Exceptions & Shared Types](../planning-artifacts/epics.md) — canonical AC, lines 657–676.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — lines 359–395, epic objectives, architecture constraints (esp. "Domain exceptions only — adapter exceptions never cross port boundary", line 388).
- [Source: _bmad-output/planning-artifacts/architecture.md#Error Handling Patterns](../planning-artifacts/architecture.md) — lines 1230–1247, exception flow at adapter boundary, examples (`sqlite3.OperationalError → StorageError`, `anthropic.APIStatusError → ApiUnavailableError`).
- [Source: _bmad-output/planning-artifacts/architecture.md#Enforcement Guidelines](../planning-artifacts/architecture.md) — lines 1265–1287, enforcement rule #7 ("Use domain exception types"), #11 (typed event classes).
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — lines 1379–1394, file paths `core/exceptions.py`, `core/types.py`.
- [Source: _bmad-output/planning-artifacts/architecture.md#State Determination: Nerve Decides](../planning-artifacts/architecture.md) — lines 648–675, `BriefingState` definition (override from `Enum` to `StrEnum`).
- [Source: _bmad-output/planning-artifacts/architecture.md#T1 Skeleton — What Exists at First Implementation Milestone](../planning-artifacts/architecture.md) — line 1516, lists `core/exceptions.py` and `core/types.py` as T1-active.
- [Source: _bmad-output/project-context.md](../project-context.md) — line 37 (frozen dataclasses for value objects), line 39 (domain exceptions only), line 41 (Enum for constrained values), line 45 (no `Any`), line 54 (stable serialization), line 173 (excluded-context protection in derived text including exception payloads), line 174 (no sensitive content in exception messages).
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md#Review Findings](./1-1-project-scaffolding-and-package-setup.md) — Story 1.1 D1/D2/D3/D4 decisions (no `tests/__init__.py`, `T20` enabled, mypy widened to `tests`, `__version__` via `importlib.metadata`) — all carry into authoring conventions for this story.
- [Source: _bmad-output/implementation-artifacts/1-0-define-yaml-config-schemas-spike.md](./1-0-define-yaml-config-schemas-spike.md) — pinned `bluntness: calm | direct` in `settings.defaults.yaml`; `BluntnessLevel` enum values must match.
- [Source: docs/config-schemas.md](../../docs/config-schemas.md) — settings schema fields that consume `BluntnessLevel` values.

## Review Findings

Produced by the bmad-code-review workflow on 2026-04-14. Three parallel adversarial review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) ran against the Story 1.2 uncommitted diff. Acceptance Auditor: **Approve with minor findings** — all 10 ACs satisfied; only cosmetic gaps. Adversarial layers surfaced API-design questions and AST-test gaps to address before moving to `done`.

**Caveat:** review ran in the same Claude session that implemented the story. Single-LLM bias possible.

### Decision-Needed Findings (2 — resolved)

- [x] [Review][Decision] **`message=""` and `message=None` are accepted today, untested either way** — Resolved (b): pin current behavior with WAI tests. Reason: this story is about the exception/type contract, not input policing; tightening `NovaError.__init__` would create avoidable ripple effects into Story 1.10's top-level handler. WAI tests added in Patch P13.

- [x] [Review][Decision] **`typing` in the AST allowlist pre-permits `if TYPE_CHECKING:` adapter imports** — Resolved (a): shrink the allowlist to `{"enum", "__future__"}`. Reason: matches actual usage today, closes the unnecessary hole without adding test complexity. Allowlist update in Patch P14.

### Patch Findings (14)

- [x] [Review][Patch] **AST isolation test is blind to relative imports** [tests/unit/core/test_core_isolation.py:51-53] — Loop does `if node.level != 0 or node.module is None: continue`, so `from .. import adapters` or `from ..adapters.sqlite import Connection` slips past both `test_no_forbidden_imports` and `test_imports_within_allowlist`. Fix: drop the `level != 0` guard and either fail any relative import in `core/exceptions.py` / `core/types.py` (cleanest — core has no legitimate sibling-package relative imports) or resolve relative imports against `module.__package__` and check the resolved top-level.

- [x] [Review][Patch] **AST isolation test misses dynamic imports** [tests/unit/core/test_core_isolation.py:46-54] — Walk only matches `ast.Import` / `ast.ImportFrom`. `importlib.import_module("anthropic")` and `__import__("sqlite3")` evade detection. Fix: add a second AST pass for `ast.Call` where `func.id == "__import__"` or `func.attr == "import_module"`; assert no string literal arg matches `FORBIDDEN_TOPLEVEL_MODULES`. Belt-and-suspenders: also assert `set(sys.modules) & FORBIDDEN_TOPLEVEL_MODULES` is empty after importing `nova.core.exceptions` and `nova.core.types` in a clean subprocess.

- [x] [Review][Patch] **Allowlist admits `from enum import _private`** [tests/unit/core/test_core_isolation.py:21,50-53] — Allowlist is module-granular, so `from enum import _EnumDict` or any future leading-underscore symbol passes. Fix: collect `(module, symbol_name)` pairs for `enum` imports and assert each `symbol_name` is in `{"StrEnum", "Enum", "IntEnum", "auto", "unique"}`.

- [x] [Review][Patch] **`cause=self` and circular cause chains accepted silently** [src/nova/core/exceptions.py:55-57] — `NovaError.__init__` stores `cause` with no guard. `exc.cause = exc` or mutual `a.cause = b; b.cause = a` creates a cycle that any `.cause`-walking helper will loop on. Fix: in `__init__`, raise `ValueError("cause cannot be self")` when `cause is self` (cheap defensive check). Document the absence of cycle detection for second-degree cycles — callers walking `.cause` chains must use a visited set.

- [x] [Review][Patch] **`cause=` accepts non-`BaseException` at runtime** [src/nova/core/exceptions.py:55-57] — Type hint says `BaseException | None` but `__init__` does no isinstance check. `StorageError("x", cause="oops")` populates `self.cause` with junk; the failure surfaces far from the bug if anyone later does `raise err.cause from None`. Fix: add `if cause is not None and not isinstance(cause, BaseException): raise TypeError(f"cause must be BaseException or None, got {type(cause).__name__}")` plus a parametrized test asserting the rejection.

- [x] [Review][Patch] **`types.py` docstring overpromises YAML serialization** [src/nova/core/types.py:8-10] — Module docstring says enum string values appear "in YAML config" and that `f"{member}"` and `json.dumps(member)` yield canonical values. `json.dumps(StrEnum_member)` works (StrEnum is a `str` subclass), **but `yaml.safe_dump(StrEnum_member)` does NOT** — PyYAML raises `RepresenterError` because it has no representer for the `StrEnum` subclass. Fix: drop or qualify the YAML claim in the docstring (e.g., "appears as a plain string in YAML config files written by Story 1.6's config writer, which is responsible for converting members to their `.value` before serialization"). Add a unit test exercising `json.dumps`/`json.loads` round-trip for one representative enum to lock the actually-supported path.

- [x] [Review][Patch] **Cross-enum value overlap (`mode_switch`) lacks WAI guard** [src/nova/core/types.py:62,78; tests/unit/core/test_types.py] — `SnapshotType.MODE_SWITCH` and `ActionType.MODE_SWITCH` share the value `"mode_switch"` (intentional per epics.md:671-672), and `StrEnum` makes them `==` to each other and to bare strings. A function typed `def log(action: ActionType)` accepts `SnapshotType.MODE_SWITCH` at runtime by accident. Fix: add a WAI test pinning `type(SnapshotType.MODE_SWITCH) is SnapshotType` and `type(ActionType.MODE_SWITCH) is ActionType` and asserting the cross-class string equality is intentional. This documents the trap so Story 1.8 (audit logger) knows to use `isinstance(x, ActionType)` at its API boundary, not bare-string comparison.

- [x] [Review][Patch] **`NovaError` lacks `__repr__`; `cause` invisible in debuggers/logs** [src/nova/core/exceptions.py:40-57] — `repr(StorageError("x", cause=ValueError("y")))` returns `"StorageError('x')"`; `cause` is invisible. Fix: add `def __repr__(self) -> str:` that includes `cause=...` only when set, e.g. `f"{type(self).__name__}({self.args[0]!r}, cause={self.cause!r})"` if `self.cause is not None` else default. Add a test pinning the format.

- [x] [Review][Patch] **`_toplevel_imports` is misnamed** [tests/unit/core/test_core_isolation.py:38-54] — Uses `ast.walk`, which descends into functions, classes, and `if`-blocks. The function actually returns *all* imports, not top-level only. Fix: rename to `_all_imports` (truthful naming; behavior is correct as-is and stricter than the name suggests).

- [x] [Review][Patch] **`ids=lambda v: v if isinstance(v, str) else v.name` has dead branch** [tests/unit/core/test_types.py multiple parametrize blocks] — `StrEnum` members are `str` instances, so `isinstance(v, str)` is always `True` for members; the `v.name` branch is unreachable. Pytest IDs read like `[full-full]` instead of the intended `[FULL-full]`. Fix: change to `ids=lambda v: v.name if hasattr(v, "name") else v` (member first, then bare value strings).

- [x] [Review][Patch] **`test_invalid_value_raises` relies on a magic sentinel** [tests/unit/core/test_types.py:106-110] — Uses `"__definitely_not_a_member__"`. If anyone ever adds a value matching that sentinel, the test silently passes. Fix: compute a guaranteed-miss per enum, e.g., `unused = "x" + "_".join(m.value for m in enum_cls)` (length-bounded) or `"\x00invalid\x00"`.

- [x] [Review][Patch] **AC #7 nested try/raise diverges from spec form** [tests/unit/core/test_exceptions.py:45-53] — Spec wording calls for inline `raise StorageError("...") from cause`; current test uses a nested `try / except ValueError as caught_underlying / raise exc_cls("wrapped") from caught_underlying` form. Functionally equivalent; cosmetic. Fix: simplify to spec form for readability — `underlying = ValueError("root"); try: raise exc_cls("wrapped") from underlying; except exc_cls as caught: assert caught.__cause__ is underlying`.

- [x] [Review][Patch] **`message=""` / `message=None` behavior unpinned** [src/nova/core/exceptions.py:55-57; tests/unit/core/test_exceptions.py] — Resolution of Decision D1 (b). Pin current behavior with WAI (works-as-intended) tests so Story 1.10's CLI top-level handler doesn't regress silently. Add three parametrized assertions over all six exception classes: `str(exc_cls("")) == ""`, `str(exc_cls(None)) == "None"` (Python's `BaseException.__str__` of `None` arg), and `exc_cls("").args == ("",)`. Comment in the test cites D1 as the rationale. No production-code change.

- [x] [Review][Patch] **Shrink AST allowlist to `{"enum", "__future__"}`** [tests/unit/core/test_core_isolation.py:21] — Resolution of Decision D2 (a). Drop `"typing"` from `ALLOWED_TOPLEVEL_MODULES`. Closes the `if TYPE_CHECKING: from nova.adapters.* import ...` leak vector without adding additional AST-walking logic. If a future `core/exceptions.py` or `core/types.py` change genuinely needs `typing` (e.g., `Protocol`, `Literal`, `TYPE_CHECKING`), that contributor explicitly widens the allowlist *and* adds the corresponding `nova.adapters.*` forbidden-pattern guard at the same time.

### Deferred (2)

- [x] [Review][Defer] **`cause` is not preserved across `pickle` / `copy.deepcopy` round-trips** [src/nova/core/exceptions.py:55-57] — deferred, single-process architecture. `BaseException.__reduce__` only serializes `self.args`; `self.cause` is silently dropped on round-trip. Nova is single-process per architecture (no multiprocessing, no IPC); cause-loss across process boundaries cannot occur in T1. Document the limitation in the module docstring; revisit only if multiprocessing or remote workers ever land. Add to `deferred-work.md`.

- [x] [Review][Defer] **`inspect.getsourcefile()` returns `None` for namespace / zipped / frozen modules** [tests/unit/core/test_core_isolation.py:41-44] — deferred, packaging story. Test asserts non-None then `Path(...).read_text()`. Under `pyinstaller --onefile`, `zipapp`, or namespace-package layouts, the assertion crashes rather than reporting cleanly. Nova ships as `uv sync` + `uv run nova` per architecture (no packaging); the failure mode is unreachable in T1. If a packaging story ever lands, switch the test to `importlib.resources.files(module).joinpath(...).read_text()` or guard with `pytest.skip` when source is unavailable.

### Dismissed (3)

- AC #9 `# type: ignore[call-arg]` in `test_message_is_required_positional` — only way to exercise the missing-positional-arg `TypeError` path under mypy strict; documented in Completion Notes; AC #9 wording targets production code, not test-side suppressions.
- `from __future__ import annotations` redundant on Python 3.12 (Blind Hunter Low) — story AC #9 explicitly allows it ("use `from __future__ import annotations` if needed for Python 3.12 forward-compat"); kept defensively across all four new files for harmonization with test files that genuinely need it.
- Acceptance Auditor cosmetic note re: nested try/raise stylistic divergence in `test_from_clause_chains_via_dunder_cause` — captured as Patch P12 above (consolidated, not duplicated as a dismiss).

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- **RED phase** — wrote `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py` first. `uv run pytest tests/unit/core/ -x` failed at collection with `ModuleNotFoundError: No module named 'nova.core.exceptions'` — confirmed tests reference contracts that do not yet exist.
- **GREEN phase** — authored `src/nova/core/exceptions.py` (6 exception classes + chaining contract), `src/nova/core/types.py` (6 `StrEnum`s), updated `src/nova/core/__init__.py` with re-exports + `__all__`. First test run: `79 passed in 0.21s`.
- **Quality gate, first attempt** — ruff flagged one E501 in the `ApiUnavailableError` docstring header (line was 101 chars vs 100 limit). Trimmed wording to fit.
- **Quality gate, second attempt** — `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` → exit 0. Output: `All checks passed!` / `29 files already formatted` / `Success: no issues found in 29 source files` / `80 passed in 0.15s`.
- **`git status` post-verify** — modified: `_bmad-output/implementation-artifacts/sprint-status.yaml`, `src/nova/core/__init__.py`. Untracked: `_bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md`, `src/nova/core/exceptions.py`, `src/nova/core/types.py`, `tests/unit/core/`. No cache artifacts.

### Completion Notes List

- **All 10 ACs satisfied.** 79 new tests pass; 80 total in the project (scaffold smoke + the new core tests).
- **Constructor contract held precisely.** `NovaError.__init__(self, message: str, *, cause: BaseException | None = None) -> None`. `message` required positional; `cause` keyword-only and stored on `self.cause`. The `cause=` slot does NOT chain — that contract is documented in the module docstring, restated in the `NovaError` class docstring, and locked by `test_cause_kwarg_stores_but_does_not_chain` (parametrized over all six exception classes).
- **`BriefingState(Enum)` → `StrEnum` divergence from architecture.md §653 documented in the class docstring**, with the rationale (stable string serialization to YAML / SQLite / event bus). Story 1.2 owns the divergence as planned.
- **Adapter-isolation guardrail uses AST inspection, not raw text grep** (post-fix design). `test_core_isolation.py` walks `ast.Import` / `ast.ImportFrom` nodes against an allowlist `{"enum", "typing", "__future__"}` and a forbidden set covering `sqlite3`, `anthropic`, `pywin32`/`pywintypes`, `psutil`, `win32api`/`gui`/`com`/`con`, `rich`, `yaml`. Comments and docstrings mentioning adapter names are allowed — verified the pattern works because `exceptions.py` mentions `sqlite3.Error` in a docstring example without failing the test.
- **`BluntnessLevel` regression gate enforced.** `test_bluntness_level_has_exactly_two_members` asserts both `len(...) == 2` and `{"calm", "direct"}` membership. Adding `RUTHLESS` would fail two tests immediately.
- **Exact-membership tests added for every enum** beyond the round-trip tests. Stronger than the AC asked for; cheap to run, expensive to remove later. Defends against any silent addition or removal of members.
- **`from __future__ import annotations` added defensively to all four new files.** Python 3.12 doesn't strictly need it for `BaseException | None`, but it harmonizes with the test files (which need it for the `type[NovaError]` parameter annotations in pytest parametrize argvalues) and is allowlisted by the AST isolation test.
- **Scope held tight.** No event bus, no config loader, no tier state machine, no audit logger, no port classes. Each has its own story.
- **No new dependencies added** to `pyproject.toml`. Stdlib only (`enum`, `__future__`, plus `ast` / `inspect` / `pathlib` / `re` in the test files).
- **Carry-forward conventions applied:** absolute imports throughout, `__all__: list[str]` annotated explicitly for mypy strict, no `tests/__init__.py` (pytest goodpractices), no `print()` (T20 enforced), all test functions annotated `-> None` for mypy strict, `encoding="utf-8"` on file IO (Windows-neutral).
- **Test runtime budget held.** Combined runtime 0.15s for 80 tests including the AST source-file reads.
- **No `# type: ignore` added in production code.** Three `# type: ignore` directives exist in test files, all intentional and inline-justified: `[call-arg]` on `exc_cls()` for the missing-positional-arg `TypeError` path; `[arg-type]` on `exc_cls("msg", cause=bad_cause)` for the non-`BaseException` rejection path (P5); `[arg-type]` on `exc_cls(bad_message)` for the non-`str` rejection path (D1 reconciliation). All three exist solely to exercise runtime-validation paths that mypy strict would otherwise refuse to call; suppressions live on the test side, not on production code.
- **D1 reconciliation.** P13 originally pinned `message=None` as WAI, which conflicted with the declared `message: str` type hint and left the constructor's contract one-sided (cause was runtime-enforced; message was not). Reconciled by adding `isinstance(message, str)` at the top of `NovaError.__init__` — symmetric with the `cause` check. D1's intent (don't police message *content*) preserved: empty string `""` is still a `str` and still accepted as WAI. Only *type* is enforced; content remains the caller's responsibility (Story 1.10's CLI handler can validate non-empty if it wants). Test count rose 131 → 149 after the reconciliation.
- **Code review applied** (Sayuj's session, single-LLM). 14 patches landed: cause-self / cause-type guards (P4, P5), `__repr__` surfacing `cause` (P8), YAML docstring qualifier (P6), AST isolation hardening — relative imports / dynamic imports / private-symbol imports / allowlist shrink (P1, P2, P3, P14), `_toplevel_imports` rename to `_all_imports` (P9), pytest `ids` lambda fix (P10), guaranteed-invalid sentinel (P11), cross-enum `mode_switch` WAI guard (P7), chain test simplification (P12), `message=""` / `message=None` WAI tests (P13). Test count rose from 80 → 131. 2 items deferred (cause pickle round-trip; AST test under frozen/zipped deployments — Nova architecture is single-process and source-deployed, both unreachable in T1). 3 dismissed as noise.

### File List

- `src/nova/core/exceptions.py` (new) — 6 domain exception classes (`NovaError`, `StorageError`, `ConfigError`, `ApiUnavailableError`, `ModeNotFoundError`, `AdapterError`) with the `(message, *, cause=None)` constructor contract and the chaining/sensitivity rules documented in the module docstring and `NovaError` class docstring.
- `src/nova/core/types.py` (new) — 6 `StrEnum` classes (`CapabilityTier`, `BriefingState`, `SnapshotType`, `ActionType`, `MemoryCategory`, `BluntnessLevel`) with stable string values pinned by epics.md Story 1.2 ACs.
- `src/nova/core/__init__.py` (modified) — appended re-exports + alphabetized `__all__: list[str]`. Original one-line module docstring preserved.
- `tests/unit/core/test_exceptions.py` (new) — 36 parametrized tests covering subclass hierarchy, `str()` round-trip, required-message contract, `from`-style chaining, `cause=` introspection (no chaining), `NovaError` parent catch.
- `tests/unit/core/test_types.py` (new) — 39 tests: round-trip serialization for every member of every enum, name-pattern discipline, invalid-value rejection, exact-membership assertions, `BluntnessLevel` regression gate.
- `tests/unit/core/test_core_isolation.py` (new) — 4 AST-level tests asserting `core/exceptions.py` and `core/types.py` import only from `{enum, typing, __future__}` and never from any adapter module.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — `1-2-domain-exceptions-and-shared-types` moved `ready-for-dev` → `in-progress` → `review`.
- `_bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md` (modified) — task checkboxes, Dev Agent Record, File List, Change Log, Status.

### Change Log

| Date | Change |
|------|--------|
| 2026-04-14 | Story 1.2 implemented. Authored `src/nova/core/exceptions.py` (6 domain exceptions with `(message, *, cause=None)` contract, sensitivity rule documented), `src/nova/core/types.py` (6 `StrEnum` classes pinned by epics.md), updated `core/__init__.py` with re-exports + `__all__`. Added 79 tests across `test_exceptions.py`, `test_types.py`, `test_core_isolation.py` (AST-level adapter import guardrail). Full verify (ruff check + format + mypy strict + pytest) green; 80 total tests pass in 0.15s. Status → review. |
| 2026-04-14 | Reconciled `message: str` type hint with runtime — added `isinstance(message, str)` guard in `NovaError.__init__` symmetric with the `cause` runtime check (P5). D1 patch (P13) had pinned `message=None` as WAI, which conflicted with the declared type hint and left the contract one-sided. D1's "don't police *content*" intent preserved: `""` is still a `str` and remains accepted (`test_empty_string_message_pinned_as_wai`); only non-`str` types now reject. New `test_non_str_message_rejected_at_construction` parametrizes over `None`, `int`, `list`, `object()`. Tests: 131 → 149. Quality gate green. |
| 2026-04-14 | Addressed code review findings — 16 items resolved (2 decisions + 14 patches). `core/exceptions.py`: added `cause is self` guard, `isinstance(cause, BaseException)` runtime check, and `__repr__` that surfaces `cause` (P4, P5, P8). `core/types.py`: docstring qualified to state PyYAML lacks a `StrEnum` representer; YAML serialization is Story 1.6's writer responsibility (P6). `test_core_isolation.py`: rewritten to forbid relative imports (P1), forbid dynamic imports via `__import__`/`importlib.import_module` (P2), restrict `from enum import` to public symbols only (P3), rename `_toplevel_imports` → `_all_imports` (P9), shrink allowlist to `{"enum", "__future__"}` per D2 (P14). `test_types.py`: pytest `ids` lambda fixed to use `Enum` discriminator (P10), magic sentinel replaced with `_guaranteed_invalid_value()` helper (P11), cross-enum `mode_switch` WAI test added (P7). `test_exceptions.py`: chain test simplified to spec form (P12), new tests for cause-self rejection (P4), non-`BaseException` cause rejection (P5), `__repr__` format (P8), `message=""` and `message=None` WAI behavior pinned per D1 (P13). Re-verify clean: 131 tests pass in 0.26s; ruff + format + mypy strict all green. 2 items deferred to `deferred-work.md` (cause-pickle round-trip; AST test under frozen/zipped deployments). 3 items dismissed as noise. Status → done. |
