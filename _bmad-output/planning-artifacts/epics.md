---
stepsCompleted:
  - "step-01-validate-prerequisites"
  - "step-02-design-epics"
  - "step-03-create-stories"
  - "step-04-final-validation"
status: "complete"
totalStories: 55
totalEpics: 8
validationResult: "PASS — all 60 FRs, 31 NFRs, 22 UX-DRs covered; FR43 deferred to v0.2+"
completedAt: "2026-04-14"
inputDocuments:
  - "prd.md"
  - "architecture.md"
  - "ux-design-specification.md"
  - "project-context.md"
documentPriority:
  - "PRD — product scope and requirements"
  - "Architecture — system design, boundaries, T1 scope, schema, event flows"
  - "UX Design Specification — interaction model, components, flows"
  - "project-context.md — implementation guardrails, coding/testing/workflow/privacy rules"
scopeConstraint: "T1 only"
heroPath: "startup → briefing → mode → shutdown → warm resume"
lockedConstraints:
  t1ScopeLock:
    - "Windows 11 only"
    - "Single-process modular monolith"
    - "Session-based only — no background daemon"
    - "Safe-only desktop actions (launch, focus, arrange)"
    - "No starter template; custom foundation at Epic 1 / Story 1"
  briefingContracts:
    - "3-state model: A (First Run) / B (Post-Setup) / C (Warm Resume)"
    - "State determination rules per Architecture Decision 3b"
    - "BriefingAggregate → BriefingViewModel source/view-model contract (Brain → Nerve → Ritual → Skin)"
    - "Progressive omission: no hollow placeholders, no fake history"
  commandGrammar:
    - "3 layers: launch commands, in-session commands, contextual replies"
    - "nova bare invocation included"
    - "resume/yes/no/skip/cancel are contextual only, not global"
    - "nova audit, nova self-update, nova <mode> shorthand are NOT T1"
    - "Invalid/partial/empty input behaviors must be acceptance-tested"
  trustPrivacy:
    - "PromptBuilder is the ONLY cloud egress path"
    - "No hidden secondary memory stores"
    - "Deletion must be atomic from user perspective"
    - "Transparency must reflect complete truth within privacy boundary"
    - "Excluded context opaque across capture, storage, audit, transparency, logs, errors, derived text"
  architectureInStories:
    - "Brain owns SQLite"
    - "Persist-before-emit"
    - "Adapters translate, never decide"
    - "Voice writes prose, Skin renders"
    - "Nerve orchestrates policy, Ritual owns ceremony logic"
    - "Fallback behavior preserves structure across full/degraded/offline tiers"
  epicSequencing:
    - "Hero path first: startup → briefing → mode → shutdown → warm resume"
---

# AI Assistant (N.O.V.A.) - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for N.O.V.A., decomposing the requirements from the PRD, UX Design Specification, Architecture, and project-context.md into implementable stories scoped to T1 ("Alive enough to use myself").

## Requirements Inventory

### Functional Requirements

**1. Setup & Onboarding**
FR1: User can install N.O.V.A. by running a single guided setup script that handles environment, dependencies, and initial configuration
FR2: User can configure their Claude API key during guided setup with validation that the key works
FR3: User can create workspace modes during first-run setup through an interactive wizard that asks for mode name, apps, folders, URLs, and behavior flags
FR4: System provides at least one starter mode template during first-run setup (e.g., "coding", "study"), with ability to accept, modify, or skip
FR5: System can capture an initial workspace snapshot (open apps, active windows, focused project) during first-run setup
FR6: User can complete guided setup in under 15 minutes on a supported Windows 11 machine

**2. Workspace Modes & Orchestration**
FR7: User can switch between workspace modes with a single command
FR8: User can define multiple workspace modes, each with its own app set, folder/project associations, URLs, and behavior flags
FR9: System can restore a workspace mode by launching configured apps, focusing the right windows, and setting the mode state
FR10: System can bookmark the current mode state when the user switches to a different mode, preserving what was open and where they left off
FR11: User can create new workspace modes on the fly during an active session
FR12: User can edit existing workspace modes via command or by directly editing the local config file
FR13: User can view all configured workspace modes and their contents

**3. Context Awareness & Capture**
FR14: System can detect the active foreground window, its title, and the owning process during active sessions
FR15: System can track window/app context changes and maintain a recent context buffer
FR16: System can extract meaningful context from window titles (e.g., VS Code project name, browser page title, document name)
FR17: System can capture workspace state on demand or during shutdown (active apps, window list, current mode, focused window)
FR18: System can infer a likely workspace mode from open apps and active context, and suggest it to the user

**4. Memory & Persistence**
FR19: System can store session data, user preferences, workspace states, and accumulated context in a local SQLite database on the user's machine
FR20: System can accumulate knowledge across sessions — projects worked on, mode usage patterns, decisions recorded, recurring contexts
FR21: System can retrieve relevant prior session context when generating briefings or responding to user queries
FR22: System can detect usage patterns over time (e.g., typical mode by day of week, recurring project focus, session timing)
FR23: User can back up their memory database by copying a single local file
FR24: System can create automatic timestamped backups of the memory database before schema migrations

**5. Session Rituals (Briefing & Shutdown)**
FR25: System can present a session briefing on return that surfaces: last session's tomorrow seed, last active mode, recent context, and relevant prior session information
FR26: Session briefings can improve in relevance over time as memory accumulates (day 1 vs day 7 vs day 30 progression)
FR27: User can initiate a shutdown flow that captures current state, progress summary, and a "tomorrow seed" note
FR28: User can write a tomorrow seed — a short note to their future self about what to pick up next
FR29: System can capture shutdown state including active mode, open apps, session notes, and the tomorrow seed
FR30: System can surface the tomorrow seed from the previous session during the next session's briefing
FR31: System can suppress or de-emphasize ritual elements that repeatedly go unused, without permanently deleting them unless the user chooses to

**6. Transparency & Trust**
FR32: User can ask "What do you know right now?" and receive a complete, structured view of all stored knowledge — modes, project history, patterns detected, session seeds, and accumulated context
FR33: User can selectively forget specific topics, projects, or data points (e.g., "Forget Meridian") with deletion propagated across all stored and derived representations
FR34: User can inspect the audit trail of automated actions N.O.V.A. has taken
FR35: System can communicate its current capability tier (full / degraded / offline-local-only) honestly when conditions change
FR36: System can explain what it can and cannot do in the current capability tier
FR37: System can recover from cloud API outages and offer a briefing catch-up when connectivity is restored
FR38: User can verify the result of a forget/delete action through the transparency command immediately after deletion

**7. Desktop Actions & Automation**
FR39: System can launch applications configured in workspace modes
FR40: System can focus (bring to foreground) a running application window
FR41: System can arrange windows in basic layouts as part of mode restoration
FR42: All automated desktop actions are logged in an audit trail
FR43: Any action beyond the safe tier (launch, focus, arrange) requires explicit user confirmation before execution
FR44: User can invoke a context resume command that restores the last workspace mode, launches the configured app set, and surfaces session notes

**8. Privacy & Data Protection**
FR45: All personal memory and workspace data is stored locally on the user's machine — never in the cloud
FR46: System can distinguish between local-only data and cloud-eligible derived context, sending only minimized summaries to Claude API
FR47: System can maintain a sensitive-context exclusion list (password managers, banking apps, health portals, incognito windows, user-flagged apps) with sensible defaults
FR48: System can treat excluded app contexts as opaque — detecting that an app is focused without capturing identifying details
FR49: Excluded contexts are omitted from memory, cloud reasoning, pattern detection, session briefings, and transparency summaries (shown only as generic opaque placeholders)
FR50: User can inspect and modify the sensitive-context exclusion list
FR51: When deletion is requested, system can remove the target from raw entries, summaries, embeddings, bookmarks, seeds, and persisted cached context — audit trail logs the deletion event without preserving deleted content
FR52: If the system cannot safely minimize or classify a piece of context for cloud reasoning, it falls back to local-only behavior or asks the user before proceeding

**9. Personality & Interaction**
FR53: System can respond with a consistent personality (sharp, loyal, witty) that follows N.O.V.A.'s behavioral doctrine
FR54: System can adapt interaction style based on context — concise during work sessions, more detailed when asked for explanation
FR55: System can provide honest, direct feedback about user behavior when contextually appropriate (e.g., "That's procrastination dressed as research") with user-controllable bluntness levels
FR56: System can mark meaningful progress moments with strategic, earned praise — rare enough to mean something

**10. System Management**
FR57: User can trigger a self-update command that checks for new versions, backs up the memory database, and applies updates with visible migration notes
FR58: System can operate in three explicit capability tiers (full / degraded / offline-local-only) based on cloud API availability, with local operations never depending on cloud connectivity
FR59: System can queue cloud reasoning requests during intermittent connectivity and retry when available (degraded tier)
FR60: User can access previously stored briefings and session notes verbatim during degraded or offline operation

### NonFunctional Requirements

**Performance**
NFR1: Workspace restore (mode switch + app launch) completes in < 30 seconds
NFR2: Guided first-run setup completes in < 15 minutes end-to-end
NFR3: Session briefing generation completes in < 5 seconds after N.O.V.A. starts
NFR4: Shutdown flow completion takes < 30 seconds of active user time
NFR5: Active window context detection completes in < 100ms per poll cycle
NFR6: Transparency command response completes in < 3 seconds
NFR7: Claude API round-trip (with prompt caching) completes in < 3 seconds typical for conversational responses

**Security & Privacy**
NFR8: All personal memory and workspace data must be stored in a local SQLite file with no network transmission of raw memory content
NFR9: Claude API prompts must contain only minimized, derived context — never full memory stores, raw audit logs, raw audio, or sensitive-context data
NFR10: Sensitive-context exclusion must be enforced at the capture layer — excluded app data must never reach memory, cloud reasoning, or briefing generation
NFR11: Deletion propagation must complete fully before the transparency command can be invoked post-deletion — partial deletion states must not be visible to the user
NFR12: The SQLite memory file must be readable and verifiable by the user using standard SQLite tools (no proprietary encryption that prevents user inspection)
NFR13: No telemetry, usage analytics, or crash reporting transmitted without explicit user opt-in
NFR14: API key must be stored locally in a protected configuration file, not embedded in source code or transmitted beyond Claude API authentication

**Reliability**
NFR15: The continuity loop (shutdown seed → resume) must be highly reliable; failures in shutdown seed capture or resume are critical-severity defects
NFR16: Local operations (memory reads, mode switching, workspace restore, transparency command) must function without cloud API connectivity — no single points of failure for the core loop
NFR17: Capability tier transitions (full → degraded → offline) must be detected and communicated within 5 seconds of connectivity change
NFR18: Schema migrations must be non-destructive — automatic backup before migration, rollback path if migration fails, no data loss under any migration scenario
NFR19: Graceful shutdown must capture state even on unexpected termination (e.g., system crash, power loss) to the extent possible — at minimum, the last known good state should be recoverable
NFR20: N.O.V.A. must not interfere with the user's active work — no modal dialogs that block input, no stealing focus from the user's current application, no actions that modify the user's files or documents

**Resource Efficiency**
NFR21: Total runtime memory footprint should target under 750MB during an active session on a system with 16GB RAM
NFR22: CPU usage during idle active session (polling, no active interaction) must remain under 2% on a mid-range processor
NFR23: SQLite database size must remain manageable — target under 100MB after 6 months of daily use with typical session patterns
NFR24: N.O.V.A. must not noticeably degrade the performance of the user's primary work applications
NFR25: Claude API cost must remain under $2.50/month at 50 conversational turns per day with prompt caching enabled

**Auditability & Transparency**
NFR26: Every automated desktop action (app launch, window focus, window arrange) must be logged with timestamp, action type, target, and result
NFR27: The transparency command must show a complete, accurate representation of all stored knowledge — no hidden state, no omitted categories, no stale cache presented as current
NFR28: Audit trail must be queryable by the user — at minimum, viewable through N.O.V.A. commands, ideally also inspectable in the SQLite file directly
NFR29: Deletion events must be logged in the audit trail (what was deleted, when, by user request) without preserving deleted content
NFR30: Capability tier status must always be accessible to the user and proactively surfaced when it changes

**Backup & Recovery**
NFR31: User must be able to back up and restore N.O.V.A.'s local memory and state without specialized tooling — documented commands or file copy must be sufficient

### Additional Requirements

**From Architecture — T1 Scope Lock:**
- No starter template applies; project foundation is custom-built (Python 3.12+, asyncio, Rich, SQLite, pywin32, Anthropic SDK)
- Modular monolith: 8 named systems (Brain, Eyes, Hands, Shield-stubbed, Ritual, Voice, Skin, Nerve) in single asyncio process
- Ports-and-adapters is load-bearing — every system defines abstract port + concrete adapter; all wiring in app.py
- PromptBuilder is a separate trust-boundary component between Brain and Claude adapter (core/prompt_builder.py)
- Event bus for all inter-system communication — typed frozen dataclasses in core/events.py
- SQLite schema: sessions, workspace_snapshots, memory_items, audit_log, schema_version tables
- File-based config: modes/*.yaml, exclusions.yaml, settings.yaml in %LOCALAPPDATA%/nova/
- Config module (core/config.py) is the single YAML reader — no system reads config directly
- Migration runner with auto-backup before every schema migration
- AuditLogger as single audit interface — no direct audit_log writes
- Three capability tiers (full/degraded/offline) as architecture-level behavior with tolerant degrade model
- Write-then-emit rule: persist before emitting durable-fact events
- Shield system stubbed (port interface defined, no-op adapter)
- T1 Commands: Layer A (launch), Layer B (in-session), Layer C (contextual responses)
- T1 Personality: Calm and Direct bluntness only; Ruthless deferred to T2
- BriefingAggregate → BriefingViewModel data contract defined (Decision 3b)
- Briefing Card State Contract: three distinct states (A/B/C) with explicit conditions
- Domain exceptions only — never let adapter-specific exceptions cross port boundary
- Structured logging to file only — terminal is Skin's domain
- Project structure defined to individual file level (src/nova/, tests/, config/)
- User data directory: %LOCALAPPDATA%/nova/ (DB, settings, modes, exclusions, backups, logs)
- setup.bat as first-run entrypoint; uv run nova for subsequent sessions

**From Architecture — Implementation Sequence:**
- 1. Project scaffolding (uv init, directory structure, pyproject.toml, core infrastructure)
- 2. Core event bus + tier state machine (Nerve foundation)
- 3. SQLite storage engine + migration runner (core/storage)
- 4. File-based config loader (modes, exclusions, settings)
- 5. Eyes adapter (win32gui polling, exclusion filtering)
- 6. Brain + memory_items + sessions tables
- 7. Hands adapter (app launch, focus, arrange)
- 8. Ritual (shutdown flow + seed capture + briefing assembly)
- 9. Voice (basic personality text generation via Claude)
- 10. PromptBuilder (basic minimization)
- 11. Skin (Rich components: Briefing Card, Progress Indicator, Shutdown Card, Command Response)
- 12. Transparency command (Knowledge Display via Brain query)
- 13. Composition root (app.py wires everything)
- 14. CLI entrypoint + guided first-run setup

**From project-context.md — Implementation Guardrails:**
- Python 3.12.x fully typed, asyncio-based, single-process
- Type annotations on everything; X | None not Optional[X]; list[str] not List[str]
- @dataclass(frozen=True) for immutable value objects
- No raw string event types — all typed classes in core/events.py
- Domain exceptions in core/exceptions.py — adapter exceptions never cross port boundary
- No raw SQL outside migrations
- Enum for constrained values (BriefingState, CapabilityTier, SnapshotType, ActionType, MemoryCategory, BluntnessLevel)
- Absolute imports only between systems
- No print() anywhere — terminal through Skin, debugging through logging
- Timezone-aware UTC datetimes; localize at Skin only
- No Any in application code
- Typed boundary parsing for all external payloads
- Timeouts required at external boundaries
- pathlib.Path for filesystem code
- No mutable default values
- Test structure mirrors src; unit tests use mock adapters; integration tests use real SQLite
- Test the boundaries independently (Brain → Nerve → Ritual → Skin)
- Deterministic clock and IDs required for tests
- Ruff for linting/formatting, mypy strict on src/nova/
- uv as package manager; all tooling via uv run
- pyproject.toml as single config source
- setup.bat idempotent; user data in %LOCALAPPDATA%/nova/
- No hidden secondary memory stores
- PromptBuilder is the only cloud egress path
- Safety boundaries fail closed
- Audit logging is observational, not transactional
- Operational success messages must reflect actual completion state

### UX Design Requirements

UX-DR1: Implement Briefing Card component with three distinct render states (A: First Run, B: Post-Setup, C: Warm Resume) using Rich Panel — each with explicit conditions per the State Contract. Never render a hollow template with empty fields.
UX-DR2: Implement Progressive Briefing design — panel content grows as memory accumulates. Day-1 is compact/honest; day-30 includes patterns and project threads. Self-trimming: omit fields with no data entirely.
UX-DR3: Implement Command Response component — plain text, no panel, personality in words not formatting. States: standard, action confirmation (green ✓), error (red + explanation), unavailable (amber + alternative).
UX-DR4: Implement Progress Indicator component — per-item ✓/✗ feedback for multi-step operations (workspace restore, mode switch). Graceful-partial pattern: failures don't block. Voice-final-line summary.
UX-DR5: Implement Shutdown Card component — Rich Panel framing session summary + one seed question ("What should you pick up tomorrow?"). User types below panel. Confirmation outside panel. Under 30 seconds active time.
UX-DR6: Implement Knowledge Display component — Rich Panel with Tree structure for transparency command. Categories: Modes, Memory, Session (with tier). Excluded items shown as opaque placeholders only. Ends with "Want me to forget anything?"
UX-DR7: Implement Tier Notice component — single amber warning line + capability list. No panel. Shown once on change, not repeated. States: degraded, offline, restored.
UX-DR8: Implement Confirmation Prompt component — shows preview of what will happen, binary y/n. Used only for genuinely sensitive actions (forget confirmation, not safe actions).
UX-DR9: Implement the complete T1 Command Grammar — three layers (Launch/In-Session/Contextual). Deterministic parsing in Skin; NLP intent resolution through Nerve/Voice. Case-insensitive. Partial command guidance. Invalid input shows max 3 context-relevant suggestions.
UX-DR10: Implement the color system with semantic roles — Primary cyan (#5FB4D9), Success green (#5FBF7F), Warning amber (#D9A55F), Error red (#D95F5F), Muted gray (#6B6B6B), Emphasis bright white (#E8E8E8), Body soft white (#C0C0C0). Truecolor preferred, 256-color fallback. Console(color_system="auto").
UX-DR11: Implement typography hierarchy — H1 (bold cyan panel titles), H2 (bold white section headers), Body (soft white), Emphasis (bold bright white), Metadata (dim gray), Status markers (semantic color + symbol). Bold is the only weight tool — no italics, no underline.
UX-DR12: Implement the N.O.V.A. Personality Doctrine in Voice system — prohibited patterns (never "How can I help you today?", never sycophantic framing), required patterns (brevity default, direct address, earned familiarity, honest failure, user agency). Claude system prompt must encode these rules.
UX-DR13: Implement bluntness levels (Calm and Direct for T1) — configurable in settings.yaml, affects phrasing not observation selection. Ruthless deferred to T2.
UX-DR14: Implement strategic praise system — triggers on multi-session completion, sustained focus, shipping. Max once per session. Short phrases ("Clean work."), no special formatting. Zero praise is normal.
UX-DR15: Implement context-adaptive response style in Voice — minimal during flow, structured during briefing, one-question during shutdown, detailed on explanation request, honest on failure.
UX-DR16: Implement ad-hoc mode creation flow — explicit entry (`mode create`) and implicit entry (user tries nonexistent mode). 2-3 questions, natural app name resolution, immediate YAML write, offer to switch. Cancellation exits cleanly with no partial mode saved.
UX-DR17: Implement three critical error scenario UX flows — (1) SQLite missing/corrupted: explicit user-facing recovery with options, (2) malformed API response: inline one-line fallback notice, (3) partial workspace restore: per-app ✓/✗ with actionable failure reasons.
UX-DR18: Implement terminal-responsive layout — design for 80 columns minimum, benefit from more. Rich auto-sizing, no hardcoded widths. No horizontal scrolling. Tables truncate gracefully.
UX-DR19: Implement accessibility patterns — color never sole indicator (always paired with ✓/✗/⚠ symbols), plain text readability (output understandable with styling stripped), WCAG AA equivalent contrast, keyboard-only by design.
UX-DR20: Implement feedback patterns consistently — Success (✓ green), Failure (✗ red), Warning (⚠ amber), Info (body white), Metadata (dim gray). Symbol + color together always. Warnings shown once.
UX-DR21: Implement empty input handling per context — free command: silent no-op; directed non-destructive: skip; destructive prompt: reprompt once then cancel.
UX-DR22: Implement Voice vs. Skin output routing — personality-bearing responses (briefings, summaries, explanations, failure messages, earned praise) route through Voice → Skin. Operational output (progress lines ✓/✗, tier notices, confirmations, status tables, transparency trees) goes direct to Skin bypassing Voice.

### FR Coverage Map

| FR | Epic | Brief Description |
|---|---|---|
| FR1 | Epic 2 | Guided setup script |
| FR2 | Epic 2 | API key configuration with validation |
| FR3 | Epic 2 | Mode creation via wizard |
| FR4 | Epic 2 | Starter workspace-mode templates (not project scaffolding) |
| FR5 | Epic 2 | Initial workspace snapshot |
| FR6 | Epic 2 | Setup <15 minutes |
| FR7 | Epic 3 | Single-command mode switch |
| FR8 | Epic 6 | Full multi-mode with all config options |
| FR9 | Epic 3 | Restore mode — launch apps |
| FR10 | Epic 6 | Bookmark mode state on switch |
| FR11 | Epic 6 | Create modes on the fly |
| FR12 | Epic 6 | Edit modes via command or file |
| FR13 | Epic 3 | View all configured modes |
| FR14 | Epic 4 | Foreground window detection |
| FR15 | Epic 4 | Context change tracking |
| FR16 | Epic 4 | Extract context from window titles |
| FR17 | Epic 4 | Workspace state capture on demand |
| FR18 | Epic 4 | Mode inference from context |
| FR19 | Epic 1 (storage engine + schema) + Epic 3 (session/seed persistence) | Local SQLite storage |
| FR20 | Epic 4 | Accumulate knowledge across sessions |
| FR21 | Epic 4 | Retrieve prior context for briefings |
| FR22 | Epic 4 | Detect usage patterns |
| FR23 | Epic 4 | Backup = file copy |
| FR24 | Epic 1 | Timestamped backup before migration |
| FR25 | Epic 3 | Session briefing on return |
| FR26 | Epic 4 | Briefings improve over time with memory |
| FR27 | Epic 3 | Shutdown flow |
| FR28 | Epic 3 | Tomorrow seed |
| FR29 | Epic 3 | Shutdown state capture |
| FR30 | Epic 3 | Surface seed at next briefing |
| FR31 | Epic 7 | Self-trimming rituals |
| FR32 | Epic 5 | Transparency command |
| FR33 | Epic 5 | Selective forget with propagation |
| FR34 | Epic 5 | Inspect audit trail |
| FR35 | Epic 5 (user-facing) + Epic 8 (integration testing) | Communicate tier honestly |
| FR36 | Epic 5 (user-facing) + Epic 8 (integration testing) | Explain tier capabilities |
| FR37 | Epic 8 | Recovery + catch-up briefing |
| FR38 | Epic 5 | Verify forget result immediately |
| FR39 | Epic 3 | Launch apps for mode restore |
| FR40 | Epic 6 | Focus running window (safe-only) |
| FR41 | Epic 6 | Arrange windows (safe-only) |
| FR42 | Epic 3 | Audit all desktop actions |
| FR43 | Deferred (v0.2+) | Confirmation for beyond-safe actions — T1 ships safe-only with fail-closed behavior; real confirmation gate belongs to v0.2+ when action model expands |
| FR44 | Epic 3 | Context resume command |
| FR45 | Epic 4 | All data local |
| FR46 | Epic 4 | PromptBuilder — minimized summaries to cloud |
| FR47 | Epic 4 | Exclusion list with defaults |
| FR48 | Epic 4 | Excluded apps as opaque |
| FR49 | Epic 4 | Excluded contexts omitted from all surfaces |
| FR50 | Epic 4 | Inspect/modify exclusion list |
| FR51 | Epic 5 | Deletion from all representations |
| FR52 | Epic 4 | Fallback to local if cannot safely minimize |
| FR53 | Epic 7 | Consistent personality |
| FR54 | Epic 7 | Context-adaptive style |
| FR55 | Epic 7 | Honest feedback + configurable bluntness |
| FR56 | Epic 7 | Strategic earned praise |
| FR57 | Epic 1 (migration runner infra) — user-facing `nova self-update` is NOT T1 | Self-update infrastructure |
| FR58 | Epic 1 (tier state machine infra) + Epic 8 (full system behavior) | Three capability tiers |
| FR59 | Epic 8 | Queue/retry in degraded tier |
| FR60 | Epic 3 (implementation) + Epic 8 (degraded/offline integration testing only) | Stored notes accessible verbatim offline |

**All 60 FRs mapped. No orphans. FR60 intentionally dual-mapped: Epic 3 builds, Epic 8 tests under failure.**

## Epic List

### Epic 1: Project Foundation & Core Infrastructure

The developer (or AI agent) can scaffold, build, and run the N.O.V.A. project with all core plumbing operational. No user-facing features yet, but every subsequent epic builds on this foundation without rewiring.

**What ships:**
- Project scaffolding: `uv init`, directory structure per architecture spec, `pyproject.toml` with all T1 dependencies, `.gitignore`, `.python-version`
- **Story 0 / Spike: Define and pin YAML config schemas** — mode schema (`modes/*.yaml`), exclusion schema (`exclusions.yaml`), settings schema (`settings.yaml`) with field definitions, validation rules, and defaults. Every epic from 2 onwards touches config files; schemas must be locked here to prevent drift across agents.
- Core event bus (`core/events.py`) — typed frozen dataclass events, in-process async delivery, ordered, best-effort, no persistence
- Capability tier state machine (`core/tiers.py`) — full/degraded/offline, tolerant degrade model, health check infrastructure
- SQLite storage engine (`core/storage/engine.py`) — connection management, backup-before-migrate enforcement
- Migration runner (`core/storage/migrations/runner.py`) + `001_initial_schema.py` — sessions, workspace_snapshots, memory_items, audit_log, schema_version tables
- Automatic startup migrations (check pending, auto-backup, apply)
- Config loader (`core/config.py`) — loads modes, exclusions, settings from `%LOCALAPPDATA%/nova/` into immutable `NovaConfig` dataclass. Single YAML reader for the whole project.
- Domain exceptions (`core/exceptions.py`)
- Audit logger (`core/audit.py`) — single audit interface, append-only
- Shared domain types (`core/types.py`)
- All 8 port interfaces (`ports/*.py`) including Shield (stubbed)
- Composition root (`app.py`) — wires ports to adapters, boots the monolith
- CLI entrypoint skeleton (`cli.py`) — argument parsing, session lifecycle shell
- Shipped defaults (`config/`) — default mode templates, exclusion defaults, settings defaults (no API key)
- Shield no-op stub (`systems/shield/system.py`)

**FRs covered:** FR19 (partial — storage engine + schema), FR24 (timestamped backup before migration), FR57 (partial — migration runner infrastructure only; user-facing `nova self-update` is NOT T1), FR58 (partial — tier state machine infrastructure)

**NFRs addressed:** NFR8 (local SQLite only), NFR12 (user-readable SQLite), NFR13 (no telemetry), NFR18 (non-destructive migrations with auto-backup), NFR31 (backup = file copy)

**Architecture constraints enforced from this epic forward:**
- Ports-and-adapters: every system defines abstract port + concrete adapter; all wiring in `app.py`
- No system imports another system's adapter
- Domain exceptions only — adapter exceptions never cross port boundary
- No raw SQL outside migrations
- All events are typed classes in `core/events.py`
- Config module is the single YAML reader
- No `print()` anywhere — terminal through Skin, debugging through logging
- Python 3.12+ fully typed, asyncio, `@dataclass(frozen=True)` for immutable value objects
- All project-context guardrails enforced

---

### Epic 2: First-Run Setup & Onboarding

A new user clones the repo, runs `setup.bat`, configures their API key, creates their first workspace mode through a guided wizard with starter workspace-mode templates (not project scaffolding — the custom project foundation was built in Epic 1), and captures an initial workspace snapshot. Setup completes in under 15 minutes. The session ends at Briefing Card State A (first-run orientation) which auto-transitions into the wizard.

**Clarification:** "Starter templates" here means workspace-mode templates only (e.g., "coding" mode with VS Code + Chrome + Terminal). No project starter template exists — Epic 1 / Story 1 builds the custom foundation from scratch.

**What ships:**
- `setup.bat` — idempotent Windows setup script (prerequisites check, uv sync, user data dir creation, first-run wizard launch)
- First-run wizard (`setup/wizard.py`) — API key prompt with validation, starter workspace-mode templates offered, guided mode creation, initial workspace capture
- Briefing Card State A rendering — "First session. No history yet." + auto-transition to setup wizard
- User data directory creation (`%LOCALAPPDATA%/nova/`) with shipped defaults copied (modes, exclusions, settings)
- API key stored in `settings.yaml`, never in source

**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6

**NFRs addressed:** NFR2 (<15 min setup), NFR14 (API key in settings.yaml, never in source)

**UX-DRs addressed:** UX-DR1 (State A render only), UX-DR10 (color system — first visual output), UX-DR11 (typography hierarchy), UX-DR18 (terminal-responsive from the start), UX-DR19 (accessibility patterns — symbols + color, plain text readable)

---

### Epic 3: Core Session Loop (Hero Path)

The full continuity loop fires end-to-end: `nova` → Briefing Card (State B or C) → mode command → apps launch → work → `shutdown` → seed captured → next session resumes warm. This is the product proof. A user can run N.O.V.A. on day 1, shut down with a seed, return on day 2, and experience the hero moment: "It remembered where I left off."

**What ships:**
- `nova` bare invocation boots the app and renders the Briefing Card
- **Briefing bridge contracts (explicitly implemented, not implicit):**
  - `BriefingAggregate` — Brain loads and assembles from sessions, snapshots, memory_items, mode config
  - State determination logic — Nerve evaluates A/B/C (first match wins per Architecture Decision 3b)
  - `BriefingViewModel` — Ritual assembles render-ready view model from aggregate + state + tier
  - Brain → Nerve → Ritual → Skin handoff — each boundary independently testable
  - Progressive omission rules — fields with no data are omitted entirely; no hollow placeholders, no fake history
- Briefing Card renders all 3 states:
  - **State A:** "N.O.V.A." title, first-run orientation, auto-transition to setup wizard
  - **State B:** "Session Briefing" title, no seed, available modes, start suggestion
  - **State C:** "Session Briefing" title, seed (hero line), last mode/timing/apps, resume suggestion
- T1 command grammar (core subset):
  - Layer A: `nova`, `nova mode <name>`, `nova status`, `nova help`
  - Layer B: `mode <name>`, `mode`/`modes`, `status`, `help`/`?`, `shutdown`/`quit`/`exit`
  - Layer C (contextual only): `resume`, `yes`, `no`, `skip`, `cancel`
  - Deterministic parsing in Skin — same input always produces same Command object
  - Invalid input: max 3 suggestions, never "invalid command"
  - Partial commands: specific guidance (e.g., `mode edit` → "Need one more detail")
  - Empty input: silent no-op in free mode, skip in directed, reprompt in destructive
- Mode restore: launches configured apps via `subprocess`/`ShellExecute` (FR39 — launch only)
- Per-app progress feedback (✓/✗) with graceful-partial pattern — failures don't block
- Voice-final-line summary after restore
- Shutdown flow: Shutdown Card with session summary + seed question + session persistence
- Seed lifecycle: capture at shutdown → store in Brain → surface at next briefing
- Minimal Brain persistence:
  - Sessions table: create, end, query last session
  - Workspace snapshots: startup/shutdown captures
  - Memory items: seeds, session notes
  - BriefingAggregate loading from these tables + mode config
- Voice/Skin split respected: Voice generates briefing prose and shutdown confirmation, Skin renders Rich components. Operational output (✓/✗ progress, status tables) bypasses Voice and goes direct to Skin.
- Audit logging for app launches and mode switches via AuditLogger
- Stored notes/seeds accessible verbatim (no Claude dependency for core display)
- `nova status` shows current mode, session duration, tier

**FRs covered:** FR7, FR9, FR13, FR19 (partial — session/seed/snapshot persistence), FR25, FR27, FR28, FR29, FR30, FR39, FR42, FR44, FR60 (implementation — stored notes verbatim)

**NFRs addressed:** NFR1 (<30s restore), NFR3 (<5s briefing), NFR4 (<30s shutdown), NFR15 (continuity loop highly reliable), NFR20 (no focus stealing), NFR26 (actions logged)

**UX-DRs addressed:** UX-DR1 (all 3 states), UX-DR2 (progressive briefing — self-trimming), UX-DR3 (command response), UX-DR4 (progress indicator), UX-DR5 (shutdown card), UX-DR9 (T1 command grammar — core subset), UX-DR20 (feedback patterns), UX-DR21 (empty input handling), UX-DR22 (Voice vs. Skin routing)

---

### Epic 4: Context Awareness & Memory Enrichment

N.O.V.A. becomes aware of what's happening on the desktop — detecting the active window, tracking context changes, extracting meaning from window titles, and accumulating richer memory across sessions. The exclusion boundary is hardened: sensitive apps produce only opaque events at the capture layer. Memory compounds — day 7 briefings are richer than day 2. Cloud prompts receive only minimized context via PromptBuilder (the only cloud egress path).

**What ships:**
- Eyes system active: Win32 context capture adapter, polling every 500ms–1s, events only on change
- Exclusion filtering at capture layer — excluded apps produce `OpaqueContextEvent`, no identifying details
- Exclusion list loaded from `exclusions.yaml` with shipped defaults (password managers, banking, etc.)
- User can inspect/modify exclusion list
- Workspace state capture on demand (not just startup/shutdown)
- Context buffer: track recent app/window changes during active sessions
- Window title parsing: extract project name, page title, document name
- Mode inference: suggest likely mode from current open apps
- Memory accumulation: Brain stores richer context summaries across sessions
- Pattern detection (minimal T1): basic usage patterns (mode by day, recurring projects)
- PromptBuilder (`core/prompt_builder.py`): minimizes Brain context, strips excluded references, enforces token budget, produces cloud-safe payload. The Claude adapter only receives PromptBuilder output.
- Briefings improve in relevance as memory accumulates (FR26)

**FRs covered:** FR14, FR15, FR16, FR17, FR18, FR20, FR21, FR22, FR23, FR26, FR45, FR46, FR47, FR48, FR49, FR50, FR52

**NFRs addressed:** NFR5 (<100ms poll), NFR8 (no raw memory to network), NFR9 (minimized prompts), NFR10 (exclusion at capture), NFR21 (<750MB memory), NFR22 (<2% CPU idle), NFR23 (<100MB SQLite/6mo), NFR24 (no degradation of user's apps)

**UX-DRs addressed:** UX-DR22 (operational output routing for context events)

---

### Epic 5: Transparency, Trust & Deletion

A user can ask "What do you know?" and see a complete, structured view of all stored knowledge — modes, memory, sessions, tier status. They can selectively forget topics with deletion propagated atomically across all tables (sessions, memory_items, workspace_snapshots, seeds). They can inspect the audit trail. The transparency command matches SQLite contents exactly — no hidden state, no omitted categories. Excluded items appear as opaque placeholders only. SQLite corruption triggers an explicit user-facing recovery flow.

**Acceptance criteria enforce:**
- Complete truth within the privacy boundary
- Deletion atomic from the user's perspective (all-or-nothing before transparency re-query)
- No hidden secondary memory stores
- PromptBuilder as the only cloud egress path (validated, not just assumed)

**What ships:**
- Knowledge Display component: Rich Panel with Tree structure (Modes, Memory, Session with tier)
- Transparency query: Brain assembles complete `TransparencyModel` from all tables + config
- Selective forget: exact-match-by-default → preview → confirm → delete from all representations → audit log
- Audit trail inspection: viewable through N.O.V.A. commands and directly via SQLite
- Tier status embedded in transparency and status commands
- Tier Notice component: single amber line + capability list, shown once on change
- Confirmation Prompt component: preview + binary y/n for forget operations
- SQLite corruption recovery flow: detect → offer restore from backup / start fresh / exit
- Post-deletion verification: transparency command immediately reflects deletions

**FRs covered:** FR32, FR33, FR34, FR35 (user-facing tier communication), FR36 (explain tier capabilities), FR38, FR51

**NFRs addressed:** NFR6 (<3s transparency), NFR11 (deletion complete before transparency re-query), NFR27 (complete accurate transparency), NFR28 (audit queryable), NFR29 (deletion logged without content), NFR30 (tier always accessible), NFR31 (backup/restore without special tooling)

**UX-DRs addressed:** UX-DR6 (Knowledge Display), UX-DR7 (Tier Notice), UX-DR8 (Confirmation Prompt), UX-DR17 (SQLite corruption recovery)

**Note:** User-facing `nova self-update` command is NOT T1 scope. Migration runner infrastructure (auto-backup, automatic startup migrations) ships in Epic 1.

---

### Epic 6: Desktop Actions & Workspace Orchestration Expansion

Mode restore becomes richer — N.O.V.A. can focus running windows and arrange windows in basic layouts as part of mode restoration, all within the safe-only action boundary. Users can create modes on the fly mid-session, edit existing modes, and bookmark mode state when switching. Multiple modes with full config options (folders, URLs, behavior flags) are fully supported.

**T1 safety boundary (strictly enforced):**
- Only safe desktop actions ship in T1: launch, focus, arrange
- Focus and arrange are allowed only within the safe-only boundary — no window closing, no file modification, no keyboard/mouse simulation
- FR43 is scoped to the safe-tier boundary only — it defines the confirmation gate for the safety boundary edge, not an expansion past it. No "beyond-safe" actions exist in T1.
- Safety boundaries fail closed on ambiguity

**What ships:**
- Focus window (`win32gui.SetForegroundWindow`) — bring running app to foreground
- Arrange windows (`win32gui.MoveWindow`) — basic layout as part of mode restore
- Bookmark mode state on switch: capture current mode's apps/context before switching
- Ad-hoc mode creation: explicit (`mode create`) and implicit (user tries nonexistent mode) entry points, 2-3 questions, natural app name resolution, immediate YAML write, offer to switch, cancel exits cleanly
- Mode editing via `mode edit <name>` command
- Full mode config: app sets, folder associations, URLs, behavior flags
- All desktop actions audited
- FR43 scoped as safe-tier confirmation gate: if an action is ambiguous about whether it falls within safe tier, require confirmation rather than guessing

**FRs covered:** FR8, FR10, FR11, FR12, FR40, FR41, FR43 (scoped to safe-tier boundary)

**NFRs addressed:** NFR1 (restore <30s with richer actions), NFR20 (no focus stealing), NFR26 (all actions logged)

**UX-DRs addressed:** UX-DR9 (full T1 grammar — `mode create`, `mode edit`), UX-DR16 (ad-hoc mode creation flow), UX-DR4 (progress indicator for richer restores)

---

### Epic 7: Personality, Voice & Conversational Polish

N.O.V.A. speaks with its full personality doctrine — sharp, loyal, witty. Bluntness is configurable (Calm/Direct for T1). Strategic praise fires when earned. Context-adaptive style adjusts between briefings, work, shutdown, and failure. Self-trimming rituals fade when unused. The Claude system prompt encodes the complete personality doctrine. PromptBuilder enforces cloud trust boundary with full minimization and token budgeting.

**Note:** Earlier epics already respect the Voice/Skin split and brevity doctrine — they just use functional placeholder prose. This epic replaces placeholders with the full personality system.

**What ships:**
- Voice system fully implemented: personality generation via Claude adapter
- Claude system prompt encodes the Personality Doctrine (prohibited patterns, required patterns)
- Bluntness levels: Calm and Direct (Ruthless deferred to T2)
- Strategic praise: triggers on multi-session completion, sustained focus. Max once per session. Short phrases ("Clean work."), no special formatting.
- Context-adaptive style: minimal during flow, structured during briefing, one-question during shutdown, detailed on explanation request, honest on failure
- Self-trimming rituals: suppress elements that repeatedly go unused
- PromptBuilder full implementation: minimization, excluded context stripping, token budget enforcement
- Personality progression driven by memory depth (not calendar days)

**FRs covered:** FR53, FR54, FR55, FR56, FR31

**NFRs addressed:** NFR7 (<3s Claude round-trip), NFR9 (minimized prompts), NFR25 (<$2.50/month API cost)

**UX-DRs addressed:** UX-DR12 (personality doctrine), UX-DR13 (bluntness Calm/Direct), UX-DR14 (strategic praise), UX-DR15 (context-adaptive style)

---

### Epic 8: Capability Tiers & Graceful Degradation (Integration/Hardening)

N.O.V.A. handles the real world. This epic is cross-cutting integration hardening, not isolated module work. All three capability tiers (full/degraded/offline) are tested as integrated system behavior across every prior epic's functionality. Tier transitions are detected within 5 seconds and communicated once, honestly. Local operations never break. Degraded mode shows raw seeds verbatim. Recovery offers catch-up briefing.

**Scoping rule:** Stories in this epic test tier behavior across the full system — they do not duplicate implementation from earlier epics. FR60 coverage here is explicitly degraded/offline integration testing only (the implementation lives in Epic 3).

**What ships:**
- Full tier behavior tested across all systems: Brain, Eyes, Hands, Ritual, Voice, Skin, Nerve
- Tier transition integration: full → degraded (2+ consecutive API failures) → offline (health checks consistently fail) → recovery (health check succeeds)
- Degraded mode: local ops continue, Voice bypassed, raw seed/data verbatim, queue + retry for cloud requests
- Offline mode: local-only operations (memory reads, mode switching, workspace restore, transparency)
- Recovery flow: "Cloud reasoning restored. Catch-up briefing?" → synthesis of outage period
- Single malformed API response fallback: inline notice, no tier degradation, local fallback for that operation
- Partial workspace restore under degraded conditions
- Tier notices shown once per transition, not repeated
- Honest communication at every failure point — never silently degrade, never pretend

**FRs covered:** FR35 (integration testing), FR36 (integration testing), FR37, FR58 (full system behavior), FR59, FR60 (degraded/offline integration testing only — implementation in Epic 3)

**NFRs addressed:** NFR16 (local ops never depend on cloud), NFR17 (tier transition <5s)

**UX-DRs addressed:** UX-DR7 (Tier Notice — integration across all flows), UX-DR17 (malformed API response fallback, partial restore — system-wide scenarios)

---

## Dependency Flow

```
Epic 1 (Foundation)
  └─► Epic 2 (Setup) ─── first modes + API key exist
        └─► Epic 3 (Core Session Loop) ─── hero path fires end-to-end
              ├─► Epic 4 (Context & Memory) ─── enriches what loop captures
              ├─► Epic 5 (Transparency & Trust) ─── inspect what loop stores
              ├─► Epic 6 (Desktop Orchestration) ─── richer mode restore
              └─► Epic 7 (Personality & Voice) ─── polishes how loop speaks
                    └─► Epic 8 (Tiers & Hardening) ─── tests everything under failure
```

Epics 4, 5, 6, 7 can be developed in parallel after Epic 3. Epic 8 comes last as integration hardening.

---

## Epic 1: Project Foundation & Core Infrastructure

The developer (or AI agent) can scaffold, build, and run the N.O.V.A. project with all core plumbing operational. No user-facing features yet, but every subsequent epic builds on this foundation without rewiring.

### Story 1.0: Define YAML Config Schemas (Spike)

As a developer (or AI agent implementing future epics),
I want the YAML config schemas for modes, exclusions, and settings pinned with exact field definitions, validation rules, and defaults,
So that every epic from 2 onwards has a single source of truth for config shape and no schema drift occurs across agents.

**Acceptance Criteria:**

**Given** the architecture document defines mode, exclusion, and settings YAML schemas
**When** this spike is complete
**Then** the following schema definitions exist as documented reference in the codebase:
- Mode schema (`modes/*.yaml`): `name` (required string), `apps` (required list, each with `name` string + `executable` string + optional `args` list defaulting to []), `folders` (optional list of absolute path strings), `urls` (optional list of URL strings), `is_default` (optional boolean, default false)
- Exclusion schema (`exclusions.yaml`): `excluded_apps` (list of objects with `name` string + `match` string for case-insensitive subprocess name matching), `excluded_title_patterns` (list of strings, case-insensitive substring match against window title)
- Settings schema (`settings.yaml`): `api_key` (required string), `bluntness` (optional enum: calm/direct — T1 ships these two only; ruthless deferred to T2), `skip_briefing_if_recent` (optional boolean, default true), `briefing_recency_threshold_minutes` (optional integer, default 60)
**And** shipped default files exist in `config/`: at least one workspace-mode template (`coding.yaml` — this is a workspace-mode template, not a project starter template), `exclusions.yaml` with sensible defaults (password managers, banking apps), `settings.defaults.yaml` without API key
**And** settings schema does not include `telemetry_opt_in` — the project-context rule is "no telemetry without explicit opt-in" and no telemetry infrastructure exists in T1
**And** validation rules are documented: required fields error on absence, optional fields use documented defaults, invalid enum values fall back with logged warning, invalid mode files are skipped with warning (other modes still load)
**And** unknown keys in any config file are ignored (forward compatibility)

### Story 1.1: Project Scaffolding & Package Setup

As a developer,
I want a runnable Python project skeleton with the complete directory structure, pyproject.toml, and all T1 dependencies,
So that I can build and run the project from day one with uv sync and uv run nova.

**Acceptance Criteria:**

**Given** no project exists yet
**When** the scaffolding story is complete
**Then** the directory structure matches the architecture spec exactly (src/nova/, tests/, config/, ports, systems, adapters, core directories all exist with __init__.py)
**And** pyproject.toml declares Python 3.12+, all T1 dependencies (Rich, pywin32, psutil, anthropic SDK, pytest, pytest-asyncio, pytest-cov, ruff, mypy), ruff config, mypy strict config, pytest config, and [project.scripts] nova = "nova.cli:main"
**And** .gitignore excludes __pycache__, .venv, *.db, %LOCALAPPDATA%/nova/ references, IDE files
**And** .python-version specifies 3.12
**And** uv sync succeeds and uv run nova executes cli.py:main (which may just print a placeholder and exit at this stage)
**And** uv run ruff check src/ tests/ and uv run mypy src/ pass with zero errors on the skeleton

### Story 1.2: Domain Exceptions & Shared Types

As a developer implementing any system,
I want a central set of domain exception types and shared types available,
So that adapter-specific exceptions never cross port boundaries and all systems use consistent domain types.

**Acceptance Criteria:**

**Given** the architecture specifies domain exceptions and shared types
**When** this story is complete
**Then** core/exceptions.py defines at minimum: NovaError (base), StorageError, ConfigError, ApiUnavailableError, ModeNotFoundError, AdapterError
**And** core/types.py defines shared enums using StrEnum with stable string values:
- CapabilityTier (full, degraded, offline)
- BriefingState (first_run, post_setup, warm_resume)
- SnapshotType (startup, shutdown, mode_switch, periodic)
- ActionType (app_launch, app_focus, window_arrange, mode_switch, mode_restore, mode_create, mode_edit, deletion, seed_capture, tier_change, database_recovery)
- MemoryCategory (seed, session_note, context_summary, pattern)
- BluntnessLevel (calm, direct) — T1 only; ruthless deferred to T2
**And** no adapter-specific types (sqlite3, anthropic, pywin32) appear in these files
**And** unit tests verify enum serialization round-trips correctly (str value → enum member → str value)

### Story 1.3: Event Bus & Typed Event Definitions

As a developer wiring inter-system communication,
I want an in-process async event bus with all T1 event types defined as typed frozen dataclasses with explicit fields,
So that systems communicate through events via Nerve without raw string types, generic payload dicts, or direct cross-system calls.

**Acceptance Criteria:**

**Given** the architecture defines event bus semantics and typed event classes
**When** this story is complete
**Then** core/events.py contains:
- EventBus class with async subscribe(event_class, handler) and async emit(event) — routing by event class, not string name
- Ordered sequential delivery within current process (not concurrent fan-out in T1)
- No generic base Event with payload: dict — each event type has its own explicit typed fields
- T1 typed event classes, each a @dataclass(frozen=True) with explicit fields:
  - ContextChanged(source="eyes", app_name: str | None, window_title: str | None, process_name: str | None, is_opaque: bool) — is_opaque=True for excluded apps, other fields None when opaque
  - TierChanged(source="nerve", previous_tier: CapabilityTier, new_tier: CapabilityTier, reason: str)
  - SessionStarted(source="nerve", session_id: int, mode_name: str | None)
  - SessionEnded(source="ritual", session_id: int, seed_text: str | None, is_complete: bool)
  - SeedSaved(source="ritual", session_id: int, seed_text: str)
  - ModeRestored(source="hands", mode_name: str, apps_launched: list[str], apps_failed: list[str])
  - AppLaunched(source="hands", app_name: str, executable: str, success: bool, reason: str | None)
  - MemoryForgotten(source="brain", target: str, items_deleted: int)
- All events carry a timestamp: str field (ISO 8601 UTC), auto-populated at creation
**And** handler failures are logged (not swallowed) and do not block other handlers or crash the session
**And** no raw string event dispatch exists anywhere — all subscription and emission uses typed event classes
**And** unit tests verify: ordered delivery, handler failure isolation, event immutability, subscribe/emit lifecycle, correct field types on each event

### Story 1.4: SQLite Storage Engine

As a developer implementing Brain or any persistence,
I want a SQLite storage engine with connection management, configurable DB path, and startup initialization,
So that database access is centralized and no system talks to SQLite directly outside Brain's ownership.

**Acceptance Criteria:**

**Given** the architecture specifies SQLite storage at %LOCALAPPDATA%/nova/nova.db using stdlib sqlite3
**When** this story is complete
**Then** core/storage/engine.py provides SqliteStorageEngine with:
- Connection management using stdlib sqlite3 wrapped with asyncio.to_thread() for non-blocking access (no aiosqlite dependency)
- Configurable DB path via constructor injection
- Startup initialization: creates DB file if missing, enables WAL mode for performance
- Exposes async query helpers: execute, executemany, fetchone, fetchall
- Connection lifecycle: open on startup, close on shutdown
**And** the storage engine is the only module that imports sqlite3 — no other module creates SQLite connections
**And** Brain will own all table access through its port; the storage engine provides infrastructure, not business logic
**And** unit tests verify: DB creation, connection lifecycle, async query execution, configurable path

### Story 1.5: Migration Runner & Initial Schema

As a developer,
I want a migration runner that discovers, validates, and applies numbered migration scripts with auto-backup before every schema change,
So that database schema evolves safely and no data is lost during migration.

**Acceptance Criteria:**

**Given** the storage engine exists (Story 1.4) and the architecture defines migration conventions
**When** this story is complete
**Then** core/storage/migrations/runner.py provides a migration runner that:
- Discovers migrations in core/storage/migrations/ by numbered filename (001_*.py, 002_*.py)
- Checks schema_version table for already-applied versions
- Creates a timestamped backup of nova.db to backups/ directory before applying any pending migration
- Applies pending migrations in sequential order, never skips
- Records each applied migration in schema_version with version number, ISO 8601 timestamp, and description
- Is idempotent — re-running with no pending migrations is a safe no-op
**And** core/storage/migrations/001_initial_schema.py creates all T1 tables per the architecture schema:
- schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT)
- sessions (id, started_at, ended_at, mode_name, seed_text, summary, is_complete)
- workspace_snapshots (id, session_id FK, captured_at, snapshot_type, workspace_data JSON)
- memory_items (id, session_id FK, category, content, created_at, relevance_score)
- audit_log (id, timestamp, action_type, target, result, details JSON)
**And** the migration runner integrates with the storage engine's startup sequence — migrations run automatically when the app boots
**And** unit tests verify: fresh DB gets 001 applied, backup created before migration, idempotent re-run, schema_version tracking
**And** integration test verifies: upgrade from empty DB applies 001 and all tables exist with correct columns

### Story 1.6: Config Loader & Immutable NovaConfig

As a developer implementing any system,
I want a config loader that reads all YAML files from %LOCALAPPDATA%/nova/ and exposes them as an immutable NovaConfig dataclass,
So that no system reads YAML directly and all config access is centralized and type-safe.

**Acceptance Criteria:**

**Given** config schemas are defined (Story 1.0) and shipped defaults exist in config/
**When** this story is complete
**Then** core/config.py provides NovaConfig (frozen dataclass) with: db_path: Path, data_dir: Path, modes: dict[str, ModeConfig], exclusions: ExclusionConfig, settings: UserSettings, api_key: str | None
**And** ModeConfig, ExclusionConfig, UserSettings are frozen dataclasses matching the pinned YAML schemas from Story 1.0
**And** the config loader:
- Reads all .yaml files from %LOCALAPPDATA%/nova/modes/ into modes dict
- Reads exclusions.yaml and settings.yaml
- Validates against schema rules: required fields present, types correct, invalid enum values fall back with logged warning (e.g., invalid bluntness → direct)
- Mode files that fail validation are skipped with a warning (other modes still load)
- Unknown keys are ignored (forward compatibility)
- Missing settings.yaml produces sensible defaults with api_key=None
**And** config is loaded once at startup, immutable after loading
**And** no other module reads YAML/JSON config files directly — this is the single config reader for the whole project
**And** unit tests verify: valid config loads correctly, missing optional fields get defaults, invalid mode file skipped with warning, missing api_key produces None, unknown keys ignored

### Story 1.7: Capability Tier State Machine

As a developer implementing cloud-dependent features,
I want a tier state machine that tracks full/degraded/offline state with a tolerant degrade model,
So that systems can check tier state before cloud operations and tier transitions are detected reliably.

**Acceptance Criteria:**

**Given** the architecture defines three capability tiers with a tolerant degrade model
**When** this story is complete
**Then** core/tiers.py provides TierManager with:
- Current tier state as a CapabilityTier enum (full/degraded/offline)
- Transition logic: full → degraded (2+ consecutive API failures), degraded → offline (health checks consistently fail), offline → full (health check succeeds), degraded → full (health check succeeds)
- async health_check() method that pings the Claude API with an explicit timeout
- Recovery check scheduling: every 60s + opportunistic on next cloud-requiring action
- TierChanged event emitted on every transition (once per transition, not repeated)
**And** single malformed API response does NOT trigger tier degradation — only the specific operation falls back locally
**And** tier state is queryable synchronously by any system via the TierManager
**And** TierManager receives its health check dependency (Claude adapter or a health-check port) via constructor injection — no direct adapter imports
**And** unit tests verify: all state transitions, tolerant degrade (single failure does not degrade), 2+ consecutive failures trigger degradation, recovery, event emission on transition
**And** tests use a deterministic clock and mock health check — no wall-clock or network dependencies

### Story 1.8: Audit Logger

As a developer implementing auditable actions,
I want a single audit logging interface that writes to the audit_log table,
So that all automated actions are logged consistently without any system writing to audit_log directly.

**Acceptance Criteria:**

**Given** the architecture specifies AuditLogger as a cross-cutting concern
**When** this story is complete
**Then** core/audit.py provides AuditLogger with async log_action(action_type: ActionType, target: str | None, result: str, details: dict | None) method
**And** action_type uses the ActionType enum (app_launch, app_focus, window_arrange, mode_switch, mode_restore, deletion, seed_capture, tier_change, database_recovery)
**And** target is opaque for excluded contexts — never the actual app name or window title
**And** details is JSON-serializable, never contains raw excluded content
**And** audit writes are append-only — no updates, no deletes of audit entries
**And** audit logging is observational: audit write failure must not block the primary action (log the failure, continue)
**And** AuditLogger receives its storage dependency via constructor injection (not importing adapters directly)
**And** unit tests verify: action logged correctly with correct fields, excluded target is opaque, write failure does not raise to caller, append-only behavior (no update/delete methods exist)

### Story 1.9: Port Interfaces & Shield Stub

As a developer implementing any system,
I want all 8 port interfaces defined as Protocol classes and the Shield no-op stub in place,
So that systems can be wired through the composition root without importing concrete adapters.

**Acceptance Criteria:**

**Given** the architecture defines ports for all 8 systems
**When** this story is complete
**Then** ports/brain.py, ports/eyes.py, ports/hands.py, ports/shield.py, ports/voice.py, ports/ritual.py, ports/skin.py, ports/nerve.py each define a Protocol class with async methods using domain types only
**And** port methods use domain types from core/types.py and system-specific models — never adapter-specific types (no sqlite3.Row, no rich.Panel, no anthropic.Message in port signatures)
**And** Shield no-op adapter lives in adapters/ (not systems/shield/) — it is a concrete adapter implementing ShieldPort that returns inert/empty responses for all methods. systems/shield/ defines the port/facade boundary; the no-op implementation is an adapter concern.
**And** no port file imports from adapters/ or from another system's internals
**And** mypy strict passes on all port files

### Story 1.10: Composition Root & CLI Entrypoint

As a developer,
I want app.py wiring all ports to adapters and cli.py providing a minimal terminal entrypoint,
So that uv run nova boots the monolith through a single composition point and exits cleanly.

**Acceptance Criteria:**

**Given** all ports, storage engine, config loader, event bus, tier manager, and audit logger exist
**When** this story is complete
**Then** app.py is the single place where ports are wired to adapters:
- Creates SqliteStorageEngine, runs migrations
- Loads NovaConfig from %LOCALAPPDATA%/nova/
- Creates concrete adapters (stubs or real: SqliteBrainAdapter, Win32EyesAdapter, Win32HandsAdapter, ClaudeReasoningAdapter, RichSkinAdapter, Shield no-op)
- Creates system instances with constructor injection
- Wires the EventBus
- Returns a NovaApp object that Nerve can drive
**And** app.py initializes structured logging infrastructure:
- Configures Python logging to write to %LOCALAPPDATA%/nova/logs/nova.log
- Sets default log level to INFO (DEBUG available via config or environment variable)
- Uses structured key-value context in extra fields, not free-form string interpolation
- Log format: timestamp, logger name (nova.systems.brain etc.), level, message, extra fields
- Log rotation: deferred to T2 — T1 ships a single log file with no rotation (acceptable for solo dev usage; document the manual cleanup path)
- No log output to terminal — terminal is Skin's domain; logging and rendering are completely separate channels
- Logger names follow nova.{layer}.{system} convention (e.g., nova.systems.brain, nova.core.tiers, nova.adapters.sqlite)
**And** cli.py provides:
- main() entrypoint registered as nova script
- Minimal bootstrap: loads config → initializes logging → checks migrations → boots composition root → placeholder app boot that exits cleanly or enters a minimal prompt
- No Layer A command parsing beyond bare nova — full command grammar (mode, status, help, memory, shutdown) belongs to Epic 3+
**And** uv run nova starts, initializes all infrastructure (including logging), and exits cleanly (or shows a minimal placeholder message)
**And** no system module imports concrete adapter classes — only app.py does
**And** swapping an adapter means changing one line in app.py

### Story 1.11: CI Quality-Gate Automation

As a developer (or AI agent),
I want a CI pipeline that runs the full quality gate on every push,
So that ruff, mypy, and pytest failures are caught before code merges and local/CI environments never drift.

**Acceptance Criteria:**

**Given** the project uses uv for all tooling and pyproject.toml as the single config source
**When** this story is complete
**Then** a GitHub Actions workflow (or equivalent CI config) runs on every push and PR:
- uv sync (install dependencies from lockfile)
- uv run ruff check src/ tests/ (lint)
- uv run ruff format --check src/ tests/ (format check)
- uv run mypy src/ (type check, strict mode)
- uv run pytest tests/unit/ (unit tests)
- uv run pytest tests/integration/ (integration tests, if any exist at this point)
**And** the workflow matches the canonical commands from project-context.md exactly — no drift between local and CI
**And** the workflow targets Python 3.12 and runs on a Windows runner (or documents why Ubuntu is acceptable for non-Win32 tests)
**And** the workflow fails fast: if ruff fails, mypy and pytest do not run
**And** test markers (unit, integration, windows_only) are respected so CI can separate suites
**And** the CI config is committed to the repo and runs green on the existing skeleton from Story 1.1

---

## Epic 2: First-Run Setup & Onboarding

A new user clones the repo, runs setup.bat, configures their API key, creates their first workspace mode through a guided wizard with starter workspace-mode templates, and captures an initial workspace snapshot. Setup completes in under 15 minutes. Briefing Card State A renders at the very beginning (pre-setup) and auto-transitions into the wizard.

### Story 2.1: Setup Script (setup.bat) + Shared Path Validation

As a new user on Windows 11,
I want to run a single setup script that checks prerequisites, installs dependencies, and creates my data directory,
So that I can get N.O.V.A. running without knowing Python packaging.

**Story-type classification:** This is a **first-through-boundary story** (per Epic 1 retrospective, 2026-04-15). Story 2.1 introduces a new shared infrastructure module (`nova.core.paths`) plus a user-facing failure surface (setup.bat). The boundary-first invariant sweep in the *Review Focus* section below is mandatory.

**Acceptance Criteria — Group A: Setup script behavior (original scope)**

**Given** the user has cloned the N.O.V.A. repository on a Windows 11 machine
**When** the user runs setup.bat
**Then** the script checks for Windows 11 (exits with clear message if not), Python 3.12+ (exits with download URL if missing), and uv (installs via official installer if missing)
**And** runs uv sync to create venv and install all dependencies
**And** creates %LOCALAPPDATA%/nova/ with subdirectories: modes/, backups/, logs/
**And** copies shipped defaults from config/ to user data directory only if target files don't already exist (never overwrites user customizations)
**And** the script is idempotent — running it twice does not corrupt state or overwrite user config
**And** the script does not require administrator privileges
**And** every failure produces a clear, non-technical message with specific next action (not raw stack traces)
**And** the script works from both cmd.exe and PowerShell
**And** on success, the script launches the first-run wizard via uv run python -m nova.setup

**Acceptance Criteria — Group B: Shared path validation module (new)**

**And** a new shared module `nova.core.paths` is introduced with a public function `validate_data_dir(path: Path) -> None` that raises `ConfigError` with a product-grade (non-technical) message on any violation
**And** `validate_data_dir` rejects the following conditions after the path has been resolved via `Path.resolve(strict=False)`:
- **Reserved Windows names at any path segment** (case-insensitive, with or without file extension): `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`
- **Invalid characters in any segment**: `<`, `>`, `:` (except the drive-letter colon at index 1 of the first segment), `"`, `|`, `?`, `*`
- **Trailing dots or trailing spaces** in any segment
- **Resolved path points to a file** (not a directory) that already exists on the current host
**And** when the resolved path exceeds the current host's supported Windows path limit (detected at runtime via a module-level helper `_get_max_path_length()` — NOT hard-coded to 260), validation raises `ConfigError` with the specific message: *"Path too long for this system. Shorten the path or enable Windows long-path support."*
**And** `_get_max_path_length()` is called through the module attribute (e.g., `paths._get_max_path_length()`) so tests can monkeypatch it — follows the two-function clock indirection pattern (see `docs/cross-cutting-patterns.md`, Pattern #1)
**And** `nova.core.paths` is pure (no I/O beyond `Path.resolve`), synchronous, and has no imports from `nova.adapters.*` or `nova.systems.*` — enforced by an AST guard test (follows `docs/cross-cutting-patterns.md`, Pattern #2)
**And** all validation failure messages are opaque-friendly: they name the offending segment and the reason, but never include the full input path, no filesystem metadata, and no stack traces in the user-visible surface

**Acceptance Criteria — Group C: Validation applied at both call sites (new)**

**And** `nova.setup` (the first-run entrypoint launched by setup.bat via `uv run python -m nova.setup`) calls `validate_data_dir(data_dir)` on the resolved `%LOCALAPPDATA%/nova/` path **before creating any subdirectories** — validation must run before `mkdir` so a bad path never produces partial state
**And** on validation failure in the setup flow, setup.bat exits with a clear non-technical message naming the offending segment and suggesting a remedy (e.g., *"Your user data path contains a reserved Windows name: 'CON'. Choose a different Windows user profile or contact support."*) — no Python traceback reaches the terminal
**And** `cli.py` (from Story 1.10) calls `validate_data_dir(data_dir)` in `create_app` **before any directory creation** or engine start — this closes the deferred Windows path validation item from Story 1.10
**And** the cli.py change is minimal: one added call plus one translated `ConfigError` → clear exit path; no refactoring of the existing Phase A / Phase B logging structure
**And** validation behavior is identical at both call sites (same module, same rules, same error messages)

**Acceptance Criteria — Group D: Testing (new)**

**And** a parametrized unit test covers every reserved name (CON, PRN, AUX, NUL, COM1–9, LPT1–9 — 22 names total) with all four variants: (a) as last segment, (b) as middle segment, (c) with `.txt` extension, (d) with different casing — 22 names × 4 variants minimum
**And** a parametrized unit test covers each invalid character (`<`, `>`, `:`, `"`, `|`, `?`, `*`) at each segment position (first, middle, last)
**And** a parametrized unit test covers trailing-dot and trailing-space cases on each segment position
**And** a test asserts that the drive-letter colon at index 1 of the first segment (e.g., `C:\foo`) is NOT rejected
**And** a test monkeypatches `_get_max_path_length()` to a small value and asserts the long-path error is raised for paths exceeding it
**And** a test asserts validation error messages contain no full input path (only the offending segment name + reason)
**And** an integration test exercises the cli.py path: invoking `nova --data-dir <bad-path>` produces the expected non-technical exit message and no partial directory creation
**And** an AST guard test asserts `nova.core.paths` imports nothing from `nova.adapters.*` or `nova.systems.*`

**Review Focus (boundary-first invariant sweep, per Epic 1 retrospective)**

| Dimension | Resolution for this story |
|---|---|
| **Lifecycle** | `validate_data_dir` is pure; no start/stop state to manage. Setup flow lifecycle (venv, mkdir, copy-defaults) must tear down cleanly on validation failure: no partial %LOCALAPPDATA%/nova/ directory must be created if validation fails. |
| **Teardown under partial failure** | If `mkdir` succeeds for one subdirectory but fails for another, setup.bat must not leave the data dir in an inconsistent state. Either: (a) no subdirs created, or (b) all subdirs created. Document which. |
| **Concurrency model** | Validation is synchronous and pure; safe from any thread. Setup flow is single-process, single-thread; no concurrency concerns. |
| **Cancellation** | Setup flow is synchronous. User Ctrl+C during setup.bat must produce a clean exit message (no half-created venv left in an unrecoverable state — uv handles its own rollback, but setup.bat must not leave mkdir partial if interrupted between mkdir calls). |
| **Error translation** | All validation failures raise `ConfigError` (reuse existing domain exception from `nova.core.exceptions`; no new exception type). OS errors from `Path.resolve(strict=False)` on pathological input are caught and translated to `ConfigError`. Setup.bat translates `ConfigError` to non-technical terminal output; cli.py translates `ConfigError` to exit code + stderr message. |
| **Test determinism** | `_get_max_path_length()` is the sole source of non-determinism in the module (OS-dependent); it must be monkeypatchable via module attribute (see Pattern #1). All other validation rules are pure. |
| **Patterns consulted** | `Error-translation-at-boundary` (Pattern #4), `Two-function clock indirection` (Pattern #1, applied to `_get_max_path_length`), `AST-based architectural guardrails` (Pattern #2), `Per-file skip-on-error vs. singleton hard-fail` (Pattern #5 — setup is a singleton flow: hard-fail on validation error, don't skip). |

**Explicit non-goals (scope fence):**
- Setup.bat does NOT accept a `--data-dir` argument in T1. The only user-controlled path surface is `cli.py --data-dir`.
- Long-path opt-in (registry edit or manifest) is NOT handled by N.O.V.A.; we detect the current host limit and report clearly when it's exceeded.
- No interactive "choose a different data directory" flow — setup fails with a clear message and exits.
- UNC paths (`\\server\share\...`) are not in scope for T1 validation; they may pass or fail incidentally but are not tested. Document as a known limitation.

### Story 2.2: API Key Configuration

As a new user,
I want to configure my Claude API key during first-run setup with validation that it works,
So that N.O.V.A. can use cloud reasoning and I know the key is valid before I start.

**Acceptance Criteria:**

**Given** the first-run wizard is running and no API key exists in settings.yaml
**When** the wizard prompts for an API key
**Then** the user enters their Anthropic API key at the prompt
**And** the wizard validates the key by making a lightweight Claude API call with a timeout
**And** on success: key is written to %LOCALAPPDATA%/nova/settings.yaml, confirmation shown ("API key validated.")
**And** on failure (invalid key, network error, timeout): clear message shown with specific guidance, user can retry or skip
**And** if skipped: N.O.V.A. starts in offline-local-only tier with a one-time notice that cloud reasoning is unavailable — skipping does not block onboarding
**And** the API key is never logged, never printed in full to the terminal, never committed to source code
**And** settings.yaml is written with correct YAML formatting and the key is the only field modified (other settings retain defaults)

### Story 2.3: Guided Mode Creation Wizard

As a new user,
I want to create my first workspace mode through an interactive wizard with starter workspace-mode templates offered,
So that I have at least one useful mode configured before my first session ends.

**Acceptance Criteria:**

**Given** setup has completed API key step and no user-created modes exist yet
**When** the wizard reaches the mode creation step
**Then** starter workspace-mode templates are offered (e.g., "coding" with VS Code + Chrome + Terminal, "study" with Notion + Chrome) — the user can accept as-is, modify, or skip each template
**And** the wizard asks practical questions per mode: mode name, apps to open, optional folders/URLs, with clear skip option for optional fields
**And** app names are entered naturally ("VS Code", "Chrome") — the wizard resolves to executable names using known app registry or PATH lookup
**And** unresolvable apps produce a clear message ("Couldn't find 'AppName' — add it manually to the mode file later") without blocking mode creation
**And** each completed mode is written immediately to %LOCALAPPDATA%/nova/modes/{mode_name}.yaml conforming to the pinned mode schema (Story 1.0)
**And** at least one mode must be created to exit the wizard (or the user explicitly chooses to exit setup early with a clear message about running setup again)
**And** the wizard uses N.O.V.A.'s design language: Rich panels, the color system (UX-DR10), typography hierarchy (UX-DR11), no emoji, no sycophantic framing

### Story 2.4: Briefing Card State A, Initial Capture & Setup Completion

As a new user launching N.O.V.A. for the first time,
I want to see a clear first-run orientation (Briefing Card State A) that auto-transitions into setup, and after setup completes I want my workspace captured and a clean entry into my first session,
So that I understand what N.O.V.A. is and my first session starts with real context.

**Acceptance Criteria:**

**Given** N.O.V.A. is launched for the first time (no modes, no sessions, no seed)
**When** the app boots
**Then** Briefing Card State A renders first: title "N.O.V.A.", body "First session. No history yet — that's expected. Let's set up your first workspace mode so tomorrow starts warm." (or equivalent per UX spec)
**And** State A auto-transitions into the first-run setup wizard (Stories 2.2 and 2.3) — no pause or manual trigger required
**And** State A is NOT rendered again after setup completes — it is the pre-setup state only
**And** after setup completes (API key configured or skipped, at least one mode created):
- N.O.V.A. captures a best-effort initial workspace snapshot: currently open apps, active windows, focused window (via Eyes/Win32 adapter if available)
- If Eyes returns an empty or partial snapshot, setup still succeeds — initial capture is non-blocking
- The snapshot is stored as a workspace_snapshot record tied to the first session
- A setup completion confirmation is displayed ("Setup complete. You have 1 mode ready: coding. Run nova to start your next session.")
- An audit log entry records the first-run completion
**And** Epic 2 does NOT render Briefing Card State B or C — those belong to Epic 3
**And** after setup completion, N.O.V.A. either enters a minimal first session state or exits cleanly (the full session loop with briefing/mode/shutdown is Epic 3 scope)
**And** the terminal output uses the design system foundation: Rich Panels, semantic colors (UX-DR10), typography hierarchy (UX-DR11), responsive layout at 80+ columns (UX-DR18), accessibility with symbols + color (UX-DR19)
**And** the entire first-run flow (setup.bat → State A → wizard → capture → completion) completes in under 15 minutes on a supported Windows 11 machine (NFR2)

### Story 2.5: API Key Update Post-Setup

As a user whose API key has expired, been revoked, or needs changing,
I want a clear path to update my API key after initial setup,
So that I am not stranded when my key stops working.

**Acceptance Criteria:**

**Given** the user has already completed first-run setup and has a settings.yaml with an existing (possibly invalid) API key
**When** the user needs to change their API key
**Then** there is a documented path to update the key:
- Power user path: edit %LOCALAPPDATA%/nova/settings.yaml directly (documented in help command output and README)
- The help command includes a line: "API key: edit settings.yaml in your data directory"
**And** on next session start, cli.py re-reads settings.yaml and picks up the new key
**And** if the new key is invalid, N.O.V.A. degrades to offline-local-only tier with a clear one-time notice — it does not crash or block the session
**And** if the key field is removed or empty, N.O.V.A. starts in offline-local-only tier
**And** no interactive "change your key" wizard in T1 — the settings file is the documented interface for post-setup key changes (a nova config command is a T2 candidate)
**And** unit tests verify: changed key picked up on restart, invalid key degrades gracefully, missing key starts offline

---

## Epic 3: Core Session Loop (Hero Path)

The full continuity loop fires end-to-end: nova → Briefing Card (State B or C) → mode command → apps launch → work → shutdown → seed captured → next session resumes warm. This is the product proof.

### Story 3.1: Brain Session & Seed Persistence

As a developer building the continuity loop,
I want Brain to store and retrieve sessions, seeds, and workspace snapshots using typed domain models,
So that the shutdown→resume cycle has a persistence layer to write to and read from.

**Acceptance Criteria:**

**Given** the SQLite schema exists (Epic 1) and Brain owns all table access
**When** this story is complete
**Then** the SqliteBrainAdapter implements the session/seed/snapshot subset of BrainPort:
- async create_session(mode_name: str | None) -> int — creates a session row, returns session_id
- async end_session(session_id: int, seed_text: str | None, summary: str | None, is_complete: bool) -> None — updates ended_at, seed, summary, is_complete
- async get_last_session() -> SessionSummary | None — returns most recent session
- async get_last_seed() -> str | None — returns seed_text from last completed session
- async store_snapshot(session_id: int, snapshot: WorkspaceSnapshotInput) -> None — accepts a typed domain input, not a raw dict
- async get_last_snapshot(session_id: int) -> WorkspaceSnapshot | None
**And** domain types are defined in systems/brain/models.py as frozen dataclasses per Architecture Decision 3b:
- SessionSummary (session_id, started_at, ended_at, duration, mode_name, summary, is_complete)
- WorkspaceSnapshot (captured_at, snapshot_type: SnapshotType, apps: list[str], focused_app: str | None, mode_name: str | None)
- WorkspaceSnapshotInput (snapshot_type: SnapshotType, apps: list[str], focused_app: str | None, mode_name: str | None) — typed input DTO; the adapter serializes to JSON for SQLite internally
- MemoryItem (id, session_id, category: MemoryCategory, content, created_at, relevance_score)
**And** SessionSummary.duration is computed (ended_at - started_at), None if session was interrupted
**And** all storage access goes through asyncio.to_thread() wrapping stdlib sqlite3 — no direct sqlite3 calls from system code
**And** the adapter catches sqlite3 exceptions and re-raises as StorageError (domain exception)
**And** no raw dict crosses the port boundary — the adapter handles JSON serialization/deserialization internally
**And** unit tests use in-memory SQLite with the 001 migration applied
**And** tests verify: create/end session round-trip, seed retrieval from last completed session, snapshot storage/retrieval with typed input, interrupted session (is_complete=False) handling

### Story 3.2: BriefingAggregate & State Determination

As a developer building the briefing pipeline,
I want Brain to provide persisted session facts and Nerve to assemble the BriefingAggregate by merging those with mode config, then determine the briefing state (A/B/C),
So that the correct briefing content is produced based on what data exists.

**Acceptance Criteria:**

**Given** Brain can store/retrieve sessions, seeds, and snapshots (Story 3.1)
**When** this story is complete
**Then** Brain provides persisted-fact queries only (it does NOT read or own mode config):
- async get_last_session() -> SessionSummary | None
- async get_last_seed() -> str | None
- async get_last_snapshot_for_session(session_id: int) -> WorkspaceSnapshot | None
- async get_mode_last_used(mode_name: str) -> datetime | None — queries sessions table for most recent session with this mode_name
**And** Nerve (or a briefing-assembly collaborator invoked by Nerve) merges Brain's persisted facts with available modes from NovaConfig to produce BriefingAggregate:
- last_session: SessionSummary | None (from Brain)
- last_snapshot: WorkspaceSnapshot | None (from Brain)
- last_seed: str | None (from Brain)
- available_modes: list[ModeInfo] (from NovaConfig, enriched with last_used_at via Brain.get_mode_last_used)
- recent_memory: list[MemoryItem] (from Brain — minimal in T1)
**And** Brain does NOT read mode YAML files or assemble ModeInfo objects — that crosses the config ownership boundary
**And** ModeInfo (frozen dataclass): name, app_count, is_default, last_used_at (from sessions table via Brain query, not from config)
**And** Nerve implements state determination logic (first match wins):
- IF available_modes is empty AND last_session is None → FIRST_RUN (State A)
- ELIF last_seed is None AND (last_session is None OR last_session.is_complete == False) → POST_SETUP (State B)
- ELSE → WARM_RESUME (State C) — activates when any of: last_seed exists, last completed session exists, last session has summary
**And** state determination is a pure function testable without DB, Claude, or Win32
**And** unit tests verify all state boundary conditions: empty DB → A, modes exist but no seed → B, seed exists → C, completed session without seed → C, interrupted session with no seed → B

### Story 3.3: BriefingViewModel & Briefing Card Rendering

As a returning user,
I want to see a Briefing Card that shows my last seed, mode, and a resume suggestion,
So that I can get back to work without reconstructing context manually.

**Acceptance Criteria:**

**Given** BriefingAggregate is assembled and state determined (Story 3.2)
**When** Ritual assembles the BriefingViewModel
**Then** Ritual produces a BriefingViewModel (frozen dataclass) with all fields per Architecture Decision 3b:
- state, tier, title, prompt_text, auto_start_setup
- available_modes, suggested_mode
- seed_text, last_mode, last_duration_display: str | None (pre-formatted render-safe string, e.g., "1h 42m" — not a raw timedelta crossing layers), last_apps
- prose_enrichment (None in Epic 3 — placeholder for Epic 7)
**And** field population follows the state table exactly:
- State A: title="N.O.V.A.", auto_start_setup=True, all session fields None/empty
- State B: title="Session Briefing", prompt_text="Start in {mode} mode?", seed_text=None
- State C: title="Session Briefing", prompt_text="Resume {mode} mode?", seed_text from aggregate
**And** progressive omission: fields with no data are omitted entirely — no empty placeholders, no "N/A", no fake history
**And** Skin renders the BriefingViewModel as a Rich Panel using the design system: H1 cyan title, seed in bold bright white (hero line), body in soft white, metadata in dim gray, resume suggestion as final bold line
**And** Skin makes zero content decisions — it maps fields to Rich components only
**And** prose_enrichment=None renders cleanly (no blank space or "enrichment unavailable" text)
**And** unit tests verify: Ritual produces correct ViewModel for each state, duration formatting is render-safe, Skin renders without error for all states, progressive omission works for missing fields

### Story 3.4: T1 Command Grammar & Deterministic Parser

As a user in an active session,
I want to type commands like mode coding, status, shutdown, and receive consistent responses,
So that I can control N.O.V.A. through a predictable, learnable grammar.

**Acceptance Criteria:**

**Given** the T1 command grammar is locked (3 layers)
**When** this story is complete
**Then** Skin implements a deterministic command parser that produces Command objects (frozen dataclass: verb, target, raw_input, is_contextual)
**And** Layer A launch behavior is explicitly owned by this story: bare nova invocation boots the app, starts/attaches the session lifecycle, loads briefing state, and renders the Briefing Card — this is the primary entry point to the session loop
**And** Layer B in-session commands are parsed:
- mode <name> → Command(verb="mode", target=name)
- mode / modes → Command(verb="modes", target=None)
- status → Command(verb="status", target=None)
- memory / what do you know → Command(verb="memory", target=None) — parser recognizes these in Epic 3; Nerve routes to a placeholder response ("Transparency coming soon. Your data is stored locally in nova.db.") until Epic 5 wires the full Knowledge Display
- forget <topic> → Command(verb="forget", target=topic) — parser recognizes in Epic 3; Nerve routes to placeholder response ("Forget capability coming soon.") until Epic 5 wires deletion. Partial command forget without target → "Tell me what to forget. Example: forget Meridian"
- help / ? → Command(verb="help", target=None)
- shutdown / quit / exit → Command(verb="shutdown", target=None) — all three route to the same graceful shutdown flow
**And** Layer C contextual replies are parsed with is_contextual=True:
- resume, yes, no, skip, cancel — valid only when the current UI state expects a response
- Outside a directed prompt context, contextual replies are treated as unknown input
**And** parsing is case-insensitive and deterministic: same input always produces same Command
**And** invalid input produces a helpful response with max 3 context-relevant suggestions, never "invalid command" or "error"
**And** partial commands produce specific guidance: mode without a valid target after mode edit → "Need one more detail. Try mode edit coding." (mode edit and mode create belong to Epic 6 — parser recognizes them in Epic 3 and routes to placeholder guidance)
**And** empty input in free command mode is a silent no-op (return to prompt, no acknowledgment)
**And** Skin never calls system logic directly — all Commands are routed to Nerve
**And** unit tests verify: all canonical commands parse correctly, bare nova boot path, case insensitivity, contextual reply scoping, invalid input response, partial command guidance, empty input no-op

### Story 3.5: Nerve Command Routing & Session Lifecycle

As a user,
I want my commands routed to the correct system with tier-awareness and policy decisions,
So that N.O.V.A. responds appropriately regardless of what's available.

**Acceptance Criteria:**

**Given** Skin produces Command objects and Nerve is the orchestrator
**When** this story is complete
**Then** Nerve routes commands to the appropriate system:
- mode <name> → Hands (mode restore)
- modes → Brain (list modes from config) → Skin (render mode list)
- status → Nerve assembles status (current mode, session duration, tier) → Skin renders
- help → Skin renders command table
- shutdown → Ritual (begin shutdown flow)
**And** Nerve manages session lifecycle:
- On bare nova boot: creates a session via Brain, sets initial tier state, assembles BriefingAggregate (merging Brain facts with NovaConfig modes), determines state, delegates to Ritual for ViewModel assembly, delegates to Skin for rendering
- On shutdown: delegates to Ritual, ensures session is ended in Brain
- On unexpected termination (SIGINT/close): best-effort state capture via signal handler
**And** Nerve checks tier state before cloud-dependent operations — if offline, returns honest unavailability through Voice (personality-bearing) or direct to Skin (operational)
**And** Nerve makes policy decisions: skip briefing if last session ended < threshold minutes ago (configurable in settings)
**And** Nerve never generates user-facing prose — that is Voice's job
**And** unit tests verify: correct routing for each command, tier check before cloud ops, session lifecycle (start → work → end), briefing assembly ownership (Nerve merges, not Brain), signal handler registration

### Story 3.6: Mode Restore & App Launching

As a user,
I want to type mode coding and have my configured apps launch automatically with per-app progress feedback,
So that I am in my workspace in one command instead of manually opening everything.

**Acceptance Criteria:**

**Given** at least one mode exists in config and the Hands system is active
**When** the user issues mode <name>
**Then** Nerve reads the mode config from NovaConfig and delegates to Hands
**And** Hands launches each configured app sequentially via subprocess/ShellExecute
**And** per-app progress renders inline as it happens: ✓ VS Code / ✗ Postman (not found — is it installed?)
**And** progress output goes direct to Skin (operational, bypasses Voice)
**And** graceful-partial pattern: if 2 of 3 apps launch, the session continues with the 2 — failures never block
**And** failure reasons are specific and actionable: "not found", "permission denied", "timed out", "already running"
**And** after all launches, a Voice-final-line summary: "Workspace ready." or "Workspace partially ready. {app} was skipped."
**And** "Workspace ready" and "Workspace partially ready" are distinct — never the same success language for partial restore
**And** if ALL apps fail: "No apps could be launched. Check mode config: mode edit coding" — session still starts
**And** AppLaunched events are emitted per app as each launch completes (success or failure)
**And** ModeRestored event is emitted once after the overall restore completes
**And** each app launch attempt is logged via AuditLogger — audit logging is observational: audit write failure must NOT block the restore or prevent event emission
**And** Epic 3 performs launch-only restore — N.O.V.A. does not explicitly call SetForegroundWindow or MoveWindow in this epic. Focus and arrange behavior belongs to Epic 6.
**And** workspace restore completes in under 30 seconds (NFR1)
**And** unit tests with mock Hands adapter verify: full success, partial success, total failure, per-app event emission, audit logging (including audit failure not blocking), correct progress output

### Story 3.7: Shutdown Flow & Seed Capture

As a user ending my session,
I want to type shutdown and capture a tomorrow seed in under 30 seconds,
So that my next session starts with context instead of a blank slate.

**Acceptance Criteria:**

**Given** the user is in an active session with at least one mode used
**When** the user types shutdown (or quit or exit)
**Then** Nerve delegates to Ritual to begin the shutdown flow
**And** Ritual renders a Shutdown Card (Rich Panel): session summary showing current mode, session duration, apps used
**And** Ritual prompts with one directed question: "What should you pick up tomorrow?"
**And** the user types 1-2 sentences below the panel
**And** empty input on the seed prompt: reprompt once ("Please confirm or cancel."), if still empty on second attempt: "Cancelled." and proceed with no seed
**And** skip or cancel during seed prompt exits cleanly with no seed stored
**And** on seed entry: Ritual delegates to Brain to persist:
- Session ended (ended_at, is_complete=True, seed_text, summary)
- Final workspace snapshot (snapshot_type=shutdown, typed WorkspaceSnapshotInput)
- Memory item with category=seed
**And** persistence completes before any confirmation is displayed (operational success reflects actual completion)
**And** confirmation rendered outside the panel as a Voice-final-line: "Planted for tomorrow." (or equivalent personality-bearing text)
**And** SeedSaved and SessionEnded events emitted only after Brain confirms the writes (persist-before-emit)
**And** audit log records seed_capture action — audit failure does not block shutdown completion
**And** shutdown completes in under 30 seconds of active user time (NFR4)
**And** shutdown, quit, and exit all route through this same flow — no alias bypasses seed capture
**And** unit tests verify: full shutdown with seed, shutdown with skip, shutdown with empty input (reprompt then cancel), persist-before-emit ordering, audit logging

### Story 3.8: Warm Resume (Session 2 Hero Moment)

As a returning user on day 2,
I want nova to show my yesterday's seed, suggest my last mode, and restore my workspace in one command,
So that I experience the hero moment: "It remembered where I left off."

**Acceptance Criteria:**

**Given** a previous session exists with a completed shutdown and seed
**When** the user runs nova
**Then** Briefing Card State C renders with:
- Seed text as the hero line (bold bright white, first content line)
- Last session mode, duration (pre-formatted display string), and apps listed below the seed
- Resume suggestion as the final bold line: "Resume coding mode?"
**And** the user can respond with resume or yes (contextual reply — valid only because the briefing prompted for it)
**And** on resume: mode restore fires (Story 3.6) with full progress feedback
**And** the user can respond with no and then issue any other command
**And** the user can type a different mode name directly instead of accepting the suggestion
**And** if the user types resume outside of a resume prompt context, they get: "Nothing to resume right now. Try mode <name> or mode to view available modes."
**And** the briefing renders in under 5 seconds (NFR3)
**And** the full flow (nova → briefing → resume → workspace ready) experientially targets under 2 minutes
**And** integration test verifies the complete continuity loop: session 1 shutdown with seed → session 2 startup → State C briefing shows the seed → resume → mode restore → working state

### Story 3.9: Status Command & Help Display

As a user during a session,
I want status to show what's active and help to show available commands,
So that I always know what N.O.V.A. is doing and what I can do.

**Acceptance Criteria:**

**Given** the user is in an active session
**When** the user types status
**Then** Skin renders a compact status display: current mode (or "no active mode"), session duration, capability tier
**And** status output goes direct to Skin (operational, bypasses Voice)
**And** when in degraded or offline tier, status shows both the current tier and which local capabilities remain available — one compact view

**Given** the user types help or ?
**When** the help command is processed
**Then** Skin renders a command table showing all T1 commands with brief descriptions
**And** the table includes: mode <name>, mode/modes, status, help, shutdown/quit/exit, and a note about contextual replies
**And** help does not include commands that are not in T1 scope (no audit, no self-update, no nova <name> shorthand)
**And** help output goes direct to Skin (operational)
**And** unit tests verify: status renders correctly with and without active mode, help shows all T1 commands

### Story 3.10: Crash Recovery & Unexpected Termination

As a user whose session was interrupted by a crash, power loss, or forced close,
I want N.O.V.A. to recover gracefully on next startup — detecting the interrupted session and handling it honestly in the briefing,
So that the continuity loop remains reliable even when things go wrong.

**Acceptance Criteria:**

**Given** the architecture requires graceful shutdown to capture state even on unexpected termination (NFR19) and the continuity loop must be highly reliable (NFR15)
**When** this story is complete
**Then** Nerve registers a SIGINT/SIGTERM signal handler (and Windows console close handler) at session startup that performs best-effort state capture:
- Persists the current session to Brain with is_complete=False (interrupted)
- Captures a best-effort workspace snapshot (snapshot_type=shutdown) if Eyes is available
- Does NOT attempt seed capture (no user interaction possible during forced close)
- Completes within a bounded timeout (e.g., 2 seconds) — never hangs on shutdown
**And** the signal handler is best-effort: if Brain write fails during forced close, the failure is logged and the process exits cleanly — no hang, no crash loop
**And** on next startup, Brain detects the interrupted session (is_complete=False in the most recent session row)
**And** Nerve/Ritual handle the interrupted session in the briefing flow:
- State C still activates if a prior completed session with seed exists (the interrupted session does not erase earlier seeds)
- If the interrupted session was the only session, State B activates (no usable seed)
- The briefing honestly acknowledges the interruption: "Last session ended unexpectedly." (one line, not dramatic)
- If the interrupted session had a mode active, that mode is suggested for resume
**And** the interrupted session is recoverable: its partial data (mode, start time, any pre-crash snapshots) is preserved in Brain, not discarded
**And** the last known good state (most recent completed session) is always recoverable regardless of how many interrupted sessions follow
**And** audit_log records the unexpected termination (action_type=tier_change or a session lifecycle event) if the write succeeds during the signal handler
**And** unit tests verify: signal handler captures session state, is_complete=False set correctly, next startup detects interrupted session, briefing handles interrupted session gracefully (State B and C paths), prior completed sessions are not affected by subsequent crashes
**And** integration test: simulate session → forced kill → restart → verify interrupted session detected and briefing is honest

---

## Epic 4: Context Awareness & Memory Enrichment

N.O.V.A. becomes aware of what's happening on the desktop — detecting the active window, tracking context changes, extracting meaning from window titles, and accumulating richer memory across sessions. The exclusion boundary is hardened: sensitive apps produce only opaque events at the capture layer. PromptBuilder enforces the cloud trust boundary as the only cloud egress path.

### Story 4.1: Eyes Win32 Context Capture

As a user in an active session,
I want N.O.V.A. to detect which app and window is in the foreground,
So that it builds awareness of my desktop context over time.

**Acceptance Criteria:**

**Given** the Eyes system is active during a session
**When** context polling is running
**Then** the Win32EyesAdapter polls win32gui.GetForegroundWindow() every 500ms–1s
**And** each poll captures: window handle, window title, process name (via psutil)
**And** the work performed per poll cycle stays under 100ms (NFR5) — the poll interval itself is 500ms–1s, the per-cycle cost must not exceed 100ms
**And** events are emitted only on change (deduplicated — same app/title does not re-emit)
**And** each context change emits a typed ContextChanged event with app_name, window_title, process_name fields
**And** polling runs as an asyncio task, non-blocking to the main session
**And** CPU usage during idle polling stays under 2% on a mid-range processor (NFR22)
**And** the adapter captures and filters only — no title parsing, no mode inference, no business logic in the adapter (adapters translate, never decide)
**And** failure behavior is deterministic:
- Transient polling failures (single win32gui/psutil call fails): log at WARNING level and continue with empty/partial result for that cycle — no crash, no blocking
- Unrecoverable initialization failure (pywin32 not available, persistent access errors): disable Eyes gracefully for the session with a one-time logged notice; session continues without context awareness
**And** the adapter catches pywin32/psutil exceptions and re-raises as domain exceptions at the port boundary
**And** unit tests use a mock Win32 adapter (no real Windows APIs in tests)
**And** tests verify: change detection, deduplication, graceful transient failure (continues polling), graceful initialization failure (disables cleanly), event emission

### Story 4.2: Exclusion Boundary at Capture Layer

As a privacy-conscious user,
I want sensitive apps (password managers, banking, etc.) excluded at the capture layer so no identifying details are ever stored or transmitted,
So that I trust N.O.V.A. with my desktop awareness.

**Acceptance Criteria:**

**Given** an exclusion list is loaded from exclusions.yaml (shipped defaults + user edits)
**When** Eyes detects a foreground window
**Then** Eyes checks the process name against excluded_apps[].match (case-insensitive substring) and the window title against excluded_title_patterns[] (case-insensitive substring)
**And** if either matches: Eyes emits a ContextChanged event with is_opaque=True, app_name=None, window_title=None, process_name=None
**And** to be precise about the boundary: excluded identifying details (app name, window title, process name) never enter storage, audit, transparency, cloud prompts, logs, error messages, or derived text. An opaque placeholder event may still flow through the system — the placeholder is the only thing downstream systems may see.
**And** opaque events propagate through all downstream systems as placeholders only:
- Brain stores only "protected_app_active" — no app name, no title
- Audit trail records opaque references only (target: "protected_app")
- PromptBuilder strips opaque events entirely from cloud payloads
- Transparency display shows "A protected app was active"
- Generated prose, summaries, and error messages never reveal excluded details
**And** the user can inspect and modify exclusions.yaml directly; changes take effect on next session start
**And** unit tests verify: matching by process name, matching by title pattern, opaque event fields, no identifying details in stored data
**And** integration test: excluded app context stays opaque across capture → storage → audit → transparency chain

### Story 4.3: Workspace Snapshots on Demand & Context Buffer

As a user,
I want N.O.V.A. to capture richer workspace snapshots during my session (not just startup/shutdown),
So that mode switches and on-demand captures provide more context for future briefings.

**Acceptance Criteria:**

**Given** Eyes is polling and context changes are tracked (Story 4.1)
**When** a snapshot is requested (mode switch, on-demand, periodic)
**Then** Eyes captures the full workspace state: list of open apps, focused app, current mode, active window titles
**And** snapshots are stored via Brain as workspace_snapshot records with typed WorkspaceSnapshotInput
**And** snapshot_type distinguishes: startup, shutdown, mode_switch, periodic
**And** a recent context buffer (in-memory, bounded) tracks the last N context changes for use in briefing assembly and mode inference
**And** context is persisted selectively, not continuously — startup/shutdown snapshots are the primary records; the buffer supports richer queries without storing every poll
**And** excluded apps in the snapshot are replaced with opaque placeholders
**And** unit tests verify: snapshot capture with mixed excluded/non-excluded apps, buffer bounded size, snapshot type classification

### Story 4.4: Context Extraction & Mode Inference

As a user,
I want N.O.V.A. to extract meaningful context from window titles and suggest the right mode,
So that briefings are more useful and mode suggestions are accurate.

**Acceptance Criteria:**

**Given** Eyes is tracking context changes and capturing window titles
**When** context extraction and mode inference run
**Then** context extraction is implemented in system/domain logic (not in the Win32 adapter) — Eyes adapter captures and filters; a domain-layer context processor interprets:
- VS Code: project name from "filename - ProjectName - Visual Studio Code"
- Browser: page title from "PageTitle - Chrome/Firefox/Edge"
- General: document name, application context
**And** extraction produces typed domain objects, not raw strings
**And** mode inference (also domain logic, not adapter): given current open apps, suggest the most likely mode from configured modes by matching app overlap
**And** mode suggestion is surfaced in the BriefingAggregate's suggested_mode field
**And** if no confident match exists, suggested_mode falls back to most recently used or default mode
**And** extraction and inference respect the exclusion boundary — opaque events are skipped, no excluded app details used
**And** the Win32 adapter contains zero title-parsing or inference logic — it provides raw captures; the domain layer decides what they mean
**And** unit tests verify: title parsing for VS Code, browser, generic apps; mode inference with varying app overlap; exclusion respected; adapter contains no business logic

### Story 4.5: Memory Accumulation & Enriched Briefings

As a returning user,
I want N.O.V.A.'s memory to compound across sessions so briefings get richer over time,
So that the product delivers its compounding-value thesis.

**Acceptance Criteria:**

**Given** multiple sessions have been completed with seeds, modes, and context
**When** Brain accumulates memory across sessions
**Then** Brain stores memory_items with appropriate categories: seed, session_note, context_summary, pattern
**And** Brain can retrieve relevant prior session context when generating briefings: recent memory items ordered by recency and relevance (FR21)
**And** Brain detects a minimal deterministic pattern set for T1 (FR22):
- Most-used mode name
- Typical session time-of-day
- Recurring project names from context summaries
**And** BriefingAggregate.recent_memory is populated with relevant memory items for the current briefing context
**And** enrichment is additive — it adds context to existing briefing fields, never removes or overwrites the structured seed/mode/apps fields
**And** context summaries are bounded: Brain stores summarized context per session, not unbounded raw captures. SQLite growth stays manageable (NFR23 — target under 100MB after 6 months)
**And** all accumulated memory is local (FR45) and inspectable via the transparency command (Epic 5)
**And** the user can back up their entire memory by copying the single nova.db file (FR23) — no special tooling required, documented in help or README
**And** unit tests verify: memory item storage/retrieval by category, pattern detection with deterministic sample session data, briefing aggregate enrichment, bounded summary storage

### Story 4.6: PromptBuilder Trust Boundary

As a privacy-first user,
I want all context sent to Claude API to be minimized and scrubbed of excluded content,
So that my raw memory never leaves my machine.

**Acceptance Criteria:**

**Given** the architecture defines PromptBuilder as the only cloud egress path
**When** any system needs Claude API reasoning
**Then** core/prompt_builder.py accepts local context + memory items from Brain and produces a minimized, cloud-safe prompt payload
**And** PromptBuilder:
- Produces summaries, never raw memory store contents
- Strips any excluded/opaque references entirely
- Enforces a token budget (basic in T1 — hard cap, not sophisticated optimization)
- Returns an immutable cloud-safe payload
**And** the Claude adapter ONLY receives output from PromptBuilder — never raw Brain data
**And** no system may call the Claude adapter directly with ad hoc context — PromptBuilder is the single gate
**And** if PromptBuilder cannot safely minimize or classify a piece of context, it omits it and falls back to local-only behavior (FR52)
**And** PromptBuilder output is immutable — adapters may transport but must not append hidden context
**And** no excluded content reappears through generated prose, summaries, paraphrases, error messages, or exception payloads
**And** local operations and local-only briefings do NOT depend on PromptBuilder or Claude — PromptBuilder is invoked only when cloud reasoning is actually requested. The local-first architecture must remain honest: all local flows (briefing from structured data, mode restore, shutdown, transparency, memory reads) work without PromptBuilder involvement.
**And** unit tests verify: raw memory is summarized not passed through, excluded items stripped, token budget enforced, immutable output, fallback on un-classifiable context
**And** integration test: end-to-end from Brain context → PromptBuilder → mock Claude adapter, verifying no raw/excluded content reaches the adapter

### Story 4.7: Bounded Memory Retention & Pruning Policy

As a user who runs N.O.V.A. daily for months,
I want memory to stay bounded so storage does not grow unbounded,
So that the SQLite database stays under 100MB after 6 months of daily use (NFR23).

**Acceptance Criteria:**

**Given** memory accumulates across sessions (seeds, session notes, context summaries, snapshots, audit entries)
**When** Brain writes new memory
**Then** a deterministic retention policy is enforced:
- Context summaries older than a configurable threshold (default: 90 days) are pruned automatically on session start
- Workspace snapshots of type=periodic older than the threshold are pruned (startup/shutdown/mode_switch snapshots are retained longer as they are higher-value)
- Seeds and session notes are retained indefinitely (they are the continuity backbone)
- Audit log entries are retained indefinitely in T1 (rotation is a T2 concern)
**And** pruning runs automatically at session start as a lightweight maintenance step — not during active session work
**And** pruning is bounded in time: if pruning would take longer than a few seconds, it batches and continues on next startup
**And** pruned data is deleted, not archived — this is not a backup mechanism
**And** pruning does NOT create a hidden secondary memory store (no "pruned items" table or archive)
**And** the retention threshold is documented but not user-configurable in T1 (a settings option is a T2 candidate)
**And** after pruning, the transparency command reflects the current state accurately
**And** unit tests verify: items older than threshold are pruned, seeds/session notes are retained, pruning is bounded in time, transparency reflects post-prune state, no hidden archive created

---

## Epic 5: Transparency, Trust & Deletion

A user can inspect all stored knowledge, selectively forget topics with atomic deletion, inspect the audit trail, and see tier status. Transparency matches SQLite exactly — no hidden state. SQLite corruption has an explicit recovery flow.

### Story 5.1: Transparency Command & Knowledge Display

As a user,
I want to ask "What do you know?" and see a complete, structured view of all my stored knowledge,
So that I can verify exactly what N.O.V.A. has learned and trust that nothing is hidden.

**Acceptance Criteria:**

**Given** the user types memory or what do you know during a session
**When** the transparency display is assembled
**Then** a transparency assembler (owned by Nerve or a dedicated collaborator, not Brain alone) merges data from two sources:
- Brain provides persisted facts from SQLite: session history (count, recent seeds, session notes, context summaries), detected patterns (minimal T1), recent action count/timestamp from audit_log
- Config module provides config-derived data: all configured modes with app counts, exclusion list summary, current settings
- Brain does NOT read mode YAML files or own config-derived data — that crosses the config ownership boundary
**And** Skin renders a Knowledge Display component (UX-DR6): Rich Panel with Tree structure:
- Modes (from config): names, app counts, last used
- Memory (from Brain): session count, recent seeds, notes, patterns
- Session (from Brain + Nerve): current session info, tier status
- Audit summary (from Brain): recent action count, last action
**And** the display shows everything stored — no hidden state, no omitted categories, no stale cache (NFR27)
**And** excluded items appear only as opaque placeholders: "A protected app was active" — no app name, no title, no identifying details
**And** the display ends with a trust invitation: "Want me to forget anything?"
**And** transparency response completes in under 3 seconds (NFR6)
**And** what the transparency command shows must match what is in the SQLite file — no divergence
**And** output goes direct to Skin (structured display, bypasses Voice)
**And** unit tests verify: transparency assembly from sample data with both Brain and config sources, tree rendering with all categories, opaque placeholder rendering, empty-state rendering

### Story 5.2: Selective Forget with Atomic Deletion

As a user,
I want to say "forget Meridian" and have all traces of that topic removed atomically across all tables,
So that I control my data and deleted content is truly gone.

**Acceptance Criteria:**

**Given** the user types forget <topic> during a session
**When** the forget flow begins
**Then** Nerve owns the interaction flow: receives the forget command, delegates matching to Brain, delegates preview/confirm to Skin
**And** Brain performs case-insensitive substring search for the topic across all stored data: memory_items.content, sessions.seed_text, sessions.summary, workspace_snapshots.workspace_data (JSON content)
**And** matching semantics are explicit and deterministic for T1:
- Case-insensitive substring match (e.g., "meridian" matches "Meridian project" and "Work on Meridian API")
- All matched candidates are returned to the interaction flow for preview
- The user sees every item that will be deleted before confirming
**And** Nerve delegates to Skin to render a Confirmation Prompt (UX-DR8): preview showing matched items grouped by table, count per table, what will be removed
**And** on confirm/yes: Brain deletes the target from all representations atomically:
- memory_items matching the target
- session seeds/summaries containing the target (cleared or removed)
- workspace snapshot data referencing the target
- Any persisted derived context referencing the target
**And** deletion is atomic from the user's perspective: all deletions succeed before any confirmation is shown. On partial failure, the system reports incomplete deletion and blocks false verification (NFR11)
**And** no hidden secondary memory stores undermine deletion — SQLite is the single system of record
**And** audit_log records the deletion event with: action_type=deletion, items_deleted count, affected tables, timestamp, result (success/partial/failed). The audit entry does NOT contain the forgotten topic text, the deleted content, or any identifying details of what was forgotten — the audit records the action and its scope, never the deleted data.
**And** MemoryForgotten event emitted only after Brain confirms all deletes succeeded (persist-before-emit). The event carries target as a correlation identifier and items_deleted count, not the deleted content.
**And** after deletion, the user is invited to re-verify: "Run memory to verify."
**And** cancel/no at confirmation exits cleanly with nothing deleted
**And** forget without a topic: "Tell me what to forget. Example: forget Meridian"
**And** unit tests verify: case-insensitive substring matching across tables, atomic deletion (all-or-nothing), audit log entry contains no deleted content, persist-before-emit ordering, Nerve owns interaction flow (not Brain)
**And** integration test: forget → verify via transparency command that deleted content is gone from all tables and audit log contains no trace of the deleted data

### Story 5.3: Audit Trail Inspection

As a user,
I want to inspect the audit trail of actions N.O.V.A. has taken,
So that I can verify what was automated and when.

**Acceptance Criteria:**

**Given** the user wants to see what N.O.V.A. has done
**When** the audit trail is queried (via transparency command's Session section or a future dedicated view)
**Then** Brain queries the audit_log table and returns recent actions as a structured list
**And** each entry shows: timestamp, action type (from ActionType enum), target (opaque for excluded contexts), result (success/failed/skipped)
**And** the display uses a Rich Table (Action Log component)
**And** excluded context targets show only "protected_app" — never the actual app name
**And** deletion audit entries show action metadata only — never the forgotten topic or deleted content
**And** audit trail is queryable at minimum through the transparency command; also inspectable directly via SQLite tools (NFR28)
**And** output goes direct to Skin (structured operational display)
**And** unit tests verify: audit retrieval with mixed action types, opaque targets for excluded context, deletion entries contain no deleted content, correct table formatting

### Story 5.4: Tier Status Display & Notification

As a user,
I want to see the current capability tier and be notified once when it changes,
So that I always know what N.O.V.A. can and cannot do right now.

**Acceptance Criteria:**

**Given** the tier state machine exists (Epic 1) and tier changes emit TierChanged events
**When** a tier transition occurs
**Then** Skin renders a Tier Notice (UX-DR7): single amber warning line with capability list, no panel
**And** the notice is shown once per transition, not repeated on every interaction
**And** the notice states: what changed, what is still available, what is unavailable
**And** status command always shows current tier and available capabilities in one compact view (FR35, FR36)
**And** tier status is embedded in the transparency command's Session section (NFR30)
**And** tier transitions are logged via AuditLogger (action_type=tier_change from the shared ActionType enum)
**And** unit tests verify: tier notice renders once per transition, status includes tier, transparency includes tier, no repeated notices

### Story 5.5: SQLite Corruption Recovery Flow

As a user whose database has been corrupted or gone missing unexpectedly,
I want an explicit recovery flow that gives me clear options,
So that I never lose trust because N.O.V.A. silently recreated my data.

**Acceptance Criteria:**

**Given** N.O.V.A. boots and the storage engine detects nova.db is missing, unreadable, or fails integrity check
**When** the issue is detected
**Then** the system distinguishes between two cases:
- True first run (no prior installation state — no nova.db has ever existed, no backups directory populated, no prior sessions): normal initialization path via Epic 1 migration runner. This is NOT a recovery scenario.
- Unexpected missing or corrupted DB (evidence of prior state exists — backups directory populated, mode configs present, or corrupted file found): explicit recovery flow triggered.
**And** for the recovery case, N.O.V.A. does NOT silently create a new database — that violates the trust contract
**And** N.O.V.A. presents an explicit recovery prompt:
- If backups exist: list most recent 3 with timestamps, offer [1] Start fresh [2] Restore from backup [3] Exit
- If no backups but corruption detected: move corrupted file to backups/nova_corrupted_{timestamp}.db, then offer [1] Start fresh [3] Exit
**And** the user chooses their recovery path explicitly
**And** after recovery, N.O.V.A. starts in State A or B depending on whether mode configs still exist in the user data directory
**And** recovery flow never destroys evidence silently — corrupted files are preserved for manual recovery
**And** audit_log records the recovery using a proper ActionType enum value (add database_recovery to the ActionType enum in core/types.py — no raw string action types)
**And** unit tests verify: first-run vs. recovery distinction, corrupted DB detection, backup listing, recovery path selection, corrupted file preservation, correct ActionType enum usage

### Story 5.6: Backup & Restore User-Facing Flow

As a user,
I want a clear, documented, testable way to back up and restore my N.O.V.A. data,
So that I can protect my accumulated memory without specialized tooling (NFR31).

**Acceptance Criteria:**

**Given** NFR31 requires backup/restore without specialized tooling
**When** this story is complete
**Then** the backup/restore path is explicitly defined and documented:
- Backup: copy %LOCALAPPDATA%/nova/nova.db to any safe location (the entire memory, session history, and audit trail is this one file)
- Full data backup: copy the entire %LOCALAPPDATA%/nova/ directory (includes DB, mode configs, exclusions, settings, logs)
- Restore: replace %LOCALAPPDATA%/nova/nova.db with the backup copy (N.O.V.A. picks it up on next startup; migration runner handles any schema version differences)
**And** the help command includes backup/restore guidance: "Backup: copy %LOCALAPPDATA%/nova/nova.db. Restore: replace the file and restart."
**And** the transparency command (Knowledge Display) shows the data directory path so the user always knows where their data lives
**And** automatic pre-migration backups (from Epic 1 Story 1.5) are surfaced: the backups/ directory is mentioned in help and transparency output so the user knows automatic backups exist
**And** no nova backup or nova restore command in T1 — file copy is the documented interface (a dedicated command is a T2 candidate)
**And** unit tests verify: help output includes backup guidance, transparency output includes data directory path, restored DB from backup is accepted on startup

---

## Epic 6: Desktop Actions & Workspace Orchestration Expansion

Mode restore becomes richer — N.O.V.A. can focus running windows and arrange them in basic layouts, all within the safe-only action boundary. Users can create modes on the fly, edit existing modes, and bookmark mode state when switching. All within T1 safe-only scope.

### Story 6.1: Window Focus & Arrange in Mode Restore

As a user,
I want mode restore to also focus running windows and arrange them in a basic layout,
So that my workspace is fully set up in one command, not just apps launched.

**Acceptance Criteria:**

**Given** Epic 3 delivers launch-only mode restore
**When** this story is complete
**Then** Hands can focus a running application window via win32gui.SetForegroundWindow
**And** Hands can arrange windows in basic layouts via win32gui.MoveWindow as part of mode restore
**And** T1-safe focus rule: focus/raise is allowed ONLY inside an explicit user-initiated mode restore, ONLY for mode-configured target windows, NEVER as a background or unsolicited action, and NEVER outside the restore flow
**And** focus and arrange are safe-only actions within the T1 boundary — no window closing, no file modification, no keyboard/mouse simulation
**And** if a focus/arrange call fails (window not found, permission denied, elevated process), the action is skipped with a logged warning — graceful-partial pattern
**And** safety boundaries fail closed on ambiguity: if Hands cannot confirm a target window is safe to manipulate, it declines rather than guessing
**And** per-action progress updates inline: ✓ VS Code (focused) / ✗ Terminal (could not arrange — skipped)
**And** each focus and arrange attempt is logged via AuditLogger (action_type=app_focus / window_arrange)
**And** workspace restore with focus+arrange still completes in under 30 seconds (NFR1)
**And** unit tests with mock Win32 adapter verify: focus success/failure, arrange success/failure, graceful-partial, fail-closed on ambiguity, focus only during user-initiated restore, audit logging

### Story 6.2: Mode State Bookmarking on Switch

As a user switching between modes,
I want N.O.V.A. to bookmark my current mode state before switching,
So that I can return to exactly where I was in the previous mode.

**Acceptance Criteria:**

**Given** the user is in an active mode and issues mode <different_name>
**When** the mode switch begins
**Then** Nerve captures the current mode state before switching:
- Current open apps (via Eyes snapshot)
- Current mode name
- Timestamp
**And** the bookmark is stored as a workspace_snapshot with snapshot_type=mode_switch via Brain (typed WorkspaceSnapshotInput)
**And** bookmark confirmation is rendered: "Bookmarked coding mode. Switching to study..."
**And** the new mode restore fires (launch + focus + arrange)
**And** mode switching does NOT auto-close the previous mode's apps — it adds the new mode's apps
**And** bookmarks are surfaced in future briefings when the user returns to that mode
**And** audit_log records the mode switch (action_type=mode_switch)
**And** unit tests verify: bookmark captured before switch, snapshot stored correctly, bookmark confirmation displayed, no app closing

### Story 6.3: Ad-Hoc Mode Creation

As a user who wants a new mode mid-session,
I want to create modes on the fly with 2-3 questions,
So that I can adapt N.O.V.A. to new work contexts without leaving my session.

**Acceptance Criteria:**

**Given** the user types mode create during a session (explicit entry) OR tries to switch to a nonexistent mode (implicit entry)
**When** the ad-hoc creation flow starts
**Then** for explicit entry (mode create): wizard asks mode name → apps → optional folders/URLs → summary → save
**And** for implicit entry (mode studygroup): "No mode named 'studygroup.' Create it?" → on yes, name is pre-filled, proceed to apps question
**And** the flow is 2-3 questions: name (or pre-filled), apps (required, at least one), optional extras (folders/URLs — offered once, skippable)
**And** app names entered naturally ("Chrome", "VS Code") — resolved to executable paths via known app registry + PATH lookup
**And** unresolvable apps: "Couldn't find 'AppName' — add it manually to the mode file later." Mode creation continues.
**And** confirmation summary shown before saving: mode name, apps, any extras
**And** mode is written immediately to %LOCALAPPDATA%/nova/modes/{mode_name}.yaml conforming to the pinned schema (Story 1.0)
**And** after creation, offer to switch: "Switch to studygroup now?" For implicit entry (user tried to switch), auto-switch after creation without re-asking.
**And** cancel at any prompt during creation exits cleanly: "Mode creation cancelled." No partial mode saved.
**And** no starter templates offered during ad-hoc creation (that is first-run wizard only)
**And** audit_log records mode creation with action_type=mode_create (proper typed ActionType enum value, not smuggled through mode_switch details)
**And** unit tests verify: explicit entry flow, implicit entry flow, cancel at each step, unresolvable app handling, YAML output matches schema, auto-switch on implicit entry, audit uses mode_create action type

### Story 6.4: Mode Editing via Command

As a user,
I want to edit an existing mode via mode edit <name>,
So that I can refine my modes without manually editing YAML files.

**Acceptance Criteria:**

**Given** the user types mode edit coding during a session
**When** the edit flow starts
**Then** N.O.V.A. loads the current mode config and presents it: name, apps list, folders, URLs
**And** the user can modify individual fields conversationally: add/remove apps, change folders, change URLs
**And** changes are written back to the YAML file immediately on confirmation
**And** the mode config is reloaded into the active NovaConfig (or takes effect on next mode switch)
**And** cancel exits the edit flow with no changes saved
**And** mode edit without a target: "Need one more detail. Try mode edit coding. Or run modes to see available modes."
**And** mode edit nonexistent: "No mode named 'nonexistent'. Run modes to see available modes."
**And** audit_log records mode edit with action_type=mode_edit (proper typed ActionType enum value)
**And** unit tests verify: load existing config, modify and save, cancel without save, missing target guidance, nonexistent mode handling, audit uses mode_edit action type

### Story 6.5: Full Mode Configuration — Folders & URLs

As a power user,
I want modes to fully support folders and URLs alongside apps,
So that my mode configurations capture everything about a work context.

**Acceptance Criteria:**

**Given** modes created in earlier epics support apps as the primary config
**When** this story is complete
**Then** mode restore also opens configured URLs (in default browser) on mode restore
**And** folders in mode config are used for context awareness (Eyes can reference them for richer context extraction) — not auto-opened in T1
**And** the modes command shows full mode details including folders and URLs
**And** direct YAML editing by power users works correctly — the config loader handles all fields from the pinned schema (Story 1.0)
**And** this story does NOT introduce "behavior flags" or any schema fields beyond what was pinned in Story 1.0 (name, apps, folders, urls, is_default). If new schema fields are needed, they must be added through an explicit schema amendment propagated consistently.
**And** unit tests verify: URL launch on restore, folder association in context, full mode display, YAML round-trip with all pinned fields

**Note on FR43:** FR43 (confirmation for beyond-safe actions) is removed from T1 story scope. T1 ships safe-only actions with fail-closed behavior on ambiguity (Story 6.1). A real confirmation gate for higher-tier actions belongs to v0.2+ when the action safety model expands beyond safe-only. FR43 remains in the PRD for future implementation.

---

## Epic 7: Personality, Voice & Conversational Polish

N.O.V.A. speaks with its full personality doctrine — sharp, loyal, witty. Bluntness is configurable (Calm/Direct for T1). Strategic praise fires when earned. Context-adaptive style adjusts between briefings, work, shutdown, and failure. Self-trimming rituals fade when unused. Earlier epics already respect the Voice/Skin split — this epic replaces functional placeholder prose with the full personality system.

### Story 7.1: Voice System & Personality Doctrine

As a user,
I want N.O.V.A. to respond with its personality doctrine — sharp, loyal, witty — not generic AI assistant framing,
So that every interaction feels intentional and earned.

**Acceptance Criteria:**

**Given** earlier epics use functional placeholder prose in Voice
**When** this story is complete
**Then** Voice owns all personality-bearing text generation as a system responsibility
**And** in full tier: Voice uses the Claude adapter (through PromptBuilder) as the generation backend
**And** in degraded/offline tiers: Voice returns None for prose enrichment, and the system falls back to structured rendering or deterministic local text — local operations never depend on cloud prose generation
**And** the Claude system prompt (used in full tier) encodes the complete Personality Doctrine:
- Prohibited patterns: never "How can I help you today?", never "I'd be happy to...", never "Great question!", never apologetic AI framing, never emoji in standard output, never exclamation marks
- Required patterns: brevity by default ("Done." is valid), direct address, earned familiarity, honest failure, user agency
**And** Voice generates text for: briefing prose enrichment, shutdown confirmations, restore summaries, error explanations, failure alternatives
**And** operational output (✓/✗ progress, tier notices, status tables, confirmations, transparency trees) continues to bypass Voice and go direct to Skin
**And** Claude API round-trip stays under 3 seconds typical with prompt caching (NFR7)
**And** API cost stays under $2.50/month at 50 turns/day with prompt caching (NFR25)
**And** unit tests with mock Claude adapter verify: prohibited patterns never appear in output, required patterns present, operational output bypasses Voice, degraded/offline fallback produces valid structured output without Claude

### Story 7.2: Configurable Bluntness Levels

As a user,
I want to configure how direct N.O.V.A. is — calm or direct — through my settings,
So that the personality matches my comfort level.

**Acceptance Criteria:**

**Given** the user has a bluntness setting in settings.yaml (default: direct)
**When** Voice generates any personality-bearing response
**Then** bluntness level affects phrasing, not observation selection — both levels surface the same information
**And** Calm: gentle observation, no judgment ("You've been away from VS Code for a while. Want to switch back?")
**And** Direct: clear statement, no padding ("90 minutes on YouTube. Coding mode is still active.")
**And** Ruthless is NOT available in T1 — if set in config, falls back to direct with a logged warning
**And** bluntness level is read from NovaConfig at Voice initialization
**And** bluntness is an input to the Claude system prompt construction (full tier) and affects deterministic local text selection (degraded/offline)
**And** unit tests verify: same observation produces different phrasing per level, ruthless falls back to direct

### Story 7.3: Strategic Praise & Context-Adaptive Style

As a user,
I want N.O.V.A. to occasionally acknowledge real progress and adapt its tone to what I am doing,
So that interactions feel earned and contextually appropriate.

**Acceptance Criteria:**

**Given** memory has accumulated across sessions
**When** Voice generates responses
**Then** strategic praise triggers only when genuinely earned: completing a multi-session task, returning after a break, sustained focus
**And** praise is max once per session — zero praise is the normal state
**And** praise phrases are short and specific: "Clean work." / "That was the right call." / "Solid session." — no exclamation marks, no special formatting
**And** context-adaptive style adjusts per the UX spec:
- During active work: minimal, one-line responses
- Briefing: structured, information-dense, warm but concise
- Shutdown: one question, brief confirmation, forward-looking
- When asked for explanation: more detailed, still direct
- Error/failure: honest, alternative-offering, never apologetic
**And** personality progression is driven by memory depth, not calendar days — if the user has many sessions with seeds and consistent modes, personality warms; if sparse, stays professional
**And** unit tests verify: praise max once per session, praise not triggered on routine actions, style adapts per context type

### Story 7.4: Self-Trimming Rituals

As a user who has been using N.O.V.A. for weeks,
I want ritual elements that I never use to fade away without me having to configure anything,
So that the product stays lean and respects my actual usage patterns.

**Acceptance Criteria:**

**Given** ritual elements (briefing sections, shutdown prompts, mode suggestions) have been active for multiple sessions
**When** Brain detects that specific ritual elements are consistently unused or skipped
**Then** those elements are suppressed or de-emphasized in future sessions (FR31)
**And** suppression is gradual: first reduce prominence, then omit — never permanently delete unless the user chooses to
**And** if the user re-engages with a suppressed element, it returns to full prominence
**And** self-trimming applies to: mode suggestions that are never accepted, briefing context that is never acted on, optional seed prompt metadata
**And** self-trimming must NEVER fully suppress or remove:
- Core shutdown seed capture — the continuity loop is non-negotiable
- Trust/privacy disclosures when required (deletion confirmations, tier change notices)
- Required recovery or confirmation prompts
- Transparency command completeness
**And** the trimming logic is deterministic and based on usage counters (not Claude reasoning)
**And** unit tests verify: usage tracking, suppression after threshold, re-engagement restores prominence, no permanent deletion, core continuity rituals cannot be suppressed

---

## Epic 8: Capability Tiers & Graceful Degradation (Integration/Hardening)

N.O.V.A. handles the real world. This epic is cross-cutting integration hardening, not isolated module work. All three capability tiers (full/degraded/offline) are tested as integrated system behavior across every prior epic's functionality.

### Story 8.1: Degraded Tier Integration Testing

As a user experiencing intermittent API connectivity,
I want N.O.V.A. to degrade gracefully — local operations continue, Voice is bypassed, raw data shown verbatim,
So that my session is never blocked by API issues.

**Acceptance Criteria:**

**Given** the Claude API becomes intermittent (2+ consecutive failures trigger degraded tier via TierManager)
**When** the system enters degraded mode
**Then** all local operations continue uninterrupted: mode switching, workspace restore, memory reads, transparency, shutdown, seed capture
**And** Voice is bypassed — briefings render from structured BriefingViewModel fields alone (prose_enrichment=None), which is always sufficient
**And** raw seeds and session notes are shown verbatim instead of Claude-synthesized prose
**And** cloud reasoning requests may be queued for retry when connectivity returns (FR59), subject to these constraints:
- The retry queue is bounded (max N entries, configurable, default small)
- The queue is in-memory only, non-persistent — NOT a hidden secondary memory store
- Only safe, recomputable enrichment requests are queued (prose enrichment, briefing polish) — never critical user data or actions
- The queue is non-authoritative: if items expire or are dropped, no data is lost
**And** the user sees a single Tier Notice (amber, shown once): "Cloud reasoning degraded. Local operations still working."
**And** status command shows: "Tier: degraded" with list of available/unavailable capabilities
**And** no command grammar, state machine shape, or event model changes between tiers — fallback preserves structure
**And** integration tests verify: full session flow (briefing → mode → work → shutdown) completes successfully in degraded tier, Voice output absent, structured fields sufficient, retry queue bounded and non-persistent

### Story 8.2: Offline Tier Integration Testing

As a user with no API connectivity,
I want N.O.V.A. to operate in offline-local-only mode with all local features functional,
So that I can still use workspace modes, view my memory, and shut down with a seed.

**Acceptance Criteria:**

**Given** the Claude API is completely unavailable (health checks consistently fail, as determined by TierManager)
**When** the system enters offline tier
**Then** all local operations function: mode switching, workspace restore, memory reads, mode listing, transparency command, shutdown with seed, status
**And** no cloud reasoning is attempted — Voice returns None for all generation requests
**And** any degraded-tier retry queue is flushed (non-persistent, non-authoritative — items are simply dropped, no data loss)
**And** briefings render from structured fields only — no "enrichment unavailable" text, just clean structured display
**And** shutdown flow works identically — seed capture, session end, all persistence
**And** transparency command shows full knowledge including tier: "offline-local-only"
**And** stored briefings and session notes accessible verbatim (FR60 — degraded/offline integration testing)
**And** tier notice shown once: "Cloud reasoning unavailable. Local operations active."
**And** integration tests verify: complete session lifecycle in offline tier, all local commands functional, no cloud calls attempted, seed capture and resume work across offline sessions

### Story 8.3: Tier Recovery & Catch-Up Briefing

As a user whose API connectivity has been restored,
I want N.O.V.A. to acknowledge recovery and offer to catch up on what happened during the outage,
So that I trust the system handles transitions honestly.

**Acceptance Criteria:**

**Given** the system was in degraded or offline tier and a health check succeeds (detected by TierManager)
**When** the tier transitions back to full
**Then** tier notice: "Cloud reasoning restored. Catch-up briefing?" (shown once)
**And** if the user accepts: Voice synthesizes what happened during the outage period — which modes were active, how long the outage lasted, any seeds planted, session activity (FR37)
**And** if the user declines: session continues normally
**And** the catch-up briefing uses only locally-captured data from the outage period — it does not fabricate or hallucinate events
**And** tier recovery is logged via AuditLogger (action_type=tier_change, from offline/degraded to full)
**And** unit tests verify: recovery detection, catch-up offer, synthesis from local data only, decline path

### Story 8.4: Malformed API Response Handling

As a user,
I want a single bad API response to fall back gracefully without degrading the whole system,
So that transient API issues do not interrupt my session.

**Acceptance Criteria:**

**Given** the Claude adapter returns a malformed or unparseable response (bad JSON, truncated, content filter)
**When** the response cannot be processed
**Then** the specific operation falls back to local behavior — this is NOT a tier change
**And** Voice: skips prose enrichment, renders from structured data only
**And** Brain: skips synthesis, uses raw local data
**And** a single inline notice: "Cloud response couldn't be processed. Using local fallback."
**And** no panel, no modal, no options menu — one line where the response would have appeared
**And** the malformed response is reported to TierManager as a failure signal — TierManager applies its centralized degradation policy (2+ consecutive failures trigger degradation). No parallel degradation logic exists outside TierManager.
**And** the malformed response is logged at ERROR level to nova.log for debugging — never shown raw to the user
**And** no automatic retry in a user-blocking loop — fail fast, fall back, continue
**And** unit tests verify: single malformed response does not change tier (TierManager tolerant degrade), local fallback produces valid output, failure signal routed through TierManager (not ad hoc), 2+ consecutive failures via TierManager trigger degradation, error logged not displayed

### Story 8.5: Partial Restore Under Degraded Conditions

As a user restoring a workspace when conditions are imperfect,
I want partial failures handled cleanly across the full system,
So that I always get as much functionality as possible.

**Acceptance Criteria:**

**Given** the system is in any tier and a workspace restore is attempted
**When** some components fail (apps not found, focus fails on elevated windows, Voice unavailable for summary)
**Then** the graceful-partial pattern applies across all systems:
- Hands: launched apps succeed, failed apps reported with reason, session continues
- Voice unavailable: restore summary uses operational text ("Workspace ready." / "Workspace partially ready.") direct to Skin
- Eyes initialization failed: session continues without context awareness
**And** for non-critical restore-side writes (optional mode-switch snapshots, observational context captures): write failure is logged with a warning, session continues
**And** for critical continuity writes (shutdown seed/session end, memory persistence): the persist-before-confirm contract is NOT weakened — these must succeed before confirmation is shown. If a critical write fails, the user is notified explicitly ("Could not save session data. Please try again or check storage.")
**And** the composite outcome is communicated honestly — never "Workspace ready" when components failed
**And** the session is always functional — partial is better than blocked
**And** integration tests verify: restore with mixed Hands success/failure + Voice offline + Eyes disabled → session functional with honest reporting; critical write failure produces explicit user notification, not silent success

### Story 8.6: User & Developer Runbook

As a user or developer,
I want a single runbook documenting all recovery paths, common issues, and operational procedures,
So that when something goes wrong I know exactly what to do without reading source code.

**Acceptance Criteria:**

**Given** N.O.V.A. has multiple failure modes and recovery paths spread across epics
**When** this story is complete
**Then** a runbook document exists (docs/runbook.md or equivalent) covering:
- **Data recovery:** SQLite corruption (Story 5.5 recovery flow), backup/restore via file copy (Story 5.6), pre-migration automatic backups location
- **API key issues:** expired/revoked key → edit settings.yaml (Story 2.5), missing key → offline-local-only tier
- **Tier degradation:** what each tier means, what works in each, how recovery happens
- **Crash recovery:** interrupted session handling (Story 3.10), what data survives a hard crash, what does not
- **Common errors:** app not found during restore, pywin32 not available, permission errors on elevated windows
- **Developer reset:** how to reset test state vs. user data vs. full reinstall — clearly separated
- **Log location:** %LOCALAPPDATA%/nova/logs/nova.log, how to read it, what log levels mean
**And** the runbook is written for the target audience: technical users (builders, students, power users) — not support agents
**And** each section follows a consistent format: symptom → cause → fix → verification
**And** the runbook does not duplicate architecture docs — it references them where appropriate
**And** the runbook is verified against actual system behavior (not aspirational — every documented path must work)
