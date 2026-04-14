---
stepsCompleted: [step-01-document-discovery, step-02-prd-analysis, step-03-epic-coverage-validation, step-04-ux-alignment, step-05-architecture-cross-check, step-06-constraint-validation]
filesIncluded:
  prd: prd.md (589–731)
  architecture: architecture.md
  epics: epics.md (1905 lines, 55 stories, 8 epics)
  ux: ux-design-specification.md
  constraints: project-context.md (151 rules)
validationPriority:
  - PRD
  - Architecture
  - UX Design Specification
  - Epics
  - project-context.md (implementation guardrails)
runNumber: 2
runReason: "Re-run with explicit line-number citations for all story references"
---

# Implementation Readiness Assessment Report

**Date:** 2026-04-14
**Project:** AI Assistant (N.O.V.A.)
**Assessor:** Implementation Readiness Validator (re-run #2)
**Scope:** T1 ("Alive enough to use myself")

---

## Overall Verdict: PASS — Ready for T1 Implementation

All 60 FRs mapped (59 covered, 1 explicitly deferred). All 31 NFRs addressed. All 22 UX-DRs assigned. All 12 user-specified constraints validated. No blocking gaps. Every story reference below cites its epics.md line number — independently verifiable.

---

## Story Index (Verification Anchor)

Every story reference in this report maps to a `### Story X.Y` heading in `epics.md`. Full index with line numbers:

| Story | Line | Title |
|---|---|---|
| 1.0 | 621 | Define YAML Config Schemas (Spike) |
| 1.1 | 640 | Project Scaffolding & Package Setup |
| 1.2 | 657 | Domain Exceptions & Shared Types |
| 1.3 | 678 | Event Bus & Typed Event Definitions |
| 1.4 | 706 | SQLite Storage Engine |
| 1.5 | 726 | Migration Runner & Initial Schema |
| 1.6 | 753 | Config Loader & Immutable NovaConfig |
| 1.7 | 776 | Capability Tier State Machine |
| 1.8 | 798 | Audit Logger |
| 1.9 | 817 | Port Interfaces & Shield Stub |
| 1.10 | 833 | Composition Root & CLI Entrypoint |
| 1.11 | 866 | CI Quality-Gate Automation |
| 2.1 | 895 | Setup Script (setup.bat) |
| 2.2 | 915 | API Key Configuration |
| 2.3 | 933 | Guided Mode Creation Wizard |
| 2.4 | 951 | Briefing Card State A, Initial Capture & Setup Completion |
| 2.5 | 975 | API Key Update Post-Setup |
| 3.1 | 1000 | Brain Session & Seed Persistence |
| 3.2 | 1029 | BriefingAggregate & State Determination |
| 3.3 | 1059 | BriefingViewModel & Briefing Card Rendering |
| 3.4 | 1084 | T1 Command Grammar & Deterministic Parser |
| 3.5 | 1114 | Nerve Command Routing & Session Lifecycle |
| 3.6 | 1139 | Mode Restore & App Launching |
| 3.7 | 1165 | Shutdown Flow & Seed Capture |
| 3.8 | 1193 | Warm Resume (Session 2 Hero Moment) |
| 3.9 | 1216 | Status Command & Help Display |
| 3.10 | 1238 | Crash Recovery & Unexpected Termination |
| 4.1 | 1272 | Eyes Win32 Context Capture |
| 4.2 | 1297 | Exclusion Boundary at Capture Layer |
| 4.3 | 1320 | Workspace Snapshots on Demand & Context Buffer |
| 4.4 | 1338 | Context Extraction & Mode Inference |
| 4.5 | 1360 | Memory Accumulation & Enriched Briefings |
| 4.6 | 1383 | PromptBuilder Trust Boundary |
| 4.7 | 1408 | Bounded Memory Retention & Pruning Policy |
| 5.1 | 1437 | Transparency Command & Knowledge Display |
| 5.2 | 1464 | Selective Forget with Atomic Deletion |
| 5.3 | 1496 | Audit Trail Inspection |
| 5.4 | 1515 | Tier Status Display & Notification |
| 5.5 | 1533 | SQLite Corruption Recovery Flow |
| 5.6 | 1556 | Backup & Restore User-Facing Flow |
| 6.1 | 1582 | Window Focus & Arrange in Mode Restore |
| 6.2 | 1603 | Mode State Bookmarking on Switch |
| 6.3 | 1625 | Ad-Hoc Mode Creation |
| 6.4 | 1648 | Mode Editing via Command |
| 6.5 | 1668 | Full Mode Configuration — Folders & URLs |
| 7.1 | 1693 | Voice System & Personality Doctrine |
| 7.2 | 1715 | Configurable Bluntness Levels |
| 7.3 | 1733 | Strategic Praise & Context-Adaptive Style |
| 7.4 | 1755 | Self-Trimming Rituals |
| 8.1 | 1783 | Degraded Tier Integration Testing |
| 8.2 | 1806 | Offline Tier Integration Testing |
| 8.3 | 1826 | Tier Recovery & Catch-Up Briefing |
| 8.4 | 1843 | Malformed API Response Handling |
| 8.5 | 1863 | Partial Restore Under Degraded Conditions |
| 8.6 | 1883 | User & Developer Runbook |

**Total: 55 stories. Matches epics.md frontmatter `totalStories: 55` (line 8).**

---

## 1. PRD Analysis

### Functional Requirements: 60 FRs (prd.md lines 589–663)

| Category | FRs | Count |
|---|---|---|
| Setup & Onboarding | FR1–FR6 | 6 |
| Workspace Modes & Orchestration | FR7–FR13 | 7 |
| Context Awareness & Capture | FR14–FR18 | 5 |
| Memory & Persistence | FR19–FR24 | 6 |
| Session Rituals (Briefing & Shutdown) | FR25–FR31 | 7 |
| Transparency & Trust | FR32–FR38 | 7 |
| Desktop Actions & Automation | FR39–FR44 | 6 |
| Privacy & Data Protection | FR45–FR52 | 8 |
| Personality & Interaction | FR53–FR56 | 4 |
| System Management | FR57–FR60 | 4 |

### Non-Functional Requirements: 31 NFRs (prd.md lines 679–731)

| Category | NFRs | Count |
|---|---|---|
| Performance | NFR1–NFR7 | 7 |
| Security & Privacy | NFR8–NFR14 | 7 |
| Reliability | NFR15–NFR20 | 6 |
| Resource Efficiency | NFR21–NFR25 | 5 |
| Auditability & Transparency | NFR26–NFR30 | 5 |
| Backup & Recovery | NFR31 | 1 |

### PRD Completeness Assessment

Thorough, well-structured. All requirements numbered, testable, scoped to T1 vs. post-T1. Scope-cut ladder clear. Absolute floor defined. No ambiguous or contradictory requirements.

---

## 2. Epic Coverage Validation

### FR Coverage Matrix

Source: epics.md FR Coverage Map (lines 290–354). Cross-checked against story content.

| FR | Epic Coverage | Story (line) | Status |
|---|---|---|---|
| FR1 | Epic 2 | 2.1 (L895) | ✓ |
| FR2 | Epic 2 | 2.2 (L915) | ✓ |
| FR3 | Epic 2 | 2.3 (L933) | ✓ |
| FR4 | Epic 2 | 2.3 (L933) | ✓ |
| FR5 | Epic 2 | 2.4 (L951) | ✓ |
| FR6 | Epic 2 | 2.4 (L951) | ✓ |
| FR7 | Epic 3 | 3.6 (L1139) | ✓ |
| FR8 | Epic 6 | 6.5 (L1668) | ✓ |
| FR9 | Epic 3 | 3.6 (L1139) | ✓ |
| FR10 | Epic 6 | 6.2 (L1603) | ✓ |
| FR11 | Epic 6 | 6.3 (L1625) | ✓ |
| FR12 | Epic 6 | 6.4 (L1648) | ✓ |
| FR13 | Epic 3 | 3.9 (L1216) | ✓ |
| FR14 | Epic 4 | 4.1 (L1272) | ✓ |
| FR15 | Epic 4 | 4.3 (L1320) | ✓ |
| FR16 | Epic 4 | 4.4 (L1338) | ✓ |
| FR17 | Epic 4 | 4.3 (L1320) | ✓ |
| FR18 | Epic 4 | 4.4 (L1338) | ✓ |
| FR19 | Epic 1 + Epic 3 | 1.4 (L706), 1.5 (L726), 3.1 (L1000) | ✓ |
| FR20 | Epic 4 | 4.5 (L1360) | ✓ |
| FR21 | Epic 4 | 4.5 (L1360) | ✓ |
| FR22 | Epic 4 | 4.5 (L1360) | ✓ |
| FR23 | Epic 4 + Epic 5 | 4.5 (L1360), 5.6 (L1556) | ✓ |
| FR24 | Epic 1 | 1.5 (L726) | ✓ |
| FR25 | Epic 3 | 3.2 (L1029), 3.3 (L1059) | ✓ |
| FR26 | Epic 4 | 4.5 (L1360) | ✓ |
| FR27 | Epic 3 | 3.7 (L1165) | ✓ |
| FR28 | Epic 3 | 3.7 (L1165) | ✓ |
| FR29 | Epic 3 | 3.7 (L1165) | ✓ |
| FR30 | Epic 3 | 3.8 (L1193) | ✓ |
| FR31 | Epic 7 | 7.4 (L1755) | ✓ |
| FR32 | Epic 5 | 5.1 (L1437) | ✓ |
| FR33 | Epic 5 | 5.2 (L1464) | ✓ |
| FR34 | Epic 5 | 5.3 (L1496) | ✓ |
| FR35 | Epic 5 + Epic 8 | 5.4 (L1515), 8.1 (L1783) | ✓ |
| FR36 | Epic 5 + Epic 8 | 5.4 (L1515), 8.1 (L1783) | ✓ |
| FR37 | Epic 8 | 8.3 (L1826) | ✓ |
| FR38 | Epic 5 | 5.2 (L1464) | ✓ |
| FR39 | Epic 3 | 3.6 (L1139) | ✓ |
| FR40 | Epic 6 | 6.1 (L1582) | ✓ |
| FR41 | Epic 6 | 6.1 (L1582) | ✓ |
| FR42 | Epic 3 | 3.6 (L1139) | ✓ |
| FR43 | Deferred (v0.2+) | epics.md L336 | ⏸ Deferred |
| FR44 | Epic 3 | 3.8 (L1193) | ✓ |
| FR45 | Epic 4 | 4.6 (L1383) | ✓ |
| FR46 | Epic 4 | 4.6 (L1383) | ✓ |
| FR47 | Epic 4 | 4.2 (L1297) | ✓ |
| FR48 | Epic 4 | 4.2 (L1297) | ✓ |
| FR49 | Epic 4 | 4.2 (L1297) | ✓ |
| FR50 | Epic 4 | 4.2 (L1297) | ✓ |
| FR51 | Epic 5 | 5.2 (L1464) | ✓ |
| FR52 | Epic 4 | 4.6 (L1383) | ✓ |
| FR53 | Epic 7 | 7.1 (L1693) | ✓ |
| FR54 | Epic 7 | 7.3 (L1733) | ✓ |
| FR55 | Epic 7 | 7.2 (L1715) | ✓ |
| FR56 | Epic 7 | 7.3 (L1733) | ✓ |
| FR57 | Epic 1 (infra only) | 1.5 (L726) | ⚠ Partial — `nova self-update` NOT T1 |
| FR58 | Epic 1 + Epic 8 | 1.7 (L776), 8.1 (L1783) | ✓ |
| FR59 | Epic 8 | 8.1 (L1783) | ✓ |
| FR60 | Epic 3 + Epic 8 | 3.7 (L1165), 8.2 (L1806) | ✓ |

**59/60 covered. 1 deferred (FR43). 1 partial (FR57 — infra only, correct per T1 scope).**

### NFR Coverage Matrix

| NFR | Requirement | Story (line) | Status |
|---|---|---|---|
| NFR1 | Restore < 30s | 3.6 (L1139), 6.1 (L1582) | ✓ |
| NFR2 | Setup < 15 min | 2.4 (L951) | ✓ |
| NFR3 | Briefing < 5s | 3.8 (L1193) | ✓ |
| NFR4 | Shutdown < 30s | 3.7 (L1165) | ✓ |
| NFR5 | Poll < 100ms | 4.1 (L1272) | ✓ |
| NFR6 | Transparency < 3s | 5.1 (L1437) | ✓ |
| NFR7 | Claude < 3s | 7.1 (L1693) | ✓ |
| NFR8 | Local SQLite only | 1.4 (L706), 4.6 (L1383) | ✓ |
| NFR9 | Minimized prompts | 4.6 (L1383), 7.1 (L1693) | ✓ |
| NFR10 | Exclusion at capture | 4.2 (L1297) | ✓ |
| NFR11 | Deletion before transparency | 5.2 (L1464) | ✓ |
| NFR12 | User-readable SQLite | 1.4 (L706) | ✓ |
| NFR13 | No telemetry | 1.0 (L621) | ✓ |
| NFR14 | API key in settings.yaml | 2.2 (L915) | ✓ |
| NFR15 | Continuity loop reliable | 3.7 (L1165), 3.8 (L1193) | ✓ |
| NFR16 | Local ops without cloud | 3.6 (L1139), 8.2 (L1806) | ✓ |
| NFR17 | Tier transition < 5s | 1.7 (L776), 8.1 (L1783) | ✓ |
| NFR18 | Non-destructive migrations | 1.5 (L726) | ✓ |
| NFR19 | Crash recovery state capture | **3.10 (L1238)** | ✓ |
| NFR20 | No focus stealing | 3.6 (L1139), 6.1 (L1582) | ✓ |
| NFR21 | Memory < 750MB | 4.1 (L1272) | ✓ |
| NFR22 | CPU idle < 2% | 4.1 (L1272) | ✓ |
| NFR23 | SQLite < 100MB / 6mo | **4.7 (L1408)** | ✓ |
| NFR24 | No user app degradation | 4.1 (L1272) | ✓ |
| NFR25 | API cost < $2.50/mo | 7.1 (L1693) | ✓ |
| NFR26 | Every action logged | 1.8 (L798), 3.6 (L1139) | ✓ |
| NFR27 | Transparency = complete | 5.1 (L1437) | ✓ |
| NFR28 | Audit queryable | 5.3 (L1496) | ✓ |
| NFR29 | Deletion logged no content | 5.2 (L1464) | ✓ |
| NFR30 | Tier always accessible | 5.4 (L1515) | ✓ |
| NFR31 | Backup without tooling | 1.5 (L726), **5.6 (L1556)** | ✓ |

**All 31 NFRs addressed. The four previously challenged references bolded — all verified at cited line numbers.**

### UX-DR Coverage Matrix

| UX-DR | Requirement | Story (line) | Status |
|---|---|---|---|
| UX-DR1 | Briefing Card A/B/C | 2.4 (L951), 3.3 (L1059) | ✓ |
| UX-DR2 | Progressive Briefing | 3.3 (L1059) | ✓ |
| UX-DR3 | Command Response | 3.4 (L1084) | ✓ |
| UX-DR4 | Progress Indicator ✓/✗ | 3.6 (L1139), 6.1 (L1582) | ✓ |
| UX-DR5 | Shutdown Card | 3.7 (L1165) | ✓ |
| UX-DR6 | Knowledge Display | 5.1 (L1437) | ✓ |
| UX-DR7 | Tier Notice | 5.4 (L1515), 8.1 (L1783) | ✓ |
| UX-DR8 | Confirmation Prompt | 5.2 (L1464) | ✓ |
| UX-DR9 | T1 Command Grammar | 3.4 (L1084), 6.3 (L1625) | ✓ |
| UX-DR10 | Color system | 2.4 (L951) | ✓ |
| UX-DR11 | Typography hierarchy | 2.4 (L951), 3.3 (L1059) | ✓ |
| UX-DR12 | Personality Doctrine | 7.1 (L1693) | ✓ |
| UX-DR13 | Bluntness Calm/Direct | 7.2 (L1715) | ✓ |
| UX-DR14 | Strategic praise | 7.3 (L1733) | ✓ |
| UX-DR15 | Context-adaptive style | 7.3 (L1733) | ✓ |
| UX-DR16 | Ad-hoc mode creation | 6.3 (L1625) | ✓ |
| UX-DR17 | Critical error UX | 5.5 (L1533), 8.4 (L1843) | ✓ |
| UX-DR18 | Terminal-responsive | 2.4 (L951) | ✓ |
| UX-DR19 | Accessibility | 2.4 (L951) | ✓ |
| UX-DR20 | Feedback patterns | 3.6 (L1139) | ✓ |
| UX-DR21 | Empty input handling | 3.4 (L1084) | ✓ |
| UX-DR22 | Voice vs. Skin routing | 3.6 (L1139), 7.1 (L1693) | ✓ |

**All 22 UX-DRs assigned. No gaps.**

---

## 3. User-Specified Constraint Validation

### Constraint 1: T1 Scope Lock — PASS

Epics frontmatter (L24–30) locks: Windows 11 only, single-process, session-based, safe-only actions. No story introduces post-T1 scope.

### Constraint 2: Hero Path Sequencing — PASS

`heroPath` field (L23): `startup → briefing → mode → shutdown → warm resume`. Epic 3 (L996) is entirely the hero path. Story 3.8 (L1193) is the hero moment. Integration test at L1214 verifies the full loop.

### Constraint 3: Briefing Card A/B/C — PASS

Story 3.2 (L1029) defines state determination as a pure function:
- State A (L1053): no modes AND no sessions
- State B (L1054): no seed AND (no session OR incomplete)
- State C (L1055): seed exists OR completed session exists

Progressive omission enforced. BriefingAggregate → BriefingViewModel pipeline explicit.

### Constraint 4: T1 Command Grammar — PASS

Story 3.4 (L1084) defines all three layers. Layer A (L1096), Layer B (L1098–1102), Layer C (L1104–1106). Explicitly NOT T1 (L40): `nova audit`, `nova self-update`, `nova <mode>` shorthand.

### Constraint 5: PromptBuilder Only Cloud Egress — PASS

Story 4.6 (L1383): "No system may call the Claude adapter directly with ad hoc context" (L1400). project-context.md (L171): "No exceptions." Epic 5 (L500): "validated, not just assumed."

### Constraint 6: Brain Owns SQLite — PASS

Story 1.4 (L706): storage engine is the only sqlite3 importer (L722). Story 3.1 (L1000): Brain owns table access through port. project-context.md (L85): "No hidden persistence outside Brain."

### Constraint 7: Voice vs. Skin Routing — PASS

UX-DR22 defines the split. Story 3.6 (L1152–1153): progress direct to Skin. Story 7.1 (L1710): "operational output continues to bypass Voice." Consistent across all epics.

### Constraint 8: Privacy / Deletion / Transparency — PASS

Exclusion at capture: Story 4.2 (L1297). PromptBuilder strips: Story 4.6 (L1399). Atomic deletion: Story 5.2 (L1486). Transparency = SQLite: Story 5.1 (L1460).

### Constraint 9: Safe-Only Desktop Actions — PASS

Story 6.1 (L1594): "Only safe desktop actions ship in T1: launch, focus, arrange." (L1595): "No window closing, no file modification, no keyboard/mouse simulation." Fail-closed on ambiguity (L1597).

### Constraint 10: No User-Facing `self-update` — PASS

FR57 map (L350): "user-facing `nova self-update` is NOT T1." Epic 5 note (L519): explicit. Story 3.4 grammar (L1096–1102): not included.

### Constraint 11: FR43 Deferred — PASS

FR Coverage Map (L336): "Deferred (v0.2+)." Epic 6 (L1685): "FR43 is removed from T1 story scope." Story 6.1 (L1597): fail-closed handles boundary.

### Constraint 12: project-context.md Guardrails — PASS

All 151 rules compatible. Verified via: Story 1.1 (L640) Python 3.12/asyncio, Story 1.9 (L817) ports-and-adapters, Story 1.2 (L657) domain exceptions/enums, Story 1.5 (L726) no raw SQL, Story 1.10 (L833) structured logging, Story **1.11 (L866)** CI quality gate enforcing ruff/mypy/pytest.

---

## 4. Architecture Cross-Validation

### System Boundary Alignment — All 8 Consistent

| System | Epic(s) | Key Story (line) |
|---|---|---|
| Brain | 1, 3, 4, 5 | 3.1 (L1000) |
| Eyes | 4 | 4.1 (L1272) |
| Hands | 3, 6 | 3.6 (L1139), 6.1 (L1582) |
| Shield | 1 (stub) | 1.9 (L817) |
| Ritual | 3 | 3.2 (L1029), 3.7 (L1165) |
| Voice | 7 | 7.1 (L1693) |
| Skin | 2, 3 | 3.3 (L1059), 3.4 (L1084) |
| Nerve | 3 (primary) | 3.5 (L1114) |

### Key Architecture Decisions Verified

- Decision 3b (Briefing pipeline): Stories 3.2–3.3 (L1029–L1082)
- Tolerant degrade: Story 1.7 (L788) — 2+ consecutive failures
- Persist-before-emit: Story 3.7 (L1187), Story 5.2 (L1489)
- Adapters translate, never decide: Story 4.1 (L1289), Story 4.4 (L1356–1357)
- Composition root: Story 1.10 (L833)

---

## 5. Inter-Document Consistency — No Contradictions

| Document Pair | Result |
|---|---|
| PRD ↔ Architecture | ✓ Consistent |
| PRD ↔ Epics | ✓ All FRs mapped, deferrals justified |
| PRD ↔ UX Spec | ✓ UX-DRs operationalize PRD interaction requirements |
| Architecture ↔ Epics | ✓ System boundaries respected |
| Architecture ↔ project-context.md | ✓ project-context.md distills architecture rules |
| UX Spec ↔ Epics | ✓ All 22 UX-DRs assigned |
| project-context.md ↔ Epics | ✓ All guardrails in story ACs |

---

## 6. Supplementary Items

### 6.1 API Key Update Post-Setup

Covered by Story 2.5 (L975): "API Key Update Post-Setup." ACs include: edit settings.yaml directly (L986), help command surfaces path (L987), invalid key degrades gracefully (L989), missing key starts offline (L990).

### 6.2 Observations (Non-Blocking)

1. **ActionType enum**: Story 1.2 (L657) is authoritative for all values. Later stories reference subsets — no actual gap.
2. **FR60 dual-mapping**: Intentional — Epic 3 builds (L1165), Epic 8 tests under failure (L1806).
3. **PromptBuilder two-stage build**: Story 4.6 (L1383) = structural boundary. Story 7.1 (L1693) = personality layer.
4. **Retry queue**: Story 8.1 (L1797–1800) adds stricter constraints than PRD FR59 — bounded, in-memory, non-persistent. Good refinement.
5. **55 stories**: Substantial for solo evening dev. PRD scope-cut ladder (prd.md L524–534) remains the safety valve.

---

## 7. Final Assessment

| Dimension | Result |
|---|---|
| FR Coverage | 59/60 covered, FR43 deferred (justified) |
| NFR Coverage | 31/31 addressed |
| UX-DR Coverage | 22/22 assigned |
| T1 Scope Lock | Enforced — no scope creep |
| Hero Path | Complete end-to-end |
| Briefing A/B/C | 3-state with explicit conditions |
| Command Grammar | Locked, 3-layer, edge cases tested |
| Trust/Privacy | Multi-layer enforcement |
| Architecture | 8 systems consistent |
| project-context.md | 151 rules, no contradictions |
| Inter-Document | No contradictions across 5 docs |
| Story References | All 55 verified with line numbers |

### Verdict

**PASS — Implementation of T1 may proceed starting at Epic 1.**

All story references independently verifiable via the line-number citations above. No phantom references. No missing stories. No uncovered requirements.

Recommended order: Epic 1 → Epic 2 → Epic 3 → Epics 4/5/6/7 (parallel) → Epic 8.
