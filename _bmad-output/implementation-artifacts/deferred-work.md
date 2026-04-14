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
