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
