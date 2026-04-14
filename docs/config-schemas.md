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
- On first run, setup copies each shipped default from `config/` → `%LOCALAPPDATA%/nova/` using these exact mappings:
  - `config/modes/coding.yaml` → `%LOCALAPPDATA%/nova/modes/coding.yaml`
  - `config/exclusions.yaml` → `%LOCALAPPDATA%/nova/exclusions.yaml`
  - `config/settings.defaults.yaml` → `%LOCALAPPDATA%/nova/settings.yaml` *(name change during copy — the `.defaults` suffix is ship-time-only)*
- Setup **never overwrites** an existing file in the user data directory. If `%LOCALAPPDATA%/nova/exclusions.yaml` already exists, the shipped default is ignored.
- Setup must **rename on copy** for `settings.defaults.yaml`. A file named `settings.defaults.yaml` sitting directly in the user data directory is invisible to the loader (loader only reads `settings.yaml`); if the user manually drops the shipped defaults file in without the rename, the loader silently loads "no settings file found → apply all defaults, `api_key = None`" instead of the user's expected values. The setup script handles the rename; users who edit shipped defaults post-install should edit their `%LOCALAPPDATA%/nova/settings.yaml` directly.
- Post-install changes to shipped defaults do **not** silently recopy. They flow through explicit upgrade logic or migrations in a future story.
- The user's `api_key` is appended to `%LOCALAPPDATA%/nova/settings.yaml` by first-run setup after the copy step; it is never present in `config/settings.defaults.yaml`.

---

## Mode schema

**File pattern:** `%LOCALAPPDATA%/nova/modes/{mode_name}.yaml` — one file per mode. **The file stem is the canonical mode identifier.** The file stem is the dictionary key in `NovaConfig.modes` and the value the user types to invoke the mode (`nova mode coding`). File stem must be kebab-case and file-system-safe. The `name:` field inside the YAML is display-only (used in briefings, status output, audit) and is **not** slugified or used as an identifier by the loader.

**Loader behavior:** Reads all files matching the **case-sensitive glob `*.yaml`** in `modes/`. Case variants (`*.YAML`), alternate extensions (`*.yml`), and editor swap/backup files (`*.yaml.bak`, `*.yaml~`) are ignored. Any file whose YAML fails to parse, or whose validated content is invalid, is **skipped at the file level** with a `WARNING`-level log and a post-briefing tier-style notice; other modes still load. Mode loading failures never halt startup.

### Fields

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `name` | string (non-empty) | Yes | — | **Display-only.** Shown in briefings, status output, audit. May contain spaces. **Never used as an identifier** — the loader does not slugify this field. The mode identifier is the file stem. |
| `apps` | list (≥ 1 entry) | Yes | — | At least one entry required. See `apps[]` below. |
| `apps[].name` | string | Yes | — | **Display-only.** Used in briefings, status output, audit messages. **Never used for window/process matching.** |
| `apps[].executable` | string | Yes | — | Canonical identifier. Hands launches via this value; Eyes matches windows via this value. **Normalization:** comparison is case-insensitive; trailing `.exe` is optional and stripped during comparison (so `code`, `Code`, `code.exe`, and `CODE.EXE` all match the same process). Bare names are resolved through `PATH`; absolute paths are used verbatim with no PATH lookup. |
| `apps[].args` | list[str] | No | `[]` | Command-line args for launch. |
| `folders` | list[str] | No | `[]` | **Absolute path strings, required if present.** Relative paths, null entries, and non-string entries are dropped with a `WARNING` log; the remaining valid entries load. **Not auto-opened in T1.** Used by Eyes for context awareness only. |
| `urls` | list[str] | No | `[]` | URLs opened in the default browser on mode restore. **Scheme allowlist: `http://` and `https://` only.** Entries with any other scheme (`file:`, `javascript:`, `data:`, bare paths, empty strings) are dropped with a `WARNING` log — this is a security boundary, not a convenience check. |
| `is_default` | bool | No | `false` | See runtime semantic below. |

### `is_default` runtime semantic

`is_default: true` means two things at runtime:

1. **Startup briefing suggestion** — when no pattern-based suggestion applies (no recent session, no mode inferred from current context), the briefing card suggests this mode.
2. **Cold-start restore target** — when no prior session exists (State A/B handoff per UX spec), this mode is the default restore target.

**Tie-breaking:** If multiple modes set `is_default: true`, the first one by **alphabetically-sorted file stem** wins, with a `WARNING` log. Fix the conflict by editing the config. Modes that failed validation (and were therefore skipped by the loader) are excluded from the tie-break — only successfully-loaded candidates are considered.

**Zero-case (no mode sets `is_default: true`):** The loader does not fabricate a default. Briefing suggestion falls back to "no suggested mode" (briefing card omits the suggestion line). Cold-start restore target falls back to the **first alphabetically-sorted file stem** among successfully-loaded modes, with a `WARNING` log ("no default mode configured — falling back to `<stem>`"). If zero modes are loaded at all, cold-start restore is a no-op and the briefing renders in State A.

### Validation rules

File-level failures (entire mode file is skipped, with a `WARNING` log and a post-briefing tier-style notice):
- **YAML parse error** — malformed YAML in a mode file is a file-skip, *not* a hard startup error. (The cross-cutting "malformed YAML → hard startup error" rule applies to singletons — `exclusions.yaml`, `settings.yaml` — not to the per-mode collection.)
- **Top-level is not a mapping** — e.g., file parses to a list or `null`. File skipped.
- **`name` missing or empty.**
- **`apps` missing, not a list, or has zero valid entries after entry-level validation below.**

Entry-level failures in `apps[]` (the specific entry is dropped; other entries and the mode itself still load, matching the entry-skip semantic used by `exclusions.yaml`):
- `apps[].name` missing or empty → entry dropped with warning.
- `apps[].executable` missing or empty → entry dropped with warning.
- `apps[].args` present but not a list of strings → `args` substituted with `[]`, warning logged (entry still loads).

Field-level graceful handling:
- `folders` absent → `[]`. Non-list → warning, `[]` substituted. List containing non-absolute or non-string entries → those entries dropped with warning; remaining valid entries load.
- `urls` absent → `[]`. Non-list → warning, `[]` substituted. List entries with disallowed schemes or malformed values → those entries dropped with warning; remaining valid entries load.
- `is_default` absent → `false`. Non-bool → warning, `false` substituted.
- File stem must be a valid file-system name (no `/`, `\`, `:`, etc., no reserved Windows names — enforced at creation time by Story 2.3's mode wizard; Story 1.6's loader surfaces a clear error if an invalid stem is encountered on disk).
- **Unknown keys are ignored at every depth** (root, `apps[]` entries, and any future nested structures). Forward-compatible for T2+ fields.

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

**Loader behavior:** Loaded once at startup. **Missing file → both lists default to empty AND a `WARNING`-level log plus a post-briefing tier-style notice: "exclusions.yaml not found — zero exclusion protection." The loader does not halt and does not auto-recreate the file; the notice exists because privacy-sensitive silent defaults are a trap.** Malformed YAML → hard startup error (not silent) with the file path and parse-error summary.

### Two lists, two matching semantics

| List | Matched against | Use when |
|------|-----------------|----------|
| `excluded_apps[].match` | window process / executable name (case-insensitive substring) | The app has a **stable process-name signature** (password managers, specific vaults, known binary names). |
| `excluded_title_patterns` | window title text (case-insensitive substring) | The concept is **title/content-level** and has no stable process (banking, credit cards, account-settings pages in any browser). |

### Fields

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `excluded_apps` | list of objects | No | `[]` | See entry fields below. |
| `excluded_apps[].name` | string (non-empty) | Yes (per entry) | — | Display-only (for settings-UI listing). Not used for matching. |
| `excluded_apps[].match` | string (non-empty) | Yes (per entry) | — | Case-insensitive substring match on window process / executable name. **Empty string is rejected** (an empty substring matches every window — silent privacy blackout). The same `.exe` normalization that applies to `apps[].executable` applies here. |
| `excluded_title_patterns` | list[str] (each non-empty) | No | `[]` | Case-insensitive substring match on window title text. **Empty strings within the list are rejected** for the same reason. |

YAML quoting in this file is cosmetic: `"banking"` and `banking` are equivalent. Matching is **always literal substring**, never regex or glob — quote style does not change semantics.

### Validation rules

- Both lists default to `[]` if omitted.
- Each `excluded_apps` entry missing `name` or `match`, or with either set to an empty/whitespace-only string, → entry skipped with warning; other entries still load.
- Each `excluded_title_patterns` entry that is empty or whitespace-only → entry skipped with warning; other entries still load.
- Eyes checks every captured window against both lists. Match on either → opaque event ("a protected app was active"). No app name, no window title, no content enters storage, cloud prompts, audit trail, or transparency display.
- **Unknown keys ignored at every depth** (root-level and within `excluded_apps[]` entries).

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
| `api_key` | string | No (schema) | `None` (absent) | Schema-optional. **Canonical rule: empty string, whitespace-only string, and absent field are all treated as "no key configured" → offline-local-only tier with a one-time user notice.** No separate "present but empty" error path — empty is absent. (Framing: "optional in the schema, required for cloud tier.") |
| `bluntness` | enum: `calm` \| `direct` | No | `direct` | T1 allowed values: `calm`, `direct`. `ruthless` is T2-deferred. Invalid values fall back to `direct` with `WARNING` log. |
| `skip_briefing_if_recent` | bool | No | `true` | Skip the briefing if the last session ended within `briefing_recency_threshold_minutes`. |
| `briefing_recency_threshold_minutes` | int | No | `60` | Threshold in minutes for `skip_briefing_if_recent`. **`0` means never skip (always brief)** — the conservative interpretation. Negative values and non-ints fall back to `60` with a `WARNING` log. |

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

- `api_key`: optional string. Absent, empty string, and whitespace-only string are all treated identically as "no key configured" → offline-local-only tier with one-time user notice. (Canonical single-path rule: empty = absent. There is no second "present-but-empty is invalid" error path.)
- `bluntness`: optional enum. Invalid enum values → fall back to `direct` with `WARNING` log.
- `skip_briefing_if_recent`: optional bool. Non-bool → `true` with warning.
- `briefing_recency_threshold_minutes`: optional int. `0` is valid and means "never skip the briefing". Negative values and non-ints fall back to `60` with a `WARNING` log. YAML floats (e.g., `59.5`) are treated as non-int.
- **Unknown keys ignored at every depth** (forward-compatible for T2+ settings).

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
| **Unknown keys → ignored at every depth** | Any key not listed in this document is silently accepted and discarded — at the document root, inside `apps[]` entries, inside `excluded_apps[]` entries, and in any future nested structure. Forward-compatible for T2+ fields. Never a warning for unknown keys; that would noise the log every time a new field ships. |
| **Invalid enum → fallback + warning** | Unrecognized enum values (e.g., `bluntness: chaotic`) fall back to the documented default with a `WARNING`-level log. |
| **UTF-8 BOM tolerated** | Files that begin with a UTF-8 BOM (`\xef\xbb\xbf`) — which Windows Notepad writes by default — are read successfully; the BOM is stripped before YAML parsing. A BOM is **not** a malformed-YAML condition. |
| **Top-level `null` YAML → treat as empty** | A file that parses to `None` (e.g., completely whitespace, `null` literal, or `# only comments`) is treated as "empty document": documented defaults apply and a `WARNING` log is emitted ("<file> is empty — applying defaults"). Not a hard error. |
| **Top-level shape mismatch (non-mapping root) → malformed for singletons, skip for modes** | A file whose root is a list, scalar, or otherwise not a mapping is treated as malformed: hard startup error for `settings.yaml` and `exclusions.yaml` (singletons), file-skip-with-warning for a `modes/*.yaml` (per-mode collection). |
| **Malformed YAML rule split: singletons vs. mode collection** | Parse error or top-level shape mismatch in `settings.yaml` or `exclusions.yaml` → hard startup error with file path and parse-error summary. Same failure in any `modes/*.yaml` → skip that file with `WARNING` log and post-briefing tier-style notice; other modes still load; startup continues. |
| **Required field missing → error path differs per file** | Mode file: skip that mode file; other modes still load. Settings or Exclusions: apply documented per-field defaults — the file itself is not rejected just because one field is missing. |
| **Warning visibility: log + post-briefing tier-style notice** | `WARNING`-level config issues that persist across the session (missing exclusions file, skipped mode files, zero-default fallback, invalid-enum fallback) route to both the log file AND a terse post-briefing tier-style notice rendered by Skin — the same rail used for tier transitions and operational status. Routing follows the "operational output bypasses Voice" architecture rule: notices are status lines, not personality-bearing prose. |
| **Missing `api_key` → offline-local-only tier** | A soft fallback, not an error. One-time notice shown to the user via the same tier-style rail. |

---

## Loader contract preview (Story 1.6)

This spike pins the schema. Story 1.6 implements the loader with this contract:

- Location: `core/config.py`.
- Entry point: `load_config(data_dir: Path) -> NovaConfig`.
- `NovaConfig` is a frozen dataclass with exactly these fields:
  - `db_path: Path`
  - `data_dir: Path`
  - `modes: dict[str, ModeConfig]` — **keyed by file stem** (the canonical mode identifier per the Mode schema §File pattern section). The `name:` field inside each YAML is display-only and is not used as a dict key anywhere in the loader.
  - `exclusions: ExclusionConfig`
  - `settings: UserSettings`
  - `api_key: str | None` — promoted out of `settings` for explicit handling in tier logic. Empty-string and whitespace-only API keys normalize to `None` at load time, so tier logic only ever sees `str | None` with no "present-but-useless" third state.
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
