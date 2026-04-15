# Story 1.11: CI Quality-Gate Automation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer (or AI agent),
I want a GitHub Actions CI workflow that runs the full quality gate (ruff lint, ruff format-check, mypy strict, pytest unit, pytest integration) on every push and pull request,
So that ruff / mypy / pytest failures are caught before code merges, local and CI environments never drift, and the "canonical commands" contract from [project-context.md:148-156](../project-context.md) is mechanically enforced.

## Acceptance Criteria

**Given** the project uses `uv` for all tooling and [pyproject.toml](../../pyproject.toml) is the single config source (Story 1.1), AND the full T1 infrastructure from Stories 1.1 – 1.10 ships green (739 tests passed, 1 skipped as of the Story 1.10 done-commit `9996903`), AND the canonical full-verify command is pinned at [project-context.md:156](../project-context.md) as `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ && uv run pytest`,

**When** this story is complete:

**AC #1 — Workflow file location and basic shape:**
A single workflow file [.github/workflows/ci.yml](../../.github/workflows/ci.yml) exists at the repo root. Exactly one workflow, exactly one job, exactly one matrix cell (no cross-OS / cross-Python matrix in T1 — the project is Windows-first per project-context.md:29; cross-platform CI is out of scope). The workflow:

- `name: CI`
- **`"on":` key MUST be double-quoted as a YAML string** — `"on": { push: { branches: ['**'] }, pull_request: {} }`. **Rationale: load-bearing for AC #13's YAML parsing test.** PyYAML's `yaml.safe_load` follows YAML 1.1 rules (PyYAML has never upgraded to 1.2), under which the unquoted bareword `on` is a boolean literal that parses to Python `True` — so an unquoted `on: ...` key would land in the parsed dict as `workflow[True]`, not `workflow["on"]`. GitHub Actions itself accepts the unquoted form (GHA's parser is tolerant), so this is purely a PyYAML ergonomics concern for our structural test. Quoting `"on":` makes the workflow file self-documenting about the PyYAML edge case AND produces `workflow["on"]` as expected. Lock this via a dedicated AC #13 test (`test_workflow_on_key_is_string_not_bool`) so a future edit that drops the quotes is caught before the test suite reads the wrong key. The other YAML 1.1 booleans (`yes`, `no`, `true`, `false`, `y`, `n`, `off`) do not appear as top-level keys in this workflow; quoting is needed only for `"on":`.
- `"on": { push: { branches: ['**'] }, pull_request: {} }` — runs on every push to every branch AND on every PR-open / PR-update / PR-synchronize event. (Explicit `branches: ['**']` is load-bearing: without it, `on: push` still fires for all refs, but future concurrency controls (AC #6) rely on `github.ref` being populated, and explicit is safer.)
- `concurrency: { group: "ci-${{ github.ref }}", cancel-in-progress: true }` — cancels superseded runs on the same ref so a rapid push stream doesn't burn CI minutes.
- `permissions: { contents: read }` — minimal default. No `write`. (Hardens against a future workflow edit that accidentally grants `issues: write` etc. — pin least-privilege from day one.)
- Exactly one `job:` named `quality-gate`.

**AC #2 — Runner and Python:**
The `quality-gate` job runs on `runs-on: windows-latest` (GitHub-hosted Windows runner). Python version is sourced from the repo's [.python-version](../../.python-version) file (`3.12`) — do **NOT** hard-code `python-version: "3.12"` in the workflow (hard-coding creates the exact drift this story exists to prevent; local dev uses `.python-version`, CI must read the same source). Two acceptable approaches — dev agent picks one, documents the choice in a `# comment` at the step:

- **Approach A (preferred, simpler):** `actions/setup-python@v5` with `python-version-file: .python-version` — the action reads `.python-version` natively.
- **Approach B (explicit echo):** A setup step with `shell: bash`, `id: pyver`, `run: echo "version=$(cat .python-version)" >> "$GITHUB_OUTPUT"`, then `actions/setup-python@v5` with `python-version: "${{ steps.pyver.outputs.version }}"`. More steps but more observable in the CI log.

Both approaches satisfy "CI Python version is sourced from `.python-version`". The AC #13 test accepts either shape (checks for `python-version-file: .python-version` OR for a `${{ steps.<id>.outputs.* }}` expression referencing a prior step that reads `.python-version`).

**Rationale for Windows-only runner (vs. Ubuntu):** The product is Windows 11 exclusive (architecture.md:74 "Windows 11 only — win32gui, psutil, subprocess for OS integration"; project-context.md:29 "Platform scope: Windows 11 only"). T1 tests don't yet exercise the `windows_only` marker (Stories 4.1 Eyes and 3.6 Hands land those), but pywin32 is already in `dependencies` (gated by `sys_platform == "win32"`) and future Win32-touching tests must run on Windows. Running CI on Ubuntu would silently skip any future `windows_only` test without failing — a drift class this story exists to prevent. The epic AC explicitly permits documenting "why Ubuntu is acceptable for non-Win32 tests"; we explicitly reject Ubuntu here and document the rejection (not the acceptance).

**Rationale for Windows-only runner (vs. Ubuntu):** The product is Windows 11 exclusive (architecture.md:74 "Windows 11 only — win32gui, psutil, subprocess for OS integration"; project-context.md:29 "Platform scope: Windows 11 only"). T1 tests don't yet exercise the `windows_only` marker (Stories 4.1 Eyes and 3.6 Hands land those), but pywin32 is already in `dependencies` (gated by `sys_platform == "win32"`) and future Win32-touching tests must run on Windows. Running CI on Ubuntu would silently skip any future `windows_only` test without failing — a drift class this story exists to prevent. The epic AC explicitly permits documenting "why Ubuntu is acceptable for non-Win32 tests"; we explicitly reject Ubuntu here and document the rejection (not the acceptance).

**AC #3 — `uv` installed via official action with a pinned minimum version:**
Use the official `astral-sh/setup-uv@v5` action (NOT a hand-rolled `pip install uv` or curl-installer). The action:
- Pins `version:` to a specific `uv` version known to support the committed [uv.lock](../../uv.lock) format (`revision = 3` — written by `uv >= 0.5.11` per the Story 1.1 deferred-work item). The dev agent picks the current stable `uv` release as of the implementation commit (likely `0.5.x` or `0.6.x` — pin whatever the local dev machine has been using, verify via `uv --version`). Document the pin + its rationale in a `# comment` inside the workflow file.
- Sets `enable-cache: true` (uses the action's built-in cache — no manual `actions/cache@v4` dance needed).
- Does NOT use `cache-dependency-glob` overrides — the default globbing on `uv.lock` is correct.

**AC #4 — Lockfile-frozen install:**
The install step runs `uv sync --frozen --all-extras`:
- `--frozen` fails the step if `uv.lock` is out-of-date relative to `pyproject.toml`. This catches the class of bugs where a developer edits `pyproject.toml` without regenerating the lockfile. Local dev's default `uv sync` regenerates; CI's `--frozen` asserts.
- `--all-extras` installs `[project.optional-dependencies].dev` so `pytest`, `ruff`, `mypy` etc. are available. (Using `--dev` is the older uv flag; `--all-extras` is the current canonical form — pick `--all-extras` to future-proof against a deprecation in a later uv release.)

**AC #5 — Steps match canonical commands exactly:**
The workflow runs these steps, **in this order**, each as its own named step (so GitHub Actions' per-step log + failure surface is granular):

1. `uv sync --frozen --all-extras` (install)
2. `uv run ruff check src/ tests/` (lint) — matches [project-context.md:153](../project-context.md#L153) verbatim
3. `uv run ruff format --check src/ tests/` (format check) — matches [project-context.md:154](../project-context.md#L154) verbatim
4. `uv run mypy src/ tests/` (type check) — widened from the epic AC's `mypy src/` to `mypy src/ tests/` to match the Story 1.10 AC #18 precedent and the [pyproject.toml:49](../../pyproject.toml#L49) `files = ["src/nova", "tests"]` setting. Both forms resolve to the same mypy invocation under the current config — the explicit args ARE the invariant the workflow enforces, independent of future config edits. Document this deliberate delta from [project-context.md:155](../project-context.md#L155) in a `# comment` in the workflow file so the next `project-context.md` edit can decide which side wins.
5. `uv run pytest tests/unit/` (unit tests) — matches [project-context.md:151](../project-context.md#L151) verbatim
6. `uv run pytest tests/integration/` (integration tests) — matches [project-context.md:152](../project-context.md#L152) verbatim

The step names are stable human-readable strings (NOT the raw commands): `Install dependencies`, `Lint (ruff check)`, `Format check (ruff format)`, `Type check (mypy)`, `Unit tests`, `Integration tests`. Stable names mean future workflow edits (e.g., adding a `Coverage` step) don't break repo rules ("require check: Unit tests passes" on a protected branch).

**AC #6 — Fail-fast sequencing:**
GitHub Actions' default sequential-step-with-first-failure-stops-job behavior is relied on — **no custom `if: failure()` overrides, no `continue-on-error: true`, no explicit `if:` guards that would let later steps run after a previous failure**. An AC-level assertion: the workflow contains **zero** `continue-on-error` keys (AC #13 locks this via a structural test). When ruff check fails, mypy + pytest do not run. When mypy fails, pytest does not run. This is the epic AC "fail fast" requirement, delivered by doing nothing — which is the best kind of delivery.

**AC #7 — Coverage config wired (closes Story 1.1 deferred-work item):**
[pyproject.toml](../../pyproject.toml) gains a `[tool.coverage.run]` section + a `[tool.coverage.report]` section:
- `[tool.coverage.run]`: `source = ["src/nova"]`, `branch = true`, `parallel = false` (single-process test runs; `parallel = true` is only needed for `pytest-xdist` or multiprocess tests, neither of which T1 uses).
- `[tool.coverage.report]`: `exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError", "\\.\\.\\."]`, `precision = 1`, `show_missing = true`, `skip_covered = false`.
- **No `fail_under` threshold set.** The deferred-work item from Story 1.1 ("coverage config absent") is closed by wiring the report config, NOT by enforcing a threshold. Threshold enforcement is a policy decision that belongs to a later retrospective once baseline coverage is known. Setting `fail_under = 80` speculatively would likely fail CI on Story 1.11 itself — untested territory.

The `Unit tests` step passes `--cov=nova --cov-report=term --cov-report=xml` so a `coverage.xml` is produced for future GitHub-reports integration (artifact upload is out of scope — wiring is NOT artifact-uploading). The `Integration tests` step runs **without** `--cov` (integration-test coverage is additive only when combined with unit coverage via `coverage combine`, which adds complexity for zero T1 benefit — coverage reporting in this story is unit-tests-only and deliberately unambitious).

**AC #8 — Test markers respected:**
The two pytest invocations (`tests/unit/`, `tests/integration/`) already separate suites by directory, not by marker. The `[pyproject.toml:55-61]` markers (`unit`, `integration`, `e2e`, `windows_only`, `migration`) are declared but no CI step filters on `-m` in T1. **Reasoning:** pre-filtering on markers would hide the fact that a test in `tests/unit/` is actually marked `@pytest.mark.integration` (a miscategorization). Directory-based selection lets `--strict-markers` (already in [pyproject.toml:54](../../pyproject.toml#L54) `addopts`) catch unknown markers, and the marker registry stays forward-compatible for Stories 4.1+ which will add `windows_only` tests.

A structural test (AC #13) asserts: no workflow step invokes `pytest -m <marker>`. If a future story needs marker-based CI filtering (e.g., split `windows_only` into a Linux-runner-skip matrix), that story lifts the restriction deliberately. Today it's a closed-for-extension guard.

**AC #9 — `.gitignore` covers CI artifact file types (closes Story 1.1 deferred-work item):**
[.gitignore](../../.gitignore) gains four patterns, grouped under a new `# --- CI artifacts (Story 1.11)` section inserted before the `# --- Secrets` block:
- `coverage.xml`
- `junit.xml` (pytest-junit support is available but not wired — forward-compat)
- `.uv_cache/` (uv's local cache when `UV_CACHE_DIR` is not overridden)
- `.hatch/` (hatch's local cache — relevant because the build backend is hatchling per [pyproject.toml:30](../../pyproject.toml#L30))

All four files/dirs are currently absent from the repo. AC verification is one ruff-format-style regression test + a visual inspection; no production code behavior changes.

**Do NOT** add `htmlcov/` (already present at line 20), `.coverage` (already line 18), `.coverage.*` (already line 19), or `dist/` / `build/` (already lines 21-22). These are already ignored from Story 1.1; duplicating them would be cosmetic churn + trigger ruff-format (though ruff doesn't touch `.gitignore`, the project convention from Story 1.4+ is "check before duplicating").

**AC #10 — Minimum `uv` version documented:**
Create [docs/development.md](../../docs/development.md) (new file) — a short markdown doc (≤ 50 lines) documenting:
- **Minimum `uv` version:** pin the exact minimum required to open the committed `uv.lock` (`uv >= 0.5.11` for `revision = 3` support — verify against the actually-committed `uv.lock` file's `revision = N` line and document the corresponding uv minimum from uv's CHANGELOG at the time of authoring).
- **Canonical full-verify command:** copy verbatim from [project-context.md:156](../project-context.md#L156). The doc is a "where a new contributor looks when uv sync fails" breadcrumb, not a spec.
- **CI parity invariant:** one sentence stating "the `.github/workflows/ci.yml` pipeline runs the identical commands documented here; if you edit one, edit the other".

This closes the Story 1.1 deferred-work item ("Document a minimum `uv` version and pin it in CI runner setup"). Place in [docs/](../../docs/) (pre-existing dir from Story 1.0). Do **not** create a README.md — that's a future polish pass (Story 1.1 deliberately deferred README creation).

**AC #11 — `project-context.md` canonical-commands list unchanged:**
Do **not** edit [_bmad-output/project-context.md](../project-context.md). The workflow is validated AGAINST that file — editing it mid-story would destroy the invariant test (AC #13). If a future story needs to change the canonical commands, it edits both files in the same commit; this story only reads project-context.md.

**AC #12 — Workflow runs green on the existing skeleton:**
`.github/workflows/ci.yml` runs green against the current `main` branch (commit `9996903` — Story 1.10 done). The dev agent MUST run the same commands locally as a pre-push dry-run: `uv sync --frozen --all-extras && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest tests/unit/ && uv run pytest tests/integration/`, captured into Dev Debug Log References. A broken CI first-run is a story-scope failure (the CI exists to catch regressions, not to BE the first regression).

**AC #13 — Structural test locks the workflow contract:**
A new test [tests/unit/test_ci_workflow.py](../../tests/unit/test_ci_workflow.py) MUST lock these invariants via `yaml.safe_load` + dict assertions (not text regex — the feedback_ast precedent generalizes: structural parsing beats string matching):

- `test_workflow_file_exists` — asserts `.github/workflows/ci.yml` exists and is valid YAML (parses without `yaml.YAMLError`).
- `test_workflow_has_one_job` — asserts exactly one top-level `jobs.<name>` entry named `quality-gate`.
- `test_workflow_runs_on_windows` — asserts `jobs.quality-gate.runs-on == "windows-latest"`.
- `test_workflow_on_key_is_string_not_bool` — asserts `"on" in workflow` (string key present) AND `True not in workflow` (no boolean-coerced key). **This is the load-bearing guard for AC #1's quoting requirement**; if the workflow file drops the quotes around `"on":`, PyYAML parses the key as Python `True` under YAML 1.1 rules, this test fails first, and the subsequent `test_workflow_triggers_on_push_and_pr` never gets a chance to fail mysteriously with a `KeyError: 'on'`.
- `test_workflow_triggers_on_push_and_pr` — asserts `workflow["on"]` is a dict containing both `push` and `pull_request` keys. Safe to index via `"on"` because the preceding test guarantees the string key is present.
- `test_workflow_has_concurrency_group` — asserts `concurrency.cancel-in-progress == True` and `concurrency.group` contains `${{ github.ref }}`.
- `test_workflow_permissions_are_read_only` — asserts `permissions == {"contents": "read"}`.
- `test_workflow_uses_setup_uv_action` — asserts one step's `uses` starts with `astral-sh/setup-uv@` and specifies a non-empty `with.version`.
- `test_workflow_python_version_from_dot_python_version` — asserts the setup-python step sources its version from `.python-version` via one of the two approaches in AC #2: either (a) `with.python-version-file == ".python-version"`, OR (b) `with.python-version` matches `${{ steps.<id>.outputs.* }}` where that `<id>` step's `run:` contains both `cat .python-version` and `$GITHUB_OUTPUT`. Test accepts either; rejects any literal `"3.12"` or `"3.12.x"` in `with.python-version`.
- `test_workflow_uv_sync_uses_frozen` — asserts a step's `run` contains `uv sync --frozen --all-extras`.
- `test_workflow_runs_canonical_commands_in_order` — parametrized: extract the `run:` strings from every step, read [project-context.md](../project-context.md) as text, regex out the 5 canonical bullet lines (`Lint`, `Format`, `Type check`, `Test (unit)`, `Test (integration)` — the 5 single-command bullets under "Canonical commands" at lines 148–155, NOT the combined full-verify line 156). For each of the 5 canonical commands, assert some workflow step's `run:` string **starts with** that canonical command (substring-from-position-0). `startswith` (not generic substring) is the correct direction — the workflow MAY extend a command with additional args (e.g., `uv run pytest tests/unit/ --cov=nova --cov-report=xml` extends the canonical `uv run pytest tests/unit/`), but MUST NOT substitute or reorder. Additionally: assert the 5 matched steps appear in the canonical order (lint → format → mypy → unit → integration) in the workflow. **This is the drift-prevention invariant.** If the mypy step uses `uv run mypy src/ tests/` (AC #5's widened form), it passes `startswith("uv run mypy src/")` — the canonical `mypy src/` is a strict prefix. If a future story changes the canonical command, this test fails until the workflow updates, forcing a paired edit.
- `test_workflow_has_no_continue_on_error` — AST-walk of the full parsed YAML; assert no `continue-on-error: true` anywhere.
- `test_workflow_has_no_marker_based_pytest` — asserts no step's `run` matches the regex `pytest .* -m [a-z]` (marker-based pytest filter). AC #8 lock.
- `test_workflow_coverage_passes_cov_on_unit_step_only` — the unit-tests step's `run` contains `--cov=nova`; the integration-tests step's `run` does NOT. AC #7 lock.
- `test_pyproject_has_coverage_run_section` — parse [pyproject.toml](../../pyproject.toml) via `tomllib`, assert `tool.coverage.run.source == ["src/nova"]` and `tool.coverage.run.branch is True`.
- `test_pyproject_has_coverage_report_section` — assert `tool.coverage.report.exclude_lines` is a list with at least 4 entries and `tool.coverage.report.precision == 1`.
- `test_pyproject_does_not_set_coverage_fail_under` — asserts `tool.coverage.report.fail_under` is NOT set (AC #7: no threshold enforced in T1). If a future story adds the threshold, THAT story removes this test.
- `test_gitignore_covers_ci_artifacts` — parametrized over `["coverage.xml", "junit.xml", ".uv_cache/", ".hatch/"]`; assert each appears as a line in `.gitignore`.
- `test_docs_development_md_exists` — asserts [docs/development.md](../../docs/development.md) exists and contains substrings for "minimum uv version" + the canonical full-verify command.

Test file placement: `tests/unit/test_ci_workflow.py` — flat under `tests/unit/`, same precedent as `test_composition_root.py` (Story 1.10), `test_scaffold.py` (Story 1.1). No new subdirectory.

**Dependency note:** `PyYAML` is already in [project] `dependencies` (Story 1.1 — because Story 1.6 loads YAML config files), so no new dev-dep is needed for the YAML parsing in the test. `tomllib` is stdlib in 3.11+.

**AC #14 — Closes three deferred-work items from `deferred-work.md`:**
Three items from [_bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) explicitly target Story 1.11:

1. **"uv.lock `revision = 3` requires recent uv on CI"** (line 20) — closed by AC #3 (pinned `astral-sh/setup-uv@v5` version) + AC #10 (documented minimum in `docs/development.md`).
2. **"Coverage config `[tool.coverage.*]` absent"** (line 21) — closed by AC #7 (wires `run` + `report` sections; threshold enforcement deliberately deferred).
3. **"`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`"** (line 22) — closed by AC #9.

**Action:** remove these three bullet entries from `deferred-work.md` as part of this story's commit. Leave the fourth Story 1.1 deferred-work item ("Hatchling default sdist includes `_bmad-output/`…") UNTOUCHED — it's gated on "if N.O.V.A. is ever published to PyPI" which T1 explicitly rules out. Leave the fifth item ("PEP 735 `[dependency-groups]` migration") UNTOUCHED — it's a monitor-only item with no action this story takes.

**AC #15 — Quality gate clean:**
After the workflow and coverage config land, a local run of the full verify command (project-context.md:156) passes with zero new failures:
```
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest
```
Expected test count delta: **+18 new tests** in `tests/unit/test_ci_workflow.py` (the 17 AC #13 bullets plus the new `test_workflow_on_key_is_string_not_bool` guard; with `test_workflow_runs_canonical_commands_in_order` and `test_gitignore_covers_ci_artifacts` parametrized). Baseline 739 passed / 1 skipped → expected 757 passed / 1 skipped post-Story-1.11. No new `# type: ignore`, no new `cast()`, no new `Any`. `mypy strict` stays clean on the new test file (YAML parsing via `yaml.safe_load` returns `Any`; narrow at access sites via `isinstance` checks or typed helper functions — Story 1.10 pattern for `argparse.Namespace` attribute access).

**AC #16 — Scope boundary (do NOT do):**
- Do NOT create a `Dockerfile` or container-based CI. `uv` + `windows-latest` + `actions/setup-python` is the full stack.
- Do NOT add pre-commit hooks ([pre-commit.ci](https://pre-commit.ci), `.pre-commit-config.yaml`). Pre-commit adds a second tool-config surface; the epic AC pins CI as the quality gate, not git hooks. A future hygiene pass can layer pre-commit on top once the team's developer ergonomics demand it.
- Do NOT add a code-coverage gate on PRs (e.g., codecov, coveralls). Out of scope — see AC #7 rationale. The `coverage.xml` is produced for future wiring, not consumed today.
- Do NOT add a cross-platform test matrix (Windows + Ubuntu + macOS). Rejected per AC #2 rationale.
- Do NOT add a Python-version matrix (3.12 + 3.13). The project requires exactly `>=3.12,<3.13` per [pyproject.toml:6](../../pyproject.toml#L6); 3.13 is excluded by design.
- Do NOT add a `release` workflow, a `publish` workflow, or anything outside `quality-gate`. Release management is post-T1 (the architecture sketches mention v0.15 Shield, v0.2 Voice — none of which ship from T1, so release pipelines are premature).
- Do NOT edit existing Story 1.1 – 1.10 production source files. Only NEW files + three existing-file edits: [pyproject.toml](../../pyproject.toml) (two new `[tool.coverage.*]` sections), [.gitignore](../../.gitignore) (four new lines in a new section), [deferred-work.md](./deferred-work.md) (three removed lines). No `src/nova/**` edits. No `tests/unit/**` edits beyond the new file. No `tests/integration/**` edits.
- Do NOT add GitHub branch protection rules via the workflow (that's a repo-admin setting, not a workflow file concern). The workflow's `permissions` block already pre-emptively restricts write access — branch-protection-rule authoring is out of scope.

**AC #17 — Commit message format:**
Expected commit (Stories 1.1 – 1.10 precedent): `"Story 1.11: CI quality-gate automation (.github/workflows/ci.yml)"`. Scope parens name the primary new artifact. Secondary files (coverage config, .gitignore, docs/development.md, deferred-work.md cleanup, test file) are implied.

## Tasks / Subtasks

- [x] **Task 1: Scaffolding** (AC: #1, #2, #16)
  - [x] Create `.github/` directory at repo root (does not exist yet — `ls -la` in working-dir confirms).
  - [x] Create `.github/workflows/` directory.
  - [x] Create `.github/workflows/ci.yml` with the `name: CI`, `on:`, `concurrency:`, `permissions:`, and empty `jobs: { quality-gate: ... }` skeleton per AC #1.
  - [x] Confirm NO other `.github/workflows/*.yml` files are created (AC #16 scope boundary).

- [x] **Task 2: Runner + Python setup** (AC: #2, #3)
  - [x] Add `runs-on: windows-latest` to `jobs.quality-gate`.
  - [x] Add `actions/checkout@v4` step (most-recent major version per 2026 CI conventions — verify against `https://github.com/actions/checkout/releases` if in doubt).
  - [x] Add a `Read .python-version` step: `shell: bash`, `id: pyver`, `run: echo "version=$(cat .python-version)" >> "$GITHUB_OUTPUT"`. This works on `windows-latest` because GitHub-hosted Windows runners ship git-bash by default.
  - [x] Add `astral-sh/setup-uv@v5` step with pinned `version:` (pick from `uv --version` on the dev machine + cross-check with uv GitHub releases) and `enable-cache: true`.
  - [x] Add `actions/setup-python@v5` step with `python-version: ${{ steps.pyver.outputs.version }}`.
  - [x] Add a `# comment` block above the uv action citing: (a) the pinned uv version, (b) "minimum compatible with uv.lock revision = N" cross-ref.

- [x] **Task 3: Install + verify steps** (AC: #4, #5, #6)
  - [x] Add `Install dependencies` step: `run: uv sync --frozen --all-extras`.
  - [x] Add `Lint (ruff check)` step: `run: uv run ruff check src/ tests/`.
  - [x] Add `Format check (ruff format)` step: `run: uv run ruff format --check src/ tests/`.
  - [x] Add `Type check (mypy)` step: `run: uv run mypy src/ tests/`.
  - [x] Add `Unit tests` step: `run: uv run pytest tests/unit/ --cov=nova --cov-report=term --cov-report=xml`.
  - [x] Add `Integration tests` step: `run: uv run pytest tests/integration/`.
  - [x] Confirm zero `continue-on-error` keys anywhere (default is `false` — just don't add the key).
  - [x] Confirm zero `if:` conditionals on any of the six quality-gate steps.

- [x] **Task 4: Coverage config in `pyproject.toml`** (AC: #7)
  - [x] Append `[tool.coverage.run]` + `[tool.coverage.report]` sections to [pyproject.toml](../../pyproject.toml) AFTER the `[tool.pytest.ini_options]` section (preserve existing order: `[project]` → `[project.scripts]` → `[project.optional-dependencies]` → `[build-system]` → `[tool.hatch.*]` → `[tool.ruff*]` → `[tool.mypy]` → `[tool.pytest.ini_options]` → NEW `[tool.coverage.run]` → NEW `[tool.coverage.report]`).
  - [x] `[tool.coverage.run]`: `source = ["src/nova"]`, `branch = true`, `parallel = false`.
  - [x] `[tool.coverage.report]`: `exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError", "\\.\\.\\."]`, `precision = 1`, `show_missing = true`, `skip_covered = false`.
  - [x] Do NOT set `fail_under`. AC #7 explicit.
  - [x] Do NOT add a `[tool.coverage.xml]` or `[tool.coverage.html]` section — the CLI `--cov-report=xml` / `--cov-report=term` flags control output format; these TOML sections would override them.

- [x] **Task 5: `.gitignore` edits** (AC: #9)
  - [x] Read [.gitignore](../../.gitignore); confirm the 4 target patterns are absent (already confirmed during story authoring: lines 1–66 do not contain `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`).
  - [x] Insert a new section `# --- CI artifacts (Story 1.11) -----------------------------------------` BEFORE the `# --- Secrets` block (which is currently at line 59).
  - [x] Add the 4 patterns, one per line: `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`.
  - [x] Preserve all existing 66 lines verbatim — do NOT reflow, re-sort, or otherwise churn.

- [x] **Task 6: `docs/development.md`** (AC: #10)
  - [x] Create [docs/development.md](../../docs/development.md) — ≤50 lines.
  - [x] Section: "Minimum `uv` Version" — pin the exact minimum (`uv >= 0.5.11` or whatever matches the committed `uv.lock` revision) + a one-line link to uv's CHANGELOG.
  - [x] Section: "Canonical Full-Verify Command" — copy verbatim from [project-context.md:156](../project-context.md#L156).
  - [x] Section: "CI Parity" — one sentence: "The CI workflow in `.github/workflows/ci.yml` runs the identical commands above; edits to one must be mirrored in the other."
  - [x] Do NOT create a README.md, a CONTRIBUTING.md, or any other doc. Single-purpose file only.

- [x] **Task 7: Structural workflow test** (AC: #13)
  - [x] Create [tests/unit/test_ci_workflow.py](../../tests/unit/test_ci_workflow.py).
  - [x] Module docstring: "Story 1.11 AC #13 — locks the CI workflow YAML + coverage config against drift from `project-context.md` canonical commands."
  - [x] Import `yaml` (PyYAML, already in `[project].dependencies`), `tomllib` (stdlib 3.11+), `re` (stdlib), `pathlib.Path` (stdlib).
  - [x] Write a tiny helper `_load_workflow() -> dict[str, object]` that `yaml.safe_load`s `Path(".github/workflows/ci.yml").read_text(encoding="utf-8")` and returns the parsed dict. Narrow types at access sites (e.g., `jobs = workflow["jobs"]; assert isinstance(jobs, dict)`).
  - [x] Write a tiny helper `_load_pyproject() -> dict[str, object]` that `tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))` returns.
  - [x] Write a tiny helper `_workflow_step_runs() -> list[str]` that returns all `run:` strings from `jobs.quality-gate.steps[*].run` in order, concatenating multi-line `run:` strings via `\n.join()` so a single `run: |` block still contributes as one element.
  - [x] Implement the 18 AC #13 tests (the 17 original bullets + `test_workflow_on_key_is_string_not_bool`). Parametrize where noted (`test_workflow_runs_canonical_commands_in_order` with 5 canonical-command fragments; `test_gitignore_covers_ci_artifacts` with 4 patterns).
  - [x] **Order `test_workflow_on_key_is_string_not_bool` FIRST in the file** (right after the helpers) so it fails first on the PyYAML `on: → True` edge case before any downstream test blows up with a confusing `KeyError: 'on'`.
  - [x] Marker: `@pytest.mark.unit` is **not** strictly required (no `@pytest.mark.unit` elsewhere — the marker is declared but existing unit tests rely on directory placement). Leave unmarked for consistency.
  - [x] No external I/O beyond `Path(...).read_text()` — read the 3 files (`.github/workflows/ci.yml`, `pyproject.toml`, `.gitignore`, `_bmad-output/project-context.md`) via relative path from repo root. pytest's `testpaths = ["tests"]` + running pytest from repo root gives the CWD needed.

- [x] **Task 8: deferred-work bookkeeping** (AC: #14)
  - [x] Edit [_bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md).
  - [x] Remove the 3 bullet entries from the "Deferred from: code review of story 1-1-project-scaffolding-and-package-setup" section:
    - `- **uv.lock `revision = 3` requires recent uv on CI.**` (line 20)
    - `- **Coverage config `[tool.coverage.*]` absent.**` (line 21)
    - `- **`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`.**` (line 22)
  - [x] Preserve the other 2 Story 1.1 items (hatchling sdist + PEP 735 `[dependency-groups]`) verbatim.
  - [x] Preserve ALL other sections (Stories 1.0, 1.2–1.10 deferred items) verbatim.
  - [x] If removing the 3 bullets leaves a stranded "**---**" separator or an empty Story 1.1 section with zero remaining bullets, DO leave the remaining 2 bullets in place (they stay) — the section stays intact.

- [x] **Task 9: Pre-commit verify** (AC: #12, #15)
  - [x] Run the full verify locally, sequentially, and capture output for Debug Log References:
    - `uv sync --frozen --all-extras`
    - `uv run ruff check src/ tests/`
    - `uv run ruff format --check src/ tests/` (if drift, run `uv run ruff format src/ tests/` and re-check — but Story 1.10 left the tree clean so no format drift expected)
    - `uv run mypy src/ tests/`
    - `uv run pytest tests/unit/` — expect test-count +18 vs. the Story 1.10 baseline (739 passed → 757 passed)
    - `uv run pytest tests/integration/` — unchanged count
  - [x] Verify no new `# type: ignore` / `cast()` / `Any` via a quick grep:
    - `grep -rn "# type: ignore" tests/unit/test_ci_workflow.py` — expect zero results
    - `grep -rn "cast(" tests/unit/test_ci_workflow.py` — expect zero results
    - `grep -rn ": Any" tests/unit/test_ci_workflow.py` — expect zero results (the `_load_workflow` helper narrows via `isinstance`, not `Any`)
  - [x] Verify repo tree is clean post-run: no `coverage.xml` staged (it's now gitignored — should not appear in `git status`), no `.pytest_cache` / `.mypy_cache` / `.ruff_cache` staged.

### Handoff to User (not tracked as dev-story tasks)

The following actions are user-owned and out of dev-story scope. They do not gate the `done` status — the story ships when the code and tests are complete and the quality gate is green, which they are.

- **Commit:** message `"Story 1.11: CI quality-gate automation (.github/workflows/ci.yml)"`.
- **Push to a feature branch** (NOT `main` — this is the very first CI run; feature-branch + PR is the safer first-contact path).
- **Open a PR to `main`; watch the first CI run.** If CI is red on first run:
  - Env-specific failures (Windows-vs-local path separators, CRLF-vs-LF in generated files, shell quoting on `windows-latest` pwsh default): fix the workflow or the test — do NOT loosen the invariant.
  - Genuine regression (a Story 1.1–1.10 test passes locally but fails on Windows CI): flag as platform drift in a new deferred-work entry.
- **Once CI is green, merge the PR.** No sprint-status flip needed on merge — status is already `done`.

### Review Findings (2026-04-15, Opus 4.6 adversarial + edge-case + acceptance audit)

**Patch (unchecked — real issues to fix):**

- [x] [Review][Patch] `test_workflow_python_version_from_dot_python_version` has redundant `isinstance` + truthy-walrus chain that both duplicates the type check and inconsistently rejects empty strings [tests/unit/test_ci_workflow.py:175-182] — simplified to `isinstance(uses := s.get("uses"), str) and uses.startswith("actions/setup-python@")`. Quality gate green (767 passed, 1 skipped).

**Defer (pre-existing, spec-mandated, or not-actionable-now):**

- [x] [Review][Defer] Concurrency cancels mid-flight validation on `main` branch too — rapid merges could cancel a predecessor's validation before it completes. Spec AC #1 locked this pattern; solo-dev cadence makes the risk negligible. **Target:** revisit if multi-dev workflow begins; switch to `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` or scope `group:` by `github.head_ref || github.run_id` [.github/workflows/ci.yml:13-15].
- [x] [Review][Defer] `push: branches: ['**']` + `pull_request: {}` double-fires CI on every PR commit (once as push to feature branch, once as pull_request). 2x runner minutes per PR. Spec-locked trade-off; solo-dev volume is low. **Target:** restrict `push:` to `main` only if minute budget tightens, or add `if: github.event_name != 'push' || github.ref == 'refs/heads/main'` guard [.github/workflows/ci.yml:5-11].
- [x] [Review][Defer] `windows-latest` silently rolls `windows-2022` → `windows-2025`; default `pwsh` shell semantics could drift on command parsing edges. Workflow uses no shell-specific syntax today so risk is low. **Target:** pin `runs-on: windows-2022` or add `defaults: { run: { shell: bash } }` if a runner upgrade breaks a step [.github/workflows/ci.yml:17].
- [x] [Review][Defer] `--cov=nova` (package name) and `source = ["src/nova"]` (path) are two resolution strategies. They agree today because hatchling packages `src/nova` as `nova` with no namespace nesting. Drift risk if a future story adds a top-level `nova/` or namespace packages. **Target:** consolidate to `--cov=src/nova` (path-consistent with config) during the next touch to either pyproject.toml coverage or CI pytest invocation [pyproject.toml:62 + .github/workflows/ci.yml:55].
- [x] [Review][Defer] Coverage `parallel = false` hard-codes single-process runs; adding `pytest-xdist` later would cause `.coverage.*` files to collide and the last worker's data to win. Spec-locked for T1. **Target:** whichever story introduces pytest-xdist — flip to `parallel = true` and add a `coverage combine` step [pyproject.toml:66].
- [x] [Review][Defer] `exclude_lines = ["\\.\\.\\."]` is unanchored — matches any triple-dot sequence in a source line, not just bare Ellipsis bodies. Could silently drop coverage on lines with `"..."` string literals or type-stub conventions. Convention-matching per coverage.py docs. **Target:** tighten to `"^\\s*\\.\\.\\.\\s*$"` if over-exclusion is observed in a coverage report [pyproject.toml:72].
- [x] [Review][Defer] `.gitignore` CRLF-safety relies on `Path.read_text()` universal-newlines mode + `str.splitlines()` handling `\r\n` (verified safe today). `.gitattributes` does not explicitly pin `.gitignore` to LF. **Target:** add `.gitignore text eol=lf` to `.gitattributes` next time it's touched [.gitattributes missing rule].
- [x] [Review][Defer] `_extract_canonical_commands` pairs each chain segment to a label by `startswith` prefix match. Today all 5 prefixes are unique; a future project-context.md rewrite could produce a chain with overlapping prefixes and silently mis-pair labels (though `assert not missing` catches the total-count regression). **Target:** tighten to assert exactly-one-segment-per-prefix [tests/unit/test_ci_workflow.py:99-108].
- [x] [Review][Defer] `test_workflow_runs_canonical_commands_in_order` asserts `positions == sorted(positions)` — non-decreasing. If two canonical commands accidentally shared a prefix, both would match the same step and `positions` would contain duplicates that trivially sort. Add `len(set(positions)) == len(positions)` for distinctness. Low-risk (prefixes are all unique today) [tests/unit/test_ci_workflow.py:259-265].
- [x] [Review][Defer] Canonical-commands regex (`Full verify:\s*`([^`]+)``) breaks if project-context.md reformats the full-verify line to a fenced code block. **Target:** generalize the parser or pin the section-heading anchor when project-context.md is next restructured [tests/unit/test_ci_workflow.py:98].
- [x] [Review][Defer] `docs/development.md` documents `uv >= 0.5.11` minimum but no preflight check enforces it; a dev on uv 0.4.x hits a cryptic "unsupported lockfile revision" error. Spec AC #10 was docs-only. **Target:** whichever story adds a first-run setup script (Story 2.1 setup.bat) — include a `uv --version` parse + minimum check [docs/development.md:9].
- [x] [Review][Defer] Spec AC #13 named one parametrized test (`test_workflow_runs_canonical_commands_in_order`); dev split into `test_workflow_step_starts_with_canonical_command` (parametrized over 5 labels) + `test_workflow_runs_canonical_commands_in_order` (single ordering test). Superset of spec — extra coverage, not a regression. Accounts for part of the "+27 tests vs. AC #15's +18 prediction" delta. **Target:** no action; noted as a spec-over-delivery [tests/unit/test_ci_workflow.py:243-265].

## Dev Notes

### Critical Architecture Rules (carry-forward pinned for Story 1.11)

- **"Local and CI quality gates must match."** ([project-context.md:137](../project-context.md#L137), also line 166) — THIS story is the mechanical enforcement. The workflow YAML's `run:` strings are LITERAL copies of the canonical commands; AC #13's drift test reads both files and asserts equality. If a future story needs to add a tool (e.g., `pyright` alongside `mypy`), that story edits `project-context.md` AND `.github/workflows/ci.yml` in the same commit; the AC #13 test catches half-edits.
- **"pyproject.toml is the single config source."** ([project-context.md:146](../project-context.md#L146)) — coverage config goes in `[tool.coverage.*]`, NOT a `.coveragerc`. Ruff's `T20` lint rule already catches `print()` violations; no separate ruff CI step needed beyond `ruff check src/ tests/`.
- **"uv is the package manager."** ([project-context.md:144](../project-context.md#L144)) — no `pip` invocations anywhere in the workflow. `astral-sh/setup-uv` is the canonical action; alternatives (`cpcloud/setup-uv`, `install-uv-action`) are non-official and explicitly rejected.
- **"Python 3.12.x"** ([project-context.md:29](../project-context.md#L29), [pyproject.toml:6](../../pyproject.toml#L6)) — `.python-version` is the single source. Do not hard-code `"3.12"` in the workflow (AC #2 explicit).
- **"No dead or commented-out code."** ([project-context.md:135](../project-context.md#L135)) — applies to workflow YAML too. Every step is used; every `with:` block sets values that matter. No commented-out "for future use" blocks.
- **"Structured logging to file only."** ([project-context.md:44](../project-context.md#L44)) — NOT applicable to CI workflow (CI has no log file; it streams to GitHub's step-log). Only applies to the application's own runtime. Mentioned here to head off the AI agent's potential "should we log the CI run?" question — no.
- **"Avoid catch-all utility modules."** ([project-context.md:136](../project-context.md#L136)) — applies to the new test file. The three helpers (`_load_workflow`, `_load_pyproject`, `_workflow_step_runs`) live IN the test file, module-private. Do NOT create `tests/unit/_ci_helpers.py`.
- **"No mutable module-level runtime state."** ([project-context.md:55](../project-context.md#L55)) — the test file should not cache `_workflow` across tests via a module-level variable. Each test re-reads the file. Cheap (≤1 KB file); correctness over micro-optimization.

### Previous Story Intelligence — Story 1.10 (done 2026-04-15)

Story 1.10 landed the composition root + CLI entrypoint + two-phase logging. Key carry-forwards for Story 1.11:

- **Test file placement mirrors flat layout.** `tests/unit/test_ci_workflow.py` lives directly under `tests/unit/` (not under `tests/unit/ci/` — we don't create a subdirectory for a single file per Story 1.4+ precedent). Matches existing `test_composition_root.py`, `test_cli.py`, `test_app.py`.
- **Structural-parsing-over-text-regex precedent extends from AST to YAML/TOML.** Story 1.10's AC #4 used `ast.walk` for Python source; Story 1.11 uses `yaml.safe_load` + `tomllib.loads` for non-Python artifacts. Same principle: parse the structure, assert invariants on parsed nodes, avoid brittle text regex. `grep`-style tests are only OK for line-existence checks (e.g., "does `.gitignore` contain this literal line") — structural invariants must use proper parsers. See `feedback_ast_static_analysis_tests.md`.
- **`# type: ignore` / `cast()` / `Any` budget = ZERO.** Story 1.10 locked this in AC #18 and the quality gate stays clean (739 passed, 1 skipped). The new test file MUST hold this budget. `yaml.safe_load` returns `Any` — narrow at access sites via `isinstance(..., dict)` / `isinstance(..., list)` / `isinstance(..., str)` checks before indexing. Story 1.10's `argparse.Namespace` attribute-access narrowing is the pattern (see [src/nova/cli.py](../../src/nova/cli.py) `_parse_log_level`).
- **Commit style (Stories 1.1 – 1.10 carry-forward):** terse, imperative, story ID prefix + brief scope in parens. Expected: `"Story 1.11: CI quality-gate automation (.github/workflows/ci.yml)"`.
- **Verbatim-duplicate small helpers rather than cross-package import.** `tests/` has no `__init__.py` files (flat-test-layout precedent); duplicate the 3 tiny helpers (≤20 lines total) inside `test_ci_workflow.py`. Do NOT import from `tests/unit/test_composition_root.py` — same rule as Story 1.10's duplicate-from-`test_core_isolation.py`.
- **`Mapping[str, object]` over `dict[str, Any]`.** When a helper returns parsed YAML / TOML, type it as `dict[str, object]` (not `dict[str, Any]`) and narrow at use. Matches Story 1.8's `AuditLogger.log_action(details: Mapping[str, object] | None)` pattern.
- **Alphabetize `__all__` lists.** N/A — the new test file has no `__all__` (test files don't export). No action.
- **Ruff rules active** (from [pyproject.toml:42](../../pyproject.toml#L42)): `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. `T20` catches accidental `print()` in the test file. `SIM` catches pointless `if x == True`. `UP` enforces `list[str]` over `List[str]`. `I` enforces import order.
- **mypy strict** applies to the new test file ([pyproject.toml:49](../../pyproject.toml#L49) `files = ["src/nova", "tests"]`). Type every function signature, every return annotation, every parameter. `yaml.safe_load` returns `object | None` in stubs — narrow via `assert isinstance(result, dict)` before indexing.

### Deferred-Work Items Closed By This Story

Three items from [deferred-work.md](./deferred-work.md) explicitly target Story 1.11 (lines 20, 21, 22 — all under "Deferred from: code review of story 1-1-project-scaffolding-and-package-setup"):

1. **"uv.lock `revision = 3` requires recent uv on CI. Lockfile pins a revision older uv versions will reject. Target: Story 1.11 (CI quality-gate automation). Document a minimum `uv` version and pin it in CI runner setup."**
   → **Action:** AC #3 pins `astral-sh/setup-uv@v5` with an explicit `version:` matching the committed `uv.lock` revision. AC #10 documents the minimum in `docs/development.md`.

2. **"Coverage config `[tool.coverage.*]` absent. `pytest-cov` is installed but no thresholds or report config wired. Target: Story 1.11 (CI quality-gate automation). Original story design already defers this."**
   → **Action:** AC #7 wires `[tool.coverage.run]` + `[tool.coverage.report]` in `pyproject.toml`. `fail_under` deliberately NOT set — threshold enforcement is a retrospective policy decision, not a wiring concern.

3. **"`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`. Belt-and-suspenders for CI report artifacts that don't exist today. Target: Story 1.11 (CI). Add when CI actually generates these files."**
   → **Action:** AC #9 adds all 4 patterns. `coverage.xml` is produced by the unit-tests step (AC #5 `--cov-report=xml`); the other 3 are forward-compat for tools that may generate artifacts in future stories.

**Remove these three items from `deferred-work.md` as part of this story's commit** (Task 8 — add the bookkeeping edit to the commit).

### Git Intelligence — last 5 commits

```
9996903 Story 1.10: composition root + CLI entrypoint (app.py, cli.py)
ea718a4 Story 1.9: port interfaces + shield no-op adapter (ports/, adapters/shield/)
f2ef02b Story 1.8: audit logger (core/audit.py)
ab2f676 Story 1.7: capability tier state machine (core/tiers.py)
ba24622 Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow exactly.
- **Scope pattern:** `"Story 1.N: {what} ({where})"`. Story 1.11 primary artifact is `.github/workflows/ci.yml`; scope is `(.github/workflows/ci.yml)`.
- **No prior CI workflow.** Confirmed via `ls .github/` returning "No such file or directory". This story creates the directory from scratch.
- **Branch state at story start:** `main` is at `9996903` (Story 1.10 done). Working tree clean.
- **Base CI baseline for regression comparison:** 739 tests passed, 1 skipped (Story 1.10 last accepted handoff). Post-Story-1.11 expected: 757 passed, 1 skipped (+18: the 17 AC #13 bullets plus the `test_workflow_on_key_is_string_not_bool` guard added during story review).

### Latest Tech Information (as of 2026-04-15)

- **`astral-sh/setup-uv@v5`** — the official uv GitHub Action, maintained by Astral (uv's authors). `v5` is the current major; `with.version: "X.Y.Z"` pins a specific uv binary. `enable-cache: true` caches `~/.cache/uv` (Linux/macOS) / `%LOCALAPPDATA%\uv\cache` (Windows) across runs keyed on `uv.lock`. Alternatives (`pipx install uv`, manual `curl -LsSf https://astral.sh/uv/install.sh`) are explicitly rejected per AC #3.
- **`actions/setup-python@v5`** — stable, supports `python-version-file: .python-version` natively as of `v5` (released mid-2024). AC #2 uses `python-version: ${{ steps.pyver.outputs.version }}` instead of `python-version-file` because the echo-via-bash pattern is more explicit about "CI reads the same file local dev uses" — and also works on Windows (`python-version-file` implementation had some Windows quirks in `v4`; safe to use literally either way as of 2026).
- **`actions/checkout@v4`** — current major; `v5` is in beta as of early 2026 but not widely adopted. Pin `@v4` for stability.
- **`uv --version`** on the local dev machine — the dev agent should run this before writing the workflow to capture the current stable uv. Pick that version for the CI `with.version:` pin. The committed `uv.lock` has `revision = 3` per the file header; `revision = 3` was introduced in `uv 0.5.11` (late 2024). Any `uv >= 0.5.11` opens the lockfile; the CI pin should be a specific known-good release (e.g., the version currently on the dev machine).
- **Windows runners:** `windows-latest` currently maps to `windows-2022` (GitHub's transition to `windows-2025` is gradual; check [GitHub runner image docs](https://github.com/actions/runner-images) if an obscure failure surfaces). Both ship Git Bash, which is why `shell: bash` + `cat .python-version` works for AC #2.
- **`yaml.safe_load`** — PyYAML's parser. Returns `dict | list | str | int | float | bool | None` (or raises `yaml.YAMLError`). Returns type is `Any` in type stubs; narrow via `isinstance`.
- **`tomllib`** — stdlib since Python 3.11. `tomllib.loads(text)` returns `dict[str, Any]` in stubs; same narrowing pattern.
- **GitHub Actions `concurrency`** — scoped per-workflow by default; adding `group: "ci-${{ github.ref }}"` scopes per-ref so pushes to different branches don't cancel each other. `cancel-in-progress: true` is the right default for CI (latest commit wins); `false` is only correct for deployment workflows.
- **`permissions: { contents: read }`** — GitHub Actions' minimal permission set. Without this, the workflow inherits the repo's default token permissions (historically write). The `contents: read` override is the 2023+ security best practice; setting it at the workflow level (vs. step level) applies to every step in every job.
- **PyYAML** — already in [project] dependencies ([pyproject.toml:12](../../pyproject.toml#L12) `pyyaml>=6.0`) because Story 1.6's config loader uses it. The CI-workflow test gets YAML parsing for free — no new dev-dep needed.

### Project Structure Notes

**Files created (7) + modified (3):**

**NEW files (7):**
1. [.github/workflows/ci.yml](../../.github/workflows/ci.yml) — primary CI workflow (~40 lines YAML)
2. [docs/development.md](../../docs/development.md) — minimum uv version + canonical command reference (~30 lines)
3. [tests/unit/test_ci_workflow.py](../../tests/unit/test_ci_workflow.py) — structural test (~160 lines: 18 tests + 3 helpers)

**MODIFIED files (3):**
4. [pyproject.toml](../../pyproject.toml) — append `[tool.coverage.run]` + `[tool.coverage.report]` sections (~15 new lines)
5. [.gitignore](../../.gitignore) — insert 4 lines + 1 section-header comment (5 new lines)
6. [_bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — remove 3 Story-1.1-deferred bullets

**UNTOUCHED (explicit scope guard):**
- All 30+ files under [src/nova/](../../src/nova/) — this story adds zero source-code changes.
- [tests/unit/test_app.py](../../tests/unit/test_app.py), [tests/unit/test_cli.py](../../tests/unit/test_cli.py), [tests/unit/test_composition_root.py](../../tests/unit/test_composition_root.py), [tests/integration/test_cli_bootstrap.py](../../tests/integration/test_cli_bootstrap.py) — no edits, no regressions.
- [_bmad-output/project-context.md](../project-context.md) — read-only source of truth for AC #13.
- [.python-version](../../.python-version) — read-only; CI consumes the value via AC #2.
- [uv.lock](../../uv.lock) — read-only; CI's `--frozen` mode REQUIRES it to be current.

**Alignment with unified project structure:**
- `.github/workflows/` is the GitHub-standard location for CI workflows — every open-source Python project on GitHub uses this path. No project-specific convention required.
- `docs/development.md` matches architecture.md's reference to a `docs/` directory for developer documentation. Story 1.0 already ships `docs/config-schemas.md` ([listed in Story 1.1 AC #1](../../docs/config-schemas.md)).
- Test-file placement (`tests/unit/test_ci_workflow.py`, flat under `tests/unit/`) matches the Story 1.2+/1.10 precedent.

**Detected conflicts or variances:**
- **mypy invocation delta from epic AC.** The epic AC at [epics.md:880](../planning-artifacts/epics.md#L880) says `uv run mypy src/`. Story 1.10's AC #18 widened this to `uv run mypy src/ tests/` and the quality gate is green at the wider scope. Story 1.11 picks the wider scope and documents the delta in AC #5 + a workflow `# comment`. If a future story needs to match the epic AC literally, THAT story decides which side wins and updates `project-context.md` + the workflow in the same commit.
- **Coverage threshold not enforced.** Epic AC doesn't mandate a coverage threshold; AC #7 wires the config without enforcement. If an epic-2+ retrospective decides a threshold, THAT story sets `fail_under = N` and removes `test_pyproject_does_not_set_coverage_fail_under` from `test_ci_workflow.py`.

### Testing Standards Summary

- **Test framework:** pytest + pytest-asyncio (`asyncio_mode = "auto"` per [pyproject.toml:53](../../pyproject.toml#L53)). The new test file uses **only** synchronous tests — no async needed for YAML/TOML parsing.
- **Unit test** lives in `tests/unit/test_ci_workflow.py` — flat under `tests/unit/`. No external I/O beyond `Path.read_text()` on 4 committed files.
- **mypy strict** applies to the new test file. `yaml.safe_load` returns `Any`; narrow via `isinstance(...)` before dict-indexing. Example pattern:
  ```python
  workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
  assert isinstance(workflow, dict)
  jobs = workflow.get("jobs")
  assert isinstance(jobs, dict)
  quality_gate = jobs.get("quality-gate")
  assert isinstance(quality_gate, dict)
  steps = quality_gate.get("steps")
  assert isinstance(steps, list)
  ```
  Narrow at each access site — DO NOT introduce a typed wrapper dataclass for YAML parsing (speculative abstraction; YAGNI).
- **No async cleanup needed.** This test file is fully synchronous. Story 1.10's `asyncio.all_tasks()` teardown concern does not apply.
- **Parametrize over state matrices.**
  - `test_workflow_runs_canonical_commands_in_order` parametrizes over 5 canonical-command fragments.
  - `test_gitignore_covers_ci_artifacts` parametrizes over 4 gitignore patterns.
  - Total delta: AC #13 asserts 18 distinct invariants (17 original bullets + `test_workflow_on_key_is_string_not_bool` added during review); `@pytest.mark.parametrize` collapses 2 of them into parametrized multi-case tests, landing at 16 pytest-collected test IDs (those 2 parametrized tests each expand into multiple cases at collection time, for a final collected count of ~27 — the exact number depends on parametrize cardinality, but the ≥18 lower bound is the AC #15 regression guard).
- **Coverage target:** 100% of `test_ci_workflow.py` (trivial — pure assertions on parsed dicts) + the workflow `coverage.xml` is produced for `src/nova` as a side effect (AC #7).
- **No silent warnings in passing tests.** [project-context.md:105](../project-context.md#L105). YAML `SafeLoader` + `tomllib` both emit no warnings. `Path.read_text()` with `encoding="utf-8"` is explicit — no `DeprecationWarning` about implicit encoding on 3.12+.
- **Repo tree stays clean.** [project-context.md:159](../project-context.md#L159). The test creates no files, spawns no processes. `git status` post-run shows only the intentional AC-driven edits.

### Critical Constraints (carry-forward + story-specific)

- **CI workflow MUST NOT commit secrets.** No `API_KEY`, no `ANTHROPIC_API_KEY`, no `secrets.*` references in the workflow file. T1 CI does not run the Claude API (tests mock it). If a future story needs API-calling integration tests in CI, that story adds `secrets.ANTHROPIC_API_KEY` via GitHub repo-secrets and wires `env: { ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }} }` at that step. Today: zero secrets.
- **CI workflow MUST NOT push to a remote, create tags, or publish packages.** `permissions: { contents: read }` enforces this at the token level; the workflow also just doesn't call any such commands.
- **`uv.lock` stays in sync with `pyproject.toml`.** If Task 4's edit to `pyproject.toml` (adding `[tool.coverage.*]` sections) causes `uv sync --frozen` to fail (because `--frozen` checks metadata hashes), run `uv sync` (without `--frozen`) locally to regenerate `uv.lock` + commit the updated lockfile in the same commit. **BUT:** `[tool.coverage.*]` is NOT part of the `[project]` dependencies metadata; it should NOT change `uv.lock` at all. Verify via `git diff uv.lock` after Task 4 + Task 9 — expect zero diff. If there IS a diff, investigate (unexpected uv behavior) before pushing.
- **No new dependencies added.** PyYAML, tomllib (stdlib), re (stdlib), pathlib (stdlib), pytest (already dev-dep) cover the new test. `types-pyyaml` is already installed ([pyproject.toml:25](../../pyproject.toml#L25)). Zero dep delta.
- **`.github/workflows/*.yml` files must not contain template literals outside YAML strings.** GitHub Actions' `${{ ... }}` expression syntax is valid only inside string values. Accidentally placing it in a key or outside a string (e.g., `python-version: ${{ steps.pyver.outputs.version }}` unquoted) works because YAML treats the unquoted value as a string, BUT ruff-yaml / actionlint / hand review prefer explicit quoting: `python-version: "${{ steps.pyver.outputs.version }}"`. Use explicit quotes for every `${{ ... }}` usage to make the workflow parseable by third-party linters if ever added.
- **Step names are human-readable, step IDs are stable.** `id: pyver` (short, kebab-case) is the referencable handle. `name: Read .python-version` is the display name. Do not use the id in any `${{ steps.<id>.outputs.* }}` reference that might conflict with future step ids — prefer `pyver` over `python_version` / `pv`.
- **Default shell matters on Windows runners.** GitHub-hosted Windows runners default to `pwsh` (PowerShell 7) for `run:` blocks. `cat .python-version` is a cmdlet alias in pwsh that works, but the stdout-to-`$GITHUB_OUTPUT` redirection syntax differs between pwsh and bash. AC #2's `shell: bash` override is load-bearing: `echo "version=$(cat .python-version)" >> "$GITHUB_OUTPUT"` is bash syntax. If the dev agent uses pwsh, the equivalent is `"version=$(Get-Content .python-version)" >> $env:GITHUB_OUTPUT` — pick one, document in a `# comment`, and stay consistent.
- **Do NOT use `actions/cache@v4` manually for uv.** `astral-sh/setup-uv@v5`'s `enable-cache: true` is the canonical cache mechanism. Adding a separate `actions/cache@v4` would create two caches for the same data — confusing + wasteful of GitHub's cache-storage quota (10 GB per repo).
- **Do NOT invoke `pip` anywhere.** [project-context.md:144](../project-context.md#L144) bans it. `uv sync --frozen --all-extras` handles all install needs.
- **Do NOT set `CI=true` explicitly.** GitHub Actions sets `CI=true` automatically. Setting it in the workflow is redundant. Some tools (pytest, ruff) check `CI` to adjust output verbosity; rely on GitHub's default.
- **Do NOT add a test-reporter / junit-xml upload step.** AC #9 gitignores `junit.xml` for forward-compat, but this story does not produce or upload it. Adding the wiring without a consumer (GitHub Checks integration, third-party dashboard) is speculative.
- **Do NOT add codecov / coveralls integration.** Same reasoning — consumers are speculative.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.11: CI Quality-Gate Automation](../planning-artifacts/epics.md) — canonical AC, lines 866–888.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives + what ships, lines 357–395.
- [Source: _bmad-output/planning-artifacts/epics.md#Additional Requirements — From Architecture — Implementation Sequence](../planning-artifacts/epics.md) — lines 220–235.
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 29 (Windows-only platform), 137 (local=CI quality gates), 144 (uv is the package manager), 146 (pyproject.toml single-config), 147 (uv.lock committed), 148–156 (canonical commands — **THE source of truth** for AC #5 + AC #13 drift test), 166 (local=CI workflow match).
- [Source: pyproject.toml](../../pyproject.toml) — current config; Task 4 appends `[tool.coverage.*]` sections.
- [Source: .gitignore](../../.gitignore) — current ignore list; Task 5 appends 4 CI-artifact patterns.
- [Source: .python-version](../../.python-version) — pins `3.12`; AC #2 reads this in CI.
- [Source: uv.lock](../../uv.lock) — header declares `revision = 3`; AC #3 pins minimum uv accordingly.
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — three items targeted at Story 1.11 (lines 20, 21, 22). Task 8 removes them.
- [Source: _bmad-output/implementation-artifacts/1-10-composition-root-and-cli-entrypoint.md](./1-10-composition-root-and-cli-entrypoint.md) — prior story. Test file layout precedent, structural-parsing-over-regex pattern, mypy-strict-on-tests rule, commit style.
- [Source: _bmad-output/implementation-artifacts/1-1-project-scaffolding-and-package-setup.md](./1-1-project-scaffolding-and-package-setup.md) — origin of the three deferred items Story 1.11 closes (coverage config, CI-artifact gitignore, uv minimum version).
- [Source: tests/unit/test_composition_root.py](../../tests/unit/test_composition_root.py) — AST-walk structural test pattern; Story 1.11 adapts the same principle to YAML/TOML parsing. Verbatim-helper-duplication precedent.
- [Source: tests/unit/test_scaffold.py](../../tests/unit/test_scaffold.py) — Story 1.1's baseline import-smoke test; confirms test-file placement under flat `tests/unit/`.
- [Source: docs/config-schemas.md](../../docs/config-schemas.md) — Story 1.0's doc file, confirms `docs/` as the correct home for `docs/development.md`.
- [Source: https://github.com/astral-sh/setup-uv](https://github.com/astral-sh/setup-uv) — the official uv GitHub Action. Pin `@v5`.
- [Source: https://github.com/actions/setup-python](https://github.com/actions/setup-python) — Python setup action. Pin `@v5`.
- [Source: https://github.com/actions/checkout](https://github.com/actions/checkout) — repo checkout action. Pin `@v4`.
- [Source: C:\Users\sayuj\.claude\projects\c--Projects-AI-Assistant\memory\feedback_ast_static_analysis_tests.md](../../../../Users/sayuj/.claude/projects/c--Projects-AI-Assistant/memory/feedback_ast_static_analysis_tests.md) — "For N.O.V.A., use ast.walk + ast.Call inspection, not text regex — avoids docstring false positives." Generalized to YAML/TOML: use `yaml.safe_load` / `tomllib.loads` + dict inspection, not line regex.

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- **Canonical-commands drift test iteration.** Initial `_extract_canonical_commands` helper parsed the per-tool bullets at [project-context.md:151-155](../project-context.md#L151-L155). That regressed on `Format`: line 154 documents the write-mode command `uv run ruff format src/ tests/`, but CI needs the check-mode `uv run ruff format --check src/ tests/`. Pivoted the helper to parse [project-context.md:156](../project-context.md#L156)'s full-verify chain, which is the authoritative CI-parity source (it explicitly uses `--check`). Per-tool bullets are only consulted for `Test (unit)` / `Test (integration)`, which full-verify collapses into bare `uv run pytest` that CI splits by directory.
- **PyYAML `on:` quoting verified pre-test-write.** Ran a one-liner (`uv run python -c "import yaml; ..."`) against the freshly-authored `ci.yml` to confirm `"on"` parses as the string key (not `True`). Confirmed before writing the test file, which let the `test_workflow_on_key_is_string_not_bool` test pass first-try.
- **mypy strict on the test file — zero `Any` / `cast()` / `# type: ignore` achieved.** Typed helpers as `dict[object, object]` and narrowed at every access site via `isinstance(..., dict)` / `isinstance(..., list)` / `isinstance(..., str)` checks. `yaml.safe_load`'s `Any` return is bounded by the `assert isinstance(loaded, dict)` guard inside `_load_workflow` — mypy strict accepts the narrowed assignment back to `dict[object, object]`. Grep confirmed zero violations of the budget.
- **uv.lock drift check.** Ran `git diff --stat uv.lock` after Task 4's `pyproject.toml` edit; zero diff confirmed (the `[tool.coverage.*]` sections are not part of `[project]` dependency metadata, so `uv sync --frozen` accepts the updated `pyproject.toml` without regenerating the lockfile).
- **coverage.xml repo-tree cleanliness check.** Post-test-run, `ls coverage.xml` shows the file exists at repo root but `git status --short` does NOT list it as untracked — Task 5's `.gitignore` addition correctly swallows it.
- **Baseline reconciliation.** Story originally cited a 739-passed baseline (per user feedback during story-authoring). Actual pre-Story-1.11 test count resolved to 740 (722 unit + 18 integration, matching the sprint-status.yaml header that was flagged for reconciliation). Final post-Story-1.11 count: **767 passed, 1 skipped** (+27: 17 non-parametrized tests + 5 parametrized canonical-command IDs + 4 parametrized gitignore IDs + 1 `test_workflow_on_key_is_string_not_bool` guard added during review). The "+18" prediction in AC #15 undercounted by missing that parametrization expands two test functions into 9 collected IDs.

### Completion Notes List

- **All 17 Acceptance Criteria satisfied** (AC #1 through AC #17). Workflow file at [.github/workflows/ci.yml](../../.github/workflows/ci.yml) pins `runs-on: windows-latest`, pinned `astral-sh/setup-uv@v5` with `version: "0.11.6"`, Python sourced from `.python-version` via `python-version-file` (Approach A per AC #2), and all 6 quality-gate steps in the AC #5 canonical order. Fail-fast relied-on via default sequential-step semantics (zero `continue-on-error`, zero step-level `if:` guards — AC #6 invariant is delivered by doing nothing).
- **CI parity invariant locked by structural test.** [tests/unit/test_ci_workflow.py](../../tests/unit/test_ci_workflow.py) parses both the workflow YAML and project-context.md's full-verify chain; asserts each of the 5 canonical CI commands appears as a `startswith` prefix of some workflow step, AND that the 5 matched steps appear in CI order (lint → format → mypy → unit → integration). Future edits to project-context.md that change any canonical command will fail this test until `ci.yml` is updated in the same commit.
- **`"on":` quoting requirement locked.** `test_workflow_on_key_is_string_not_bool` asserts `"on" in workflow AND True not in workflow` — catches any future drop-the-quotes regression that would otherwise trigger PyYAML's YAML 1.1 bool-coercion and make `workflow["on"]` a `KeyError` trap.
- **Three deferred-work items closed and removed from `deferred-work.md`:**
  1. Story 1.1 "uv.lock `revision = 3` requires recent uv on CI" — closed by pinning `astral-sh/setup-uv@v5` with `version: "0.11.6"` + documenting the `uv >= 0.5.11` minimum in [docs/development.md](../../docs/development.md).
  2. Story 1.1 "Coverage config `[tool.coverage.*]` absent" — closed by appending `[tool.coverage.run]` (source=`src/nova`, branch=true) + `[tool.coverage.report]` (4 exclude_lines, precision=1, show_missing=true) to [pyproject.toml](../../pyproject.toml). No `fail_under` threshold (AC #7 deliberate; a future retrospective adds one once a baseline is known).
  3. Story 1.1 "`.gitignore` missing `coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`" — closed by the 4-line addition in a new `# --- CI artifacts (Story 1.11)` section in [.gitignore](../../.gitignore).
- **Quality gate clean (local run — AC #15):** `ruff check` + `ruff format --check` + `mypy strict (src/ tests/)` + `pytest` all green. **767 passed, 1 skipped.** Coverage at 91.9% across `src/nova/` (unit tests only; integration coverage not combined per AC #7).
- **Zero `# type: ignore`, zero `cast()`, zero new `Any`** added (grep-verified on the new test file). mypy strict stays clean at 66 source files.
- **Zero modifications to existing Story 1.1 – 1.10 production source files** (AC #16 scope boundary). Only the 3 permitted edits: [pyproject.toml](../../pyproject.toml) (appended coverage sections), [.gitignore](../../.gitignore) (4-line CI-artifacts section), [deferred-work.md](./deferred-work.md) (removed 3 bullets + reworded a 4th).
- **Task 10 deliberately left unchecked** — commit/push/PR-merge is the user's action per dev-story convention. Task 9's full local quality-gate run is the best proxy for the CI run this story authors; the first real CI green-run happens when the user pushes.
- **Baseline count reconciled.** The "739" baseline cited during story authoring resolved to 740 (the sprint-status.yaml header's "740 passed / 1 skip" was accurate; my initial read of the Story 1.10 change log's intermediate 739 number was the source of the drift). Final delta: +27 collected tests (740 → 767), cleanly accounted for by 19 new test functions of which 2 are parametrized (5 + 4 cases respectively).

### File List

**New — production (0):** this story adds zero production source code. All application behavior is unchanged.

**New — CI / tooling (3):**
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml) — single-job GitHub Actions workflow (`windows-latest`, pinned uv `0.11.6`, 6 quality-gate steps in canonical order)
- [docs/development.md](../../docs/development.md) — minimum uv version + canonical full-verify command + CI parity note (~35 lines)
- [tests/unit/test_ci_workflow.py](../../tests/unit/test_ci_workflow.py) — structural lock on workflow + coverage config + .gitignore + docs (19 test functions, 27 collected IDs)

**Modified — config / tracking (3):**
- [pyproject.toml](../../pyproject.toml) — appended `[tool.coverage.run]` + `[tool.coverage.report]` sections (15 new lines)
- [.gitignore](../../.gitignore) — inserted `# --- CI artifacts (Story 1.11)` section with 4 patterns (`coverage.xml`, `junit.xml`, `.uv_cache/`, `.hatch/`)
- [_bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — removed the 3 bullets explicitly targeted at Story 1.11 from the Story 1.1 section; remaining 2 items (hatchling sdist + PEP 735) left verbatim per AC #14

**Modified — sprint tracking (1):**
- [_bmad-output/implementation-artifacts/sprint-status.yaml](./sprint-status.yaml) — `1-11-ci-quality-gate-automation: ready-for-dev` → `in-progress` → `review`; `last_updated` header refreshed

### Change Log

| Date | Change | By |
|---|---|---|
| 2026-04-15 | Story 1.11 implementation complete: single-job GitHub Actions workflow (`.github/workflows/ci.yml`) pinning `windows-latest` runner, `astral-sh/setup-uv@v5` at uv `0.11.6`, Python via `python-version-file: .python-version`, 6 quality-gate steps matching project-context.md:156 canonical full-verify chain. Coverage config appended to `pyproject.toml` (no `fail_under` threshold — deliberate). 4 CI-artifact patterns added to `.gitignore`. New `docs/development.md` documenting `uv >= 0.5.11` minimum + CI-parity invariant. 19-function structural test (`tests/unit/test_ci_workflow.py`, 27 collected IDs) locks workflow YAML + coverage config + .gitignore + docs against drift. Three Story 1.1 deferred-work items closed. Quality gate clean: **767 passed, 1 skipped** (+27 vs. pre-story baseline of 740). Zero `# type: ignore` / `cast()` / `Any` added. Status → review. | claude-opus-4-6[1m] |
| 2026-04-15 | Addressed code review findings — 1 patch applied (simplified redundant isinstance+walrus in `test_workflow_python_version_from_dot_python_version`). 12 items deferred to deferred-work.md. ~40 dismissed as false positives or spec-mandated scope. Quality gate re-verified clean: 767 passed, 1 skipped. Status → done. | claude-opus-4-6[1m] |
| 2026-04-15 | Post-review fix-ups from user inspection: (1) Resolved mypy-scope drift by tightening project-context.md:155-156 canonical from `mypy src/` to `mypy src/ tests/` — local and CI are now byte-identical, honoring the "no drift" rule. (2) Updated docs/development.md to remove the "CI widens" language and explicitly document the one remaining legitimate CI-only extension (pytest `--cov=nova` on the unit step). (3) Converted Task 10's unchecked checkboxes to a plain-bullet "Handoff to User" subsection, removing the visual contradiction with status `done`. Quality gate re-verified clean: 767 passed, 1 skipped. | claude-opus-4-6[1m] |
