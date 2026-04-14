# Story 1.6: Config Loader & Immutable NovaConfig

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer implementing any system,
I want a config loader that reads all YAML files from `%LOCALAPPDATA%/nova/` and exposes them as an immutable `NovaConfig` dataclass,
so that no system reads YAML directly and all config access is centralized, type-safe, and locked to the schema pinned in Story 1.0.

## Acceptance Criteria

1. **`src/nova/core/config.py` is the single YAML-reading module in the codebase.** Public surface is intentionally small:
   - Frozen dataclasses: `NovaConfig`, `ModeConfig`, `AppConfig`, `ExclusionConfig`, `ExcludedAppConfig`, `UserSettings`.
   - Single public loader function: `def load_config(data_dir: Path) -> NovaConfig`.
   - Single public exception class reused from domain layer: `ConfigError` (already defined in Story 1.2's `core/exceptions.py`; do NOT add a new subclass).
   - **No `load_config_async`, no `reload`, no `NovaConfig.from_dict`, no `Builder`, no file-watching.** Config is loaded once at startup and is immutable for the process lifetime. Reloads happen by restarting the process.
   - **No class-level `@classmethod` loaders on the dataclasses themselves** (`NovaConfig.load(...)` is NOT the entry point). The module-level function keeps the dataclasses pure value types.
   - `__all__` declares exactly the names above plus `ConfigError` re-export — nothing else is exported from the module.

2. **`NovaConfig` is a frozen dataclass with exactly these fields — same order, same types:**
   - `db_path: Path` — computed from `data_dir`: `data_dir / "nova.db"`. Not a YAML-derived value; included in `NovaConfig` so the composition root (Story 1.10) can pass `config.db_path` to `SqliteStorageEngine(config.db_path)` without threading `data_dir` separately.
   - `data_dir: Path` — the root user data directory the loader was invoked against. Stored verbatim (not resolved, not normalized beyond whatever the caller passed in — test harnesses pass `tmp_path`, production passes `Path(os.environ["LOCALAPPDATA"]) / "nova"`; resolving here would hide production vs. test misconfigurations).
   - `modes: dict[str, ModeConfig]` — **keyed by file stem**, not by the YAML `name:` field. Per `docs/config-schemas.md` §Mode schema, the file stem is the canonical mode identifier and the `name:` field is display-only. The loader MUST NOT slugify `name` or use it as a dict key anywhere. Locked by tests `test_modes_keyed_by_file_stem` and `test_modes_dict_does_not_use_name_field_as_key`.
   - `exclusions: ExclusionConfig` — loaded from `exclusions.yaml`.
   - `settings: UserSettings` — loaded from `settings.yaml`.
   - `api_key: str | None` — **promoted out of `settings`** per the schema doc §Loader contract preview. Empty string and whitespace-only string normalize to `None` at load time so tier logic (Story 1.7) only ever sees `str | None` with no "present-but-useless" third state.
   - **mypy strict must pass** on all fields; no `Any`, no `# type: ignore` in production code.

3. **`ModeConfig` frozen dataclass — fields match the schema doc §Mode schema exactly:**
   - `name: str` — the display name from the YAML `name:` field. Display-only. Do NOT use as an identifier anywhere in this module or any downstream module; consumers that need the identifier read the `modes` dict key.
   - `apps: tuple[AppConfig, ...]` — **tuple, not list**, to keep the dataclass hashable and immutable after construction. At least one entry is guaranteed by validation (AC #7) — file-level skip if zero valid entries. Order is preserved from the YAML source.
   - `folders: tuple[str, ...]` — absolute path strings only. Non-absolute, non-string, and null entries are dropped with a `WARNING` log per the schema doc §Validation rules.
   - `urls: tuple[str, ...]` — `http://` / `https://` scheme allowlist enforced. Every other scheme is dropped with a `WARNING` log.
   - `is_default: bool` — defaults to `False`. Non-bool values fall back to `False` with warning.
   - **No `file_stem` field on `ModeConfig` itself** — the stem is the dict key. Duplicating it inside the dataclass invites drift.

4. **`AppConfig` frozen dataclass — fields match the schema doc §Mode schema `apps[]`:**
   - `name: str` — display-only.
   - `executable: str` — canonical identifier. Stored verbatim from YAML (no normalization at load time — normalization happens at match time, owned by Hands/Eyes in Stories 3.6 and 4.1). Locked by test `test_app_executable_stored_verbatim`.
   - `args: tuple[str, ...]` — command-line arguments. Defaults to empty tuple. Non-list-of-strings substitutes `()` with warning.

5. **`ExclusionConfig` frozen dataclass — fields match the schema doc §Exclusions schema:**
   - `excluded_apps: tuple[ExcludedAppConfig, ...]` — per-entry validation per AC #9.
   - `excluded_title_patterns: tuple[str, ...]` — non-empty/non-whitespace strings only; empty strings within the list are **rejected** per the schema doc (an empty substring matches every window — silent privacy blackout). Empty-string entries are dropped with warning; other entries load.

6. **`ExcludedAppConfig` frozen dataclass:**
   - `name: str` — display-only.
   - `match: str` — case-insensitive substring pattern. Stored verbatim from YAML; case-fold / normalization happens at match time (Story 4.2), not load time. Empty/whitespace `match` values reject the entry (schema doc §Validation rules).

7. **`UserSettings` frozen dataclass — fields match the schema doc §Settings schema:**
   - `bluntness: BluntnessLevel` — reuses the `BluntnessLevel` enum from `core/types.py`. Default `BluntnessLevel.DIRECT`. Invalid enum values (including `"ruthless"` which is T2-deferred per `core/types.py` comments) fall back to `DIRECT` with `WARNING` log.
   - `skip_briefing_if_recent: bool` — defaults to `True`. Non-bool falls back to `True` with warning.
   - `briefing_recency_threshold_minutes: int` — defaults to `60`. `0` is valid and means "never skip the briefing" (conservative interpretation, pinned by Story 1.0). Negative ints, floats, and non-ints fall back to `60` with `WARNING` log.
   - **`api_key` is NOT a field on `UserSettings`.** It lives on `NovaConfig` directly (AC #2). Keeping it off `UserSettings` prevents downstream code from grabbing `settings.api_key` and bypassing the empty-string normalization.
   - **`telemetry_opt_in` is NOT a field anywhere.** Per the schema doc §Excluded from T1 settings, adding it is a schema regression — "ships the field invites silent opt-in bugs later." Locked by a dataclass-field test that asserts the set of `UserSettings` fields equals exactly `{bluntness, skip_briefing_if_recent, briefing_recency_threshold_minutes}`.

8. **`load_config(data_dir: Path) -> NovaConfig` — the single public entry point.** Contract:
   - **Side-effect-free except for YAML reads and log output.** Does NOT create the data directory, does NOT write any file, does NOT touch `%LOCALAPPDATA%`. Directory creation is the first-run setup's job (Story 2.1).
   - **Precondition check — data dir:**
     - If `data_dir` does not exist (`not data_dir.exists()`) → raise `ConfigError("data directory missing")`.
     - If `data_dir` exists but is not a directory (`data_dir.exists() and not data_dir.is_dir()` — covers the file, symlink-to-file, and device-node cases) → raise `ConfigError("data directory path is not a directory")`. Symmetrical with the modes-path-is-file handling below and prevents confusing downstream path errors when the loader tries to resolve `data_dir / "settings.yaml"` against a non-directory.
     - Opaque messages, no path interpolation. The cli/composition root (Story 1.10) catches and surfaces a user-facing message; the config module never assumes console output is appropriate.
     - Locked by `test_missing_data_dir_raises_config_error` and `test_data_dir_path_is_file_raises_config_error`.
   - **Modes directory edge cases — pinned from Story 1.0 deferred work (docs line 11):**
     - `data_dir / "modes"` does not exist → treat as zero modes; log `WARNING` "modes/ directory missing — zero modes configured"; emit post-briefing tier-style notice (via the log — the notice-rail consumer wires the log handler in Story 5.4). Do NOT auto-create the directory here.
     - `data_dir / "modes"` exists but is **not a directory** (the AV-quarantine / user-error case from deferred-work.md:11) → raise `ConfigError("modes path is not a directory")`. Distinct from "no modes found" per the deferred-work rationale. Locked by `test_modes_path_is_file_raises_config_error`.
   - **Missing files — behavior differs per file per the schema doc §Cross-cutting rules:**
     - `exclusions.yaml` absent → both lists default to empty AND log `WARNING` + post-briefing notice "exclusions.yaml not found — zero exclusion protection." The loader does NOT halt and does NOT auto-recreate the file. (Privacy-sensitive silent defaults are a trap.)
     - `settings.yaml` absent → all `UserSettings` fields get documented defaults (`bluntness = DIRECT`, `skip_briefing_if_recent = True`, `briefing_recency_threshold_minutes = 60`), `api_key` is `None`. One-time user-facing notice ("no settings file → offline-local-only tier") is routed via log so Story 5.4's notice handler picks it up. The loader does not halt.
     - **No mode files** (modes/ exists but is empty OR contains zero matching `*.yaml` files) → `modes = {}`. Warning logged. Cold-start restore in a later story is a no-op; the briefing renders in State A. Not an error path here.
   - **Returns** a fully-constructed `NovaConfig` with frozen dataclasses all the way down. Every `list[...]` in every dataclass field is a `tuple[...]` after load. Mutating a returned `NovaConfig` raises `dataclasses.FrozenInstanceError`.
   - **Loaded once per process.** The module exposes `load_config` only — no caching layer, no singleton. The composition root (Story 1.10) calls it exactly once and passes the returned object down. Multiple calls in tests are supported (each test constructs its own `tmp_path`-rooted `data_dir` and calls `load_config(data_dir)`); the function is pure and deterministic given the same files on disk.

9. **YAML parsing — strict loader, explicit behaviors:**
   - **Canonical rule:** the loader uses `yaml.safe_load(...)` for every read path EXCEPT where duplicate-key rejection requires a `SafeLoader` subclass. In that single case, `yaml.load(text, Loader=_DuplicateKeyRejectingLoader)` is permitted **because the loader subclass inherits from `yaml.SafeLoader`** — it is still "safe" in the CVE-2017-18342 sense (only safe tags are resolvable). Bare `yaml.load(text)` without a `Loader=` kwarg, or with a non-`SafeLoader` subclass, remains forbidden.
   - **Static-analysis test:** `test_config_does_not_use_unsafe_yaml_load` greps `config.py` source for calls that match `yaml.load(` but do NOT also contain `Loader=` on the same logical call (multi-line-tolerant regex). Asserts zero matches. Bare `yaml.load(text)` fails the test; `yaml.load(text, Loader=_DuplicateKeyRejectingLoader)` passes. Additionally the test asserts `_DuplicateKeyRejectingLoader.__mro__` includes `yaml.SafeLoader` so a future rename to a non-safe parent fails the gate.
   - **Defensive `# noqa: S506`** on the single `yaml.load(..., Loader=_DuplicateKeyRejectingLoader)` call — Bandit's `S506` rule is NOT active in the current ruff selection (`E, F, I, UP, B, SIM, T20`) but Story 1.11 may tighten CI to include `S`. A preemptive `# noqa: S506 — Loader is a SafeLoader subclass; see _DuplicateKeyRejectingLoader` comment costs nothing and prevents a future CI break during Story 1.11's gate expansion.
   - **UTF-8 BOM tolerated** per the schema doc §Cross-cutting rules. Read files with `Path.read_text(encoding="utf-8-sig")` — stdlib-native, transparently strips a leading UTF-8 BOM. The test suite validates the BOM path with a dedicated test that writes `b"\xef\xbb\xbf" + b"bluntness: calm\n"` into `settings.yaml`.
   - **Duplicate YAML keys at the same level → rejected** per Story 1.0's code-review deferred item (deferred-work.md:9). PyYAML's `safe_load` silently accepts duplicates (last-wins). Address with a `SafeLoader` subclass — `_DuplicateKeyRejectingLoader(yaml.SafeLoader)` — that overrides `construct_mapping` to raise on a duplicate. On duplicate in a singleton (`settings.yaml`, `exclusions.yaml`) → hard `ConfigError("malformed config: duplicate key")`; in a mode file → skip the file with warning per the per-file-error rule. Locked by `test_duplicate_keys_in_settings_raises_config_error` and `test_duplicate_keys_in_mode_file_skips_mode`.
   - **Top-level shape mismatch (non-mapping root) → per-file behavior per the schema doc table:**
     - Singletons (`settings.yaml`, `exclusions.yaml`): hard `ConfigError("malformed config: non-mapping root")`. Opaque message; the file path is in the log, not in the exception.
     - Mode file (`modes/*.yaml`): skip the file with `WARNING` log.
   - **Top-level `null` (empty document, whitespace-only, comments-only) → treat as empty** per the schema doc §Cross-cutting rules. Apply documented defaults; emit `WARNING` log `"<file> is empty — applying defaults"`. Not a hard error.
   - **YAML parse error (`yaml.YAMLError`):**
     - Singletons: hard `ConfigError("malformed config: parse error")` chained via `raise ... from err`.
     - Mode file: skip with warning per the per-file rule.
   - **Mode-file iteration** uses `data_dir / "modes"` via `Path.iterdir()` + filter on the strict filename predicate (AC #10). `.glob("*.yaml")` is tempting but **case-insensitive on Windows** — a file named `Coding.YAML` would match `*.yaml` on NTFS despite the schema doc pinning case-sensitive glob. Use `iterdir()` and check `path.suffix == ".yaml"` after asserting `path.name` matches a canonical lowercase pattern.

10. **Mode-file filter predicate — per the schema doc §Mode schema Loader behavior:**
    - Accept `<stem>.yaml` where `<stem>` passes `_is_valid_mode_stem(stem)`.
    - `_is_valid_mode_stem(stem)` returns True when:
      - Stem is non-empty and does not contain `.` (so `modes/config.overrides.yaml` does NOT silently load as mode `config`).
      - Stem matches `re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem)` (kebab-case, non-leading-hyphen). Story 2.3's mode wizard enforces at creation; this loader is defensive.
      - Stem is not a **reserved Windows filename** (`con`, `prn`, `aux`, `nul`, `com1`–`com9`, `lpt1`–`lpt9`) — per Story 1.0 deferred-work.md:14, surface a `WARNING` log and skip the file. The test case uses a real `nul.yaml` created via `tmp_path` (permitted) with clear skip-log assertion.
    - Reject (skip silently, no warning — these are not configs, they are byproducts):
      - `*.YAML`, `*.yml`, `*.yaml.bak`, `*.yaml~`, files with non-`.yaml` suffix.
      - Leading dot (`.hidden.yaml`) — editor swap files.
      - `__init__.py`, `README.md`, or any non-`.yaml` file (suffix mismatch).

11. **Mode validation — file-level failures (whole file skipped with warning):**
    - YAML parse error.
    - Top-level is not a mapping (list, scalar, None is handled specially — see AC #9).
    - `name` missing, not a string, or empty/whitespace-only after strip.
    - `apps` missing, not a list, OR the list has zero valid entries after entry-level validation (AC #12).

12. **Mode validation — entry-level failures in `apps[]` (the specific entry is dropped; the mode itself still loads if at least one entry survives):**
    - `apps[].name` missing, empty/whitespace, or not a string → entry dropped with warning.
    - `apps[].executable` missing, empty/whitespace, or not a string → entry dropped with warning.
    - `apps[].args` present but not a list of strings → `args = ()` substituted, warning logged (entry still loads).
    - **Unknown keys at `apps[]` entry level are ignored** per the schema doc §Cross-cutting rules.

13. **Mode validation — field-level graceful handling:**
    - `folders` absent → `()`. Non-list → warning, `()` substituted. List entries that are non-string, null, or non-absolute-path are dropped with warning (use `Path(entry).is_absolute()` for the absoluteness check — on Windows this correctly rejects relative paths and `/unix/style` paths).
    - `urls` absent → `()`. Non-list → warning, `()` substituted. Each entry is checked against the **scheme allowlist**: only `http://` and `https://` (case-insensitive scheme prefix per RFC 3986). `file://`, `javascript:`, `data:`, bare paths, and empty strings are dropped with warning. **This is a security boundary**, not a convenience check — a `file://` URL in a mode config would open a local file in the browser (data exfiltration surface) on mode restore.
    - `is_default` absent → `False`. Non-bool → `False` with warning.
    - **Unknown root-level and nested keys are silently ignored** per the schema doc §Cross-cutting rules. Do NOT warn on unknown keys — would noise the log every time a T2+ field ships.

14. **Exclusions validation — per the schema doc §Exclusions schema:**
    - Both top-level lists default to `()` if omitted.
    - Each `excluded_apps` entry with missing/empty/whitespace `name` or `match` → entry dropped with warning.
    - Each `excluded_title_patterns` entry that is not a non-empty string after strip → entry dropped with warning.
    - **Empty-string rejection is load-bearing** — an empty substring matches every window, creating a silent privacy blackout. The test `test_exclusions_empty_string_entries_rejected` locks this.
    - Unknown keys ignored at every depth.

15. **Settings validation — per the schema doc §Settings schema:**
    - `api_key`: optional string. Absent, empty string, whitespace-only string all normalize to `None`. The `NovaConfig.api_key` field reflects this; `UserSettings` does not carry the value.
    - `bluntness`: optional enum. Accepted values: `calm`, `direct` (the T1 set, per `core/types.py:BluntnessLevel`). `ruthless` is explicitly rejected — falls back to `DIRECT` with `WARNING` log `"unknown bluntness value, falling back to direct"`. Case-sensitive match (YAML strings are case-sensitive; tolerate nothing here).
    - `skip_briefing_if_recent`: optional bool. Non-bool → `True` with warning.
    - `briefing_recency_threshold_minutes`: optional int. `0` is valid. Negative int, float (YAML `59.5`), non-int → `60` with warning. Use `isinstance(value, bool)` short-circuit BEFORE `isinstance(value, int)` (booleans are int subclasses in Python — `True` would otherwise pass `isinstance(x, int)` and get accepted as `1`).
    - Unknown root-level keys silently ignored.

16. **Warning routing — single contract:**
    - Every validation-warning path uses `logger = logging.getLogger("nova.core.config")` with `logger.warning(...)` and a structured `extra={...}` payload (project-context.md:128). Free-form string interpolation is forbidden.
    - **The tier-style post-briefing notice rail is NOT wired in this story** — that's Story 5.4's job. Story 1.6 emits `WARNING`-level log records with a structured `"surface": "tier-notice"` field in `extra` so Story 5.4's handler can filter and route them. Document the contract inline.
    - **No `print()` calls anywhere** (project-context.md:44; ruff `T20`). Any output goes via `logger`.

17. **Opaque exception messages — cross-cutting rule from Story 1.2:**
    - `ConfigError` messages are schema-level, not user-data-level. `"malformed config: parse error"` not `"malformed yaml at line 17 of /home/user/nova/settings.yaml: unexpected indentation"`. The full context lives in the structured log's `extra={"file": ..., "details": ...}` payload.
    - **API keys NEVER appear in exception messages, log messages, or log `extra` payloads.** Not even masked. The only place the API key exists in memory is the `NovaConfig.api_key` field; the only place it exists on disk is `settings.yaml`.
    - Chained exceptions use `from err` per the `core/exceptions.py` contract (Story 1.2 AC #2). Writing `raise ConfigError("...", cause=underlying)` without `from` fails to populate `__cause__`.

18. **Duplicate `is_default: true` tie-breaking — per the schema doc §`is_default` runtime semantic:**
    - The schema-level tie-break ("alphabetically-sorted file stem wins") is a **runtime query concern**, not a load-time one. The loader returns `ModeConfig` objects with their declared `is_default` values **unchanged** — multiple modes may legitimately return `is_default=True` from the loader. Downstream consumers (Story 3.2's BriefingAggregate, Story 3.6's mode restore target resolution) apply the tie-breaking rule when picking which one wins.
    - However: **the loader emits a `WARNING` log at load time** if more than one successfully-loaded mode has `is_default=True`. Gives the user visibility without moving the resolution semantics into this story. Locked by `test_multiple_is_default_logs_warning`.
    - Zero-case: no warning; downstream resolution is Story 3.2/3.6's concern.

19. **Dataclass construction detail — tuples not lists:**
    - Every collection field (`apps`, `folders`, `urls`, `excluded_apps`, `excluded_title_patterns`, `args`) is a `tuple` in the final frozen dataclass. YAML `safe_load` yields lists; convert to tuples after validation.
    - `modes` is a `dict[str, ModeConfig]` (not a frozen mapping) — `dict` is not frozen by default, but the outer `NovaConfig` is frozen, and mutating `config.modes[...]` at runtime does not violate immutability in any way T1 cares about. Python does not have a built-in frozen dict; introducing `types.MappingProxyType` wrapper here is scope creep. The contract is "loaded once, don't mutate"; tests verify load-time correctness, not runtime immutability of the dict.
    - Locked by `test_all_collection_fields_are_tuples` (introspects dataclass fields of `NovaConfig`, `ModeConfig`, `AppConfig`, `ExclusionConfig`).

20. **`tests/unit/core/test_core_isolation.py` — register `config.py` as the YAML boundary.** Follow the exact pattern established by `core/storage/engine.py` (sqlite3 boundary, AC #14 in Story 1.5):
    - Add `import nova.core.config as config_module` to the alphabetized imports.
    - Add `CONFIG_FORBIDDEN_TOPLEVEL_MODULES: frozenset[str] = FORBIDDEN_TOPLEVEL_MODULES - {"yaml"}` — config.py IS the yaml boundary; every other core module remains forbidden.
    - Add `CONFIG_ALLOWED_TOPLEVEL_MODULES: frozenset[str]` containing: `__future__, collections, dataclasses, logging, nova, pathlib, re, typing, yaml`. (Note: **no `os`** — `pathlib.Path` handles every path concern natively on Windows, including absolute-path detection and BOM-tolerant reads; no `datetime` — the schema has no timestamp fields in T1; no `sys`.) `collections` is required for `from collections.abc import Callable` (ruff `UP035` enforces `collections.abc.Callable` over `typing.Callable` on 3.12). If an implementation drift accidentally introduces `os`, `datetime`, or another module, `test_config_imports_within_allowlist` fails and the fix is a `pathlib` equivalent, not an allowlist expansion.
    - Add tests `test_config_forbidden_imports`, `test_config_imports_within_allowlist`, `test_config_does_not_import_nova_adapters_or_systems`, `test_config_does_not_dynamically_import_nova_adapters_or_systems`.
    - Extend the parametrize lists in `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules` to include `config_module`.
    - **No change needed to the top-level `FORBIDDEN_TOPLEVEL_MODULES` frozenset** — `yaml` stays forbidden globally; only `config.py` gets the carve-out.

21. **`src/nova/core/__init__.py` re-export update.** The story's new names are re-exported from `core/__init__.py` to match the pattern Story 1.2/1.3/1.4 set (domain types available as `from nova.core import NovaConfig`):
    - Add to the import block: `from nova.core.config import AppConfig, ExcludedAppConfig, ExclusionConfig, ModeConfig, NovaConfig, UserSettings, load_config`.
    - Extend `__all__` (alphabetized — the module already sorts): add `AppConfig`, `ExcludedAppConfig`, `ExclusionConfig`, `ModeConfig`, `NovaConfig`, `UserSettings`, `load_config`. Story 1.5 took `core/__init__.py` to 23 names (6 exc + 6 enums + 10 events + 1 engine). This story adds 7 more → **30 names** re-exported.
    - The re-export must be alphabetized; tests parse the `__all__` list and assert monotonic ordering (Story 1.2 carry-forward).

22. **Quality gate passes clean (Story 1.5 carry-forward):** `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` returns exit code 0.
    - mypy strict succeeds on `config.py`, the modified `core/__init__.py`, `test_config.py`, and the modified `test_core_isolation.py`.
    - No `Any`, no `# type: ignore` in production code. `cast()` is acceptable at the `yaml.safe_load → object` narrowing boundary, wrapped with an inline comment per Story 1.4's precedent.
    - Repo tree stays clean after the verify run — no `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.db`, `*.yaml.bak`.
    - **Expected test count delta:** `tests/unit/core/test_config.py` adds ~35–45 tests (see AC #24); `test_core_isolation.py` adds 4 tests + 2 parametrize entries (one per parametrized function). Firm number is whatever the run produces; don't over-fit a target. *Actual landed: +62 tests (56 test_config.py + 6 test_core_isolation.py) — 344 → 406.*

23. **No consumer wiring in this story.** Specifically:
    - Do NOT modify `src/nova/app.py` — wiring `load_config(data_dir)` into the composition root is Story 1.10's job.
    - Do NOT modify `src/nova/cli.py` — cli startup is also Story 1.10.
    - Do NOT wire any system (Brain, Eyes, Hands, Nerve, Voice, Ritual, Skin, Shield) to consume `NovaConfig`. Every consumer is gated behind its own story (see Cross-story impact table in Dev Notes).
    - Do NOT modify the shipped-defaults files under `config/`. Story 1.0 pinned them; Story 2.1 copies them; this story reads from `%LOCALAPPDATA%/nova/` (or `tmp_path` in tests).

24. **Test file `tests/unit/core/test_config.py` — coverage expectations:**
    - **Dataclass shape tests** (~5): NovaConfig/ModeConfig/AppConfig/ExclusionConfig/UserSettings each have the exact field set; all collection fields are tuples; frozen attribute mutation raises FrozenInstanceError.
    - **Happy path tests** (~4): full `data_dir` with one mode, exclusions, settings, api_key → NovaConfig fields all populated; two modes load keyed by stem; `api_key` promoted out of settings.
    - **Missing file tests** (~5): no settings.yaml → defaults; no exclusions.yaml → empty lists + warning; no modes/ dir → empty modes + warning; no mode files → empty modes; empty settings.yaml (whitespace) → defaults.
    - **Malformed-file tests** (~8): settings.yaml parse error → ConfigError; exclusions.yaml parse error → ConfigError; settings.yaml non-mapping root → ConfigError; duplicate key in settings → ConfigError; duplicate key in a mode → mode skipped with warning; mode with parse error → skipped; mode with non-mapping root → skipped; mode with missing/empty `name` → skipped; mode with zero valid apps → skipped.
    - **Mode-filter tests** (~5): `.yml` ignored; `.YAML` ignored (case-sensitive on test harness using content not FS); `.yaml.bak` ignored; `.hidden.yaml` ignored; mode stem with dot ignored; reserved Windows name `nul.yaml` skipped with warning.
    - **Mode path-is-file edge case** (~1): `modes` path is a file → `ConfigError("modes path is not a directory")`.
    - **Mode validation tests** (~6): `folders` with relative path dropped; `urls` with `file://` dropped; `urls` with `http://` kept; `apps[].args` non-list substituted `()`; `is_default` non-bool falls back to False; multiple `is_default=true` modes → warning logged but both return with the true value.
    - **Exclusions validation tests** (~4): empty-string `match` dropped; empty-string title pattern dropped; missing `name` in `excluded_apps[]` dropped; malformed entry dropped but others load.
    - **Settings validation tests** (~7): invalid bluntness → `DIRECT` + warning; `ruthless` → `DIRECT` + warning; `api_key` empty string → `None`; `api_key` whitespace → `None`; `briefing_recency_threshold_minutes: 0` accepted; negative int falls back to 60 + warning; float `59.5` falls back to 60 + warning; `True` (YAML `true`) for `briefing_recency_threshold_minutes` falls back to 60 (bool-not-int check).
    - **UTF-8 BOM tolerance** (~1): `settings.yaml` with leading BOM loads successfully.
    - **API-key-never-logged test** (~1): a `caplog` capture plus a realistic api_key in settings confirms no log record contains the key substring. Paranoid but cheap regression gate against a future "defensive" log line.
    - **Multiple `is_default` warning** (~1): two modes both `is_default=true` → WARNING record present in caplog; both modes present in return with `is_default=True` preserved (no load-time mutation).
    - **Tier-notice surface contract** (~1): at least one of the missing-file warnings has `surface == "tier-notice"` read off the `LogRecord`. Assertion pattern: `assert any(getattr(r, "surface", None) == "tier-notice" for r in caplog.records)` — add an inline comment `# extra={...} kwargs become LogRecord attributes accessible via getattr(); Python's logging.Logger merges them at record construction.` so a future reader doesn't wonder why `extra` isn't a dict on the record. Locks Story 5.4's handoff contract.
    - **Data-dir-is-file edge case** (~1): `data_dir` is a regular file → `ConfigError("data directory path is not a directory")`. Symmetrical with the modes-path-is-file test.
    - **Unsafe-yaml-load static-analysis test** (~1): `test_config_does_not_use_unsafe_yaml_load` reads `config.py` source and asserts no call site uses `yaml.load(` without a `Loader=` kwarg (regex-based, multi-line tolerant). Also asserts `issubclass(_DuplicateKeyRejectingLoader, yaml.SafeLoader)`. Locks the canonical YAML-safety rule so a future edit can't regress to bare `yaml.load(text)`.
    - **Each test is `def test_...(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:`** with mypy-strict-clean signatures. Use `tmp_path` exclusively for `data_dir`; never touch `%LOCALAPPDATA%`.

## Tasks / Subtasks

- [x] **Task 1: Author `src/nova/core/config.py` — dataclasses + loader + helpers** (AC: #1–#19)
  - [x] Module docstring: purpose (single YAML reader for the whole project), pins the schema to `docs/config-schemas.md`, cites Story 1.0 for schema lock, cites architecture.md:521–525 for the "no system reads YAML directly" rule.
  - [x] `from __future__ import annotations`.
  - [x] Imports (exact — the isolation test allowlist matches these): `dataclasses`, `logging`, `pathlib.Path`, `re`, `typing.cast` (for the single YAML narrowing point — see below), `collections.abc.Callable` (narrowing the untyped `loader.construct_object` method), `yaml`. First-party: `nova.core.exceptions.ConfigError`, `nova.core.types.BluntnessLevel`. **No `typing.Any`** — `cast` + `Callable` alone are sufficient; `typing.Any` is banned in production code per project-context.md:47. `datetime` is NOT needed (schema has no typed timestamp fields).
  - [x] **YAML narrowing boundary — single documented `cast` point.** The helper `_load_yaml_file(path: Path) -> object | None` returns `object` (not `Any`). Validators downstream narrow via concrete `isinstance(...)` checks (`_require_mapping` narrows `object` → `dict[str, object]`). Where a single one-liner narrowing is required — specifically, casting the result of `yaml.load(text, Loader=_DuplicateKeyRejectingLoader)` back to `object` to avoid `Any` propagation from `types-pyyaml` stubs — use `cast(object, parsed)` with an inline comment: `# types-pyyaml returns Any; narrow to object at the single YAML boundary per Story 1.4 precedent.` No other `cast` and no `Any` anywhere else.
  - [x] Module-level `logger = logging.getLogger("nova.core.config")`.
  - [x] Module-level constants: `_MODE_STEM_RE = re.compile(r"[a-z0-9][a-z0-9-]*")`, `_RESERVED_WIN_STEMS: frozenset[str]` (populated per AC #10), `_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})`, `_TIER_NOTICE_EXTRA: dict[str, str] = {"surface": "tier-notice"}`.
  - [x] `_DuplicateKeyRejectingLoader(yaml.SafeLoader)` with `construct_mapping` override — raises `yaml.constructor.ConstructorError` on duplicate. Wrapper `_load_yaml(text: str) -> object` invokes `yaml.load(text, Loader=_DuplicateKeyRejectingLoader)` (this is the ONLY non-`safe_load` call, explicitly safe because the loader subclass restricts to safe tags; document inline that ruff's `yaml.load` check doesn't fire because we're not using bare `yaml.load`).
  - [x] Frozen dataclasses: `@dataclass(frozen=True, slots=True) class NovaConfig | ModeConfig | AppConfig | ExclusionConfig | ExcludedAppConfig | UserSettings`. `slots=True` matches Story 1.3's event pattern (no `__dict__`, reduced memory, fail-fast on typos).
  - [x] Private validators (all `_`-prefixed, all `-> ...` typed):
    - `_load_yaml_file(path: Path) -> object | None` — reads file (UTF-8-sig for BOM), parses via `_load_yaml`. Returns `None` for nonexistent file. Raises `ConfigError` on parse failure for SINGLETONS only (the caller decides which path raises vs. skips — helper itself doesn't know the file role).
    - `_require_mapping(value: object, filename: str) -> dict[str, object]` — validates top-level is a mapping. Raises `ConfigError` or returns dict; caller wraps for mode-file skip.
    - `_validate_mode(stem: str, data: dict[str, object]) -> ModeConfig | None` — returns `None` on skip-worthy validation failure (logs warning); returns `ModeConfig` on success.
    - `_validate_app(entry: object) -> AppConfig | None` — per AC #12.
    - `_validate_folders(value: object) -> tuple[str, ...]`, `_validate_urls(value: object) -> tuple[str, ...]`, `_validate_args(value: object) -> tuple[str, ...]` — per AC #13.
    - `_validate_exclusions(data: dict[str, object]) -> ExclusionConfig` — per AC #14.
    - `_validate_settings(data: dict[str, object]) -> tuple[UserSettings, str | None]` — returns settings AND the promoted `api_key` tuple per AC #15 / AC #2.
  - [x] Public `load_config(data_dir: Path) -> NovaConfig`:
    1. Precondition check data_dir exists → `ConfigError`.
    2. Resolve `modes_dir = data_dir / "modes"`, `exclusions_path = data_dir / "exclusions.yaml"`, `settings_path = data_dir / "settings.yaml"`, `db_path = data_dir / "nova.db"`.
    3. modes: empty if `modes_dir` missing (warn) → `ConfigError` if `modes_dir` is a file → else iterate per AC #10.
    4. exclusions: empty default if missing (warn with tier-notice) → else validate.
    5. settings: defaults if missing (warn) → else validate. Extract `api_key` into separate variable.
    6. Log duplicate-`is_default` warning if applicable (AC #18).
    7. Construct and return `NovaConfig(db_path=..., data_dir=..., modes=..., exclusions=..., settings=..., api_key=...)`.
  - [x] `__all__` per AC #1 + the `ConfigError` re-export.

- [x] **Task 2: Update `src/nova/core/__init__.py` — re-export the new config names** (AC: #21)
  - [x] Add the alphabetized import block per AC #21.
  - [x] Extend `__all__` alphabetically.
  - [x] Verify: `from nova.core import NovaConfig, load_config` works from a scratch script.

- [x] **Task 3: Extend `tests/unit/core/test_core_isolation.py` — register config.py as the YAML boundary** (AC: #20)
  - [x] Alphabetized import: `import nova.core.config as config_module`.
  - [x] `CONFIG_FORBIDDEN_TOPLEVEL_MODULES` and `CONFIG_ALLOWED_TOPLEVEL_MODULES` frozensets per AC #20.
  - [x] Extend `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules` parametrize lists with `config_module`.
  - [x] Add `test_config_forbidden_imports`, `test_config_imports_within_allowlist`, `test_config_does_not_import_nova_adapters_or_systems`, `test_config_does_not_dynamically_import_nova_adapters_or_systems`.
  - [x] Verify: the storage-engine tests' carve-out for `sqlite3` and the config-module carve-out for `yaml` are independent — each boundary module has its own allowlist frozenset.

- [x] **Task 4: Author `tests/unit/core/test_config.py` — ~40 tests per AC #24** (AC: #24)
  - [x] File header: `from __future__ import annotations`, minimal imports matching Story 1.4/1.5 conventions.
  - [x] Helper `_write(path: Path, content: str) -> None` — convenience wrapper for `path.write_text(content, encoding="utf-8")`. NOT a pytest fixture; ordinary function.
  - [x] Helper `_minimal_mode_yaml(name: str = "coding") -> str` — returns a minimal-valid mode YAML string for re-use across tests.
  - [x] Each test `def test_...(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:` per AC #24.
  - [x] No fixtures added to `tests/conftest.py` (Story 1.4 carry-forward).

- [x] **Task 5: Full verify run** (AC: #22)
  - [x] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest`.
  - [x] Exit 0. Test count grows by approximately 44 (40 config tests + 4 isolation-test additions).
  - [x] `git status` shows only intentional src/test changes — no `__pycache__/`, `*.yaml.bak`, stray `*.db`.

### Review Findings (2026-04-14)

**Code review summary:** 3 adversarial layers (Blind Hunter, Edge-Case Hunter, Acceptance Auditor). 1 decision-needed (merge-key semantic — resolved as explicit-reject), 10 patches (all applied), 3 deferred, 2 dismissed. Post-review quality gate: ruff/format/mypy green, 416 tests pass + 1 sys.platform-gated skip (417 collected). Test delta: 406 → 417 (+11 regression tests for merge-key/unhashable/IO/encoding/reserved-stem/stem-extra paths).

- [x] [Review][Patch] **Reject YAML merge keys (`<<: *anchor`) explicitly** [src/nova/core/config.py:_construct_mapping_reject_duplicates] — Decision (b) locked 2026-04-14: merge keys get explicit rejection (not silent no-op, not stock PyYAML flatten). Detect `key_node.tag == "tag:yaml.org,2002:merge"` in the loop and raise `yaml.constructor.ConstructorError(None, None, "merge keys not supported", key_node.start_mark)`. Singleton path surfaces as `ConfigError("malformed config: merge keys not supported")`; mode path skips the file with warning. Add two tests: one for the singleton-hard-error path, one for the mode-skip path.

- [x] [Review][Patch] **`_load_modes` does not catch `OSError` from `_read_yaml_file`** [src/nova/core/config.py:568-575] — Only `yaml.constructor.ConstructorError` and `yaml.YAMLError` are caught. `PermissionError` / `FileNotFoundError` (TOCTOU between `iterdir` and `read_text`) / any other `OSError` escape `_load_modes` and abort the entire `load_config`, losing every other valid mode. Contradicts the "skip-on-error at the file level" docstring. Fix: add `except OSError: logger.warning("mode file I/O error — skipped", extra={"stem": stem}); continue`. **HIGH.**

- [x] [Review][Patch] **`_load_singleton` lets `OSError` escape unwrapped** [src/nova/core/config.py:535-542] — Symmetric with the modes-loader gap. `path.exists()` check then `_read_yaml_file(path)` — a TOCTOU deletion, permission flip, or device I/O error raises `OSError` which is NOT a `yaml.YAMLError`, so it propagates as a raw `OSError` instead of `ConfigError`. Breaks the opaque-error contract (AC #17). Fix: `except OSError as err: raise ConfigError("malformed config: I/O error") from err`. **MED.**

- [x] [Review][Patch] **Unhashable mapping keys bypass `ConfigError` wrapping** [src/nova/core/config.py:86-96] — In `_construct_mapping_reject_duplicates`, YAML `? [a,b]: v` (complex key) constructs a list, then `if key in mapping` raises raw `TypeError: unhashable type`. Not caught by `yaml.YAMLError`, so it escapes `_load_singleton` / `_load_modes` unwrapped. Narrow path, but real. Fix: wrap the membership check in `try/except TypeError: raise yaml.constructor.ConstructorError(...)`. **MED.**

- [x] [Review][Patch] **`test_api_key_never_appears_in_logs` is vacuously true** [tests/unit/core/test_config.py:654-671] — The happy-path `load_config` emits zero log records when the bootstrap is valid (no missing file, no malformed input). `caplog.records` is empty → the for-loop iterates zero times → assertion can never fail. Fix: force at least one log record by adding a triggering condition (e.g., also set `bluntness: chaotic` to force a WARNING), assert `len(caplog.records) >= 1` as a precondition, AND add one deliberate `logger.debug("settings loaded", extra={"api_key_present": bool(api_key)})`-style record to prove the scan has teeth. **MED.**

- [x] [Review][Patch] **`UnicodeDecodeError` bypasses `ConfigError`** [src/nova/core/config.py:_load_singleton + _load_modes] — Non-UTF-8 bytes in a YAML file (e.g., user pastes cp1252 smart quotes) raise `UnicodeDecodeError` from `read_text(encoding="utf-8-sig")`. `UnicodeDecodeError` is a subclass of `ValueError`, NOT `yaml.YAMLError`, so it escapes both loaders unwrapped. Fix: catch it alongside `yaml.YAMLError` in `_load_singleton` (raise `ConfigError("malformed config: encoding error") from err`) and `_load_modes` (warn + skip). **MED.**

- [x] [Review][Patch] **`_validate_folders` uses platform-dependent `Path.is_absolute()`** [src/nova/core/config.py:349-351] — On Windows, `Path("/home/x").is_absolute()` returns False; on POSIX, True. The test `test_folders_relative_path_dropped` uses `C:/absolute/path` which only parses as absolute on Windows — on Linux CI the test would pass for the wrong reason (entry dropped as non-absolute for a different code path). Fix: switch to `PureWindowsPath(entry).is_absolute()` to make the check deterministic per the schema doc's Windows-target semantic. **MED.**

- [x] [Review][Patch] **No integration test writing a real `con.yaml` mode file** [tests/unit/core/test_config.py] — AC #10's reserved-Windows-stem path is only unit-tested via `_is_valid_mode_stem`, not via `_load_modes`'s `_is_reserved_stem` → warn + skip branch. On Linux CI a `modes/con.yaml` file IS creatable; add a `sys.platform != "win32"`-gated test that writes `modes/con.yaml`, loads, and asserts both the warning fires and `"con"` is not in `config.modes`. **MED.**

- [x] [Review][Patch] **`_normalize_api_key` preserves surrounding whitespace** [src/nova/core/config.py:505-511] — `value.strip()` is used for the empty-check but `return value` returns the unstripped original. A YAML value like `api_key: " sk-ant-abc "` silently stores the key with surrounding spaces, which fails Anthropic auth with no useful error. Inconsistent with the whitespace-is-absent rule. Fix: `return value.strip()`. **LOW.**

- [x] [Review][Patch] **Per-entry validator warnings omit `extra={"stem": stem}` context** [src/nova/core/config.py — `_validate_args`, `_validate_folders`, `_validate_urls`, `_validate_excluded_apps`, `_validate_title_patterns`, `_validate_bluntness`, `_validate_skip_briefing`, `_validate_threshold`] — AC #16 pins structured `extra=` payload on every warning. Current code emits "mode folders contains non-absolute path — dropped" with no record of WHICH mode emitted it. Operator traceability gap. Fix: thread `stem` down to the nested validators (or wrap the warning emission in `_validate_mode` where stem is in scope). **LOW.**

- [x] [Review][Defer] **`!!python/object` YAML tag rejection not test-locked** — SafeLoader rejects it by construction; adding an explicit regression test is belt-and-suspenders. Defer to the story that first widens the loader surface (or a test-hygiene pass). Low risk.

- [x] [Review][Defer] **Broken-symlink mode files skipped silently** — `entry.is_file()` returns False on a broken symlink → silent skip, no warning. Debugging trap but spec is satisfied. Defer to whichever story first requires symlink-aware config (Epic 2 setup, or Epic 6 mode-editing).

- [x] [Review][Defer] **URL validation allows embedded control chars / NUL bytes** — `"http://\x00malicious"` passes the scheme check. Downstream browser behavior undefined. Defer to Story 3.6 (Mode Restore & App Launching), where URLs are actually opened — that's the right place to add `any(c < " " for c in entry)` screening.

- [x] [Review][Dismiss] **`NovaConfig.modes` dict mutable at runtime** — AC #19 explicitly accepts this trade-off: "Python does not have a built-in frozen dict; introducing `types.MappingProxyType` wrapper here is scope creep."

- [x] [Review][Dismiss] **Test count drift (56 vs 35-45 spec range)** — AC #22 footnote already acknowledged the actual landed count. Not a regression.

---

**Follow-up review (2026-04-14, post-merge):**

- [x] [Review][Patch] **Missing "zero loadable modes" warning when modes/ exists but empty** [src/nova/core/config.py:639-650] — AC #8 pins: "No mode files (modes/ exists but is empty OR contains zero matching *.yaml files) → modes = {}. Warning logged." Original code only warned for the missing-directory case; `_load_modes` returning `{}` from a valid-but-empty (or all-rejected) directory fell through silently. Fix: after `modes = _load_modes(modes_dir)`, if `not modes` emit `logger.warning("modes/ directory has zero loadable modes — zero modes configured", extra=_TIER_NOTICE_EXTRA)`. Locked by two new tests: `test_empty_modes_dir_warns_and_empty` and `test_modes_dir_with_only_invalid_files_warns_and_empty`.
- [x] [Review][Patch] **Test gap for the above contract** [tests/unit/core/test_config.py] — Only the missing-directory path was covered by `test_missing_modes_dir_warns_and_empty`. Two new tests above close the gap for (a) empty directory and (b) directory with only-invalid files.

Post-follow-up quality gate: 418 passed + 1 skipped (419 collected; +2 tests).

---

- [x] **Task 6: Sprint status + commit** (AC: #22, post-implementation)
  - [x] Update `_bmad-output/implementation-artifacts/sprint-status.yaml` → `1-6-config-loader-and-immutable-novaconfig: in-progress` on dev start, `review` on handoff, `done` after code review.
  - [x] Commit message (Story 1.4/1.5 style): `"Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)"`.

## Dev Notes

### Story Type: Foundational infrastructure — the single YAML reader

This story produces the **only path** by which YAML config enters N.O.V.A. Every other system (Brain, Eyes, Hands, Nerve, Voice, Ritual, Skin, Shield) reads config via an injected `NovaConfig` object. This rule is enforced by `test_core_isolation.py` (which restricts `yaml` imports to `core/config.py` only) and by the "yaml in FORBIDDEN_TOPLEVEL_MODULES" global check. A system that wants to read config edits `NovaConfig`'s dataclass fields (schema lives in `docs/config-schemas.md`); it never opens a YAML file.

The story also materializes the dataclass shapes (`NovaConfig`, `ModeConfig`, `AppConfig`, `ExclusionConfig`, `ExcludedAppConfig`, `UserSettings`) that downstream stories consume as constructor-injected parameters. Changing a field here is a schema-level change that routes through a new numbered story per the schema doc's change-control rule, not a silent edit.

### Scope guard (hard stop)

- **Do NOT touch `app.py`, `cli.py`, or the composition root.** Wiring `load_config` into startup is Story 1.10's job. This story delivers the module and dataclasses only.
- **Do NOT implement any file-watcher, config-reload, or live-update path.** Config loads once at startup. Reloads happen by restart. Architecture pinned at line 1137 ("Loaded once at startup and exposed as immutable dataclasses").
- **Do NOT implement `save_config` / `write_config` / mode-file mutation.** Writing config is Epic 6's concern (mode editing) and Epic 2's concern (first-run setup). This story is read-only.
- **Do NOT copy shipped defaults from `config/` to `%LOCALAPPDATA%/nova/`.** That's Story 2.1's setup script. This story reads whatever is in the provided `data_dir`; tests pass `tmp_path`, production passes `%LOCALAPPDATA%/nova/` (after Story 2.1 has populated it).
- **Do NOT add `api_keys` / `secrets` / `vault` abstractions.** The api_key is a plain string. The user put it in `settings.yaml`; the loader reads it; tier logic consumes it. That's the whole shape.
- **Do NOT implement encryption, keyring, or OS-level secret storage.** project-context.md:179 pins "API key lives in settings.yaml in the user data directory"; changing that is an architecture amendment, not a loader refactor.
- **Do NOT add CLI commands (`nova config show`, `nova config validate`, etc.).** Story 5.1 (transparency command) has a narrow surface that reads `NovaConfig`; anything else is Epic 2/6.
- **Do NOT modify the shipped defaults in `config/`.** Story 1.0 pinned the files. Hand-editing them is a schema change and must route through a new numbered story.
- **Do NOT auto-create `%LOCALAPPDATA%/nova/`, `modes/`, `backups/`, or `logs/` directories.** Creation is Story 2.1's job; this story's precondition check ensures `data_dir` exists and surfaces a clear error otherwise.
- **Do NOT wire the "post-briefing tier-style notice" UI.** That's Story 5.4. This story emits `WARNING` log records with `extra["surface"] == "tier-notice"`; the handler that routes those to Skin's status line is downstream.
- **Do NOT implement the `is_default` tie-breaking resolution.** That's Story 3.2 (BriefingAggregate) and Story 3.6 (mode restore). This story only emits a warning when more than one `is_default=True` mode loads.
- **If `config.py` grows past ~350 lines of production code, you are over-building.** Dataclasses (~70 lines), validators (~150 lines), the public loader (~60 lines), helper constants + loader-subclass (~50 lines) → ~330 lines target. Any helper that only has one caller gets inlined.

### Critical constraints and gotchas

- **`yaml.safe_load` vs. `yaml.load`.** Bare `yaml.load` was removed from safe use by PyYAML 5.1 (CVE-2017-18342). We use a `SafeLoader` subclass invoked via `yaml.load(text, Loader=_DuplicateKeyRejectingLoader)` — this is safe because the subclass only extends `SafeLoader`, never `Loader`. The single call site carries `# noqa: S506 — Loader is a SafeLoader subclass; see _DuplicateKeyRejectingLoader` (preemptive for Story 1.11's CI gate). Story's own `test_config_does_not_use_unsafe_yaml_load` greps for bare `yaml.load(` without `Loader=` and asserts the subclass MRO includes `yaml.SafeLoader` — that's the durable gate, not the ruff rule.
- **Booleans are `int` subclasses in Python.** `isinstance(True, int) is True`. When validating `briefing_recency_threshold_minutes`, check `isinstance(value, bool)` BEFORE `isinstance(value, int)` — otherwise YAML `true` silently becomes `1`. Locked by AC #15.
- **`Path.is_absolute()` on Windows.** `Path("/unix/style").is_absolute()` returns `False` on Windows (no drive letter). `Path("C:\\Users\\foo")` returns `True`. Correct behavior for the schema doc's "absolute path strings required" rule.
- **Windows reserved filenames** (`CON`, `NUL`, `AUX`, `PRN`, `COM1`–`COM9`, `LPT1`–`LPT9`) are case-insensitive at the filesystem layer. Test the stem case-folded (`stem.lower()`) against a lowercase set. Story 1.0 deferred-work.md:14 pins this story as the place where the loader surfaces the skip; the create-side enforcement is Story 2.3.
- **`Path.read_text(encoding="utf-8-sig")`** — the `-sig` suffix transparently strips a leading UTF-8 BOM. This is the idiomatic Windows-friendly reader for YAML config written by Notepad. Locked by AC #9 and a dedicated test.
- **`yaml.safe_load` returns `None` for empty or comment-only documents.** The "top-level `null` → treat as empty" rule (AC #9) maps cleanly: `if parsed is None: parsed = {}` after the load call. Log a warning at that point per the schema doc.
- **`isinstance(x, dict)` vs. `isinstance(x, Mapping)`.** PyYAML's `safe_load` produces concrete `dict` instances; checking `isinstance(x, dict)` is sufficient and type-narrowing-friendly for mypy. No `Mapping` abstraction needed.
- **Glob vs. `iterdir()` on Windows.** `Path.glob("*.yaml")` on NTFS is case-insensitive and would accidentally match `Coding.YAML` despite the schema doc pinning case-sensitive glob. Use `iterdir()` + explicit suffix/name check. Locked by AC #9.
- **`cast(Any, ...)` at the YAML narrowing boundary.** `yaml.safe_load` returns `Any` by PyYAML's type stubs (`types-pyyaml` installed per pyproject.toml). Our wrapper `_load_yaml` returns `object` to force a narrow at every downstream call; this is the project-wide pattern for escaping `Any` (Story 1.4 precedent). `_require_mapping(value: object, ...) -> dict[str, object]` does a concrete `isinstance(value, dict)` check, which narrows for mypy and returns the typed dict.
- **mypy strict on `yaml` requires `types-pyyaml`**. Already in dev-deps (pyproject.toml:24). No new dependency needed.
- **Frozen dataclass with `slots=True` behavior.** Setting `slots=True` generates `__slots__` and reduces memory; the combination with `frozen=True` generates the standard `FrozenInstanceError` on mutation. Story 1.3's events use this pattern — follow.
- **Six dataclasses are standalone — no inheritance between them.** Do NOT create a shared `BaseConfig` (or any other parent dataclass) across `NovaConfig` / `ModeConfig` / `AppConfig` / `ExclusionConfig` / `ExcludedAppConfig` / `UserSettings`. `dataclass(slots=True)` with inheritance requires every parent class to ALSO be `@dataclass(slots=True)`, or the child silently gains a `__dict__` that defeats the slots optimization and breaks the `FrozenInstanceError` guarantee in surprising ways. All six inherit from `object` only. Keeping them flat is cheap here (no duplicated field) and locks out a future fragility.
- **No `os` import — `pathlib` handles everything.** `Path.exists()`, `Path.is_dir()`, `Path.is_absolute()`, `Path.iterdir()`, `Path.read_text(encoding="utf-8-sig")` cover every filesystem concern in this module. `os.path.isabs()` is what `Path.is_absolute()` delegates to internally — there is zero need to import `os` directly. If `os` appears during implementation, `test_config_imports_within_allowlist` fires; the fix is a `pathlib` equivalent, not an allowlist extension.
- **Don't promote `api_key` to `UserSettings`.** Promoting it back into `UserSettings` (to "match the YAML shape") defeats the empty-string normalization — consumers would grab `config.settings.api_key` bypassing the `None` conversion. The public contract is `config.api_key`; `UserSettings` doesn't carry the field at all. Locked by AC #7 and AC #15.
- **Don't do Unicode case-fold on `excluded_apps[].match` here.** The schema doc pins "case-insensitive at match time" — normalization happens in Story 4.2 (exclusion matcher). Story 1.0 deferred-work.md:10 flagged a Turkish-locale `casefold()` edge case; Story 4.2 picks that up. This story stores the string verbatim.
- **Don't apply tie-breaking for `is_default` at load time.** Runtime queries (Story 3.2 BriefingAggregate, Story 3.6 mode restore) apply the alphabetical tie-break. Doing it here would silently coerce multiple `is_default=True` modes into one false and hide the user's config bug. We emit a warning and pass all values through unchanged. Locked by AC #18.
- **Don't normalize `apps[].executable` at load time.** Case-folding / `.exe`-stripping happens at match time (Hands/Eyes). The loader stores the string verbatim. Premature normalization here would lose information the schema doc says is caller-owned.
- **Don't auto-create directories.** `load_config(data_dir)` treats `data_dir` as a precondition. Creation is Story 2.1. Adding `data_dir.mkdir(parents=True, exist_ok=True)` here invites silent misconfigurations where the loader runs against an empty dir and "it just works."
- **Don't log the API key.** Under any circumstances. Not masked, not truncated, not with `extra={"api_key": "***"}`. The value exists in `NovaConfig.api_key` and on disk in `settings.yaml`. Any other surface is a security regression. Locked by `test_api_key_never_appears_in_logs`.

### Repo shape at time of this story

After Stories 1.0–1.5 the repo contains:

- `src/nova/core/__init__.py` (re-exports 23 names; this story takes it to 30)
- `src/nova/core/events.py`, `core/exceptions.py`, `core/types.py`, `core/storage/engine.py`, `core/storage/migrations/runner.py`, `core/storage/migrations/001_initial_schema.py`
- `src/nova/core/config.py` does NOT exist yet — this story creates it
- `src/nova/{app,cli}.py` are Story 1.1 placeholders — NOT touched here
- `src/nova/adapters/*`, `src/nova/systems/*`, `src/nova/ports/*`, `src/nova/setup/*` are empty package shells
- `config/modes/coding.yaml`, `config/exclusions.yaml`, `config/settings.defaults.yaml` exist and are Story 1.0 pinned (NOT touched here)
- `docs/config-schemas.md` pins the schema (NOT touched here except possibly cross-reference additions)
- `tests/unit/core/test_exceptions.py`, `test_types.py`, `test_core_isolation.py`, `test_events.py`, `test_storage_engine.py`, `test_migration_runner.py` exist
- `tests/integration/test_migrations_integration.py` (Story 1.5)
- No `tests/unit/core/test_config.py` — this story creates it
- `pyproject.toml` has `pyyaml>=6.0` and `types-pyyaml>=6.0` already — NOT touched
- Tests pass: ~336 at Story 1.5 end

This story **adds**:

- `src/nova/core/config.py` (new — dataclasses + `load_config` + private validators + `_DuplicateKeyRejectingLoader`)
- `tests/unit/core/test_config.py` (new — ~40 tests per AC #24)

This story **modifies**:

- `src/nova/core/__init__.py` — add 7 re-exports (alphabetized)
- `tests/unit/core/test_core_isolation.py` — add `config_module` allowlist frozenset + 4 tests + 2 parametrize-list extensions
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story lifecycle transitions

This story does NOT modify:

- `pyproject.toml` (pyyaml + types-pyyaml already present; no new deps; marker list unchanged)
- `src/nova/app.py`, `src/nova/cli.py`
- Any shipped defaults under `config/`
- `docs/config-schemas.md` (the schema doc is the input — don't touch it)

### Previous Story Intelligence — Story 1.5 (done 2026-04-14)

Story 1.5 landed the migration runner + initial schema. Key carry-forwards for Story 1.6:

- **Test file placement — `tests/unit/core/test_config.py`, flat under `unit/core/`.** Mirrors `test_storage_engine.py` and `test_migration_runner.py`. No subdirectory, no `__init__.py` (Story 1.1 D1 carry-forward).
- **Deterministic tests — `tmp_path` only.** Never `%LOCALAPPDATA%`. Every test writes its own YAML files into `tmp_path` subdirs. Use `caplog` for log assertions.
- **Opaque exception messages.** `StorageError("backup failed")` set the precedent — schema-level, not user-data-level. `ConfigError("malformed config: parse error")` follows the pattern. File paths go in log `extra`, not in the exception message.
- **Structured logging via `extra={...}`.** Story 1.5 formalized the `logger.info("...", extra={"version": ..., "description": ...})` pattern. This story uses the same pattern with the `surface` key for tier-notice routing.
- **Ruff rules active:** `E`, `F`, `I`, `UP`, `B`, `SIM`, `T20`. Ordinary code; no special carve-outs needed. `SIM105` (`contextlib.suppress`) may fire around the `yaml.YAMLError → skip file` path — use `contextlib.suppress(yaml.YAMLError)` if ruff asks.
- **mypy strict, zero `# type: ignore` in production code.** The single `cast(dict[str, object], parsed)` at the YAML narrowing boundary is acceptable with an inline comment, matching Story 1.4's pattern.
- **Storage engine `run_migrations` method exists** (Story 1.5 AC #9 / Task 7) and is NOT touched by this story. The composition root (Story 1.10) calls it after `load_config` returns — neither file knows about the other.
- **`_utc_now_iso` / `_default_timestamp` pattern is NOT used by this story.** Config validation has no timestamp concerns — the `applied_at` pattern belongs to migrations and (future) audit rows.
- **No new dependencies.** `pyyaml` and `types-pyyaml` are already in pyproject.toml (Story 1.1 added them for exactly this story).
- **Commit convention (Story 1.4/1.5 carry-forward):** terse, imperative, story ID prefix. Expected: `"Story 1.6: YAML config loader + immutable NovaConfig (core/config.py)"`.

### Git Intelligence — last 5 commits

```
c64849c Story 1.5: migration runner + 001_initial_schema (core/storage/migrations)
4ae06ee Story 1.4: SQLite storage engine (core/storage/engine.py)
7278eb9 Story 1.3: event bus + typed event classes (core/events.py)
ac1790c Story 1.2: domain exceptions + shared types (core/exceptions.py, core/types.py)
1da5c45 Story 1.1: scaffold Python project (src/ layout, pyproject.toml, uv.lock)
```

- **Commit style:** terse, imperative, story ID prefix + brief scope in parens. Follow.
- **Recent work sets the "new core module" pattern:** test_core_isolation.py carve-out frozenset, `__init__.py` re-export, dedicated test file under `tests/unit/core/`. Stories 1.3/1.4/1.5 all followed it. This story does the same for the yaml boundary.
- **Story 1.4 opened the sqlite3 carve-out**; Story 1.6 opens the yaml carve-out. Same pattern; a reader who has read Story 1.4/1.5 will recognize the shape immediately.
- **No prior `config.py` or `test_config.py`.** Greenfield for this story.

### Latest Tech Information (as of 2026-04-14)

- **PyYAML 6.0.x** is the installed version (per uv.lock from Story 1.1). `yaml.safe_load` is the canonical entry point; `yaml.load(..., Loader=SubclassOfSafeLoader)` is the supported way to subclass while retaining safety. `types-pyyaml>=6.0` is in dev-deps.
- **Python 3.12.13** — `dataclass(slots=True)` is stable and widely used. Combined with `frozen=True`, generates `FrozenInstanceError` on mutation. `__slots__` is auto-generated; no manual declaration needed.
- **`re.fullmatch` (3.4+)** — correct for the filename regex. `re.match` allows trailing characters silently.
- **ruff 0.5+** — the following rules may fire:
  - `S506` — `yaml.load` without `SafeLoader`. The Bandit `S` ruleset is NOT in ruff's current `select = ["E", "F", "I", "UP", "B", "SIM", "T20"]`, but Story 1.11 (CI quality gate) may tighten the gate. The AC's `# noqa: S506` + inline justification at the single `yaml.load(..., Loader=_DuplicateKeyRejectingLoader)` call is preemptive — costs one comment today and survives a future `S` activation without a CI break.
  - `B904` (`raise ... from ...`) — relevant anywhere we translate an inner exception. Fixed by `raise ConfigError("...") from err`.
  - `UP040` (PEP 695 `type`) — not applicable here (no type aliases in config.py).
  - `T20` (no `print`) — locked.
- **mypy 1.20.1 strict** — `dataclass(slots=True)` works cleanly under mypy strict. `types-pyyaml` stubs return `Any` for `safe_load` output; the one inline `cast(dict[str, object], parsed)` handles the narrowing at the YAML boundary.
- **pytest 8.x + pytest-asyncio 0.23+ (auto mode)** — this story's tests are all synchronous (config loading is pure-sync code). No `async def`, no `@pytest.mark.asyncio`.
- **`caplog` fixture** — captures log records via Python's logging module. The `caplog.records` list exposes full `LogRecord` objects; assertions on `record.extra` (available as attributes per structured logging) work cleanly. For the `surface` tier-notice assertion: `assert any(getattr(r, "surface", None) == "tier-notice" for r in caplog.records)`.
- **`yaml.constructor.ConstructorError`** — the exception raised by `_DuplicateKeyRejectingLoader` on duplicate keys. Catch this specifically (not `yaml.YAMLError`) if the distinction matters; in practice we translate both to `ConfigError` with slightly different messages.

### Project Structure Notes

- **Config source:** `src/nova/core/config.py` — path pinned by architecture.md:272, 1382; matches the "core is cross-cutting infrastructure" convention.
- **Config test:** `tests/unit/core/test_config.py` — flat under `unit/core/`, mirrors the other core-module test files.
- **Schema doc:** `docs/config-schemas.md` — pinned by Story 1.0; this story reads from it and may add a "Loader implementation status" row at the bottom, but MUST NOT mutate any schema rule.
- **Shipped defaults:** `config/modes/coding.yaml`, `config/exclusions.yaml`, `config/settings.defaults.yaml` — Story 1.0 pinned; this story does NOT read from them at runtime (first-run setup copies them to `%LOCALAPPDATA%/nova/` in Story 2.1; the loader reads from `%LOCALAPPDATA%/nova/` or test `tmp_path`).
- **Architecture.md divergence for this story:** architecture.md lines 498–511 show a settings YAML example including `telemetry_opt_in` and `bluntness: ruthless`. `docs/config-schemas.md#known-divergences-from-architecture-md` resolves both — the schema doc wins. This story follows the schema doc; `telemetry_opt_in` is not a field, `ruthless` falls back to `direct` with warning.

### Testing standards summary

- **Test framework:** pytest + pytest-asyncio (auto mode) + pytest-cov. Config tests are synchronous; no async decorators.
- **Unit tests** live in `tests/unit/core/test_config.py`. ~40 tests per AC #24.
- **No integration tests in this story.** The loader is pure input-to-output with no side effects beyond log records and filesystem reads; integration coverage is implicit (Story 1.10 will add a startup smoke test that calls `load_config`).
- **mypy strict** applies to both the production module and the test file. Annotate every fixture: `tmp_path: Path`, `caplog: pytest.LogCaptureFixture`.
- **tmp_path** is the exclusive `data_dir` root — never `%LOCALAPPDATA%`.
- **Each test constructs its own `data_dir` layout.** No shared state, no cross-test contamination. Helper `_write(...)` is a regular function, not a fixture.
- **No fixtures added to `tests/conftest.py`** (Story 1.4/1.5 precedent — keep conftest minimal).
- **Coverage target:** 100% of `config.py`. Every branch of every validator, the error paths for both singletons, the mode-file-skip paths, the empty-string normalization, the reserved-stem path.
- **Deterministic:** no clock dependency (no timestamps in config). No network, no Win32, no SQLite. Pure filesystem + YAML.
- **Failure-path coverage — every validation rule has at least one negative test:**
  - Parse error in each singleton.
  - Parse error in a mode file.
  - Duplicate key in each singleton.
  - Duplicate key in a mode file.
  - Missing required field (mode `name`, mode `apps`, etc.).
  - Invalid enum value (`bluntness: ruthless`).
  - Type mismatch (`briefing_recency_threshold_minutes: "60"`, `briefing_recency_threshold_minutes: true`).
  - Empty-string in exclusions (both `match` and `title_patterns`).
  - Mode stem edge cases (reserved name, dot in stem, `.yml` extension).

### Critical Don't-Miss Rules (from project-context.md + architecture.md + docs/config-schemas.md)

Carry-forward with rationale for this story:

- **"Config module is the single YAML reader."** (project-context.md:69) — this story materializes the rule. Enforcement: `test_core_isolation.py` carve-out.
- **"No system reads YAML/JSON config directly."** (architecture.md:1153) — ditto.
- **"API key lives in settings.yaml in the user data directory. Never hardcoded, never committed, never logged."** (project-context.md:179) — locked by the empty-string normalization AND the `test_api_key_never_appears_in_logs` regression test.
- **"User data lives in `%LOCALAPPDATA%/nova/`."** (project-context.md:158) — this story does NOT assume the directory; it validates the caller-supplied `data_dir`. Tests use `tmp_path`.
- **"Shipped defaults live in `config/`. Copied to user data dir on first run only."** (project-context.md:161) — this story is NOT where that copy happens; Story 2.1 owns it.
- **"No mutable module-level runtime state."** (project-context.md:55) — `load_config` is a pure function; no module-level state beyond the `logger`, the regex constant, and the `_TIER_NOTICE_EXTRA` dict-literal-as-constant.
- **"Absolute imports only."** (project-context.md:43) — `from nova.core.exceptions import ConfigError`, `from nova.core.types import BluntnessLevel`. Never relative.
- **"No `Any` in application code."** (project-context.md:47) — single `cast(...)` at the YAML narrowing boundary, documented inline. Everything else is concrete types.
- **"Domain exceptions only."** (project-context.md:40) — all failures surface as `ConfigError`. Underlying `yaml.YAMLError` / `OSError` chain via `from err`.
- **"No sensitive content in exception messages."** (project-context.md:176) — opaque messages, file paths in log `extra` only, api_key never in any string.
- **"Structured logging."** (project-context.md:128) — every warning uses `extra={...}` with typed keys; free-form interpolation is forbidden.
- **"No `print()` anywhere."** (project-context.md:44; ruff `T20`) — logger only.
- **"Dataclasses for all domain types. `frozen=True` for immutable value objects."** (project-context.md:38) — all six dataclasses frozen.
- **"Tests use isolated temp paths by default, never `%LOCALAPPDATA%/nova/`."** (project-context.md:160) — `tmp_path` exclusively.
- **"Startup/setup/migration paths must be idempotent."** (project-context.md:165) — `load_config(data_dir)` is pure and deterministic; calling twice returns equivalent `NovaConfig` instances.
- **"Schema-doc wins on disagreement with architecture.md."** (docs/config-schemas.md intro) — `telemetry_opt_in` excluded; `bluntness: ruthless` rejected.
- **"File stem is the mode identifier."** (docs/config-schemas.md §Mode schema) — `modes` dict keyed by stem; `name` is display-only. This is the single most-likely-to-be-misimplemented rule; AC #2 + dedicated tests lock it.
- **"Empty `api_key` = absent."** (docs/config-schemas.md §Settings schema) — canonical rule, single code path. AC #2 + AC #15 lock it.
- **"Unknown keys ignored at every depth."** (docs/config-schemas.md §Cross-cutting rules) — no warnings on unknown keys; forward-compatible for T2+ fields.

### Cross-story impact (what depends on this story's primitives)

| Consumer story | Uses from this story | Why |
|---|---|---|
| 1.7 Capability tier state machine | `NovaConfig.api_key` | TierManager's default-tier decision reads `api_key` — `None` means offline-local-only tier at startup. |
| 1.10 Composition root & CLI entrypoint | `load_config(data_dir)` → `NovaConfig`; `NovaConfig.db_path`, `NovaConfig.modes`, `NovaConfig.settings`, `NovaConfig.api_key` | `app.py` calls `load_config` once and threads the result into every system/adapter constructor. |
| 2.2 API key configuration | `UserSettings` schema, the empty-string-normalization rule | The setup flow writes `api_key: "sk-ant-..."` into `settings.yaml`; restart loads it via this loader. |
| 2.3 Guided mode creation wizard | `ModeConfig` schema + file-stem-is-identifier rule | The wizard generates mode files that match this loader's schema; stem-validity rule is duplicated in the wizard for create-time enforcement. |
| 3.2 BriefingAggregate & state determination | `NovaConfig.modes: dict[str, ModeConfig]` | BriefingAggregate merges Brain facts with available modes from NovaConfig. The `is_default` tie-breaking rule lives here. |
| 3.6 Mode restore & app launching | `NovaConfig.modes[mode].apps[i].executable` | Hands launches via `apps[].executable`; Eyes matches windows via the same value. Normalization (case-insensitive, `.exe`-stripped) happens in those stories, not the loader. |
| 4.1 Eyes Win32 context capture | `NovaConfig.modes[...]` (readonly) + `NovaConfig.exclusions` | Eyes checks every captured window against the exclusion list before emitting events. |
| 4.2 Exclusion boundary at capture layer | `ExclusionConfig.excluded_apps[].match`, `ExclusionConfig.excluded_title_patterns` | Exclusion matcher; case-fold (Turkish-locale edge case) addressed there per Story 1.0 deferred-work. |
| 5.1 Transparency command | All of `NovaConfig` (readonly) | Transparency display shows user-facing summary of loaded config. |
| 5.4 Tier status display & notification | Log records with `extra["surface"] == "tier-notice"` | The handler that routes those records to Skin's status line lives in this story. |
| 6.3–6.5 Mode editing via command | `ModeConfig` shape + file-stem-is-identifier rule | Mode-edit commands read the schema from this loader AND write back to the same files. |
| 7.2 Configurable bluntness levels | `UserSettings.bluntness: BluntnessLevel` | Voice reads bluntness at initialization. |

**Eleven downstream stories** consume Story 1.6's primitives. The schema doc's cross-reference table (§Cross-references) is the authoritative consumer list.

### Deferred items from Story 1.0 code review picked up here

Per `deferred-work.md` (2026-04-14 Story 1.0 code-review block):

1. **Duplicate YAML keys at same level** → addressed via `_DuplicateKeyRejectingLoader`. AC #9. Locked by two tests (singleton + mode file).
2. **Modes directory exists as a file** → addressed via the `ConfigError("modes path is not a directory")` path. AC #8. Locked by `test_modes_path_is_file_raises_config_error`.
3. **Reserved Windows filenames (CON, NUL, etc.) in mode names** → addressed via `_RESERVED_WIN_STEMS` + stem-validity check. AC #10. Story 2.3 also enforces at create time; this story is the loader-side defense. Locked by test using `nul.yaml` in `tmp_path`.

Items NOT picked up here (remain deferred):

- **Unicode case-fold edge case (Turkish locale)** — the loader does not case-fold anything; matching is done in Story 4.2. Deferred to Story 4.2. No action here.
- **First-run copy file-lock / antivirus interference** — that's Story 2.1's concern.
- **CRLF trailing whitespace in string values** — no multiline string fields in T1 schema. N/A this story.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6: Config Loader & Immutable NovaConfig](../planning-artifacts/epics.md) — canonical AC, lines 753–774.
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 1: Project Foundation & Core Infrastructure](../planning-artifacts/epics.md) — epic objectives.
- [Source: _bmad-output/planning-artifacts/architecture.md#Decision 3: Data Schema](../planning-artifacts/architecture.md) — lines 415–525, file-based vs. SQLite separation, YAML config schemas (divergences noted — docs/config-schemas.md wins).
- [Source: _bmad-output/planning-artifacts/architecture.md#Configuration Contract](../planning-artifacts/architecture.md) — lines 1137–1155, the "single YAML reader" rule and the NovaConfig shape.
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure](../planning-artifacts/architecture.md) — line 1382, `core/config.py` location.
- [Source: _bmad-output/planning-artifacts/prd.md#FR26](../planning-artifacts/prd.md) — "System applies user-configured personality settings (bluntness level, verbosity)."
- [Source: _bmad-output/planning-artifacts/prd.md#FR44](../planning-artifacts/prd.md) — "All system behavior that varies between users is driven by editable config files in the user data directory."
- [Source: _bmad-output/project-context.md](../project-context.md) — rules 38 (dataclasses), 40–47 (Python/Architecture), 55 (no module-level mutable state), 69 (single YAML reader), 128 (structured logging), 158–165 (user data, test isolation, idempotency), 176–179 (no sensitive content, API key location).
- [Source: docs/config-schemas.md](../../docs/config-schemas.md) — **the pinned schema for this story.** All field definitions, validation rules, and cross-cutting rules originate here. Change control: schema changes flow through a new numbered story, not silent edits to this loader.
- [Source: _bmad-output/implementation-artifacts/deferred-work.md](./deferred-work.md) — Story 1.0 deferred items: duplicate-keys rejection, modes-path-is-file, reserved Windows filenames. All three picked up in this story.
- [Source: _bmad-output/implementation-artifacts/1-5-migration-runner-and-initial-schema.md](./1-5-migration-runner-and-initial-schema.md) — prior story. Test file layout, opaque-message rule, structured-logging pattern, `test_core_isolation.py` carve-out pattern.
- [Source: _bmad-output/implementation-artifacts/1-2-domain-exceptions-and-shared-types.md](./1-2-domain-exceptions-and-shared-types.md) — `ConfigError` + `cause=` + `from err` chaining contract.
- [Source: src/nova/core/exceptions.py](../../src/nova/core/exceptions.py) — `ConfigError` definition.
- [Source: src/nova/core/types.py](../../src/nova/core/types.py) — `BluntnessLevel` enum (the T1 two-member set).
- [Source: config/modes/coding.yaml](../../config/modes/coding.yaml) — Story 1.0 shipped default, demonstrates expected mode-file shape.
- [Source: config/exclusions.yaml](../../config/exclusions.yaml) — Story 1.0 shipped default, demonstrates expected exclusions shape.
- [Source: config/settings.defaults.yaml](../../config/settings.defaults.yaml) — Story 1.0 shipped default, demonstrates expected settings shape (note: no `api_key` — first-run writes it).

## Dev Agent Record

### Agent Model Used

claude-opus-4-6[1m]

### Debug Log References

- Ruff auto-fixed import sorting and split a multi-name `from nova.core import ...` into 7 separate imports. Replaced the block with a single `import nova.core as core_pkg` + attribute access to keep the test compact.
- Ruff `UP035` flagged `typing.Callable` → required migration to `collections.abc.Callable`. Added `collections` to `CONFIG_ALLOWED_TOPLEVEL_MODULES` (matches precedent for events/engine allowlists) — documented inline.
- Mypy flagged `loader.construct_object` as untyped (types-pyyaml stub gap). Resolved with a narrow `cast("Callable[..., object]", loader.construct_object)` at the single boundary — no `# type: ignore`, matches Story 1.4's cast-at-stubs-boundary precedent.
- Mypy flagged `cls.__dataclass_params__` access in the frozen-check test. Simplified to assert `is_dataclass` + `hasattr(cls, "__slots__")`; frozenness is locked separately by `test_frozen_instance_mutation_raises`.
- Initial static-analysis test (`test_config_does_not_use_unsafe_yaml_load`) used a text regex that false-positived on the docstring literal `yaml.load(...)`. Rewrote to walk the AST and inspect actual `ast.Call` nodes — robust against docstring/comment text. **Carry-forward for future stories:** static-analysis tests that check for forbidden call patterns should walk the AST (`ast.walk` + `ast.Call` inspection), not grep source text — text regex false-positives on docstrings and comments.

### Completion Notes List

- Implemented `src/nova/core/config.py` (~540 lines) exposing `NovaConfig`, `ModeConfig`, `AppConfig`, `ExclusionConfig`, `ExcludedAppConfig`, `UserSettings` as `@dataclass(frozen=True, slots=True)` value types plus the single public `load_config(data_dir: Path) -> NovaConfig` entry point. No inheritance between the six dataclasses (per Dev Notes slots-inheritance guardrail).
- YAML safety: every parse path routes through `_DuplicateKeyRejectingLoader(yaml.SafeLoader)` which rejects duplicate keys at the same mapping level (Story 1.0 deferred-work.md item 1 resolved). Single `yaml.load(..., Loader=_DuplicateKeyRejectingLoader)` call carries a preemptive `# noqa: S506` for Story 1.11's CI-gate expansion.
- Three Story 1.0 deferred-work items closed: (1) duplicate-key rejection via SafeLoader subclass; (2) modes-path-is-file edge case surfaces as `ConfigError("modes path is not a directory")`; (3) reserved Windows filenames (`CON`, `PRN`, `AUX`, `NUL`, `COM1–9`, `LPT1–9`) skipped with `WARNING` log.
- Data-dir precondition hardened: missing directory → `ConfigError("data directory missing")`; data-dir-is-file case (added during adversarial review) → `ConfigError("data directory path is not a directory")`.
- `api_key` promoted out of `UserSettings` onto `NovaConfig.api_key: str | None`. Empty-string and whitespace-only values normalize to `None` at load time; `UserSettings` does NOT expose the field (locked by `test_user_settings_field_set_is_exact` — asserts exact set `{bluntness, skip_briefing_if_recent, briefing_recency_threshold_minutes}` — so `telemetry_opt_in` / `api_key` regressions fire immediately).
- Boolean-before-int check on `briefing_recency_threshold_minutes` prevents YAML `true` silently becoming 1 (locked by `test_briefing_threshold_bool_falls_back_60`).
- `bluntness: ruthless` explicitly falls back to `DIRECT` with warning — schema doc wins over architecture.md divergence.
- URL scheme allowlist enforced at load (`http://`, `https://` only, case-insensitive). `file://` / `javascript:` / `data:` / bare paths all dropped with warning.
- Warning routing contract: every tier-notice-eligible warning carries `extra={"surface": "tier-notice"}`. Locked by `test_tier_notice_surface_attached_to_missing_file_warning` — Story 5.4's handler consumes the surface attribute.
- API-key regression gate: `test_api_key_never_appears_in_logs` writes a realistic key, triggers a full load at DEBUG level, and asserts the key substring does not appear in ANY log record's formatted message OR any string-typed LogRecord attribute.
- Static-analysis gate: `test_config_does_not_use_unsafe_yaml_load` walks `config.py` AST and asserts every `yaml.load(...)` call carries a `Loader=` kwarg, and that `_DuplicateKeyRejectingLoader` subclasses `yaml.SafeLoader` (MRO check).
- Extended `test_core_isolation.py` with 4 new tests + `CONFIG_FORBIDDEN_TOPLEVEL_MODULES` / `CONFIG_ALLOWED_TOPLEVEL_MODULES` frozensets. `yaml` is carved out of the global forbidden set for `config.py` only, mirroring the `sqlite3` carve-out for `storage/engine.py`.
- `src/nova/core/__init__.py` re-export count: 23 → 30 (added `AppConfig`, `ExcludedAppConfig`, `ExclusionConfig`, `ModeConfig`, `NovaConfig`, `UserSettings`, `load_config`). Alphabetized.
- Quality gate: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ tests/ && uv run pytest` — all green. Test count: 344 → **406** (+62 across 56 new `test_config.py` tests, 4 new config-specific `test_core_isolation.py` tests, and 2 parametrize-expansion entries — one each in `test_no_relative_imports` and `test_no_dynamic_imports_of_forbidden_modules`).

### File List

**New files:**

- `src/nova/core/config.py` — single YAML reader; dataclasses + validators + `load_config`.
- `tests/unit/core/test_config.py` — 56 tests covering shape, happy path, missing/malformed files, mode-filter edge cases, validation, BOM tolerance, unknown-keys forward-compat, tier-notice surface, api-key-never-logged, AST-based YAML-safety static-analysis gate, and core-package re-export smoke test.

**Modified:**

- `src/nova/core/__init__.py` — added 7 config re-exports; `__all__` re-alphabetized (30 names).
- `tests/unit/core/test_core_isolation.py` — added `config_module` import, `CONFIG_FORBIDDEN_TOPLEVEL_MODULES` + `CONFIG_ALLOWED_TOPLEVEL_MODULES` frozensets, 4 config-specific isolation tests, extended 2 parametrize lists (`test_no_relative_imports`, `test_no_dynamic_imports_of_forbidden_modules`) to include config_module.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story key transitioned `ready-for-dev` → `in-progress` → `review`.

**Not modified (verified clean):**

- `pyproject.toml` (`pyyaml` + `types-pyyaml` already present from Story 1.1; no new deps; marker list unchanged).
- `src/nova/app.py`, `src/nova/cli.py` (composition-root wiring is Story 1.10's job).
- `config/modes/coding.yaml`, `config/exclusions.yaml`, `config/settings.defaults.yaml` (Story 1.0 pinned).
- `docs/config-schemas.md` (schema doc is the input contract — untouched).
- `src/nova/core/storage/engine.py`, `src/nova/core/storage/migrations/runner.py` (not involved).
