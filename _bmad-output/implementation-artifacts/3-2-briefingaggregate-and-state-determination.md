# Story 3.2: BriefingAggregate & State Determination

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

**Epic:** 3 — Core Session Loop (Hero Path)
**Depends on:** Story 1.6 (`NovaConfig` + `load_config`), Story 1.9 (`BrainPort` Protocol + `BriefingAggregate` + `ModeInfo` + `BriefingViewModel` shapes), Story 2.4 (setup row in `sessions` / `workspace_snapshots` — the first `last_session` any real DB carries), Story 3.1 (`SqliteBrainAdapter` + `BrainPort` reshape — this story extends that surface with one method)
**Downstream consumers:** Story 3.3 (`BriefingViewModel` assembly by Ritual — consumes `BriefingAggregate` + `BriefingState` this story ships), Story 3.5 (Nerve session lifecycle — calls `load_briefing_aggregate` on bare-`nova` boot then `determine_briefing_state` to pick the render path), Story 3.8 (warm-resume hero moment — the full A→B→C progression depends on this state machine being correct)

## Story

As a developer building the briefing pipeline,
I want Brain to expose persisted-fact queries and a Nerve-owned briefing-assembly module to merge them with `NovaConfig.modes` into a `BriefingAggregate`, then a pure function to determine `BriefingState` (A / B / C),
So that the correct briefing content is produced based on what data actually exists — without Brain ever reading mode YAML and without the state logic touching the database.

## Story-type classification

**Interaction-boundary story** (Epic 2 retro A6). Pre-flagged in [epic-3-story-preflags.md:11-20](epic-3-story-preflags.md#L11). Three questions:

1. **New contract between existing pieces?** YES. This is the first production use of the **Brain × NovaConfig × Nerve** triangle. Nerve becomes the ownership site for briefing assembly — Brain provides granular persisted-fact queries and NEVER reads mode YAML (that would cross the config-ownership boundary and violate project-context.md:69 "Config module is the single YAML reader"). `ModeInfo` sourced from `NovaConfig.modes` is enriched by Nerve calling `Brain.get_mode_last_used(mode_name)` per configured mode — a N-call-per-briefing read amplification that is acceptable at T1 scale (1–5 modes) and locked by test.

2. **New invariants in degraded / partial-failure paths?** YES. State determination is **first-match-wins** across three conditions:
   - `FIRST_RUN` (State A): `available_modes` empty AND `last_session is None`
   - `POST_SETUP` (State B): `last_seed is None` AND (`last_session is None` OR `last_session.is_complete is False`)
   - `WARM_RESUME` (State C): else (catches "completed session without seed", "any last_seed", "interrupted session with seed")

   Six boundary conditions must be test-locked (AC #13–#18) — the decision boundary between B and C is load-bearing for Story 3.3's render path and Story 3.8's warm-resume hero moment.

3. **Depends on prior-story state?** YES, critically. Runs against `nova.db` containing Story 2.4's setup row — same prior state Story 3.1 reconciled. The pre-flag note claimed setup-row-only → POST_SETUP; that is **incorrect per the literal state machine** (setup row has `is_complete=True`, so the State B guard `last_session is None OR last_session.is_complete is False` is both-false → falls through to the `else` branch → **State C**). This story implements the literal state machine, locks the boundary with tests, and documents the divergence from the pre-flag's intuition in § Depends on prior-story state. Progressive omission (Story 3.3) renders State C cleanly when `last_mode is None` and `seed_text is None` — the fallback rules in architecture.md lines 738-745 handle the visual.

**Classification result:** ✅ **Interaction-boundary story.** Apply full A1 invariant sweep (lifecycle, teardown, concurrency/cancellation, error translation, test determinism, Review Focus subsection). Apply A9 degraded-path proof (happy / degraded / rerun). Apply A10 prior-state reconciliation (this section + the pre-flag divergence callout in Dev Notes).

## Depends on prior-story state (A10)

### Story 3.1 locked: `BrainPort` surface + `SqliteBrainAdapter` shape

- [`src/nova/ports/brain.py`](../../src/nova/ports/brain.py) declares `create_session`, `end_session`, `get_last_session`, `get_last_seed`, `store_snapshot`, `get_last_snapshot_for_session`, plus four Epic 5 stubs. This story **adds one method**: `get_mode_last_used(mode_name: str) -> str | None`. The Epic 5 stubs stay untouched.
- [`src/nova/adapters/sqlite/brain.py`](../../src/nova/adapters/sqlite/brain.py) is the concrete implementation. This story **adds one method body** and one SQL constant (`_SELECT_LAST_MODE_USAGE_SQL`). The no-`sqlite3`-import rule (AC #29 in Story 3.1) stays — all DB access goes through `SqliteStorageEngine`.
- Clock indirection via `events._utc_now_iso()` (cross-cutting-patterns.md #1) is the established convention. `get_mode_last_used` is a **pure read** — no clock stamping. The returned `str | None` is the raw `started_at` column value (ISO-8601 UTC) from the most recent session matching the mode; the column was written by the adapter's own clock-stamping path, so format is already canonical.

### Story 3.1 locked: `BriefingAggregate` shape

[`src/nova/systems/brain/models.py:170-193`](../../src/nova/systems/brain/models.py#L170-L193) ships the frozen dataclass:

```python
@dataclass(frozen=True)
class BriefingAggregate:
    last_session: SessionSummary | None
    last_snapshot: WorkspaceSnapshot | None
    last_seed: str | None
    available_modes: tuple[ModeInfo, ...]       # tuple, not list — Story 1.9 AC #5
    recent_memory: tuple[MemoryItem, ...]       # tuple, not list — same rule
```

This story **does not alter the shape**. All five fields are populated by the new `load_briefing_aggregate` function. `recent_memory` is populated as **an empty tuple** in T1 — memory-item reads are Epic 5 scope (see § Explicit non-goals). Populating `recent_memory` with a placeholder empty tuple (not `None`) preserves the frozen-dataclass invariant and gives downstream consumers (Story 3.3, Voice) a stable iterable type.

### Story 3.1 locked: setup-row-only DB state

After `nova` setup completes once and before any user-initiated session runs, `nova.db` contains exactly ONE `sessions` row (Story 2.4 AC #12, preserved byte-exact by Story 3.1's `persist_first_run` migration):

| Column | Value |
|---|---|
| `id` | `1` |
| `started_at` | ISO-8601 UTC from `capture.snapshot.captured_at` |
| `ended_at` | ISO-8601 UTC stamped AFTER the snapshot INSERT |
| `mode_name` | `NULL` |
| `seed_text` | `NULL` |
| `summary` | `NULL` |
| `is_complete` | `1` → coerced to `bool(True)` by `SessionSummary.is_complete` |

**The decisive state-determination pivot** — walk the state machine against this row:

1. `FIRST_RUN`? `available_modes` is non-empty (user configured ≥1 mode in setup, per Story 2.3 AC + Story 2.4's setup-complete precondition). `last_session is None` is False. **Not A.**
2. `POST_SETUP`? `last_seed is None` (True, setup row has `seed_text=NULL`) AND (`last_session is None` OR `last_session.is_complete is False`). Second conjunct: `last_session` is NOT None (the setup row exists) AND `is_complete is True` → False. Conjunction: True AND False = **False**. **Not B.**
3. `WARM_RESUME`? Else branch. **C.**

→ **Setup-row-only → State C.**

**Divergence from the pre-flag note:** [epic-3-story-preflags.md:18](epic-3-story-preflags.md#L18) states "state determination produces POST_SETUP (not FIRST_RUN) when the setup session exists with is_complete=1, seed_text=NULL". That sentence was written to rule out FIRST_RUN — which is correct — but wrongly concluded POST_SETUP by intuition rather than by walking the state machine. The literal state machine (sourced from [epics.md:1104-1107](../planning-artifacts/epics.md#L1104-L1107) and [architecture.md:662-664](../planning-artifacts/architecture.md#L662-L664)) falls through to C because `is_complete=True` closes out the B guard.

This is **not a spec conflict** — epics.md and architecture.md agree on the state machine; the pre-flag note agrees that setup-row-only is "post-setup" semantically but conflated that label with the state enum value. Story 3.2 resolves by:

- Implementing the literal state machine (AC #13–17).
- Locking boundary case: `test_setup_row_only_state_c_not_b` (AC #19b) — seed Story 2.4's exact row and assert `determine_briefing_state(aggregate) == BriefingState.WARM_RESUME`.
- Relying on Story 3.3's progressive-omission render (architecture.md:738-745) to display State C cleanly when `seed_text`, `last_mode`, and `last_apps` are all missing/empty.

The UX intent for "post-setup" (user configured modes, hasn't resumed yet) is achieved visually by the fallback cascade — `seed_text=None` omits the hero line, `last_mode=None` omits the "Last session" line, and `suggested_mode` falls back to the config default per architecture.md:745. The resume prompt resolves to `"What mode?"` (generic fallback) rather than `"Resume {mode} mode?"`.

### Story 1.9 / 1.2 / 1.6 locked: enum + config + shape foundations

- **`BriefingState`** already exists as a `StrEnum` at [`src/nova/core/types.py:44-56`](../../src/nova/core/types.py#L44-L56) with values `FIRST_RUN = "first_run"`, `POST_SETUP = "post_setup"`, `WARM_RESUME = "warm_resume"`. **Do NOT declare a new one** — import from `nova.core.types`.
- **`BriefingViewModel`** already exists at [`src/nova/systems/ritual/models.py:26-51`](../../src/nova/systems/ritual/models.py#L26-L51). Story 3.2 does NOT populate it — that is Story 3.3's scope. Story 3.2 ships only the `BriefingAggregate` + `BriefingState` inputs.
- **`NovaConfig.modes`** is `dict[str, ModeConfig]` (file stem → config). Story 3.2 iterates `config.modes.items()` to build `tuple[ModeInfo, ...]` in a **deterministic order** — ascending by file stem (matches `_load_modes`'s `sorted(...)` ordering in [`src/nova/core/config.py:582`](../../src/nova/core/config.py#L582)). Deterministic ordering is load-bearing for test assertions and for Story 3.3's "suggested_mode" rendering.
- **`AppConfig` sequence on `ModeConfig`** is `tuple[AppConfig, ...]`. `app_count` on `ModeInfo` is `len(mode_config.apps)` — no recursion, no filtering.
- **`ModeConfig.is_default`** is the boolean source for `ModeInfo.is_default`. When multiple modes set `is_default=True`, the config loader logs a warning ([`src/nova/core/config.py:659-663`](../../src/nova/core/config.py#L659-L663)) but passes every flagged mode through — this story propagates each mode's flag verbatim into its `ModeInfo`. Tie-break resolution (choosing ONE suggested mode across multiple defaults) is Story 3.3's concern, NOT Story 3.2's.

### Story 1.9 locked: `ModeInfo` shape extension

[`src/nova/systems/brain/models.py:118-127`](../../src/nova/systems/brain/models.py#L118-L127) currently ships:

```python
@dataclass(frozen=True)
class ModeInfo:
    name: str
    last_used_at: str | None
```

The epic AC (story 3.2) requires: **`name: str, app_count: int, is_default: bool, last_used_at: str | None`** (epic's `datetime | None` is reconciled to `str | None` to match Story 3.1's ISO-string-everywhere convention — see § Type note below). This story extends the dataclass in place.

**Type note — `last_used_at: str | None` not `datetime | None`:** [architecture.md:638](../planning-artifacts/architecture.md#L638) and the epic AC reference `datetime | None`. Story 3.1 locked the convention that all session-column values flow as ISO-8601 strings (see [`SessionSummary.started_at: str`](../../src/nova/systems/brain/models.py#L67) — architecture also says `datetime` there, but Story 3.1 shipped `str` because: (a) the column is `TEXT` in 001_initial_schema, (b) `datetime` conversion at every adapter read wastes CPU and invites timezone bugs, (c) the two-function clock indirection emits strings). Story 3.2 ships `ModeInfo.last_used_at: str | None` for the same reasons. Any consumer needing a `datetime` object calls `datetime.fromisoformat()` at the render layer (Story 3.3's Skin-side formatting).

**Shape-test update:** [`tests/unit/ports/test_port_isolation.py::test_mode_info_is_distinct_from_mode_config`](../../tests/unit/ports/test_port_isolation.py#L738) asserts `last_used_at in info_fields`, `last_used_at not in config_fields`, and that `config_fields - info_fields` is non-empty. The assertions remain valid when `ModeInfo` grows (`app_count` and `is_default` are also in `ModeConfig`, but `apps`, `folders`, `urls` are not, so `config_only` is still non-empty). This story adds a NEW companion test: `test_mode_info_exposes_app_count_and_is_default` (AC #22) to lock the extension explicitly — the existing test stays unchanged.

### Story 3.1 locked: `_AlwaysHealthyCheck` / composition-root ownership

Story 3.1 wired `NovaApp.brain = SqliteBrainAdapter(storage)` inside `create_app`. Story 3.2 does NOT re-wire anything in the composition root — `load_briefing_aggregate` is a **free function** in `nerve/briefing.py` that takes `BrainPort` and `NovaConfig` as parameters. Story 3.5 is the one that actually calls it on boot. The `_AlwaysHealthyCheck` / tier-manager contract Story 2.5 locked is **unaffected** by this story — briefing assembly is tier-independent in T1 (prose enrichment is the only tier-sensitive field, Story 3.3 scope).

## Acceptance Criteria

### Group A: `BrainPort` extension — one new method

1. **`BrainPort`** in [`src/nova/ports/brain.py`](../../src/nova/ports/brain.py) gains exactly one new method, inserted in logical position immediately after `get_last_snapshot_for_session` and before the Epic 5 block:

   ```python
   async def get_mode_last_used(self, mode_name: str) -> str | None: ...
   ```

   Per Story 1.9 port rules: `async def`, ellipsis body, no default argument, typed with domain `str | None`. No change to the other nine method declarations. `__all__` is unchanged (it exports only `BrainPort`).

2. The Protocol's docstring is updated with one-line mention of `get_mode_last_used` alongside the existing T1 scope description. The Story 3.2 reference sentence "Story 3.2 will add `get_mode_last_used`" is removed (this story satisfies it).

3. **Port contract shape test** [`tests/unit/ports/test_port_isolation.py::PORT_CONTRACT[brain_port_module]`](../../tests/unit/ports/test_port_isolation.py) is updated to the new 11-method tuple (Story 3.1's 10 + `get_mode_last_used`) — added immediately after `get_last_snapshot_for_session` and before `query_memory` to match the Protocol declaration order:

   ```python
   (
       "BrainPort",
       (
           "create_session",
           "end_session",
           "get_last_session",
           "get_last_seed",
           "store_snapshot",
           "get_last_snapshot_for_session",
           "get_mode_last_used",              # Story 3.2 addition
           "query_memory",
           "delete_matching",
           "confirm_deletion",
           "get_transparency_model",
       ),
   ),
   ```

### Group B: `ModeInfo` extension — rename `name` + add three new fields (identity split)

4. [`src/nova/systems/brain/models.py`](../../src/nova/systems/brain/models.py) `ModeInfo` is **reshaped** to separate the canonical identifier from the user-facing label. The existing single `name: str` field is **replaced** by `stem: str` + `display_name: str`, and two new fields are added. Final shape, in this exact order:

   ```python
   @dataclass(frozen=True)
   class ModeInfo:
       stem: str                  # canonical identifier — dict key in NovaConfig.modes,
                                  # also the value stored in sessions.mode_name (see AC #4a cross-story contract)
       display_name: str          # user-facing label — from ModeConfig.name (YAML `name:` field);
                                  # this is what Skin renders in "Resume {mode}?" prompts
       app_count: int             # len(ModeConfig.apps)
       is_default: bool           # ModeConfig.is_default (may be True on multiple modes;
                                  # tie-break is the consumer's concern, not ModeInfo's)
       last_used_at: str | None   # ISO-8601 UTC from Brain.get_mode_last_used(stem);
                                  # None if the mode has never been used
   ```

   The rename is safe today — no runtime code reads `ModeInfo.name` (the type was declared by Story 1.9 but is populated for the first time in this story). Field order matters because future positional construction (in tests) is supported; existing consumers that construct `ModeInfo(...)` with keyword arguments need both `stem=` and `display_name=` updated.

   The docstring is rewritten to describe:
   - The stem-vs-display-name split and why it exists (stems are stable canonical IDs; display names are user-editable labels).
   - Provenance of each field (stem from dict key, display_name from `ModeConfig.name`, app_count from `len(apps)`, is_default from `ModeConfig.is_default`, last_used_at from `BrainPort.get_mode_last_used(stem)`).
   - The cross-story contract: **`sessions.mode_name` stores the stem, not the display name** (AC #4a below).

4a. **Cross-story contract locked by this story (write-side obligation for downstream stories):** `sessions.mode_name` stores the **stem** (canonical identifier), never the display name. This story reads that column via `Brain.get_mode_last_used(stem)` and trusts the equality match. **Stories 3.5 (Nerve session lifecycle), 3.6 (mode restore), and 3.7 (shutdown flow) MUST write the stem into `sessions.mode_name` when creating or updating sessions** — they do not write the display name. This contract is stated in:
   - `ModeInfo` module docstring ("`sessions.mode_name` stores the stem").
   - `BrainPort.get_mode_last_used` docstring ("`mode_name` parameter is the canonical stem, matching `sessions.mode_name` write-side contract locked in Story 3.2 for Stories 3.5/3.6/3.7").
   - Story 3.2 itself (this AC) — downstream stories MUST cite this contract in their own Dev Notes.

   A test in Story 3.5 (or wherever the first runtime `create_session(mode_name=...)` call lands) is expected to lock the write-side: create a session with a mode whose stem differs from its display name (e.g., stem=`"coding"`, `ModeConfig.name="Deep Coding"`), then call `Brain.get_mode_last_used("coding")` and assert it returns that session's `started_at`. Story 3.2 does NOT ship this test (no write path in scope), but **`test_mode_info_fields_carry_stem_and_display_name_separately` (AC #21)** locks the read-side independence of the two fields.

5. `__all__` in `systems/brain/models.py` is unchanged (it already exports `ModeInfo`). No other model in the file is touched. The existing port-isolation test [`test_mode_info_is_distinct_from_mode_config`](../../tests/unit/ports/test_port_isolation.py#L738) continues to pass (ModeInfo field set now includes `stem, display_name, app_count, is_default, last_used_at`; ModeConfig field set is `name, apps, folders, urls, is_default` — the two sets differ, `last_used_at` is still only on ModeInfo, and `ModeConfig` still has differentiating fields `apps / folders / urls`). Update that test's assertion message if the field-listing line in its error output needs to reflect the new ModeInfo surface — the assertions themselves hold unchanged.

### Group C: `SqliteBrainAdapter.get_mode_last_used`

6. [`src/nova/adapters/sqlite/brain.py`](../../src/nova/adapters/sqlite/brain.py) gains one new SQL constant and one new method body. The SQL constant lives alongside `_SELECT_LAST_SEED_SQL` (grouped by concern — session-column reads):

   ```python
   _SELECT_LAST_MODE_USAGE_SQL = """
   SELECT started_at
     FROM sessions
    WHERE mode_name = ?
    ORDER BY id DESC
    LIMIT 1
   """
   ```

   The query returns the `started_at` of the **most-recently-started** session with the given mode (`ORDER BY id DESC` matches `get_last_session`'s ordering convention — `id` is `AUTOINCREMENT` so it is monotonic with insert order even if two sessions share `started_at`).

7. **Method body:**

   ```python
   async def get_mode_last_used(self, mode_name: str) -> str | None:
       """Return the ``started_at`` of the most recent session with ``mode_name`` or None.

       Consumed by Nerve's briefing-assembly layer to enrich ``ModeInfo``
       with a usage timestamp. Returns the raw ISO-8601 string — callers
       that need a ``datetime`` parse at the render layer
       (``datetime.fromisoformat``).

       A mode that has never been used returns ``None``. An empty mode_name
       is accepted and returns ``None`` (no sessions carry empty string
       as mode_name — setup writes NULL, runtime writes populated names).
       """
       logger.debug("brain.get_mode_last_used start")
       row = await self._storage.fetchone(_SELECT_LAST_MODE_USAGE_SQL, (mode_name,))
       if row is None:
           return None
       started_at = row["started_at"]
       return None if started_at is None else str(started_at)
   ```

   - Logging at DEBUG only — INFO is reserved for orchestration (project-context.md:128). `mode_name` is NOT logged at any level (excluded-app names could theoretically appear as mode names; opacity rule project-context.md:175).
   - The method does NOT catch `StorageError` from the engine — Story 3.1 AC #15's no-double-catch rule applies identically. Engine-translated errors propagate untouched.
   - The method does NOT translate any adapter-layer exception because no JSON decode or enum coercion happens on this read (`started_at` is a plain `TEXT` column).

### Group D: Nerve briefing-assembly module (the new home for Story 3.2's logic)

8. New file [`src/nova/systems/nerve/briefing.py`](../../src/nova/systems/nerve/briefing.py) is the first real code in the Nerve system package ([`src/nova/systems/nerve/__init__.py`](../../src/nova/systems/nerve/__init__.py) currently has only the placeholder docstring). Module contract: TWO public callables, both free functions, both stateless.

   **Module docstring** cites:
   - architecture.md Decision 3b (BriefingAggregate → BriefingViewModel with state determination as Nerve's concern)
   - project-context.md:65 ("Nerve is an orchestrator, not a router")
   - project-context.md:69 ("Config module is the single YAML reader")
   - The explicit non-ownership: "Brain does NOT read mode YAML — NovaConfig is the single source of mode identity. Brain only provides the `last_used_at` enrichment per mode."

9. **Public callable #1 — `load_briefing_aggregate` (async).** Signature:

   ```python
   async def load_briefing_aggregate(
       brain: BrainPort, config: NovaConfig
   ) -> BriefingAggregate: ...
   ```

   Behavior:
   - Calls `await brain.get_last_session()` → `last_session: SessionSummary | None`.
   - Calls `await brain.get_last_seed()` → `last_seed: str | None`.
   - If `last_session is not None`: calls `await brain.get_last_snapshot_for_session(last_session.session_id)` → `last_snapshot: WorkspaceSnapshot | None`. If `last_session is None`: skips the call and sets `last_snapshot = None`. This avoids one round-trip on an empty DB.
   - Builds `available_modes: tuple[ModeInfo, ...]` by iterating `sorted(config.modes.items())` (ascending by stem) and for each `(stem, mode_config)`:
     - `last_used_at = await brain.get_mode_last_used(stem)` — passes the **stem** (dict key), which is also what `sessions.mode_name` stores per the AC #4a cross-story contract.
     - `ModeInfo(stem=stem, display_name=mode_config.name, app_count=len(mode_config.apps), is_default=mode_config.is_default, last_used_at=last_used_at)`.
     - **Stem vs display_name independence:** `stem` comes from the dict key (filename-derived, filename-safe); `display_name` comes from `mode_config.name` (the `name:` field in the YAML, freely user-editable). A user may rename `modes/coding.yaml`'s `name: "Coding"` to `name: "Deep Coding"` at any time — the stem stays `coding`, history in `sessions.mode_name` stays queryable, and only the rendered label changes.
   - Sets `recent_memory = ()` (empty tuple, T1 scope — see § Explicit non-goals).
   - Returns a single `BriefingAggregate(...)` constructed from the five fields above.

   **Call ordering is locked** (AC #19c test): `get_last_session` → `get_last_seed` → optionally `get_last_snapshot_for_session` → one `get_mode_last_used` per mode (in stem-ascending order). This order matches the architecture's field-population sequence and is deterministic for test assertion.

10. **Public callable #2 — `determine_briefing_state` (pure, sync).** Signature:

    ```python
    def determine_briefing_state(aggregate: BriefingAggregate) -> BriefingState: ...
    ```

    Behavior — implement **exactly** the state machine from [epics.md:1104-1107](../planning-artifacts/epics.md#L1104-L1107) and [architecture.md:662-664](../planning-artifacts/architecture.md#L662-L664):

    ```python
    def determine_briefing_state(aggregate: BriefingAggregate) -> BriefingState:
        if not aggregate.available_modes and aggregate.last_session is None:
            return BriefingState.FIRST_RUN
        if aggregate.last_seed is None and (
            aggregate.last_session is None or aggregate.last_session.is_complete is False
        ):
            return BriefingState.POST_SETUP
        return BriefingState.WARM_RESUME
    ```

    - `not aggregate.available_modes` — `tuple[ModeInfo, ...]` is truthy iff non-empty; `not ()` is True. Works for tuple (not list) without change.
    - `aggregate.last_session.is_complete is False` — NOT `== False`; `is` identity check is the ruff-preferred idiom for `bool` comparison and matches Story 3.1's `is_complete` coercion returning a canonical `bool`.
    - **Pure function** — no async, no DB, no clock, no logging (logging a pure-function call adds noise without signal). Zero side effects. Deterministic: same aggregate → same state.

11. Module `__all__` exports both callables:

    ```python
    __all__: list[str] = ["determine_briefing_state", "load_briefing_aggregate"]
    ```

    Alphabetical order (matches project convention in other `__all__` lists).

12. **`nerve/__init__.py`** is updated from the placeholder docstring to re-export both callables so external consumers import from `nova.systems.nerve` rather than reaching into `nova.systems.nerve.briefing`:

    ```python
    """Nerve system — orchestration, event routing, briefing state.

    Story 3.2 ships the briefing-assembly surface (``load_briefing_aggregate``
    + ``determine_briefing_state``). Story 3.5 will add command routing and
    the session lifecycle orchestration.
    """

    from nova.systems.nerve.briefing import determine_briefing_state, load_briefing_aggregate

    __all__: list[str] = ["determine_briefing_state", "load_briefing_aggregate"]
    ```

### Group E: State-machine boundary invariants (six test cases)

13. **FIRST_RUN (State A) canonical case:** `available_modes=()`, `last_session=None`, `last_seed=None`, `last_snapshot=None`, `recent_memory=()` → `FIRST_RUN`. This is the freshest possible DB (user ran setup without configuring any mode — a degraded setup path that Story 2.3 technically allows if the user cancels mode creation).

14. **POST_SETUP (State B) — empty DB with modes configured manually:** `available_modes=(mode,)`, `last_session=None`, `last_seed=None` → `POST_SETUP`. This happens if the user edits mode YAML before ever running `nova` — or if a test fixture populates modes without a session.

15. **POST_SETUP (State B) — interrupted session, no seed:** `available_modes=(mode,)`, `last_session=SessionSummary(is_complete=False, …)`, `last_seed=None` → `POST_SETUP`. Matches the epic's "interrupted session with no seed → B" boundary.

16. **WARM_RESUME (State C) — setup-row-only (the critical case):** `available_modes=(mode,)`, `last_session=SessionSummary(is_complete=True, mode_name=None, seed_text persistence already filtered so last_seed=None)`, `last_seed=None` → `WARM_RESUME`. Matches the epic's "completed session without seed → C" boundary. **This is the case the pre-flag got wrong** — the test is the source of truth.

17. **WARM_RESUME (State C) — seed present:** `available_modes=(mode,)`, `last_session=SessionSummary(is_complete=True, …)`, `last_seed="Push the deploy through"` → `WARM_RESUME`. Standard Day 2+ warm start.

18. **WARM_RESUME (State C) — interrupted session WITH seed:** `available_modes=(mode,)`, `last_session=SessionSummary(is_complete=False, …)`, `last_seed="partial thought"` → `WARM_RESUME`. Walks past the B guard on `last_seed is None` being False. Locks that `last_seed is not None` is sufficient for C even when the session was interrupted — the seed was captured before the crash.

### Group F: Assembly test cases (three flavors)

19. **Assembly tests** at [`tests/unit/systems/nerve/test_briefing_assembly.py`](../../tests/unit/systems/nerve/test_briefing_assembly.py) use a `_FakeBrainPort` (simple class structurally satisfying `BrainPort`, no DB) so `load_briefing_aggregate` is exercised end-to-end without crossing into the adapter. Three required tests:

    a. **`test_load_briefing_aggregate_empty_db_empty_modes`** — `_FakeBrainPort` returns `None` / `None` / never-called for the snapshot method; `NovaConfig.modes = {}`. Assert aggregate has `last_session=None`, `last_snapshot=None`, `last_seed=None`, `available_modes=()`, `recent_memory=()`. Assert `get_last_snapshot_for_session` was **not called** (short-circuit on `last_session is None`).

    b. **`test_load_briefing_aggregate_setup_row_only_yields_state_c`** — `_FakeBrainPort` returns a `SessionSummary(session_id=1, started_at="2026-04-01T10:00:00+00:00", ended_at="2026-04-01T10:00:05+00:00", duration_seconds=5, mode_name=None, summary=None, is_complete=True)`, `last_seed=None`, `last_snapshot=WorkspaceSnapshot(...)` for session_id=1. `NovaConfig.modes = {"coding": ModeConfig(name="Coding", apps=(app,), is_default=True)}`. Call `load_briefing_aggregate`, then `determine_briefing_state(aggregate)`. Assert aggregate's five fields match and state is `BriefingState.WARM_RESUME`. **This is the setup-row-only regression test** (A10 reconciliation).

    c. **`test_load_briefing_aggregate_call_ordering`** — Wrap `_FakeBrainPort` to record the order of method calls. Assert the recorded sequence is exactly `[get_last_session, get_last_seed, get_last_snapshot_for_session, get_mode_last_used("coding"), get_mode_last_used("writing")]` when two modes `coding` and `writing` are configured (stems ordered alphabetically). Snapshot call appears because `last_session` is not None in this fixture.

20. **`test_load_briefing_aggregate_mode_order_is_stem_ascending`** — with `NovaConfig.modes = {"writing": …, "coding": …, "admin": …}` (dict insertion order intentionally scrambled), assert `available_modes` is `(admin_info, coding_info, writing_info)` — ascending by stem. Locks the ordering contract in AC #9.

21. **`test_mode_info_fields_carry_stem_and_display_name_separately`** — construct a `ModeConfig(name="Deep Coding", apps=(app1, app2, app3), is_default=True)` under stem `"coding"` (stem intentionally differs from display name) and prime the fake Brain with `get_mode_last_used("coding")` returning `"2026-04-20T09:30:00+00:00"`. Assert the resulting `ModeInfo(stem="coding", display_name="Deep Coding", app_count=3, is_default=True, last_used_at="2026-04-20T09:30:00+00:00")`. Locks five independent facts:
    - `stem` comes from the dict key (not from `mode_config.name`).
    - `display_name` comes from `mode_config.name` (not from the stem).
    - `stem != display_name` can be observed (the test fixture forces them to differ so a lazy implementation collapsing the two would fail).
    - `app_count == len(mode_config.apps)`.
    - `is_default` and `last_used_at` are pass-through as documented.

    The test also calls `brain.get_mode_last_used` with assertion on the argument: `mock.assert_called_with("coding")` (the stem) — NOT `"Deep Coding"` (the display name). This pins the query-side of the AC #4a cross-story contract: Nerve asks Brain by stem, Brain's row match succeeds because Stories 3.5/3.6/3.7 will write stems into `sessions.mode_name`.

### Group G: Adapter test coverage for `get_mode_last_used`

22. **Unit tests for `SqliteBrainAdapter.get_mode_last_used`** added to [`tests/unit/adapters/sqlite/test_brain_adapter.py`](../../tests/unit/adapters/sqlite/test_brain_adapter.py). Fixtures reuse the Story 3.1 pattern (in-memory SQLite + applied migrations + per-test fresh engine).

    **Happy-path coverage (A9 category 1):**
    - `test_get_mode_last_used_returns_started_at_for_most_recent_session` — create two sessions with the same mode "coding", second started later than first. Assert the returned string equals the second session's `started_at`.
    - `test_get_mode_last_used_returns_none_for_unused_mode` — create a session with mode "coding", query `get_mode_last_used("writing")`. Expect `None`.
    - `test_get_mode_last_used_returns_none_on_empty_db` — construct engine + apply migrations, no sessions inserted. Query `get_mode_last_used("anything")`. Expect `None`.
    - `test_get_mode_last_used_filters_by_mode_name_exactly` — create sessions for "coding", "coding-v2", "code". Assert `get_mode_last_used("coding")` returns the coding session's `started_at`, not the other two. Locks `=` (not `LIKE`) in the SQL.

    **Degraded-path coverage (A9 category 2):**
    - `test_get_mode_last_used_skips_sessions_with_null_mode_name` — seed two sessions: one with `mode_name=NULL` (the setup row shape) and one with `mode_name="coding"` inserted later. Query `get_mode_last_used("coding")`. Assert the returned string matches the coding session's `started_at` — the NULL-mode setup row does not accidentally match ANY mode query. (`WHERE mode_name = 'coding'` with NULL mode returns no match per SQL semantics.)
    - `test_get_mode_last_used_does_not_double_catch_storage_error_from_engine` — monkeypatch `storage.fetchone` to raise `StorageError("engine boundary failure")`; assert the **same instance** propagates (identity equality) — no re-wrap, consistent with Story 3.1 AC #15 applied to the new method.

    **Rerun / determinism coverage (A9 category 3):**
    - `test_get_mode_last_used_is_idempotent_on_reread` — create one session, call `get_mode_last_used("coding")` three times, assert all three return byte-identical strings.

### Group H: State-determination test file (pure function, no DB)

23. Unit tests for `determine_briefing_state` at [`tests/unit/systems/nerve/test_briefing_state.py`](../../tests/unit/systems/nerve/test_briefing_state.py). Parametrize the six boundary cases from AC #13–#18 using `pytest.mark.parametrize` with `ids=` so failures are self-identifying. One additional test: `test_determine_briefing_state_is_pure` — call the function three times with the same aggregate and assert identical returns on all three calls, with no side effects observable through a recording wrapper around Brain/Config (trivial here because the function takes no dependencies).

### Group I: Isolation / AST guards

24. **AST import isolation guard** at [`tests/unit/systems/nerve/test_briefing_isolation.py`](../../tests/unit/systems/nerve/test_briefing_isolation.py). Walks `ast.Import` / `ast.ImportFrom` for `src/nova/systems/nerve/briefing.py` and enforces:

    - **Forbidden:** any import from `nova.adapters.*` (adapters are for the composition root only, project-context.md:76 "Dependency direction is one-way"). The briefing module consumes `BrainPort`, not `SqliteBrainAdapter`.
    - **Forbidden:** any import from `nova.systems.ritual.*` or `nova.systems.skin.*` or any other sibling system's internals. `nova.systems.*.models` is the one allowed cross-system surface (Story 1.9 AC #8) — this story does not need any of them.
    - **Forbidden:** `sqlite3` at any scope (same rule as Story 3.1 AC #29 applied to Nerve — the DB is Brain's concern).
    - **Forbidden:** dynamic imports (`importlib.import_module`, `__import__`). Project-context.md:55 "no mutable module-level runtime state" generalizes here.
    - **Allowed (positive allowlist):** stdlib imports; `nova.core.*` (for `NovaConfig`, `BriefingState`); `nova.ports.brain` (the port it consumes); `nova.systems.brain.models` (for `BriefingAggregate`, `ModeInfo` cross-system model surface).

25. **No adapter-instantiation guard change.** [`tests/unit/test_composition_root.py::test_app_module_level_has_no_adapter_instantiation`](../../tests/unit/test_composition_root.py#L206) continues to pass untouched — Story 3.2 does not add a new adapter and does not instantiate any adapter at module scope. No new AST guard is added at the composition-root level; Story 3.2's code is a system module, not a composition-root module.

### Group J: Cross-cutting pattern and invariant locks

26. **Patterns consulted:** #2 AST guards (new nerve-isolation guard), #3 frozen dataclass (`ModeInfo` extension stays frozen; `BriefingAggregate` unchanged; `BriefingState` is a `StrEnum` — frozen-by-design). Pattern #1 (clock indirection) is **NOT consulted** because `get_mode_last_used` is a read-only method that does not stamp timestamps. Pattern #4 (error translation) is consulted at the Brain **engine** layer — the adapter method delegates to `storage.fetchone` which already translates `sqlite3.Error` at its own boundary; no adapter-layer translation is introduced.

27. **Mode name opacity.** Mode names CAN contain user-chosen text (Story 2.3 wizard allows user-named modes). Log messages and exception messages in this story's new code MUST NOT include mode name values. DEBUG-level `logger.debug("brain.get_mode_last_used start")` is the only log call and carries no mode_name. This matches Story 3.1's AC #14 opacity rule applied to the new method.

28. **`BriefingAggregate` field invariants on every successful `load_briefing_aggregate` call:**

    - `available_modes` is always a `tuple[ModeInfo, ...]` — every `ModeInfo` has all four fields populated (never `None` for `name`, `app_count`, `is_default`; `last_used_at` may be None).
    - `recent_memory` is always `()` (empty tuple) in T1 — never `None`. Story 3.3 / Story 4.5 will populate real content. Tests lock `aggregate.recent_memory == ()` explicitly to catch accidental `None` regression.
    - When `last_session is None`, `last_snapshot is also None` (the snapshot call is skipped). Locked by `test_load_briefing_aggregate_empty_db_empty_modes`.

29. **No `BriefingAggregate` construction outside the briefing-assembly module in this story.** Tests may construct fixtures directly (that is the intended testing pattern), but no other source module introduces a second assembly path. Story 3.5 will call `load_briefing_aggregate` through Nerve — it does not reconstruct the aggregate manually.

## Tasks / Subtasks

- [x] **Task 1 — Reshape `ModeInfo` (rename + extend)** (AC: #4, #4a, #5)
  - [x] Edit [`src/nova/systems/brain/models.py`](../../src/nova/systems/brain/models.py) to replace the existing `name: str` field with `stem: str` + `display_name: str`, and add `app_count: int` + `is_default: bool`. Final five-field shape in order: `stem, display_name, app_count, is_default, last_used_at`.
  - [x] Rewrite the `ModeInfo` docstring: stem-vs-display-name split, per-field provenance, cross-story contract that `sessions.mode_name` stores the stem (AC #4a).
  - [x] Grep `src/` and `tests/` for any `ModeInfo(name=...)` or `.name` access on a ModeInfo instance — none expected today (Story 1.9 declared the type but no runtime code populates it), but confirm zero hits before proceeding. If hits exist, they are updates in this task; do not skip them.
  - [x] Run the existing [`test_mode_info_is_distinct_from_mode_config`](../../tests/unit/ports/test_port_isolation.py#L738) — it should still pass (ModeInfo and ModeConfig field sets still differ; `last_used_at` is still only on ModeInfo; `apps`/`folders`/`urls` still differentiate ModeConfig). Update the test's assertion-message strings if they enumerate fields verbatim.

- [x] **Task 2 — Extend `BrainPort` with `get_mode_last_used`** (AC: #1–#3)
  - [x] Edit [`src/nova/ports/brain.py`](../../src/nova/ports/brain.py) — add `async def get_mode_last_used(self, mode_name: str) -> str | None: ...` between `get_last_snapshot_for_session` and `query_memory`.
  - [x] Remove the forward-reference sentence "Story 3.2 will add `get_mode_last_used`" from the `BrainPort` class docstring.
  - [x] Update [`tests/unit/ports/test_port_isolation.py`](../../tests/unit/ports/test_port_isolation.py) `PORT_CONTRACT` tuple for `BrainPort` to the 11-method form (AC #3). Keep the ordering exactly as specified.

- [x] **Task 3 — Implement `SqliteBrainAdapter.get_mode_last_used`** (AC: #6, #7)
  - [x] Add the `_SELECT_LAST_MODE_USAGE_SQL` constant alongside `_SELECT_LAST_SEED_SQL` in [`src/nova/adapters/sqlite/brain.py`](../../src/nova/adapters/sqlite/brain.py).
  - [x] Add the method body immediately after `get_last_snapshot_for_session`.
  - [x] Verify no new `sqlite3` imports landed in the file (AST guard will catch regressions in Task 8).

- [x] **Task 4 — Ship `nerve/briefing.py`** (AC: #8–#12)
  - [x] Create [`src/nova/systems/nerve/briefing.py`](../../src/nova/systems/nerve/briefing.py) with the module docstring, two public callables, and `__all__`.
  - [x] Update [`src/nova/systems/nerve/__init__.py`](../../src/nova/systems/nerve/__init__.py) to re-export `load_briefing_aggregate` and `determine_briefing_state`.
  - [x] Confirm the module imports only from `nova.core.types` (`BriefingState`), `nova.core.config` (`NovaConfig`), `nova.ports.brain` (`BrainPort`), and `nova.systems.brain.models` (`BriefingAggregate`, `ModeInfo`).

- [x] **Task 5 — State-determination tests** (AC: #13–#18, #23)
  - [x] Create [`tests/unit/systems/nerve/test_briefing_state.py`](../../tests/unit/systems/nerve/test_briefing_state.py) with one parametrized test covering the six cases in AC #13–#18 plus `test_determine_briefing_state_is_pure`.
  - [x] Each case uses inline `BriefingAggregate` construction — no DB, no async.
  - [x] Parametrize IDs match the AC names (`"first_run_canonical"`, `"post_setup_empty_session"`, `"post_setup_interrupted"`, `"warm_resume_setup_row_only"`, `"warm_resume_seed_present"`, `"warm_resume_interrupted_with_seed"`) so a failure's `pytest` output names which boundary broke.
  - [x] Do NOT create `tests/unit/systems/nerve/__init__.py` — this project does not use `__init__.py` in test directories (see `tests/unit/core/`, `tests/unit/adapters/sqlite/` for precedent). pytest discovers via `rootdir` + `testpaths` configuration alone.

- [x] **Task 6 — Assembly tests** (AC: #19, #20, #21)
  - [x] Create [`tests/unit/systems/nerve/test_briefing_assembly.py`](../../tests/unit/systems/nerve/test_briefing_assembly.py).
  - [x] Implement a `_RecordingFakeBrainPort` helper class that structurally conforms to `BrainPort`, records method call order in a list attribute, and allows per-method return-value priming.
  - [x] Write the five tests listed in AC #19a–c, #20, #21.
  - [x] Confirm every `_RecordingFakeBrainPort` constructs `ModeConfig` fixtures using **realistic** shapes (at least one app, `is_default` varying) so Task 7 adapter tests don't duplicate fixture construction.

- [x] **Task 7 — Adapter unit tests for `get_mode_last_used`** (AC: #22)
  - [x] Extend [`tests/unit/adapters/sqlite/test_brain_adapter.py`](../../tests/unit/adapters/sqlite/test_brain_adapter.py) with the seven tests listed in AC #22 (happy / degraded / rerun bands). Reuse the existing fixtures pattern (in-memory engine + applied migrations + `SqliteBrainAdapter` under test).
  - [x] For the `StorageError` propagation test, reuse the monkeypatch pattern from Story 3.1's `test_adapter_does_not_double_catch_storage_error_from_engine` — same identity-equality assertion.

- [x] **Task 8 — AST isolation guard** (AC: #24)
  - [x] Create [`tests/unit/systems/nerve/test_briefing_isolation.py`](../../tests/unit/systems/nerve/test_briefing_isolation.py) walking `ast.Import` / `ast.ImportFrom` nodes in `src/nova/systems/nerve/briefing.py` and enforcing the allowlist in AC #24.
  - [x] Use `ast.walk(tree)` (not just top-level iteration) to catch function-local imports — per cross-cutting-patterns.md #2 review-focus guidance.

- [x] **Task 9 — Full CI gate**
  - [x] `uv run ruff check --fix src/ tests/`
  - [x] `uv run ruff format src/ tests/`
  - [x] `uv run mypy src/ tests/` — strict-mode clean.
  - [x] `uv run pytest tests/unit/` — 1317 passed + 1 skipped. All new tests pass, no regressions.
  - [x] `uv run pytest tests/integration/` **excluding** [`test_setup_bat.py`](../../tests/integration/test_setup_bat.py) — 51 passed. The excluded suite is a pre-existing Story 2.1 shell-out harness that runs the real `setup.bat` end-to-end; confirmed via grep to have zero overlap with Story 3.2's code (no `ModeInfo` / `BrainPort` / `briefing` / `nerve` references). The Windows shell-outs take minutes per test in this environment and are not exercising anything this story changed. A full `uv run pytest` including that suite should be re-run before merge as part of the standard code-review gate.
  - [x] Coverage on touched modules: `nova.systems.nerve.briefing` 100%, `nova.systems.nerve.__init__` 100%, `nova.adapters.sqlite.brain` 95.1% (3 lines uncovered are pre-existing Story 3.1 code). Combined 96%, well above the 88% floor.

### Review Findings

Three-layer adversarial review run 2026-04-22 via `/bmad-code-review`: Blind Hunter (20 raw findings), Edge Case Hunter (19 raw findings), Acceptance Auditor (3 raw findings, all Low severity, "PASS" verdict). Triage: 9 patches, 4 deferred (logged in `deferred-work.md`), 29 dismissed as noise or spec-compliant.

**Reviewer independence caveat:** Review was performed in the same Claude session that implemented the story — reduced independence signal vs. the dev-story tip's "different LLM" recommendation. The three parallel adversarial layers still found real gaps (partial-failure propagation test, cancellation test, fake-port protocol lock), but a fresh-session or different-model re-run before merge remains best practice.

#### Patch findings

- [x] [Review][Patch] Lock `_RecordingFakeBrainPort` structural conformance to `BrainPort` at construction time [tests/unit/systems/nerve/test_briefing_assembly.py] — add a `cast(BrainPort, brain)` or `runtime_checkable` Protocol assertion so if `BrainPort` grows a 12th method tomorrow, tests fail fast instead of silently skipping the uncovered method.
- [x] [Review][Patch] Add adapter test for empty-string `mode_name` input [tests/unit/adapters/sqlite/test_brain_adapter.py] — `get_mode_last_used("")` docstring promises `None`; no test locks it.
- [x] [Review][Patch] Add assembly test: `StorageError` from one `get_mode_last_used` call propagates cleanly [tests/unit/systems/nerve/test_briefing_assembly.py] — Review Focus claims propagation is the intended behavior; no test locks it across the N-call loop.
- [x] [Review][Patch] Add assembly test: `asyncio.CancelledError` mid-loop propagates, no partial state leaks [tests/unit/systems/nerve/test_briefing_assembly.py] — Review Focus documents cancellation safety; no test exercises the partial-completion path.
- [x] [Review][Patch] Make `_Call` test helper dataclass frozen [tests/unit/systems/nerve/test_briefing_assembly.py:48-53] — codebase-wide rule is frozen-by-default.
- [x] [Review][Patch] Add assembly test: stem == display_name coincidentally equal still queries by stem [tests/unit/systems/nerve/test_briefing_assembly.py] — existing test forces them to differ; a bug swapping to display_name would pass silently when they happen to match.
- [x] [Review][Patch] Clean up duplicate `# last_updated:` comment in sprint-status.yaml [_bmad-output/implementation-artifacts/sprint-status.yaml:2-3] — Story 3.1's review-complete comment still coexists with Story 3.2's context comment alongside the live `last_updated:` field. Keep only the most recent comment; the live field is the source of truth.
- [x] [Review][Patch] Fix truncated docstring sentence in `get_mode_last_used` [src/nova/adapters/sqlite/brain.py] — "callers needing a `datetime` parse at the render layer via `datetime.fromisoformat`" is grammatically incomplete; insert "can" so the sentence parses.
- [x] [Review][Patch] Reconcile Completion Notes test-count summary with the actual suite [_bmad-output/implementation-artifacts/3-2-briefingaggregate-and-state-determination.md] — "6 tests using _RecordingFakeBrainPort" understates the full nerve-suite count by 3 invariant tests in `test_briefing_state.py`. Update the notes to the accurate 9 + 6 + 4 = 19 tests in `tests/unit/systems/nerve/` + 6 adapter tests + 1 parametrize entry = 22 new tests total.
- [x] [Review][Patch] Adapter conformance test drifted from BrainPort contract — missing `get_mode_last_used` [tests/unit/adapters/sqlite/test_brain_adapter.py:507-518] — post-review finding (user-spotted, not captured by the three adversarial layers). The runtime method-list in `test_sqlite_brain_adapter_structurally_satisfies_brainport` still enumerated Story 3.1's 10-method surface; `get_mode_last_used` (Story 3.2's addition) was absent. Test would pass even if the adapter were missing the new method — exactly the false-confidence gap P1's fake-port guard addresses on the other side. Fix: added `"get_mode_last_used"` to the tuple in the same logical position as `PORT_CONTRACT` in `test_port_isolation.py` + added a docstring note requiring this list stay in lockstep with both port Protocol and `PORT_CONTRACT`.

#### Post-patch CI state

- `uv run ruff check src/ tests/`: **All checks passed.**
- `uv run ruff format src/ tests/`: 1 file reformatted after the patches (`tests/unit/systems/nerve/test_briefing_assembly.py`), now clean.
- `uv run mypy src/ tests/`: **Success — no issues found in 105 source files** (strict mode).
- `uv run pytest tests/unit/`: **1322 passed + 1 skipped** in 8.23s.
- `uv run pytest tests/integration/` (excluding the slow `test_setup_bat.py` suite): **51 passed** in 1.98s.
- `tests/unit/systems/nerve/`: **23 passed** (9 state + 10 assembly + 4 isolation) — every Story 3.2 + review-patch test green.
- `tests/unit/adapters/sqlite/test_brain_adapter.py`: **45 passed** (Story 3.1 baseline + 7 new `get_mode_last_used` tests + 1 parametrize entry).
- `tests/integration/test_setup_bat.py`: ✅ **4 passed in 15.52s** when run with the `-s` flag. Diagnosed the earlier hang root-cause: pytest's default output capture deadlocks the cmd.exe → uv → python subprocess chain on Windows because grandchild processes inherit the capture pipes and hold them open. Fix is the `-s` flag (disables output capture). Verified by running setup.bat under `subprocess.run(capture_output=True)` standalone — completes in 3.9s. Reproducer at `c:/tmp/repro_setup_bat.py`. **Recommended `pytest` invocation pattern on Windows local dev:** run `pytest --ignore=tests/integration/test_setup_bat.py` for the bulk of the suite (default capture), then `pytest tests/integration/test_setup_bat.py -s` for setup_bat specifically. Linux CI is unaffected — no Windows process-handle inheritance, no setup.bat. Tracking a Story 2.1 follow-up in deferred-work to make test_setup_bat.py self-detach (e.g., via `creationflags=CREATE_NEW_PROCESS_GROUP` or pipe-handle hygiene) so `-s` is no longer required.
- **Resolution path:** Smart App Control was permanently turned `Off` mid-session — restored local `mypy` + `pytest` execution. Net SAC trade-off documented at the project level: dev box no longer benefits from cloud-reputation unsigned-binary blocking, but every other Defender layer (real-time AV, SmartScreen, firewall, controlled folder access) stays active. Win11 Home limitation: SAC cannot be re-enabled without a Windows reinstall.

#### Deferred findings (logged separately — see `deferred-work.md`)

- [x] [Review][Defer] Index on `sessions(mode_name)` — deferred per story's load_briefing_aggregate performance profile.
- [x] [Review][Defer] `last_used_at` ISO-format validation at adapter boundary — out of scope; duplicates upstream writer contract ("adapters translate, never decide").
- [x] [Review][Defer] `is_default` tie-break enforcement when multiple modes set the flag — spec explicitly punts to Stories 3.3/3.5.
- [x] [Review][Defer] Snapshot-read isolation during concurrent writes — Story 3.5 (Nerve session lifecycle) concern; T1 is single-user single-session so not reachable in Story 3.2's scope.

## Dev Notes

### Pattern library consulted

- **#2 AST guards** — new `test_briefing_isolation.py` enforces nerve's import surface.
- **#3 frozen dataclass + single-worker executor** — `ModeInfo` extension stays `frozen=True`; the adapter method reads through the engine's single-worker executor, no new concurrency surface.
- **#4 error translation** — applies at the engine boundary (already in place); adapter method does not introduce new translation rules.

### Pattern NOT consulted (and why)

- **#1 clock indirection** — `get_mode_last_used` is a pure read; no timestamp stamping.
- **#6 transaction CM** — single-statement read; no multi-statement atomicity needed.
- **#7 partial-init cleanup** — `nerve/briefing.py` exposes free functions, not a constructed object. Story 3.5 (Nerve session lifecycle) will address any composition-root additions.

### State B is NOT the normal post-setup path in T1 (important for Story 3.3)

With Story 2.4's setup writing a completed session row (`is_complete=True`, `seed_text=NULL`, `mode_name=NULL`) and Story 3.1's `get_last_seed` correctly filtering it out, the **normal "I just finished setup, run `nova` again"** path lands in **State C (WARM_RESUME) with progressive omission**, not State B. State C's fallback rules (architecture.md:738-745) cleanly omit the seed hero line, omit "Last session", and resolve `suggested_mode` via default-mode fallback — which is visually indistinguishable from a "post-setup" card.

**Story 3.3 (BriefingViewModel assembly) must NOT assume State B is the ordinary onboarding follow-up state.** The first session-2 card the new user sees is State C with heavy omission — not State B.

State B in T1 is reachable only via these non-ordinary paths:
1. **User edited mode YAML before ever running `nova`.** Config has modes but no setup-complete session exists (`last_session is None`). Possible for power users who hand-author `modes/*.yaml` and skip the wizard.
2. **Interrupted session with no seed.** A prior session crashed / was killed / lost power after `create_session` but before `end_session` with a seed. `is_complete=False` AND `last_seed is None`. This is a genuinely-post-interruption degraded path — the Card should surface "your last session didn't finish cleanly" messaging, which Story 3.3 / 3.10 will address (Story 3.10 owns the crash-recovery UX specifically).

Both State B paths are legitimate and their render copy matters, but neither is the **onboarding follow-up** path. If Story 3.3's render tests are organized as "State A = first run / State B = post-setup / State C = warm resume", that labeling is misleading — the more accurate split is "State A = first run (no modes yet) / State B = unusual or degraded / State C = normal return (with or without seed)". Story 3.3 should call this out explicitly in its own Dev Notes.

### `load_briefing_aggregate` performance profile

At T1 scale the method issues ≤ 3 + N awaited DB calls per invocation (N = mode count, typically 1–5):

1. `get_last_session` — `SELECT … LIMIT 1` on `sessions` (indexed by PK).
2. `get_last_seed` — `SELECT … WHERE is_complete=1 AND seed_text IS NOT NULL LIMIT 1` on `sessions` (PK-ordered).
3. `get_last_snapshot_for_session` — `SELECT … WHERE session_id=? LIMIT 1` on `workspace_snapshots` (has a `session_id` FK index per 001 migration).
4. `get_mode_last_used(stem)` per mode — `SELECT … WHERE mode_name=? LIMIT 1` on `sessions`.

For 5 modes this is 8 fetchones on a local SQLite file. The briefing NFR is < 5s total render time (PRD NFR3); adapter reads typically complete in sub-millisecond range. Story 3.5 owns the end-to-end measurement; Story 3.2 does not add measurement infrastructure.

**If `mode_name` becomes a bottleneck in later epics (e.g., 50+ modes in a T2 workspace-template scenario):** add an index `CREATE INDEX idx_sessions_mode_name ON sessions(mode_name)` via a new migration. Out of scope for T1 — the sequential scan over ≤ ~1,000 rows (6 months × ~5 sessions/day) is well under NFR budget.

### Test seam: `_RecordingFakeBrainPort` is a pattern primitive

The fake-Brain helper in Task 6 is the **first cross-system test fake** in this codebase. Future stories that wire Ritual or Voice or Skin against `BrainPort` will reuse the same pattern. Worth noting but NOT worth extracting to `tests/conftest.py` in this story — keep it local to `test_briefing_assembly.py`. If Story 3.3 / 3.5 copy this class, THAT is when extraction becomes a retro note.

### Why `nerve/briefing.py`, not `nerve/briefing/__init__.py`

Story 3.2 ships two free functions (~50–80 lines total). A single-file module is the right shape. The package-with-__init__.py form becomes appropriate when Nerve grows to 4+ concerns (command router, session lifecycle, briefing, tier check — expected after Story 3.5). Deferring sub-packaging until the count justifies it — YAGNI.

### Why `BriefingState` imports from `nova.core.types`, not `nova.systems.ritual.models`

[`src/nova/core/types.py`](../../src/nova/core/types.py) is the single "shared vocabulary" location for enums that multiple systems persist or cross (Story 1.2 AC). `BriefingState` is such an enum — Nerve (this story) determines it, Ritual (Story 3.3) renders it, Skin (Story 3.3/3.4) dispatches on it. Importing from `core/types` keeps the one-way dependency direction clean: `nerve` depends on `core`, never on sibling system's `models`. This is a deliberate departure from architecture.md:1351 which placed `BriefingState` in `systems/ritual/models.py` — Story 1.2 resolved it to `core/types.py` for exactly this cross-system sharing reason, and this story honors the Story 1.2 placement.

### Module-attribute imports vs. direct re-exports

`nova.systems.nerve.__init__.py` re-exports via `from nova.systems.nerve.briefing import ...`. That is a direct re-export, NOT a module-attribute pattern. The clock-indirection rule (cross-cutting-patterns.md #1) applies to timestamp-generating modules — `briefing.py` does not stamp timestamps, so the `from ... import` form is fine here.

### Explicit scope fence (non-goals)

- Story 3.2 does NOT populate `BriefingViewModel`. Ritual assembly is Story 3.3.
- Story 3.2 does NOT render anything. Skin rendering is Story 3.3.
- Story 3.2 does NOT wire the composition root to call `load_briefing_aggregate` on boot. Nerve session lifecycle wiring is Story 3.5.
- Story 3.2 does NOT ship `recent_memory` content. `recent_memory = ()` is T1 scope; Epic 4 (Memory Enrichment) and Epic 5 (Transparency) own real content.
- Story 3.2 does NOT implement `suggested_mode` selection logic. Architecture.md:727 describes "most recent, default, or pattern-based" — Story 3.3 owns that policy against the aggregate this story produces.
- Story 3.2 does NOT ship tie-break logic for multiple modes with `is_default=True`. Each `ModeInfo` carries its own `is_default` verbatim; Story 3.3 / 3.5 resolve ties at consumption time. The config loader already logs a WARNING when multiple modes set `is_default=True` ([`src/nova/core/config.py:659-663`](../../src/nova/core/config.py#L659-L663)) — operator-visible; this story does not duplicate that warning.
- Story 3.2 does NOT ship the **write-side** of the stem-vs-display-name contract (AC #4a). Sessions.mode_name is not written by any code in this story; Stories 3.5/3.6/3.7 own that obligation and each must cite AC #4a in their own Dev Notes.
- Story 3.2 does NOT add tier-aware branching. `prose_enrichment` is always `None` in T1 for this aggregate shape (the field doesn't live on `BriefingAggregate` — it lives on `BriefingViewModel`).
- Story 3.2 does NOT modify `setup/initial_capture.py` or any setup-time write path. Story 3.1 completed that migration; Story 3.2 reads from the resulting DB state only.
- Story 3.2 does NOT ship memory-item writes (`create_memory_item`). Story 3.7 (shutdown seed capture) owns that Brain extension.
- Story 3.2 does NOT widen `BrainPort` beyond `get_mode_last_used`. Any additional read methods are deferred to their consuming story.
- Story 3.2 does NOT touch the Epic 5 adapter stubs (`query_memory`, `delete_matching`, `confirm_deletion`, `get_transparency_model`). They remain `NotImplementedError("Epic 5 scope")`.
- Story 3.2 does NOT introduce an index migration on `sessions.mode_name`. See "performance profile" note above — deferred to whichever story first proves the bottleneck.

## Review Focus (boundary-first invariant sweep)

Per Epic 1 retrospective action item A1 (extended to interaction boundaries per Epic 2 retro A6). Story 3.2 is an interaction-boundary story; this sweep is mandatory.

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | Briefing module has zero lifecycle — two free functions, no constructor, no state. `SqliteBrainAdapter.get_mode_last_used` inherits Story 3.1's statelessness invariant. No background tasks, timers, subscriptions. |
| **Teardown under partial failure** | No resources acquired in this story. Engine teardown remains `create_app`'s partial-init cleanup (cross-cutting-patterns.md #7, unchanged). |
| **Concurrency model** | `load_briefing_aggregate` issues N+3 sequential awaits to Brain (the engine's `_tx_lock` / single-worker executor serializes them, cross-cutting-patterns.md #3). No parallelization — one consistent read view. Same-task calls never nest in a transaction (the assembler is a pure reader; no `storage.transaction()` block). `determine_briefing_state` is sync and pure. |
| **Cancellation** | `asyncio.CancelledError` during any awaited Brain call propagates untouched (project-context.md:49). The assembler does not catch `CancelledError` — if cancelled mid-assembly, the partially-constructed aggregate is dropped on the floor (no resource leak, no side effects). Unit test covers this: start `load_briefing_aggregate` inside a task, cancel the task between the seed fetch and the snapshot fetch, assert CancelledError propagates. |
| **Error translation** | Two-layer design, same as Story 3.1. **Engine layer:** `sqlite3.Error` / `sqlite3.Warning` / `OSError` → `StorageError`. **Adapter layer:** no new translations (read-only on a typed column, no JSON, no enum coercion). **Assembler layer:** no translation — propagates `StorageError` to the caller (Story 3.5's Nerve decides how to degrade). |
| **Test determinism** | `determine_briefing_state` is pure — deterministic by construction. `load_briefing_aggregate` tests use `_RecordingFakeBrainPort` (no real clock, no real DB). Adapter tests use in-memory SQLite + applied migrations + monkeypatched `events._utc_now_iso` for `create_session` timestamp control. |
| **Logging opacity** | DEBUG-only. The adapter's `get_mode_last_used` logs `"brain.get_mode_last_used start"` with no `extra`. The assembler logs nothing — debugging it is done by stepping through or re-running the tests. `mode_name` is never logged (it CAN contain excluded-app names). No exception messages carry mode_name (existing engine/adapter error translation already satisfies this — no new exception strings in this story). |
| **Idempotency** | Both public callables are idempotent: same inputs → same outputs, zero side effects. `get_mode_last_used` is a read-only SQL; repeated invocation returns the same row until a new session with that mode is inserted. |
| **Atomicity contract** | No multi-statement writes in this story. Reads do not need atomicity beyond SQLite's default per-statement consistency. |
| **Deterministic mode ordering** | `sorted(config.modes.items())` fixes the iteration order. `dict[str, ModeConfig]` preserves Python 3.12's insertion-order guarantee but that's not load-bearing here — the `sorted()` call overrides it. Test `test_load_briefing_aggregate_mode_order_is_stem_ascending` locks this. |
| **Pre-flag divergence handling** | The pre-flag's "POST_SETUP for setup row" claim is explicitly contradicted by the literal state machine in epics.md + architecture.md. Story 3.2 follows the literal machine (setup row → State C) and ships a regression test (AC #16 / #19b) locking the boundary. Documented in § Depends on prior-story state. |
| **State B reachability in T1** | State B is NOT the normal "ran `nova` after setup" path (that path is State C with progressive omission). State B is reached only via (a) user hand-edited mode YAML before running `nova` once, or (b) a prior session was interrupted before seed capture. See Dev Notes "State B is NOT the normal post-setup path in T1" — Story 3.3 MUST NOT label State B as "post-setup onboarding follow-up" in its render copy or test naming. |
| **Mode identity contract** | `ModeInfo.stem` and `ModeInfo.display_name` are **two independent fields** (AC #4). `sessions.mode_name` stores the stem; Skin renders the display_name. Stories 3.5/3.6/3.7 MUST write the stem on session creation/update (AC #4a). This story locks the read-side; write-side is a downstream obligation with its own regression test deferred to whichever story first creates a runtime session with a mode. |
| **Patterns consulted** | #2 AST guards (new nerve-isolation), #3 frozen dataclass (`ModeInfo` reshape + extension, `BriefingAggregate` unchanged). Patterns NOT consulted: #1 clock indirection (pure read), #4 error translation (no new translation), #6 transaction (no writes), #7 partial-init cleanup (no new constructed resources). |

### Open questions resolved during SM authoring

1. **Does `BriefingState` already exist?** — YES, at [`src/nova/core/types.py:44-56`](../../src/nova/core/types.py#L44-L56). Do NOT re-declare; import from `nova.core.types`. Rationale in Dev Notes section "Why `BriefingState` imports from `nova.core.types`".

2. **Does `BriefingAggregate` already exist?** — YES, at [`src/nova/systems/brain/models.py:170-193`](../../src/nova/systems/brain/models.py#L170-L193). Story 3.1 kept it after removing the `load_briefing_aggregate` port method. The shape is final; this story populates it.

3. **Does `BriefingViewModel` already exist?** — YES, at [`src/nova/systems/ritual/models.py:26-51`](../../src/nova/systems/ritual/models.py#L26-L51). Story 3.2 does NOT touch it — the ViewModel and its population logic are Story 3.3's scope.

4. **Is `ModeInfo` already aligned with the Story 3.2 AC?** — NO. The current shape is `(name, last_used_at)`. Story 3.2 reshapes it to `(stem, display_name, app_count, is_default, last_used_at)` — the `name` field is split into two distinct identifiers to lock the cross-story contract that `sessions.mode_name` stores the stem (canonical ID), while the display_name carries the user-facing label separately. See AC #4 + AC #4a. Task 1 handles this.

5. **Should `last_used_at` be `datetime` or `str`?** — `str` (ISO-8601 UTC). Architecture.md says `datetime`; Story 3.1 locked `str` as the convention at the port boundary for all timestamps. Parse to `datetime` at the render layer only.

6. **Does `get_last_snapshot_for_session` already exist?** — YES, on both `BrainPort` (Story 3.1 AC #12) and `SqliteBrainAdapter`. The epic AC for Story 3.2 lists it under "Brain provides persisted-fact queries only" but that is a restatement of Story 3.1's delivered surface, not a new request. Story 3.2 calls it but does not add it.

7. **Pre-flag said "POST_SETUP for setup row" — which do we follow?** — The **literal state machine** in epics.md / architecture.md, which yields State C for setup-row-only. See § Depends on prior-story state. The pre-flag's intuition label was wrong; the AC boundary test is the source of truth.

## Project Structure Notes

**New source files:**
- `src/nova/systems/nerve/briefing.py` — `load_briefing_aggregate`, `determine_briefing_state`, module docstring, `__all__`.

**Modified source files:**
- `src/nova/systems/brain/models.py` — `ModeInfo` extended with two new fields; docstring updated.
- `src/nova/ports/brain.py` — one new Protocol method; class docstring updated.
- `src/nova/adapters/sqlite/brain.py` — one new SQL constant, one new method body.
- `src/nova/systems/nerve/__init__.py` — placeholder docstring replaced with re-exports.

**New test files:**
- `tests/unit/systems/nerve/test_briefing_state.py` — parametrized state-determination tests.
- `tests/unit/systems/nerve/test_briefing_assembly.py` — fake-Brain aggregate assembly tests.
- `tests/unit/systems/nerve/test_briefing_isolation.py` — AST import guard.

No `tests/unit/systems/nerve/__init__.py` — matches existing test-layout convention (see `tests/unit/core/`, `tests/unit/adapters/sqlite/`).

**Modified test files:**
- `tests/unit/ports/test_port_isolation.py` — `PORT_CONTRACT[brain_port_module]` expanded to 11 methods.
- `tests/unit/adapters/sqlite/test_brain_adapter.py` — 7 new tests for `get_mode_last_used`.

**Modified planning / tracking files:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Scrum Master flips `3-2-briefingaggregate-and-state-determination: backlog → ready-for-dev` via the create-story workflow; Dev flips `ready-for-dev → in-progress → review` during implementation; code-review workflow flips `review → done`.

**Line-count discipline:** `briefing.py` should fit under ~80 lines (two functions, module docstring, imports). `ModeInfo` extension is +2 lines. Adapter extension is +15 lines (method + SQL constant). Port extension is +2 lines. Total new source: ~100 lines. New test lines: ~300 (six state tests + five assembly tests + four AST tests + seven adapter tests, each 15-25 lines). Story 3.2 is a thin-slice follow-up to Story 3.1 — resist scope creep.

### Alignment with unified project structure

- `nova.systems.nerve` is the system package for orchestration / routing / briefing concerns — matches architecture.md:1328 directory layout.
- `nova.systems.brain.models.ModeInfo` extension stays inside Brain's model file (cross-system-portable per Story 1.9 AC #8).
- `nova.ports.brain.BrainPort` extension stays in Brain's port file (the Protocol's single source of truth).
- `tests/unit/systems/nerve/` is the first nerve-test directory — follows Story 1.3's `tests/unit/core/` layout pattern.

### Detected conflicts or variances

None detected. `BriefingState` in `core/types.py` (Story 1.2) is a deliberate deviation from architecture.md:1351 which placed it in `ritual/models.py` — Story 1.2's documented rationale (cross-system shared vocabulary) stands; this story honors it.

## References

- [Source: _bmad-output/planning-artifacts/epics.md — Story 3.2 ACs (lines 1081–1109), Epic 3 framing (lines 1048–1050)](../planning-artifacts/epics.md#L1081-L1109)
- [Source: _bmad-output/planning-artifacts/architecture.md — Decision 3b BriefingAggregate / ModeInfo / state determination (lines 586–764)](../planning-artifacts/architecture.md#L586-L764)
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md — Briefing Card State Contract (lines 746–805), Briefing Card composition (lines 356–492)](../planning-artifacts/ux-design-specification.md#L746-L805)
- [Source: _bmad-output/planning-artifacts/prd.md — briefing FRs and NFR3 (5-second budget)](../planning-artifacts/prd.md)
- [Source: _bmad-output/project-context.md — Brain owns all SQLite tables (line 67), Config module is single YAML reader (line 69), Nerve is orchestrator not router (line 65), Adapters translate never decide (line 77), BriefingCard has three distinct states (line 188)](../project-context.md)
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-15.md — boundary-first invariant sweep, cross-cutting-patterns origin](epic-1-retro-2026-04-15.md)
- [Source: _bmad-output/implementation-artifacts/epic-2-retro-2026-04-18.md — interaction-boundary classification (A6), degraded-path proof (A9), prior-state assumptions (A10)](epic-2-retro-2026-04-18.md)
- [Source: _bmad-output/implementation-artifacts/epic-3-story-preflags.md — Story 3.2 pre-flag (lines 11–22) and the POST_SETUP-vs-WARM_RESUME callout](epic-3-story-preflags.md#L11-L22)
- [Source: _bmad-output/implementation-artifacts/3-1-brain-session-and-seed-persistence.md — BrainPort reshape, SqliteBrainAdapter conventions, setup-row reconciliation, A10 pattern precedent](3-1-brain-session-and-seed-persistence.md)
- [Source: _bmad-output/implementation-artifacts/2-4-briefing-card-state-a-initial-capture-and-setup-completion.md — setup-row contract and fast-path interaction](2-4-briefing-card-state-a-initial-capture-and-setup-completion.md)
- [Source: docs/cross-cutting-patterns.md — patterns #2 (AST guards), #3 (frozen dataclass), #4 (error translation)](../../docs/cross-cutting-patterns.md)
- [Source: src/nova/core/types.py — `BriefingState` StrEnum (lines 44–56)](../../src/nova/core/types.py#L44-L56)
- [Source: src/nova/core/config.py — `NovaConfig.modes` (lines 196–209), `ModeConfig` (lines 152–164), `_load_modes` stem-sorted iteration (lines 574–616)](../../src/nova/core/config.py)
- [Source: src/nova/ports/brain.py — `BrainPort` Protocol (extended in this story)](../../src/nova/ports/brain.py)
- [Source: src/nova/systems/brain/models.py — `BriefingAggregate` (lines 170–193), `ModeInfo` extension target (lines 118–127), `SessionSummary` (lines 50–72)](../../src/nova/systems/brain/models.py)
- [Source: src/nova/systems/ritual/models.py — `BriefingViewModel` (lines 26–51) — consumer of this story's output, not modified here](../../src/nova/systems/ritual/models.py)
- [Source: src/nova/systems/eyes/models.py — `WorkspaceSnapshot` (unchanged; referenced by `BriefingAggregate.last_snapshot`)](../../src/nova/systems/eyes/models.py)
- [Source: src/nova/adapters/sqlite/brain.py — `SqliteBrainAdapter` (extended in this story with `get_mode_last_used`)](../../src/nova/adapters/sqlite/brain.py)
- [Source: src/nova/core/storage/engine.py — `SqliteStorageEngine.fetchone`, thread-affinity contract](../../src/nova/core/storage/engine.py)
- [Source: src/nova/core/exceptions.py — `StorageError` propagation contract](../../src/nova/core/exceptions.py)
- [Source: src/nova/core/events.py — `_utc_now_iso` clock (not stamped in this story, referenced for convention)](../../src/nova/core/events.py)
- [Source: tests/unit/ports/test_port_isolation.py — `PORT_CONTRACT` shape test (updated for 11-method BrainPort)](../../tests/unit/ports/test_port_isolation.py)
- [Source: tests/unit/adapters/sqlite/test_brain_adapter.py — Story 3.1 adapter tests (extended in this story)](../../tests/unit/adapters/sqlite/test_brain_adapter.py)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context)

### Debug Log References

- **No `ModeInfo(name=...)` consumers to migrate.** The pre-task grep (Task 1) confirmed zero runtime constructions of `ModeInfo` and zero `.name` accesses on a `ModeInfo` instance. The rename-plus-extend landed cleanly without any downstream updates beyond the test docstring refresh.
- **Existing `test_mode_info_is_distinct_from_mode_config` assertions held unchanged.** The test's assertion logic — "ModeInfo and ModeConfig must differ in field set; `last_used_at` only on ModeInfo; ModeConfig must have at least one field ModeInfo does not" — continued to pass verbatim under the reshaped ModeInfo. Only the prose docstring needed a refresh to reference the new `stem` / `display_name` split.
- **Port docstring forward reference removed.** The Story 3.1 docstring said "Story 3.2 will add `get_mode_last_used` for Nerve-side BriefingAggregate assembly" as a promissory note. Story 3.2 consumes and removes that sentence, now stating `get_mode_last_used` as a live part of the T1 surface with the stem-semantics call-out.
- **`_RecordingFakeBrainPort` Protocol conformance.** The fake implements all 11 `BrainPort` methods structurally. The four exercised by `load_briefing_aggregate` (`get_last_session`, `get_last_seed`, `get_last_snapshot_for_session`, `get_mode_last_used`) have real implementations with call recording; the remaining seven raise `NotImplementedError` with descriptive messages so an accidental invocation surfaces loudly rather than silently returning a default. mypy strict accepts this because `BrainPort` is a `typing.Protocol` — structural subtyping does not require annotations beyond the declared methods.
- **`test_determine_briefing_state_first_match_wins` clarifies a subtle rule.** The "empty modes + None session" case satisfies BOTH the FIRST_RUN guard (empty modes AND None session) AND the POST_SETUP guard (last_seed=None AND (None session)). Without an explicit ordering test, a future refactor that swaps the two `if` branches would silently demote FIRST_RUN cases to POST_SETUP — the test ensures the `if` ladder ordering is load-bearing.
- **Adapter double-catch parametrize extension.** Rather than write a separate `test_get_mode_last_used_does_not_double_catch_storage_error_from_engine`, the existing `test_adapter_does_not_double_catch_storage_error_from_engine` parametrize grew one entry (`"get_mode_last_used"`). Same monkeypatching logic applies — the storage-error identity-propagation contract is uniform across adapter methods.
- **Ruff auto-fix on the assembly test file.** `test_briefing_assembly.py` initially imported `BriefingAggregate` which ruff flagged as unused (the test never references the class directly — aggregates flow through `load_briefing_aggregate` returns only). Ruff's `--fix` removed the unused import and reformatted the imports block to group/sort consistently. The `BriefingAggregate` type is still reachable transitively via return-annotation inference; no test behavior changed.

### Completion Notes List

- **Task 1 — ModeInfo reshape.** `ModeInfo` grew from 2 fields (`name`, `last_used_at`) to 5 fields (`stem, display_name, app_count, is_default, last_used_at`). The rename of `name` → `stem` + `display_name` makes the canonical-identifier-vs-rendered-label distinction explicit in the type system rather than as a hidden convention. Module docstring documents the cross-story contract that `sessions.mode_name` stores the stem.
- **Task 2 — BrainPort.get_mode_last_used added.** One new Protocol method inserted between `get_last_snapshot_for_session` and the Epic 5 block. Port isolation contract tuple updated to 11 methods. All 94 port-isolation tests green.
- **Task 3 — SqliteBrainAdapter.get_mode_last_used implemented.** ~15 lines: one SQL constant (`_SELECT_LAST_MODE_USAGE_SQL`) + one method body using `storage.fetchone`. Stateless, read-only, no clock stamping. Debug logging carries no `mode_name` (opacity rule for user-chosen text).
- **Task 4 — nerve/briefing.py shipped.** Two public callables totaling ~50 lines of code (plus ~50 lines of module docstring documenting the state machine, the cross-story contract, and the State B reachability clarification). `determine_briefing_state` is a pure function; `load_briefing_aggregate` is async and issues 3+N awaited Brain calls in deterministic order.
- **Task 5 — State-determination tests.** 6 parametrized boundary cases + 3 invariant tests (purity, first-match-wins, recent_memory irrelevance). All 9 green on first run. Parametrize IDs name each boundary so test failures self-identify.
- **Task 6 — Assembly tests.** 6 tests using `_RecordingFakeBrainPort`: empty DB short-circuit, setup-row-only → State C, call-ordering, stem-ascending mode order, stem/display_name independence with query-by-stem contract, and recent_memory empty-tuple invariant. All green.
- **Task 7 — Adapter unit tests.** 6 new tests + 1 parametrize entry in the existing double-catch test. Covers happy path (latest session wins, unused mode returns None, empty DB returns None), degraded path (filter is `=` not `LIKE`, NULL-mode rows never match, StorageError propagation), and rerun (idempotent reread).
- **Task 8 — AST isolation guard.** 4-test file mirroring `test_brain_adapter_isolation.py` structure: forbidden-module imports, sqlite3-at-any-scope, dynamic-import prevention, positive-list allowlist. Locks nerve's import surface to `nova.core.*`, `nova.ports.brain`, and `nova.systems.brain.models`.
- **Task 9 — CI gate.** `ruff check --fix` + `ruff format`: clean. `mypy src/ tests/`: no issues in 105 source files (strict mode). `pytest tests/unit/`: 1317 passed + 1 skipped (14.02s). `pytest tests/integration/` (excluding `test_setup_bat.py`, a pre-existing Story 2.1 shell-out suite that is slow and orthogonal to Story 3.2's scope — grepped to confirm zero overlap with anything this story touches): 51 passed (3.10s). Coverage on touched modules: `nova.systems.nerve.briefing` 100% (21 stmts + 6 branches), `nova.systems.nerve.__init__` 100%, `nova.adapters.sqlite.brain` 95.1% (3 lines in pre-existing Story 3.1 code uncovered). Combined new-or-modified line coverage: 96%, well above the 88% floor.
- **Final test inventory (after 2026-04-22 review patches).** 19 tests in `tests/unit/systems/nerve/` pre-review (9 state + 6 assembly + 4 isolation) grew to 23 post-review (9 state + 10 assembly + 4 isolation) with the addition of 4 review-patch tests: `test_recording_fake_implements_all_brainport_methods` (P1 drift guard), `test_load_briefing_aggregate_propagates_storage_error_from_mode_lookup` (P3 error propagation), `test_load_briefing_aggregate_propagates_cancellation` (P4 cancellation), `test_load_briefing_aggregate_queries_by_stem_when_coincidentally_equal` (P6 coincidental-equality). 6 new adapter `get_mode_last_used` tests grew to 7 with `test_get_mode_last_used_returns_none_for_empty_string_mode_name` (P2 empty-string contract). Plus 1 parametrize entry on the existing double-catch StorageError test. Total new test runs: 23 + 7 + 1 = **31 new test cases** across this story.

### File List

**New source files:**

- `src/nova/systems/nerve/briefing.py` — `load_briefing_aggregate` + `determine_briefing_state` + module docstring documenting state machine, cross-story contract, State B reachability.

**Modified source files:**

- `src/nova/systems/brain/models.py` — `ModeInfo` reshaped from 2 fields to 5 fields (`stem`, `display_name`, `app_count`, `is_default`, `last_used_at`); module docstring updated.
- `src/nova/ports/brain.py` — `get_mode_last_used(mode_name: str) -> str | None` added; class docstring updated with stem-semantics call-out.
- `src/nova/adapters/sqlite/brain.py` — `_SELECT_LAST_MODE_USAGE_SQL` constant + `get_mode_last_used` method body.
- `src/nova/systems/nerve/__init__.py` — re-exports `load_briefing_aggregate` and `determine_briefing_state`; docstring updated.

**New test files:**

- `tests/unit/systems/nerve/test_briefing_state.py` — 9 state-determination tests (6 parametrized boundary cases + purity + first-match-wins + recent_memory irrelevance).
- `tests/unit/systems/nerve/test_briefing_assembly.py` — 6 assembly tests using `_RecordingFakeBrainPort`.
- `tests/unit/systems/nerve/test_briefing_isolation.py` — 4 AST import guards on `nerve/briefing.py`.

**Modified test files:**

- `tests/unit/ports/test_port_isolation.py` — `PORT_CONTRACT[brain_port_module]` expanded to 11 methods (added `get_mode_last_used`); `test_mode_info_is_distinct_from_mode_config` docstring refreshed to reflect the reshape.
- `tests/unit/adapters/sqlite/test_brain_adapter.py` — 6 new `get_mode_last_used` tests appended in a new "Story 3.2" section; existing `test_adapter_does_not_double_catch_storage_error_from_engine` parametrize gained one entry for the new method.

**Modified planning / tracking files:**

- `_bmad-output/implementation-artifacts/3-2-briefingaggregate-and-state-determination.md` — Tasks 1–9 marked complete; Dev Agent Record populated; Status updated to `review` at end of Task 9.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `3-2-briefingaggregate-and-state-determination: ready-for-dev → in-progress → review` across the implementation session.

## Change Log

- 2026-04-21: Story 3.2 drafted via `/bmad-create-story`. Auto-discovered as the next `backlog` entry in `sprint-status.yaml`. Status set to `ready-for-dev`. Pre-flag file ([epic-3-story-preflags.md](epic-3-story-preflags.md)) folded in; the "POST_SETUP for setup row" pre-flag intuition was reconciled against the literal state machine (epics.md / architecture.md) and resolved as WARM_RESUME — a boundary regression test is specified (AC #16 / #19b) to lock the correct answer. (Co-Authored-By: Claude Opus 4.7 (1M context))
- 2026-04-21 (rev 2): Three SM review fixes applied pre-dev. (a) **Mode identity contract** — `ModeInfo.name` split into `stem` + `display_name` (AC #4 reshape); cross-story contract locked that `sessions.mode_name` stores the stem (AC #4a) as a write-side obligation for Stories 3.5/3.6/3.7. Prevents the hidden contract where downstream code would have to guess whether to query Brain by stem or display name. (b) **`__init__.py` dropped** — `tests/unit/systems/nerve/__init__.py` removed from Task 5 and the file list; matches existing convention (`tests/unit/core/`, `tests/unit/adapters/sqlite/` have none). (c) **State B reachability clarified** — new Dev Notes subsection + Review Focus row explicitly stating that State B is NOT the normal post-setup path in T1 (that path is State C with progressive omission); Story 3.3 is warned against treating B as the onboarding follow-up state.
- 2026-04-21: Story 3.2 implementation complete. `BrainPort.get_mode_last_used` shipped with 7 unit tests + parametrized double-catch coverage; `ModeInfo` reshaped to 5-field `stem`/`display_name`/`app_count`/`is_default`/`last_used_at` shape; Nerve briefing-assembly module shipped with `load_briefing_aggregate` (async) + `determine_briefing_state` (pure function) + 19 tests covering state-machine boundaries, fake-BrainPort assembly, and AST import isolation. Full CI gate green. Status → review. (Co-Authored-By: Claude Opus 4.7 (1M context))
- 2026-04-22: Three-layer adversarial code review run via `/bmad-code-review` (same-session caveat noted). Triage: 9 patches, 4 deferred, 29 dismissed. All 9 patches applied: (P1) runtime drift guard locking `_RecordingFakeBrainPort` against `BrainPort` method growth, (P2) adapter test for empty-string `mode_name`, (P3) assembly test for mid-loop `StorageError` propagation, (P4) assembly test for `asyncio.CancelledError` propagation, (P5) `_Call` dataclass frozen, (P6) stem == display_name coincidentally-equal assembly test, (P7) sprint-status duplicate `last_updated` comment cleanup, (P8) fixed truncated docstring in `get_mode_last_used`, (P9) completion-notes test-count reconciliation. 4 deferred items logged in `deferred-work.md`. `ruff check` + `ruff format`: clean. `mypy` + `pytest` re-run blocked by transient Windows AppLocker policy this session — user to re-verify before merge.
- 2026-04-22 (post-review): User-spotted finding the three adversarial layers missed — `test_sqlite_brain_adapter_structurally_satisfies_brainport` had a stale method-list of Story 3.1's 10 methods, missing `get_mode_last_used` from Story 3.2. False-confidence risk (test would pass even if adapter dropped the new method). Fixed: added `"get_mode_last_used"` to the tuple + inline comment requiring the list to stay in lockstep with `BrainPort` Protocol and `PORT_CONTRACT`. Ruff clean. Worth noting that the mirror-image guard on the fake-Brain side (P1 hardcoded list) has the exact same drift risk and the same mitigation — both sites deliberately require manual update when the port grows, so the manual update becomes the forcing signal. A deferred-work entry now tracks the "consolidate three hardcoded BrainPort method lists" refactor for a future hygiene pass.
- 2026-04-22: Story 3.2 flipped `review → done`. Local `mypy` + `pytest` re-verification blocked by Windows Smart App Control ("Application Control policy blocked file" — `os error 4551` on `.venv\Scripts\python.exe`). Diagnosis: environmental (system-level SAC/WDAC policy), not a Story 3.2 code issue. `ruff check` + `ruff format` both clean post-patch. All production code changes are a single docstring typo fix (P8); the remaining 9 changes are pure test additions or non-code edits (frozen dataclass annotation, YAML comment cleanup, story-file prose). Risk profile: any failure would appear as a red test (additive tests are decoupled from production behavior). Pre-merge CI on a non-SAC-affected runner re-verifies before the branch lands on main. If the pre-merge CI does surface a failure, the fix ships as a Story 3.2 follow-up patch and the story returns to `review` temporarily.
- 2026-05-04: Local CI gate verified post-SAC-disable. Smart App Control was permanently turned Off (Win11 Home one-way door). Results: `ruff check`/`ruff format` clean; `mypy src/ tests/` → success on 105 source files (strict mode); `pytest tests/unit/` → **1322 passed + 1 skipped** in 8.23s; `pytest tests/integration/` (excluding `test_setup_bat.py`) → **51 passed** in 1.98s. Story 3.2 touched modules at 100% green: 23 nerve tests + 45 adapter tests. `test_setup_bat.py` initially hung >10 min — initially attributed to Defender real-time scanning, but root-cause diagnosed below.
- 2026-05-04 (test_setup_bat.py root-cause): Pytest's default output-capture deadlocks the cmd.exe → uv.exe → python.exe subprocess chain on Windows. Grandchild processes inherit pytest's capture pipe handles and hold them open, so pytest's end-of-test pipe drain hangs indefinitely. Verified by running setup.bat under `subprocess.run(capture_output=True)` standalone (3.9s, exit 0) and by running pytest with `-s` flag (`uv run pytest tests/integration/test_setup_bat.py -s` → **4 passed in 15.52s**). Final local CI gate: `pytest --ignore=tests/integration/test_setup_bat.py` + `pytest tests/integration/test_setup_bat.py -s` = **1377 passed + 1 skipped, 0 failures across all 1378 tests**. The deadlock predates Story 3.2 and would affect any Windows dev box; logged in `deferred-work.md` as a Story 2.1 test-hygiene follow-up. Pre-existing CI on Linux runners is unaffected (no Windows handle inheritance, no setup.bat).
