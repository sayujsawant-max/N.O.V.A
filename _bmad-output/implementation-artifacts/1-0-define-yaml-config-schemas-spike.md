# Story 1.0: Define YAML Config Schemas (Spike)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer (or AI agent implementing future epics),
I want the YAML config schemas for modes, exclusions, and settings pinned with exact field definitions, validation rules, and defaults,
so that every epic from 2 onwards has a single source of truth for config shape and no schema drift occurs across agents.

## Acceptance Criteria

1. A schema reference document exists at `docs/config-schemas.md` pinning the three T1 YAML schemas (mode, exclusions, settings) with: field list, required vs. optional, types, defaults, validation rules, and one worked example per schema.
2. **Mode schema** (`modes/*.yaml`) is documented with exactly these fields:
   - `name` — required string (non-empty). Display name; may contain spaces.
   - `apps` — required list (≥ 1 entry). Each entry: `name` (required string, **display-only** — used in briefings, status output, audit messages; never used for window/process matching), `executable` (required string — this is the canonical identifier used by Hands/Eyes for launch and window matching), `args` (optional list[str], default `[]`).
   - `folders` — optional list[str] of absolute path strings (default `[]`).
   - `urls` — optional list[str] of URL strings (default `[]`).
   - `is_default` — optional boolean (default `false`). **Runtime semantic:** `is_default: true` means this mode is suggested during startup briefing when no pattern-based suggestion applies, and it is used as the cold-start restore target when no prior session exists (State A/B handoff). At most one mode should be default; on tie, first loaded alphabetically wins with a logged warning.
3. **Exclusions schema** (`exclusions.yaml`) is documented with exactly these fields:
   - `excluded_apps` — list of objects, each with `name` (string, display only) and `match` (string, case-insensitive substring match against **window process name / executable**). Use this for apps with stable process-name signatures (password managers). Default `[]` if omitted.
   - `excluded_title_patterns` — list[str], case-insensitive substring match against **window title text**. Use this for title-or-content-level concepts (banking, credit cards, account settings) that don't have a stable process name. Default `[]` if omitted.
4. **Settings schema** (`settings.yaml`) is documented with exactly these fields and **NO OTHERS**:
   - `api_key` — **optional string.** Absent or empty → startup falls back to offline-local-only tier with a one-time user notice. When present, must be non-empty. (The field is "optional in the schema, required for cloud tier" — this framing is deliberate and is the single canonical statement; earlier drafts calling it `required` were internally inconsistent with the offline-tier fallback rule.)
   - `bluntness` — optional enum; T1 allowed values: `calm`, `direct` (default `direct`, per `ux-design-specification.md` line 1041). `ruthless` is **deferred to T2** and MUST NOT appear as a valid T1 value. Invalid values fall back to `direct` with logged warning. *(Rationale for the default — schema doc MUST capture this verbatim: Direct is the UX-spec-pinned default because N.O.V.A. targets power users re-entering work quickly; Direct is clear, not harsh. Calm is one `settings.yaml` edit away for users who prefer softer phrasing. Ruthless is T2-gated behind pattern-detection maturity.)*
   - `skip_briefing_if_recent` — optional boolean (default `true`).
   - `briefing_recency_threshold_minutes` — optional integer (default `60`).
5. **`telemetry_opt_in` MUST NOT be included in the settings schema.** The architecture.md example shows it, but this story overrides that: no telemetry infrastructure exists in T1, and the project-context rule is "no telemetry without explicit opt-in." Schema reference doc must explicitly call out this exclusion with rationale.
6. A top-level `config/` directory exists in the repo containing shipped defaults:
   - `config/modes/coding.yaml` — workspace-mode template (VS Code + Chrome + Windows Terminal), clearly labelled as a workspace-mode template, not a project starter template.
   - `config/exclusions.yaml` — sensible defaults: `excluded_apps` contains password managers only (1Password, KeePassXC, Bitwarden) — entries with stable process-name signatures. `excluded_title_patterns` contains title/content concepts: `password`, `banking`, `credit card`, `account settings`. **`Banking` MUST NOT appear in `excluded_apps`** — banking is a title/content concept, not a stable process name, and a broad `bank` process-name match false-positives on unrelated executables.
   - `config/settings.defaults.yaml` — all optional settings at their defaults; **no `api_key` field** (first-run setup adds it to the real `settings.yaml` in `%LOCALAPPDATA%/nova/`).
7. Documented validation rules (in `docs/config-schemas.md`):
   - Required fields absent → clear startup error (not silent crash).
   - Optional fields absent → documented defaults applied.
   - Invalid enum values → fall back to default with `WARNING`-level log.
   - Invalid mode file → skip that file with warning; other modes still load.
   - Unknown keys in any config file → ignored (forward compatibility for T2+).
8. Schema reference doc clarifies the shipped-defaults-vs-runtime split: `config/` in repo is shipped defaults only; runtime user config lives in `%LOCALAPPDATA%/nova/` and is the authoritative location the config loader (Story 1.6) reads from.
9. Each shipped YAML file parses cleanly with **PyYAML's** `yaml.safe_load` (verified manually by running `python -c "import yaml; yaml.safe_load(open('config/<file>.yaml'))"` for each file). PyYAML is **not** part of the standard library; see Dev Notes for the ad-hoc install guidance. No Python project setup is created in this spike — scaffolding is Story 1.1.

## Tasks / Subtasks

- [x] **Task 1: Create `config/` directory with shipped defaults** (AC: #6, #9)
  - [x] Create `config/modes/coding.yaml` with `name`, `apps` (VS Code, Chrome `--new-window`, Windows Terminal `wt`), empty `folders`, empty `urls`, `is_default: false`. Include inline comments mirroring the schema doc.
  - [x] Create `config/exclusions.yaml` with `excluded_apps`: 1Password, KeePassXC, Bitwarden **only** (password managers with stable process names — **do NOT include a `Banking` entry here**). `excluded_title_patterns`: `password`, `banking`, `credit card`, `account settings`.
  - [x] Create `config/settings.defaults.yaml` with `bluntness: direct`, `skip_briefing_if_recent: true`, `briefing_recency_threshold_minutes: 60` — **no `api_key`, no `telemetry_opt_in`**. (`api_key` is schema-optional; absent in the shipped defaults file by design — first-run setup in Story 2.2 writes the user's real `settings.yaml` with the key.)
  - [x] Verify each file parses cleanly: `python -c "import yaml; print(yaml.safe_load(open('config/<path>')))"`. PyYAML is not stdlib — see Dev Notes for install guidance.
- [x] **Task 2: Author `docs/config-schemas.md` reference doc** (AC: #1, #2, #3, #4, #5, #7, #8)
  - [x] Top section: scope, how this doc is used (by config loader in Story 1.6, by setup wizard in Story 2.2/2.3, by every agent touching config).
  - [x] Section: "Shipped defaults vs. runtime location" — `config/` (repo) vs. `%LOCALAPPDATA%/nova/` (runtime), first-run copy rules (never overwrite existing user files).
  - [x] Section: Mode schema — field table, validation rules, file-name slug normalization, worked example. **Must include**: `is_default: true` runtime semantic (suggested during startup briefing when no pattern-based suggestion applies; used for cold-start restore when no prior session exists), and `apps[].name` is display-only while window/process matching uses `executable`.
  - [x] Section: Exclusions schema — field table, validation rules, worked example. **Must include**: the `excluded_apps` vs. `excluded_title_patterns` selection heuristic (process-name-stable concepts vs. title/content concepts), and explicit note that `Banking` belongs in title patterns only.
  - [x] Section: Settings schema — field table, validation rules, worked example. **Explicit callouts**: `api_key` is schema-optional with offline-local-only fallback when absent/empty (single canonical wording — no "required" language); `telemetry_opt_in` is NOT part of T1 settings (with rationale); `ruthless` bluntness is NOT a T1 value (deferred to T2); default `bluntness: direct` rationale per UX spec line 1041.
  - [x] Section: Cross-cutting rules — unknown keys ignored, invalid-file-skip behavior, enum fallback-with-warning, startup-error-vs-silent-crash contract.
  - [x] Section: Loader contract preview — who reads YAML (only `core/config.py`), immutable `NovaConfig` shape, loaded once at startup. Point forward to Story 1.6 for implementation.
- [x] **Task 3: Schema freeze + cross-references** (AC: #1, #5)
  - [x] Reconcile with `architecture.md` lines 426–525: flag the `telemetry_opt_in` divergence in the reference doc (architecture example is stale on this point — schema doc wins). Also flagged: `ruthless` bluntness and `Banking` in `excluded_apps` — see "Known divergences from architecture.md" in the reference doc.
  - [x] Reconcile with `architecture.md` Story 1.6 description: ensure NovaConfig target shape (`db_path`, `data_dir`, `modes`, `exclusions`, `settings`, `api_key`) is compatible with the pinned schemas. Covered in "Loader contract preview" section.
  - [x] Add a "Change control" note: schema changes after this spike must flow through a new numbered migration/story — not silent edits. Captured in the doc's header and footer.

## Dev Notes

### Story Type: Spike (Documentation + Example Configs)

This is a **spike story**. Deliverables are:
1. A reference doc (`docs/config-schemas.md`) — the single source of truth all future agents cite when touching config.
2. Three shipped-default YAML files under `config/` — real files the scaffolding story (1.1) and config loader (1.6) will depend on.

**No Python code, no tests, no `pyproject.toml` changes in this story.** Scaffolding is Story 1.1; config loader implementation is Story 1.6. Do not pre-implement either.

**Scope guard (hard stop):** Deliverables are `docs/config-schemas.md` and three YAML files under `config/`. **If you write any `.py` file, you are out of scope — stop and delete it.** No `pyproject.toml`, no `requirements.txt`, no `src/`, no `tests/`, no `conftest.py`.

**PyYAML availability for AC #9 manual verification:** PyYAML is not in the Python standard library, and no project-level Python environment exists yet (Story 1.1 creates it). For this one-time manual parse check, run `pip install pyyaml` in any available Python environment (a throwaway venv, the system Python, or skip — it's a nice-to-have sanity check, not a ship-blocking gate). **Do not add PyYAML to a `pyproject.toml` or `requirements*.txt` in this story** — that is Story 1.1's job, and the real dependency is already planned there (anthropic SDK, pywin32, psutil, Rich, pytest, etc. — PyYAML will be added in 1.1 alongside them).

### Why this story exists (prevent schema drift)

Multiple downstream stories depend on exact YAML shape: 1.6 (config loader), 2.2 (API key config), 2.3 (guided mode wizard), 3.6 (mode restore), 4.2 (exclusion boundary), 7.2 (bluntness levels), 6.3–6.5 (mode creation/editing). Without a pinned schema, each agent will reinvent fields and the project fractures. This spike locks the shape.

### Critical constraints and gotchas

- **`telemetry_opt_in` exclusion is intentional and overrides the architecture example.** The architecture.md YAML sample at lines 493–511 shows `telemetry_opt_in: false`, but the epic AC explicitly says to exclude it. Reason: no telemetry infrastructure exists in T1, and the project-context rule is "no telemetry without explicit opt-in." Including the field invites silent opt-in bugs later. Call this out explicitly in the schema doc.
- **T1 bluntness values are `calm` and `direct` only.** The architecture example lists `ruthless` — that value is **T2-deferred**. Schema doc must state T1 enum is `{calm, direct}`; invalid values fall back to `direct` with logged warning.
- **Default bluntness is `direct` — this is UX-spec-pinned, not a reviewer preference.** `ux-design-specification.md` line 1041 explicitly sets the T1 default to Direct. The schema doc must include a one-paragraph rationale so future agents don't re-litigate: Direct is clear without being harsh (Ruthless behavior is gated behind T2 pattern maturity); N.O.V.A.'s target users are power users re-entering work and prefer terse feedback; Calm is a single `settings.yaml` edit away. Do not change the default in this spike — any change must go through correct-course against the UX spec and propagate to Epic 7.1.
- **`settings.defaults.yaml` MUST NOT contain `api_key`.** The real `settings.yaml` in `%LOCALAPPDATA%/nova/` is written by first-run setup (Story 2.2). Shipping a defaults file with an empty or placeholder `api_key` would either prevent offline-local-only tier detection or commit a fake secret to the repo.
- **File-name-as-mode-identity convention.** Mode YAML files are keyed by file name (kebab-case, file-system-safe). `config/modes/coding.yaml` → mode `coding`. The loader normalizes the `name` field to a file-safe slug for storage. Call this out in the schema doc.
- **Unknown keys are ignored, not rejected.** Forward-compatibility for T2+ settings (e.g., future `voice_profile`, `ritual_timings`). Invalid-value warnings are different from unknown-key tolerance — keep them separate in the doc.
- **Mode file validation failures skip the file, they do not halt startup.** One broken `modes/*.yaml` must not prevent other modes from loading. Settings and exclusions, being singletons, do halt on hard validation failures (missing required `api_key` is a soft failure that triggers offline tier; malformed YAML is a hard startup error).

### Repo shape at time of this story

The repo is **not yet scaffolded** — Story 1.1 does that. At execution time the repo contains `_bmad/`, `_bmad-output/`, `design-artifacts/`, `docs/` (may be empty), and this story file. Create `config/` and `docs/config-schemas.md` at the repo root. Do not create `src/`, `pyproject.toml`, `tests/`, or any Python package — that's 1.1's job.

### Project Structure Notes

- `config/` lives at **repo root**, not inside `src/`. This matches the shipped-defaults vs. user-data separation documented in architecture.md line 280.
- `docs/config-schemas.md` — new file. If `docs/` does not yet exist, create it.
- No edits to `architecture.md` in this story. The schema reference doc is the authoritative resolution for the `telemetry_opt_in` and `ruthless` divergences; architecture.md can be cleaned up in a separate doc-hygiene pass.

### Testing standards summary

No automated tests in this spike (no Python project exists yet). Manual verification per AC #9: each shipped YAML parses with `yaml.safe_load`. Story 1.6 will add real unit tests for the loader; this spike's job is to pin the shape such that those tests have an unambiguous target.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.0: Define YAML Config Schemas (Spike)] — canonical acceptance criteria.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 3: Data Schema] — lines 415–525, file-based config vs. SQLite split, schema examples (noting `telemetry_opt_in` divergence).
- [Source: _bmad-output/planning-artifacts/architecture.md#Config loading contract] — lines 521–525, loader constraints (single reader, immutable, load-once).
- [Source: _bmad-output/project-context.md#Critical Implementation Rules] — `api_key` lives in `settings.yaml` in user data dir; `Config module is the single YAML reader`; `No telemetry without opt-in`.
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6: Config Loader & Immutable NovaConfig] — downstream consumer; schemas here must match the `NovaConfig`/`ModeConfig`/`ExclusionConfig`/`UserSettings` shape expected there.
- [Source: _bmad-output/planning-artifacts/architecture.md#Shipped defaults vs. runtime user data] — line 280, `config/` (in repo) vs. `%LOCALAPPDATA%/nova/` (runtime).

## Review Findings

Produced by the bmad-code-review workflow on 2026-04-14. Three parallel adversarial review layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) ran against the four Story 1.0 deliverables. Acceptance Auditor: **Approve** — all 9 ACs satisfied. Adversarial layers surfaced edge cases in the schema contract that this spike should pin (rather than letting Story 1.6 silently invent answers).

**Caveat:** review ran in the same Claude session that implemented the story. Single-LLM bias is a real limitation — consider a fresh-session review before promoting Story 1.0 to `done` on anything marked `[Review][Decision]` below if this feedback feels too aligned.

### Decision-Needed Findings (9)

- [x] [Review][Decision] **Mode identity rule is ambiguous** — docs/config-schemas.md says mode identifier is "file name" (line 42) but also "slugified `name` or file stem" (Loader contract line 229). Pick one canonical rule. Recommendation: **file stem is the identifier**; `name:` is display-only; loader does NOT slugify `name` for storage.
- [x] [Review][Decision] **Zero modes with `is_default: true` — cold-start target undefined** — docs/config-schemas.md §`is_default` runtime semantic covers "true" and tie-breaking but not the all-false case, which is the shipped default (coding.yaml has `is_default: false`). Recommendation: **fall back to first-alphabetical mode with a WARNING log**: "no default mode configured".
- [x] [Review][Decision] **`apps[].executable` normalization rules unspecified** — case sensitivity, `.exe` suffix, path separators all unaddressed. Story 3.6 (launcher) and 4.1 (window match) will diverge. Recommendation: **case-insensitive compare; `.exe` suffix optional and stripped for comparison; bare name resolves via PATH, absolute path used verbatim**.
- [x] [Review][Decision] **`urls[]` scheme allowlist missing — injection vector** — `file:///` and `javascript:` URLs are handed to the OS unchecked by Story 3.6. Recommendation: **allowlist `http://` / `https://` only; warn-and-drop everything else**.
- [x] [Review][Decision] **`briefing_recency_threshold_minutes: 0` behavior undefined** — schema allows `0` (non-negative int) but doesn't pin whether 0 means "never skip" or "always skip". Recommendation: **`0` = never skip the briefing** (conservative — always brief).
- [x] [Review][Decision] **Mode file with one broken entry: skip entry or skip file?** — docs/config-schemas.md Mode validation rules say "apps: missing or empty → mode file skipped" but exclusions schema says "entry skipped with warning; other entries still load". Inconsistent. Recommendation: **entry-skip at the `apps[]` level** (matches exclusions), file only skipped if `name` or the whole `apps` list is invalid.
- [x] [Review][Decision] **Missing `exclusions.yaml` silently disables all exclusion protection** — loader defaults both lists to empty if file absent; no warning mandated. Privacy-sensitive silent behavior. Recommendation: **WARNING log at startup** ("no exclusions file — zero protection"); do not halt.
- [x] [Review][Decision] **UTF-8 BOM behavior unspecified** — Windows Notepad saves with BOM by default; PyYAML may trip. Recommendation: **strip BOM on read; do not treat as malformed YAML**. Requiring users to re-save is user-hostile.
- [x] [Review][Decision] **Critical warning visibility: log file only, or surface in briefing?** — mode-skip, missing-exclusions, invalid-enum fallbacks all go to `WARNING` log; on a background-service startup, users never see console logs. **Resolved:** ongoing config warnings route to both the log file and a **post-briefing tier-style notice** rendered by Skin, on the same rail as tier transitions and operational status. Bypasses Voice (operational output, not personality-bearing prose).

### Patch Findings (11)

- [x] [Review][Patch] **Add `folders[]` absolute-path validation rule** [docs/config-schemas.md: Mode validation rules]
- [x] [Review][Patch] **Require non-empty strings in `excluded_apps[].match` and `excluded_title_patterns[]`** [docs/config-schemas.md: Exclusions validation]
- [x] [Review][Patch] **`api_key` contradiction: "non-empty when present" vs "empty treated as absent"** — pick canonical (empty = absent) [docs/config-schemas.md: Settings table + validation]
- [x] [Review][Patch] **Mode-file YAML parse error — file-skip, not hard startup error** (resolves mode rule vs cross-cutting rule conflict for singletons vs per-mode) [docs/config-schemas.md: Cross-cutting rules]
- [x] [Review][Patch] **Clarify: YAML quoting in title patterns is cosmetic; match is always literal substring** [docs/config-schemas.md: Exclusions schema]
- [x] [Review][Patch] **Top-level `null` YAML — treat as empty (apply defaults + WARNING)** [docs/config-schemas.md: Cross-cutting rules]
- [x] [Review][Patch] **Top-level shape mismatch (list instead of mapping) — hard malformed-YAML error** [docs/config-schemas.md: Cross-cutting rules]
- [x] [Review][Patch] **"Unknown keys ignored" rule applies at every depth (root, `apps[]`, `excluded_apps[]`)** [docs/config-schemas.md: Cross-cutting rules]
- [x] [Review][Patch] **Case-sensitive `.yaml` match; `.yml` / `.YAML` / `.yaml.bak` ignored** [docs/config-schemas.md: Mode loader behavior]
- [x] [Review][Patch] **First-run copy: `settings.defaults.yaml` renamed to `settings.yaml` on copy** [docs/config-schemas.md: Shipped defaults vs. runtime location]
- [x] [Review][Patch] **`is_default` tie-break ignores skipped (validation-failed) modes** [docs/config-schemas.md: Mode validation rules]

### Deferred (6)

- [x] [Review][Defer] **Duplicate YAML keys at same level (PyYAML silent last-wins)** — deferred to Story 1.6 loader
- [x] [Review][Defer] **Unicode case-fold under Turkish locale (dotted i)** — deferred to Story 1.6/4.2 (Python `.casefold()` handles English/most latin correctly)
- [x] [Review][Defer] **Modes directory is a file (user/AV error state)** — deferred to Story 1.6 impl
- [x] [Review][Defer] **CRLF trailing in string values** — deferred; no multiline string fields in current T1 schema
- [x] [Review][Defer] **First-run copy file-lock/AV failures** — deferred to Story 2.1 (setup.bat)
- [x] [Review][Defer] **Reserved Windows filenames (CON/NUL) in mode names** — deferred to Story 2.3 (mode wizard) and 1.6 (loader)

### Dismissed (3)

- [Review][Dismiss] "logged warning" lowercase in YAML comment vs `WARNING` in schema doc — stylistic nitpick.
- [Review][Dismiss] Duplicate `excluded_apps[].match` entries — wasteful but not a correctness bug.
- [Review][Dismiss] Concurrent edit while running — already pinned by architecture ("load once at startup, immutable thereafter").

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (1M context)

### Debug Log References

- `python -c "import yaml; ..."` — all three shipped YAML files parsed cleanly with PyYAML `yaml.safe_load`. Top-level keys matched the pinned schemas exactly:
  - `config/modes/coding.yaml` → `{apps, folders, is_default, name, urls}`
  - `config/exclusions.yaml` → `{excluded_apps, excluded_title_patterns}`
  - `config/settings.defaults.yaml` → `{bluntness, briefing_recency_threshold_minutes, skip_briefing_if_recent}` (no `api_key`, no `telemetry_opt_in` ✓)
- Schema-contract spot-checks (see Completion Notes for assertions) all passed.

### Completion Notes List

- **Scope held.** No `.py`, no `pyproject.toml`, no `requirements*.txt`, no `src/`, no `tests/` created. Deliverables are exactly the four files listed below.
- **All nine ACs satisfied.**
  - AC #1: `docs/config-schemas.md` authored with one worked example per schema.
  - AC #2: Mode schema pinned with `name`, `apps` (+ `name`/`executable`/`args` subfields), `folders`, `urls`, `is_default`; `apps[].name` explicitly documented as display-only; `is_default` runtime semantic pinned.
  - AC #3: Exclusions schema pinned with `excluded_apps` (process-name match) and `excluded_title_patterns` (window-title match); selection heuristic documented.
  - AC #4: Settings schema pinned with exactly `{api_key, bluntness, skip_briefing_if_recent, briefing_recency_threshold_minutes}`. `api_key` single canonical framing: schema-optional, required for cloud tier, offline-local-only fallback when absent/empty. `bluntness` T1 enum = `{calm, direct}`; default `direct` per UX spec line 1041.
  - AC #5: `telemetry_opt_in` explicitly excluded with rationale in the "Excluded from T1 settings" table and the "Known divergences from architecture.md" section.
  - AC #6: `config/` populated with `modes/coding.yaml`, `exclusions.yaml`, `settings.defaults.yaml`. `Banking` intentionally NOT in `excluded_apps` (documented in YAML comments and the reference doc).
  - AC #7: Validation rules documented per schema + cross-cutting rules table.
  - AC #8: "Shipped defaults vs. runtime location" section documents the `config/` (repo) vs. `%LOCALAPPDATA%/nova/` (runtime) split and first-run copy rules.
  - AC #9: All three files parse cleanly with PyYAML `yaml.safe_load`; spot-check assertions confirmed the parsed contents match the pinned contracts (required fields present, excluded fields absent, defaults at documented values).
- **Known divergences from architecture.md resolved in favor of this schema doc** (three points): `telemetry_opt_in` excluded, `ruthless` bluntness deferred to T2, `Banking` moved from `excluded_apps` to `excluded_title_patterns`. Captured in a dedicated section so future agents don't re-introduce them.
- **Default bluntness stayed `direct`.** Per in-story guardrail: changing this is a correct-course against the UX spec (Epic 7.1 + Epic 2.3 scope), not a schema edit. Rationale captured in the reference doc so future readers don't re-litigate.
- **No `.py` files were created at any point during implementation** — scope guard held.

**Code review resolution (2026-04-14):**
- Ran bmad-code-review with 3 parallel adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor: Approve. Adversarial layers produced 29 unique findings after dedup.
- Resolved 9 decision-needed findings (see Review Findings above). D9 ("warning visibility") amended by user: notices route to a post-briefing tier-style notice, not the Briefing Card.
- Applied 11 patches directly to `docs/config-schemas.md`, pinning: `folders[]` absolute-path enforcement; non-empty validation for `excluded_apps[].match` and `excluded_title_patterns[]`; `api_key` canonical "empty = absent" (single error path, not two); mode-file YAML parse error = file-skip (not hard startup error, which applies only to singletons); YAML quoting in title patterns is cosmetic; top-level `null` = empty document with warning; top-level shape mismatch = malformed for singletons / file-skip for modes; unknown-keys rule applies at every depth; case-sensitive `.yaml` glob only (no `.yml`/`.YAML`/`.yaml.bak`); first-run copy renames `settings.defaults.yaml` → `settings.yaml`; `is_default` tie-break ignores validation-failed modes.
- Updated inline YAML comments in `config/modes/coding.yaml`, `config/exclusions.yaml`, `config/settings.defaults.yaml` to reflect new rules (file-stem-is-identity, executable normalization, URL allowlist, threshold-zero semantic, non-empty match rule, missing-exclusions warning).
- Re-verified all three YAMLs parse with PyYAML and contract spot-checks still pass.
- 6 findings deferred to downstream stories (logged in `_bmad-output/implementation-artifacts/deferred-work.md`): duplicate YAML keys (Story 1.6), Turkish-locale case-fold (1.6/4.2), modes dir is a file (1.6), CRLF in string values (future schema changes), first-run copy file-lock (Story 2.1), reserved Windows filenames (Story 2.3/1.6).
- 3 findings dismissed as noise or already-architecturally-addressed.
- **Review caveat:** same-session adversarial review — single-LLM bias. Acceptance Auditor's Approve verdict is the clearest signal; the adversarial findings were useful regardless of bias (the edge cases exist even if a fresh LLM might have ranked them differently).

### File List

- `config/modes/coding.yaml` (new) — shipped-default workspace-mode template for the "coding" mode.
- `config/exclusions.yaml` (new) — shipped-default exclusion list (password managers in `excluded_apps`, sensitive title patterns in `excluded_title_patterns`).
- `config/settings.defaults.yaml` (new) — shipped-default user settings baseline (no `api_key`, no `telemetry_opt_in`).
- `docs/config-schemas.md` (new) — authoritative schema reference for modes, exclusions, and settings YAML in T1.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — `epic-1` moved `backlog` → `in-progress`; `1-0-define-yaml-config-schemas-spike` moved `backlog` → `ready-for-dev` → `in-progress` → `review` → `done`.
- `_bmad-output/implementation-artifacts/1-0-define-yaml-config-schemas-spike.md` (modified) — story file; task checkboxes, Dev Agent Record, File List, Change Log, Status, Review Findings (permitted sections only).
- `_bmad-output/implementation-artifacts/deferred-work.md` (new) — log of 6 edge-case findings deferred to Story 1.6 / 2.1 / 2.3.
- `.gitignore` (new) — ignores tooling (.claude/, .agents/, _bmad/), Python/SQLite clutter, secrets.

### Change Log

| Date | Change |
|------|--------|
| 2026-04-14 | Story 1.0 implemented as a spike. Authored `docs/config-schemas.md` reference doc and shipped three YAML defaults in `config/`. Pinned mode, exclusions, and settings schemas for T1. Resolved three divergences from `architecture.md` (`telemetry_opt_in` excluded, `ruthless` T2-deferred, `Banking` moved to title patterns). Status → review. |
| 2026-04-14 | Addressed code review findings — 20 items resolved (9 decisions + 11 patches). Pinned file-stem-as-mode-identity; `apps[].executable` normalization (case-insensitive, `.exe` optional, PATH for bare names); `urls[]` http/https-only allowlist (security); `briefing_recency_threshold_minutes: 0` = never skip; mode-file YAML parse error = file-skip; missing `exclusions.yaml` → warning + tier notice; UTF-8 BOM tolerated; config warnings route to post-briefing tier-style notice. 6 items deferred to 1.6/2.1/2.3, 3 dismissed. Status → done. |
