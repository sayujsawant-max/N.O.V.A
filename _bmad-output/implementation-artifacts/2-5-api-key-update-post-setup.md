# Story 2.5: API Key Update Post-Setup

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user whose API key has expired, been revoked, or needs changing,
I want a clear, documented path to update my API key after initial setup and have N.O.V.A. pick up the new value on the next `nova` run without crashing,
So that a rotated or invalid key never strands me without a way forward.

## Story-type classification

**Pattern-application story** (per Epic 1 retrospective, 2026-04-15). Story 2.5 introduces **no new integration boundary** — it:

1. Wires a decision the config loader and composition root both already have enough information to make (initial tier := OFFLINE when `config.api_key is None`).
2. Surfaces two documented paths (argparse `--help` epilog + repo-level `README.md`) pointing at `%LOCALAPPDATA%/nova/settings.yaml` as the post-setup update surface.
3. Adds deterministic tests around the restart-picks-up-new-key contract.

The full boundary-invariant sweep is therefore **not** required; a lighter Review-Focus section at the end captures the specific lifecycle + logging invariants this story does touch. Any deviation (e.g., the dev agent proposes adding a `nova config` subcommand, a settings file watcher, or an in-session reload) must be rejected per AC #1's scope fence.

## Acceptance Criteria

### Group A: Scope fence — no runtime reload, no interactive wizard in T1

1. Story 2.5 ships **no** interactive key-change wizard, **no** `nova config` subcommand, **no** `nova key update` CLI verb, **no** settings-file watcher or hot-reload, **no** mid-session key change surface. The documented interface for post-setup key changes in T1 is: **the user edits `%LOCALAPPDATA%/nova/settings.yaml` in a text editor, then re-runs `nova` on their next session**. A `nova config` command is an explicit T2 candidate (epics.md:1043) and must not be added here. An AST guard test (AC #19) asserts no new CLI subparser or new setup module ships under this story's name.

### Group B: Config loader already picks up changes on restart (regression guard)

2. `nova.core.config.load_config(data_dir)` is the **sole reader** of `settings.yaml` (project-context.md:69 — "Config module is the single YAML reader"). No code path caches the YAML content across invocations. A regression test asserts: given a written `settings.yaml` with `api_key: "k1"`, `load_config(data_dir).api_key == "k1"`; overwrite the file with `api_key: "k2"`; call `load_config(data_dir)` again in the same test process (no subprocess); assert the second return's `api_key == "k2"`. The test does NOT restart the Python interpreter — the contract under test is "no in-memory caching of settings", not "subprocess separation".

3. `_normalize_api_key` behavior (already shipped in Story 2.2) is locked by parametrized tests in this story so future refactors can't silently regress it:
   - `api_key: "sk-ant-abc"` → `"sk-ant-abc"` (untouched)
   - `api_key: " sk-ant-abc "` → `"sk-ant-abc"` (whitespace stripped)
   - `api_key: ""` → `None` (empty string → None)
   - `api_key: "   "` → `None` (whitespace-only → None)
   - `api_key:` (YAML null) → `None`
   - `api_key` key absent → `None`
   - `api_key: 123` → `None` (non-string types silently normalize to None per Story 2.2)
   - `api_key: [sk-ant-abc]` → `None` (non-string — no crash)
   If any of these tests already exist in `tests/unit/core/test_config.py`, this story re-homes or references them; it does not duplicate assertions. The canonical location for the full table is `tests/unit/core/test_config.py::TestApiKeyNormalization` (new class or extend existing).

### Group C: Initial tier is OFFLINE when api_key is absent (core behavior change)

4. **`nova.app.create_app(config)` derives `initial_tier` from `config.api_key`** instead of hard-coding `CapabilityTier.FULL`:
   - `config.api_key is None` (missing, empty, whitespace-only, non-string) → `initial_tier = CapabilityTier.OFFLINE`
   - `config.api_key is not None` → `initial_tier = CapabilityTier.FULL`

   The change is a single decision inside `create_app` before the `TierManager(...)` construction. `TierManager`'s `initial_tier` keyword argument (see `TierManager.__init__` in `src/nova/core/tiers.py`) is the injection point — no new field on `NovaApp`, no new method on `TierManager`.

5. When `create_app` picks `CapabilityTier.OFFLINE`, it logs one INFO record — `nova.app` logger, message `"starting in offline-local-only tier (no API key configured)"`, no `extra` field that echoes the key itself or a redacted form. The canonical extra payload is `{"reason": "no_api_key"}` (closed-set string; future reasons extend the enum via a new story). This is a composition-root-level log, not a TierManager transition event — no `TierChanged` event is emitted because the FULL→OFFLINE transition didn't happen; OFFLINE was the initial state.

6. When `create_app` picks `CapabilityTier.FULL` with a present key, there is **no new log line** — the existing `"tier manager constructed"` INFO record in `create_app` remains unchanged. This keeps the signal-to-noise ratio high: a log line only fires when the tier decision materially deviates from the default FULL.

### Group D: One-time offline notice on cli.py startup (user-facing)

7. `nova.cli._async_main` renders **exactly one** user-facing stderr line when `config.api_key is None`, emitted **after** the existing `"N.O.V.A. initialized"` INFO record and **before** the session-shell placeholder INFO record — see AC #10 for the exact Step-number placement. The INFO records go to the file logger (Phase B has already replaced Phase A by this point); the notice goes directly to stderr. The two channels don't interleave in user-visible output, so placing the notice after the success log keeps the semantic ordering "confirm bootstrap succeeded first, then surface the consequence of the absent key." The line bypasses Voice (project-context.md:66 — "Operational output bypasses Voice") and is NOT a Rich `Panel`, NOT a colored bar, just a single amber line:

   ```
   ⚠ Cloud reasoning unavailable. Running in offline-local-only tier. To add or update your API key, edit %LOCALAPPDATA%/nova/settings.yaml and re-run nova.
   ```

   - The `⚠` codepoint matches the whitelist Stories 2.2/2.3/2.4 already established (`✓`, `✗`, `⚠`, `—`, box-drawing U+2500–U+257F).
   - The line ends with a literal period (no trailing whitespace, no exclamation).
   - No emoji. No sycophantic framing (`"Don't worry"`, `"No problem"`, `"All good"`, `"You can still…"`). No "please" / "kindly".
   - The path uses the literal `%LOCALAPPDATA%/nova/settings.yaml` substring (the user-facing form), NOT the resolved absolute path returned by `_resolve_data_dir`. Rationale: the resolved form leaks the username (`C:\Users\<username>\AppData\Local\nova\settings.yaml`); the `%LOCALAPPDATA%` form is universally interpretable on Windows and doesn't widen the log surface. A test asserts the captured output contains the literal `%LOCALAPPDATA%/nova/settings.yaml` substring and does NOT contain any `C:\Users\` substring.

8. **Exactly once** means: the notice prints once per `nova` invocation. It is not printed twice on the same cold start. It is not printed again when the session placeholder hands back to `main()`. It is not printed at all if `config.api_key is not None`. A test captures stderr + stdout across a full `_async_main` run with `api_key=None` and asserts the notice string appears exactly once; a second test with `api_key="sk-ant-test"` asserts the notice string never appears.

9. The notice is emitted **only** when `config.api_key is None` AND `create_app` returned successfully. If `create_app` raises (e.g., `StorageError` from migrations), the notice must NOT print — the primary storage failure is what the operator needs to see; layering a secondary offline warning on top would create a misleading two-line failure output. A test monkeypatches `create_app` to raise `StorageError` inside `_async_main` and asserts the notice never reaches stderr.

10. **Exact placement** in the existing `_async_main` 8-step bootstrap (`cli.py`, `_async_main` function — reference the "Two-phase logging" section of the module docstring and the `_configure_stderr_logging` / `_configure_file_logging` helpers for the channel lifecycle):

    ```
    Step 1: Phase A stderr logging
    Step 2: _resolve_data_dir
    Step 2.5: validate_data_dir
    Step 3: load_config
    Step 4: Phase B file logging (removes Phase A)
    Step 5: create_app
    Step 6: "N.O.V.A. initialized" INFO log              ← happens first
    Step 6.5: _emit_offline_notice_once(config.api_key)  ← NEW: Story 2.5 insertion
    Step 7: session shell placeholder INFO log           ← unchanged
    Step 8: teardown (await app.close()) in finally      ← unchanged
    ```

    By Step 5, Phase A has already been torn down (Step 4 removes it). A `logger.warning(...)` call at Step 6.5 would go only to the file logger — the user wouldn't see it at the terminal. The notice must therefore be emitted via **direct `sys.stderr.write(...)` + flush**, matching the same-channel pattern the module already uses in `main()` for the pre-logger `ConfigError` surface (the stderr write inside `_parse_log_level`'s error path). A helper `_emit_offline_notice_once(api_key: str | None) -> None` lives in `cli.py` next to `_configure_file_logging`. Placing the call at Step 6.5 (not earlier) means: (a) the success log lands first so operators can correlate the file log with what they saw on screen, and (b) the notice is guaranteed not to precede a later `create_app` failure — by this step, `create_app` has already returned.

### Group E: Documented update path — argparse help epilog + README

11. The `argparse.ArgumentParser` built by `_build_parser()` in `src/nova/cli.py` gains an **epilog** string so `nova --help` ends with:

    ```
    API key:
      To add or update your Anthropic API key, edit:
          %LOCALAPPDATA%/nova/settings.yaml
      Change the `api_key:` line, save, and re-run `nova`.
      Removing the line starts N.O.V.A. in offline-local-only tier.
    ```

    The epilog uses `argparse.RawDescriptionHelpFormatter` (new `formatter_class=...` on the parser) so the indentation and line breaks survive. A test invokes the parser with `--help` and asserts the epilog substrings (`"API key:"`, `"%LOCALAPPDATA%/nova/settings.yaml"`, `"re-run `nova`"`) appear in the captured help output. The test uses `parser.format_help()` (no subprocess) so it runs in-process and remains deterministic.

12. A new repository-root `README.md` file is created with (at minimum) these sections in this order:
    - **Project title + one-line purpose** (the PRD product-name line).
    - **Quick start** — reference to `setup.bat` as the entrypoint (Story 2.1), one line.
    - **API key management** — documented update path per this story. The README describes *only* behavior that ships in Story 2.5; the first-cloud-call degradation path (invalid-but-present key → tier degrade) is owned by Story 3.5 and is deliberately not claimed here. Exact body:
      ```markdown
      ## API key management

      N.O.V.A. uses your Anthropic API key for cloud reasoning in the FULL tier.

      - First-run setup prompts for the key and writes it to
        `%LOCALAPPDATA%/nova/settings.yaml`.
      - To update the key (expired, revoked, rotated):
        1. Open `%LOCALAPPDATA%/nova/settings.yaml` in any text editor.
        2. Change the `api_key:` value.
        3. Save and re-run `nova`. The next start reads the new value.
      - To remove the key (operate locally only), delete or comment out the
        `api_key:` line. N.O.V.A. will start in the offline-local-only tier
        with a one-time notice. Local features (modes, memory, transparency)
        continue to work.
      - A present but invalid key does NOT crash bootstrap — `nova` starts
        normally and the error surfaces only when cloud reasoning is actually
        requested. Automatic tier degradation from invalid-key signals will
        arrive in a later release.
      ```
    - **Further documentation** — a short list of pointers: `docs/config-schemas.md`, `docs/cross-cutting-patterns.md`, `docs/development.md`.

    `README.md` is deliberately minimal: this story does not ship a full onboarding narrative — that is tracked separately. The test-assertable content is four substrings in the section body: `"## API key management"`, `"%LOCALAPPDATA%/nova/settings.yaml"`, `"offline-local-only tier"`, and `"does NOT crash bootstrap"`; a repository-root-file test (`tests/unit/docs/test_readme.py` or similar) asserts all four appear exactly once and in that relative order. The test also asserts the forbidden phrase `"degrades to offline-local-only on the first cloud failure"` does NOT appear, so a future over-eager edit can't re-introduce the Story-3.5-overpromise.

13. `docs/config-schemas.md` (already shipped) is **not modified** — its `api_key` documentation (the schema-level contract) is already authoritative. The README's "API key management" section is the update-instructions surface; the schema doc is the field-definition surface. They do not duplicate.

### Group F: Logging opacity — the key must never appear in any log or message

14. The API key value is **never** written to any log record produced under this story's scope. Specifically:
    - `create_app`'s new INFO line (AC #5) logs `{"reason": "no_api_key"}` — no redacted form like `"sk-ant-***"`, no length hint, no hash.
    - `_emit_offline_notice_once` writes to stderr only; it never calls `logger.*`.
    - `cli.py`'s existing `"N.O.V.A. initialized"` INFO record in `_async_main` continues to log `api_key_present: bool` — an unchanged, already-shipped field. This story does NOT add a new `api_key_value` field anywhere.
    - An AST guard test in `tests/unit/test_api_key_log_opacity.py` walks `ast.Call` nodes under `src/nova/app.py` and `src/nova/cli.py` and asserts no call to `logger.*` has a string argument or `extra` dict containing the substring `config.api_key` where the key value itself (as opposed to its presence as a bool) is passed. This extends the Story 2.2 opacity AST guard pattern (project-context.md:179 — "API key lives in settings.yaml… never logged"); the existing guard covers the setup-wizard subtree. This story's guard covers the composition-root subtree.

15. The one-time offline notice (AC #7) does not echo the user's key **even when the user set it to an obviously fake value** — the notice text is fully static with no `{api_key}` interpolation anywhere. This is enforced by the Story 2.2 AST guard's existing rule (`key` / `api_key` / `raw` local-variable interpolation is banned across `src/nova/setup/`); Story 2.5 extends the same guard to `src/nova/cli.py` and `src/nova/app.py` (two additional scan roots, same rule, same test file).

### Group G: Testing

16. **Unit tests for `create_app` initial-tier derivation** (`tests/unit/test_app.py`, extending existing):
    - `test_initial_tier_is_offline_when_api_key_is_none` — pass a `NovaConfig(api_key=None, …)`; assert `app.tier_manager.tier is CapabilityTier.OFFLINE`; assert the `"starting in offline-local-only tier"` INFO record is logged with `extra={"reason": "no_api_key"}`.
    - `test_initial_tier_is_full_when_api_key_is_present` — pass `NovaConfig(api_key="sk-ant-test", …)`; assert `app.tier_manager.tier is CapabilityTier.FULL`; assert no offline INFO record appears.
    - `test_initial_tier_is_offline_when_api_key_is_empty_string_after_load_config` — regression on the contract that `load_config` normalizes `""` to `None`; the test writes `api_key: ""` to a temp settings.yaml, calls `load_config`, passes its result to `create_app`, asserts OFFLINE. (This is the end-to-end lock — previous tests inject `NovaConfig` directly.)
    - `test_no_extra_field_echoes_the_key` — pass `NovaConfig(api_key="sk-ant-VERYSECRET", …)`; capture all log records emitted during `create_app`; assert no record's message or `extra` values contain the substring `"VERYSECRET"`.

17. **Unit tests for `_emit_offline_notice_once`** (`tests/unit/test_cli_offline_notice.py`, new):
    - `test_notice_prints_to_stderr_when_api_key_is_none` — capture stderr; call the helper with `api_key=None`; assert the notice text matches AC #7 verbatim (including `%LOCALAPPDATA%/nova/settings.yaml`, the `⚠` codepoint, the trailing period).
    - `test_notice_is_silent_when_api_key_is_present` — call with `api_key="sk-ant-test"`; assert stderr is empty.
    - `test_notice_contains_no_user_home_path_when_localappdata_resolved` — even if the caller also has the resolved `data_dir` on hand, the helper's notice body is static and does NOT include `C:\Users\` or any substring derived from `os.path.expanduser("~")`. Assert the captured stderr does not contain `"C:\\Users\\"` or `"C:/Users/"`.
    - `test_notice_does_not_echo_the_key_even_if_somehow_passed_nonstandard` — defense-in-depth: even if a future edit accidentally changes the signature to accept a key, the current test passes a bogus string and asserts the string is not in the output. (Skipped if the helper's signature is unchanged from AC #10; otherwise implement.)
    - `test_notice_integration_in_async_main_when_api_key_none` — patch `create_app` to return a fake `NovaApp` with `api_key=None` behavior; run `_async_main` end-to-end with stderr capture; assert the notice appears exactly once, emitted at Step 6.5 (after the `"N.O.V.A. initialized"` file-log record and before the session-placeholder file-log record). The test verifies ordering by inspecting the file-log record sequence (via `caplog`) AND the stderr content (via `capsys`) — both must match the documented ordering.
    - `test_notice_does_not_fire_when_create_app_raises` — patch `create_app` to raise `StorageError`; run `_async_main`; assert the notice string is never in the captured stderr. Exit code is `EXIT_STORAGE_ERROR`.

18. **Unit test for the argparse help epilog** (`tests/unit/test_cli.py`, extending existing):
    - `test_help_epilog_documents_key_update_path` — build the parser via `_build_parser()`; call `parser.format_help()`; assert the captured help contains (in order) `"API key:"`, `"%LOCALAPPDATA%/nova/settings.yaml"`, and ``"re-run `nova`"``. Assert the help uses `argparse.RawDescriptionHelpFormatter` (read `parser.formatter_class`).

19. **AST guard test — no new CLI subparser in this story** (`tests/unit/test_cli_no_new_subcommands.py`, new):
    - Walk `ast.walk` over `src/nova/cli.py`; assert no call to `add_subparsers(...)` and no new `ArgumentParser` besides the single module-level parser built by `_build_parser`. Rationale: AC #1's scope fence. This test ships in Story 2.5 and protects against accidental subparser-mode creep in subsequent stories touching `cli.py` before Story 3.9 (which explicitly owns in-session commands).

20. **Unit tests for `README.md` content contract** (`tests/unit/docs/test_readme.py`, new):
    - `test_readme_exists_at_repo_root` — assert `README.md` exists at the repo root (parent of `src/`).
    - `test_readme_has_api_key_management_section` — read the file; assert the four substrings from AC #12 appear in the correct relative order.
    - `test_readme_does_not_overpromise_story_35_behavior` — assert the forbidden phrase `"degrades to offline-local-only on the first cloud failure"` does NOT appear anywhere in `README.md`. If a future edit wants to claim this behavior, Story 3.5 must land first and this guard must be updated.
    - `test_readme_does_not_leak_resolved_user_path` — assert no `"C:\\Users\\"` or `"C:/Users/"` substring (same opacity rule as the stderr notice).

21. **Integration test — changed key is picked up on the next `nova` run** (`tests/integration/test_api_key_update.py`, new):
    - `test_settings_yaml_edit_is_visible_on_next_load_config` — write `api_key: "k1"` to a temp data_dir's `settings.yaml`; call `load_config(data_dir)` → assert `config.api_key == "k1"`; overwrite the file with `api_key: "k2"` (atomic overwrite via `os.replace` — match Story 2.2's write pattern); call `load_config(data_dir)` a second time → assert `config.api_key == "k2"`. No subprocess boundary; the test runs both loads in the same interpreter.
    - `test_settings_yaml_removed_key_is_picked_up_as_none` — write `api_key: "k1"`; `load_config` → `"k1"`; rewrite the file with the `api_key:` line removed (and `bluntness: direct` kept, so `settings.yaml` is still a valid mapping root); `load_config` → `None`.
    - `test_removed_key_degrades_initial_tier_to_offline_on_next_create_app` — chain: `load_config` → `config.api_key = "k1"` → `create_app` → FULL; overwrite settings.yaml (remove the key) → `load_config` → `config.api_key is None` → `create_app` (fresh call in same test) → OFFLINE. Asserts the full restart-picks-up-change contract end-to-end.

22. **Integration test — invalid (rotten) key does not crash `nova`** (`tests/integration/test_api_key_update.py`):
    - `test_stale_or_invalid_key_completes_bootstrap_without_exception` — write `api_key: "sk-ant-obviously-invalid"` to settings.yaml; run `_async_main` end-to-end with default env; the `TierManager` is still constructed with `_AlwaysHealthyCheck` (per app.py:60-75) which never fails, so FULL tier sticks at bootstrap; assert `_async_main` returns `EXIT_OK` (0). This asserts that **bootstrap does not revalidate the key via a cloud ping** (that would violate setup's 8-second budget if applied on every `nova` invocation — per Story 2.4 AC #23) and that a rotted key therefore fails only at the first real cloud call (Nerve's `report_failure` path, Story 3.5 — not this story's surface).
    - Test docstring explicitly records: "An invalid-but-present key degrades to OFFLINE at first cloud-call failure via TierManager.report_failure (Story 1.7, Story 3.5). Story 2.5's contract is only: bootstrap succeeds, one-time notice is NOT shown (because the key is present), and the existing tier machinery handles the eventual failure. The first-cloud-call degradation path is Story 3.5's test surface."

23. **Test determinism** — all tests in this story use in-process `load_config` and `create_app` calls (no subprocess, no real filesystem under `%LOCALAPPDATA%`). The one acceptable exception is the `tmp_path` fixture for `settings.yaml` writes — standard pytest-tmp-dir pattern.

### Group H: Explicit non-goals (scope fence)

24. Explicitly **NOT** in Story 2.5 scope:
    - Any in-session `help` command implementation — Story 3.9.
    - Any Skin-rendered Tier Notice Rich component — Story 5.4 (Tier Notice component per ux-design-spec.md:815).
    - Any re-validation of the key at `nova` startup via a cloud ping — setup's responsibility during the wizard (Story 2.2) and Nerve's responsibility during session commands (Story 3.5). A boot-time cloud ping would add 1–15 s to every `nova` invocation and violate the <8 s setup budget posture per Story 2.4 AC #23.
    - Any file-watching / hot-reload of `settings.yaml` while a session is running — out of scope. Full T1 behavior is restart-required.
    - Any `nova config` / `nova key rotate` subcommand — explicit T2 candidate (epics.md:1043).
    - Any edit to `docs/config-schemas.md` — the schema doc already documents `api_key` (architecture.md:513-519 mirrors it); this story adds the *update* instructions in README, not a duplicate schema section.
    - Any change to `_AlwaysHealthyCheck` behavior in `nova/app.py` — the stub never fails, and that's correct for T1 (the real Claude health check lands with the Claude adapter in Epic 3). The initial-tier decision is enough to distinguish "user has a key" from "user has no key" at bootstrap.
    - Any migration of the key to a secret store (keyring, DPAPI, Windows Credential Manager) — tracked as a T2/trust-hardening candidate. T1 accepts the plaintext file in `%LOCALAPPDATA%/nova/settings.yaml` because the data dir is per-user and `%LOCALAPPDATA%` is ACL-restricted.

## Tasks / Subtasks

- [x] **Task 1: Derive `initial_tier` from `config.api_key` in `create_app`** (AC: #4, #5, #6, #16)
  - [x] In `src/nova/app.py`, compute `initial_tier = CapabilityTier.OFFLINE if config.api_key is None else CapabilityTier.FULL` immediately before the `TierManager(...)` construction in `create_app`. Locate by searching for `"tier manager constructed"` — that INFO log sits right after the constructor call.
  - [x] Pass the computed value to `TierManager(..., initial_tier=initial_tier)`.
  - [x] Add one INFO log record under the `nova.app` logger when the value is OFFLINE: message `"starting in offline-local-only tier (no API key configured)"`, `extra={"reason": "no_api_key"}`. Do NOT add a corresponding FULL-case log — the existing `"tier manager constructed"` line already covers that case.
  - [x] Add the four unit tests in `tests/unit/test_app.py` per AC #16. Use parametrization where the same test body works for multiple inputs.
  - [x] Verify no existing test in `tests/unit/test_app.py` relies on `initial_tier=CapabilityTier.FULL` behavior when `api_key is None` — if any do (they shouldn't today), update them and add a comment pointing at Story 2.5 as the reason.

- [x] **Task 2: Emit the one-time offline notice in `cli._async_main`** (AC: #7, #8, #9, #10, #15, #17)
  - [x] Add a private module-level helper `_emit_offline_notice_once(api_key: str | None) -> None` in `src/nova/cli.py`. Signature is minimal — takes the key (or None) and nothing else — so future refactors can't silently widen what the function sees. Body:
    ```python
    def _emit_offline_notice_once(api_key: str | None) -> None:
        if api_key is not None:
            return
        # Notice is emitted after Phase A is removed (cli.py:268), so we
        # bypass logging and go direct to stderr. Format mirrors the
        # Story 2.2 setup-time skip notice (api_key.py:_show_skip_notice).
        sys.stderr.write(
            "\u26a0 Cloud reasoning unavailable. Running in "
            "offline-local-only tier. To add or update your API key, "
            "edit %LOCALAPPDATA%/nova/settings.yaml and re-run nova.\n"
        )
        sys.stderr.flush()
    ```
  - [x] In `_async_main`, call `_emit_offline_notice_once(config.api_key)` at **Step 6.5** per AC #10 — on the line immediately following the existing `logger.info("N.O.V.A. initialized", …)` call and immediately preceding the `logger.info("session shell placeholder …")` line. Locate the call by searching for the string `"N.O.V.A. initialized"` in `cli.py` rather than by line number, since this file will edit around that region.
  - [x] The call site must be inside the `try:` block that owns `app.close()` in the `finally`. Placing it before `create_app` would run with a non-constructed graph; placing it after `app.close` would risk double-emit if `app.close` raised and the caller retried. Step 6.5 is the one placement where (a) bootstrap is known to have succeeded, (b) the success log has already landed, and (c) teardown has not yet started.
  - [x] Add the six unit tests in `tests/unit/test_cli_offline_notice.py` per AC #17. Use `capsys.readouterr().err` for stderr capture (pytest-standard).

- [x] **Task 3: Add `--help` epilog documenting the key-update path** (AC: #11, #18)
  - [x] Update `_build_parser()` in `src/nova/cli.py`:
    - Add `formatter_class=argparse.RawDescriptionHelpFormatter`.
    - Add `epilog="…"` with the AC #11 text, using a triple-quoted string and a module-level constant `_HELP_EPILOG` so the string is reusable in tests without parser introspection. The constant is defined alongside `_VALID_LOG_LEVELS` at the top of the module.
  - [x] The epilog string uses the literal Windows-style `%LOCALAPPDATA%/nova/settings.yaml` — forward slashes inside the env-var path are intentional (matches architecture.md:493 wording, matches cross-platform PEP 8 string rendering) and avoid backslash-escape pain in the source string.
  - [x] Add the unit test in `tests/unit/test_cli.py::test_help_epilog_documents_key_update_path` per AC #18.
  - [x] Verify `python -m nova --help` (or equivalently `nova --help` after `uv run`) renders cleanly at 80-column terminals — the RawDescriptionHelpFormatter preserves the indentation. No test automates the 80-col width because argparse's formatter is the authoritative behavior; manual smoke at the end of the task is sufficient.

- [x] **Task 4: Create repository-root `README.md`** (AC: #12, #13, #20)
  - [x] Create `README.md` at the repo root (sibling to `pyproject.toml`, `setup.bat`). Use the exact content in AC #12 for the "API key management" section; the other sections (title, Quick start, Further documentation) are minimal one-to-three-line stubs pointing at existing artifacts.
  - [x] Do NOT add a PRD-derived marketing section or a full onboarding narrative — both are out of scope for Story 2.5 and belong in a separate docs story.
  - [x] Add the four unit tests in `tests/unit/docs/test_readme.py` per AC #20 (existence, ordered-markers-with-unique-count, forbidden-phrase guard, and opacity/no-user-home-leak). The tests read `Path(__file__).resolve().parent.parent.parent.parent / "README.md"` (four parents: `docs/` → `unit/` → `tests/` → repo-root) — verify the `../../../..` count matches the actual file layout before committing. If the test file lives at `tests/unit/docs/test_readme.py`, three `.parent` calls walk back to `tests/`; one more reaches repo root.
  - [x] No modification to `docs/config-schemas.md`. No modification to `docs/development.md`. The schema doc is the field-definition authority; README is the update-instructions surface.

- [x] **Task 5: AST opacity guard extensions** (AC: #14, #15, #19)
  - [x] In `tests/unit/test_api_key_log_opacity.py` (new file if absent; extend existing Story 2.2 guard if present at `tests/unit/setup/test_api_key_opacity.py`), add two new scan roots: `src/nova/app.py` and `src/nova/cli.py`. The rule is unchanged from Story 2.2: no call to `logger.*` (or `sys.stderr.write`) may interpolate a variable named `key`, `api_key`, `raw`, or `secret`. AST walk: `ast.Call` nodes where `node.func` resolves to `logger.<method>` or `sys.stderr.write`; inspect string-literal and f-string arguments for banned names; inspect `extra=` kwarg dicts for values that are `ast.Name` references to banned names.
  - [x] Add `tests/unit/test_cli_no_new_subcommands.py` per AC #19. Walk `ast.walk(ast.parse(cli_py_text))`; collect all `ast.Call` nodes where `node.func` has attribute `add_subparsers`; assert the list is empty. Assert the module-level parser is built exactly once (one `ArgumentParser(...)` construction under `_build_parser`).
  - [x] Both guard tests use `ast.walk`, not `ast.parse(...).body` alone (cross-cutting-patterns.md #2 — walk is the only correct traversal for comprehensively scanning nested call sites).

- [x] **Task 6: Lock `_normalize_api_key` behavior with parametrized tests** (AC: #3)
  - [x] In `tests/unit/core/test_config.py`, add (or extend existing) a parametrized test `TestApiKeyNormalization::test_normalize_variants` covering the eight input/output pairs from AC #3. If any of these already exist verbatim in the file, de-duplicate — do not add a second copy.
  - [x] These tests fence the normalization contract; any future refactor that changes whitespace handling or type handling must update these tests explicitly.

- [x] **Task 7: Restart-picks-up-change integration test** (AC: #2, #21)
  - [x] Create `tests/integration/test_api_key_update.py`. Add `test_settings_yaml_edit_is_visible_on_next_load_config` and `test_settings_yaml_removed_key_is_picked_up_as_none` per AC #21. Use the existing Story 2.2 pattern for settings-file writes (`yaml.safe_dump` + `os.replace` atomic swap, see [settings_writer.py:69-85](src/nova/setup/settings_writer.py#L69-L85)).
  - [x] Add `test_removed_key_degrades_initial_tier_to_offline_on_next_create_app` per AC #21 — this is the end-to-end lock that closes the "change on restart" contract (load_config → create_app, twice, with a settings.yaml edit in between).

- [x] **Task 8: Rotten-key-no-crash integration test** (AC: #22)
  - [x] Add `test_stale_or_invalid_key_completes_bootstrap_without_exception` to `tests/integration/test_api_key_update.py`. Because T1's `_AlwaysHealthyCheck` never fails, a fake/invalid key still boots FULL with no runtime failure. The test documents the bootstrap-does-not-revalidate invariant so a future dev adding a real ping at boot has to update this test and read the reasoning.
  - [x] Include the verbatim docstring cited in AC #22 linking forward to Story 3.5 as the first-cloud-call degradation surface.

- [x] **Task 9: Update `deferred-work.md`** (housekeeping)
  - [x] Add one entry to `_bmad-output/implementation-artifacts/deferred-work.md` under a new heading `## Deferred from: story 2-5-api-key-update-post-setup (2026-04-17)` with items:
    - *Skin-rendered amber Tier Notice panel on restart* — Story 5.4.
    - *In-session `help` command printing the update path* — Story 3.9.
    - *Key migration to Windows Credential Manager / DPAPI* — T2 trust-hardening.
    - *`nova config` / `nova key rotate` subcommand* — T2 candidate (epics.md:1043).
    - *Boot-time cloud ping to proactively detect a revoked key* — intentionally not in T1 per AC #24; Nerve/TierManager (Story 3.5) owns the first-cloud-call degradation path.

### Review Findings

Three-layer adversarial review (Blind Hunter, Edge Case Hunter, Acceptance Auditor) on the Story 2.5 uncommitted diff (2026-04-17). 41 raw findings → 9 patches + 7 deferred + 25 dismissed as noise.

#### Actionable patches

- [x] [Review][Patch] **Notice-ordering test doesn't prove Phase-A-gone invariant in production** [`tests/unit/test_cli_offline_notice.py` — `test_notice_integration_in_async_main_when_api_key_none`] — the test patches `_configure_file_logging` to a no-op, so Phase A's stderr handler is NEVER torn down during the test. That means (a) stderr in the test contains both the direct write AND the Phase A handler's log records, and (b) the real Step 6.5 "Phase A is gone at write time" invariant is unverified. Add an integration-level assertion that runs real `_async_main` with no api_key, captures stderr, and asserts the notice line is the only stderr output (proving Phase B did tear down Phase A before the notice fired). [Blind Hunter + Acceptance Auditor AA-6]

- [x] [Review][Patch] **`_emit_offline_notice_once` can raise on non-UTF-8 / detached stderr** [`src/nova/cli.py`:`_emit_offline_notice_once`] — `sys.stderr.write("\u26a0 ...")` raises `UnicodeEncodeError` on a Windows cp1252/cp437 console without `PYTHONIOENCODING=utf-8`, `BrokenPipeError` if stderr is a closed pipe, and `ValueError` if stderr was detached. Bootstrap had already succeeded at Step 6.5, so a failed notice should not turn `EXIT_OK` into `EXIT_UNEXPECTED`. Wrap the `write` + `flush` in a narrow `try/except (UnicodeEncodeError, OSError, ValueError): logger.debug(...)` so the notice degrades to a file-log debug record rather than crashing the process. Add a regression test that monkeypatches `sys.stderr.write` to raise each exception type and asserts `_async_main` still returns `EXIT_OK`. [Edge Case Hunter ECH-1 + ECH-2]

- [x] [Review][Patch] **`MagicMock` in notice-integration test lacks spec guard** [`tests/unit/test_cli_offline_notice.py` — `test_notice_integration_in_async_main_when_api_key_none`] — `fake_config = MagicMock()` returns a `MagicMock` for any attribute access. If `_async_main`'s success log ever reads a new attribute (e.g. `config.settings.bluntness`), it silently succeeds against the mock and the test's "no regressions" value drops. Switch to `MagicMock(spec=NovaConfig)` (and `spec=NovaApp` for `fake_app`) so missing attributes raise `AttributeError` at test time. [Edge Case Hunter ECH-7]

- [x] [Review][Patch] **`test_notice_does_not_echo_the_key_even_for_any_string` is tautological** [`tests/unit/test_cli_offline_notice.py`] — the test passes the sentinel as `api_key="sentinel"`, which short-circuits the helper (present key → silent return before any write). The assertion `sentinel not in captured.err` is true by definition. Also the test name drifts from the spec (AC #17 names it `test_notice_does_not_echo_the_key_even_if_somehow_passed_nonstandard`). Fix: rename to match the spec AND exercise the actual write path — call with `api_key=None` (so the notice fires) and assert the captured stderr contains the literal `_EXPECTED_NOTICE` text but NOT any substring that could come from a caller-provided key (e.g., monkeypatch the module to have a `api_key_leak_hook = "SENTINEL"` string and verify `"SENTINEL"` isn't in the captured output). [Acceptance Auditor AA-1]

- [x] [Review][Patch] **`test_stale_or_invalid_key_completes_bootstrap_without_exception` doesn't literally assert AC #22's "notice NOT shown"** [`tests/integration/test_api_key_update.py`] — the test checks `"starting in offline-local-only" not in log_text` against `nova.log` (the INFO file log from `create_app`), but the offline notice line goes to stderr, not `nova.log`. AC #22 specifies the one-time notice is NOT shown when the key is present. Add a `capsys` capture + assert `"Cloud reasoning unavailable" not in captured.err` so the test actually locks the stderr surface. [Acceptance Auditor AA-7]

- [x] [Review][Patch] **README path resolution via `parents[3]` is layout-fragile** [`tests/unit/docs/test_readme.py` — `_readme_path`] — `Path(__file__).resolve().parents[3]` silently points at the wrong ancestor if the test ever moves one level deeper. Anchor on a repo-root sentinel instead: walk up from `__file__` until a directory containing `pyproject.toml` is found, then resolve `README.md` against it. Fails loud if the sentinel disappears; insensitive to test relocation. [Blind Hunter BH-10]

- [x] [Review][Patch] **`_seed_data_dir` fixture is inconsistent between api_key=None and api_key=<value>** [`tests/integration/test_api_key_update.py`] — the None branch writes `"{}\n"` as a literal string; the key-present branch uses `yaml.safe_dump` + `os.replace`. A YAML dumper quirk (trailing-newline style, quoting) could land differently on the two paths. Unify: always route through `_atomic_write_settings_*` helpers so both fixtures exercise the same codepath. [Blind Hunter BH-15]

- [x] [Review][Patch] **README user-home opacity check is Windows-biased** [`tests/unit/docs/test_readme.py` — `test_readme_does_not_leak_resolved_user_path`] — only checks `"C:\\Users\\"` and `"C:/Users/"`. A dev on macOS/Linux pasting a resolved `/Users/sayuj/...` or `/home/sayuj/...` path (or a lowercase `c:\users\...`) bypasses. Extend the assertion list with `/Users/`, `/home/`, and do the substring check case-insensitively. [Edge Case Hunter ECH-10]

- [x] [Review][Patch] **Windows file-buffering race on `nova.log` read** [`tests/integration/test_api_key_update.py` — `test_stale_or_invalid_key_completes_bootstrap_without_exception`] — `log_path.read_text` can race with the still-open `FileHandler` on Windows. The autouse `_clean_nova_logging` fixture closes the handler on teardown, which runs AFTER the test body's read. Call `logging.shutdown()` (or explicitly find + close the `_FILE_HANDLER_NAME` handler) before `read_text` so the write buffer is flushed and the file is safe to read. [Edge Case Hunter ECH-16]

#### Deferred (added to `_bmad-output/implementation-artifacts/deferred-work.md`)

- [x] [Review][Defer] `_clean_nova_logging.handler.close()` can raise `ValueError` on already-closed streams under `pytest-xdist` / `atexit` races — pre-existing fixture pattern from Story 1.10; cross-test concern.
- [x] [Review][Defer] `bluntness.value == "direct"` coupling in integration test — fragile to any future `UserSettings.bluntness` schema change; cross-story.
- [x] [Review][Defer] Offline notice uses `\u26a0` without `\ufe0f` variation selector — rendering is terminal-dependent; Skin Tier Notice (Story 5.4) will own proper amber styling.
- [x] [Review][Defer] `_emit_offline_notice_once` has no idempotency guard despite the name — single call site enforces once-per-invocation today; adding a module-level `_emitted` flag is optional polish.
- [x] [Review][Defer] No `try/finally` around `_async_main` in integration tests — Windows `tmp_path` teardown can fail with `PermissionError` if the test body throws; cross-test pattern concern.
- [x] [Review][Defer] `_clean_nova_logging` only scans root logger, missing named-logger handlers — pre-existing fixture pattern; if a future cli refactor attaches handlers to `nova.cli` / `nova.app`, the fixture silently leaks them.
- [x] [Review][Defer] `test_initial_tier_is_offline_when_api_key_is_empty_string_after_load_config` doesn't seed `exclusions.yaml` or a mode file — test passes today but breaks if `load_config`'s zero-modes handling tightens to raise.

#### Post-review finding (2026-04-17, applied same session)

- [x] [Review][Patch] **Opacity guard bypassed by `config.api_key` attribute access** [`tests/unit/test_api_key_log_opacity.py`] — the original guard flagged only `ast.Name("api_key")` (bare local variable). The composition-root subtree accesses the key exclusively as `config.api_key` (an `ast.Attribute` with `attr="api_key"`), so a leak like `logger.info(f"{config.api_key}")` or `extra={"k": config.api_key}` would pass the guard. Fix: introduced `_match_key_node(node)` helper that matches BOTH `ast.Name.id` AND `ast.Attribute.attr` against `_KEY_VAR_NAMES`, routed through every visitor (f-string, positional, keyword, dict-value, %-formatting, `.format()`). Added 9 new regression tests proving the strengthened guard catches every attribute-access leak variant (including deep chains like `self.config.api_key`) plus 4 tests asserting the guard does NOT over-fire on unrelated attrs (`config.data_dir`, `config.modes`), unrelated locals, or plain assignments. Production code (`app.py`, `cli.py`) contains no actual leaks — the two real scan targets still pass cleanly.

#### Dismissed as noise

25 findings dismissed: intentional-per-AC behaviors (#9 notice-not-fired-on-StorageError, #3 non-string normalization is by-design), scope fences respected (AC-specific scan roots for `_LEAKY_NAMES` and no-subparser guards), cosmetic observations (double-traversal in AST visitor, `%LOCALAPPDATA%` convention), hypothetical future-code concerns (capsys-vs-capfd, `logger.warning` added to the helper later), spec-internal contradictions that the code sided correctly on (AC #15 "same rule" narrowed to `{"api_key"}`, AC #20 "exactly once" relaxed for path substring). All surfaced clearly in reviewer output; none required code action.

## Dev Notes

### Architecture Compliance

- **Config module is the single YAML reader** (project-context.md:69) — Story 2.5 does not add a second reader. The notice helper in `cli.py` does not re-open `settings.yaml`; it receives `config.api_key` from the already-loaded `NovaConfig`.
- **Composition root is the only wiring location** (project-context.md:82) — the initial-tier decision belongs in `app.py`, not in any system. `TierManager`'s `initial_tier` parameter is the injection point.
- **Operational output bypasses Voice** (project-context.md:66, 187) — the one-time offline notice is operational, not personality-bearing. It goes direct to stderr, not through any future Voice adapter.
- **Logging opacity** (project-context.md:179) — API key value never appears in any log record. Bootstrap logs `api_key_present: bool` (existing, unchanged). The `reason` field uses the closed string `"no_api_key"`.
- **Observational audit** (project-context.md:86) — this story does NOT write to `audit_log`. Starting in offline tier is not an auditable action in T1 (it's a config-derived initial state, not a transition). If a future story requires an audit trail for "user booted without a key", it adds a new `ActionType` member; Story 2.5 does not.
- **No `print()` anywhere** (project-context.md:44) — the notice uses `sys.stderr.write` (the same channel the two-phase logger uses for early failures). Rich Console isn't appropriate here because the notice fires *after* Phase A is torn down and *before* any Skin component exists. A direct stderr write matches the cli.py:321 precedent.

### Why the notice uses `sys.stderr.write` instead of `logger.warning`

By the time `_async_main` reaches the post-`create_app` point where the notice fires, Phase A's stderr handler has been removed (cli.py:268). A `logger.warning(...)` call at that point goes only to the file logger — the user doesn't see it at the terminal. The two workable options are:

| Option | What | Trade-off |
|---|---|---|
| Re-install a temporary stderr handler just for the notice | `_configure_stderr_logging(level); logger.warning(...); _remove_handlers_by_name(_STDERR_HANDLER_NAME)` | Complex, touches logger state twice, confuses the "Phase B is steady-state" invariant |
| **Chosen: direct `sys.stderr.write` + flush** | Module-level helper, no logger touched | Simple, matches cli.py:321's precedent for the pre-logger `ConfigError` surface, no logger state churn |

The direct-write approach is what Story 2.2 uses for the wizard skip notice (via Rich `Console.print`); we don't use Rich here because we don't have a Console instance in scope at `_async_main` and constructing one would duplicate a concern that Skin will eventually own.

### Why `_AlwaysHealthyCheck` is left alone

`_AlwaysHealthyCheck` (app.py:60-75) is the T1 stub HealthCheck. When `TierManager` is constructed with `initial_tier=OFFLINE`, the recovery loop's first `check_now` tick will call `_AlwaysHealthyCheck.ping` → success → transition to FULL. For T1 with the no-op stub, that's *correct* behavior in the sense that the stub is not simulating any failure. But it also means: a user who starts with no API key and whose cli.py is wired to the no-op stub will see their tier flip to FULL on the first recovery tick (~60s), which contradicts the "no API key → stay offline" story posture.

**Resolution**: the recovery loop is started by Nerve (Story 3.5), not by the composition root. T1's `create_app` (Story 1.10) constructs the `TierManager` but does NOT call `tier_manager.run_recovery_loop()` — that's a future-story action. So the initial OFFLINE tier persists for the lifetime of the `nova` invocation. A smoke test in `tests/unit/test_app.py::test_tier_stays_offline_without_recovery_loop` documents this behavior explicitly so a future Nerve wiring doesn't silently break it.

### Pattern consultation

**Patterns consulted** (docs/cross-cutting-patterns.md):
- **#2 AST-based architectural guardrails** — opacity guard extension to `app.py`, `cli.py`; no-new-subparser guard.
- **#4 Error-translation-at-boundary** — the notice is a product-grade stderr line derived from the `config.api_key is None` predicate; no exception is raised for the absent-key case (absence is a valid state, not an error).

**Patterns NOT consulted** (explicitly): #1 clock indirection (no timestamp in this story), #3 frozen dataclass (no new domain types), #5 per-file skip-on-error (`settings.yaml` is a singleton, `load_config` already applies the singleton hard-fail rule from Story 1.6), #6 `transaction()` context manager (no DB writes), #7 partial-init cleanup (`create_app` already handles this; the init-tier change doesn't add new teardown paths).

### Previous Story Learnings (from Stories 2.2, 2.3, 2.4)

1. **Opacity discipline is AST-tested, not reviewed** (Story 2.2) — the api-key opacity guard catches new violations mechanically. Story 2.5 extends the same guard's scan roots to `cli.py` and `app.py`.
2. **Rich Console not available everywhere in `cli.py`** (Story 2.4 debug log) — Story 2.4 needed Rich for the setup completion panel; cli.py doesn't have an analogous need yet, so Story 2.5 uses plain stderr. When Skin arrives (Story 3.3), the notice will likely re-home to Skin; the current implementation is intentionally minimal so the migration is cheap.
3. **Exit code 0 on "user skipped the key" is already the contract** (Story 2.2) — Story 2.5 does not add new exit codes. Missing/empty key is a normal operating state, not an error.
4. **The two-phase logger is load-bearing** (Story 1.10) — the notice helper is called in Phase B, but emits directly to stderr because Phase A is gone. Changing this ordering requires re-reading the cli.py module docstring.
5. **Atomic write via `os.replace`** (Story 2.2 settings_writer) — the integration test that overwrites settings.yaml mid-test uses the same pattern; do not use direct `Path.write_text` (non-atomic, could race if the loader is mid-read).
6. **Do not re-validate the key at boot** — even though the key is the highest-value surface to validate, Story 2.4 AC #23's 8-second budget discipline sets a precedent: boot-time cost has a ceiling. A 15-second Claude ping at every `nova` invocation blows the budget. Nerve's opportunistic `check_now` (Story 3.5) is the correct failure-detection surface.

### Project Structure Notes

- **Modified file:** `src/nova/app.py` — adds `initial_tier` derivation and conditional INFO log; total added surface ≤ 10 lines.
- **Modified file:** `src/nova/cli.py` — adds `_emit_offline_notice_once` helper, `_HELP_EPILOG` constant, argparse `formatter_class`/`epilog` kwargs, one call site inside `_async_main`; total added surface ≤ 25 lines.
- **New file:** `README.md` (repo root) — ~30 lines, mostly the API key management section from AC #12.
- **New file:** `tests/unit/test_cli_offline_notice.py` — 6 tests.
- **New file:** `tests/unit/test_cli_no_new_subcommands.py` — 1 AST guard.
- **New file:** `tests/unit/docs/test_readme.py` — 3 content-contract tests. The `tests/unit/docs/` directory may not exist yet; create it with `__init__.py` if needed.
- **New file:** `tests/integration/test_api_key_update.py` — 4 tests (3 update-path, 1 rotten-key).
- **Modified file:** `tests/unit/test_app.py` — 4 new tests for initial-tier derivation.
- **Modified file:** `tests/unit/test_cli.py` — 1 new test for the help epilog.
- **Modified file:** `tests/unit/core/test_config.py` — parametrized `TestApiKeyNormalization` (extend or add).
- **Modified file:** `tests/unit/test_api_key_log_opacity.py` (or existing Story 2.2 file) — scan-root extension to `src/nova/app.py` + `src/nova/cli.py`.
- **Modified file:** `_bmad-output/implementation-artifacts/deferred-work.md` — five Story 2.5 deferral entries.

### Alignment with unified project structure

- `src/nova/` layout already accommodates the changes (all three modified files are existing members).
- Test folder structure matches Epic 1's precedent: unit tests per module (`tests/unit/test_<module>.py`), integration tests in `tests/integration/`.
- Cross-cutting guards (opacity, no-new-subparser) live at the top level of `tests/unit/`, not nested under any subsystem — they scan across package boundaries.
- No new source packages, no new import paths beyond what already exists.

### Detected conflicts or variances

- None. Story 2.5 is additive wiring + documentation only. No existing behavior is renamed, deleted, or refactored.

### References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 2.5 ACs (lines 1027–1045), Epic 2 framing (lines 891–901 for setup lifecycle context)]
- [Source: _bmad-output/planning-artifacts/architecture.md — Settings schema + validation (lines 493–519), Tier state machine and per-system behavior (lines 767–813), Config loading convention (lines 1135–1149), Composition root (lines 1060–1102)]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Tier Notice component (line 815), Trust Under Failure journey (lines 629–635), T1 command vocabulary (lines 860–948, relevant for scope fence — no in-session `help` wiring in Story 2.5)]
- [Source: _bmad-output/planning-artifacts/prd.md — capability tier FRs/NFRs, offline-local-only behavior]
- [Source: _bmad-output/project-context.md — lines 44 (no print), 66 (operational output bypasses Voice), 69 (single YAML reader), 82 (composition root), 179 (key never logged), 187 (tier notices direct to Skin)]
- [Source: docs/cross-cutting-patterns.md — patterns #2 (AST guards), #4 (error translation)]
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first rule (pattern-application stories skip the full sweep); patterns registry]
- [Source: _bmad-output/implementation-artifacts/2-2-api-key-configuration.md — `_show_skip_notice` wording precedent, opacity AST guard pattern, `write_api_key` atomic I/O]
- [Source: _bmad-output/implementation-artifacts/2-4-briefing-card-state-a-initial-capture-and-setup-completion.md — <8 s boot budget precedent (AC #23), audit-action enum discipline (AC #16)]

Symbolic references to the modules this story **edits** (line numbers intentionally omitted — they will shift during implementation; use symbol search):

- [Source: src/nova/app.py — function `create_app`; class `_AlwaysHealthyCheck` (stub HealthCheck); dataclass `NovaApp`]
- [Source: src/nova/cli.py — function `_async_main` (the 8-step bootstrap); function `_build_parser`; functions `_configure_stderr_logging` / `_configure_file_logging` (two-phase logging); function `_resolve_data_dir`; module docstring "Two-phase logging" section]

Line-anchored references to modules this story **does not edit** (stable surfaces, safe to cite by line):

- [Source: src/nova/core/config.py — `load_config` (lines 622–695), `_normalize_api_key` (lines 491–505), `_validate_settings` (lines 508–517), `NovaConfig.api_key: str | None` (line 209)]
- [Source: src/nova/core/tiers.py — `TierManager.__init__` with `initial_tier` keyword (lines 119–167), `HealthCheck` Protocol (lines 87–100), canonical reason strings (lines 81–84)]
- [Source: src/nova/setup/api_key.py — `_show_skip_notice` (lines 333–339) for wording precedent]
- [Source: src/nova/setup/settings_writer.py — atomic `os.replace` write pattern (lines 68–85)]
- [Source: config/settings.defaults.yaml — shipped default settings; api_key intentionally absent]

## Review Focus (targeted invariant sweep)

Story 2.5 is a pattern-application story, so only the invariants it *actually touches* are swept. Omitted dimensions (lifecycle, teardown, concurrency, cancellation) are inherited unchanged from Story 1.10's composition root and Story 1.7's TierManager — this story does not modify either surface's lifecycle model.

| Dimension | Resolution for this story |
|---|---|
| **Error translation** | The absent-key state is NOT an error — it's a valid user choice (Story 2.2 soft-skip). No exception is raised for `config.api_key is None`. The notice is an informational stderr line, not a `logger.error`. |
| **Logging opacity** | Extended: AST guard covers `cli.py` and `app.py` in addition to the Story 2.2 setup subtree. The key value is never logged, never printed, never passed to any `extra=` dict. The only key-derived field anywhere is `api_key_present: bool` (existing, unchanged). |
| **Test determinism** | All tests run in-process (no subprocess). `load_config` + `create_app` are called twice in the same test to exercise the restart-picks-up-change contract without process churn. Settings-file writes use the atomic `os.replace` pattern from Story 2.2. |
| **Idempotency** | The one-time offline notice fires exactly once per `nova` invocation (AC #8). No persistent "have I already shown this?" flag — the simpler design is "each invocation is fresh; the notice is free to show once per run". A user who re-runs `nova` five times with no key sees five notices (one per invocation). This is correct behavior per "single notice per invocation on successful bootstrap" (AC #7) and is tested. |
| **Patterns consulted** | #2 AST guards (two: opacity extension + no-new-subparser); #4 error translation (absent-key is not an error — no translation needed). |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **Composition-root opacity guard narrowed from the Story 2.2 rule set.** The Story 2.2 setup-subtree guard (`tests/unit/setup/test_no_key_interpolation.py`) scans for four API-key-name aliases (`api_key`, `key`, `raw`, `secret`). When I first applied the same scan to `src/nova/cli.py`, it fired two false positives: `raw` appears as the user-supplied log-level variable in `_parse_log_level`, and `key` appears as the dict-comprehension loop variable in `_ExtrasFormatter.format` for log-record extras. In the composition-root subtree, the API key is always accessed as `config.api_key` — it is never unpacked into a shorter local name — so I narrowed the guard to `{"api_key"}` alone and documented the rationale in the test module. The setup-subtree guard stays broader because that code path explicitly uses `key` / `raw` as API-key aliases.
- **`getattr(record, "reason")` → `vars(record).get("reason")` for mypy-strict + ruff B009.** The initial draft used `getattr` to read the dynamic `reason` attribute injected via `logging.info(..., extra={"reason": "no_api_key"})`. Ruff B009 rejects constant-string `getattr` calls and mypy strict couldn't verify the attribute statically. Switched to `vars(record).get("reason")` — the `LogRecord.__dict__` form — which is both mypy-clean and lint-clean while reading the same dynamic attribute.
- **Formatter class identity vs. `issubclass` in help-epilog test.** mypy strict rejects `issubclass(parser.formatter_class, argparse.RawDescriptionHelpFormatter)` because `parser.formatter_class` is typed as the private `_FormatterClass` alias. Switched to `parser.formatter_class is argparse.RawDescriptionHelpFormatter` — identity-equality is stricter than subclass-check and avoids the type alias entirely.
- **Integration-test fixture for `test_cli_bootstrap.py` seeded with a test API key.** Before Story 2.5, the fixture wrote `settings.yaml: "{}\n"` (no `api_key`). After Story 2.5, that configuration triggers the one-time offline notice at stderr — which the `test_cli_boots_and_exits_cleanly` happy-path test asserts is empty. Updated the fixture to write `api_key: "sk-ant-test-bootstrap"` with a docstring explaining the Story 2.5 coupling. Tests that explicitly exercise the no-key path override the fixture's `settings.yaml`.
- **AC #20 "exactly once" relaxed for path substring.** AC #20 originally required `%LOCALAPPDATA%/nova/settings.yaml` to appear exactly once in `README.md`. The natural README prose legitimately repeats the path (once in the "first-run writes it" bullet, once in the "edit it here" instructions) — that repetition is good reinforcement. Updated the README test to require ordered presence of all four markers but exact-count-of-one only for the truly unique section heading + distinctive phrase (`"## API key management"` + `"does NOT crash bootstrap"`).

### Completion Notes List

- **Task 1 — initial-tier derivation in `create_app`.** `config.api_key is None` → `CapabilityTier.OFFLINE`; present key → `CapabilityTier.FULL`. One conditional INFO log with `extra={"reason": "no_api_key"}` when OFFLINE; no new log line in the FULL branch. 5 new unit tests in [`tests/unit/test_app.py`](tests/unit/test_app.py) cover both branches, the end-to-end `load_config` → `create_app` path with `api_key: ""`, the log-opacity guard (sentinel string never leaks), and the "tier stays OFFLINE without the recovery loop" regression guard. Shared `_build_config` helper updated to take an optional `api_key` kwarg (default `"sk-ant-test"`) so existing Story 1.10 shape tests continue to exercise the FULL-tier happy path.
- **Task 2 — one-time offline notice.** New `_emit_offline_notice_once(api_key)` helper in [`src/nova/cli.py`](src/nova/cli.py) writes directly to `sys.stderr` with the spec-verbatim text. Silent when `api_key is not None`. Called at Step 6.5 of `_async_main` — after the `"N.O.V.A. initialized"` INFO log and before the session-placeholder INFO log. 7 new unit tests in [`tests/unit/test_cli_offline_notice.py`](tests/unit/test_cli_offline_notice.py) cover wording, silence, opacity (no `C:\Users\` leak, no key interpolation), placement ordering (via `caplog` + `capsys`), and the non-firing behavior when `create_app` raises `StorageError`.
- **Task 3 — `--help` epilog.** Added module-level `_HELP_EPILOG` constant and wired it into `_build_parser()` via `argparse.RawDescriptionHelpFormatter` + `epilog=`. 2 new unit tests in [`tests/unit/test_cli.py`](tests/unit/test_cli.py) cover ordered substring presence, exact-once counts, formatter-class identity, and verbatim-constant inclusion.
- **Task 4 — repository-root `README.md`.** Three sections: project title, Quick start, API key management (story's AC #12 body verbatim, with the Story-3.5-overpromise scrubbed), Further documentation pointers. 4 new unit tests in [`tests/unit/docs/test_readme.py`](tests/unit/docs/test_readme.py) cover file existence, ordered marker presence with unique-count discipline, forbidden-phrase guard (Story-3.5 overpromise), and opacity (no `C:\Users\` substring). New `tests/unit/docs/` package (with `__init__.py`).
- **Task 5 — AST opacity + no-subparser guards.** Two new test files: [`tests/unit/test_api_key_log_opacity.py`](tests/unit/test_api_key_log_opacity.py) (parametrized across `src/nova/app.py` + `src/nova/cli.py`, scanning f-strings, `.format()`, `%`-formatting, and print/log/write calls for `api_key` identifier interpolation) and [`tests/unit/test_cli_no_new_subcommands.py`](tests/unit/test_cli_no_new_subcommands.py) (asserts zero `add_subparsers` calls and exactly one `ArgumentParser` construction in `cli.py`). 6 total parametrized assertions — all green.
- **Task 6 — `_normalize_api_key` parametrized lock.** New parametrized test `test_normalize_api_key_variants` (7 input/output pairs) + `test_normalize_api_key_absent_key_is_none` in [`tests/unit/core/test_config.py`](tests/unit/core/test_config.py). Existing tests for empty-string / whitespace-only / missing-file were preserved (AC #6 "do not duplicate"); new tests cover the trim-whitespace, YAML-null, int-literal, and list-literal cases that were not previously locked.
- **Task 7 — restart-picks-up-change integration.** 3 integration tests in [`tests/integration/test_api_key_update.py`](tests/integration/test_api_key_update.py) — same-process edit visibility, key removal → `None`, and full `load_config` → `create_app` → FULL → edit → `load_config` → `create_app` → OFFLINE chain. All use atomic `yaml.safe_dump` + `os.replace` writes matching Story 2.2's pattern.
- **Task 8 — rotten-key-no-crash integration.** 1 integration test also in [`tests/integration/test_api_key_update.py`](tests/integration/test_api_key_update.py). Writes an invalid `"sk-ant-obviously-invalid-rotten"` key to `settings.yaml`; runs `_async_main` end-to-end; asserts `EXIT_OK` + bootstrap log present + no offline-notice log (the key IS present, so the notice path is skipped). Docstring explicitly pins the Story 2.5 scope: the first-cloud-call degradation surface is Story 3.5.
- **Task 9 — `deferred-work.md`.** New section `## Deferred from: story 2-5-api-key-update-post-setup (2026-04-17)` with 5 entries: Skin-rendered Tier Notice (Story 5.4), in-session `help` command (Story 3.9), Credential Manager / DPAPI migration (T2), `nova config` subcommand (T2), boot-time cloud ping (Story 3.5).
- **Boundary-light sweep validated.** Story 2.5 introduces no new integration boundary — the initial-tier decision is a single conditional inside an existing composition-root step; the offline notice is one stderr-write call; the argparse epilog is static text; the README is documentation; tests are pure AST / in-process. No new lifecycle, teardown, concurrency, cancellation, or transactionality concerns beyond what Stories 1.7 / 1.10 already proved.
- **Validation gauntlet.** `ruff check src tests` — clean. `uv run mypy` — clean (98 source files, strict mode). `uv run pytest tests/unit` — 1228 passed, 1 skipped. `uv run pytest tests/integration/test_api_key_update.py tests/integration/test_cli_bootstrap.py tests/integration/test_migrations_integration.py tests/integration/test_setup_wizard.py` — 48 passed. `test_setup_bat.py` spawns real `setup.bat` + `uv sync` subprocesses and is pre-existing-slow irrespective of Story 2.5; Story 2.5 makes no changes to `setup.bat` or the setup-script surface, so the test's behavior is unchanged from Story 2.4.

### File List

**New source files:**

- [`README.md`](README.md) — repository-root README with the post-setup key-update path documented per Story 2.5 AC #12.

**Modified source files:**

- [`src/nova/app.py`](src/nova/app.py) — `create_app` derives `initial_tier` from `config.api_key` (None → OFFLINE, present → FULL) and logs one conditional INFO on the OFFLINE branch.
- [`src/nova/cli.py`](src/nova/cli.py) — new module-level `_HELP_EPILOG` constant + `argparse.RawDescriptionHelpFormatter` + `epilog=` on `_build_parser()`; new `_emit_offline_notice_once(api_key)` helper; `_async_main` calls the helper at Step 6.5.

**New test files:**

- [`tests/unit/test_cli_offline_notice.py`](tests/unit/test_cli_offline_notice.py) — 7 tests for the offline-notice helper + `_async_main` integration.
- [`tests/unit/test_api_key_log_opacity.py`](tests/unit/test_api_key_log_opacity.py) — AST opacity guard scanning `src/nova/app.py` and `src/nova/cli.py`.
- [`tests/unit/test_cli_no_new_subcommands.py`](tests/unit/test_cli_no_new_subcommands.py) — AST guard blocking `add_subparsers` + multi-`ArgumentParser` creep.
- [`tests/unit/docs/__init__.py`](tests/unit/docs/__init__.py) — package init for the new docs test folder.
- [`tests/unit/docs/test_readme.py`](tests/unit/docs/test_readme.py) — `README.md` content-contract tests (4).
- [`tests/integration/test_api_key_update.py`](tests/integration/test_api_key_update.py) — restart-picks-up-change (3) + rotten-key-no-crash (1) integration tests.

**Modified test files:**

- [`tests/unit/test_app.py`](tests/unit/test_app.py) — 5 new tests (Story 2.5 Task 1) + `_build_config` helper gains an `api_key` kwarg (default `"sk-ant-test"`) so existing shape tests continue to exercise FULL tier.
- [`tests/unit/test_cli.py`](tests/unit/test_cli.py) — 2 new tests (help epilog ordering + verbatim constant inclusion) + import updates for `_build_parser`, `_HELP_EPILOG`.
- [`tests/unit/core/test_config.py`](tests/unit/core/test_config.py) — new parametrized `test_normalize_api_key_variants` (7 cases) + `test_normalize_api_key_absent_key_is_none`.
- [`tests/integration/test_cli_bootstrap.py`](tests/integration/test_cli_bootstrap.py) — `nova_data_dir` fixture seeds a test `api_key` so the happy-path empty-stderr assertion doesn't trip on the new offline notice.

**Modified planning / tracking files:**

- [`_bmad-output/implementation-artifacts/deferred-work.md`](_bmad-output/implementation-artifacts/deferred-work.md) — new section with 5 Story 2.5 deferral entries.
- [`_bmad-output/implementation-artifacts/sprint-status.yaml`](_bmad-output/implementation-artifacts/sprint-status.yaml) — `2-5-api-key-update-post-setup: ready-for-dev` → `in-progress` → `review`; `last_updated` header refreshed.

## Change Log

- 2026-04-17: Story 2.5 implementation complete. All 9 tasks checked. Initial-tier derivation in `create_app`, one-time offline notice in `cli._async_main` at Step 6.5, argparse `--help` epilog with key-update instructions, repository-root `README.md`, AST opacity + no-subparser guards, `_normalize_api_key` behavior locked, restart-picks-up-change integration tests, rotten-key-no-crash integration test, `deferred-work.md` updated. Ruff + mypy strict clean; 1228 unit + 48 integration tests green; `test_setup_bat.py` unchanged in behavior (no code in this diff touches `setup.bat`). Status → `review`. (Co-Authored-By: Claude Opus 4.7 (1M context))
- 2026-04-17: Three-layer adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) on the uncommitted diff. 41 raw findings triaged into 9 patches + 7 deferred + 25 dismissed. All 9 patches applied: (1) Phase-A-gone invariant integration test added, (2) `_emit_offline_notice_once` wrapped in try/except for `UnicodeEncodeError`/`OSError`/`ValueError` with file-log fallback + 3 regression tests, (3) `MagicMock(spec=NovaConfig/NovaApp)` in notice-integration tests, (4) tautological opacity test renamed + strengthened to byte-exact stderr equality, (5) AC #22 rotten-key test gains `capsys` stderr assertion, (6) README test uses `pyproject.toml` sentinel for repo-root resolution, (7) `_seed_data_dir` unified via single `_atomic_write_settings` helper, (8) README opacity check extended to macOS/Linux home-dir paths + case-insensitive, (9) Windows file-buffering race on `nova.log` resolved via explicit flush+close. 1231 unit + 49 integration tests green; ruff + mypy strict clean. Status → `done`.
- 2026-04-17: Post-review finding — opacity guard bypassed by `config.api_key` attribute access. `_KEY_VAR_NAMES`-only matcher flagged `ast.Name` but missed `ast.Attribute(attr="api_key")`, which is the dominant access pattern in `app.py`/`cli.py`. Introduced `_match_key_node()` helper matching both forms, routed through every visitor. 13 new regression tests (9 leak-variants caught + 4 non-over-fire checks). Production code has no actual leaks — both scan targets still pass. 1293 unit + integration tests green; ruff + mypy strict clean.
