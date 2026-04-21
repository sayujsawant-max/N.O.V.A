# Epic 3 — Story Pre-Flags (Interaction-Boundary Treatment)

**Created:** 2026-04-18
**Origin:** Epic 2 retrospective action items **A1** (extended boundary sweep), **A6** (interaction-boundary detection).
**Purpose:** Pre-mark Epic 3 stories that need interaction-boundary treatment at SM `create-story` time. Not full authoring — the SM agent folds these flags into the full story file when each story moves to `ready-for-dev`.

> Story 3.1 has its own prep file ([3-1-brain-session-and-seed-persistence.md](3-1-brain-session-and-seed-persistence.md)) with full A6 + A10 sections already populated. The three stories below get lighter pre-marks only.

---

## Story 3.2 — BriefingAggregate & State Determination

**Classification:** ✅ Interaction-boundary story.

**A6 answers:**
1. **New contract between existing pieces?** YES. Introduces a new ownership boundary: Nerve (or a briefing-assembly collaborator it owns) merges Brain's persisted facts with `NovaConfig.modes` to produce `BriefingAggregate`. Critically, **Brain does NOT read mode YAML** — that crosses the config-ownership boundary. `ModeInfo` is enriched with `last_used_at` sourced from Brain's session history, not from config. This is a non-obvious data-flow contract between three already-shipped pieces (Brain / Nerve / NovaConfig).
2. **New invariants in degraded / partial-failure paths?** YES. State determination is "first match wins": `FIRST_RUN` / `POST_SETUP` / `WARM_RESUME`. The `POST_SETUP → WARM_RESUME` boundary depends on the interaction of *three* signals (`last_seed`, `last_session.is_complete`, `last_session` presence). Edge cases: completed session without seed → C; interrupted session without seed → B. These are pure-function invariants that must be locked by tests before the pipeline is wired live.
3. **Depends on prior-story state?** YES. Runs against `nova.db` containing Story 2.4's setup row — same prior state that Story 3.1 reconciles. `last_session` for a brand-new user = the setup session. Confirm: state determination produces `POST_SETUP` (not `FIRST_RUN`) when the setup session exists with `is_complete=1, seed_text=NULL`. That is the logic path that drives Briefing State B on session 2.

**SM prep requirements:** Apply A1 invariant sweep. Apply A9 degraded-path proof (happy: each state determined correctly; degraded: partial data — session without snapshot, snapshot without session, mode last_used for non-existent mode; rerun: state determination is stateless, same inputs → same outputs). Apply A10 — reference Story 3.1's reconciliation notes for the sessions-row state inherited from setup.

---

## Story 3.3 — BriefingViewModel & Briefing Card Rendering

**Classification:** ✅ Interaction-boundary story.

**A6 answers:**
1. **New contract between existing pieces?** YES. Ritual produces `BriefingViewModel`; Skin renders. This introduces the **first production use of the Ritual → Skin pipeline** — the State A render that currently lives in setup is scaffolded; this story makes Ritual the owner. Also introduces the "progressive omission" contract (fields with no data are omitted, not rendered as placeholders) which is a rendering-layer invariant the `BriefingViewModel` must encode.
2. **New invariants in degraded / partial-failure paths?** YES. `prose_enrichment=None` must render cleanly (no blank space, no "enrichment unavailable" text) — that is Epic 7's extension point and must not leak through in Epic 3. Also: `last_duration_display` is a pre-formatted render-safe string (not a raw `timedelta` crossing layers) — the serialization-at-boundary invariant.
3. **Depends on prior-story state?** YES. The existing direct-Rich-Panel State A render in [src/nova/setup/__main__.py](../../src/nova/setup/__main__.py) (`_render_state_a`) is scaffolding that this story replaces. Setup's `_render_state_a` must stay in place for the fast-path "setup already complete" panel (Story 2.4 AC #3), but the first-run State A path must route through the new Ritual → Skin pipeline. Two renderers must coexist without divergence in visible output.

**SM prep requirements:** Apply A1 invariant sweep. Apply A9 degraded-path proof (happy: A/B/C each render correctly; degraded: progressive omission for missing seed / missing last_mode / missing last_apps; rerun: same ViewModel → same rendered bytes). Apply A10 — document that setup's `_render_state_a` persists as the fast-path-complete panel and must not visually diverge from the new Ritual-assembled State A.

---

## Story 3.5 — Nerve Command Routing & Session Lifecycle

**Classification:** ✅ Interaction-boundary story. **Highest interaction-surface story in Epic 3.** Also: A3 fresh-session review target.

**A6 answers:**
1. **New contract between existing pieces?** YES — multiple. Nerve becomes the orchestrator that routes Skin `Command` objects to Brain / Hands / Ritual / Skin. New contracts: Skin → Nerve command submission, Nerve → Brain session lifecycle calls (pairs with Story 3.1's adapter), Nerve → TierManager tier-check-before-cloud-op (pairs with Story 1.7), Nerve → Ritual delegation for briefing + shutdown. Nerve also owns the signal-handler registration for unexpected-termination (SIGINT). Every Epic 1/2 system that was shipped as a stub or isolated module now connects through Nerve.
2. **New invariants in degraded / partial-failure paths?** YES. Tier-check before cloud ops — if OFFLINE, return honest unavailability without calling Claude. Skip-briefing policy — if last session ended < `briefing_recency_threshold_minutes` ago, skip briefing. Signal-handler "best-effort state capture" — the handler runs in a constrained context (no event loop guarantees) and must not itself raise. Session lifecycle: on bare `nova` boot, create session *before* briefing assembly (Brain write must succeed before Ritual reads "current session"). Partial-failure: briefing-assembly failure must not leave an orphan open session.
3. **Depends on prior-story state?** YES, and this is the critical one. Nerve wires `tier_manager.run_recovery_loop()` — which, with the current `_AlwaysHealthyCheck` stub from Story 1.7, flips OFFLINE → FULL on the first recovery tick. **Story 2.5's `test_tier_stays_offline_without_recovery_loop` smoke test explicitly locks the no-recovery-loop behavior** — if Story 3.5 wires the recovery loop without addressing the stub, that test will fail on the next run, *or worse*, will silently pass because the test mocks the recovery loop away. Story 3.5 must either (a) replace `_AlwaysHealthyCheck` with a real health probe before starting the recovery loop, or (b) explicitly update the Story 2.5 smoke test to document the new post-Nerve-wiring behavior, with a clear migration note. Silent breakage of the locked contract is the failure mode A10 exists to prevent.

**SM prep requirements:** Apply A1 invariant sweep in full — this story has the most lifecycle / teardown / concurrency / cancellation surface of any Epic 3 story. Apply A9 degraded-path proof (happy: bare boot → briefing → mode → shutdown; degraded: cloud op attempted while offline; rerun: crash recovery + warm resume). Apply A10 — explicit reconciliation with Story 2.5's `_AlwaysHealthyCheck` smoke test is mandatory; reference Story 3.1's Brain adapter as the session-lifecycle persistence layer.

**A3 fresh-session review trial:** Story 3.5 is the target. Format: standard same-session review first; then a fresh-session review with no implementation context; document both finding-sets and delta in `fresh-session-review-3.5-<date>.md`. Decision note on whether extra cost is worth the independence signal.

---

## References

- [epic-2-retro-2026-04-18.md](epic-2-retro-2026-04-18.md) — action items A1 / A3 / A6 / A9 / A10
- [3-1-brain-session-and-seed-persistence.md](3-1-brain-session-and-seed-persistence.md) — Story 3.1 prep (full A6 + A10)
- [epics.md: Epic 3](../planning-artifacts/epics.md#L1048-L1339) — full story AC tables
- [2-5-api-key-update-post-setup.md](2-5-api-key-update-post-setup.md) — source of the `_AlwaysHealthyCheck` smoke-test contract that Story 3.5 must reconcile

---

*Pre-flag file authored 2026-04-18 per Epic 2 retro. SM agent folds these A6 classifications into the full story file when each moves to ready-for-dev.*
