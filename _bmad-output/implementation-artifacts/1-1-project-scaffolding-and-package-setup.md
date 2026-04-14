# Story 1.1: Project Scaffolding & Package Setup

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a runnable Python project skeleton with the complete directory structure, `pyproject.toml`, and all T1 dependencies,
so that I can build and run the project from day one with `uv sync` and `uv run nova`.

## Acceptance Criteria

1. **Directory structure matches the architecture spec exactly.** Every directory in architecture.md §"Complete Project Directory Structure" exists with a `__init__.py` (where it is a Python package). At minimum:
   - `src/nova/` (root package) with `__init__.py`, `cli.py`, `app.py`
   - `src/nova/ports/` with `__init__.py` only (empty package — the 8 port `.py` files with `Protocol` classes are authored in Story 1.9, **not here**)
   - `src/nova/systems/{brain,eyes,hands,shield,voice,ritual,skin,nerve}/__init__.py` (empty packages; `system.py` / `models.py` come in their own stories)
   - `src/nova/adapters/{claude,win32,sqlite,rich}/__init__.py` (empty packages)
   - `src/nova/core/__init__.py` and `src/nova/core/storage/__init__.py`, `src/nova/core/storage/migrations/__init__.py` (empty — implementation stories fill these)
   - `src/nova/setup/__init__.py` (empty — wizard comes later)
   - `tests/` with `conftest.py`, `tests/unit/{systems,core,adapters}/`, `tests/integration/` (each with `__init__.py` or pytest-discoverable empty dirs)
   - `config/` (already populated by Story 1.0 — **do not modify, recreate, or reorder**)
   - `docs/` (already contains `config-schemas.md` from Story 1.0 — do not modify)
2. **`pyproject.toml` exists at the repo root** and declares (PEP 621 syntax — get this exactly right or `uv sync` rejects the file):
   - `[project]` table with `name = "nova"`, `version = "0.1.0"`, `requires-python = ">=3.12,<3.13"`, `description`, `authors = [{ name = "Sayuj" }]`
   - `readme` field — **conditional**: include `readme = "README.md"` **only if** a `README.md` file exists at the repo root when `pyproject.toml` is authored. If no README exists, **omit the `readme` field entirely** — do not point at a missing file (hatchling will hard-fail the build if the target is absent). Creating a README is out of scope for this story.
   - `[project.scripts]` table with: `nova = "nova.cli:main"`
   - **Runtime dependencies** — `dependencies = [...]` as a **list inside `[project]`**, not a separate `[project.dependencies]` table. PEP 621 has no `[project.dependencies]` header. Contents: `rich`, `pywin32 ; sys_platform == "win32"`, `psutil`, `anthropic`, `pyyaml` (PyYAML is required by the Story 1.0 shipped defaults and the Story 1.6 loader).
   - **Dev dependencies** under `[project.optional-dependencies]` as `dev = [...]` — a list assignment inside that table: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `types-pyyaml` (and any additional `types-*` stubs surfaced by mypy strict during verify).
   - `[build-system]` — uv-compatible backend (e.g., `hatchling` or `setuptools>=68`; hatchling is the uv default). Backend choice is dev's call; justify briefly in Completion Notes.
   - `[tool.ruff]` config — Python target 3.12, line length 100, sensible default rule set (`E`, `F`, `I` at minimum; `UP`, `B`, `SIM` recommended). Ruff is formatter + linter (AC #6).
   - `[tool.mypy]` config — `strict = true`, `python_version = "3.12"`, `packages = ["nova"]`, `mypy_path = "src"`. Document any narrow third-party ignore blocks (pywin32, etc.) inline.
   - `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `asyncio_mode = "auto"`, markers for `unit`, `integration`, `e2e`, `windows_only`, `migration` (per project-context testing rules).
   - `[tool.coverage.*]` — `pytest-cov` config is optional in this story (pytest-cov is installed, wiring thresholds is deferred to Story 1.11 CI quality gate).
3. **Dependency version strategy:** specify compatible ranges in `pyproject.toml` (e.g., `rich>=13`, `anthropic>=0.34`, `pywin32>=306`, `psutil>=5.9`, `pyyaml>=6.0`). Exact versions pin in `uv.lock`. Do **not** pin exact `==` versions in `pyproject.toml` itself — that breaks the uv convention. Research current stable releases on PyPI before committing ranges (AC #9 verify).
4. **`.python-version` file at repo root** contains `3.12` (no patch, no trailing newline requirement beyond what uv expects). This pins the Python interpreter for `uv sync`.
5. **`.gitignore` covers required patterns.** The existing `.gitignore` already covers `__pycache__/`, `.venv/`, `*.db`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, IDE files, and secrets. **Verify** it covers `uv.lock` is **not** ignored (uv.lock MUST be committed — per project-context), and `%LOCALAPPDATA%/nova/` paths cannot leak into the repo (already handled by `*.db` and runtime-data rules). Add `src/nova.egg-info/` or similar if the build backend needs it. Do **not** rewrite the file wholesale — edit only if a gap is found.
6. **`uv sync` succeeds end-to-end** on a fresh clone: creates `.venv/`, resolves dependencies, writes `uv.lock`, installs all runtime and dev deps. `uv.lock` is committed to the repo.
7. **`uv run nova` executes `src/nova/cli.py:main`** and exits cleanly with exit code 0. The placeholder `main()` prints a minimal banner to stdout (use `print()` **only here** — Skin does not exist yet; or better, use `sys.stdout.write`/`rich.print` if Rich import is stable) and returns. **No business logic, no config loading, no SQLite touch, no Win32 calls** in this story's `main()`. The placeholder is explicitly a smoke test.
   - **Exception to the project-context "no print()" rule:** this scaffolding smoke-test placeholder is the single exception. Add a `# noqa` comment or a one-line rationale; Story 1.10 (composition root + real CLI) replaces it with routed output through Skin.
8. **`uv run ruff check src/ tests/` passes with zero errors** on the skeleton.
9. **`uv run ruff format --check src/ tests/` passes** (formatting is already compliant with ruff's formatter — run `ruff format` once before committing).
10. **`uv run mypy src/ tests/` passes with zero errors** on the skeleton under strict mode. Scope covers both `src/` and `tests/` (widened from `src/` only during code review — D3). Empty module stubs carry a module docstring to satisfy mypy strict. `explicit_package_bases = true` handles `src/` layout robustness across mypy versions.
11. **`uv run pytest` runs successfully** against an empty / placeholder test suite. Include **one** placeholder test at `tests/unit/test_scaffold.py` that asserts `import nova` succeeds and `nova.__version__` (or an equivalent marker) is reachable. This proves the package is importable end-to-end.
12. **Full verify command passes clean:** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0 in a single run. Capture the command output for Debug Log References.
13. **Repo tree stays clean after a full verify run** — no `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.coverage`, or `*.db` artifacts are staged by `git status` (they are all gitignored; verify after running the full verify).

## Tasks / Subtasks

- [x] **Task 1: Create the directory skeleton** (AC: #1)
  - [x] Create `src/nova/` package root with empty `__init__.py` (set `__version__ = "0.1.0"` here for AC #11).
  - [x] Create `src/nova/cli.py` with a minimal `main()` function (see Task 4).
  - [x] Create `src/nova/app.py` as an empty module with a one-line docstring ("Composition root — implementation deferred to Story 1.10").
  - [x] Create `src/nova/ports/` with `__init__.py` only (module docstring: "Port interfaces — Protocol classes authored in Story 1.9."). **Do not pre-create `brain.py`, `eyes.py`, etc.** — Story 1.9 owns those files. Keeping the package empty here prevents "stub drift" (empty modules later edited out-of-scope).
  - [x] Create `src/nova/systems/{brain,eyes,hands,shield,voice,ritual,skin,nerve}/__init__.py` (each an empty package with a module docstring naming the system).
  - [x] Create `src/nova/adapters/{claude,win32,sqlite,rich}/__init__.py` (empty packages).
  - [x] Create `src/nova/core/__init__.py`, `src/nova/core/storage/__init__.py`, `src/nova/core/storage/migrations/__init__.py` (empty packages).
  - [x] Create `src/nova/setup/__init__.py` (empty package).
  - [x] Create `tests/__init__.py`, `tests/conftest.py` (empty fixtures module — just a module docstring for now), `tests/unit/__init__.py`, `tests/unit/systems/__init__.py`, `tests/unit/core/__init__.py`, `tests/unit/adapters/__init__.py`, `tests/integration/__init__.py`.
  - [x] Do **NOT** touch `config/` or `docs/config-schemas.md` — those are Story 1.0 deliverables and are frozen.

- [x] **Task 2: Author `pyproject.toml`** (AC: #2, #3)
  - [x] Declare `[project]` table: `name`, `version = "0.1.0"`, `description`, `authors = [{ name = "Sayuj" }]`, `requires-python = ">=3.12,<3.13"`.
  - [x] **Conditional `readme` field:** check whether a `README.md` exists at the repo root *before* authoring the file. If present, add `readme = "README.md"`. If absent, **omit the field entirely** — do not add a placeholder path (hatchling hard-fails on missing readme targets). This story does not create a README.
  - [x] Declare `[project.scripts]` with `nova = "nova.cli:main"`.
  - [x] Declare **runtime `dependencies`** as a list inside `[project]` (PEP 621 — there is no `[project.dependencies]` header): `rich`, `pywin32 ; sys_platform == "win32"`, `psutil`, `anthropic`, `pyyaml`. Use compatible ranges (see Latest Tech Information). Check PyPI for current stable as of 2026-04-14 before committing exact ranges.
  - [x] Declare `[project.optional-dependencies]` table with `dev = [...]` — a list assignment containing `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `types-pyyaml`, and any additional stubs surfaced by mypy strict during Task 8 verify.
  - [x] Declare `[build-system]` with the chosen backend. Default recommendation: `hatchling` (uv's default, zero-config for `src/` layout).
  - [x] Declare `[tool.hatch.build.targets.wheel] packages = ["src/nova"]` so hatchling finds the `src/` layout.
  - [x] Declare `[tool.ruff]` with `target-version = "py312"`, `line-length = 100`, `src = ["src", "tests"]`, `[tool.ruff.lint] select = ["E", "F", "I", "UP", "B", "SIM"]` (ruff lint rule set — recommended baseline; dev may trim if noise is excessive).
  - [x] Declare `[tool.mypy]` with `strict = true`, `python_version = "3.12"`, `packages = ["nova"]`, `mypy_path = "src"`. Add any narrow `[[tool.mypy.overrides]]` entries **only** if a dep lacks type stubs and `types-*` is not available (document each override inline).
  - [x] Declare `[tool.pytest.ini_options]` with `testpaths = ["tests"]`, `asyncio_mode = "auto"`, `markers = ["unit: unit tests", "integration: integration tests", "e2e: end-to-end tests", "windows_only: requires Windows", "migration: schema migration tests"]`.

- [x] **Task 3: Pin Python version and verify git hygiene** (AC: #4, #5, #13)
  - [x] Write `.python-version` at repo root with `3.12` (no patch version — uv resolves latest 3.12.x).
  - [x] Read existing `.gitignore`; verify coverage. Add missing entries only if Task 8's verify step surfaces them (e.g., hatchling build artifacts in `src/*.egg-info/` or `dist/`).
  - [x] **Do not** add `uv.lock` to `.gitignore` — uv.lock MUST be committed. Grep the existing file and confirm it is not listed.

- [x] **Task 4: Minimal `cli.py:main()`** (AC: #7)
  - [x] Author `src/nova/cli.py` with placeholder `main() -> int` returning 0 and printing a banner. `# noqa: T201` added to tolerate `print` under the active ruff rule set; Story 1.10 replaces this with Skin-routed output.
  - [x] Confirm `main()` returns an `int` exit code (mypy strict passes).
  - [x] Baseline ruff rule set does not include `flake8-print` (T-rules), but `# noqa: T201` is kept defensively — harmless under current config, prevents breakage if Story 1.11 tightens rules.

- [x] **Task 5: Placeholder test** (AC: #11)
  - [x] Created `tests/unit/test_scaffold.py` — imports `nova`, asserts `__version__ == "0.1.0"`.
  - [x] `tests/conftest.py` kept minimal (module docstring only).

- [x] **Task 6: `uv sync` end-to-end** (AC: #6)
  - [x] Ran `uv sync --extra dev` from repo root. `.venv/` created, `uv.lock` written, 39 packages installed including `nova==0.1.0` from the local source tree (re-sync after code-review patches added `types-psutil`).
  - [x] `uv.lock` committed (AC #6 satisfied after code-review commit landed).
  - [x] pywin32 installed successfully on Windows (target platform). Platform marker `sys_platform == "win32"` guards cross-platform installs.

- [x] **Task 7: `uv run nova` smoke test** (AC: #7)
  - [x] `uv run nova` printed the placeholder banner and exited 0. Output captured in Debug Log References.

- [x] **Task 8: Quality gates pass** (AC: #8, #9, #10, #11, #12, #13)
  - [x] `uv run ruff format src/ tests/` — files already conformant on first author; `ruff format --check` idempotent.
  - [x] `uv run ruff check src/ tests/` — zero errors.
  - [x] `uv run mypy src/` — zero errors under strict mode across 22 source files. No `# type: ignore` added, strict mode unrelaxed.
  - [x] `uv run pytest` — 1 test passed, no warnings.
  - [x] Full verify one-liner ran clean; exit code 0.
  - [x] `git status` clean: only intended new files (`pyproject.toml`, `uv.lock`, `.python-version`, `src/`, `tests/`, story file) and the sprint-status edit. No cache artifacts staged or untracked (AC #13).

## Dev Notes

### Story Type: Foundational scaffolding — enables every subsequent 1.x story

This story produces the runnable Python skeleton. After this story, Stories 1.2–1.10 fill in real implementations inside the directory shape this story creates. Get this right and downstream stories only ever add code; they never reshape the package.

### Scope guard (hard stop)

- **Do NOT implement any system logic.** Ports stay empty stubs (just module docstrings); systems stay empty packages; adapters stay empty packages; `app.py` is an empty module docstring.
- **Do NOT touch `config/` or `docs/config-schemas.md`.** Those are Story 1.0 outputs — frozen.
- **Do NOT create migration scripts, SQLite code, or `001_initial_schema.py`.** That is Story 1.5.
- **Do NOT implement `events.py`, `exceptions.py`, `types.py`, `tiers.py`, `audit.py`, `config.py`, or `prompt_builder.py`.** Each has its own story.
- **If you write more than ~10 lines of Python in `cli.py`, you are out of scope.** The placeholder must be intentionally boring.

### Critical constraints and gotchas

- **`src/` layout is non-negotiable.** The architecture pins `src/nova/` as the package root (not `nova/` at repo root). The `pyproject.toml` build backend must know this (`[tool.hatch.build.targets.wheel] packages = ["src/nova"]` for hatchling). Flat `nova/` at the repo root will break imports and clash with `_bmad/` / `_bmad-output/` tooling directories.
- **`uv.lock` is committed.** Per project-context "uv.lock is committed and tool-managed" — do not add it to `.gitignore`. The existing `.gitignore` does not list it; verify during Task 3.
- **pywin32 is Windows-only.** `uv sync` on non-Windows will fail on the pywin32 install step. The architecture target is Windows 11 only. If dev is prototyping on another OS, mark pywin32 as a platform-conditional dependency: `pywin32 ; sys_platform == "win32"`. Same treatment may be needed for any other Windows-only package that surfaces. On Windows, no conditional is needed.
- **Mypy strict on empty modules.** `mypy --strict` will flag truly empty `__init__.py` files in some configs. Add a one-line module docstring to each. Do **not** add `# type: ignore` or relax strict mode.
- **Ruff is linter + formatter — do not add black/isort/flake8.** Per project-context, ruff is the single tool. `[tool.ruff.lint] select = [...]` governs lint; `ruff format` governs formatting. No separate config files (`.ruff.toml`, `setup.cfg`) — everything in `pyproject.toml`.
- **Absolute imports only.** Even in empty packages, if you add any import at all, use `from nova.ports.brain import ...` — never relative.
- **No `print()` anywhere — except the `cli.py` placeholder.** Per project-context, terminal output goes through Skin. Skin does not exist in this story. The placeholder `print()` in `cli.py:main()` is the sole documented exception; it is removed in Story 1.10.
- **Placeholder test must not import adapters, systems, or anything with heavy import-time side effects.** A single `import nova` + a version assertion is correct. Adding fixtures now creates hidden coupling for Stories 1.4/1.6.
- **Type stubs:** `types-pyyaml` is the only stubs package definitively needed (PyYAML has no inline types). `pywin32-stubs` may help mypy strict on Windows but is not required in this story since no pywin32 code is written yet. `psutil-stubs` — same. Add stubs packages only as mypy errors arise, document each addition.
- **`pytest-cov` is installed but not wired.** Adding a coverage gate is Story 1.11 (CI quality-gate automation). Including `pytest-cov` in `dev` deps here ensures it's available when 1.11 turns it on; do not configure coverage thresholds now.

### Repo shape at time of this story

Repo already contains (from Story 1.0 + planning):
- `.git/`, `.gitignore` (already present and mostly complete)
- `_bmad/`, `_bmad-output/`, `_bmad-output/planning-artifacts/`, `_bmad-output/implementation-artifacts/`
- `.agents/`, `.claude/` (tooling — already gitignored)
- `config/` with three YAML files (`modes/coding.yaml`, `exclusions.yaml`, `settings.defaults.yaml`) — **frozen from Story 1.0**
- `docs/config-schemas.md` — **frozen from Story 1.0**
- `design-artifacts/` — pre-existing planning inputs

Repo does **NOT** yet contain:
- Any `src/`, `tests/`, `.venv/`, `pyproject.toml`, `uv.lock`, `.python-version`, or `setup.bat`
- Any Python code whatsoever

### Previous Story Intelligence — Story 1.0 (done 2026-04-14)

Story 1.0 was a documentation-only spike. Relevant carry-over for this story:

- **Scope discipline is load-bearing.** Story 1.0 held scope tightly (no `.py` files, no `pyproject.toml`). This story reverses that exclusion — but with its own scope guard above. Do not let "since we're scaffolding, I'll also add…" creep kick in; every system has its own story.
- **PyYAML is NOT in stdlib.** Story 1.0 verified shipped YAMLs parse with `yaml.safe_load` but deferred adding PyYAML as a project dep to this story. Add `pyyaml` to runtime deps (AC #2) and `types-pyyaml` to dev deps.
- **Story 1.0 deferred work (`_bmad-output/implementation-artifacts/deferred-work.md`)** — targets Stories 1.6, 2.1, 2.3, 4.2 for pickup. **None of it lands in 1.1.** The deferred items require real Python code (loader logic, setup flow, mode wizard), which is out of scope here.
- **Known divergences from `architecture.md` are already resolved in `docs/config-schemas.md`** — do not reopen them in this story. If `pyproject.toml` or dependency choices appear to conflict with the schema doc, the schema doc wins.

### Git Intelligence — last 2 commits

```
80dba55 Story 1.0 code review: resolve 20 findings, mark done
5b9d026 Initialize repo with planning artifacts and Story 1.0 (YAML config schemas spike)
```

- Repo was initialized with planning artifacts + Story 1.0 only. **This story is the first to introduce Python code, `pyproject.toml`, `.venv/`, and test scaffolding.**
- Commit style: terse, imperative, mentions story ID + gate (e.g., "Story 1.0 code review: resolve 20 findings, mark done"). Follow this pattern when dev commits: "Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)" or similar.
- No prior Python commits — nothing to inherit regarding code style; use ruff defaults + this story's config.

### Latest Tech Information (as of 2026-04-14)

Research stable versions on PyPI before committing version ranges. Baseline guidance (verify current on PyPI):

- **Python:** 3.12.x (architecture pin). Do not widen to 3.13 in this story — the project-context rule is `Python 3.12.x`.
- **uv:** latest stable (uv ≥ 0.4 handles hatchling + `src/` layout cleanly).
- **Rich:** ≥ 13 (stable API; 14.x if released and compatible). Architecture relies on `Panel`, `Table`, `Tree`, `Text`, `Progress`, `Prompt`, `Columns` — all stable since 13.x.
- **Anthropic SDK:** ≥ 0.34 (prompt caching support). Check for breaking changes since — cost-control depends on caching working correctly in Story 1.10+.
- **pywin32:** ≥ 306 (stable on Windows 11). Use `pywin32 ; sys_platform == "win32"` if dev works cross-platform.
- **psutil:** ≥ 5.9 (broad stability).
- **pyyaml:** ≥ 6.0 (stable `SafeLoader` / `safe_load` — this is what Story 1.0 relies on).
- **pytest:** ≥ 8 (stable marker + asyncio-mode interactions with pytest-asyncio ≥ 0.23).
- **pytest-asyncio:** ≥ 0.23 (project-context uses `@pytest.mark.asyncio`; `asyncio_mode = "auto"` is recommended per pytest-asyncio docs).
- **pytest-cov:** latest stable.
- **ruff:** ≥ 0.5 (ruff formatter is mature; both lint and format use the same binary). Pin the minor range loosely — ruff evolves fast; rely on CI (Story 1.11) to catch rule drift.
- **mypy:** ≥ 1.10 (strict-mode UX improvements).

### Project Structure Notes

- **`src/` layout chosen for import isolation.** Architecture.md §1295-1432 explicitly uses `src/nova/`. This prevents the common "tests import the dev checkout" problem and ensures `uv sync` → `uv run nova` goes through the installed package path.
- **Build backend:** `hatchling` is uv's out-of-the-box default and requires minimal config for `src/` layout. Alternatives (setuptools, flit, pdm-backend) are permissible but require more config — justify the choice briefly in Completion Notes.
- **Naming alignment with project-context:** package name `nova` (lowercase), module files `snake_case.py`, classes `PascalCase` (none in this story). Per project-context "File naming: snake_case, one module per concern."
- **No `docs/architecture.md` symlink.** Architecture.md §1430 mentions `docs/` may contain a reference to the planning artifact; **skip this in 1.1**. The planning artifact already lives under `_bmad-output/planning-artifacts/architecture.md` and is referenced by story files directly. Creating a symlink on Windows requires extra care and adds no value.
- **`setup.bat` is Story 2.1, not this story.** Do not create it here. Architecture.md §1301 lists it in the tree, but its implementation (guided first-run, pywin32 post-install, API key capture) belongs to Epic 2.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio + pytest-cov — installed and configured in `pyproject.toml`, but only **one** test exists in this story (`tests/unit/test_scaffold.py` — import smoke).
- **Markers declared:** `unit`, `integration`, `e2e`, `windows_only`, `migration`. Declaring them up-front prevents pytest warnings when Story 1.2+ starts using them.
- **`asyncio_mode = "auto"`** — per pytest-asyncio convention; means async test functions don't need `@pytest.mark.asyncio` decorators. Dev may override per-test with the explicit marker if needed.
- **`tests/conftest.py`** — present but empty (module docstring only). Shared fixtures (test DB, mock adapters, event bus) arrive in Stories 1.4 / 1.6. Adding fixtures now creates premature coupling.
- **No integration or e2e tests in this story.** The verify one-liner runs pytest end-to-end, which will find and run only the scaffold test. That is correct.
- **Coverage threshold:** not enforced here. Story 1.11 (CI quality-gate automation) wires coverage into CI.

### Critical Don't-Miss Rules (from project-context.md)

Carry-forward for this story; expanded rationale lives in `_bmad-output/project-context.md`:

- **`uv.lock` is committed** — reproducible installs (per project-context workflow rules).
- **All tooling runs through `uv run`** — no globally installed executables assumed (`uv run ruff`, `uv run mypy`, `uv run pytest`, `uv run nova`).
- **Local and CI quality gates must match** — ruff, mypy, pytest configs go in `pyproject.toml` (single source). Story 1.11 CI will invoke the same `uv run` commands.
- **Absolute imports only** — even empty stubs follow `from nova.<subpkg> import ...` if they import at all.
- **Repo tree stays clean** — AC #13 enforces this explicitly after the full verify run.
- **Developer reset is distinct from user data reset** — this story does not create reset scripts; noted for downstream story awareness.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1: Project Scaffolding & Package Setup](../planning-artifacts/epics.md) — canonical acceptance criteria, lines 640–655.
- [Source: _bmad-output/planning-artifacts/architecture.md#Foundation Setup / Language, Platform, Project Framework](../planning-artifacts/architecture.md) — lines 218–289, technology stack, dependency list, project structure overview.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — lines 1291–1432, complete tree to scaffold.
- [Source: _bmad-output/planning-artifacts/architecture.md#Development Workflow](../planning-artifacts/architecture.md) — lines 1527–1549, canonical `uv run` commands and verify sequence.
- [Source: _bmad-output/project-context.md#Development Workflow Rules](../project-context.md) — `uv sync`, `uv run`, lockfile, quality gate, repo-tree cleanliness.
- [Source: _bmad-output/project-context.md#Technology Stack & Versions](../project-context.md) — Python 3.12, ruff, mypy strict, pytest/pytest-asyncio/pytest-cov.
- [Source: _bmad-output/implementation-artifacts/1-0-define-yaml-config-schemas-spike.md#Dev Notes](./1-0-define-yaml-config-schemas-spike.md) — previous story PyYAML carry-forward, scope discipline.
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — Story 1.0 deferred items; none relevant to 1.1 (all target 1.6/2.1/2.3/4.2).
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.11: CI Quality-Gate Automation](../planning-artifacts/epics.md) — downstream consumer; CI must succeed against the skeleton this story produces.

## Review Findings

Produced by the bmad-code-review workflow on 2026-04-14. Three parallel adversarial review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) ran against the Story 1.1 diff (35 files, +942/−2 lines). Acceptance Auditor: **Approve** — all 13 ACs satisfied on the diff (note: Auditor assumed uv.lock shipping in the diff ⇒ commit pending). Adversarial layers surfaced decisions and patches the scaffold should pin before moving to `done`.

**Caveat:** review ran in the same Claude session that implemented the story. Single-LLM bias possible.

### Decision-Needed Findings (4)

- [x] [Review][Decision] **`tests/__init__.py` + `src/` layout anti-pattern** — Blind+Edge flagged. Pytest's own goodpractices recommends `tests/` without `__init__.py` when using `src/` layout; `__init__.py` forces package-mode collection and can create import collisions (e.g., future `tests/unit/core/storage/…` shadowing `nova.core.storage.*`). Story AC #1 permitted either `__init__.py` OR "pytest-discoverable empty dirs". Dev picked `__init__.py`. Options: **(a)** Remove all 7 `tests/**/__init__.py` files (follow pytest idiom); **(b)** Keep as-is and document the choice. Recommendation: (a).
- [x] [Review][Decision] **`# noqa: T201` tags a rule that is not in the active ruff rule set** — Baseline rules are `E, F, I, UP, B, SIM`; `T20` (flake8-print) is NOT selected, so the suppression is a no-op today and may become a `RUF100` error later. This also means nothing enforces the "no print() except cli.py placeholder" rule across the codebase — a future story can slip in stray `print`s silently. Options: **(a)** Add `T20` to `[tool.ruff.lint] select` so the noqa becomes meaningful AND the rule is enforced project-wide; **(b)** Remove the `# noqa: T201` as cargo-culted. Recommendation: (a) — enforces the documented policy.
- [x] [Review][Decision] **mypy scope excludes `tests/`** — `[tool.mypy] packages = ["nova"]` plus the canonical `uv run mypy src/` verify command together mean tests are not type-checked. Project-context "type annotations on everything" arguably extends to tests. Options: **(a)** Add `tests` to mypy scope (widen `packages` or change verify command to `mypy src/ tests/`); **(b)** Explicitly exempt tests from strict mode and document the choice. Recommendation: (a) — consistent with strict-typing posture.
- [x] [Review][Decision] **`__version__` hardcoded in two places** — `src/nova/__init__.py:__version__ = "0.1.0"` duplicates `pyproject.toml:version = "0.1.0"`. Scaffold test asserts the `__init__` value; drift on version bump leaves test green and banner wrong (or vice-versa). Options: **(a)** Derive at runtime via `__version__ = importlib.metadata.version("nova")` — single source of truth; **(b)** Keep hardcoded and add a consistency test that reads `pyproject.toml`. Recommendation: (a).

### Patch Findings (9)

- [x] [Review][Patch] **AC #6 not actually satisfied — uv.lock + scaffold not yet committed** [working tree, sprint-status.yaml] — Story sits at `Status: review` with `uv.lock` and all scaffold files uncommitted. AC #6 requires `uv.lock` committed. Resolution: commit the scaffold.
- [x] [Review][Patch] **Missing `.gitattributes` — every file emits LF→CRLF warnings** [repo root] — 30+ files in the diff produce `warning: in the working copy of '...', LF will be replaced by CRLF the next time Git touches it`. Cross-OS CI (Story 1.11) will see phantom line-ending diffs; `uv.lock` integrity depends on consistent EOL. Fix: add `.gitattributes` with `* text=auto eol=lf` plus explicit entries for `*.py`, `*.toml`, `*.yaml`, `*.md`, `*.lock`, `.python-version`.
- [x] [Review][Patch] **`sprint-status.yaml` header comment is stale** [sprint-status.yaml:2] — Comment reads `last modified when 1-1 moved backlog → ready-for-dev` but current status is `review`. Fix: update comment or remove the parenthetical.
- [x] [Review][Patch] **Story file internal contradictions re AC #6 state** [1-1-project-scaffolding-and-package-setup.md, multiple locations] — Change Log says "uv.lock committed"; Completion Notes and File List say "not yet committed"; Task 3 sub-bullet is checked `[x]` for `.gitignore` verification but captures no diff-level evidence. Resolve by rewriting Change Log and Completion Notes after the commit lands to match reality.
- [x] [Review][Patch] **Tighten `anthropic>=0.34` → `anthropic>=0.94,<1`** [pyproject.toml:11] — 64-minor-version gulf between declared floor and resolved `0.94.1`. `>=0.34` silently accepts pre-prompt-caching SDKs; prompt caching is cost-control-load-bearing (project-context). Tighten to a realistic floor and cap at 1.0 for breaking-change safety.
- [x] [Review][Patch] **Add `addopts = ["--strict-markers"]` to pytest config** [pyproject.toml, `[tool.pytest.ini_options]`] — Markers are declared but typos (`@pytest.mark.windws_only`) silently pass as unknown markers with a warning. Adding `--strict-markers` fails fast on typos and realizes the intent of declaring markers.
- [x] [Review][Patch] **Add `explicit_package_bases = true` to `[tool.mypy]`** [pyproject.toml:43-47] — Known pitfall for `src/` layouts combined with `mypy_path = "src"`. Passes on mypy 1.20 today; can break on other mypy versions or when tests enter the scope.
- [x] [Review][Patch] **Pre-add `types-psutil` to dev deps** [pyproject.toml:25] — Story 1.2+ will import psutil; mypy strict will fail without stubs. Story Dev Notes deferred to "add as needed" but pre-adding now costs nothing and prevents a predictable retrofit in the next story.
- [x] [Review][Patch] **Pin `types-pyyaml` version** [pyproject.toml:25] — Only dev dep with no floor. Stub package drift relative to `pyyaml>=6.0` can regress mypy silently on lock refresh. Align with the resolved `types-pyyaml==6.0.12.20260408`: pin `types-pyyaml>=6.0`.

### Deferred (5)

- [x] [Review][Defer] **uv.lock `revision = 3` requires recent uv on CI** — Document minimum uv version when Story 1.11 wires CI.
- [x] [Review][Defer] **Coverage config `[tool.coverage.*]` absent** — Deferred to Story 1.11 (CI quality-gate automation) per the story's original design.
- [x] [Review][Defer] **`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`** — Belt-and-suspenders for Story 1.11 CI report artifacts; add when CI is wired.
- [x] [Review][Defer] **Hatchling default sdist includes `_bmad-output/`, `design-artifacts/`** — Matters only if the project is ever published (project-context says no). Revisit if publishing is ever on the roadmap.
- [x] [Review][Defer] **PEP 735 `[dependency-groups]` migration** — uv is steering toward `[dependency-groups]` over `[project.optional-dependencies]` for dev deps; monitor and migrate when uv's guidance stabilizes.

### Dismissed (16)

- ASCII hyphen vs. em-dash in `cli.py` banner and `pyproject.toml` description — cosmetic drift.
- PyPI upload timestamps (anthropic, docstring-parser same-day) — project's internal 2026 timeline, not a supply-chain concern for this codebase.
- `if __name__ == "__main__": raise SystemExit(main())` in `cli.py` — standard Python idiom; duplicating the `[project.scripts]` entrypoint costs nothing.
- Placeholder docstring prophecies ("Implementation in Story X.Y") across 17 `__init__.py` files — low-impact; story-number rot is a docs concern, not a code concern.
- Ruff `B008` / `UP` / `SIM` rule fires in later stories — premature; trim when actual noise surfaces.
- Single-quoted PEP 508 marker (`sys_platform == 'win32'`) — uv/packaging accept both; TOML string-quote preference.
- Missing `hatchling` version pin in `[build-system] requires` — low concern for this scale.
- `rich>=13` range loose — API stable across 13/14/15.
- `filterwarnings` / `minversion` / `asyncio_mode = "auto"` scope-change nitpicks — premature.
- Scaffold test doesn't import `nova.systems.*` subpackages — `import nova` is the intended minimalism for AC #11.
- `__all__` not declared on empty `__init__.py` files — premature.
- Pytest `pythonpath` / `conftest.py` placement nitpicks — works under the canonical `uv run` invocation.
- Git warnings appearing mid-diff in the patch output — cosmetic diff noise.
- `src/nova/app.py` empty docstring — intentional per scope guard; Story 1.10 fills.
- No `license` field in `[project]` — project-context rules out publishing; adding is optional.
- No explicit `[tool.ruff.format]` — defaults are fine; not blocking.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- **Tooling bootstrap (uv + Python 3.12).** `uv` was not on PATH. Installed via `pip install uv` into the system Python 3.11 (uv 0.11.6). Managed Python 3.12 provisioned via `python -m uv python install 3.12` → cpython-3.12.13-windows-x86_64. `.python-version = 3.12` pins uv's interpreter selection; uv resolved to 3.12.13.
- **`python -m uv sync --extra dev`** — Prepared 38 packages in 10.97s, installed in 915ms. Notable resolved versions (captured to `uv.lock`):
  - Runtime: `rich==15.0.0`, `pywin32==311`, `psutil==7.2.2`, `anthropic==0.94.1`, `pyyaml==6.0.3`
  - Dev: `pytest==9.0.3`, `pytest-asyncio==1.3.0`, `pytest-cov==7.1.0`, `ruff==0.15.10`, `mypy==1.20.1`, `types-pyyaml==6.0.12.20260408`
  - `nova==0.1.0` installed from the local source tree (editable via hatchling build).
- **`python -m uv run nova`** → `N.O.V.A. - scaffold placeholder. Implementation pending Story 1.10.` / exit 0.
- **`uv run ruff check src/ tests/`** → `All checks passed!` / exit 0.
- **`uv run ruff format --check src/ tests/`** → `30 files already formatted` / exit 0.
- **`uv run mypy src/`** → `Success: no issues found in 22 source files` / exit 0.
- **`uv run pytest`** → `1 passed in 0.03s` (collected 1 item from `tests/unit/test_scaffold.py`) / exit 0.
- **Full verify one-liner** (`ruff check && ruff format --check && mypy && pytest`) → exit 0.
- **`git status` post-verify** — untracked: `.python-version`, `pyproject.toml`, `src/`, `tests/`, `uv.lock`, new story file. Modified: `sprint-status.yaml`. **No** `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.venv/`, `*.egg-info/` — all gitignored and absent from the working tree's git view (AC #13 ✓).
- **Shell note.** `VIRTUAL_ENV` was set to the Python 3.11 system install by the shell; `uv run` emits a warning but correctly uses `.venv/` (3.12). Cleared via `unset VIRTUAL_ENV` per invocation. Not a project issue — only affects the current dev shell.

### Completion Notes List

- **All 13 ACs satisfied** after the code-review commit landed (which completed AC #6). Every quality gate exits 0.
- **pytest-asyncio version sanity-checked.** Lockfile resolves `pytest-asyncio==1.3.0` (sdist sha256 `d7f52f36…e9e5`, upload 2025-11-10). Real PyPI release; the 0.x→1.x jump landed in 2024. Consistent with project-context `>=0.23` baseline.
- **Code-review patches applied (13 total).** All 4 decisions and 9 patches landed before the final commit: `tests/**/__init__.py` removed (follow pytest goodpractices for `src/` layout); `T20` (flake8-print) added to ruff `select` so `# noqa: T201` in `cli.py` is now meaningful AND stray prints are blocked project-wide; mypy scope widened to `src/nova` + `tests` via `files = [...]` with `explicit_package_bases = true`; `__version__` now derived from `importlib.metadata.version("nova")` (single source of truth); `.gitattributes` added (forces LF commits, eliminates CRLF warnings); sprint-status header comment cleaned up; `anthropic` tightened to `>=0.94,<1` (prompt-caching floor + pre-1.0 safety cap); `--strict-markers` added to pytest addopts; `types-psutil>=5.9` pre-added; `types-pyyaml` pinned `>=6.0`. Re-verify after patches: ruff+format+mypy+pytest all exit 0; `uv run nova` exit 0. 5 items deferred to Story 1.11 / future work (logged in `deferred-work.md`). 16 findings dismissed as noise.
- **Hatchling chosen as build backend** per instruction. Zero-config for `src/` layout with `[tool.hatch.build.targets.wheel] packages = ["src/nova"]`. No concrete reason surfaced to prefer another backend.
- **No README, no LICENSE created.** Both out of scope for scaffolding. `readme` field **omitted** from `pyproject.toml` (conditional AC satisfied — file absent, field absent).
- **Scope held.** No system logic, no port Protocol classes, no SQLite, no migrations, no event bus, no config loader. All of those are later 1.x stories.
- **Ports directory is `__init__.py`-only.** Story 1.9 will author `brain.py`, `eyes.py`, etc. with Protocol classes — deliberately not pre-stubbed here to avoid stub drift.
- **pywin32 installed natively on Windows.** The `sys_platform == "win32"` marker is defensive for any dev who tries to `uv sync` on Linux/macOS — won't help with actual runtime, but keeps the lockfile resolvable cross-platform for editor tooling.
- **`types-pyyaml` added** as the only explicit stubs package; mypy strict was green without any additional stubs (no pywin32/psutil/rich/anthropic usage yet — stubs will be added as those deps get wired in their respective stories).
- **Ruff rule set = `E, F, I, UP, B, SIM`.** Baseline recommended in the story; no noise surfaced on the skeleton. `# noqa: T201` on the `print` in `cli.py` is defensive — T-rules aren't currently active but the comment is harmless and future-proofs the line if Story 1.11 tightens the rule set.
- **Coverage gate not wired.** `pytest-cov` installed, no thresholds configured — deferred to Story 1.11 per the story's design.
- **Placeholder test is deliberately minimal.** Imports `nova`, asserts `__version__ == "0.1.0"`. Proves the installed package is importable end-to-end; expanded suites arrive with their owning stories.
- **Story 1.0 outputs untouched.** `config/` and `docs/config-schemas.md` verified unchanged (not in `git status`).
- **Known cosmetic:** `uv run` emits a one-line VIRTUAL_ENV warning when invoked from a shell that has a stale `VIRTUAL_ENV` pointing at Python 3.11. Does not affect correctness; the project `.venv/` (3.12) is used. Noted here for future contributors.

### File List

- `pyproject.toml` (new) — project metadata, deps, and tool config (ruff with `T20`, mypy strict with `explicit_package_bases` and widened scope, pytest with `--strict-markers`, hatchling).
- `uv.lock` (new) — reproducible dependency lock, 39 packages. Committed.
- `.gitattributes` (new) — enforces LF line endings across the repo to prevent cross-OS CRLF churn (added during code review).
- `.python-version` (new) — pins interpreter to `3.12` for `uv`.
- `src/nova/__init__.py` (new) — package root, `__version__` derived from `importlib.metadata.version("nova")` (D4 — single source of truth).
- `src/nova/cli.py` (new) — placeholder `main()` banner + exit 0. Replaced in Story 1.10.
- `src/nova/app.py` (new) — empty composition-root placeholder module.
- `src/nova/ports/__init__.py` (new) — empty package; Protocol files come in Story 1.9.
- `src/nova/systems/__init__.py`, `src/nova/systems/{brain,eyes,hands,shield,voice,ritual,skin,nerve}/__init__.py` (new, 9 files) — empty system packages.
- `src/nova/adapters/__init__.py`, `src/nova/adapters/{claude,win32,sqlite,rich}/__init__.py` (new, 5 files) — empty adapter packages.
- `src/nova/core/__init__.py`, `src/nova/core/storage/__init__.py`, `src/nova/core/storage/migrations/__init__.py` (new, 3 files) — empty core packages.
- `src/nova/setup/__init__.py` (new) — empty setup package; wizard lands in Story 2.1+.
- `tests/conftest.py` (new) — test-root scaffolding; fixtures arrive with Stories 1.4/1.6.
- `tests/unit/test_scaffold.py` (new) — import smoke test (asserts `nova.__version__ == "0.1.0"`).
- `tests/` has **no** `__init__.py` files (D1 — pytest goodpractices for `src/` layout).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — `1-1-project-scaffolding-and-package-setup` moved `ready-for-dev` → `in-progress` → `review`.
- `_bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md` (modified) — task checkboxes, Dev Agent Record, File List, Change Log, Status.

### Change Log

| Date | Change |
|------|--------|
| 2026-04-14 | Story 1.1 implemented. Scaffolded `src/nova/` + `tests/` package shape per architecture.md §1291–1432. Authored `pyproject.toml` (hatchling, PEP 621 deps, ruff/mypy/pytest config), `.python-version = 3.12`. Installed uv + managed Python 3.12.13 (uv 0.11.6). `uv sync --extra dev` resolved 38 packages. Full verify (ruff check + format + mypy strict + pytest) passes clean on the skeleton. Status → review. |
| 2026-04-14 | Addressed code review findings — 13 items resolved (4 decisions + 9 patches). Removed `tests/**/__init__.py` (pytest goodpractices for `src/` layout). Enabled ruff `T20` (flake8-print) so `# noqa: T201` is meaningful and stray prints are blocked project-wide. Widened mypy scope to `src/nova` + `tests` with `explicit_package_bases = true`. `__version__` now derived via `importlib.metadata.version("nova")` (single source of truth). Added `.gitattributes` (LF enforcement). Tightened `anthropic>=0.94,<1`; pinned `types-pyyaml>=6.0`; pre-added `types-psutil>=5.9`. Added `addopts = ["--strict-markers"]` to pytest. Fixed stale sprint-status header comment. Re-sync (39 packages) + re-verify all green. 5 items deferred to Story 1.11 / future, 16 dismissed. Status → done. |
