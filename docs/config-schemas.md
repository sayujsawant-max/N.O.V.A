# N.O.V.A. Config Schema Reference (T1)

**Status:** Pinned — this document is the single source of truth for the shape of N.O.V.A.'s YAML configuration files in T1. Every downstream story (1.6 config loader, 2.2 API key setup, 2.3 mode wizard, 3.6 mode restore, 4.2 exclusion boundary, 6.3–6.5 mode editing, 7.2 bluntness) reads against this contract.

**Change control:** Schema changes after this spike flow through a new numbered story — not silent edits. If architecture.md disagrees with this document, **this document wins** (see "Known divergences from architecture.md" below).

---

## Scope

Three YAML files define all user-owned config in T1:

| File | Purpose | Cardinality |
|------|---------|-------------|
| `modes/*.yaml` | Workspace-mode definitions (apps, folders, urls per mode) | One file per mode |
| `exclusions.yaml` | Sensitive-context exclusion list | Single file |
| `settings.yaml` | User preferences (API key, bluntness, briefing behavior) | Single file |

**Who reads these files:** `core/config.py` (Story 1.6) — **and nothing else.** No other module may parse YAML directly. All config access routes through the loader, which exposes immutable `NovaConfig` / `ModeConfig` / `ExclusionConfig` / `UserSettings` dataclasses.

---

## Shipped defaults vs. runtime location

Two locations, two roles:

| Location | Role | Content | Who writes |
|----------|------|---------|------------|
| `config/` (in the repo) | **Shipped defaults.** Ship-time artifact, read-only at runtime. | `modes/coding.yaml`, `exclusions.yaml`, `settings.defaults.yaml` | Developers (schema stewards) |
| `%LOCALAPPDATA%/nova/` | **Runtime user config.** Authoritative at runtime. | `modes/*.yaml`, `exclusions.yaml`, `settings.yaml`, `nova.db`, audit trail | First-run setup + user edits |

**First-run copy rules:**
- On first run, setup copies each shipped default from `config/` → `%LOCALAPPDATA%/nova/`.
- Setup **never overwrites** an existing file in the user data directory. If `%LOCALAPPDATA%/nova/exclusions.yaml` already exists, the shipped default is ignored.
- Post-install changes to shipped defaults do **not** silently recopy. They flow through explicit upgrade logic or migrations in a future story.
- `settings.defaults.yaml` is a special case: its name signals "defaults" and it is copied to `settings.yaml` (without the `.defaults` suffix) in the user data directory, with the user's `api_key` appended by first-run setup.

---

## Mode schema

**File pattern:** `%LOCALAPPDATA%/nova/modes/{mode_name}.yaml` — one file per mode. File name (kebab-case, file-system-safe) is the mode identifier.

**Loader behavior:** Reads all `.yaml` files in `modes/`. A mode file that fails validation is skipped with a `WARNING`-level log; **other modes still load**. Mode loading failures never halt startup.

### Fields

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `name` | string (non-empty) | Yes | — | Display name. May contain spaces. Loader normalizes to file-safe slug for storage. |
| `apps` | list (≥ 1 entry) | Yes | — | At least one entry required. See `apps[]` below. |
| `apps[].name` | string | Yes | — | **Display-only.** Used in briefings, status output, audit messages. **Never used for window/process matching.** |
| `apps[].executable` | string | Yes | — | Canonical identifier. Hands launches via this value; Eyes matches windows via this value. Resolved through `PATH` or absolute path. |
| `apps[].args` | list[str] | No | `[]` | Command-line args for launch. |
| `folders` | list[str] | No | `[]` | Absolute path strings. **Not auto-opened in T1.** Used by Eyes for context awareness only. |
| `urls` | list[str] | No | `[]` | URLs opened in the default browser on mode restore. |
| `is_default` | bool | No | `false` | See runtime semantic below. |

### `is_default` runtime semantic

`is_default: true` means two things at runtime:

1. **Startup briefing suggestion** — when no pattern-based suggestion applies (no recent session, no mode inferred from current context), the briefing card suggests this mode.
2. **Cold-start restore target** — when no prior session exists (State A/B handoff per UX spec), this mode is the default restore target.

**Tie-breaking:** If multiple modes set `is_default: true`, the first one loaded alphabetically by file name wins, with a `WARNING`-level log. Fix the conflict by editing the config.

### Validation rules

- `name`: required, non-empty string. Missing or empty → mode file skipped with warning.
- `apps`: required, at least one entry with `name` and `executable` present. Missing or empty → mode file skipped with warning.
- `apps[].executable`: required, non-empty string. Missing → mode file skipped with warning.
- `folders`, `urls`: optional lists. Missing → empty list. Non-list values → warning, empty list substituted.
- `is_default`: optional bool. Non-bool values → warning, `false` substituted.
- File name must be a valid file-system name (no `/`, `\`, `:`, etc.). The loader normalizes `name` to a file-safe slug for storage.
- **Unknown keys are ignored.** Forward-compatible for T2+ fields.

### Worked example

```yaml
# modes/coding.yaml
name: coding
apps:
  - name: VS Code
    executable: code
    args: []
  - name: Chrome
    executable: chrome
    args: ["--new-window"]
  - name: Windows Terminal
    executable: wt
    args: []
folders: []
urls: []
is_default: false
```

---

## Exclusions schema

**File:** `%LOCALAPPDATA%/nova/exclusions.yaml` — single file. Ships with sensible defaults. User-editable.

**Loader behavior:** Loaded once at startup. Missing file → both lists default to empty (no exclusions). Malformed YAML → hard startup error (not silent).

### Two lists, two matching semantics

| List | Matched against | Use when |
|------|-----------------|----------|
| `excluded_apps[].match` | window process / executable name (case-insensitive substring) | The app has a **stable process-name signature** (password managers, specific vaults, known binary names). |
| `excluded_title_patterns` | window title text (case-insensitive substring) | The concept is **title/content-level** and has no stable process (banking, credit cards, account-settings pages in any browser). |

### Fields

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `excluded_apps` | list of objects | No | `[]` | See entry fields below. |
| `excluded_apps[].name` | string | Yes (per entry) | — | Display-only (for settings-UI listing). Not used for matching. |
| `excluded_apps[].match` | string | Yes (per entry) | — | Case-insensitive substring match on window process / executable name. |
| `excluded_title_patterns` | list[str] | No | `[]` | Case-insensitive substring match on window title text. |

### Validation rules

- Both lists default to `[]` if omitted.
- Each `excluded_apps` entry missing `name` or `match` → entry skipped with warning; other entries still load.
- Eyes checks every captured window against both lists. Match on either → opaque event ("a protected app was active"). No app name, no window title, no content enters storage, cloud prompts, audit trail, or transparency display.
- **Unknown keys ignored** (forward-compatible).

### Why `Banking` is a title pattern, not an app

Banking is a title/content concept. A broad `bank` match on `excluded_apps` false-positives on any executable with "bank" in the name. `excluded_title_patterns` with `"banking"` correctly catches browser tabs and bank-app windows via their titles without the false-positive risk.

### Worked example

```yaml
excluded_apps:
  - name: 1Password
    match: 1password
  - name: KeePassXC
    match: keepassxc
  - name: Bitwarden
    match: bitwarden
excluded_title_patterns:
  - "password"
  - "banking"
  - "credit card"
  - "account settings"
```

---

## Settings schema

**File:** `%LOCALAPPDATA%/nova/settings.yaml` — single file. Created during first-run setup. User-editable.

**Loader behavior:** Loaded once at startup. Missing file → sensible defaults with `api_key = None` (triggers offline-local-only tier with one-time notice). Malformed YAML → hard startup error.

### Fields — complete T1 set

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `api_key` | string | No (schema) | `None` (absent) | Schema-optional. When present, must be non-empty. When absent or empty, startup falls back to offline-local-only tier with a one-time user notice. (Framing: "optional in the schema, required for cloud tier.") |
| `bluntness` | enum: `calm` \| `direct` | No | `direct` | T1 allowed values: `calm`, `direct`. `ruthless` is T2-deferred. Invalid values fall back to `direct` with `WARNING` log. |
| `skip_briefing_if_recent` | bool | No | `true` | Skip the briefing if the last session ended within `briefing_recency_threshold_minutes`. |
| `briefing_recency_threshold_minutes` | int | No | `60` | Threshold for `skip_briefing_if_recent`. |

### Excluded from T1 settings (intentional)

These fields MUST NOT appear in the settings schema, shipped defaults, or loader dataclass. Adding them is a schema regression.

| Field | Why excluded |
|-------|--------------|
| `telemetry_opt_in` | No telemetry infrastructure exists in T1. The project-context rule is "no telemetry without explicit opt-in." Shipping the field invites silent opt-in bugs later. When telemetry is introduced, it requires a dedicated story, explicit user flow, and migration. |
| `bluntness: ruthless` | T2-deferred. Ruthless is not mean — it is direct + loyal + pattern-aware. Pattern-detection maturity is a T2 prerequisite; shipping it in T1 would produce false-positive "pushbacks" on innocuous sessions and damage trust. |

### Why default `bluntness: direct`

Per [`ux-design-specification.md` line 1041](../_bmad-output/planning-artifacts/ux-design-specification.md) — the UX-spec-pinned default is Direct. Rationale, for agents who will otherwise re-litigate:

- N.O.V.A.'s target users are power users re-entering focused work quickly. Direct phrasing (`90 minutes on YouTube. Coding mode is still active.`) reads as clarity, not harshness.
- Direct ≠ Ruthless. Ruthless is gated behind T2 pattern maturity precisely because it requires earned context.
- Calm is a single `settings.yaml` edit away for users who prefer softer phrasing.
- Changing this default affects the Personality Doctrine (Epic 7.1) and the setup wizard's preamble (Epic 2.3). It is out of scope for schema work and must route through correct-course against the UX spec.

### Validation rules

- `api_key`: optional string. When present, must be non-empty; empty string is treated as absent (offline-local-only tier).
- `bluntness`: optional enum. Invalid enum values → fall back to `direct` with `WARNING` log.
- `skip_briefing_if_recent`: optional bool. Non-bool → `true` with warning.
- `briefing_recency_threshold_minutes`: optional int. Non-int or negative → `60` with warning.
- **Unknown keys ignored** (forward-compatible for T2+ settings).

### Worked example (shipped defaults)

```yaml
# settings.defaults.yaml — shipped in config/, copied to settings.yaml on first run.
# No api_key (first-run setup writes it). No telemetry_opt_in (excluded — see above).
bluntness: direct
skip_briefing_if_recent: true
briefing_recency_threshold_minutes: 60
```

---

## Cross-cutting rules

These rules apply to all three schemas and govern loader behavior.

| Rule | Behavior |
|------|----------|
| **Unknown keys → ignored** | Any key not listed in this document is silently accepted and discarded. Forward-compatible for T2+ fields. Never a warning for unknown keys — that would noise the log every time a new field ships. |
| **Invalid enum → fallback + warning** | Unrecognized enum values (e.g., `bluntness: chaotic`) fall back to the documented default with a `WARNING`-level log. |
| **Required field missing → error path differs per file** | Mode file: skip that file with warning; other modes still load. Settings or Exclusions file (singletons): if the file is malformed, hard startup error. If fields are missing individually, apply documented defaults. |
| **Malformed YAML → hard startup error** | Not a silent crash. Clear message identifying the file and the parse error. |
| **Startup error vs. silent crash** | The loader must never silently swallow a YAML parse failure. Any file-level parse failure produces a user-facing error with the file path and a parse-error summary. |
| **Missing `api_key` → offline-local-only tier** | A soft fallback, not an error. One-time notice shown to the user. |

---

## Loader contract preview (Story 1.6)

This spike pins the schema. Story 1.6 implements the loader with this contract:

- Location: `core/config.py`.
- Entry point: `load_config(data_dir: Path) -> NovaConfig`.
- `NovaConfig` is a frozen dataclass with exactly these fields:
  - `db_path: Path`
  - `data_dir: Path`
  - `modes: dict[str, ModeConfig]` — keyed by mode identifier (the slugified `name` or file stem).
  - `exclusions: ExclusionConfig`
  - `settings: UserSettings`
  - `api_key: str | None` — promoted out of `settings` for explicit handling in tier logic.
- `ModeConfig`, `ExclusionConfig`, `UserSettings` are frozen dataclasses mirroring the schemas above.
- Loaded **once** at startup. Immutable thereafter.
- **No other module reads YAML directly.** This is architecturally load-bearing — the "single YAML reader" rule in `project-context.md` depends on it.

---

## Known divergences from architecture.md

This document is the authoritative resolution for the following divergences between this schema and `_bmad-output/planning-artifacts/architecture.md`:

1. **`telemetry_opt_in` in the settings example** — `architecture.md` lines 498–511 show a settings YAML example that includes `telemetry_opt_in: false`. This document excludes the field from T1 (see "Excluded from T1 settings" above). **This document wins.** Architecture.md will be reconciled in a separate doc-hygiene pass.
2. **`bluntness: ruthless` in the settings example** — `architecture.md` lines 502–503 list `ruthless` as a valid enum. This document restricts T1 to `{calm, direct}` with `ruthless` T2-deferred. **This document wins.**
3. **`Banking` in `excluded_apps` default** — `architecture.md` lines 473–474 include a `Banking` entry with `match: bank` in `excluded_apps`. This document moves banking to `excluded_title_patterns` only and excludes the entry from `excluded_apps` defaults. Reason: broad `bank` process-name match false-positives; banking is a title/content concept. **This document wins.**

---

## Cross-references

| Consumer | Uses this schema for |
|----------|---------------------|
| Story 1.6 — Config Loader & Immutable NovaConfig | Implements the loader matching the contract in this document. |
| Story 2.2 — API Key Configuration | Writes `api_key` into the user's `settings.yaml`; must respect the optional-with-offline-fallback rule. |
| Story 2.3 — Guided Mode Creation Wizard | Generates mode files matching the mode schema. |
| Story 3.6 — Mode Restore & App Launching | Reads `ModeConfig`; launches via `apps[].executable` (never `apps[].name`). |
| Story 4.1 — Eyes Win32 Context Capture | Matches windows via `apps[].executable` and the exclusion rules in this document. |
| Story 4.2 — Exclusion Boundary at Capture Layer | Applies `excluded_apps[].match` vs. process name and `excluded_title_patterns` vs. window title. |
| Story 6.3–6.5 — Mode Editing | Mutates mode files; must preserve the field set and validation rules in this document. |
| Story 7.2 — Configurable Bluntness Levels | Reads `settings.bluntness`; must honor `{calm, direct}` T1 enum and fallback-to-direct-on-invalid rule. |

---

*Document authored in Story 1.0 (schema spike). Do not edit fields without a follow-on story.*
