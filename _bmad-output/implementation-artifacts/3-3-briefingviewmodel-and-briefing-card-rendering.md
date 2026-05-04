# Story 3.3: BriefingViewModel & Briefing Card Rendering

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)
**Depends on:** Story 1.9 (`BriefingViewModel` placeholder shape, `RitualPort`, `SkinPort`), Story 2.4 (setup's `_render_state_a` — visual parity target), Story 3.1 (`SessionSummary.duration_seconds` semantics, `WorkspaceSnapshot` domain model), Story 3.2 (`BriefingAggregate`, `ModeInfo` reshape with `stem` / `display_name`, `determine_briefing_state`, the `BriefingState` import surface)
**Downstream consumers:** Story 3.5 (Nerve session lifecycle — first runtime caller of `RitualSystem.build_briefing` + `SkinPort.render_briefing_card` on bare-`nova` boot), Story 3.7 (shutdown flow — `RitualSystem.begin_shutdown` body lands here; Story 3.3 ships only the `NotImplementedError` placeholder), Story 3.8 (warm-resume hero moment — exercises State C end-to-end through the pipeline this story ships), Epic 7 (Voice + prose enrichment — populates `BriefingViewModel.prose_enrichment`)

## Story

As a returning user,
I want the Ritual → Skin pipeline to assemble and render a Briefing Card from a `BriefingAggregate` + state + tier — Rich Panel, progressive omission, design-system styling — without divergence from setup's existing State A render,
So that bare `nova` boot (Story 3.5) can show me a clean State A / B / C Briefing Card and Skin makes zero content decisions while doing it.

## Story-type classification

**Interaction-boundary story** (Epic 2 retro A6). Pre-flagged in [epic-3-story-preflags.md:24-33](epic-3-story-preflags.md#L24-L33). Three questions:

1. **New contract between existing pieces?** YES. This is the **first production use of the Ritual → Skin pipeline**. Every prior epic shipped Skin / Ritual as docstring-only placeholder packages — Story 1.9 declared the ports, Stories 2.4 / 3.1 / 3.2 produced and consumed the upstream domain models (`BriefingAggregate`, `BriefingState`, `ModeInfo`), but no code has populated `BriefingViewModel` or rendered a Briefing Card through `SkinPort`. Story 3.3 introduces the `RitualSystem` class (concrete `RitualPort` adapter), the `RichSkinAdapter` class (concrete `SkinPort` adapter), the **progressive omission** rendering invariant (fields with no data are omitted — no placeholders, no "N/A"), and the **content-at-Ritual / styling-at-Skin** boundary: every visible character originates in Ritual's pre-rendered label fields; Skin's only render-time work is style assignment + spacing + panel chrome.

2. **New invariants in degraded / partial-failure paths?** YES.
   - `prose_enrichment=None` MUST render cleanly (no blank line, no "enrichment unavailable" copy). Epic 7 will populate it; Epic 3 must not leak the absence visually.
   - **Pre-rendered labels are the boundary contract** — Ritual produces every visible string (`seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`, `intro_lines`, `prompt_text`); Skin maps each to a fixed Rich style. No raw integers (durations), enums (states), tuples (apps), or component values (mode names) cross into Skin where Skin would have to format. This is the practical reading of project-context.md:64 "Skin renders what it receives; never makes decisions."
   - **Progressive omission rules** locked by test (architecture.md:738-745, Story 3.3 reshape):
     - `intro_lines == ()` → omit the entire preface block (State C path).
     - `seed_quote is None` → omit the seed line (Ritual returns None for empty / null seed).
     - `last_session_label is None` → omit the "Last session" line (Ritual returns None for: no last_session, null mode_name, deleted mode).
     - `last_apps_label is None` → omit the "Apps:" line (Ritual returns None for: no snapshot, all-opaque windows).
     - `available_modes_label is None` → omit the available-modes line (Ritual returns None for: empty modes, State A / C path).
     - `prose_enrichment is None` → omit the prose paragraph (always None in Epic 3).
     - `prompt_text is None` → omit the prompt (State A path; State B / C always have a prompt).
   - **Duration-zero policy split**: `format_duration_seconds(0)` returns `"0s"` (a value-based formatter — zero seconds is a representable completed-session duration). Ritual's `_build_last_session_label` is the SINGLE policy site that omits the duration tail when `last_session.is_complete is False`. A short completed session renders accurately as `"Last session: Coding mode, 0s"`; an interrupted session renders as `"Last session: Coding mode"` (no tail). The two cases are NOT confused because the upstream signal is `is_complete`, not `duration_seconds == 0`.
   - **Tier-orthogonality** in T1: `tier` rides on the ViewModel for downstream tier-notice rendering (Story 5.4) but does not change the A/B/C panel chrome in this story. Verified by parametrizing render tests across `(state, tier)` cells.
   - **Suggested-mode tie-break determinism**: when multiple modes carry `is_default=True` (the warning logged by `nova.core.config._validate_default_mode_uniqueness` does not reject the config), Ritual MUST resolve to a single suggestion deterministically. The tie-break ladder is `(a) match last_session.mode_name if present and matching a configured stem → that ModeInfo; (b) most-recent last_used_at (lexicographic ISO sort, None is smaller than any string); (c) is_default=True mode with the alphabetically-first stem; (d) the alphabetically-first stem.` Locked by test.

3. **Depends on prior-story state?** YES. Two locked predecessors:
   - **Setup's `_render_state_a` (Story 2.4)** — direct Rich Panel render at [src/nova/setup/__main__.py:78-104](../../src/nova/setup/__main__.py#L78-L104). Setup keeps using its own renderer for the first-run pre-wizard path (no NovaApp exists at that point, no `BriefingAggregate` to assemble, no Brain to consult). Story 3.3 ships the **bare-`nova`-boot** render path — separate code site, identical visible output for State A. The pre-flag mandates "two renderers must coexist without divergence in visible output." Story 3.3 ships a parity test that locks byte-identical State A output between the two paths under `Console(record=True, width=80)` capture.
   - **Story 1.9's `BriefingViewModel` placeholder shape** — the type lives at [src/nova/systems/ritual/models.py:26-51](../../src/nova/systems/ritual/models.py#L26-L51) with raw-component fields (`seed_text`, `last_mode`, `last_duration_seconds`, `last_apps`). **Story 3.3 ships a more substantial reshape than originally pre-flagged**: rather than a single rename to `last_duration_display`, the four raw-component fields are removed and replaced by five pre-rendered label fields (`intro_lines`, `seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`). The reshape is safe today because no runtime code populates `BriefingViewModel` (the type is declared but unused — Story 3.2 did not touch it, see Story 3.2 § Open question 3). The shape change is locked by `test_briefing_view_model_field_set_matches_ac_3_3` (AC #3) and by the regression-guard assertion that the four removed names are NOT present.

**Classification result:** ✅ **Interaction-boundary story.** Apply full A1 invariant sweep (lifecycle, teardown, concurrency / cancellation, error translation, test determinism, Review Focus subsection). Apply A9 degraded-path proof (happy / degraded / rerun categories). Apply A10 prior-state reconciliation (this section + the visual-parity test).

## Depends on prior-story state (A10)

### Story 3.2 locked: `BriefingAggregate` + `BriefingState` + `ModeInfo`

- [`src/nova/systems/brain/models.py:206-228`](../../src/nova/systems/brain/models.py#L206-L228) ships `BriefingAggregate` with all five fields populated by [`nova.systems.nerve.briefing.load_briefing_aggregate`](../../src/nova/systems/nerve/briefing.py#L65-L113). `recent_memory` is **always** an empty tuple `()` in T1 (Story 3.2 § Explicit non-goals). Story 3.3's `RitualSystem.build_briefing` does not consult `recent_memory` — Epic 4 / Epic 7 will populate it and Epic 7 will route it into prose enrichment.
- [`src/nova/core/types.py:44-56`](../../src/nova/core/types.py#L44-L56) declares `BriefingState` as a `StrEnum` (`FIRST_RUN`, `POST_SETUP`, `WARM_RESUME`). Story 3.3 imports from `nova.core.types`, never re-declares.
- [`src/nova/systems/brain/models.py:118-162`](../../src/nova/systems/brain/models.py#L118-L162) ships the Story 3.2 reshape of `ModeInfo`: `stem` (canonical identifier — what `sessions.mode_name` stores), `display_name` (user-facing label from YAML `name:`), `app_count`, `is_default`, `last_used_at: str | None` (ISO-8601 UTC, lexicographically sortable). Story 3.3 reads `display_name` for `prompt_text` formatting and reads `stem` for the "match `last_session.mode_name`" tie-break. Renaming a mode YAML's `name:` after a session has been recorded leaves history queryable (the stem is stable) and only changes the rendered label — Story 3.3's tests force `stem != display_name` so a regression that collapses the two surfaces.

### Story 3.1 locked: `SessionSummary.is_complete` is the interrupted-session signal

[`src/nova/systems/brain/models.py:50-72`](../../src/nova/systems/brain/models.py#L50-L72) ships `SessionSummary` with two fields that Story 3.3 reads:
- `duration_seconds: int` — always populated; for interrupted sessions (`ended_at IS NULL`) Story 3.1 stores `0`.
- `is_complete: bool` — coerced from SQLite INTEGER 0/1; `False` means the session was interrupted before clean shutdown, `True` means clean shutdown.

**Story 3.3 keys the interrupted-vs-completed render policy on `is_complete`, NOT on `duration_seconds == 0`.** A short completed session can have `duration_seconds == 0` (e.g., user typed `shutdown` immediately after boot). Treating zero-duration as a synonym for "interrupted" would silently relabel a real completed session as interrupted — wrong UX signal. The policy split:
- `format_duration_seconds(seconds: int) -> str` is **value-based**: `0 → "0s"`, `45 → "45s"`, `6120 → "1h 42m"`. The formatter does NOT encode session-state policy.
- Ritual's `_build_last_session_label` is the **policy site**: when `last_session.is_complete is False`, omit the duration tail; otherwise call the formatter and append.

This split is documented in the formatter's module docstring (AC #5) and locked by AC #24's `test_state_c_with_completed_session_zero_duration_renders_zero_seconds` (asserts `"Last session: Coding mode, 0s"` for a 0-second completed session) plus `test_state_c_with_interrupted_session_omits_duration` (asserts duration tail omitted for `is_complete=False`).

### Story 1.9 locked: `BriefingViewModel`, `RitualPort`, `SkinPort` placeholder shapes

- [`src/nova/systems/ritual/models.py:26-51`](../../src/nova/systems/ritual/models.py#L26-L51) ships the placeholder `BriefingViewModel` with raw-component fields (`seed_text`, `last_mode`, `last_duration_seconds`, `last_apps`). Story 3.3 ships a **more substantial reshape than originally pre-flagged**: the four raw-component fields are removed and replaced by five pre-rendered label fields (`intro_lines`, `seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`). The reshape moves content composition out of Skin (where it would otherwise land per the original raw-field design) and into Ritual, where architecture.md:753 says ViewModel population belongs. The reshape is safe today because no runtime code populates the dataclass — Story 3.2 did not touch it (Story 3.2 § Open question 3). See § Group A for the full field shape; § Architecture Decision 3b vs. shipped ViewModel for the deviation rationale.
- [`src/nova/ports/ritual.py`](../../src/nova/ports/ritual.py) declares `RitualPort.build_briefing(aggregate, state, tier) -> BriefingViewModel` and `RitualPort.begin_shutdown() -> ShutdownData`. Story 3.3 ships a concrete `RitualSystem` class implementing both methods — `build_briefing` with a real body, `begin_shutdown` with `raise NotImplementedError("Story 3.7 scope")`. The `NotImplementedError` keeps the structural-Protocol shape valid (mypy strict checks shape, not body); Story 3.7 fills the body when the shutdown flow lands.
- [`src/nova/ports/skin.py`](../../src/nova/ports/skin.py) declares the six T1 SkinPort methods: `render_briefing_card`, `render_progress`, `render_shutdown_card`, `render_response`, `collect_input`, `parse_command`. Story 3.3 ships a concrete `RichSkinAdapter` class with `render_briefing_card` populated and the other five raising `NotImplementedError("Story 3.X scope")` — `parse_command` defers to Story 3.4, `render_progress` to Story 3.6, `render_shutdown_card` / `render_response` / `collect_input` to Story 3.7. The placeholder bodies are unrelaxed by mypy (Protocol methods have ellipsis bodies; concrete `NotImplementedError` raises are valid concrete implementations).

### Story 2.4 locked: setup's `_render_state_a` body + the State A copy contract

[`src/nova/setup/__main__.py:78-104`](../../src/nova/setup/__main__.py#L78-L104) renders State A with the **locked verbatim copy** (Story 2.4 AC #1, [test_setup_init.py::test_state_a_body_contains_first_session_line](../../tests/unit/setup/test_main.py)):

```text
First session. No history yet — that's expected.
Let's set up your first workspace mode so tomorrow starts warm.
```

Title: `[bold cyan]N.O.V.A.[/bold cyan]`. Border: cyan. Padding: `(1, 2)`. Body styled `bright_white` per line.

Story 3.3's `RichSkinAdapter.render_briefing_card` MUST produce **byte-identical Console-recorded output** for State A. Locked by `test_state_a_render_matches_setup_render_state_a_byte_for_byte` (AC #18). Setup's renderer is **NOT** modified by this story — the pre-flag's "must coexist without divergence" reading. Setup runs before any NovaApp exists; bare-`nova` runs through Nerve→Ritual→Skin. Two code paths, one visual contract.

**Why setup is not rerouted to use `RichSkinAdapter`:** at setup time there is no `NovaApp`, no Brain, no `BriefingAggregate`. Constructing an empty aggregate purely to call `RitualSystem.build_briefing` for its State A pass-through is forced architecture. Setup's `_render_state_a` stays as the single source for the setup-time render; the parity test is the contract that catches divergence. If a later cleanup story (post-Epic 3) wants to merge the two render sites, the parity test makes the merge mechanical — both paths already produce identical output. Until then, "two renderers, one parity-locked contract" is the explicit pre-flag instruction.

### UX spec locked: design system + state contract

- [`_bmad-output/planning-artifacts/ux-design-specification.md:746-805`](../planning-artifacts/ux-design-specification.md#L746-L805) — Briefing Card State Contract (T1) defines the body copy for B and the seed-quote / "Last session: …" / "Apps: …" / resume-prompt skeleton for C.
- [`_bmad-output/planning-artifacts/ux-design-specification.md:392-431`](../planning-artifacts/ux-design-specification.md#L392-L431) — color system (`#5FB4D9` cyan = Rich `cyan`, `#E8E8E8` bright_white = Rich `bright_white`, `#C0C0C0` body white = Rich default text style, `#6B6B6B` dim gray = Rich `dim`) and typography hierarchy (panel title bold cyan, seed bold bright_white as the hero line, supporting context dim gray, prompt as final bold line).
- [`_bmad-output/planning-artifacts/ux-design-specification.md:432-450`](../planning-artifacts/ux-design-specification.md#L432-L450) — spacing / layout (vertical flow only, panel padding `(1, 2)`, no horizontal scrolling, content wraps).

The design system is implemented in this story as the literal Rich style strings (`"bold cyan"`, `"bold bright_white"`, `"dim"`, `"bright_white"`). Future centralization (a `nova.core.styles` module mapping semantic names to Rich style strings) is **out of scope** for Story 3.3 — Skin adapter is the only user today; centralization is YAGNI until a second consumer (e.g., the Knowledge Display in Epic 5) needs the same vocabulary.

### Architecture decision locked: Render Responsibility Boundary

[`_bmad-output/planning-artifacts/architecture.md:747-755`](../planning-artifacts/architecture.md#L747-L755) — table of who-decides-what:

| Concern | Owner | NOT owned by |
|---|---|---|
| Which state applies (A/B/C) | Nerve (Story 3.2) | Brain, Ritual |
| Assembling the view model, populating UI fields (`title`, `prompt_text`, `auto_start_setup`) | **Ritual (this story)** | Nerve, Skin |
| Generating prose enrichment | Voice (Epic 7) | Ritual, Skin |
| Mapping view model fields to Rich components | **Skin (this story)** | Ritual, Voice |

Story 3.3 ships the two **Ritual** + **Skin** rows. Skin makes **zero content decisions** — the adapter receives a fully-populated `BriefingViewModel` and maps fields to Rich primitives mechanically. Locked by `test_skin_makes_no_content_decisions` (AC #20): same ViewModel → byte-identical Rich output across runs (idempotency + determinism).

## Acceptance Criteria

### Group A: `BriefingViewModel` reshape — pre-rendered labels, no raw component fields

1. [`src/nova/systems/ritual/models.py`](../../src/nova/systems/ritual/models.py) `BriefingViewModel` is **reshaped to carry pre-rendered, user-facing label strings** rather than raw component data. The shipped placeholder shape (Story 1.9) carried `seed_text`, `last_mode`, `last_duration_seconds`, `last_apps` as raw component fields — Skin would have had to compose them into rendered lines (e.g., `f"Last session: {last_mode} mode, {format_duration(last_duration_seconds)}"`). That is content assembly, which architecture.md:753 places in Ritual, not Skin. Story 3.3's reshape moves all content composition into Ritual; Skin reads label fields and applies fixed styles.

   Final shape, in declaration order, 13 fields total:

   ```python
   @dataclass(frozen=True)
   class BriefingViewModel:
       # --- Render control ---
       state: BriefingState
       tier: CapabilityTier

       # --- UI chrome ---
       title: str                                # "N.O.V.A." (State A) or "Session Briefing" (B/C)

       # --- Behavioral signal (not rendered; Skin uses for auto-transition) ---
       auto_start_setup: bool                    # True only for State A

       # --- Pre-rendered body lines, in render order. Each is one line of
       # the panel body (intro_lines is a tuple of lines for multi-line
       # locked copy). Skin maps each to a fixed style and OMITS the line
       # entirely when None / empty tuple. Skin makes ZERO content
       # decisions on these strings — Ritual produces every visible
       # character including punctuation and prefix labels. ---

       intro_lines: tuple[str, ...]              # State A/B locked framing copy. Style: bright_white.
       seed_quote: str | None                    # Pre-quoted seed, e.g., '"Push the deploy through"'. Style: bold bright_white.
       last_session_label: str | None            # e.g., "Last session: Coding mode, 1h 42m". Style: dim.
       last_apps_label: str | None               # e.g., "Apps: VS Code, Terminal, Chrome". Style: dim.
       available_modes_label: str | None         # e.g., "Available mode: Coding" / "Available modes: Coding, Writing". Style: body white.
       prose_enrichment: str | None              # Voice-supplied paragraph. Style: body white. Always None in Epic 3.
       prompt_text: str | None                   # Final action line, e.g., "Resume Coding mode?". Style: bold bright_white.

       # --- Behavioral metadata for downstream consumers (Voice/Epic 7
       # contextual prose, Story 5.4 tier display, Epic 5 transparency).
       # Skin does NOT consume these for the briefing-card render. ---

       available_modes: tuple[ModeInfo, ...]
       suggested_mode: ModeInfo | None
   ```

   **Removed (4 fields):** `seed_text`, `last_mode`, `last_duration_seconds`, `last_apps`. All four were raw-component shapes that forced Skin to compose rendered lines.

   **Added (5 fields):** `intro_lines`, `seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`. All five carry pre-rendered text that Skin maps to a fixed style.

   **Kept (8 fields):** `state`, `tier`, `title`, `auto_start_setup`, `prose_enrichment`, `prompt_text`, `available_modes`, `suggested_mode`. The first six are unchanged in shape. The last two (`available_modes`, `suggested_mode`) are kept on the ViewModel as **behavioral metadata** — Voice/Epic 7's prose enrichment will read them, Story 5.4's tier display may consume them, Epic 5's transparency view may surface them — but Skin's `render_briefing_card` does not consult them in Epic 3 (they have no rendered form in this story).

   The dataclass remains `frozen=True`. Field order matters for positional construction in tests; fields are grouped by render role then behavioral metadata.

2. The class docstring is updated to:
   - Describe the field-level boundary: "Each rendered field is a complete user-facing string. Skin's only render-time decision is which Rich style to apply per field; no string formatting, no concatenation, no singular/plural inflection happens in Skin."
   - Pin the **progressive omission** contract: "`None` (for `Optional[str]` fields) and the empty-tuple `intro_lines == ()` are render-safe omission signals — Skin omits the corresponding line entirely rather than rendering an empty placeholder."
   - Pin the **rendered-label boundary**: "Pre-rendered label fields (`last_session_label`, `last_apps_label`, `available_modes_label`, `seed_quote`) carry the literal text Skin emits, including any prefix (`'Last session: '`), punctuation (the seed's quote characters), and singular/plural inflection (`'Available mode:'` vs `'Available modes:'`). Ritual is the single decision site; raw component fields (the underlying mode name, duration seconds, apps tuple) do not appear on the ViewModel."
   - Document the deviation from architecture.md Decision 3b (which listed raw-component fields): "The shipped shape deviates from architecture.md Decision 3b's original raw-component design (`seed_text`, `last_mode`, `last_duration`, `last_apps`). The user-clarified principle that Ritual owns user-facing copy and Skin only styles, plus the pre-flag's serialization-at-boundary invariant, together pushed the boundary granularity from 'raw data + Skin-side rendering rules' to 'pre-rendered text + Skin-side style assignment'. The architecture's separation of concerns (Brain projection → Nerve state → Ritual ViewModel → Skin render) is preserved; only the field granularity at the Ritual → Skin step changed."

3. **Shape regression test** at [`tests/unit/systems/ritual/test_briefing_view_model_shape.py::test_briefing_view_model_field_set_matches_ac_3_3`](../../tests/unit/systems/ritual/test_briefing_view_model_shape.py) walks `dataclasses.fields(BriefingViewModel)` and asserts the field tuple is **exactly** the 13 names in declaration order:
   ```python
   expected = (
       "state",
       "tier",
       "title",
       "auto_start_setup",
       "intro_lines",
       "seed_quote",
       "last_session_label",
       "last_apps_label",
       "available_modes_label",
       "prose_enrichment",
       "prompt_text",
       "available_modes",
       "suggested_mode",
   )
   ```
   The test additionally asserts:
   - `intro_lines` is annotated `tuple[str, ...]` (via `typing.get_type_hints(BriefingViewModel)`),
   - `seed_quote` / `last_session_label` / `last_apps_label` / `available_modes_label` are each annotated `str | None`,
   - the dataclass is still `frozen` (via `BriefingViewModel.__dataclass_params__.frozen is True`),
   - the four removed names (`seed_text`, `last_mode`, `last_duration_seconds`, `last_duration_display`, `last_apps`) are NOT present (regression guard against accidental re-introduction).

### Group B: Centralized duration formatter — `nova.core.formatting.format_duration_seconds`

4. New module [`src/nova/core/formatting.py`](../../src/nova/core/formatting.py) ships ONE public function and is the **single home** for any future render-safe formatting helpers (project-context.md:57 "Formatting/parsing must be centralized"). Module docstring states this explicitly and calls out the policy: "Render-safe formatting helpers used at any system → Skin boundary. Stages new helpers here rather than inlining at the use site so duration / datetime / mode-name normalization rules are reused, not duplicated."

5. **Public function `format_duration_seconds`** is **value-based and policy-free**: it returns a render string for ANY non-negative integer of seconds, including zero. The "session was interrupted" decision lives in Ritual (which checks `last_session.is_complete`), not in the formatter. Conflating the two would silently treat a very short completed session (e.g., user typed `shutdown` immediately after `nova` boot) as "interrupted" — wrong signal, wrong UX. Story 3.1 fixes the interrupted-session marker at the persistence layer (`is_complete=False`), and Story 3.3 honors that marker at the ViewModel-assembly layer.

   ```python
   def format_duration_seconds(seconds: int) -> str:
       """Return a render-safe duration string for a non-negative integer of seconds.

       Pure value-to-string mapping. Does NOT encode any session-state
       policy (e.g., interrupted vs. completed) — callers that need to
       suppress the duration on interrupted sessions handle that decision
       upstream of this function.

       - ``seconds == 0`` → ``"0s"`` (a completed session that rounded to
         zero seconds — rare but representable).
       - ``0 < seconds < 60`` → ``"{n}s"`` (e.g., ``"45s"``).
       - ``60 <= seconds < 3600`` → ``"{m}m"`` (e.g., ``"5m"``, ``"42m"``).
         Sub-minute remainder is dropped.
       - ``seconds >= 3600`` → ``"{h}h {m}m"`` (e.g., ``"1h 42m"``,
         ``"12h 0m"``). Hours and minutes use integer division and
         remainder; sub-minute remainder is dropped.
       - ``seconds < 0`` → ``ValueError("seconds must be non-negative")``;
         negative durations are not a representable session shape.
       """
   ```

   Implementation is pure: no clock, no I/O, no logging. The function is fully deterministic — same input → same output. The return type is `str` (not `str | None`) — every non-negative input produces a string; `None` is not a value the formatter ever returns.

6. **Tests at [`tests/unit/core/test_formatting.py`](../../tests/unit/core/test_formatting.py)** parametrize the canonical cases:

   - `(0, "0s")` — zero-second completed session (NOT a "None / omit" sentinel; that policy belongs in Ritual).
   - `(1, "1s")`, `(45, "45s")`, `(59, "59s")` — sub-minute boundary.
   - `(60, "1m")`, `(61, "1m")`, `(120, "2m")`, `(3599, "59m")` — minute band; sub-minute remainder is dropped.
   - `(3600, "1h 0m")`, `(3661, "1h 1m")`, `(6120, "1h 42m")`, `(43200, "12h 0m")` — hour band; sub-minute remainder is dropped.
   - `(-1, ValueError)`, `(-3600, ValueError)` — negative input rejected.

   Plus one `test_format_duration_seconds_is_pure` that calls the function with the same input three times and asserts byte-identical returns and zero side effects (no logging, no global mutation — verified by capturing `caplog` records and asserting empty). Plus one `test_format_duration_seconds_does_not_encode_interrupted_session_policy` that documents the boundary: it passes `seconds=0` and asserts the return is `"0s"` (not `None`), with a docstring noting that any caller treating zero as "interrupted" must check the upstream `is_complete` flag, not infer it from the duration value.

### Group C: `RitualSystem.build_briefing` — assembly logic

7. New file [`src/nova/systems/ritual/system.py`](../../src/nova/systems/ritual/system.py). Module docstring cites:
   - architecture.md Decision 3b § Field Population by State (lines 717-732) and § State C Fallback Rules (lines 734-745) as the AC source of truth.
   - project-context.md "Ritual owns ceremony logic; Nerve decides when ceremonies run" — Ritual's role is ViewModel **assembly**, not state determination (Nerve owns that, Story 3.2).
   - The non-goal: Story 3.3 ships `RitualSystem.build_briefing` only. `RitualSystem.begin_shutdown` raises `NotImplementedError("Story 3.7 scope")`. The two-method class is shipped together so the composition root can wire one `RitualSystem` instance now and Story 3.7 fills the second method without touching `app.py`.

8. **`RitualSystem` class** structurally satisfies `RitualPort` (Protocol, no nominal inheritance — same convention as `SqliteBrainAdapter` ↔ `BrainPort`, `NoOpShieldAdapter` ↔ `ShieldPort`). Class is **stateless** — no `__init__` parameters, no instance attributes; the methods are pure functions of their arguments. Class declaration:

   ```python
   class RitualSystem:
       """Concrete RitualPort implementation.

       Stateless: build_briefing is a pure function of (aggregate, state,
       tier); begin_shutdown will become Story 3.7's pure-orchestration
       entry point. No DB, no clock, no logging — debugging is via
       reproducing the input aggregate.
       """

       async def build_briefing(
           self,
           aggregate: BriefingAggregate,
           state: BriefingState,
           tier: CapabilityTier,
       ) -> BriefingViewModel:
           ...

       async def begin_shutdown(self) -> ShutdownData:
           raise NotImplementedError("Story 3.7 scope")
   ```

9. **`build_briefing` body — State A (`FIRST_RUN`)**: regardless of `tier`, return:

   ```python
   BriefingViewModel(
       state=BriefingState.FIRST_RUN,
       tier=tier,
       title="N.O.V.A.",
       auto_start_setup=True,
       intro_lines=(
           "First session. No history yet — that's expected.",
           "Let's set up your first workspace mode so tomorrow starts warm.",
       ),
       seed_quote=None,
       last_session_label=None,
       last_apps_label=None,
       available_modes_label=None,
       prose_enrichment=None,
       prompt_text=None,
       available_modes=(),
       suggested_mode=None,
   )
   ```

   The two `intro_lines` strings are **locked verbatim** — they match setup's `_render_state_a` body byte-for-byte. The locked strings live as module-level constants in `nova.systems.ritual.system` (e.g., `_STATE_A_INTRO_LINE_1`, `_STATE_A_INTRO_LINE_2`) so the parity test (AC #20) imports the constants for direct comparison rather than relying on string-literal duplication.

   `tier` rides through verbatim (Skin will surface it via Story 5.4's tier-notice path; Epic 3 does not branch on it for the panel). Aggregate fields are **not consulted** for State A — Nerve's state determination guarantees `available_modes == ()` and `last_session is None` are already true when state is FIRST_RUN (Story 3.2 AC #13), but Ritual does not re-validate that invariant — it trusts Nerve's contract.

10. **`build_briefing` body — State B (`POST_SETUP`)**: returns:

    ```python
    suggested = _select_suggested_mode_for_state_b(aggregate)
    available_modes_label = _build_available_modes_label(aggregate.available_modes)
    BriefingViewModel(
        state=BriefingState.POST_SETUP,
        tier=tier,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=("No saved seed from your last session.",),
        seed_quote=None,
        last_session_label=None,
        last_apps_label=None,
        available_modes_label=available_modes_label,
        prose_enrichment=None,
        prompt_text=_format_prompt("Start in {} mode?", suggested),
        available_modes=aggregate.available_modes,
        suggested_mode=suggested,
    )
    ```

    Helpers (private, leading-underscore module-level functions in `system.py`):

    - `_build_available_modes_label(modes: tuple[ModeInfo, ...]) -> str | None`:
      - `()` → `None` (omission signal).
      - One mode → `f"Available mode: {modes[0].display_name}"` (singular).
      - 2+ modes → `f"Available modes: {', '.join(m.display_name for m in modes)}"` (plural; comma-separated in `available_modes` order, which is stem-ascending per Story 3.2). The singular/plural inflection lives here, NOT in Skin.

    - `_format_prompt(template: str, mode: ModeInfo | None) -> str`:
      - `mode is not None` → `template.format(mode.display_name)`.
      - `mode is None` → `"What mode?"` (the architecture.md:745 fallback). Returns a plain string — never `None` for a state that has a prompt (only State A returns `prompt_text=None`).

    - `_select_suggested_mode_for_state_b(aggregate: BriefingAggregate) -> ModeInfo | None` — applies the tie-break ladder rungs **b → c → d → e** (rung a — match `last_session.mode_name` — is skipped because State B implies no usable last session; the AC #25 test `test_state_b_ignores_last_session_match_rung` locks this).

    The State B intro line is **locked verbatim** — `"No saved seed from your last session."` — and lives as a module-level constant `_STATE_B_INTRO_LINE` so tests reference the constant.

11. **`build_briefing` body — State C (`WARM_RESUME`)**: returns:

    ```python
    suggested = _select_suggested_mode_for_state_c(aggregate)
    last_session = aggregate.last_session
    last_session_label = _build_last_session_label(last_session, suggested)
    last_apps_label = _build_last_apps_label(aggregate.last_snapshot)
    seed_quote = _build_seed_quote(aggregate.last_seed)
    BriefingViewModel(
        state=BriefingState.WARM_RESUME,
        tier=tier,
        title="Session Briefing",
        auto_start_setup=False,
        intro_lines=(),                                  # State C has no locked preface
        seed_quote=seed_quote,
        last_session_label=last_session_label,
        last_apps_label=last_apps_label,
        available_modes_label=None,                      # State C omits the available-modes line
        prose_enrichment=None,                           # Epic 7 owns this; Epic 3 leaves it None
        prompt_text=_format_prompt("Resume {} mode?", suggested),
        available_modes=aggregate.available_modes,
        suggested_mode=suggested,
    )
    ```

    State C label-builder helpers, with full progressive-omission logic:

    - `_build_seed_quote(last_seed: str | None) -> str | None`:
      - `None` or empty string → `None` (progressive omission). The truthy check (`if last_seed:`) is the rule, NOT `is not None` — Story 3.7's shutdown flow rejects empty seed input upstream, so the empty-string case is "data-corruption defense in depth." Locked by AC #27's `test_state_c_handles_empty_string_seed_as_omission`.
      - Non-empty string → `f'"{last_seed}"'` (wrapped in straight quotes; Ritual produces the literal characters Skin emits).

    - `_build_last_session_label(last_session: SessionSummary | None, suggested: ModeInfo | None) -> str | None`:

      ```python
      def _build_last_session_label(
          last_session: SessionSummary | None,
          suggested: ModeInfo | None,
      ) -> str | None:
          if last_session is None:
              return None
          if last_session.mode_name is None:
              # Setup-row case (Story 2.4 writes NULL mode_name) — no mode label
              # is recoverable. Progressive omission of the "Last session" line.
              return None
          if suggested is None or suggested.stem != last_session.mode_name:
              # The mode was deleted between sessions, so we have a stem in
              # last_session.mode_name but no matching ModeInfo to source the
              # display_name. Progressive omission rather than rendering the
              # raw stem (which is filename-derived, not user-facing).
              return None
          mode_label = suggested.display_name
          if last_session.is_complete is False:
              # Interrupted session — omit duration, render mode only.
              # The "Story 3.1 says is_complete is False ⇒ duration_seconds == 0"
              # contract is the upstream signal; this branch is the explicit
              # policy site. format_duration_seconds is value-based and
              # would return "0s" for a 0-second completed session — that
              # policy split is documented in `nova.core.formatting`.
              return f"Last session: {mode_label} mode"
          duration_display = format_duration_seconds(last_session.duration_seconds)
          return f"Last session: {mode_label} mode, {duration_display}"
      ```

    - `_build_last_apps_label(last_snapshot: WorkspaceSnapshot | None) -> str | None`:

      ```python
      def _build_last_apps_label(
          last_snapshot: WorkspaceSnapshot | None,
      ) -> str | None:
          if last_snapshot is None:
              return None
          # WorkspaceSnapshot.windows is the SINGLE source for app names.
          # WorkspaceSnapshot has no .apps field — see eyes/models.py.
          # Filter out windows whose app_name is None (opaque / excluded
          # windows per project-context.md:175 + ux-spec.md:811 — their
          # identity fields are None upstream by Eyes' contract).
          app_names = tuple(
              w.app_name for w in last_snapshot.windows if w.app_name is not None
          )
          if not app_names:
              return None  # Empty workspace OR all windows opaque.
          return f"Apps: {', '.join(app_names)}"
      ```

    - `_select_suggested_mode_for_state_c(aggregate: BriefingAggregate) -> ModeInfo | None` — applies the full tie-break ladder rungs **a → b → c → d → e** (rung a is the match-`last_session.mode_name` step; AC #12 details).

    **Critical contract notes for State C:**

    - **`WorkspaceSnapshot` has NO `.apps` field.** The Eyes-layer model at [`src/nova/systems/eyes/models.py:42-54`](../../src/nova/systems/eyes/models.py#L42-L54) ships `windows: tuple[WindowContext, ...]` only. Story 3.3 is the **first cross-system consumer** of that shape; the apps tuple is derived in `_build_last_apps_label` by mapping `WindowContext.app_name` and filtering `None` (opaque / excluded windows). This filter is the cross-system surface; it is locked by AC #24's `test_state_c_with_opaque_window_filtered_from_apps`.
    - **The interrupted-session signal is `last_session.is_complete is False`, not `duration_seconds == 0`.** `format_duration_seconds` is value-based (returns `"0s"` for a zero-duration completed session, see Group B). Ritual's `_build_last_session_label` is the policy site that omits the duration when `is_complete is False`. A very short completed session (e.g., user typed `shutdown` immediately after boot) renders `"Last session: Coding mode, 0s"` — accurate, not silently relabeled as interrupted.
    - **De-duplication of repeated `app_name` values is deferred.** Two Chrome windows surface as `"Apps: Chrome, Chrome"` in T1. At ≤ ~10 windows in a typical setup capture, the duplication is visible-but-tolerable; the first consumer that genuinely needs unique-app rendering (Epic 4 / 6 mode-restore feedback) owns the dedup decision and updates this builder.

12. **Suggested-mode tie-break ladder** — module-level helper `_select_suggested_mode(aggregate)` (or two helpers — one for B, one for C — sharing a common `_pick_default_or_first` substep). Resolution order, evaluated top-to-bottom, **first match wins**:

    a. **State C only:** if `aggregate.last_session is not None` AND `aggregate.last_session.mode_name is not None`, find the `ModeInfo` whose `stem` matches `last_session.mode_name`. If found, return it. (State B never has a usable last_session per the state machine — Nerve's `determine_briefing_state` already excluded that.)

    b. The `ModeInfo` with the most recent `last_used_at` (lexicographic ISO-8601 compare; `None` is treated as smaller than any string, so a never-used mode loses every comparison). If `available_modes` is empty, skip; if only one mode has a non-None `last_used_at`, that one wins. **Ties** (two modes share the same ISO timestamp to the millisecond — possible in test fixtures, vanishingly rare in real use): break by alphabetical stem.

    c. The `ModeInfo` with `is_default is True` and the **alphabetically-first stem** (if multiple modes carry the flag).

    d. The **alphabetically-first stem** in `available_modes`.

    e. `None` (no available modes — happens for State A / B with empty config; State A returns None directly; State B with empty config is unusual but legal — the architecture.md:745 fallback `"What mode?"` covers it).

    Ladder is locked by parametrized tests in [`tests/unit/systems/ritual/test_suggested_mode.py`](../../tests/unit/systems/ritual/test_suggested_mode.py) covering each rung independently and the cascade between rungs.

13. **`__all__` exports both names** (alphabetical):

    ```python
    __all__: list[str] = ["RitualSystem"]
    ```

    The helper functions (`_select_suggested_mode_for_state_b`, `_select_suggested_mode_for_state_c`, `_format_prompt`) are leading-underscore private — they exist for tests to import directly when locking the tie-break ladder, but they are not part of the public surface.

14. **`nova.systems.ritual.__init__`** is updated from the placeholder docstring to re-export `RitualSystem` (mirrors Story 3.2's [`nova.systems.nerve.__init__`](../../src/nova/systems/nerve/__init__.py) treatment):

    ```python
    """Ritual system — briefing assembly, shutdown ceremony, seed lifecycle.

    Story 3.3 ships :class:`RitualSystem.build_briefing` for the State A/B/C
    Briefing Card pipeline; Story 3.7 will populate
    :meth:`RitualSystem.begin_shutdown`.
    """

    from nova.systems.ritual.system import RitualSystem

    __all__: list[str] = ["RitualSystem"]
    ```

### Group D: `RichSkinAdapter.render_briefing_card` — Rich rendering

15. New file [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py). Module docstring cites:
    - architecture.md:1377 directory layout — `adapters/rich/skin.py` is the Rich-specific Skin adapter.
    - project-context.md:64 — "Voice generates text; Skin renders it" / "Skin makes zero content decisions."
    - The **port-trapping invariant**: Rich-specific types (`rich.panel.Panel`, `rich.text.Text`, `rich.console.Console`) stay inside this file. The port surface uses domain types only.
    - The non-goal: Story 3.3 implements only `render_briefing_card`. The other five methods raise `NotImplementedError("Story 3.X scope")` with the corresponding story number; the SkinPort Protocol is structurally satisfied so the composition root can wire the adapter and Stories 3.4 / 3.6 / 3.7 fill the bodies in-place.

16. **`RichSkinAdapter` class.** Constructor takes a `Console` instance (dependency-inject the console so tests can pass `Console(record=True, file=io.StringIO(), width=80)` without monkeypatching). Class is **stateless beyond the console reference** — no buffers, no per-call state.

    ```python
    class RichSkinAdapter:
        """Concrete SkinPort implementation backed by the Rich library."""

        def __init__(self, console: Console) -> None:
            self._console = console

        async def render_briefing_card(self, view_model: BriefingViewModel) -> None:
            ...

        async def render_progress(self, results: Sequence[ActionResult]) -> None:
            raise NotImplementedError("Story 3.6 scope")

        async def render_shutdown_card(self, summary: SessionSummary) -> None:
            raise NotImplementedError("Story 3.7 scope")

        async def render_response(self, text: str) -> None:
            raise NotImplementedError("Story 3.7 scope")

        async def collect_input(self, prompt: str) -> str:
            raise NotImplementedError("Story 3.7 scope")

        async def parse_command(self, raw_input: str) -> Command:
            raise NotImplementedError("Story 3.4 scope")
    ```

17. **`render_briefing_card` body — render strategy:**

    Build a single `rich.text.Text` body from the ViewModel fields, then wrap in a `rich.panel.Panel` and `console.print(panel)`. The renderer is **state-agnostic** — there is NO `if view_model.state is …` branch in the render body. Skin reads label fields in a fixed order, applies a fixed style per field, and omits when the field is `None` / empty. Ritual decided what each line says; Skin decides only how it looks.

    - **Title** is rendered as `Panel(..., title=f"[bold cyan]{view_model.title}[/bold cyan]", ...)`.
    - **Border style**: `"cyan"` for every state. Padding: `(1, 2)`. (Matches setup's `_render_state_a` panel chrome — the parity test locks this.)
    - **Body construction (fixed render order, omit when None / empty):**

      | Order | Source field | Style | Omission rule | Spacing |
      |---|---|---|---|---|
      | 1 | `intro_lines` (each item, in tuple order) | `bright_white` | Skip entire group when `intro_lines == ()` | No leading blank |
      | 2 | `seed_quote` | `bold bright_white` | Skip when `seed_quote is None` | One blank line above ONLY if any line was rendered above |
      | 3 | `last_session_label` | `dim` | Skip when `None` | One blank line above ONLY if any line was rendered above (collapsed to zero blanks if previous line was already a blank) |
      | 4 | `last_apps_label` | `dim` | Skip when `None` | No blank — collapses tightly under `last_session_label` for the dim metadata block |
      | 5 | `available_modes_label` | `default` body white | Skip when `None` | One blank line above ONLY if any structured line was rendered above |
      | 6 | `prose_enrichment` | `default` body white | Skip when `None` | One blank line above ONLY if any structured line was rendered above |
      | 7 | `prompt_text` | `bold bright_white` | Skip when `None` | One blank line above ONLY if any line was rendered above (gives the bold action line visual separation per architecture.md:491) |

    - **Spacing rule simplified:** the renderer maintains a "previous-line-was-rendered" flag and inserts ONE blank line before order #2, #3, #5, #6, #7 (each transition between visual blocks) IF the flag is set. Order #1 (intro_lines) and order #4 (last_apps_label, which collapses tight against last_session_label) do not insert a leading blank. After every emitted line, the flag is set to true. The result is that State A renders as two adjacent intro lines (no blank between them); State B renders as `intro → blank → available_modes_label → blank → prompt_text`; State C renders as `seed_quote → blank → last_session_label → last_apps_label → blank → prompt_text` (or with prose_enrichment between, when supplied).

    - **No state-specific code paths.** State A produces a ViewModel with `intro_lines` populated and everything else None / empty → only #1 fires → two lines, no prompt. State B produces `intro_lines + available_modes_label + prompt_text` → #1, #5, #7 fire → preface + available modes + prompt. State C produces `seed_quote + last_session_label + last_apps_label + prompt_text` (and optionally `prose_enrichment`) → #2, #3, #4, [#6,] #7 fire. The renderer does not check `view_model.state` to choose; the omission rules and ViewModel-field presence drive everything.

    - **Skin makes ZERO content decisions.** The renderer never invokes `format()`, never computes singular/plural, never wraps quotes around values, never emits a literal-string preface. Every visible character originates in the ViewModel — set there by `RitualSystem.build_briefing`. Skin's responsibilities are limited to:
      1. Choosing a Rich style per field (the table above is the entire mapping).
      2. Inserting blank-line spacing per the spacing rule above.
      3. Constructing the `Panel` chrome (title, border, padding).

    - **`tier` does not affect the panel chrome in Epic 3.** A tier-notice render path (single amber line above the panel for `DEGRADED` / `OFFLINE`) is Story 5.4 / 8.x scope. Story 3.3's render is tier-orthogonal: every test parametrizes `(state, tier)` and asserts the recorded output is byte-identical across `tier` values. The tier-notice path will be a separate render method on Skin, not a branch in `render_briefing_card`.

### Group E: Composition root wiring

18. [`src/nova/app.py`](../../src/nova/app.py) `NovaApp` dataclass gains TWO new fields after `shield`, before `close`:

    ```python
    ritual: RitualPort
    skin: SkinPort
    ```

    `create_app` instantiates both inside the existing `try:` block (the same partial-init cleanup boundary that already covers `brain`, `event_bus`, `audit`, `tier_manager`, `shield_adapter`):

    ```python
    ritual: RitualPort = RitualSystem()
    logger.info("ritual system wired", extra={"system": type(ritual).__name__})

    skin: SkinPort = RichSkinAdapter(console=Console())
    logger.info("skin adapter wired", extra={"adapter": type(skin).__name__})
    ```

    The `Console()` instance is constructed inside `create_app` (no global console import) so each `NovaApp` graph owns its own console — important for tests that inject a recording console via subclassing or by overriding the field post-construction (frozen dataclass + `slots=True` means `dataclasses.replace(app, skin=...)` is the test-time substitution path, not attribute mutation).

    `NovaApp.close` is **unchanged** — neither `RitualSystem` nor `RichSkinAdapter` holds resources (no DB, no network, no file handles; the `Console` is sync stdout-bound and needs no async close). The only `_close` body remains `await storage.close()`.

19. **Composition-root regression tests** at [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) gain TWO positive instantiation tests modeled on the existing `test_sqlite_brain_adapter_is_instantiated_inside_create_app` (line 296):

    - `test_ritual_system_is_instantiated_inside_create_app` — walks `create_app`'s AST body for `ast.Call` nodes whose callee is `ast.Name(id="RitualSystem")`. Asserts at least one match exists. Catches a silent-deletion regression where the wiring is removed and `NovaApp.ritual` is left unassigned at the type-checker level.
    - `test_rich_skin_adapter_is_instantiated_inside_create_app` — same shape, checking `RichSkinAdapter`.

    The negative test `test_app_module_level_has_no_adapter_instantiation` (line 206) automatically covers the new adapter — `RichSkinAdapter` is imported from `nova.adapters.rich`, so `adapter_symbols` picks it up. The adapter-subpackage isolation test (`test_adapter_subpackages_stay_intra_package`, line 164) requires `nova.adapters.rich.skin` to import only from its own subpackage (or from `nova.ports.*` / `nova.systems.*.models` / `nova.core.*`). The existing test passes structurally because `RichSkinAdapter` does not import from another adapter subpackage.

### Group F: Visual parity with setup's `_render_state_a`

20. **Plain-text parity test (CORE PRODUCT CONTRACT)** at [`tests/unit/test_briefing_state_a_parity.py::test_state_a_plain_text_matches_setup_render`](../../tests/unit/test_briefing_state_a_parity.py):

    ```python
    @pytest.mark.asyncio
    async def test_state_a_plain_text_matches_setup_render() -> None:
        """Core product contract: what the user reads on screen is identical
        between setup's State A render and the bare-`nova` State A render.
        Locks panel chrome (title, border characters, padding) + body copy
        (the two locked intro lines). ANSI styling is NOT compared here —
        see test_state_a_ansi_byte_stream_matches_setup_render for that."""

        # Render the new pipeline's State A.
        view_model = await RitualSystem().build_briefing(
            aggregate=BriefingAggregate(
                last_session=None, last_snapshot=None, last_seed=None,
                available_modes=(), recent_memory=(),
            ),
            state=BriefingState.FIRST_RUN,
            tier=CapabilityTier.OFFLINE,
        )
        new_console = Console(record=True, file=StringIO(), width=80, color_system=None)
        await RichSkinAdapter(console=new_console).render_briefing_card(view_model)
        new_output = new_console.export_text()

        # Render setup's existing State A through the same console settings.
        from nova.setup.__main__ import _render_state_a as setup_render
        setup_console = Console(record=True, file=StringIO(), width=80, color_system=None)
        setup_render(setup_console)
        setup_output = setup_console.export_text()

        assert new_output == setup_output, (
            "RichSkinAdapter State A plain-text output diverged from setup's _render_state_a"
        )
    ```

    `color_system=None` disables ANSI so the comparison is on plain text only — the visible **product contract** (title, borders, copy, line ordering) is what this test locks. A failure here means the user-visible product diverged. **This is the test that must always pass.**

    **Companion test (same file): ANSI styling-regression guard.** Add a second test `test_state_a_ansi_byte_stream_matches_setup_render` that runs the same comparison with `color_system="truecolor"` and asserts the recorded ANSI byte stream is identical between the two renderers.

    ```python
    @pytest.mark.asyncio
    async def test_state_a_ansi_byte_stream_matches_setup_render() -> None:
        """Styling-regression guard. STRICTER than the plain-text parity — catches
        drift in style markers, color codes, and Rich-version-specific spacing.

        BRITTLENESS NOTE: this test may flake on Rich version upgrades or
        terminal-detection differences. The plain-text parity test (above) is
        the IRONCLAD product-contract guard; this test is a styling-impl-drift
        signal. If the plain-text parity passes but this ANSI test fails, the
        user-visible product is still correct — investigate whether the
        styling change is visually meaningful, update the snapshot if not,
        or pin the Rich version if so. Never silence this test by removing it.
        """
        # ... same setup as plain-text test, but with color_system="truecolor"
        # and the assertion is on console.export_text(styles=True) (or
        # equivalent ANSI capture).
        ...
    ```

    **Test-failure triage:**
    - **Plain-text parity fails** → product-visible regression; the renderer or `_render_state_a` diverged. Fix immediately; never ship.
    - **Plain-text parity passes, ANSI parity fails** → styling-impl drift (Rich version delta or rendering option change). Investigate; update the ANSI snapshot if visually equivalent, or fix the renderer if a real styling regression. Not a ship blocker if the visible product is correct.

21. **Tier-orthogonality test** at [`tests/unit/adapters/rich/test_skin_adapter.py::test_state_a_render_is_tier_independent`](../../tests/unit/adapters/rich/test_skin_adapter.py): for each tier in `(FULL, DEGRADED, OFFLINE)`, render State A and assert the recorded output is byte-identical across the three. Same test at the State B and State C levels (`test_state_b_render_is_tier_independent`, `test_state_c_render_is_tier_independent`) — the tier field rides on the ViewModel but Skin's render path in Epic 3 ignores it (tier-notice rendering is Story 5.4's separate render method).

### Group G: ViewModel construction tests

22. **State A tests** at [`tests/unit/systems/ritual/test_briefing_view_model.py`](../../tests/unit/systems/ritual/test_briefing_view_model.py):

    - `test_state_a_view_model_has_locked_field_values` — calls `build_briefing(empty_aggregate, FIRST_RUN, FULL)` and asserts every field matches AC #9: `title=="N.O.V.A."`, `auto_start_setup is True`, `intro_lines == (_STATE_A_INTRO_LINE_1, _STATE_A_INTRO_LINE_2)` (asserts equality with the module-level constants imported from `nova.systems.ritual.system`, NOT against duplicated literal strings — Story 3.7 / future copy edits stay locked at one site), `seed_quote is None`, `last_session_label is None`, `last_apps_label is None`, `available_modes_label is None`, `prose_enrichment is None`, `prompt_text is None`, `available_modes == ()`, `suggested_mode is None`.
    - `test_state_a_ignores_aggregate_modes` — calls with `aggregate.available_modes` populated (a degenerate case Nerve's state determination would not produce, but Ritual must trust its inputs and not branch on aggregate contents in State A). Asserts the returned ViewModel's `available_modes == ()` regardless of the input aggregate's modes.

23. **State B tests:**

    - `test_state_b_view_model_with_one_mode` — fixture: one mode with `stem="coding"`, `display_name="Coding"`, `is_default=True`, no `last_session`, no `last_seed`. Asserts `intro_lines == ("No saved seed from your last session.",)`, `available_modes_label == "Available mode: Coding"` (singular), `prompt_text == "Start in Coding mode?"`, `suggested_mode.stem == "coding"`, `seed_quote is None`, `last_session_label is None`, `last_apps_label is None`.
    - `test_state_b_view_model_with_multiple_modes_uses_default_first` — fixture: `coding (display_name="Coding", is_default=False)`, `writing (display_name="Writing", is_default=True)`. Asserts the suggestion is `writing` (rung c of the tie-break ladder), `available_modes_label == "Available modes: Coding, Writing"` (plural; comma-joined in stem-ascending order set by Story 3.2), `prompt_text == "Start in Writing mode?"`.
    - `test_state_b_view_model_with_no_modes_falls_back_to_what_mode` — fixture: empty aggregate. The test calls `build_briefing` directly with `state=POST_SETUP` to lock the fallback prompt path. Asserts `available_modes_label is None` (helper returns None for empty modes), `prompt_text == "What mode?"`, `suggested_mode is None`.

24. **State C tests:**

    - `test_state_c_with_seed_and_session_renders_full` — fixture: aggregate with `last_seed="Push the deploy through"`, `last_session.mode_name="coding"` (matching configured mode `Coding`), `last_session.is_complete=True`, `last_session.duration_seconds=6120` (1h 42m), `last_snapshot.windows` containing three `WindowContext` rows with `app_name` values `"VS Code"`, `"Terminal"`, `"Chrome"`. Asserts ViewModel has `intro_lines == ()` (State C has no preface), `seed_quote == '"Push the deploy through"'` (Ritual-applied quotes), `last_session_label == "Last session: Coding mode, 1h 42m"`, `last_apps_label == "Apps: VS Code, Terminal, Chrome"`, `available_modes_label is None` (State C omits the available-modes line), `prompt_text == "Resume Coding mode?"`, `prose_enrichment is None`.
    - `test_state_c_with_setup_row_only_omits_progressively` — fixture: aggregate with the Story 2.4 setup row (`last_session.mode_name=None`, `last_seed=None`, `last_snapshot.windows == ()` — empty workspace), `available_modes` populated with at least one default mode. State determination already evaluated to WARM_RESUME. Asserts: `intro_lines == ()`, `seed_quote is None`, `last_session_label is None`, `last_apps_label is None`, `prompt_text == "Resume {default_display_name} mode?"`. This is the **first-session-2-after-setup** card; progressive omission carries the whole render.
    - `test_state_c_with_interrupted_session_omits_duration` — fixture: `last_session.is_complete=False`, `duration_seconds=0` (Story 3.1 convention), `mode_name="coding"` (matching `Coding`), `last_seed="partial thought"`, `last_snapshot=None`. Asserts `last_session_label == "Last session: Coding mode"` (mode rendered, duration tail omitted because `is_complete is False` — explicit policy site, not formatter-encoded), `seed_quote == '"partial thought"'` (interrupted ≠ no-seed; the seed was captured before the crash), `last_apps_label is None`.
    - `test_state_c_with_completed_session_zero_duration_renders_zero_seconds` — fixture: `last_session.is_complete=True`, `duration_seconds=0` (a degenerate but representable case: user typed `shutdown` immediately after boot). Asserts `last_session_label == "Last session: Coding mode, 0s"` — accurate rendering, NOT silent relabeling as interrupted. Locks the policy split between Ritual (interrupted-session decision) and `format_duration_seconds` (value-based formatting).
    - `test_state_c_with_deleted_mode_omits_last_session_label` — fixture: `last_session.mode_name="archived"` (a stem that was deleted between sessions, so no matching ModeInfo in `available_modes`). Asserts `last_session_label is None` (no display_name available → progressive omission, NOT raw-stem leak); `suggested_mode` resolves via rung b/c/d (does NOT raise); `prompt_text` resolves to the fallback mode's prompt.
    - `test_state_c_with_opaque_window_filtered_from_apps` — fixture: `last_snapshot.windows` containing one `WindowContext(app_name="VS Code", window_title="…", process_name="Code.exe", is_opaque=False)` and one `WindowContext(app_name=None, window_title=None, process_name=None, is_opaque=True)` (an excluded app). Asserts `last_apps_label == "Apps: VS Code"` — opaque windows do not surface their identity in the briefing, project-context.md:175 sensitive-content rule.
    - `test_state_c_with_no_snapshot_omits_apps_label` — fixture: `last_snapshot=None`. Asserts `last_apps_label is None`.
    - `test_state_c_with_empty_seed_string_omits_seed_quote` — fixture: `last_seed=""` (data-corruption defense in depth — Story 3.7's shutdown rejects empty seeds upstream, so this case shouldn't reach Ritual in production, but the helper handles it gracefully). Asserts `seed_quote is None` — empty string is falsy, so the truthy check fires the omission branch.

25. **Suggested-mode tie-break tests** at [`tests/unit/systems/ritual/test_suggested_mode.py`](../../tests/unit/systems/ritual/test_suggested_mode.py):

    - `test_state_c_prefers_last_session_mode_match` — fixture with `last_session.mode_name="coding"`, three available modes including `coding`. Asserts the suggestion is `coding` (rung a wins over b/c/d).
    - `test_state_c_falls_through_when_last_mode_not_in_available` — fixture with `last_session.mode_name="archived"`, three available modes none of which match. Asserts rung b kicks in.
    - `test_picks_most_recent_last_used_at_when_present` — fixture with three modes, two have `last_used_at` (different timestamps), one has `None`. Asserts the most recent wins.
    - `test_breaks_last_used_at_tie_alphabetically` — fixture: two modes share an identical `last_used_at` ISO string. Asserts the alphabetically-first stem wins.
    - `test_falls_back_to_default_when_no_last_used_at` — fixture: three modes, none have `last_used_at`, one has `is_default=True`. Asserts the default wins.
    - `test_breaks_default_tie_alphabetically` — fixture: three modes, two carry `is_default=True`. Asserts the alphabetically-first defaulted stem wins.
    - `test_falls_back_to_alphabetically_first_when_no_default` — fixture: three modes, none default, none used. Asserts the alphabetically-first stem wins.
    - `test_returns_none_for_empty_modes` — empty `available_modes`. Asserts `None`.
    - `test_state_b_ignores_last_session_match_rung` — fixture mimicking State C inputs but caller is `_select_suggested_mode_for_state_b`. Asserts rung a is **not** consulted (State B implies no usable last_session by the state machine; the helper must not reach into `aggregate.last_session`).

### Group H: Skin render tests (Rich Console capture)

26. **Render tests** at [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py). Pattern (used by every test):

    ```python
    console = Console(record=True, file=StringIO(), width=80, color_system="truecolor")
    adapter = RichSkinAdapter(console=console)
    await adapter.render_briefing_card(view_model)
    output = console.export_text()      # plain text — assert structure
    ansi = console.export_text(styles=True)  # ANSI — assert style markers when relevant
    ```

    Render tests construct ViewModels directly with pre-populated label fields (no Ritual call); the renderer's responsibility is purely styling + layout, so tests assert that the labels appear verbatim in the output and that omission rules apply. **Tests do NOT verify that Ritual produced the right labels** — that is Group G's job.

    Required tests:

    - `test_intro_lines_render_in_bright_white` — fixture: ViewModel with `intro_lines=("Line one.", "Line two.")` and everything else None / empty. Assert both lines appear in `output` in order, and assert the ANSI output (`export_text(styles=True)`) contains the bright_white style sequence wrapping each line.
    - `test_intro_lines_empty_renders_no_preface` — fixture: `intro_lines=()`. Assert `"Line one." not in output` (use the previous fixture's strings to confirm absence — the renderer respects empty-tuple omission).
    - `test_seed_quote_renders_bold_bright_white` — fixture: `seed_quote='"Push the deploy through"'` (already quoted by Ritual; Skin emits verbatim). Assert `'"Push the deploy through"' in output` (the literal characters including the quote marks). Assert the ANSI output contains a `bold` + `bright_white` style around the line.
    - `test_seed_quote_none_omits_line` — fixture: `seed_quote=None`. Assert `'"' not in output` (no quote characters appear).
    - `test_last_session_label_renders_dim` — fixture: `last_session_label="Last session: Coding mode, 1h 42m"`. Assert the literal string appears in `output`. Assert the ANSI output contains a `dim` style on the line.
    - `test_last_session_label_none_omits_line` — fixture: `last_session_label=None`. Assert `"Last session" not in output`.
    - `test_last_apps_label_renders_dim` — fixture: `last_apps_label="Apps: VS Code, Terminal, Chrome"`. Assert the literal string appears, with `dim` style.
    - `test_last_apps_label_none_omits_line` — fixture: `last_apps_label=None`. Assert `"Apps:" not in output`.
    - `test_available_modes_label_renders_body_white` — fixture: `available_modes_label="Available modes: Coding, Writing"`. Assert literal string appears. Assert no `bold` and no `dim` on the line — body white is the default style (no markup).
    - `test_available_modes_label_none_omits_line` — fixture: `available_modes_label=None`. Assert `"Available mode" not in output`.
    - `test_prompt_text_renders_bold_bright_white_at_panel_end` — fixture: full State C ViewModel with `prompt_text="Resume Coding mode?"`. Capture `output.splitlines()` and assert the last non-blank, non-border line contains `"Resume Coding mode?"`. Assert ANSI carries `bold` + `bright_white`.
    - `test_prompt_text_none_omits_line` — fixture: State A ViewModel with `prompt_text=None`. Assert `"Resume" not in output` AND `"Start in" not in output` AND `"What mode?" not in output`.
    - `test_prose_enrichment_renders_after_structured_fields` — fixture: full State C ViewModel including `prose_enrichment="Two-day arc on the auth refactor; tomorrow closes the loop."` (Epic 7 placeholder content — the field's first real Skin test). Capture `output.splitlines()` and assert the prose line index is GREATER than the `last_apps_label` line index AND LESS than the `prompt_text` line index. Locks the layout for Epic 7's first real prose write.
    - `test_prose_enrichment_none_omits_line` — fixture: full State C ViewModel with `prose_enrichment=None` (Epic 3 default). Assert no "enrichment unavailable" text, no two-blank-line gap between `last_apps_label` and `prompt_text` (the natural rhythm: structured fields, one blank, prompt).
    - `test_state_a_complete_render` — fixture: a State A ViewModel built via `RitualSystem.build_briefing` (or hand-constructed to match its output). Assert title `"N.O.V.A."` in output, both intro lines present, no `"Resume"`, no `"Start in"`, no `"What mode?"`, no quote characters (no `'"'` in output).
    - `test_state_b_complete_render` — fixture: State B ViewModel with `intro_lines=("No saved seed from your last session.",)`, `available_modes_label="Available modes: Coding, Writing"`, `prompt_text="Start in Writing mode?"`, all others None. Capture `output.splitlines()` and assert:
      - `"No saved seed from your last session."` appears BEFORE `"Available modes: Coding, Writing"`,
      - `"Available modes: Coding, Writing"` appears BEFORE `"Start in Writing mode?"`,
      - the bottom non-blank line is the prompt.
    - `test_state_c_complete_render` — fixture: State C ViewModel with seed_quote, last_session_label, last_apps_label, prompt_text populated. Assert line order: seed → last_session → apps → prompt. Assert blank line between seed and last_session blocks, and between apps and prompt.
    - `test_panel_chrome_is_cyan_with_padding` — assert the ANSI output contains the cyan-border bytes (`\x1b[36m` or the equivalent truecolor sequence). Verify the `Panel(..., padding=(1, 2), border_style="cyan", title="[bold cyan]…[/bold cyan]")` construction by parsing the recorded ANSI for the expected sequences. **Brittleness note**: this test is Rich-version-sensitive; if it fails on a Rich upgrade, follow the same triage as the ANSI parity test (AC #20).
    - `test_skin_makes_no_content_decisions` (idempotency lock) — render the same ViewModel three times into three fresh consoles; assert the three recorded outputs are byte-identical. The renderer has no clock, no random, no content branching, no string formatting — same input must produce identical bytes.
    - `test_renderer_does_not_consult_view_model_state_field` — fixture: pair of ViewModels constructed with `state=BriefingState.FIRST_RUN` and `state=BriefingState.WARM_RESUME` but otherwise identical field values (e.g., both with the same intro_lines + prompt_text). Assert the recorded outputs are byte-identical. Locks the architectural rule that the renderer does NOT branch on `view_model.state` — the omission rules and field presence drive everything.
    - `test_renderer_does_not_consult_view_model_available_modes_or_suggested_mode` — fixture: pair of ViewModels identical except for `available_modes` and `suggested_mode` (one populated, one empty / None). Assert outputs are byte-identical. Locks that these behavioral-metadata fields don't accidentally leak into the render.

27. **Render edge cases** — three additional tests covering the long-content path:

    - `test_long_seed_quote_wraps_within_panel_width` — fixture: a 200-character seed. Construct ViewModel with `seed_quote='"<200-char text>"'`. Assert (a) the full seed text appears in the output (no truncation), (b) it wraps across multiple lines within the 80-column panel, (c) any subsequent lines (last_session_label, prompt_text) still appear in their expected order.
    - `test_long_apps_label_wraps_in_panel_width` — fixture: `last_apps_label="Apps: " + ", ".join(twelve_app_names_totaling_200_chars)`. Assert all 12 names appear in the output, comma-separated, wrapping within panel width without truncation.
    - `test_renderer_handles_unicode_characters_in_labels` — fixture: `seed_quote='"Café au lait — push the deploy"'` (em-dash, non-ASCII). Assert the literal characters (including em-dash and accented `é`) appear in the output. Locks UTF-8 handling at the render layer; complements setup's `_force_utf8_stdout` upstream contract.

### Group I: AST isolation guards

28. [`tests/unit/systems/ritual/test_ritual_isolation.py`](../../tests/unit/systems/ritual/test_ritual_isolation.py) — AST guards on `nova.systems.ritual.system` and `nova.systems.ritual.models`. Mirrors Story 3.2's `test_briefing_isolation.py` shape. Forbidden:

    - `nova.adapters.*` at any scope (systems consume ports, never adapters).
    - `nova.systems.{eyes,hands,nerve,shield,skin,voice,ritual}.<non-models>` (no reaching into other systems' internals; cross-system contract is models-only per Story 1.9 AC #8).
    - `nova.app` / `nova.cli` / `nova.setup.*` (no upward reach to composition / entry-point layers).
    - Third-party I/O modules: `sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32api`, `win32gui`, `win32com`, `win32con`, `rich` (Ritual is rendering-agnostic; Rich lives only in `adapters/rich/skin.py`), `yaml`.
    - Dynamic imports of any forbidden prefix (`__import__` / `importlib.import_module`).

    Allowed positive list:

    - stdlib (`__future__`, typing, dataclasses).
    - `nova.core.types` (BriefingState, CapabilityTier).
    - `nova.core.formatting` (format_duration_seconds — Story 3.3 introduction).
    - `nova.systems.brain.models` (BriefingAggregate, ModeInfo, SessionSummary, WorkspaceSnapshot via re-export — actually `WorkspaceSnapshot` lives in `nova.systems.eyes.models`; the cross-system surface allowance is for that `.models` module specifically).
    - `nova.systems.eyes.models` (WorkspaceSnapshot — needed because Ritual reads `last_snapshot.windows` to derive `last_apps`). Add this to the allowlist with a comment: "Cross-system .models import — Story 1.9 AC #8 portable suffix; Ritual derives last_apps from WorkspaceSnapshot.windows."
    - `nova.systems.ritual.models` (BriefingViewModel, ShutdownData — same-package internal).

29. [`tests/unit/adapters/rich/test_skin_adapter_isolation.py`](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py) — AST guards on `nova.adapters.rich.skin`. Forbidden:

    - `nova.app` / `nova.cli` / `nova.setup.*` (adapters do not reach upward).
    - `nova.adapters.{shield,sqlite}` (adapter-subpackage isolation, already locked at composition-root level by `test_adapter_subpackages_stay_intra_package` — the new test repeats the assertion at the file level with a clearer error message).
    - Third-party I/O modules other than `rich` (the adapter is allowed to import Rich — that is its purpose). Forbid `sqlite3`, `anthropic`, `pywin32`, `pywintypes`, `psutil`, `win32*`, `yaml`.
    - Dynamic imports of any forbidden prefix.
    - Direct imports from `nova.systems.<system>.<non-models>` (adapters consume domain types only via `.models`).

    Allowed:

    - stdlib (`__future__`, `collections.abc`, typing).
    - `rich.console`, `rich.panel`, `rich.text` (the Rich types this adapter uses — pinned to those three to prevent accidental dependency on `rich.markdown` / `rich.tree` / etc. at this stage).
    - `nova.core.types` (BriefingState, CapabilityTier — for state branching in the renderer).
    - `nova.systems.brain.models` (SessionSummary — declared in the SkinPort signature for `render_shutdown_card`).
    - `nova.systems.hands.models` (ActionResult — declared in the SkinPort signature for `render_progress`).
    - `nova.systems.ritual.models` (BriefingViewModel — the input type for `render_briefing_card`).
    - `nova.systems.skin.models` (Command — declared in the SkinPort signature for `parse_command`).

30. **Existing AST guards that should continue to pass without modification** — verify before implementing:

    - [`tests/unit/test_composition_root.py::test_only_app_and_cli_import_adapters`](../../tests/unit/test_composition_root.py#L121) (line 121) — passes because `RichSkinAdapter` is imported only by `app.py`, never by a system or by ports.
    - [`tests/unit/test_composition_root.py::test_systems_never_import_adapters`](../../tests/unit/test_composition_root.py#L150) (line 150) — passes because `nova.systems.ritual.system` does not import `nova.adapters.*`.
    - [`tests/unit/test_composition_root.py::test_adapter_subpackages_stay_intra_package`](../../tests/unit/test_composition_root.py#L164) (line 164) — passes because `nova.adapters.rich.skin` imports only from its own subpackage, plus ports / systems' models. The test allows imports from outside `nova.adapters.*` as long as they don't cross to a sibling adapter subpackage.
    - [`tests/unit/systems/nerve/test_briefing_isolation.py`](../../tests/unit/systems/nerve/test_briefing_isolation.py) (Story 3.2's nerve isolation) — `nova.systems.ritual` is on its forbidden list (line 68); Story 3.3 does not modify `nova.systems.nerve.briefing`, so the existing guard continues to pass.

### Group J: Cross-cutting pattern + invariant locks

31. **Patterns consulted:**
    - **#2 AST guards** — two new isolation tests (`test_ritual_isolation.py`, `test_skin_adapter_isolation.py`). The composition-root positive instantiation tests (AC #19) are also AST-based.
    - **#3 frozen dataclass** — `BriefingViewModel` reshape stays `frozen=True`. `RitualSystem` is **not** a dataclass (it is a stateless class with methods only). `RichSkinAdapter` is **not** a dataclass either (it carries one `Console` reference; dataclass-with-mutable-state gives no benefit here, plain class with `__init__` is the right shape).
    - **#7 partial-init cleanup** — Story 3.3 adds two new resources to the composition root. Neither holds external state (no DB, no file, no socket, no executor); the existing `try: … except BaseException: await storage.close()` block already covers them — no new cleanup branch is required. Locked by adding a positive test that simulates a `RitualSystem()` constructor failure (monkeypatch class to raise) and asserts `storage.close()` was still awaited.

32. **Patterns NOT consulted (and why):**
    - **#1 clock indirection** — Ritual's `build_briefing` is pure; no timestamps stamped. Skin's `render_briefing_card` is pure rendering; no timestamps either. Duration formatting is value-derivation (`int → str`), not timestamp emission.
    - **#4 error translation** — Skin's render path can produce `UnicodeEncodeError` from a non-UTF-8 stdout (e.g., legacy cp1252 Windows terminal). Setup's [`_force_utf8_stdout`](../../src/nova/setup/__main__.py#L62-L75) pre-emptively reconfigures stdout / stderr; the bare-`nova` entrypoint (Story 3.5) will land the same call before invoking Skin. Story 3.3 does NOT introduce a Skin-side exception translation — the encoding contract belongs at the entrypoint, not at the renderer. If a future encoding-contract violation surfaces, the entrypoint owns the fix; Skin's render is allowed to fail loudly. (Story 3.5 will likely add an `_force_utf8_stdout` mirror or move the helper to a shared `nova.cli` location.)
    - **#5 per-file skip-on-error vs. singleton hard-fail** — no file loading in this story.
    - **#6 transaction CM** — no DB writes.

33. **`logger` allowlist update** — `tests/unit/test_composition_root.py::_LOGGER_NAME_DEPTH_ALLOWLIST` (line 335) does NOT need an entry for the new modules. `nova.systems.ritual.system` is two-dot-after-`nova.` (no, three-dot-counting-from-`nova` actually: `nova.systems.ritual.system`); the convention test at [`test_logger_names_follow_convention`](../../tests/unit/test_composition_root.py#L369) accepts 1- and 2-dot logger names plus the storage allowlist. **Two-dot-from-`nova`** is `nova.layer.module` (e.g., `nova.systems.ritual` would be 2 dots). The Story 3.3 modules SHOULD use `logger = logging.getLogger("nova.systems.ritual")` and `logger = logging.getLogger("nova.adapters.rich.skin")` — the latter has THREE dots, matching the allowlist precedent for `nova.adapters.sqlite.brain`. **Add `nova.adapters.rich.skin` to `_LOGGER_NAME_DEPTH_ALLOWLIST`** (one new line, alongside `nova.adapters.sqlite.brain`). Update the comment block above the allowlist to note that `adapters/{driver}/{system}` is the established three-dot pattern for concrete system adapters.

    Story 3.3's `nova.systems.ritual.system` uses `logger = logging.getLogger("nova.systems.ritual")` (2 dots after `nova.`) — within convention, no allowlist entry needed.

34. **Logging surface (DEBUG only):**
    - `RitualSystem.build_briefing` does NOT log. Pure function; debugging is via reproducing the input aggregate. (Same rationale as Story 3.2's `determine_briefing_state` — no log noise on a hot pure path.)
    - `RichSkinAdapter.render_briefing_card` does NOT log. The render is observable via the terminal output itself; logging would be either redundant (DEBUG: "rendering panel") or sensitive (`extra={"seed_text": ...}` would leak the seed). The rule project-context.md:128 "Structured logging, not print debugging" applies to systems; Skin is the print channel, and its operations are not log-channel observables.
    - `nova.app` logs `"ritual system wired"` and `"skin adapter wired"` at INFO during composition, mirroring the existing `"brain adapter wired"` line at line 158.

35. **Mode-name opacity** — applies to log messages and exception messages. `RitualSystem.build_briefing` does not log mode names at any level (it does not log at all). `RichSkinAdapter.render_briefing_card` writes mode names to the terminal — that is the point of rendering. The opacity rule (project-context.md:175) applies to **derived text in audit / log / cloud-prompt channels**, not to user-facing rendering. **Excluded-app contexts** (`is_opaque=True`) are the exception: their `app_name=None` is already enforced by the Eyes capture layer (Story 4.2 — though Story 2.4's setup capture lacks this filter; the `WindowContext.is_opaque` field exists but `app_name` may carry the real name today). Story 3.3's `last_apps` derivation filters `app_name is None` rows out (AC #11) — it does NOT consult `is_opaque`. The contract is: when an opaque window arrives, its identity fields are all `None` (Story 1.9 AC + project-context.md:72 + ux-design-specification.md:811). If a regression upstream lets a non-`None` `app_name` flow through `is_opaque=True`, **that** is the upstream regression — Story 3.3's `app_name is not None` filter is a defense-in-depth check, not the primary boundary.

## Tasks / Subtasks

- [x] **Task 1 — Reshape `BriefingViewModel` to pre-rendered labels** (AC: #1, #2, #3)
  - [x] Edit [`src/nova/systems/ritual/models.py`](../../src/nova/systems/ritual/models.py): remove the four raw-component fields (`seed_text: str | None`, `last_mode: str | None`, `last_duration_seconds: int | None`, `last_apps: tuple[str, ...]`); add the five pre-rendered label fields (`intro_lines: tuple[str, ...]`, `seed_quote: str | None`, `last_session_label: str | None`, `last_apps_label: str | None`, `available_modes_label: str | None`). Reorder fields per AC #1 (group by render role then behavioral metadata). Keep `frozen=True`. Rewrite the class docstring per AC #2 (field-level boundary, progressive-omission rules, deviation from architecture decision 3b).
  - [x] Grep `src/` and `tests/` for any reference to the four removed names — confirmed only docstring mentions in `nova.setup.__main__` and `nova.systems.nerve.briefing` (both informational only, no field access). No runtime hits.
  - [x] Create [`tests/unit/systems/ritual/test_briefing_view_model_shape.py`](../../tests/unit/systems/ritual/test_briefing_view_model_shape.py) — 4 tests: AC #3 field tuple, label-field types, frozen=True invariant, negative regression for removed names. All pass.
  - [x] Run `uv run mypy src/nova/systems/ritual/models.py` — clean.

- [x] **Task 2 — Ship `nova.core.formatting`** (AC: #4, #5, #6)
  - [x] Create [`src/nova/core/formatting.py`](../../src/nova/core/formatting.py) with the module docstring (centralization rule + value-vs-policy split), `format_duration_seconds`, and `__all__`.
  - [x] Create [`tests/unit/core/test_formatting.py`](../../tests/unit/core/test_formatting.py) — 16 tests (12 parametrized canonical cases + 2 negative-input cases + purity + policy-split-documentation). All pass.
  - [x] No new entry in `nova.core.__init__.py` — direct import `from nova.core.formatting import format_duration_seconds` (established pattern).

- [x] **Task 3 — Implement `RitualSystem`** (AC: #7–#14)
  - [x] Create [`src/nova/systems/ritual/system.py`](../../src/nova/systems/ritual/system.py) with module docstring, `RitualSystem` class (`build_briefing` body for State A/B/C + `begin_shutdown` `NotImplementedError`), six private helpers (`_build_seed_quote`, `_build_last_session_label`, `_build_last_apps_label`, `_build_available_modes_label`, `_format_prompt`, `_pick_recent_or_default` + the two `_select_suggested_mode_for_state_*` wrappers), the three locked-copy constants (`_STATE_A_INTRO_LINE_1/2`, `_STATE_B_INTRO_LINE`), and `__all__`.
  - [x] Update [`src/nova/systems/ritual/__init__.py`](../../src/nova/systems/ritual/__init__.py) per AC #14 — re-export `RitualSystem`.
  - [x] Confirmed imports: `nova.core.formatting`, `nova.core.types`, `nova.systems.brain.models`, `nova.systems.eyes.models`, `nova.systems.ritual.models`. Will be locked by Task 8 AST guard.
  - [x] `uv run mypy src/nova/systems/ritual/` clean.

- [x] **Task 4 — Implement `RichSkinAdapter`** (AC: #15–#17)
  - [x] Create [`src/nova/adapters/rich/skin.py`](../../src/nova/adapters/rich/skin.py) with module docstring, `RichSkinAdapter` class, `render_briefing_card` body (state-agnostic — reads ViewModel labels in fixed order, applies fixed style per field, omits when None / empty), spacing model via `_emit` closure tracking block transitions, five `NotImplementedError` stubs for Stories 3.4 / 3.6 / 3.7.
  - [x] Update [`src/nova/adapters/rich/__init__.py`](../../src/nova/adapters/rich/__init__.py) to re-export `RichSkinAdapter`.
  - [x] `uv run mypy src/nova/adapters/rich/` clean.

- [x] **Task 5 — Wire into composition root** (AC: #18, #19, #31)
  - [x] `NovaApp` gains `ritual: RitualPort` + `skin: SkinPort` fields. `create_app` instantiates both inside the existing `try:` block at INFO log level.
  - [x] New imports: `rich.console.Console`, `nova.adapters.rich.RichSkinAdapter`, `nova.ports.ritual.RitualPort`, `nova.ports.skin.SkinPort`, `nova.systems.ritual.RitualSystem`.
  - [x] [`tests/unit/test_composition_root.py`](../../tests/unit/test_composition_root.py) — refactored `test_sqlite_brain_adapter_is_instantiated_inside_create_app` into a shared helper `_assert_class_instantiated_inside_create_app` and added `test_ritual_system_is_instantiated_inside_create_app` + `test_rich_skin_adapter_is_instantiated_inside_create_app`.
  - [x] [`tests/unit/test_app.py`](../../tests/unit/test_app.py) — extended the populated-NovaApp shape test to assert `ritual` + `skin` slots; added `test_create_app_closes_engine_if_ritual_system_init_fails` (AC #31 partial-init regression — monkeypatch `RitualSystem.__init__` to raise, assert engine `close` was awaited).
  - [x] Added `nova.adapters.rich.skin` to `_LOGGER_NAME_DEPTH_ALLOWLIST` per AC #33 (forward-compat — RichSkinAdapter has no logger today, but Stories 3.6/3.7 will likely add one).
  - [x] All 90 app + composition-root tests pass.

- [x] **Task 6 — ViewModel + Suggested-mode + Skin render tests** (AC: #20–#27)
  - [x] [`tests/unit/systems/ritual/test_briefing_view_model.py`](../../tests/unit/systems/ritual/test_briefing_view_model.py) — 14 tests covering state A (2) + state B (3) + state C (8) + helper-direct defensive path (1). All pass.
  - [x] [`tests/unit/systems/ritual/test_suggested_mode.py`](../../tests/unit/systems/ritual/test_suggested_mode.py) — 9 tie-break tests covering rungs a/b/c/d/e + State B vs State C distinction. All pass.
  - [x] [`tests/unit/adapters/rich/test_skin_adapter.py`](../../tests/unit/adapters/rich/test_skin_adapter.py) — 33 render tests: 14 field-by-field (intro/seed/labels/prompt/prose) + 3 state-complete + 1 panel chrome + 3 idempotency / state-agnostic / metadata-orthogonality + 9 tier-orthogonality (3 states × 3 tier pairs) + 3 long-content / Unicode / wrap. All pass.
  - [x] Test layout: no `__init__.py` files in new test directories.

- [x] **Task 7 — Visual parity test** (AC: #20)
  - [x] [`tests/unit/test_briefing_state_a_parity.py`](../../tests/unit/test_briefing_state_a_parity.py) — 2 tests: plain-text parity (core product contract) + ANSI byte-stream parity (styling-regression guard with documented brittleness triage). **Both pass on first run** — the new pipeline produces byte-for-byte identical output to setup's `_render_state_a` for State A, including the ANSI escape sequences.

- [x] **Task 8 — AST isolation guards** (AC: #28, #29)
  - [x] [`tests/unit/systems/ritual/test_ritual_isolation.py`](../../tests/unit/systems/ritual/test_ritual_isolation.py) — 8 tests (4 patterns × 2 modules: ritual.system + ritual.models): forbidden-modules, sqlite3-at-any-scope, no dynamic forbidden imports, positive-shape allowlist. All pass.
  - [x] [`tests/unit/adapters/rich/test_skin_adapter_isolation.py`](../../tests/unit/adapters/rich/test_skin_adapter_isolation.py) — 8 tests: 3 forbidden-import patterns + 1 Rich-submodule pinned-allowlist (only `rich.console` / `rich.panel` / `rich.text`) + 4 parametrized "models import is present" positive locks. All pass.

- [x] **Task 9 — Full CI gate**
  - [x] `uv run ruff check src/ tests/` → All checks passed.
  - [x] `uv run ruff format --check src/ tests/` → 116 files already formatted.
  - [x] `uv run mypy src/ tests/` → Success — no issues found in 116 source files (strict mode).
  - [x] `uv run pytest tests/unit/` → **1422 passed + 1 skipped** in ~13s. Net delta: +100 tests over the 1322 baseline.
  - [x] `uv run pytest tests/integration/ --ignore=tests/integration/test_setup_bat.py` → 51 passed in 1.75s (no overlap with Story 3.3 surface).
  - [x] **100.0% coverage** on every Story 3.3 module: 137 stmts, 48 branches, 0 misses across `nova.systems.ritual.system`, `nova.systems.ritual.models`, `nova.adapters.rich.skin`, `nova.core.formatting`, and the two new `__init__.py` re-exports.

### Review Findings

**Code review run 2026-05-04** — Three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor). Independence caveat: Blind Hunter pass was performed by the same model (Opus 4.7) that implemented the story; Edge Case Hunter and Acceptance Auditor ran in fresh agent contexts (no implementation memory). 62 raw findings, 22 unique post-dedup-and-classification.

#### Decision-needed findings (resolved)

- [x] [Review][Decision] State A defensively zero-outs `available_modes` — silent override masks Nerve regressions [src/nova/systems/ritual/system.py] — **Resolved (option 3): `logger.warning("FIRST_RUN with non-empty aggregate", extra={...})`** in `RitualSystem.build_briefing`. Surfaces the upstream contract violation without crashing the render. Locked by `test_state_a_warns_and_overrides_non_empty_aggregate` + `test_state_a_does_not_warn_for_clean_empty_aggregate`.
- [x] [Review][Decision] ANSI parity test is brittle-by-design [tests/unit/test_briefing_state_a_parity.py] — **Resolved (option 2): gated behind `@pytest.mark.brittle`**. Marker registered in `pyproject.toml` with `addopts = ["--strict-markers", "-m", "not brittle"]` so default `pytest` runs deselect. Opt in with `pytest -m brittle` after a Rich/terminal-stack change. Plain-text parity test stays in the default suite as the ironclad product-contract guard. As a side effect of the P2 patch (Text-based title), `setup._render_state_a` was symmetrically updated to use `Text("N.O.V.A.", style="bold cyan")` — the ANSI parity test passes byte-for-byte under `-m brittle`.
- [x] [Review][Decision] Comma in `display_name` produces ambiguous label output [src/nova/systems/ritual/system.py] — **Resolved (option 3): `_escape_label_value` helper** backslash-escapes commas (and pre-escapes existing backslashes) in `_build_last_session_label`, `_build_available_modes_label`, and `_build_last_apps_label`. `"Coding, Deep"` renders as `"Coding\, Deep"`. The prompt text stays unescaped because `_format_prompt` uses `replace` (P10), not a join. Locked by three new tests.

#### Patch findings (all resolved)

- [x] [Review][Patch] State-machine literal hardcode + missing exhaustiveness check [src/nova/systems/ritual/system.py] — `state=state` (not literal), explicit `if state is BriefingState.WARM_RESUME:` arm, final `raise ValueError(f"Unhandled BriefingState: {state!r}")`. Locked by `test_build_briefing_raises_for_unknown_state`.
- [x] [Review][Patch] Title markup-injection latent vector [src/nova/adapters/rich/skin.py] — `Panel(..., title=Text(view_model.title, style="bold cyan"), ...)` (no markup-string concat). `setup._render_state_a` updated symmetrically.
- [x] [Review][Patch] `_build_seed_quote` doesn't escape embedded `"` characters [src/nova/systems/ritual/system.py] — `normalized.replace('"', '\\"')`. Locked by `test_state_c_seed_with_embedded_quotes_escapes_them`.
- [x] [Review][Patch] Negative `duration_seconds` propagates `ValueError` [src/nova/systems/ritual/system.py] — `safe_duration_seconds = max(0, last_session.duration_seconds)`. Locked by `test_state_c_with_negative_duration_clamps_to_zero`.
- [x] [Review][Patch] `is_complete is False` identity check brittle [src/nova/systems/ritual/system.py] — `if not last_session.is_complete:` (truthy form). Locked by `test_state_c_with_int_zero_is_complete_treated_as_falsy`.
- [x] [Review][Patch] Empty-string `display_name` produces double-space label [src/nova/systems/ritual/system.py] — `display_name.strip()` + falsy short-circuit in both `_build_last_session_label` and `_build_available_modes_label`. Locked by `test_state_c_with_empty_display_name_omits_last_session` + `test_state_b_with_empty_display_name_filters_from_label`.
- [x] [Review][Patch] Redundant `is not None` filter inside `max()` [src/nova/systems/ritual/system.py] — restored as load-bearing for mypy's type narrowing inside the generator scope; documented in code comment. (No fix possible without losing strict-mode type safety.)
- [x] [Review][Patch] AC #26 — six per-field render tests dropped ANSI style-marker assertions [tests/unit/adapters/rich/test_skin_adapter.py] — added `_render_with_styles` helper + ANSI marker constants, restored spec-named tests with style assertions: `test_intro_lines_render_in_bright_white`, `test_seed_quote_renders_bold_bright_white`, `test_last_session_label_renders_dim`, `test_last_apps_label_renders_dim`, `test_available_modes_label_renders_body_white`, `test_prompt_text_renders_bold_bright_white_at_panel_end`.
- [x] [Review][Patch] AC #26 — `test_panel_chrome_renders_with_cyan_border` was too weak [tests/unit/adapters/rich/test_skin_adapter.py] — renamed to `test_panel_chrome_is_cyan_with_padding`; now verifies (a) cyan SGR on a border line, (b) bold-cyan SGR around the title, (c) ≥2-space horizontal padding.
- [x] [Review][Patch] `_format_prompt` interprets `{` in `display_name` as format spec [src/nova/systems/ritual/system.py] — `template.replace("{}", mode.display_name)` (no `.format()`). Locked by `test_state_c_with_curly_braces_in_display_name_renders_safely`.
- [x] [Review][Patch] `_build_seed_quote` whitespace + newline handling [src/nova/systems/ritual/system.py] — `" ".join(last_seed.split())` collapses whitespace + reject empty after strip. Locked by `test_state_c_seed_with_pure_whitespace_omits` + `test_state_c_seed_with_embedded_newlines_collapses_to_spaces`.
- [x] [Review][Patch] `_select_suggested_mode_for_state_c` rung-a truthy check [src/nova/systems/ritual/system.py] — `if last_session is not None and last_session.mode_name:` (truthy).
- [x] [Review][Patch] `_build_last_apps_label` truthy filter [src/nova/systems/ritual/system.py] — `if w.app_name` (drops both `None` and empty string).
- [x] [Review][Patch] `intro_lines` blank-string entries [src/nova/adapters/rich/skin.py] — `if line:` filter inside the loop. Locked by `test_renderer_skips_empty_string_intro_lines`.
- [x] [Review][Patch] `format_duration_seconds` accepts `bool` [src/nova/core/formatting.py] — `isinstance(seconds, bool)` rejection raising `TypeError`. Locked by parametrized `test_format_duration_seconds_rejects_bool`.
- [x] [Review][Patch] `test_seed_quote_none_omits_line` over-broad [tests/unit/adapters/rich/test_skin_adapter.py] — tightened to scan body lines for the `^".*"$` shape rather than asserting `'"' not in output`.
- [x] [Review][Patch] Refactored test docstring drift [tests/unit/test_composition_root.py] — `test_sqlite_brain_adapter_is_instantiated_inside_create_app` docstring trimmed to one line.
- [x] [Review][Patch] Partial-init regression test only `RuntimeError` [tests/unit/test_app.py] — parametrized over `(RuntimeError, KeyboardInterrupt)` to lock `except BaseException` semantics.
- [x] [Review][Patch] `test_state_a_ignores_aggregate_modes` rename [tests/unit/systems/ritual/test_briefing_view_model.py] — renamed to `test_state_a_warns_and_overrides_non_empty_aggregate` and combined with the D1 logger.warning assertion.

#### Deferred findings (logged separately — see `deferred-work.md`)

- [x] [Review][Defer] Lexicographic ISO timestamp comparison breaks across non-UTC offsets [src/nova/systems/ritual/system.py:634-639] — Story 3.1 locks all writes to `+00:00`; T2 multi-machine sync would surface this.
- [x] [Review][Defer] Lexicographic ISO comparison breaks across millisecond/second precision [src/nova/systems/ritual/system.py:634-639] — codebase emits no fractional seconds today; revisit if precision changes.
- [x] [Review][Defer] Comma in `WindowContext.app_name` produces ambiguous "Apps:" list [src/nova/systems/ritual/system.py:135] — upstream Eyes-layer (Story 4.2) will own normalization.
- [x] [Review][Defer] `\n` in `display_name` breaks block-spacing [src/nova/systems/ritual/system.py:163] — config-load validation should reject (whichever story tightens `nova.core.config` mode-name validation).
- [x] [Review][Defer] Corrupt session row with `duration_seconds=0` indistinguishable from real 0s session [src/nova/systems/ritual/system.py:109 + brain.py:161-163] — Story 5.5 (corruption recovery) owns this distinguishing.
- [x] [Review][Defer] No regression test for "duplicate apps render verbatim" current behavior [src/nova/systems/ritual/system.py:135] — dedup itself is deferred; revisit when first consumer needs it.
- [x] [Review][Defer] Degenerate State C with empty `available_modes` and populated seed [src/nova/systems/ritual/system.py:104] — UX edge case; user sees "What mode?" with no breadcrumb explaining why.

#### Dismissed (12)

Per-spec ladder ordering (rung b before rung c is correct), test-helper hardcodes (acknowledged brittleness), forward-compat allowlist entries, mypy-guarded type narrowings, over-implementation that doesn't violate spec, and stylistic bikesheds. Detail in code-review session log; not flagged here as actionable.

## Dev Notes

### Pattern library consulted

- **#2 AST guards** — two new isolation tests (`test_ritual_isolation.py`, `test_skin_adapter_isolation.py`) plus two new positive composition-root instantiation tests.
- **#3 frozen dataclass** — `BriefingViewModel` reshape stays `frozen=True`. `RitualSystem` is a stateless class (zero fields); `RichSkinAdapter` is a class with a single `Console` reference (dataclass would not gain anything; the adapter has no mutable state requiring frozen-dataclass protection).

### Pattern NOT consulted (and why)

- **#1 clock indirection** — pure transformation; no timestamp stamping in this story.
- **#4 error translation** — encoding errors at the terminal boundary belong at the entrypoint (Story 3.5 / `cli.py`), not at the renderer.
- **#5 per-file skip-on-error** — no file loading.
- **#6 transaction CM** — no DB writes.
- **#7 partial-init cleanup** — both new resources (`RitualSystem`, `RichSkinAdapter`) are stateless with no external handles; the existing `try: except BaseException: await storage.close()` block in `create_app` already covers any future failure during their construction. AC #31 adds a regression test for this.

### Why pre-rendered labels and not raw component fields

Architecture.md Decision 3b's original ViewModel design used raw component fields (`seed_text`, `last_mode`, `last_duration: timedelta`, `last_apps: list[str]`) on the assumption that Skin would compose rendered lines from them. Story 3.3 deviates: every visible string is pre-rendered by Ritual, and Skin maps each to a fixed style. Three reasons:

1. **"Skin makes zero content decisions" is the load-bearing rule.** project-context.md:64 ("Voice generates text; Skin renders it") + project-context.md (Skin renders what it receives) + architecture.md:753 (Ritual owns "assembling the view model, populating UI fields") all push the boundary toward Ritual. Composing `f"Last session: {last_mode} mode, {last_duration_display}"` in Skin is content composition (chooses the prefix label, decides comma placement, decides "mode" suffix). With the reshape, Ritual produces the literal string `"Last session: Coding mode, 1h 42m"` and Skin only chooses the `dim` style.

2. **The pre-flag's serialization-at-boundary invariant generalizes.** The pre-flag (epic-3-story-preflags.md:30) called out duration specifically: no `timedelta` crossing layers; pre-formatted string only. The same logic applies to every other component value — singular/plural mode count, mode-name labeling, opaque-window filtering. The reshape applies the invariant uniformly: the boundary is `str | None`, never raw component data.

3. **Centralized formatting (project-context.md:57).** The new `nova.core.formatting.format_duration_seconds` is the single home for duration formatting. Future consumers (transparency display in Epic 5, mode-restore feedback in Story 3.6, audit-trail render in Epic 5) call the same function. By placing the formatter call in Ritual (not Skin), every consumer that builds a ViewModel-like structure goes through the same vocabulary.

**Trade-off accepted:** the ViewModel grew from 12 to 13 fields, and the field semantics shifted from "data" to "rendered text." Both are conscious choices documented in AC #1 and § Architecture Decision 3b vs. shipped ViewModel.

### Why `RitualSystem` is not a dataclass

Dataclasses are for value objects. `RitualSystem` is a stateless service — it has methods but no fields. A `@dataclass(frozen=True)` declaration with zero fields would compile but give no benefit (no `__init__` parameters to enforce, no `__eq__` semantics that matter — every instance is interchangeable). A plain class is the right shape; any future state (e.g., a Voice port reference for Epic 7) gets added to `__init__` deliberately, with the dataclass decision re-evaluated then.

### Why `RichSkinAdapter` is not a dataclass

Same reason scaled up. The adapter holds one mutable reference (the `Console` — Rich's `Console` is a stateful sink, not an immutable value), so `frozen=True` would be wrong. A plain class with `__init__(self, console: Console)` is the right shape; matches the existing `NoOpShieldAdapter` precedent (Story 1.9 stateless adapter as plain class) and the `SqliteBrainAdapter` precedent (Story 3.1 stateful adapter as plain class with `__init__(self, storage)`).

### Why `last_apps_label` is derived from `WorkspaceSnapshot.windows`, not pre-stored

Story 2.4 / Story 3.1 chose `WorkspaceSnapshot.windows: tuple[WindowContext, ...]` as the canonical shape — each window carries `app_name`, `window_title`, `process_name`, `is_opaque`. The "Apps:" line in the Briefing Card needs only the app-name list (filtered for non-None and joined by `", "`). Two design options were considered:

- **(A)** Add a `last_apps: tuple[str, ...]` field to `BriefingAggregate`, computed by Brain in `load_briefing_aggregate`.
- **(B)** Build `last_apps_label: str | None` in Ritual from `last_snapshot.windows`.

(B) is what Story 3.3 ships, because:
- Brain owns the **storage projection**, not the **render projection**. Adding a render-shaped field to `BriefingAggregate` mixes concerns and adds work to Brain that is purely Ritual's concern.
- The transformation is two lines (filter `app_name is not None`, then `f"Apps: {', '.join(...)}"`). Caching it in `BriefingAggregate` would not save meaningful work at T1 scale.
- A future story that needs *both* the windows tuple (for richer rendering — e.g., focus indicators in Epic 6) and the apps label (for the briefing card) can let each consumer derive what it wants. Storing both is duplication.

**Note:** Skin never sees `WindowContext`. The cross-system surface is Ritual ← Eyes (`WorkspaceSnapshot` + `WindowContext` from `nova.systems.eyes.models` per Story 1.9 AC #8) → Skin (`last_apps_label: str | None` only).

### Why setup's `_render_state_a` is not deleted

The pre-flag note says "scaffolding that this story replaces" — read pragmatically as "replaces in the bare-`nova` boot path" (which is what Story 3.5 wires). Setup runs *before* a `NovaApp` exists; rerouting setup's State A through Ritual+Skin would require either:
- (a) Constructing an empty `BriefingAggregate` and instantiating a `RitualSystem` purely to call `build_briefing(empty_aggregate, FIRST_RUN, OFFLINE)` — then a `RichSkinAdapter(console)` — wrapped in `asyncio.run` from setup's sync `main`.
- (b) Inlining a State A ViewModel construction in setup and calling `RichSkinAdapter(console).render_briefing_card(view_model)` (still wrapped in `asyncio.run`).

Both add overhead. The visual-parity test (AC #20) is the contract that catches divergence — if the two renderers ever produce different output, the test fails on the next CI run. Setup keeps its own renderer until a future cleanup decides the consolidation is worth one more `asyncio.run` boundary.

### State C with the setup row is the **first-session-2 card**, not "no resumable context"

Story 3.2 § "State B is NOT the normal post-setup path in T1" is mandatory reading. Story 3.3's render must NOT label State B as "the post-setup briefing" — that mislabel would make the user-visible UX claim "session 2 lands on State B," which is wrong. The first-session-2 card is **State C with progressive omission** (no seed line, no last-session line, no apps line — only the panel chrome and the resume prompt resolved via the suggested-mode ladder). Test naming reflects this: `test_state_c_with_setup_row_only_omits_progressively` is the name of the regression; the State B test names emphasize "no available data + seed null" or "interrupted session" (the actual State B paths in T1).

The user-visible result for a fresh session 2 (post-setup, no completed work session yet, no seed) is a Briefing Card with the cyan border, "Session Briefing" title, and ONE bold line: `"Resume {default_mode} mode?"` (or `"What mode?"` if no default — but Story 2.3's wizard requires a default). That is the architecture's intended cold-start render, surfaced cleanly via progressive omission. The next session — when the user has actually completed a session and planted a seed — surfaces the full State C card.

### Suggested-mode ladder design rationale

The four-rung ladder (last-session-match → most-recent-used → default → alphabetical) is borrowed from the architecture.md:727 table description ("most recent or default") and elaborated to handle ties deterministically. The architecture phrases it as a heuristic; Story 3.3 elevates it to a deterministic ladder so the test can assert specific outputs. The first-match-wins discipline mirrors Nerve's `determine_briefing_state` ladder (Story 3.2) — same idiom, similar style.

The "alphabetically-first stem" tie-break is **stable** under YAML edits — renaming a mode YAML's `name:` field changes `display_name` but not `stem` (the filename). Tests force `stem != display_name` to lock the contract that the alphabetical sort is on `stem`, not on `display_name`.

### Render-time omission rule: empty string vs. None

The reshape moves the truthy-vs-`is not None` distinction from the renderer to Ritual's helpers (where the empty-string-vs-None decision is now made), but the rule is the same:

- For `seed_quote`, the **producer** (`_build_seed_quote` in Ritual) uses a truthy check (`if last_seed:`) — a legitimate seed cannot be the empty string (Story 3.7's shutdown flow rejects empty input — "Please confirm or cancel" — so empty seed never reaches storage). Empty-string-as-input from a corrupted DB row produces `seed_quote = None`, which Skin then omits via `is not None`. Locked by AC #24's `test_state_c_with_empty_seed_string_omits_seed_quote`.

- For all other label fields (`last_session_label`, `last_apps_label`, `available_modes_label`, `prompt_text`, `prose_enrichment`) and the empty-tuple `intro_lines == ()`, the omission signal is `None` / `()`. These fields are NEVER the empty string — Ritual either produces a non-empty label or returns `None` directly. Skin's omission check is uniformly `is not None` for `str | None` fields and `if value` for the tuple field. No category errors: absent (None / empty tuple) is one signal; present (non-empty string / non-empty tuple) is the other.

### Coexistence-without-divergence is testable

The pre-flag note's "two renderers must coexist without divergence in visible output" is enforced by AC #20's parity test (plain text + ANSI). Both are `Console(record=True, width=80, color_system=...)` captures, compared byte-for-byte. A regression that changes either renderer's State A output (e.g., setup adding a "v0.1" tagline in a future polish pass, or RichSkinAdapter shifting from `padding=(1, 2)` to `padding=(2, 2)`) flips the test red on the next CI run. The test is the spec.

If Story 3.5's bare-`nova` boot wiring chooses to merge the two renderers (e.g., reroute setup's State A through `RichSkinAdapter` once the asyncio plumbing is in place), the parity test is the migration's safety net — both paths already produce identical output, so the merge is pure deduplication.

### `tier` rides through the ViewModel but does nothing in Epic 3 render

The `BriefingViewModel.tier` field exists for Story 5.4's tier-notice rendering (separate Skin method, not `render_briefing_card`) and Epic 7's prose-enrichment-availability decision (Voice consults tier before generating prose). Story 3.3's `render_briefing_card` ignores the field — every test parametrizes `(state, tier)` and asserts byte-identical output across tier values. If a future story decides State B / C should carry an inline degraded notice (instead of a separate amber line above the panel), that story changes the rule and updates these parametrize tests; until then, tier-orthogonality is the contract.

### Why `NotImplementedError` and not `pass` for the unfilled `RichSkinAdapter` methods

`pass` would silently no-op the method. `parse_command("shutdown")` returning `None` (since the body is `pass`) would be a bug Story 3.4 would have to discover via crashing tests. `NotImplementedError("Story 3.X scope")` makes the seam loud — the first call site of any unimplemented method (in Story 3.4 / 3.6 / 3.7) hits a clear exception that names the responsible story. Same precedent as the Story 3.1 Brain Epic-5 stubs (`raise NotImplementedError("Epic 5 scope")`).

### Explicit scope fence (non-goals)

- Story 3.3 does NOT call the new pipeline from `nova.cli.main` — Story 3.5 owns that wiring (Nerve session lifecycle) and the bare-`nova` entrypoint.
- Story 3.3 does NOT modify [`src/nova/setup/__main__.py`](../../src/nova/setup/__main__.py). Setup's `_render_state_a` keeps rendering State A directly via Rich Panel construction; the parity test enforces visual equivalence.
- Story 3.3 does NOT generate `prose_enrichment`. Voice + Claude integration is Epic 7. The field is `None` in every Epic 3 ViewModel.
- Story 3.3 does NOT implement the tier-notice render path (the separate amber line above the panel for `DEGRADED` / `OFFLINE`). That is Story 5.4 (tier status display + notification). Skin's `render_briefing_card` is tier-orthogonal.
- Story 3.3 does NOT implement `parse_command`, `render_progress`, `render_shutdown_card`, `render_response`, `collect_input`. Each raises `NotImplementedError` with the target story number; the bodies land in Stories 3.4 / 3.6 / 3.7.
- Story 3.3 does NOT implement `RitualSystem.begin_shutdown`. It raises `NotImplementedError("Story 3.7 scope")`.
- Story 3.3 does NOT add a `WorkspaceSnapshot.apps` derived field. The derivation lives in `RitualSystem.build_briefing` per the design rationale above.
- Story 3.3 does NOT centralize Rich style strings into a `nova.core.styles` module. The literal style strings appear at the Skin renderer call sites; centralization is YAGNI until a second consumer exists.
- Story 3.3 does NOT de-duplicate repeated app names when building `last_apps_label`. T1 scale makes the duplication visible-but-tolerable (`"Apps: Chrome, Chrome"` is accurate, just verbose); the first consumer that needs deduplication owns it.
- Story 3.3 does NOT change `BriefingAggregate` shape. Story 3.2's reshape is final; Ritual reads through.
- Story 3.3 does NOT introduce a settings flag for "show prose enrichment when available." The `prose_enrichment != None` rendering branch is tested with a fixture-supplied value; the production wiring of Voice → ViewModel is Epic 7's concern.
- Story 3.3 does NOT add timing instrumentation for the < 5s briefing NFR (PRD NFR3). Story 3.5 (Nerve session lifecycle) is the natural home for end-to-end timing — it owns the bare-`nova` boot path that the NFR measures.

### Open questions resolved during SM authoring

1. **Should setup's `_render_state_a` route through the new pipeline?** — NO. The pre-flag's "two renderers must coexist without divergence" is satisfied by the parity test. Setup runs before a NovaApp exists; rerouting adds asyncio overhead with no architectural benefit. See § Why setup's `_render_state_a` is not deleted.

2. **Should the ViewModel reshape be a single `last_duration_display` rename or a broader move to pre-rendered labels?** — Broader. Initial draft had only the duration rename per the pre-flag. Code-review feedback identified that as inconsistent: Skin would still own the State B preface text, the singular-vs-plural "Available mode(s)" decision, the seed-quote wrapping, and the "Last session: {mode} mode, {duration}" composition — all content decisions. The reshape applies the pre-flag's serialization-at-boundary invariant uniformly: every field crossing Ritual → Skin is either pre-rendered text or a behavioral-metadata signal (state, tier). See § Why pre-rendered labels and not raw component fields.

3. **`suggested_mode: ModeInfo | None` or `str | None`?** — `ModeInfo | None`. The current model already says `ModeInfo | None`; the AC says `suggested_mode` (no type) and the architecture.md table says `str | None` ("most recent or default" — implies a name string). The richer `ModeInfo` form is what's already shipped (Story 1.9) and what tests across this story expect. Skin formats `prompt_text` from `suggested_mode.display_name`; if Story 3.3 collapsed to `str`, Ritual would need to embed the display_name into prompt_text and Skin would lose access to the full mode metadata — useful for Story 5.4's tier display and Epic 7's prose. Keep the `ModeInfo` form.

4. **Where does `format_duration_seconds` live?** — `nova.core.formatting` (new module). project-context.md:57 dictates centralization; Story 3.3 introduces the first centralized formatter. Future formatters (datetime, mode-name normalization) join the same module.

5. **Should the renderer use Rich's markup string syntax or `rich.text.Text` builder?** — Either; the AC #17 narrative uses the markup form for compactness ("[bold cyan]…[/bold cyan]") but the implementer may use `Text.append(..., style="bold cyan")` if it reads more clearly. Tests assert structural content (substring presence + line ordering) and ANSI byte equality (parity test) — they do not constrain the construction style.

6. **Does the `RitualSystem` constructor need a `Voice` reference for prose enrichment?** — Not in T1. Epic 7's wiring will add it; Story 3.3's `RitualSystem` is parameterless. When Epic 7 lands, `RitualSystem.__init__(self, voice: VoicePort)` — at that point the composition root passes the `voice` adapter through. Forward-compatible because `prose_enrichment=None` already renders cleanly today.

7. **Should `render_briefing_card` clear the screen before printing?** — NO. The setup's `_render_state_a` does not clear; the bare-`nova` boot will not clear; the user's terminal scrollback is preserved. Story 3.5's command loop manages the prompt position; the briefing card does not own its own clear behavior.

## Review Focus (boundary-first invariant sweep)

Per Epic 1 retrospective action item A1 (extended to interaction boundaries per Epic 2 retro A6). Story 3.3 is an interaction-boundary story; this sweep is mandatory.

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | `RitualSystem` and `RichSkinAdapter` have zero lifecycle beyond construction. No background tasks, no timers, no event-bus subscriptions. Construction happens in `create_app`'s try-block; teardown is the existing `_close()` body which awaits `storage.close()` — neither new resource needs custom teardown. |
| **Teardown under partial failure** | If `RitualSystem()` or `RichSkinAdapter()` constructor raises (today they cannot — both are minimal-state — but a future Voice wiring or a failed Console acquisition could), the existing `except BaseException: await storage.close()` block at [`src/nova/app.py:207-216`](../../src/nova/app.py#L207-L216) covers the cleanup. AC #31 adds a positive test that monkeypatches `RitualSystem.__init__` to raise and asserts `storage.close()` was awaited. |
| **Concurrency model** | `RitualSystem.build_briefing` is `async def` (port contract) but the body is fully synchronous in implementation — no awaits, no I/O, no clock. Returning a coroutine is the contract; the engine-of-execution is sequential per call. `RichSkinAdapter.render_briefing_card` is `async def` and `console.print(panel)` is a synchronous Rich call — the adapter does not wrap it in `asyncio.to_thread` because (a) `console.print` is fast and stdout-bound; (b) Rich does not have a thread-affinity contract; (c) wrapping would add overhead with no benefit. The async signature is for port symmetry. |
| **Cancellation** | `asyncio.CancelledError` propagates untouched. Both methods consist of pure sync work after a hypothetical `await` boundary the caller may inject — the concrete implementations have no `try / except` blocks that could swallow cancellation. project-context.md:49 "Never swallow `asyncio.CancelledError`" is satisfied trivially because there are no exception handlers. |
| **Error translation** | No new translations introduced. Encoding errors at the terminal boundary (UnicodeEncodeError, OSError on closed stdout) belong at the entrypoint (Story 3.5 / `cli.py`), not at the renderer. Cross-cutting-patterns.md #4's translation contract applies at boundaries that emit external exceptions; the renderer is upstream of any external system. If a render call fails, the engine-of-execution caller (Story 3.5's Nerve) catches whatever the terminal raised and decides graceful-degradation vs. propagation. Story 3.3's renderer does not re-classify. |
| **Test determinism** | `RitualSystem.build_briefing` is pure → deterministic by construction (same aggregate + state + tier → byte-identical ViewModel). `RichSkinAdapter.render_briefing_card` is deterministic given a fresh Console (the Console is dependency-injected by tests; no global state, no random, no clock). The idempotency test (`test_skin_makes_no_content_decisions`) renders the same ViewModel three times and asserts byte-identical output. |
| **Logging opacity** | DEBUG NEVER. Both methods do not log. The composition root logs INFO at wiring time with closed-set strings (`"ritual system wired"`, `"skin adapter wired"`); no user-data leaks. Mode names CAN appear in `last_apps` and `prompt_text` ViewModel fields — these are written to the terminal (the rendering channel), not to the log channel. Project-context.md:175's opacity rule is about logs / audit / cloud-prompts, not user-facing rendering. Excluded windows (`is_opaque=True`) have `app_name=None` upstream; AC #11's filter is defense-in-depth. |
| **Idempotency** | Both methods are idempotent. `build_briefing` returns a frozen dataclass — re-invocation yields the same instance value. `render_briefing_card` writes to a console — repeated calls write the same bytes; the test locks this. |
| **Atomicity contract** | No multi-statement writes; no DB. Console.print is best-effort (a closed stdout would raise OSError; the entrypoint owns that). No atomicity contract to lock. |
| **Deterministic mode ordering** | Inherited from Story 3.2: `aggregate.available_modes` is stem-ascending. Story 3.3's renderer preserves the order in the State B "Available modes:" line (the `", ".join(m.display_name for m in modes)` call iterates in tuple order). Tie-break ladder uses alphabetical-stem fallback. |
| **State A copy contract** | The two body lines are locked verbatim by Story 2.4 AC #1 (`test_state_a_body_contains_first_session_line`). Story 3.3's parity test (AC #20) extends the lock by asserting byte-identical Console output between the two render sites. Any change to the copy in either site flips the parity test. |
| **Progressive omission contract** | Documented in `BriefingViewModel` docstring and tested via the omission cases in AC #24 (`test_state_c_with_setup_row_only_omits_progressively`, `test_state_c_with_interrupted_session_omits_duration`, `test_state_c_with_completed_session_zero_duration_renders_zero_seconds`, `test_state_c_with_deleted_mode_omits_last_session_label`, `test_state_c_with_opaque_window_filtered_from_apps`, `test_state_c_with_no_snapshot_omits_apps_label`, `test_state_c_with_empty_seed_string_omits_seed_quote`). The contract: Ritual returns `None` for absent / corrupt inputs; Skin omits the line entirely on `None`. No placeholders, no "N/A", no fake history. |
| **Tier orthogonality (Epic 3)** | `tier` rides on the ViewModel but does not affect `render_briefing_card`. Three parametrize tests assert byte-identical output across the three tiers for each state. Future tier-aware rendering (Story 5.4 tier-notice, Epic 7 prose-enrichment-on-FULL-only) is a separate render method or a Voice-side decision; this story's render path stays orthogonal. |
| **Patterns consulted** | #2 AST guards (two new isolation tests + two new positive composition-root tests), #3 frozen dataclass (BriefingViewModel reshape stays frozen). Patterns NOT consulted: #1 clock indirection (no timestamps), #4 error translation (no new translations), #5 skip-on-error (no file loading), #6 transaction (no DB), #7 partial-init cleanup (no new resources require teardown beyond existing storage close). |

## Project Structure Notes

**New source files:**
- `src/nova/core/formatting.py` — `format_duration_seconds`, module docstring, `__all__`.
- `src/nova/systems/ritual/system.py` — `RitualSystem` class, three private helpers, module docstring, `__all__`.
- `src/nova/adapters/rich/skin.py` — `RichSkinAdapter` class, module docstring, `__all__`.

**Modified source files:**
- `src/nova/systems/ritual/models.py` — `BriefingViewModel` field reshape; docstring update.
- `src/nova/systems/ritual/__init__.py` — placeholder docstring replaced with re-export of `RitualSystem`.
- `src/nova/adapters/rich/__init__.py` — placeholder docstring replaced with re-export of `RichSkinAdapter`.
- `src/nova/app.py` — `NovaApp` gains `ritual` and `skin` fields; `create_app` instantiates both; new imports.

**New test files:**
- `tests/unit/core/test_formatting.py` — duration formatter cases.
- `tests/unit/systems/ritual/test_briefing_view_model_shape.py` — Group A regression (field set, types, frozen).
- `tests/unit/systems/ritual/test_briefing_view_model.py` — state A/B/C ViewModel construction tests.
- `tests/unit/systems/ritual/test_suggested_mode.py` — tie-break ladder tests.
- `tests/unit/systems/ritual/test_ritual_isolation.py` — AST guards on `nova.systems.ritual.*`.
- `tests/unit/adapters/rich/test_skin_adapter.py` — Rich render tests using Console capture.
- `tests/unit/adapters/rich/test_skin_adapter_isolation.py` — AST guards on `nova.adapters.rich.skin`.
- `tests/unit/test_briefing_state_a_parity.py` — visual parity with setup's `_render_state_a`.

No `tests/unit/systems/ritual/__init__.py` and no `tests/unit/adapters/rich/__init__.py` — the project does not use `__init__.py` in test directories (Story 3.2 § Task 5 detail; precedent in `tests/unit/core/`, `tests/unit/adapters/sqlite/`, `tests/unit/systems/nerve/`).

**Modified test files:**
- `tests/unit/test_composition_root.py` — two new positive-instantiation tests; `_LOGGER_NAME_DEPTH_ALLOWLIST` gains `nova.adapters.rich.skin`.

**Modified planning / tracking files:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Scrum Master flips `3-3-briefingviewmodel-and-briefing-card-rendering: backlog → ready-for-dev` via the create-story workflow; Dev flips `ready-for-dev → in-progress → review` during implementation; code-review workflow flips `review → done`.

**Line-count discipline.** Approximate target sizes (numbers are guidance, not gates):
- `formatting.py` ≈ 40 lines (one function + module docstring + policy-split note).
- `system.py` (Ritual) ≈ 180 lines (RitualSystem class + 6 helpers (`_build_seed_quote`, `_build_last_session_label`, `_build_last_apps_label`, `_build_available_modes_label`, `_format_prompt`, `_select_suggested_mode_*`) + module docstring + the two locked-copy constants `_STATE_A_INTRO_LINE_*` and `_STATE_B_INTRO_LINE`).
- `skin.py` (Rich adapter) ≈ 90 lines (`render_briefing_card` body ~50 lines — fixed render order, no state branching; the five `NotImplementedError` stubs ~6 lines combined).
- `BriefingViewModel` reshape: ~+5 net lines (4 fields removed, 5 added) + docstring expansion ~20 lines.
- `app.py` additions: 2 new fields, 2 new instantiation lines, 2 new INFO logs, 4 new imports.
- New test files: ~60 tests across 8 files; expect ~750 lines test code total.

Story 3.3 introduces TWO new `nova.systems` modules and ONE new `nova.adapters` subpackage member; this is structural growth beyond Story 3.2's "thin slice" but is appropriate because Story 3.3 is the **first concrete adapter** for two ports (RitualPort, SkinPort) plus the **first centralized formatter**.

### Alignment with unified project structure

- `nova.systems.ritual.system` matches the architecture.md:1350 directory layout (`systems/ritual/system.py — RitualSystem — briefing assembly, shutdown flow, seed`).
- `nova.adapters.rich.skin` matches architecture.md:1377 (`adapters/rich/skin.py — RichSkinAdapter — Panel, Table, Tree, Progress rendering`). Story 3.3 ships only the Panel surface (Briefing Card); Tree (transparency) and Progress (mode restore) land in their epics.
- `nova.core.formatting` does NOT have an explicit architecture.md anchor — the project-context.md:57 centralization rule is the architectural authority. Module placement under `nova.core` follows the precedent of `nova.core.paths` and `nova.core.types` (cross-cutting, system-agnostic helpers).
- `tests/unit/systems/ritual/` and `tests/unit/adapters/rich/` are new test directories — follows Story 3.2's `tests/unit/systems/nerve/` layout pattern.

### Detected conflicts or variances

- **`BriefingViewModel` raw-component fields (Story 1.9, architecture.md Decision 3b) vs. pre-rendered label reshape (Story 3.3)** — Story 1.9 shipped `seed_text`, `last_mode`, `last_duration_seconds`, `last_apps` per architecture.md:689-712. Story 3.3's reshape removes those four and introduces five label fields (`intro_lines`, `seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`). The deviation is documented in AC #1 and § Why pre-rendered labels and not raw component fields. Architecture.md is updated by reference — the AC narrative replaces the field design for this story; no architecture.md file edit is in scope. A future architecture.md revision pass (post-Epic 3 retrospective) would reflect the shipped shape.
- **architecture.md:704 `last_duration: timedelta | None` vs. shipped `last_session_label: str | None`** — supersedes the architecture's `timedelta` (rejected by pre-flag for serialization-at-boundary) AND the intermediate `last_duration_display: str | None` (rejected during code review for keeping content composition in Skin). The shipped field carries the entire `"Last session: Coding mode, 1h 42m"` line as one pre-rendered string.
- **architecture.md:638 `last_used_at: datetime | None` (on ModeInfo) vs. Story 3.2's shipped `last_used_at: str | None`** — already resolved by Story 3.2 with the same rationale (ISO strings at the port boundary, parse to datetime only at render-layer convenience). Story 3.3 inherits this and uses the ISO string directly for the lexicographic-sort tie-break in the suggested-mode ladder; no datetime parse is needed.
- **architecture.md:727 `suggested_mode: str | None` vs. Story 1.9's shipped `suggested_mode: ModeInfo | None`** — Story 3.3 keeps the `ModeInfo` form. Documented in § Open question 3.

## References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.3 ACs (lines 1111–1135), Epic 3 framing (lines 1048–1050)](../planning-artifacts/epics.md#L1111-L1135)
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 3b BriefingViewModel + Field Population by State + State C Fallback Rules (lines 677–745), Render Responsibility Boundary (lines 747–755)](../planning-artifacts/architecture.md#L677-L755)
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Briefing Card State Contract T1 (lines 746–805), color system (lines 392–410), typography (lines 412–431), layout (lines 432–450), Progressive Briefing direction (lines 462–492)](../planning-artifacts/ux-design-specification.md#L746-L805)
- [Source: _bmad-output/planning-artifacts/prd.md — briefing FRs and NFR3 (5-second budget)](../planning-artifacts/prd.md)
- [Source: _bmad-output/project-context.md — Voice generates text Skin renders it (line 64), Ritual owns ceremony logic (line 68), Adapters translate never decide (line 77), Formatting must be centralized (line 57), No mutable module-level runtime state (line 55), Cancellation (line 49), Briefing Card three states (line 188)](../project-context.md)
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first invariant sweep, cross-cutting-patterns origin](epic-1-retro-2026-04-15.md)
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-04-18.md — interaction-boundary classification (A6), degraded-path proof (A9), prior-state assumptions (A10)](epic-2-retro-2026-04-18.md)
- [Source: _bmad-output/implementation-artifacts/epic-3-story-preflags.md — Story 3.3 pre-flag (lines 24–33), the "two renderers must coexist" requirement](epic-3-story-preflags.md#L24-L33)
- [Source: _bmad-output/implementation-artifacts/3-1-brain-session-and-seed-persistence.md — SessionSummary.duration_seconds == 0 interrupted-session convention, WorkspaceSnapshot round-trip contract](3-1-brain-session-and-seed-persistence.md)
- [Source: _bmad-output/implementation-artifacts/3-2-briefingaggregate-and-state-determination.md — BriefingAggregate field invariants, ModeInfo stem/display_name split, State B is NOT the normal post-setup path in T1, _RecordingFakeBrainPort fixture pattern](3-2-briefingaggregate-and-state-determination.md)
- [Source: _bmad-output/implementation-artifacts/2-4-briefing-card-state-a-initial-capture-and-setup-completion.md — Setup _render_state_a body copy, audit_log setup_complete row, fast-path probe](2-4-briefing-card-state-a-initial-capture-and-setup-completion.md)
- [Source: docs/cross-cutting-patterns.md — patterns #2 (AST guards), #3 (frozen dataclass), #7 (partial-init cleanup)](../../docs/cross-cutting-patterns.md)
- [Source: src/nova/core/types.py — BriefingState (lines 44–56), CapabilityTier (lines 31–41)](../../src/nova/core/types.py#L31-L56)
- [Source: src/nova/systems/brain/models.py — BriefingAggregate (lines 205–228), ModeInfo with stem/display_name (lines 118–162), SessionSummary (lines 50–72), WorkspaceSnapshot import](../../src/nova/systems/brain/models.py)
- [Source: src/nova/systems/eyes/models.py — WorkspaceSnapshot.windows: tuple[WindowContext, ...] (lines 42–54), WindowContext.app_name / is_opaque (lines 26–39)](../../src/nova/systems/eyes/models.py)
- [Source: src/nova/systems/ritual/models.py — BriefingViewModel placeholder (Story 1.9), reshape target (lines 26–51); ShutdownData (lines 54–66)](../../src/nova/systems/ritual/models.py)
- [Source: src/nova/systems/nerve/briefing.py — load_briefing_aggregate + determine_briefing_state (Story 3.2)](../../src/nova/systems/nerve/briefing.py)
- [Source: src/nova/ports/ritual.py — RitualPort Protocol (Story 1.9)](../../src/nova/ports/ritual.py)
- [Source: src/nova/ports/skin.py — SkinPort Protocol (Story 1.9)](../../src/nova/ports/skin.py)
- [Source: src/nova/ports/brain.py — BrainPort Protocol (Story 3.1 + 3.2)](../../src/nova/ports/brain.py)
- [Source: src/nova/setup/__main__.py — _render_state_a body (lines 78–104), _force_utf8_stdout (lines 62–75)](../../src/nova/setup/__main__.py#L62-L104)
- [Source: src/nova/app.py — NovaApp dataclass (lines 80–99), create_app construction order + partial-init cleanup (lines 102–216)](../../src/nova/app.py)
- [Source: src/nova/adapters/shield/__init__.py — adapter __init__ re-export pattern (Story 1.10)](../../src/nova/adapters/shield/__init__.py)
- [Source: src/nova/adapters/sqlite/brain.py — concrete adapter pattern + logger naming (`nova.adapters.sqlite.brain` precedent)](../../src/nova/adapters/sqlite/brain.py)
- [Source: tests/unit/test_composition_root.py — adapter-isolation AST guards, positive instantiation pattern (lines 296–327), logger-name allowlist (line 335)](../../tests/unit/test_composition_root.py)
- [Source: tests/unit/systems/nerve/test_briefing_isolation.py — AST guard pattern Story 3.3 mirrors](../../tests/unit/systems/nerve/test_briefing_isolation.py)
- [Source: tests/unit/adapters/sqlite/test_brain_adapter_isolation.py — adapter-isolation AST guard precedent](../../tests/unit/adapters/sqlite/test_brain_adapter_isolation.py)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **`WorkspaceSnapshot.snapshot_type` is `SnapshotType` enum, not `str`.** First fixture pass used `snapshot_type="startup"` and mypy strict caught it on the full-suite run. Updated to `SnapshotType.STARTUP` (Story 1.9 / 2.4 convention). The pre-task fixture-builder grep had missed this because the eyes-models module imports the enum but the test was authored before importing it.
- **`**base_kwargs` patterns trip mypy strict on dataclass construction.** Two render tests originally used `BriefingViewModel(state=X, **base_kwargs)` with `base_kwargs: dict[str, object]`. Mypy could not narrow `object` to each field's declared type and emitted 8 `arg-type` errors. Replaced with explicit `_make_vm_for_state` / `_make_vm_for_metadata` helpers that take typed parameters and call the constructor with named arguments — same test intent, mypy-clean.
- **`__dataclass_params__` is undocumented in mypy stubs.** The shape regression test wanted `BriefingViewModel.__dataclass_params__.frozen` to lock the immutability invariant. mypy strict does not see this attribute on the type stub. Used `getattr(BriefingViewModel, "__dataclass_params__", None)` + an assertion that the result is non-None, satisfying both the runtime check and strict typing.
- **`_pick_recent_or_default` initial draft contained dead code.** First attempt at the rung-b sort wrote a half-finished closure (`_recent_first_key`) while reasoning through the descending-by-timestamp / ascending-by-stem tie-break. Cleaned up to a `max()`-then-`sorted()`-by-stem pattern: pick the maximum `last_used_at` across modes that have one, then alphabetically-first stem within that grouping.
- **Long-seed wrap test had whitespace-fragile assertions.** Rich panel-body wrapping introduces extra padding spaces when a single logical line spans multiple terminal rows — `output.replace("\n", " ")` produced runs of 4+ spaces between wrapped words, so a substring search for `"Lorem ipsum dolor sit amet ..."` failed. Added `_normalize_panel_body` helper that strips border characters per line, joins with single spaces, then collapses any whitespace runs. The helper is also useful for the long-apps-label test.
- **Prompt-position assertion needed border-stripping.** `test_prompt_text_renders_at_panel_end` originally did `endswith("Resume Coding mode?")` on raw lines but Rich emits `│  Resume Coding mode?    │` (border + padding + trailing spaces). Switched to stripping `│ \t` from each line then comparing equality with the prompt text.
- **ANSI byte-stream parity passed on first run.** Pleasantly surprised — the new Ritual+Skin pipeline produces byte-identical ANSI output to setup's direct `_render_state_a` Panel construction. Two reasons: both use identical Panel chrome (`title`, `border_style="cyan"`, `padding=(1, 2)`), and the body Text built by the renderer's `_emit` closure with two `bright_white` lines emits the same Rich segments as setup's hand-built `Text().append(line, style="bright_white")`.

### Completion Notes List

- **Task 1 — `BriefingViewModel` reshape.** Removed 4 raw-component fields (`seed_text`, `last_mode`, `last_duration_seconds`, `last_apps`); added 5 pre-rendered label fields (`intro_lines`, `seed_quote`, `last_session_label`, `last_apps_label`, `available_modes_label`). Field count 12 → 13. Class docstring rewritten with the field-level boundary contract, progressive-omission rules, and the architecture-decision-3b deviation note.
- **Task 2 — `nova.core.formatting`.** New module with `format_duration_seconds(seconds: int) -> str`. Value-based and policy-free: `0 → "0s"`, sub-minute → `"Ns"`, minute band → `"Mm"`, hour band → `"Hh Mm"`, negative → `ValueError`. The interrupted-session policy (Ritual omits the duration tail when `is_complete is False`) explicitly does NOT live in the formatter.
- **Task 3 — `RitualSystem.build_briefing`.** Stateless class with State A/B/C bodies dispatching on `BriefingState`. Six private helpers: `_build_seed_quote`, `_build_last_session_label`, `_build_last_apps_label`, `_build_available_modes_label`, `_format_prompt`, `_pick_recent_or_default` (plus the two `_select_suggested_mode_for_state_*` wrappers). Three module-level locked-copy constants for State A's two intro lines + State B's preface. `begin_shutdown` raises `NotImplementedError("Story 3.7 scope")`.
- **Task 4 — `RichSkinAdapter`.** Stateless adapter with one `Console` reference. `render_briefing_card` is fully state-agnostic: reads ViewModel labels in fixed order, applies fixed Rich styles, omits None / empty-tuple fields, inserts blank lines between visual blocks. The five other SkinPort methods raise `NotImplementedError` with their target story numbers.
- **Task 5 — Composition root wiring.** `NovaApp` gains `ritual: RitualPort` + `skin: SkinPort` fields. `create_app` instantiates both inside the existing partial-init `try:` block. Two new positive AST tests + one new partial-init regression test (monkeypatch `RitualSystem.__init__` to raise, assert engine `close` was awaited). Refactored the existing `test_sqlite_brain_adapter_is_instantiated_inside_create_app` into a shared `_assert_class_instantiated_inside_create_app` helper to keep the new tests DRY.
- **Task 6 — ViewModel + Suggested-mode + Skin render tests.** 14 + 9 + 33 = 56 tests across the three suites.
- **Task 7 — Visual parity.** Two tests (plain-text + ANSI). Plain-text is the core product contract; ANSI is the styling-regression guard with documented brittleness triage. Both pass on first run.
- **Task 8 — AST isolation guards.** 8 + 8 = 16 tests across `nova.systems.ritual` and `nova.adapters.rich.skin`.
- **Task 9 — Full CI gate.** Ruff clean, ruff-format clean, mypy strict clean (116 files), 1422 unit tests pass + 1 pre-existing skip, 51 integration tests pass (excluding the slow `test_setup_bat.py` Windows shell-out suite per Story 3.2 precedent). **100.0% coverage** on all six new/modified source files (137 stmts + 48 branches, 0 misses).

### File List

**New source files:**

- `src/nova/core/formatting.py` — `format_duration_seconds` value-based formatter.
- `src/nova/systems/ritual/system.py` — `RitualSystem` class + 6 private helpers + 3 locked-copy constants.
- `src/nova/adapters/rich/skin.py` — `RichSkinAdapter` class with `render_briefing_card` body + 5 NotImplementedError stubs.

**Modified source files:**

- `src/nova/systems/ritual/models.py` — `BriefingViewModel` reshape (4 raw-component fields → 5 pre-rendered label fields). Module docstring expanded with the design rationale.
- `src/nova/systems/ritual/__init__.py` — placeholder docstring replaced with `RitualSystem` re-export.
- `src/nova/adapters/rich/__init__.py` — placeholder docstring replaced with `RichSkinAdapter` re-export.
- `src/nova/app.py` — `NovaApp.ritual: RitualPort` + `NovaApp.skin: SkinPort` fields added; `create_app` instantiates `RitualSystem()` + `RichSkinAdapter(console=Console())` inside the existing `try:` block; new imports.

**New test files:**

- `tests/unit/core/test_formatting.py` — 16 tests (12 parametrized canonical cases + 2 negative-input + purity + policy-split-documentation).
- `tests/unit/systems/ritual/test_briefing_view_model_shape.py` — 4 tests (field tuple, label-field types, frozen invariant, removed-fields negative regression).
- `tests/unit/systems/ritual/test_briefing_view_model.py` — 14 tests (state A/B/C ViewModel construction + helper-direct defensive path).
- `tests/unit/systems/ritual/test_suggested_mode.py` — 9 tie-break tests covering all five rungs.
- `tests/unit/systems/ritual/test_ritual_isolation.py` — 8 AST guard tests on `nova.systems.ritual.{system,models}`.
- `tests/unit/adapters/rich/test_skin_adapter.py` — 33 render tests (field-by-field + state-complete + panel chrome + idempotency + tier-orthogonality + long-content / Unicode).
- `tests/unit/adapters/rich/test_skin_adapter_isolation.py` — 8 AST guard tests on `nova.adapters.rich.skin`.
- `tests/unit/test_briefing_state_a_parity.py` — 2 visual parity tests (plain-text + ANSI byte-stream) against setup's `_render_state_a`.

**Modified test files:**

- `tests/unit/test_composition_root.py` — refactored brain-adapter positive-instantiation test into `_assert_class_instantiated_inside_create_app` helper; added `test_ritual_system_is_instantiated_inside_create_app` + `test_rich_skin_adapter_is_instantiated_inside_create_app`. Added `nova.adapters.rich.skin` to `_LOGGER_NAME_DEPTH_ALLOWLIST` (forward-compat).
- `tests/unit/test_app.py` — extended `test_create_app_returns_populated_novaapp` to assert `ritual` + `skin` slots; added `test_create_app_closes_engine_if_ritual_system_init_fails` (AC #31 partial-init regression).

**Modified planning / tracking files:**

- `_bmad-output/implementation-artifacts/sprint-status.yaml` — flipped `3-3-briefingviewmodel-and-briefing-card-rendering: in-progress → review`.

### Change Log

| Date | Description |
|---|---|
| 2026-05-04 | Story 3.3 implemented per ready-for-dev spec. 9 tasks complete, all 35 ACs satisfied, 100% coverage on new/modified modules, 1422 unit + 51 integration tests pass. Status: in-progress → review. |
| 2026-05-04 | Code review complete (3-layer adversarial: Blind / Edge Case / Acceptance Auditor). 22 patches applied (3 decision-needed resolved as: warn-on-FIRST_RUN-violation, brittle-marker for ANSI parity, comma-escape in label builders). 7 deferred items appended to deferred-work.md. 12 dismissed as noise. **Net delta: 1438 unit pass + 1 brittle deselected + 1 pre-existing skip + 51 integration pass; 100% coverage maintained (157 stmts + 60 branches, 0 misses).** Status: review → done. |
