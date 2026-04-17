# Story 2.3: Guided Mode Creation Wizard

Status: done

## Story

As a new user,
I want to create my first workspace mode through an interactive wizard with starter workspace-mode templates offered,
So that I have at least one useful mode configured before my first session ends.

## Acceptance Criteria

### Group A: Starter Templates

1. Given setup has completed the API key step and no user-created modes exist yet, when the wizard reaches the mode creation step, then starter workspace-mode templates are offered (e.g., "coding" with VS Code + Chrome + Terminal, "study" with Notion + Chrome) — the user can accept as-is, modify, or skip each template.

2. Templates are loaded from `config/modes/*.yaml` (shipped defaults). The wizard presents each template by display name, lists its apps, and asks the user to accept, modify, or skip it.

3. If the user accepts a template as-is, the source file at `config/modes/{stem}.yaml` is copied **verbatim** to `%LOCALAPPDATA%/nova/modes/{stem}.yaml` via a byte-level filesystem copy (comments, ordering, and formatting preserved — **no YAML load/dump round-trip**). If the target already exists (e.g., `setup.bat` pre-copied it), the wizard does not overwrite: it reports the mode as ready and moves on.

3a. If the user chooses "modify" on a template, the template is loaded via `yaml.safe_load`, presented as editable fields (name, apps, folders, urls), then written to the target via the schema writer path (see AC #9). Verbatim-copy preservation does not apply to the modify path — it is acknowledged that comments and formatting are lost when the user chose to edit.

### Group B: Custom Mode Creation

4. The wizard asks practical questions per custom mode: mode name, apps to open, optional folders/URLs, with a clear skip option for optional fields. `apps` is **required** — the wizard must not allow a custom mode to be confirmed or written with zero apps. If the user tries to finish the custom flow with zero apps entered, a clear message is shown ("A mode needs at least one app. Add one, or type `cancel` to abandon this mode.") and the apps prompt is re-entered.

5. App names are entered naturally ("VS Code", "Chrome") — the wizard resolves to executable names using a known app registry (hardcoded lookup table of common Windows apps) or `shutil.which()` PATH lookup.

6. Unresolvable apps produce a clear message ("Couldn't find 'AppName' — add it manually to the mode file later") without blocking mode creation. The app entry is still written to the mode file with the user-provided name as the executable, so the user can fix it later.

7. Mode names are validated: must be non-empty, must produce a valid kebab-case file stem (lowercase alphanumeric + hyphens, leading alnum), must not be a reserved Windows filename (CON, PRN, AUX, NUL, COM1-9, LPT1-9). Invalid names get a clear message and re-prompt.

### Group C: Mode File Writing

8. Each completed mode is written immediately to `%LOCALAPPDATA%/nova/modes/{mode_stem}.yaml` conforming to the pinned mode schema (`docs/config-schemas.md`): `name`, `apps` (list of `{name, executable, args}`), `folders`, `urls`, `is_default`.

9. Mode files are written atomically: temp-file + `os.replace()` — same pattern as `settings_writer.py`. Write failures produce a clear message and do not crash the wizard.

10. The file stem is derived from the mode name via slugification: lowercase, spaces/underscores replaced with hyphens, consecutive hyphens collapsed, leading/trailing hyphens stripped.

### Group D: Flow Control

11. At least one mode must **exist in `%LOCALAPPDATA%/nova/modes/`** for the wizard to exit cleanly — the rule is "at least one mode ready by exit," **not** "at least one mode newly created during this wizard run." Pre-existing mode files (e.g., `coding.yaml` copied by `setup.bat`) count toward this requirement. The wizard probes `%LOCALAPPDATA%/nova/modes/` at entry and again before exit via a stem-validity check matching `core/config.py:_is_valid_mode_stem` — files that would be skipped by the runtime loader do not count. If the directory still yields zero valid modes on exit attempt, a clear message explains and re-prompts.

12. The user can create multiple modes in sequence. After each mode (template or custom), the wizard asks "Create another mode? (yes/no)".

13. The user can type `cancel` at any prompt during custom mode creation to abort that specific mode (not the whole wizard). Response: "Mode creation cancelled." — returns to the template/create-another prompt.

### Group E: UX Compliance

14. The wizard uses N.O.V.A.'s design language: Rich panels for summaries, the color system (cyan borders/headers, green for success `✓`, red for failure `✗`, amber for warnings `⚠`), typography hierarchy (bold cyan titles, bold white section headers, soft white body), no emoji, no sycophantic framing ("How can I help you today?", "I'd be happy to...", "Great question!").

15. All prompts follow the UX spec's contextual response rules: `skip` advances past optional fields, `cancel` aborts the current mode flow, empty input on optional fields is treated as skip.

16. Confirmation summary shown before saving each mode: mode name, apps list, folders (if any), URLs (if any). User confirms or cancels.

### Group F: Integration

17. The mode wizard step is called from `nova.setup.__main__.main()` after the API key step, before exit. New function signature: `run_mode_wizard_step(console: Console, data_dir: Path) -> None`.

18. The wizard module lives at `src/nova/setup/mode_wizard.py`. It imports only from `nova.core.*` (exceptions, types) and stdlib — no imports from `nova.systems.*`, `nova.adapters.*`, or `nova.ports.*`.

19. Exit code remains 0 regardless of how many modes are created (minimum one enforced by the flow, not by exit code).

### Group G: Testing

20. Unit tests cover the app registry lookup: known apps resolve correctly, unknown apps return None.

21. Unit tests cover mode name slugification: spaces, underscores, mixed case, consecutive hyphens, leading/trailing hyphens, reserved Windows names rejected.

22. Unit tests cover the mode YAML writer: output conforms to the schema, atomic write via temp-file + `os.replace()`, write failure handling.

23. Unit tests for the interactive flow mock `Console.input()` and verify: template accept/modify/skip, custom mode creation, cancel flow, at-least-one-mode enforcement, confirmation summary display.

24. Integration test exercises the full wizard entrypoint (`main()`) with mocked Console: State A renders, API key step runs (mocked), mode wizard step runs with template acceptance, settings.yaml and mode files exist on disk afterward.

25. AST guard test asserts `nova.setup.mode_wizard` imports nothing from `nova.systems.*`, `nova.adapters.*`, or `nova.ports.*`.

## Tasks / Subtasks

- [x] Task 1: App registry lookup module (AC: 5, 6, 20)
  - [x] Create known-app registry dict mapping common display names to executables (VS Code→code, Chrome→chrome, Firefox→firefox, Notion→notion, Discord→discord, Spotify→spotify, Windows Terminal→wt, Notepad++→notepad++, Obsidian→obsidian, Slack→slack, etc.)
  - [x] Implement `resolve_app(display_name: str) -> str | None` — registry lookup (case-insensitive), then `shutil.which()` fallback
  - [x] Unit tests for registry hits, misses, case-insensitive matching, PATH fallback

- [x] Task 2: Mode name slugification (AC: 7, 10, 21)
  - [x] Implement `slugify_mode_name(name: str) -> str` — lowercase, replace spaces/underscores with hyphens, collapse consecutive hyphens, strip leading/trailing hyphens
  - [x] Implement `validate_mode_stem(stem: str) -> str | None` — returns error message if invalid (empty, reserved Windows name, invalid chars), None if valid. Reuse `_MODE_STEM_RE` and `_RESERVED_WIN_STEMS` from `core/config.py` or extract shared constants
  - [x] Unit tests: valid names, spaces, mixed case, reserved names, edge cases

- [x] Task 3: Mode file persistence — two distinct paths (AC: 3, 3a, 8, 9, 22)
  - [x] **Path A — verbatim copy** (accept-as-is templates): `copy_template_verbatim(source_yaml: Path, target_yaml: Path) -> None`. Uses `shutil.copyfile(source, tmp)` + `os.replace(tmp, target)` for atomic byte-level copy. Preserves comments, ordering, and formatting. If `target_yaml` already exists, function is a no-op (returns without touching the file) — never overwrites a user's existing mode file.
  - [x] **Path B — schema writer** (custom modes + modify-template path): `write_mode_file(modes_dir: Path, stem: str, mode_data: dict) -> None`. Atomic temp-file + `os.replace()`, same pattern as `settings_writer.py`. Output conforms to `ModeConfig` schema via `yaml.safe_dump(..., default_flow_style=False, allow_unicode=True, sort_keys=False)`. Comments are not preserved (acknowledged trade-off for edited modes).
  - [x] Writer selection is made by the flow layer (Task 4), not by the writer itself — writers are dumb, flow decides the path.
  - [x] Unit tests for Path A: verbatim byte-equality of source and target after copy, no-op when target exists, atomic behavior on failure.
  - [x] Unit tests for Path B: schema conformance (round-trip through `core/config.py:load_config` yields the same `ModeConfig`), atomic behavior, write failure handling.

- [x] Task 4: Interactive wizard flow (AC: 1-4, 11-16, 23)
  - [x] Implement pre-existing-modes scan: at wizard entry, list valid mode stems already in `%LOCALAPPDATA%/nova/modes/` using the same stem-validity rule as `core/config.py:_is_valid_mode_stem`. Display them as "Modes already ready: coding".
  - [x] Implement template presentation: load `config/modes/*.yaml` via `yaml.safe_load`, display each with apps, ask accept/modify/skip. If a template's target already exists in `%LOCALAPPDATA%/nova/modes/`, mark it as "already installed" and skip the accept/modify prompt (user can still choose modify which routes to Path B).
  - [x] Wire the writer choice: **accept-as-is → Path A** (`copy_template_verbatim`); **modify → Path B** (`write_mode_file` with edited dict); **custom → Path B**.
  - [x] Implement custom mode creation flow: name prompt → apps prompt (multi-entry loop) → optional folders → optional URLs → confirmation summary → Path B write.
  - [x] **Zero-apps hard stop in custom flow:** the apps loop accepts entries until the user types `done` (or an empty line to finish). Before leaving the apps loop, the flow checks the app count; if zero, it shows "A mode needs at least one app. Add one, or type `cancel` to abandon this mode." and re-enters the apps prompt. The confirmation summary and writer are unreachable with zero apps.
  - [x] Implement flow control: **at-least-one-mode-ready-at-exit gate** (probe `%LOCALAPPDATA%/nova/modes/` for valid stems; if zero valid stems, re-prompt), create-another loop, per-mode `cancel` handling (aborts current mode only, not the wizard).
  - [x] Rich rendering: panels for summaries, semantic colors, typography hierarchy per UX spec.
  - [x] Unit tests with mocked Console.input for all flow paths, including: template accept-as-is routes to Path A; template modify routes to Path B; custom mode with one app writes; custom mode with zero apps re-prompts (assert the writer is never called); exit-gate allows exit when a pre-existing mode was already in `modes/`; exit-gate blocks exit when `modes/` is empty and user tried to skip everything.

- [x] Task 5: Integration into `__main__.py` (AC: 17, 18, 19)
  - [x] Add `run_mode_wizard_step(console, data_dir)` call in `main()` after API key step
  - [x] Handle `data_dir is None` case (skip with warning, same pattern as API key step)
  - [x] Verify exit code remains 0

- [x] Task 6: AST guard + integration tests (AC: 24, 25)
  - [x] AST guard: `nova.setup.mode_wizard` has no imports from `nova.systems.*`, `nova.adapters.*`, `nova.ports.*`
  - [x] Integration test: full `main()` with mocked Console, verify mode files on disk

## Dev Notes

### Architecture Compliance

- **Layering rule:** `nova.setup.*` modules can import from `nova.core.*` (exceptions, types) and stdlib only. No imports from `nova.systems.*`, `nova.adapters.*`, or `nova.ports.*`. Enforced by AST guard test.
- **Config module is the single YAML reader at runtime.** The wizard writes YAML files that `core/config.py`'s `load_config()` will read on next startup. The wizard itself does NOT use `load_config()` — it reads template files directly via `yaml.safe_load` for the template presentation step only.
- **The wizard is a setup-time-only module.** It runs before the composition root (`app.py`) is wired. No ports, adapters, event bus, or system dependencies are available.

### Mode Schema Contract

Mode files must conform to `docs/config-schemas.md` and match the `ModeConfig` / `AppConfig` dataclasses in `core/config.py`:

```yaml
name: coding                    # Required. Display name (may contain spaces).
apps:                           # Required. At least one entry.
  - name: VS Code               # Required. Display name.
    executable: code             # Required. Executable name or path.
    args: []                     # Optional. Default: [].
folders: []                     # Optional. Absolute paths only.
urls: []                        # Optional. http:// and https:// only.
is_default: false               # Optional. Default: false.
```

**File stem rules** (from `core/config.py:_is_valid_mode_stem`):
- Non-empty
- No `.` characters
- Not a reserved Windows filename
- Matches regex `[a-z0-9][a-z0-9-]*` (kebab-case, leading alnum)

### Two Persistence Paths — Verbatim Copy vs. Schema Writer

Mode files reach `%LOCALAPPDATA%/nova/modes/` via **two distinct paths**, chosen by the flow layer based on user intent:

**Path A — Verbatim copy (accept-as-is templates)**
- Byte-level filesystem copy from `config/modes/{stem}.yaml` to `%LOCALAPPDATA%/nova/modes/{stem}.yaml`
- Uses `shutil.copyfile(source, tmp)` followed by `os.replace(tmp, target)` for atomicity
- **Preserves comments, field ordering, and formatting** — critical because `coding.yaml` has extensive inline comments documenting the schema (see `config/modes/coding.yaml`) that users benefit from when editing later
- **Never overwrites** — if the target already exists (setup.bat pre-copy, previous wizard run), the function is a no-op
- No YAML parsing on this path — any valid bytes in source end up in target

**Path B — Schema writer (custom modes + modify-template path)**
- Same atomic pattern as `settings_writer.py`:
  1. Write to `{stem}.yaml.tmp` in the same directory
  2. `os.replace(tmp_path, target_path)` for atomic swap
  3. On any failure, `contextlib.suppress(OSError)` → `tmp_path.unlink(missing_ok=True)` cleanup
  4. Use `yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)`
- Output conforms strictly to the `ModeConfig` schema — no comments (`pyyaml` `safe_dump` drops them)
- Comment loss is an **acknowledged trade-off**: the user chose to edit, so round-tripping through the schema is expected

**Writer selection rule:** the flow layer (Task 4) decides which path to call. Writers themselves are dumb I/O primitives and do not introspect user intent.

### App Resolution Strategy

The wizard resolves user-entered app names in two steps:
1. **Registry lookup** — hardcoded dict mapping common display names (case-insensitive) to executable names. Covers the 90% case for Windows users.
2. **PATH lookup** — `shutil.which(user_input)` as fallback for apps not in the registry.
3. **Unresolved** — if both fail, the app entry is still written using the user-provided name as `executable`. The mode file is valid (config loader accepts any string for `executable`); the app just won't launch until the user fixes the path.

No Win32 API calls (no `pywin32` dependency in setup modules). App discovery via Win32 registry/shell APIs is Epic 4+ scope.

### UX Voice Rules for Wizard Output

- **No emoji** in any output
- **No sycophantic framing** — no "Great!", "I'd be happy to...", "How can I help?"
- **Symbols only:** `✓` (green, success), `✗` (red, failure), `⚠` (amber, warning)
- **Panel titles:** bold cyan (`[bold cyan]...[/bold cyan]`)
- **Body text:** soft white (default Rich style)
- **Prompts:** direct, practical language. "What should this mode be called?" not "Please enter a name for your mode!"
- **Brevity:** "Mode saved." is a complete confirmation. No trailing summaries.

### Starter Template Flow + Pre-Existing Modes

Templates live in `config/modes/*.yaml` (shipped with the repo). `setup.bat` (Story 2.1) **already copies shipped-default mode files to `%LOCALAPPDATA%/nova/modes/` on first run, only if the target file does not already exist.** The wizard must not assume `%LOCALAPPDATA%/nova/modes/` is empty at entry.

**Wizard behavior:**
1. **At entry:** scan `%LOCALAPPDATA%/nova/modes/` for files whose stems pass `_is_valid_mode_stem`. Display these as "Modes already ready: coding, ..." in a panel. These count toward the "at least one mode by exit" requirement (AC #11).
2. **For each template in `config/modes/*.yaml`:**
   - If target already exists in `%LOCALAPPDATA%/nova/modes/`, mark it "already installed" and offer modify-only (modify routes through Path B and **will overwrite** the pre-copied file — this is the one place where the wizard overwrites; it is explicit user intent).
   - If target does not exist, offer accept-as-is / modify / skip. Accept-as-is routes through Path A (verbatim copy). Modify routes through Path B.
3. **After templates:** offer custom mode creation in a loop ("Create another mode? yes/no").
4. **Before exit:** re-probe `%LOCALAPPDATA%/nova/modes/` for valid stems. If zero, the exit is blocked and the user is re-prompted (AC #11). Pre-existing `coding.yaml` from setup.bat satisfies this gate even if the user skipped every interactive prompt.

**Shipped templates today:** only `coding.yaml`. The wizard may also present a "study" option as a guided custom-mode creation (apps pre-suggested as Notion + Chrome) — this goes through Path B, not a verbatim copy, since no source file exists for it.

### Previous Story Learnings (from Stories 2.1 and 2.2)

1. **Rich UTF-8 in subprocess:** `sys.stdout.reconfigure(encoding="utf-8")` is already handled by `_force_utf8_stdout()` in `__main__.py`. The wizard inherits this.
2. **Console.input mocking:** Rich `Console.input(password=True)` delegates to `getpass`. For non-password prompts, `Console.input()` can be mocked directly in tests. Story 2.2 established this pattern.
3. **Atomic write with `os.replace`:** Pattern established in `settings_writer.py`. Reuse the same approach.
4. **YAML safety:** Use `yaml.safe_dump` for writing and `yaml.safe_load` for reading templates. The duplicate-key-rejecting loader is for runtime config loading only.
5. **Exit code 0 on success/skip:** Both Stories 2.1 and 2.2 return EXIT_OK (0) on success. The wizard step should not change this.
6. **Error translation:** All errors surfaced to the user must be product-grade (non-technical). No raw tracebacks. Log technical details at ERROR level.

### Integration Point in `__main__.py`

Current `main()` flow (after Story 2.2):
```
_force_utf8_stdout()
parse args
if --validate-only: validate and exit
_render_state_a(console)        # State A panel
data_dir = _resolve_data_dir()
if data_dir: run_api_key_step(console, data_dir)   # Story 2.2
return EXIT_OK
```

After Story 2.3, add between API key step and return:
```python
from nova.setup.mode_wizard import run_mode_wizard_step

# ... after API key step ...
if data_dir is not None:
    run_mode_wizard_step(console, data_dir)
else:
    console.print("[yellow]\u26a0[/yellow] LOCALAPPDATA not set. Skipping mode creation.")
```

### Project Structure Notes

- New file: `src/nova/setup/mode_wizard.py` — the wizard module (all implementation lives in a single module; no `src/nova/setup/main.py`, `src/nova/setup/wizard.py`, or similar — the entrypoint is `src/nova/setup/__main__.py` which calls into `mode_wizard`).
- Modified file: `src/nova/setup/__main__.py` — adds the `run_mode_wizard_step` call after `run_api_key_step`.
- New unit test files (one per concern, rather than a single combined file): `tests/unit/setup/test_mode_wizard_registry.py`, `test_mode_wizard_slugify.py`, `test_mode_wizard_writers.py`, `test_mode_wizard_flow.py`, `test_mode_wizard_isolation.py`.
- Integration tests: a new `TestModeWizardWiring` class added to `tests/integration/test_setup_wizard.py`.
- AST guard: new file `tests/unit/setup/test_mode_wizard_isolation.py` (mirrors the `core/paths.py` isolation pattern from Story 2.1 but lives under `tests/unit/setup/` because the guarded module is under `src/nova/setup/`, not `core/`).

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Epic 2, Story 2.3]
- [Source: _bmad-output/planning-artifacts/architecture.md — Mode Configuration, Setup Wizard, Code Structure]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Mode Creation Flows, First-Run Setup, CLI Interaction Patterns]
- [Source: _bmad-output/planning-artifacts/prd.md — FR3, FR4, FR11, Workspace Mode Configuration UX]
- [Source: docs/config-schemas.md — Mode schema definition]
- [Source: config/modes/coding.yaml — Shipped template reference]
- [Source: src/nova/core/config.py — ModeConfig, AppConfig, _is_valid_mode_stem, _load_modes]
- [Source: src/nova/setup/settings_writer.py — Atomic write pattern reference]
- [Source: src/nova/setup/api_key.py — Interactive flow pattern reference]
- [Source: src/nova/setup/__main__.py — Integration point, _resolve_data_dir, _render_state_a]

### Review Findings (2026-04-17)

Three-layer adversarial review (Blind Hunter, Edge Case Hunter, Acceptance Auditor) on Story 2.3. 26 findings raised; 11 dismissed as noise; 5 deferred; 10 actionable patches.

**High severity (crash or data loss):**

- [x] [Review][Patch] `_modify_template` silently overwrites a user-edited target file [src/nova/setup/mode_wizard.py:583] — fixed: explicit overwrite confirmation via `_confirm` before `write_mode_file`; existing bytes preserved when user declines. Regression test `test_modify_requires_confirmation_to_overwrite`.
- [x] [Review][Patch] `yaml.YAMLError` from `write_mode_file` crashes past the caller's `except OSError` handler [src/nova/setup/mode_wizard.py:463, 584] — fixed: both call sites now `except (OSError, yaml.YAMLError)`.
- [x] [Review][Patch] `_existing_valid_mode_stems` `iterdir()` crashes with traceback if `modes_dir` disappears mid-loop [src/nova/setup/mode_wizard.py:274-292] — fixed: `iterdir()` wrapped in `try/except OSError` returning `[]`; per-entry `is_file()` call also guarded.
- [x] [Review][Patch] `resolve_app` crashes with traceback if `shutil.which` raises `OSError` or `ValueError` [src/nova/setup/mode_wizard.py:106] — fixed: wrapped in `try/except (OSError, ValueError)` returning `None`. Tests `TestShutilWhichCrashGuard` cover both exception classes.

**Medium severity (spec compliance + correctness):**

- [x] [Review][Patch] `_modify_template` discards template's existing apps/folders/urls [src/nova/setup/mode_wizard.py:537-593] — fixed: `_collect_apps` and `_collect_optional_list` now accept `initial=` and display seeded entries as `✓ (from template)`. Regression test `test_modify_seeds_with_template_apps_folders_urls`.
- [x] [Review][Patch] **Second-pass fix — "modify on already-installed" was seeding from shipped defaults, not the user's current file (data-loss risk).** The first-pass P5 patch seeded `_modify_template` unconditionally from `template_data` (shipped template). That meant: user accepts a template → hand-edits it → re-runs setup → picks "modify" → their customizations are silently replaced by shipped defaults. Fixed: `_modify_template` now probes `modes/{stem}.yaml`; when present, seeds from the user's current file via `_load_template(target)`, only falling back to `template_data` when the target doesn't exist or can't be parsed. Regression test `test_modify_edits_users_current_file_not_shipped_template`.
- [x] [Review][Patch] **Second-pass fix — modify forced `is_default: false` unconditionally (silent clearing of a user's intentional default-mode setting).** First-pass `_modify_template` hardcoded `"is_default": False` regardless of source. Fixed: `seed_is_default` is read from the seed source (user file when present, template otherwise) with non-bool fallback to `False` matching the loader's validation rule. Two regression tests: `test_modify_preserves_is_default_from_user_file` and `test_modify_preserves_is_default_true_in_shipped_template`.

**Third pass — residual verification-gap closures (2026-04-17):**

An audit of the first two passes found 6 claimed fixes with no test coverage. Each is now verified by a targeted test:

- [x] [Review][Patch] G1 — P2 (`yaml.YAMLError` catch at write_mode_file callers) now has two regression tests (custom path + modify path) proving a simulated `yaml.YAMLError` surfaces a clear message instead of a traceback.
- [x] [Review][Patch] G2 — P3 (`_existing_valid_mode_stems` iterdir OSError guard) now has a test that monkeypatches `Path.iterdir` on the modes directory to raise `OSError`, confirming the gate re-prompts cleanly and no traceback escapes.
- [x] [Review][Patch] G3 — P6 (cancel/skip/done in `_collect_optional_list`) now has four tests: cancel at folders aborts the mode, cancel at urls aborts the mode, `skip` keyword ends the list, `done` keyword ends the list with the entered values.
- [x] [Review][Patch] G4 — P7 (`_locate_shipped_templates` pyproject.toml anchor) now has two tests: returns `None` when no ancestor has both `config/modes` AND `pyproject.toml`; returns the directory when both are present.
- [x] [Review][Patch] G5 — P8 (`_offer_all_templates` skips invalid-stem templates) now has a test that ships both `coding.yaml` (valid) and `con.yaml` (reserved Windows name); only `coding` is offered, `con.yaml` is never written to the user modes dir.
- [x] [Review][Patch] G6 — H2-fallback (malformed user file silently fell back to shipped template). **Small implementation fix + test:** the fallback path now prints an explicit warning ("Your existing mode file at {target} could not be parsed. Starting from the shipped template values instead — your current file will be replaced on save.") before seeding from the template. Test `test_malformed_user_file_shows_warning_before_seeding_from_template` locks the warning in.
- [x] [Review][Patch] `_collect_optional_list` does not honor `cancel` or `skip` [src/nova/setup/mode_wizard.py:387-397] — fixed: both keywords (and `done` and blank line) now recognized; `cancel` returns `None` and propagates up as mode-abort. Callers updated to check for `None` return.
- [x] [Review][Patch] `_locate_shipped_templates` walks parents up to drive root [src/nova/setup/mode_wizard.py:248-270] — fixed: now requires the candidate's parent to also contain `pyproject.toml` as a project-root anchor, preventing pickup of adversarial `config/modes` directories in unrelated ancestors.

**Low severity (polish + test gaps):**

- [x] [Review][Patch] `_offer_template` does not validate shipped template stems [src/nova/setup/mode_wizard.py:486] — fixed: `_offer_all_templates` now calls `validate_mode_stem(template_path.stem)` before loading; invalid stems skipped with debug log.
- [x] [Review][Patch] `TestUxVoice::test_uses_semantic_symbols_only` uses `< 0x1F000` heuristic [tests/unit/setup/test_mode_wizard_flow.py:500] — fixed: replaced with an explicit non-ASCII whitelist (`✓`, `✗`, `⚠`, `—`); anything else above ASCII fails the test.
- [x] [Review][Patch] `TestValidateModeStemValid` missing positive `validate_mode_stem("1")` assertion [tests/unit/setup/test_mode_wizard_slugify.py] — fixed: `"1"` added to the valid-stem parametrize list with a contract-locking comment; removed from the reject list + dead `if invalid_stem == "1": return` branch.

**Deferred (pre-existing pattern or out of scope):**

- [x] [Review][Defer] `except BaseException` in temp-file cleanup is broad — but matches the `settings_writer.py` pattern (Story 2.2); change together or not at all
- [x] [Review][Defer] Temp file naming `{stem}.yaml.tmp` is not unique across concurrent wizard runs — out of scope per single-process CLI brief
- [x] [Review][Defer] `resolve_app` preserves user-input casing for PATH-resolved apps — by design per docstring
- [x] [Review][Defer] NFKC normalization of Unicode mode names (`Café` → `cafe`) — AC #7 explicitly specifies strict ASCII kebab-case; re-evaluate in a future UX story
- [x] [Review][Defer] `copy_template_verbatim` parent-directory-exists precondition not documented — minor doc touch-up

**Dismissed as noise:** TOCTOU on template-installed UX, `cancel`/`done` as app-name sentinels (design), `StopIteration` from MagicMock (test infra), BOM round-trip (loader uses `utf-8-sig`), case-sensitive `.YAML` suffix (shipped templates are lowercase), `_confirm` lax matching (convention), and several style notes that aren't bugs.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- One post-implementation test failure: the Story 2.2 AST guard (`test_no_key_interpolation.py`) flags any f-string / `console.print` call interpolating a variable named `raw`, `key`, or `api_key` anywhere under `src/nova/setup/`. Initial implementation used `raw` as the loop variable holding user-entered app names; renamed to `app_name` / `entry` so the guard stays strict across the whole setup module.
- Story 2.2 `TestFullWiringThroughMain::test_main_configures_key_end_to_end` hung after the wizard was wired into `main()` because the test's single-value `Console.input` lambda returns the API key string forever — the wizard's template-offer loop re-prompts on invalid choices and would never exit. Fixed by adding `monkeypatch.setattr("nova.setup.__main__.run_mode_wizard_step", lambda *_a, **_k: None)` to the affected Story 2.2 test; Story 2.3 integration tests in the new `TestModeWizardWiring` class cover the wizard path end-to-end.
- **Post-review fix: gate contract mismatch.** Initial `_enforce_minimum_mode_gate` prompted "Create a mode now? [y/n]" and let the user decline → warn → exit with zero modes. That violated AC #11's "must exist for the wizard to exit cleanly" rule. Replaced with a hard loop that always re-enters `_create_custom_mode`; the only escape is `KeyboardInterrupt`/EOF, absorbed by the outer handler. Tests rewritten: `test_empty_modes_dir_blocks_exit_until_mode_created` proves the loop persists across cancelled attempts; `test_gate_has_no_decline_escape` regression-guards the banned `"Create a mode now? [y/n]"` prompt; `test_gate_exits_cleanly_on_keyboard_interrupt` proves Ctrl+C is still a clean escape.

### Completion Notes List

- **Two persistence paths implemented per AC #3 / #3a.** `copy_template_verbatim` preserves comments and formatting byte-for-byte; `write_mode_file` uses `yaml.safe_dump(..., sort_keys=False)` for schema-conformant output. The flow layer chooses which path based on accept vs. modify vs. custom.
- **Zero-apps hard stop proven by test.** `TestZeroAppsHardStop::test_writer_never_called_with_zero_apps` patches `write_mode_file` and asserts it is never called when the user types `done` on an empty apps list followed by `cancel`. The confirmation summary and writer are unreachable from the zero-app state by construction.
- **At-least-one-mode exit gate — hard block, no decline escape.** `_existing_valid_mode_stems` scans `%LOCALAPPDATA%/nova/modes/` at both entry and exit; pre-existing `coding.yaml` from Story 2.1's setup.bat counts toward the gate. If zero valid modes on exit attempt, the gate loops — each iteration prints the requirement and re-enters `_create_custom_mode`. There is no "y/n decline" prompt; cancelling inside the custom-mode flow just loops back. The only escape is `KeyboardInterrupt` / `EOFError`, translated by the outer `try/except` into the "Mode setup interrupted" notice so setup can still be re-run later. Regression-guarded by `TestExitGate::test_gate_has_no_decline_escape` asserting `"Create a mode now? [y/n]"` never appears in output.
- **AST guard added** (`test_mode_wizard_isolation.py`) mirrors the Story 2.1 `core/paths.py` pattern — walks `ast.walk` for `ast.ImportFrom` and `ast.Call` nodes, blocks `nova.adapters.*`, `nova.systems.*`, `nova.ports.*` on both static and dynamic import paths.
- **Template source auto-discovery** (`_locate_shipped_templates`) walks up from the installed module path looking for a `config/modes` sibling of `src/`. Works both in the repo tree and in editable installs; returns `None` cleanly when no templates directory is reachable (wizard then falls back to custom-only mode).
- **UX compliance verified by test.** `TestUxVoice::test_uses_semantic_symbols_only` walks the collected Rich output and asserts no codepoint ≥ `0x1F000` (emoji range); `test_no_sycophantic_framing` asserts banned phrases ("How can I help you today", "I'd be happy to", "Great question", "Great!") never appear.
- **mypy not run locally.** Windows Application Control blocks `mypyc`-compiled mypy in this dev environment. Ruff (check + format) passes clean on all story files. CI runs mypy strict; any failure surfaces there.

### File List

- **New:** `src/nova/setup/mode_wizard.py`
- **New:** `tests/unit/setup/test_mode_wizard_registry.py`
- **New:** `tests/unit/setup/test_mode_wizard_slugify.py`
- **New:** `tests/unit/setup/test_mode_wizard_writers.py`
- **New:** `tests/unit/setup/test_mode_wizard_flow.py`
- **New:** `tests/unit/setup/test_mode_wizard_isolation.py`
- **Modified:** `src/nova/setup/__main__.py` (added `run_mode_wizard_step` call after `run_api_key_step`)
- **Modified:** `tests/unit/test_setup_main.py` (new wiring tests + patch existing Story 2.2 tests to mock `run_mode_wizard_step`)
- **Modified:** `tests/integration/test_setup_wizard.py` (added `TestModeWizardWiring` class + patched Story 2.2's `TestFullWiringThroughMain::test_main_configures_key_end_to_end` to mock the wizard step)
- **Modified:** `_bmad-output/implementation-artifacts/sprint-status.yaml` (status `ready-for-dev` → `in-progress` → `review`; `last_updated: 2026-04-17`)
