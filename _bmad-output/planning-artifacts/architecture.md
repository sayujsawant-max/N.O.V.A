---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-04-13'
inputDocuments:
  - "prd.md"
  - "product-brief-nova-distillate.md"
  - "product-brief-nova.md"
  - "ux-design-specification.md"
  - "research/technical-local-first-windows-ai-assistant-stack-research-2026-04-13.md"
  - "research/market-nova-desktop-ai-assistant-research-2026-04-13.md"
  - "research/domain-local-first-personal-ai-agents-research-2026-04-13.md"
workflowType: 'architecture'
project_name: 'AI Assistant'
user_name: 'Sayuj'
date: '2026-04-13'
architecturePriorities:
  - "terminal-first MVP"
  - "local-first data model"
  - "modular monolith first"
  - "explicit capability tiers: full / degraded / offline-local-only"
  - "workspace-level restore only in v0.1"
  - "trust model: transparency, exclusion boundaries, deletion propagation, audit trail"
  - "build for T1 first: setup, one mode, shutdown seed, next-session resume, SQLite memory, transparency"
conflictResolution:
  - "prd.md — source of truth for scope, FRs, NFRs, tiers, non-negotiables"
  - "product-brief-nova-distillate.md — source of truth for 8-system architecture, key decisions, handoff constraints"
  - "product-brief-nova.md — supporting context"
  - "ux-design-specification.md — source of truth for terminal interaction model, component strategy, experience constraints"
  - "technical research — feasibility guidance, not higher authority than PRD"
  - "market research — positioning context"
  - "domain research — landscape context"
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
60 FRs across 10 categories. The architectural weight is concentrated in five areas:
1. **Continuity loop** (FR25-31, FR44) — the end-to-end flow from shutdown seed to next-session resume. This is the hero path and the first architecture milestone.
2. **Memory and persistence** (FR19-24) — local SQLite as the single source of truth for all accumulated state. Memory must compound across sessions while remaining inspectable and deletable.
3. **Trust and transparency** (FR32-38, FR45-52) — not a settings layer but a behavioral system. Exclusion at capture, minimization before cloud, deletion across all representations, audit for every automated action.
4. **Workspace orchestration** (FR7-13, FR39-44) — mode switching, app launching, window management. Safe-only tier for v0.1 with a clear path to earned autonomy.
5. **Context awareness** (FR14-18) — lightweight Windows API polling that feeds the Brain and Nerve. Must be invisible (<100ms) and respect exclusion boundaries.

**Non-Functional Requirements:**
31 NFRs with hard budgets that constrain architectural choices:
- **Performance:** Workspace restore <30s, briefing <5s, context poll <100ms, transparency <3s, shutdown <30s active time
- **Resource:** Memory <750MB, CPU idle <2%, SQLite <100MB after 6 months, API cost <$2.50/month
- **Security/Privacy:** No raw memory to cloud, exclusion at capture layer, user-inspectable SQLite, no telemetry without opt-in, deletion propagation before transparency re-query
- **Reliability:** Local ops never depend on cloud, tier transition detection <5s, non-destructive schema migration with auto-backup, graceful shutdown state capture
- **Auditability:** Every automated action logged, transparency command shows complete state, audit trail queryable

**Scale & Complexity:**
- Primary domain: Desktop-native AI companion (Python, asyncio, Windows 11)
- Complexity level: High
- Estimated architectural components: 8 logical systems mapping to ~10-12 Python modules (8 system modules + shared infrastructure for storage, config, events)

### Technical Constraints & Dependencies

**Hard constraints (non-negotiable):**
- Single Python process, asyncio event loop, modular monolith
- Ports-and-adapters: every subsystem defines an abstract port and at least one concrete adapter
- SQLite single-file local storage, user-readable, backup = file copy
- Rich library for all terminal rendering — no GUI, no Textual in v0.1
- Session-based only — no background daemon, no system tray
- Claude API as reasoning backend with prompt caching
- Windows 11 only — win32gui, psutil, subprocess for OS integration
- Safe-only desktop actions in v0.1 (launch, focus, arrange)

**External dependencies:**
- Claude API (cloud) — reasoning, briefing generation, conversational synthesis. Single point of failure for reasoning; architecture must isolate this behind a port with graceful degradation.
- Win32 APIs (local) — pywin32, psutil for context awareness and desktop actions. Known fragility: elevated processes, Electron/WPF incomplete automation trees.
- Python ecosystem — Rich, sqlite3 (stdlib), pywin32, psutil. Managed via uv.

**Evolution path:**
- T1 → T2: add richer briefing, more modes, guided setup polish, audit trail. Same architecture, more content.
- T2 → T3: personality tuning, robust mode config UX, trust behavior polish. Same architecture, more polish.
- v0.1 → v0.15: Shield system activates (focus protection, DND). New module, existing event bus.
- v0.1 → v0.2: Voice (STT/TTS) as new adapters behind existing ports. Skin upgrades to Textual. sqlite-vec or LanceDB for semantic search. New adapters, same ports.
- v0.2 → v1.0: Tauri GUI replaces Skin adapter. Local LLM as alternative Brain adapter. MCP integration via new Hands adapters.

### Cross-Cutting Concerns Identified

1. **Capability tier enforcement** — Every subsystem must expose a health check, support three operational modes (full/degraded/offline), and report honestly. The Nerve orchestrates tier state and broadcasts transitions. This is an architecture-level behavior, not UI decoration.

2. **Trust boundary (local vs. cloud)** — A hard boundary between local-only data and cloud-eligible derived context. Enforced at two points: (a) the capture layer (Eyes) filters excluded contexts before they reach Memory, and (b) the cloud prompt construction pipeline (Brain → Claude adapter) strips raw memory and sends only minimized summaries.

3. **Audit trail** — A cross-cutting concern that touches every system performing automated actions (Hands, Ritual, Brain). Single audit table in SQLite, append-only, queryable by user. Deletion events log the action, not the deleted content. When excluded/sensitive context is involved, audit entries record only opaque references (e.g., "action performed on a protected app") — raw protected details never enter the audit trail. This keeps the trust boundary clean across storage, transparency, and logging.

4. **Exclusion boundaries** — Sensitive-context filtering applied at the Eyes capture layer. Excluded apps produce opaque events ("a protected app was active") that propagate through Memory, Briefing, Transparency, Cloud prompts, and Audit trail as placeholders only. The exclusion list is local config, user-editable, ships with sensible defaults.

5. **Event orchestration** — The Nerve is the central event router. Systems communicate through events, not direct calls. This enables: loose coupling between systems, tier-aware routing (suppress cloud-dependent actions when offline), and a clear place to add new systems (Shield in v0.15, Voice in v0.2) without rewiring existing ones.

6. **Schema migration safety** — Every SQLite schema change must: auto-backup before migration, be non-destructive, provide a rollback path, and never silently modify user data. The update command (`nova self-update`) triggers migration explicitly.

### T1 Scope Lock

T1 is the first architecture milestone. Everything below is the authoritative boundary. Anything not listed is out of T1 scope. All module boundaries, data schema, and event flow decisions must be validated against this scope first.

**T1 Continuity Loop (the hero path):**

Setup → one mode → shutdown seed → next-session resume → SQLite persistence → transparency query

**T1 Systems — Active vs. Stubbed:**

| System | T1 Status | T1 Scope |
|--------|-----------|----------|
| **Nerve** | Active | Orchestration, tier state machine, event routing, policy decisions |
| **Brain** | Active | Sessions, seeds, snapshots, memory items, transparency queries, deletion propagation |
| **Eyes** | Active | Win32 context capture, workspace snapshots, exclusion filtering |
| **Hands** | Active | Safe desktop actions only: launch, focus, arrange. Per-action audit logging |
| **Ritual** | Active | Briefing assembly (States A/B/C), shutdown capture flow, seed lifecycle |
| **Voice** | Active | Text personality generation via Claude. Calm + Direct bluntness only |
| **Skin** | Active | Rich terminal rendering, deterministic command parsing, prompt I/O |
| **Shield** | Stubbed | Port interface defined. No-op adapter. Implementation deferred to v0.15 |

**T1 Commands — Canonical Vocabulary:**

Three command layers. See UX spec T1 Command Grammar Contract for full detail.

*Layer A — Launch (shell entry):*
`nova`, `nova mode <name>`, `nova status`, `nova help`, `nova memory`

*Layer B — In-Session (interactive prompt):*
`mode <name>`, `mode`, `modes`, `mode create`, `mode edit <name>`, `status`, `memory`, `what do you know`, `forget <topic>`, `shutdown`, `quit`, `exit`, `help`, `?`

*Layer C — Contextual Responses (valid only when prompted):*
`resume`, `yes`, `no`, `skip`, `cancel`, `confirm`

**Not in T1 command grammar:** `nova <name>` (bare mode shortcut), `audit`, `self-update`.

**T1 Desktop Actions — Safe Only:**

| Action | Allowed | Method |
|--------|---------|--------|
| Launch app | Yes | `subprocess` / `ShellExecute` |
| Focus window | Yes | `win32gui.SetForegroundWindow` |
| Arrange windows | Yes | `win32gui.MoveWindow` |
| Close app | No | Deferred |
| Modify files | No | Deferred |
| Keyboard/mouse simulation | No | Deferred |
| Menu clicks / UI tree interaction | No | Deferred |

**T1 Capability Tiers — All Three Ship:**

| Tier | Condition | Behavior |
|------|-----------|----------|
| **Full** | Claude API healthy | All systems active. Prose generation via Claude. |
| **Degraded** | 2+ consecutive API failures | Local operations continue. Voice bypassed. Raw seed/data verbatim. |
| **Offline** | Health checks consistently fail | Local-only. Memory reads, mode switching, workspace restore, transparency all functional. No cloud reasoning. |

Tier transitions: Full → Degraded (2+ failures) → Offline (health checks fail) → Full (health check succeeds). Recovery check every 60s + opportunistic on next cloud-requiring action.

**T1 Briefing Card States:**

| State | Condition | Title | Content |
|-------|-----------|-------|---------|
| **A — First Run** | No modes, no sessions, no seed | N.O.V.A. | First-run orientation → auto-launch setup wizard |
| **B — Post-Setup** | Modes >= 1, no seed, no completed session | Session Briefing | Available modes, start suggestion |
| **C — Warm Resume** | Seed exists OR completed session exists | Session Briefing | Seed (hero line), mode, duration, apps, resume suggestion |

Data flow: Brain (`BriefingAggregate`) → Nerve (state determination) → Ritual (`BriefingViewModel`) → Voice (optional prose enrichment) → Skin (Rich rendering). See Decision 3b for the full data structure contract.

**T1 Personality:**
- Bluntness levels: Calm and Direct only. Ruthless deferred to T2.
- Strategic praise: enabled but rare. One roast rule: not active in T1 (requires pattern detection maturity).
- Day-1 personality: professional, slightly warm, zero fluff. Progression driven by memory depth, not calendar days.

**T1 Explicit Non-Goals:**

These are explicitly out of scope for T1. They are not forgotten — they are deferred by design.

| Non-Goal | Reason | Target |
|----------|--------|--------|
| Voice STT/TTS (faster-whisper, Edge TTS, Piper) | Core loop must prove itself before voice layer | v0.2 |
| GUI (Textual TUI, Tauri native) | Terminal-first is the v0.1 product | v0.2 / v1.0 |
| Shield (focus protection, DND) | Separate concern, needs event bus maturity | v0.15 |
| Background daemon / system tray | Session-based only in v0.1 | v0.2+ |
| Semantic search / vector storage (sqlite-vec, LanceDB) | Memory compounding is T2+ | v0.2 |
| Deep per-app state (VS Code tabs, cursor, terminal history) | Workspace-level restore only in v0.1 | v0.2+ |
| Pattern detection / rich pattern intelligence | Minimal in T1 — real patterns belong to T2/T3 | T2/T3 |
| Ruthless bluntness level | Requires pattern detection to avoid false positives | T2 |
| `nova <name>` shorthand inference | Creates ambiguity — deferred until usage patterns known | T2 |
| `audit` command (user-facing) | Admin/power-user tool, not core loop | T2 |
| `self-update` command | Operational, fragile, not product-level | T2/T3 |
| Ad-hoc NLP mode creation from freeform description | `mode create` wizard is the T1 path | T2 |
| Multi-platform support | Windows 11 only for MVP | v1.0+ |
| Local LLM fallback | Claude API is the sole reasoning backend in v0.1 | v1.0 |
| MCP integration | New Hands adapters, not T1 scope | v1.0 |

Everything in T1 must ship as a working loop — startup → briefing → mode → work → shutdown → resume — not as isolated modules. The architecture supports T1 cleanly and does not block T2/T3 evolution.

## Project Foundation

### Primary Technology Domain

Desktop-native AI companion — single-process Python CLI application for Windows 11. No web framework, no mobile framework, no existing starter template applies. The project foundation is custom, built on Python standard library + targeted dependencies.

### Why No Starter Template

N.O.V.A. is a modular monolith Python CLI with asyncio, Rich terminal rendering, SQLite persistence, Windows API integration, and Claude API reasoning. No existing starter or boilerplate covers this combination. The foundation is defined explicitly below instead of inherited from a template.

### Technology Stack Decisions

**Language & Runtime:**
- Python 3.12+ with full type annotations
- asyncio event loop as the core runtime coordinator
- Single process for v0.1 — no multiprocessing assumptions unless profiling proves it needed later
- ProcessPoolExecutor reserved as a future option for CPU-heavy work (STT, TTS, embeddings — v0.2+), not part of the v0.1 foundation

**Package & Environment Management:**
- uv for project management, virtual environment, and dependency resolution
- pyproject.toml as the single project configuration file
- Lock file for reproducible installs

**Terminal Rendering:**
- Rich library — Panel, Table, Tree, Text, Progress, Prompt, Columns
- No Textual in v0.1 (deferred to v0.2 TUI upgrade)
- Console(color_system="auto") for cross-terminal compatibility

**Local Persistence:**
- sqlite3 (stdlib) — single-file database, no external server
- User-readable, inspectable with standard SQLite tools
- Schema versioning with auto-backup before migration
- Migrations are a first-class foundation concern with dedicated module structure

**OS Integration:**
- pywin32 (win32gui, win32process) for window detection and management
- psutil for process awareness
- subprocess / ShellExecute for app launching

**Cloud Reasoning:**
- Anthropic Python SDK for Claude API
- Prompt caching for cost control (<$2.50/month target)
- Isolated behind a port — swappable for local LLM adapter in v0.3+

**Code Quality:**
- ruff for linting and formatting (single tool, fast)
- mypy for static type checking (strict mode)
- pytest + pytest-asyncio for testing

### Project Structure

```
src/nova/
├── cli.py                  # Terminal entrypoint (argument parsing, session lifecycle)
├── app.py                  # Composition root — wires ports to adapters, boots the monolith
├── systems/                # The 8 system modules
│   ├── brain/              # Memory, learning, personalization, judgment
│   ├── eyes/               # Context awareness, window/app detection
│   ├── hands/              # Desktop actions (launch, focus, arrange)
│   ├── shield/             # Focus protection (v0.15+, stubbed in v0.1)
│   ├── voice/              # Personality, tone, response generation
│   ├── ritual/             # Briefing, shutdown, tomorrow seed
│   ├── skin/               # Terminal UI rendering (Rich components)
│   └── nerve/              # Orchestration, event routing, tier management
├── ports/                  # Abstract interfaces for each system
├── adapters/               # Concrete implementations
│   ├── claude/             # Claude API reasoning adapter
│   ├── win32/              # Windows API context + actions adapter
│   ├── sqlite/             # SQLite storage adapter
│   └── rich/               # Rich terminal rendering adapter
└── core/                   # Shared infrastructure
    ├── events.py           # Event bus / message types
    ├── config.py           # Configuration loading and validation
    ├── tiers.py            # Capability tier detection and state
    └── storage/
        ├── engine.py       # SQLite connection management
        └── migrations/     # Schema versioning and migration scripts
```

**Shipped defaults vs. runtime user data:**
- `config/` (in repo) — default mode templates, default exclusion list, default settings. Shipped with the application, not modified at runtime.
- Runtime user data lives in a proper Windows user data directory (e.g., `%LOCALAPPDATA%/nova/`) — SQLite database, user config overrides, audit trail. User-owned, predictable path, outside the repo. Local-first trust depends on this separation.

**Composition root (`app.py`):**
- Single place that wires all ports to their concrete adapters
- Reads config, initializes storage, creates system instances, connects the event bus
- Makes the ports-and-adapters architecture explicit — no system directly imports another system's adapter
- Swapping an adapter (e.g., Claude → local LLM) means changing one line in the composition root

**Note:** Project initialization (uv init, directory structure, pyproject.toml, first dependencies) should be the first implementation story.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
1. Module boundaries and ownership — which system owns what data and behavior
2. Event flow for the T1 continuity loop — the hero path through the monolith
3. Data schema — what SQLite stores vs. what lives in config files
4. Capability tier detection and enforcement — architecture-level behavior, not UI
5. Trust constraint enforcement — exclusion, minimization, deletion, audit

**Deferred Decisions (Post-T1):**
- Semantic memory architecture (sqlite-vec / LanceDB) — v0.2
- Voice adapter design (STT/TTS pipeline) — v0.2
- Shield policy engine (focus protection rules) — v0.15
- Earned autonomy model (careful-tier actions) — v0.2+
- Background presence / daemon model — v0.2+

### Decision 1: Module Boundaries and Ownership

**The 8 systems map to concrete Python modules with strict ownership rules:**

| System | Module | Owns | T1 Status |
|--------|--------|------|-----------|
| **Nerve** | `systems/nerve/` | Orchestration, global tier state, event routing, policy decisions (e.g., skip briefing, degrade command, suppress during offline). Does NOT generate user-facing prose. | Active — core coordinator |
| **Brain** | `systems/brain/` | Session persistence and retrieval, memory storage, transparency query model, deletion propagation. T1 scope: sessions, seeds, snapshots, transparency queries, deletion. Pattern detection stays minimal in T1 — real pattern intelligence belongs to T2/T3. | Active — SQLite memory + transparency |
| **Eyes** | `systems/eyes/` | Workspace/context capture, window/app detection, exclusion filtering at capture layer. Produces opaque events for excluded apps. | Active — snapshots + change polling |
| **Hands** | `systems/hands/` | Safe desktop actions only (launch, focus, arrange). Action registry, per-action audit logging. | Active — mode restore |
| **Ritual** | `systems/ritual/` | Session briefing assembly (inputs + structure), shutdown capture flow, tomorrow seed lifecycle. Owns the ceremony logic; Nerve decides when ceremonies run. | Active — continuity loop |
| **Voice** | `systems/voice/` | Wording, tone, personality generation for briefings, summaries, conversational responses, failure messages. Personality is a first-class system, not a rendering concern. | Active — text personality |
| **Skin** | `systems/skin/` | Rich terminal rendering, prompt parsing, terminal I/O. Renders what it receives; never makes decisions or generates prose. | Active — all UI output |
| **Shield** | `systems/shield/` | Focus protection, DND, distraction detection. Interface defined in T1; implementation deferred to v0.15. | Stubbed — interface only |

**Boundary rules:**
- Each system owns its domain data and exposes it only through its port interface
- No system directly imports another system's adapter — all wiring goes through `app.py` composition root
- Brain owns memory tables; Ritual owns the flow that decides *what* to store (seeds, session summaries)
- Eyes captures context; Nerve decides what to do with it
- Voice generates text content; Skin renders it. This separation is load-bearing: personality is a product requirement, and the split gives a clean path to v0.2 voice adapters without rewriting terminal rendering
- Skin never generates prose and Voice never renders Rich components
- Shield's port interface is defined in T1 so other systems (Nerve, Eyes) can reference it, but its adapter is a no-op stub until v0.15

**Voice vs. Skin output routing:**
- **Through Voice → Skin:** Briefings, summaries, explanations, failure messages with alternatives, earned praise, any personality-bearing response
- **Direct to Skin (bypasses Voice):** Progress lines (✓/✗), tier notices, confirmation prompts, status tables, transparency tree structures, operational output

This keeps progress output fast and deterministic while personality lives in word choice for actual responses — matching the UX spec where operational patterns are structured and personality is expressed through content, not formatting.

### Decision 2: Event Flow — T1 Continuity Loop

**Nerve is an orchestrator and policy layer, not a dumb router.** It makes orchestration decisions (skip briefing, degrade to local-only, suppress actions during offline) but never generates user-facing prose.

**T1 Continuity Loop — Concrete Event Sequence:**

```
SESSION START
  cli.py → app.py (composition root boots monolith)
  app.py → Nerve.startup()
  Nerve: check API health → set initial tier state
  Nerve → Eyes: capture_current_workspace()
  Nerve → Brain: load_briefing_aggregate()  [returns BriefingAggregate]
  Nerve: determine BriefingState (A/B/C) from aggregate
  Nerve: policy check — should briefing run? (yes unless session <1h ago, etc.)

  IF state == FIRST_RUN (State A):
    Nerve → Ritual: build_briefing(aggregate, state=A, tier)
    Ritual: constructs BriefingViewModel(title="N.O.V.A.", auto_start_setup=True, ...)
    Ritual → Skin: render_briefing_card(view_model)
    Skin: renders first-run card, auto-transitions to setup wizard

  IF state == POST_SETUP or WARM_RESUME (State B/C):
    IF tier == FULL:
      Nerve → Ritual: build_briefing(aggregate, state, tier=full)
      Ritual → Voice: generate_prose_enrichment(aggregate)
      Voice → Claude (via PromptBuilder): synthesize briefing prose
      Ritual: constructs BriefingViewModel with prose_enrichment
      Ritual → Skin: render_briefing_card(view_model)
    IF tier == DEGRADED or OFFLINE:
      Nerve → Ritual: build_briefing(aggregate, state, tier=degraded|offline)
      Ritual: constructs BriefingViewModel with prose_enrichment=None
      Ritual → Skin: render_briefing_card(view_model)
      (Voice bypassed — structured fields rendered verbatim, always sufficient)

  Skin: displays Briefing Card, waits for user input

MODE RESTORE
  Skin → Nerve: user_command(Command(verb="mode", target="coding"))
  Nerve → Brain: get_mode_config("coding")  [reads from file-based config via Config module]
  Nerve → Hands: restore_mode(mode_config)
  Hands: launches apps sequentially, emits per-app result
  Hands → Skin: render_progress(per_app_results)  [direct, no Voice — operational output]
  Nerve → Voice: generate_restore_summary(results, last_context)
  Voice → Skin: render_response("Workspace ready. Last thread: auth tests.")

WORKING STATE
  Eyes: polls context every 500ms–1s, emits only on change
  Eyes → Nerve: context_changed(new_context)  [excluded apps → opaque event]
  Nerve → Brain: store_context_if_relevant(context)  [selective, not continuous]
  Skin: prompt available for commands, routes to Nerve

SHUTDOWN
  Skin → Nerve: user_command("shutdown")
  Nerve → Ritual: begin_shutdown()
  Nerve → Eyes: capture_final_workspace()
  Ritual → Skin: render_shutdown_card(session_summary)  [direct, structured]
  Skin → Ritual: user_seed_input("Got auth working, need tests next")
  Ritual → Brain: store_session(seed, mode, context, duration, snapshot)
  Ritual → Voice: generate_shutdown_confirmation(seed)
  Voice → Skin: render_response("Planted for tomorrow.")
  Nerve: cleanup, close session

UNEXPECTED TERMINATION
  Nerve: signal handler captures SIGINT/close
  Brain: persist last known good state (best-effort)
  Next startup: Brain detects incomplete session, reports honestly
```

**Eyes polling strategy (T1):**
- Poll every 500ms–1s via win32gui
- Emit events only on window/title change (deduplicated)
- Persist selectively, not continuously — startup/shutdown snapshots are the primary records
- Live polling supports: mode inference, accurate shutdown state, future compounding paths
- If noisy in practice, fallback is snapshot-first with change buffering, not removal

### Decision 3: Data Schema

**Principle: SQLite holds runtime/session/memory/audit state. File-based config holds user-owned editable definitions.**

**File-based configuration (human-editable, in user data directory — `%LOCALAPPDATA%/nova/`):**
- `modes/` — one YAML file per mode (e.g., `coding.yaml`, `study.yaml`). Power users edit directly; wizard creates them.
- `exclusions.yaml` — sensitive-context exclusion list. Ships with defaults (password managers, banking apps, etc.), user-editable.
- `settings.yaml` — user preferences (API key reference, bluntness level, personality flags, etc.)

Mode files and exclusion config live in the **user data directory** (`%LOCALAPPDATA%/nova/`), not the repo working tree. This keeps the local-first storage story consistent: all user-owned data — whether SQLite runtime state or editable config — lives in one predictable, user-owned location outside the application source. The repo's `config/` directory holds only shipped defaults that are copied to the user data directory on first run.

**YAML Config Schemas (T1):**

**Mode schema — `%LOCALAPPDATA%/nova/modes/{mode_name}.yaml`:**

One file per mode. File name = mode name (kebab-case: `study-group.yaml`). The config module loads all YAML files in the `modes/` directory.

```yaml
# Example: coding.yaml
name: coding                    # Required. Display name (may contain spaces). Must match a unique mode.
apps:                           # Required. At least one app.
  - name: VS Code               # Required. Human-readable app name (for display and audit).
    executable: code             # Required. Executable name or path. Resolved via PATH or absolute path.
    args: []                     # Optional. Command-line arguments.
  - name: Chrome
    executable: chrome
    args: ["--new-window"]
  - name: Terminal
    executable: wt               # Windows Terminal
    args: []
folders: []                      # Optional. Project folders to associate with this mode.
                                 # Not auto-opened in T1 — used for context awareness (Eyes).
urls: []                         # Optional. URLs to open on mode restore (opened in default browser).
is_default: false                # Optional. Default: false. At most one mode should be default.
                                 # Default mode is suggested when no pattern-based suggestion is available.
```

**Validation rules:**
- `name`: required, non-empty string
- `apps`: required, at least one entry. Each entry must have `name` (string) and `executable` (string). `args` defaults to `[]`.
- `folders`: optional, list of strings (absolute paths). Empty list if omitted.
- `urls`: optional, list of strings (valid URLs). Empty list if omitted.
- `is_default`: optional boolean, defaults to `false`. If multiple modes set `is_default: true`, the first one loaded alphabetically wins (with a warning logged).
- File name must be valid as a file system name (no `/`, `\`, `:`, etc.). The config module normalizes the `name` field to a file-safe slug for storage.

**Exclusions schema — `%LOCALAPPDATA%/nova/exclusions.yaml`:**

Single file. Ships with sensible defaults. User-editable.

```yaml
# Sensitive apps whose context should never be captured or stored.
# When these apps are active, Eyes produces only opaque events:
# "A protected app was active" — no app name, no window title, no content.
excluded_apps:
  - name: 1Password               # Human-readable name (for settings display only)
    match: 1password               # Case-insensitive substring match against window process name
  - name: KeePassXC
    match: keepassxc
  - name: Banking
    match: bank                    # Broad match — catches most banking apps/sites
  - name: Bitwarden
    match: bitwarden

# Window titles containing these strings are excluded regardless of app.
# Useful for catching browser tabs with sensitive content.
excluded_title_patterns:
  - "password"
  - "banking"
  - "credit card"
  - "account settings"
```

**Validation rules:**
- `excluded_apps`: list of objects, each with `name` (string, display only) and `match` (string, case-insensitive substring match against process name).
- `excluded_title_patterns`: list of strings, case-insensitive substring match against window title.
- Both lists default to empty if omitted (no exclusions).
- Eyes checks every captured window against both lists. Match on either → opaque event.

**Settings schema — `%LOCALAPPDATA%/nova/settings.yaml`:**

Single file. Created during first-run setup. User-editable.

```yaml
# API configuration
api_key: "sk-ant-..."              # Required. Anthropic API key. Set during first-run setup.

# Personality
bluntness: direct                  # Optional. One of: calm, direct, ruthless. Default: direct.
                                   # T1 ships calm and direct only. Ruthless deferred to T2.

# Session behavior
skip_briefing_if_recent: true      # Optional. Default: true. Skip briefing if last session ended < 1 hour ago.
briefing_recency_threshold_minutes: 60  # Optional. Default: 60. Minutes threshold for skip_briefing_if_recent.

# Privacy
telemetry_opt_in: false            # Optional. Default: false. No telemetry unless explicitly opted in.
```

**Validation rules:**
- `api_key`: required string. If missing or empty, N.O.V.A. starts in offline-local-only tier with a one-time notice.
- `bluntness`: optional enum (`calm`, `direct`, `ruthless`). Invalid values fall back to `direct` with a warning logged.
- `skip_briefing_if_recent`: optional boolean, defaults to `true`.
- `briefing_recency_threshold_minutes`: optional integer, defaults to `60`.
- `telemetry_opt_in`: optional boolean, defaults to `false`.
- Unknown keys are ignored (forward compatibility for T2+ settings).

**Config loading contract (`core/config.py`):**
- All YAML files are loaded once at startup and exposed as immutable dataclasses via `NovaConfig`.
- No system reads YAML directly — all config access goes through the config module.
- Invalid YAML files produce a clear error message at startup, not a silent crash.
- Mode files that fail validation are skipped with a warning (other modes still load).

**SQLite schema (T1 — `%LOCALAPPDATA%/nova/nova.db`):**

```sql
-- Schema versioning (first-class migration support)
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,  -- ISO 8601
    description TEXT
);

-- Session tracking
CREATE TABLE sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    mode_name           TEXT,
    seed_text           TEXT,            -- tomorrow seed (user's note to future self)
    summary             TEXT,            -- session summary for briefing generation
    is_complete         INTEGER DEFAULT 0  -- 0=interrupted, 1=clean shutdown
);

-- Workspace snapshots (tied to sessions or mode switches)
CREATE TABLE workspace_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL REFERENCES sessions(id),
    captured_at         TEXT NOT NULL,
    snapshot_type       TEXT NOT NULL,    -- 'startup', 'shutdown', 'mode_switch', 'periodic'
    workspace_data      TEXT NOT NULL     -- JSON: {apps: [...], focused_app, mode_name}
);

-- Memory items (Brain's inspectable knowledge store)
CREATE TABLE memory_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER REFERENCES sessions(id),
    category            TEXT NOT NULL,    -- enum: 'seed', 'session_note', 'context_summary', 'pattern'
    content             TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    relevance_score     REAL DEFAULT 1.0  -- for future decay/reinforcement (T2+)
);

-- Audit trail (append-only, queryable)
CREATE TABLE audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    action_type         TEXT NOT NULL,    -- 'app_launch', 'mode_switch', 'mode_restore',
                                         -- 'deletion', 'seed_capture', 'tier_change'
    target              TEXT,            -- what was acted on (opaque ref if excluded context)
    result              TEXT NOT NULL,    -- 'success', 'failed', 'skipped'
    details             TEXT             -- JSON: additional context (never raw excluded content)
);
```

**Design rules:**
- `memory_items.category` uses a small enum (`seed`, `session_note`, `context_summary`, `pattern`) — enough for T1/T2 without pretending we need a full semantic memory system
- Workspace snapshots are separate from sessions to support multiple captures per session (startup, shutdown, mode switches)
- `is_complete` on sessions enables honest reporting when a session was interrupted
- Audit trail entries for excluded contexts use opaque references only — e.g., `target: "protected_app"`, never the actual app name
- Schema version table + migration scripts in `core/storage/migrations/` — backup-before-migrate is enforced by the migration runner

### Decision 3b: Briefing Data Structure Contract (T1)

**Problem:** The SQLite schema defines what's stored. The UX spec's Briefing Card State Contract defines what's rendered. Nothing defines the data contract in between — the domain types that flow from Brain → Nerve → Ritual → Voice → Skin. Without this, implementers of each system will make incompatible assumptions about field names, nullability, and state boundaries.

**Principle: Three typed aggregates connect storage to rendering. Brain assembles a read aggregate. Ritual produces a render-ready view model. Skin receives a complete view model and makes zero decisions about content.**

#### Brain Output: BriefingAggregate

Brain assembles this from multiple tables (sessions, workspace_snapshots, memory_items) plus file-based mode config. This is a read-side aggregate, not a raw row.

```python
@dataclass(frozen=True)
class BriefingAggregate:
    """What Brain loads and assembles for Nerve/Ritual.
    Aggregates across sessions, snapshots, and memory — not a single row passthrough.
    Lives in: systems/brain/models.py"""

    last_session: SessionSummary | None       # None if no prior sessions exist
    last_snapshot: WorkspaceSnapshot | None    # most recent snapshot from last session
    last_seed: str | None                     # seed_text from last completed session
    available_modes: list[ModeInfo]           # from file-based config, enriched
    recent_memory: list[MemoryItem]           # relevant memory_items for briefing context (T2+: pattern-based)


@dataclass(frozen=True)
class SessionSummary:
    """Summarized view of a session for briefing purposes."""
    session_id: int
    started_at: datetime
    ended_at: datetime | None
    duration: timedelta | None                # computed: ended_at - started_at (None if interrupted)
    mode_name: str | None
    summary: str | None                       # session summary text
    is_complete: bool                         # True = clean shutdown, False = interrupted


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Parsed workspace state from a snapshot row."""
    captured_at: datetime
    snapshot_type: str                        # 'startup', 'shutdown', 'mode_switch', 'periodic'
    apps: list[str]                           # app names extracted from workspace_data JSON
    focused_app: str | None
    mode_name: str | None


@dataclass(frozen=True)
class ModeInfo:
    """Enriched mode reference — more than a string, less than the full config."""
    name: str
    app_count: int                            # number of configured apps in this mode
    is_default: bool                          # True if this is the user's default/fallback mode
    last_used_at: datetime | None             # from sessions table (most recent session with this mode_name)
```

Brain's `load_briefing_aggregate()` method:
1. Queries the most recent session row → `SessionSummary`
2. Queries the most recent `workspace_snapshot` for that session → `WorkspaceSnapshot`
3. Queries the most recent `memory_items` with `category = 'seed'` from the last completed session → `last_seed`
4. Reads mode YAML files from `%LOCALAPPDATA%/nova/modes/` → `list[ModeInfo]` (enriched with `last_used_at` from sessions table)
5. Queries relevant `memory_items` for briefing context → `recent_memory` (T1: minimal; T2+: pattern-based selection)

#### State Determination: Nerve Decides

Nerve receives the `BriefingAggregate` and determines the briefing state. The boundary concept is **resumable context** — is there enough to resume meaningfully?

```python
class BriefingState(Enum):
    FIRST_RUN = "first_run"       # State A: nothing to work with
    POST_SETUP = "post_setup"     # State B: modes exist but no resumable context
    WARM_RESUME = "warm_resume"   # State C: resumable context exists
```

**State evaluation logic (first match wins):**

```
IF available_modes is empty AND last_session is None → FIRST_RUN (State A)
ELIF last_seed is None AND (last_session is None OR last_session.is_complete == False) → POST_SETUP (State B)
ELSE → WARM_RESUME (State C)
```

The key boundary: State C activates when **resumable context exists** — a seed, a completed session with summary, or enough accumulated state that "resume" is a meaningful verb. This is broader than "seed exists" because a completed session with mode + duration + apps is resumable even without a seed.

Refined State C condition:
```
State C when ANY of:
  - last_seed is not None
  - last_session is not None AND last_session.is_complete == True
  - last_session is not None AND last_session.summary is not None
```

#### Ritual Output: BriefingViewModel

Ritual assembles the `BriefingAggregate` + state + tier into a render-ready view model. This is what Skin receives — a complete UI contract. Skin makes zero content decisions; it only maps fields to Rich components.

```python
@dataclass(frozen=True)
class BriefingViewModel:
    """Complete render-ready view model for the Briefing Card.
    Skin receives this and maps fields to Rich components — no logic, no decisions.
    Lives in: systems/ritual/models.py"""

    # --- Render control ---
    state: BriefingState                      # determines which render path Skin uses
    tier: CapabilityTier                      # full, degraded, offline

    # --- UI chrome ---
    title: str                                # "N.O.V.A." for State A, "Session Briefing" for B/C
    prompt_text: str | None                   # bold last line: "Start in coding mode?" / "Resume coding mode?" / None for State A
    auto_start_setup: bool                    # True only for State A — Skin triggers setup wizard after render

    # --- State B + C fields (None/empty when State A) ---
    available_modes: list[ModeInfo]           # enriched mode list
    suggested_mode: str | None                # mode name to suggest (most recent, default, or pattern-based)

    # --- State C fields (None when State A or B) ---
    seed_text: str | None                     # the hero line — user's carry-forward note
    last_mode: str | None                     # mode name from prior session
    last_duration: timedelta | None           # session duration (None if interrupted)
    last_apps: list[str]                      # app names from workspace snapshot (empty list = omit line)

    # --- Prose enrichment (additive, not structural) ---
    prose_enrichment: str | None              # Claude-generated contextual flavor (full tier only)
                                              # Skin always renders from structured fields above.
                                              # Prose is displayed as an additional line/paragraph
                                              # BELOW the structured content — it never replaces
                                              # seed_text, last_mode, last_apps, or prompt_text.
```

**Critical design rule: Prose is additive, not structural.** `prose_enrichment` is an optional Claude-generated line that adds warmth or connects dots across sessions. It is rendered *after* the structured fields, never *instead of* them. In degraded/offline tiers, `prose_enrichment` is None and the card renders from structured fields alone — which must always be sufficient for a complete, useful Briefing Card.

#### Field Population by State

| Field | State A | State B | State C |
|-------|---------|---------|---------|
| `state` | `FIRST_RUN` | `POST_SETUP` | `WARM_RESUME` |
| `tier` | current tier | current tier | current tier |
| `title` | `"N.O.V.A."` | `"Session Briefing"` | `"Session Briefing"` |
| `prompt_text` | `None` | `"Start in {suggested_mode} mode?"` | `"Resume {suggested_mode} mode?"` |
| `auto_start_setup` | `True` | `False` | `False` |
| `available_modes` | `[]` | populated (>= 1) | populated |
| `suggested_mode` | `None` | most recent or default | pattern-based or last used |
| `seed_text` | `None` | `None` | from `BriefingAggregate.last_seed` (may be None) |
| `last_mode` | `None` | `None` | from `last_session.mode_name` |
| `last_duration` | `None` | `None` | from `last_session.duration` |
| `last_apps` | `[]` | `[]` | from `last_snapshot.apps` |
| `prose_enrichment` | `None` | `None` | Claude-generated (full tier only) |

#### State C Fallback Rules (self-trimming card)

When a State C field has no data, the Briefing Card omits that line entirely — no empty placeholders, no "N/A". This is the Progressive Briefing contract.

| Field | Fallback | Card Behavior |
|-------|----------|---------------|
| `seed_text` | `None` | Omit seed line (session context alone drives the card) |
| `last_mode` | `None` | Omit "Last session: X mode" line |
| `last_duration` | `None` | Omit duration from "Last session" line |
| `last_apps` | `[]` | Omit "Apps:" line |
| `prose_enrichment` | `None` | No prose paragraph rendered (degraded/offline, or T1 before Claude synthesis is wired) |
| `suggested_mode` | `None` | `prompt_text` falls back to generic: `"What mode?"` |

#### Render Responsibility Boundary

| Concern | Owner | NOT owned by |
|---------|-------|-------------|
| Which tables to query, how to join | Brain | Ritual, Nerve |
| Which state applies (A/B/C) | Nerve | Brain, Ritual |
| Assembling the view model, populating UI fields (`title`, `prompt_text`, `auto_start_setup`) | Ritual | Nerve, Skin |
| Generating prose enrichment | Voice (via Claude) | Ritual, Skin |
| Mapping view model fields to Rich components | Skin | Ritual, Voice |

#### Testing Contract

Each boundary is independently testable:
- **Brain:** Given these DB rows → `BriefingAggregate` has these fields
- **Nerve:** Given this `BriefingAggregate` → state is A/B/C
- **Ritual:** Given this `BriefingAggregate` + state + tier → `BriefingViewModel` has these exact fields
- **Skin:** Given this `BriefingViewModel` → Rich output contains these elements (no DB, no Claude, no state logic)

### Decision 4: Capability Tier Detection and Enforcement

**All three tiers ship in T1.** The PRD, UX spec, and trust-under-failure journey depend on full / degraded / offline-local-only as real product behavior. Collapsing to two states would conflict with the product design.

**Global tier state lives in Nerve.** Each subsystem exposes a capability map that branches on the current tier. Nerve broadcasts tier-change events; Skin surfaces changes once.

**Tier state machine:**

```
                    ┌─────────────┐
         startup ──►│    FULL      │
                    └──────┬──────┘
                           │ 2+ consecutive failures
                           │ or rate-limit/outage signal
                           ▼
                    ┌─────────────┐
                    │  DEGRADED   │◄──── recovery check succeeds
                    └──────┬──────┘      (partial restore)
                           │ health checks consistently fail
                           │ or connectivity clearly absent
                           ▼
                    ┌─────────────┐
                    │   OFFLINE   │
                    └──────┬──────┘
                           │ health check succeeds
                           ▼
                    ┌─────────────┐
                    │    FULL      │
                    └─────────────┘
```

**Tolerant degrade model (not immediate global flip):**
- Single cloud-required request fails → that specific command falls back immediately (local response or honest "unavailable" message)
- Global tier degrades after 2+ consecutive failures in a short window, or a clear upstream signal (HTTP 429 rate-limit, 503 outage)
- Tier drops to offline-local-only when health checks consistently fail or connectivity is clearly absent
- Recovery: periodic health check every 60s + opportunistic check on next cloud-requiring action
- Tier-change event emitted once per transition; Skin renders once, then silent

**Per-system tier behavior:**

| System | Full | Degraded | Offline |
|--------|------|----------|---------|
| **Brain** | Read + write + synthesize via Claude | Read + write, no new synthesis | Read only (local queries) |
| **Eyes** | Full capture | Full capture | Full capture |
| **Hands** | All safe actions | All safe actions | All safe actions |
| **Ritual** | Generated briefing via Voice+Claude | Raw seed/notes verbatim, no synthesis | Raw seed/notes verbatim |
| **Voice** | Full personality generation via Claude | Limited/cached responses | No generated responses |
| **Skin** | Full rendering | Full rendering + degraded notice | Full rendering + offline notice |
| **Nerve** | Full orchestration | Suppress cloud-dependent flows | Local-only orchestration |

### Decision 5: Trust Constraint Enforcement

**Four trust mechanisms, each with a concrete enforcement point:**

**A. Exclusion Boundaries (enforced at Eyes capture layer):**
- Eyes checks every captured window against the exclusion list *before* creating an event
- Excluded windows produce `OpaqueContextEvent(type="protected_app_active")` — no app name, no title, no window content
- Opaque events propagate through Brain (stored as opaque), Ritual (omitted from briefing content), Skin (rendered as "A protected app was active"), Cloud prompts (stripped entirely), and Audit trail (opaque reference only)
- Exclusion list is file-based config (`exclusions.yaml`), user-editable, ships with sensible defaults

**B. Cloud Prompt Minimization (PromptBuilder as separate trust-boundary component):**
- `core/prompt_builder.py` — a dedicated component that sits between Brain and the Claude adapter
- Brain retrieves local context and memory; PromptBuilder transforms it into a minimized, cloud-safe prompt
- PromptBuilder responsibilities:
  - Accept local context + memory items from Brain
  - Produce minimized summary (never raw memory store contents)
  - Strip any excluded/opaque references entirely
  - Enforce a token budget
  - Return the cloud-safe prompt to the Claude adapter
- The Claude adapter *only* receives output from PromptBuilder — never raw Brain data
- **T1 ships basic prompt minimization** (summary extraction, exclusion stripping). Sophistication (better summarization, token budgeting, richer context shaping) deferred to T2.
- PromptBuilder is NOT part of Brain — the boundary between local memory and cloud-eligible context is too important to bury inside one subsystem

**C. Deletion Propagation (owned by Brain, exact-match-by-default):**
- When user says "forget X":
  1. Brain performs exact/explicit match against stored data
  2. Brain presents a preview of matched items to user via Skin
  3. User confirms deletion scope (can optionally widen to fuzzy match)
  4. Brain removes target from: memory_items, session summaries, seeds, workspace snapshots
  5. Brain logs deletion event in audit_log (action + count, never deleted content)
  6. Deletion completes fully *before* transparency query can be re-invoked (NFR11)
- Exact-by-default is the right trust posture: over-deleting is worse than under-deleting in a trust-first local tool. User can always run another delete after inspecting results.

**D. Audit Trail (cross-cutting, enforced at port boundaries):**
- Every Hands action logged automatically via decorator/middleware on the Hands port
- Every deletion event logged (action + metadata, not deleted content)
- Every tier-change logged
- Audit entries for excluded contexts use opaque references only
- Queryable via: transparency command (structured view), `nova audit` command (table view), and directly via SQLite tools
- Append-only table — no updates, no deletes of audit entries

### T1 Minimum Architecture Summary

**The smallest architecture that ships the continuity loop without blocking T2/T3:**

```
┌──────────────────────────────────────────────────────────┐
│                      cli.py (entrypoint)                  │
│                      app.py (composition root)            │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────┐    events     ┌──────────┐                 │
│  │  Skin   │◄─────────────►│  Nerve   │                 │
│  │ (Rich)  │               │(orchestr)│                 │
│  └─────────┘               └────┬─────┘                 │
│       ▲                         │ routes to:             │
│       │ render                  │                        │
│       │                    ┌────┴─────┐                  │
│  ┌────┴───┐          ┌────┴──┐ ┌─────┴─┐  ┌────────┐   │
│  │ Voice  │          │ Brain │ │ Eyes  │  │ Hands  │   │
│  │(person)│          │(mem)  │ │(ctx)  │  │(acts)  │   │
│  └────────┘          └───┬───┘ └───────┘  └────────┘   │
│       ▲                  │                               │
│       │ text        ┌────┴─────┐                        │
│  ┌────┴────┐        │  Ritual  │                        │
│  │ Prompt  │        │(ceremony)│                        │
│  │ Builder │        └──────────┘                        │
│  └────┬────┘                                            │
│       │ minimized prompt                                 │
│       ▼                                                  │
│  ┌──────────┐                                           │
│  │  Claude  │  (cloud, behind port)                     │
│  │  Adapter │                                           │
│  └──────────┘                                           │
│                                                          │
│  ┌───────────────────────────────────────────┐          │
│  │           SQLite  (%LOCALAPPDATA%/nova/)   │          │
│  │  sessions | snapshots | memory_items      │          │
│  │  audit_log | schema_version               │          │
│  └───────────────────────────────────────────┘          │
│                                                          │
│  ┌───────────────────────────────────────────┐          │
│  │     File Config (%LOCALAPPDATA%/nova/)     │          │
│  │  modes/*.yaml | exclusions.yaml           │          │
│  │  settings.yaml                            │          │
│  └───────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────┘
```

**T1 ships:** Nerve, Brain, Eyes, Hands, Ritual, Voice, Skin (all active). Shield (stubbed). PromptBuilder (basic). Claude adapter (behind port). SQLite adapter. Win32 adapter. Rich adapter.

**T1 does not ship:** Shield implementation, semantic search, voice adapters, TUI, earned autonomy, background presence, rich pattern detection.

**Evolution without rewrite:**
- T2/T3: same modules, more content and polish
- v0.15: Shield adapter activates behind existing port, connects to existing event bus
- v0.2: Voice STT/TTS as new adapters behind Voice port. Skin swaps Rich for Textual. sqlite-vec/LanceDB as new Brain storage adapter. All existing ports unchanged.
- v1.0: Tauri GUI as new Skin adapter. Local LLM as alternative Claude adapter. MCP as new Hands adapters. Composition root swaps wiring; no system internals change.

### Decision Impact Analysis

**Implementation Sequence (T1):**
1. Project scaffolding — uv init, directory structure, pyproject.toml, core infrastructure
2. Core event bus + tier state machine (Nerve foundation)
3. SQLite storage engine + migration runner (core/storage)
4. File-based config loader (modes, exclusions, settings)
5. Eyes adapter (win32gui polling, exclusion filtering)
6. Brain + memory_items + sessions tables
7. Hands adapter (app launch, focus, arrange)
8. Ritual (shutdown flow + seed capture + briefing assembly)
9. Voice (basic personality text generation via Claude)
10. PromptBuilder (basic minimization)
11. Skin (Rich components: Briefing Card, Progress Indicator, Shutdown Card, Command Response)
12. Transparency command (Knowledge Display via Brain query)
13. Composition root (app.py wires everything)
14. CLI entrypoint + guided first-run setup

**Cross-Component Dependencies:**
- Nerve depends on: all system ports (routes events between them)
- Brain depends on: SQLite adapter, file config (exclusion list for filtering queries)
- Ritual depends on: Brain (read/write sessions), Voice (generate text), Skin (render cards)
- Voice depends on: Claude adapter (via PromptBuilder) for generation, degrades to cached/no responses offline
- PromptBuilder depends on: Brain (source data), exclusion config (stripping rules)
- Eyes depends on: Win32 adapter, exclusion config
- Hands depends on: Win32 adapter, audit_log (writes)
- Skin depends on: Rich adapter (pure rendering, no business logic)

## Implementation Patterns & Consistency Rules

### Why These Patterns Exist

N.O.V.A. is built by AI agents executing implementation stories. Different agents could make different structural choices that are individually reasonable but collectively inconsistent. These patterns prevent that. Every rule below answers: "If two agents implement different systems independently, will their code compose correctly?"

### Port & Adapter Convention

**Every system defines exactly one port (abstract interface) and at least one adapter (concrete implementation).**

Port location: `src/nova/ports/{system_name}.py`
Adapter location: `src/nova/adapters/{adapter_type}/{system_name}.py` or `src/nova/systems/{system_name}/adapter.py`

**Port rules:**
- Ports are Python `Protocol` classes (structural subtyping, no inheritance required)
- Port methods are all `async` — even if the current adapter is synchronous, the port is async to allow future async adapters
- Clearly documented sync wrappers are allowed at adapter edges when the underlying implementation is inherently synchronous (e.g., SQLite operations via aiosqlite, file reads) — but the port interface itself remains async
- Port methods use domain types, never adapter-specific types (no `sqlite3.Row`, no `rich.Panel`, no `anthropic.Message` in port signatures)
- Ports define the full interface the system exposes — nothing is available outside the port
- Ports never import from adapters or from other systems' internals

**Port naming example:**
```python
# src/nova/ports/brain.py
class BrainPort(Protocol):
    async def load_last_session(self) -> Session | None: ...
    async def store_session(self, session: SessionData) -> None: ...
    async def query_memory(self, query: str) -> list[MemoryItem]: ...
    async def delete_matching(self, target: str) -> DeletionPreview: ...
    async def confirm_deletion(self, preview: DeletionPreview) -> DeletionResult: ...
    async def get_transparency_model(self) -> TransparencyModel: ...
```

**Adapter naming example:**
```python
# src/nova/adapters/sqlite/brain.py
class SqliteBrainAdapter:
    """Implements BrainPort using SQLite storage."""
    # all BrainPort methods implemented here
```

**Anti-patterns:**
- A system importing another system's adapter directly
- A port method returning an adapter-specific type (`sqlite3.Row`)
- Business logic inside an adapter (adapters translate, they don't decide)

### Event Bus Convention

**All inter-system communication goes through the event bus. No system calls another system's methods directly — Nerve routes everything.**

Event location: `src/nova/core/events.py`

**Event types are typed dataclasses centralized in `events.py` from day one.** Dot-notation strings are the canonical names, but they are defined as typed constants on the event classes, not scattered as raw strings across system code. This prevents drift across agents.

**Event structure:**
```python
@dataclass(frozen=True)
class Event:
    type: str          # dot-notation: "eyes.context_changed", "hands.app_launched"
    timestamp: str     # ISO 8601, always UTC
    payload: dict      # JSON-serializable, typed per event type
    source: str        # system name: "eyes", "nerve", "ritual"

# Specific event types as typed subclasses:
@dataclass(frozen=True)
class ContextChanged(Event):
    type: str = field(default="eyes.context_changed", init=False)
    source: str = field(default="eyes", init=False)
    # payload: {app_name, window_title, process_name}
    #   or {type: "protected_app_active"} if excluded

@dataclass(frozen=True)
class TierChanged(Event):
    type: str = field(default="nerve.tier_changed", init=False)
    source: str = field(default="nerve", init=False)
    # payload: {previous_tier, new_tier, reason}
```

**Event naming convention:** `{source_system}.{past_tense_verb}` — e.g., `eyes.context_changed`, `hands.app_launched`, `ritual.shutdown_completed`, `nerve.tier_changed`

**Event bus rules:**
- Events are immutable dataclasses (frozen=True)
- Event payloads are always JSON-serializable dicts
- Events are fire-and-forget from the sender's perspective — Nerve decides routing
- No system subscribes to another system's events directly — Nerve manages all subscriptions
- Event handlers are async
- Events for excluded contexts use opaque payloads (no protected details)
- All event type classes live in `core/events.py` — systems import event classes, never construct raw Event objects with string types

**T1 event bus semantics (explicit contract):**
- **In-process only.** Single-process, in-memory, async delivery. No durable queue, no replay, no cross-process delivery, no persistence of events.
- **Ordered delivery.** Events are delivered in emission order within the current process. Handlers execute sequentially per event (not concurrent fan-out in T1).
- **Best-effort delivery, fail-fast handlers.** If a handler raises, the event bus logs the error and continues to the next handler. A handler failure does not block other handlers or crash the session.
- **No event persistence.** Events exist only in-flight. They are not stored, not replayed on restart, not reconstructable from audit trail. The audit trail records *actions*, not events.

**Write-then-emit rule:**
- Events that describe durable facts (`session_ended`, `memory_forgotten`, `seed_saved`, `mode_created`) are emitted **only after** Brain confirms the write succeeded.
- Never emit before persistence is confirmed. This prevents downstream systems from acting on state that doesn't exist yet.
- Corollary: if Brain fails to persist, the event is never emitted and downstream systems never see it. The failure is handled at the command level, not the event level.

**Ownership rule:**
- Each domain fact has exactly one owning system. The owner is the only writer. Other systems read through the owner's port.
  - **Brain:** persisted memory, sessions, seeds, snapshots, memory items
  - **Nerve:** runtime orchestration state, current tier, active mode
  - **Skin:** terminal render state, prompt state
  - **Config module:** file-based config (modes, exclusions, settings) — read-only after startup
- No duplicated writable copies across systems. If Nerve needs session data, it reads from Brain's port — it does not maintain a shadow copy.

**Anti-patterns:**
- System A directly calling System B's port method (bypasses Nerve routing and tier enforcement)
- Mutable event objects
- Events carrying adapter-specific types
- Raw string event types scattered in system code instead of using centralized typed classes
- Emitting a durable-fact event before Brain confirms the write
- Maintaining a writable copy of another system's owned data
- Silent retry loops in event handlers

### Composition Root Convention

**`app.py` is the single place where ports are wired to adapters. No system knows which adapter it's talking to.**

```python
# src/nova/app.py — composition root
async def create_app(config: NovaConfig) -> NovaApp:
    # 1. Initialize infrastructure
    storage = SqliteStorageEngine(config.db_path)
    await storage.run_migrations()
    event_bus = EventBus()

    # 2. Create adapters
    brain_adapter = SqliteBrainAdapter(storage)
    eyes_adapter = Win32EyesAdapter(config.exclusions)
    hands_adapter = Win32HandsAdapter(audit_log=storage.audit)
    claude_adapter = ClaudeReasoningAdapter(config.api_key)
    prompt_builder = PromptBuilder(config.exclusions)
    skin_adapter = RichSkinAdapter()

    # 3. Create systems (receive ports, not adapters — but adapters implement ports)
    voice = VoiceSystem(reasoning=claude_adapter, prompt_builder=prompt_builder)
    ritual = RitualSystem(brain=brain_adapter, voice=voice, skin=skin_adapter)
    nerve = NerveSystem(
        brain=brain_adapter, eyes=eyes_adapter, hands=hands_adapter,
        ritual=ritual, voice=voice, skin=skin_adapter,
        event_bus=event_bus, tier_manager=TierManager(claude_adapter)
    )

    return NovaApp(nerve=nerve, skin=skin_adapter, event_bus=event_bus)
```

**Composition root rules:**
- All adapter instantiation happens here and nowhere else
- Systems receive their dependencies through constructor injection
- No system imports from `adapters/` — only from `ports/`
- Swapping an adapter means changing one line here
- The composition root is the only file that imports concrete adapter classes

**Anti-patterns:**
- A system module containing `from nova.adapters.sqlite.brain import SqliteBrainAdapter`
- Adapter instantiation inside a system's `__init__`
- Global singletons for adapters
- Conditional adapter selection inside system code (that belongs in app.py)

### Command Routing Convention

**Skin parses user input into a command object. Nerve routes the command. Systems execute.**

The T1 command grammar has three layers (launch / in-session / contextual). See the **T1 Command Grammar Contract** in the UX design specification for the complete canonical vocabulary, natural-language mappings, partial command behavior, invalid input response shapes, and empty input handling.

```python
# Command structure
@dataclass(frozen=True)
class Command:
    verb: str           # "mode", "shutdown", "status", "forget", "memory", "help"
    target: str | None  # "coding", "Meridian", None
    raw_input: str      # original user text for NLP fallback
    is_contextual: bool = False  # True for resume/yes/no/skip/cancel — valid only when prompted
```

**Routing: Skin → Nerve → appropriate system**

**Rules:**
- Skin handles deterministic command parsing (structured `[verb] [target]` commands and simple keyword matching). Parsing is deterministic — same input always produces same Command object.
- Contextual replies (`resume`, `yes`, `no`, `skip`, `cancel`) are tagged `is_contextual=True`. Nerve only acts on them if the current UI state expects a response. Outside that context, they are treated as unknown input.
- Natural-language intent resolution that requires reasoning (ambiguous input, conversational queries) must go through Nerve → Voice/Claude and respect tier state. Skin never attempts NLP-level interpretation.
- Skin never calls system logic directly — always routes through Nerve
- Nerve decides which system handles the command, checking tier state first
- If a command requires cloud reasoning and tier is offline, Nerve returns an honest unavailability response (routed through Voice if personality-bearing, or direct to Skin if operational)
- Unknown commands get helpful suggestions (max 3, context-relevant), not generic errors. See UX spec for exact response shape.
- Partial commands (e.g., `mode edit` without target, `forget` without topic) get specific guidance, not the generic unknown-command response.
- `shutdown`, `quit`, and `exit` all route through the same graceful shutdown flow. No alias may bypass seed capture and session end.
- This boundary prevents the parser from quietly becoming a second orchestration layer
- **Not in T1:** `nova <name>` (bare mode shortcut) is not parsed. Creates ambiguity. Deferred to T2.

### Config Loading Convention

**All configuration goes through `core/config.py`. No system reads files directly.**

```python
# src/nova/core/config.py
class NovaConfig:
    """Single source of truth for all configuration."""
    db_path: Path               # %LOCALAPPDATA%/nova/nova.db
    data_dir: Path              # %LOCALAPPDATA%/nova/
    modes: dict[str, ModeConfig]       # loaded from modes/*.yaml
    exclusions: ExclusionConfig        # loaded from exclusions.yaml
    settings: UserSettings             # loaded from settings.yaml
    api_key: str                       # from settings.yaml
```

**Rules:**
- Config is loaded once at startup by `app.py`, passed to systems via constructor
- No system reads YAML/JSON files directly — config module handles all file I/O
- Shipped defaults (in repo `config/`) are copied to user data dir on first run only
- Config changes during runtime (e.g., mode edit) go through the config module, which writes to the user data dir
- Config objects are immutable after loading (dataclasses or frozen models)

### Migration Convention

**Every schema change is a numbered migration script. The migration runner enforces order and backup.**

Location: `src/nova/core/storage/migrations/`
Naming: `001_initial_schema.py`, `002_add_workspace_snapshots.py`, etc.

```python
# Each migration file:
VERSION = 2
DESCRIPTION = "Add workspace_snapshots table"

async def up(db: aiosqlite.Connection) -> None:
    await db.execute("""CREATE TABLE workspace_snapshots (...)""")

async def down(db: aiosqlite.Connection) -> None:
    await db.execute("""DROP TABLE workspace_snapshots""")
```

**Rules:**
- Migrations are sequential, never skipped
- Migration runner creates a timestamped backup of nova.db before applying any migration
- `schema_version` table tracks which migrations have been applied
- Migrations are idempotent where possible
- No raw SQL outside of migration files for schema changes — systems use the storage engine API
- Down migrations are defined but not automatically run — they exist for manual recovery

### Audit Logging Convention

**Every automated action is logged through a single audit interface. No system writes to audit_log directly.**

```python
# src/nova/core/audit.py
class AuditLogger:
    async def log_action(self, action_type: str, target: str | None,
                         result: str, details: dict | None = None) -> None: ...
```

**Rules:**
- AuditLogger is injected into systems that perform auditable actions (Hands, Brain for deletions, Nerve for tier changes)
- `action_type` uses a fixed enum: `app_launch`, `app_focus`, `window_arrange`, `mode_switch`, `mode_restore`, `deletion`, `seed_capture`, `tier_change`
- `target` is opaque for excluded contexts — e.g., `"protected_app"`, never the actual app name
- `details` is JSON-serializable, never contains raw excluded content
- Audit log is append-only — no updates, no deletes
- Queryable via Brain's transparency model and directly via SQLite

### Naming Conventions

**Python code:**
- `snake_case` for functions, methods, variables, module names
- `PascalCase` for classes (including port protocols, adapters, event types, dataclasses)
- `UPPER_SNAKE_CASE` for constants
- Type hints on all public function signatures

**SQLite:**
- `snake_case` for table and column names
- Plural table names: `sessions`, `memory_items`, `workspace_snapshots`, `audit_log`
- Foreign keys: `{referenced_table_singular}_id` — e.g., `session_id`
- Timestamps: `TEXT` type, ISO 8601 format, always UTC

**File-based config:**
- YAML for all config files (modes, exclusions, settings)
- `snake_case` keys in YAML
- One mode per file: `modes/coding.yaml`, `modes/study.yaml`

**Events:**
- `{source_system}.{past_tense_verb}` — e.g., `eyes.context_changed`
- Always defined as typed event classes in `core/events.py`

**Commands:**
- `nova {verb} {target}` — e.g., `nova mode coding`, `nova forget Meridian`

### Error Handling Patterns

**System-level errors:**
- Systems raise domain-specific exceptions (e.g., `ModeNotFoundError`, `StorageError`, `ApiUnavailableError`)
- Nerve catches system errors and decides the response: retry, degrade, or report to user
- Errors that reach the user always go through Voice (personality-bearing) or direct to Skin (operational notice)
- Technical stack traces go to a log file, never to the terminal

**Adapter-level errors:**
- Adapters catch adapter-specific exceptions and translate to domain exceptions
- e.g., `sqlite3.OperationalError` → `StorageError`, `anthropic.APIStatusError` → `ApiUnavailableError`
- No adapter-specific exceptions leak past the adapter boundary

**Tier-related errors:**
- Cloud API failure on a specific command → that command falls back immediately
- Repeated failures → Nerve transitions global tier state
- User always gets an honest explanation of what's unavailable and what still works

### Logging Convention

**Structured logging to file only. Never to terminal (terminal is Skin's domain).**

```python
import logging
logger = logging.getLogger("nova.systems.brain")
logger.info("Session stored", extra={"session_id": 42, "mode": "coding"})
```

**Rules:**
- Log to `%LOCALAPPDATA%/nova/logs/nova.log`
- Structured key-value pairs in `extra`, not string interpolation with sensitive data
- Never log excluded/sensitive context content — opaque references only
- Log levels: DEBUG (development), INFO (normal operations), WARNING (degraded behavior), ERROR (failures)
- Terminal output is Skin's responsibility — logging and terminal rendering are completely separate channels

### Enforcement Guidelines

**All AI agents implementing N.O.V.A. stories MUST:**

1. Define system interfaces as `Protocol` classes in `ports/`, never as concrete classes
2. Wire adapters only in `app.py` — never import adapters inside system modules
3. Route all inter-system communication through the event bus via Nerve
4. Use the config module for all file-based configuration — never read YAML/JSON directly
5. Use the migration runner for all schema changes — never run raw DDL outside migrations
6. Use the AuditLogger for all automated action logging — never write to audit_log directly
7. Use domain exception types — never let adapter-specific exceptions cross the port boundary
8. Keep excluded/sensitive context opaque at every layer — capture, storage, logging, audit, cloud prompts, generated prose, and exception messages
9. Check tier state before cloud-dependent operations — never assume full connectivity
10. Log to file, render to terminal — never mix the two channels
11. Define event types as typed classes in `core/events.py` — never use raw string event types in system code
12. Keep command parsing in Skin deterministic — natural-language intent resolution goes through Nerve/Voice and respects tier state
13. Persist before emit — events representing durable facts are emitted only after Brain confirms the write
14. Respect single ownership — each domain fact has one owning system; no duplicated writable copies
15. Route all cloud-bound context through PromptBuilder — no system may send ad hoc context to the Claude adapter directly
16. Keep adapters logic-free — adapters translate I/O, they never make policy decisions or perform ceremony logic
17. Use the Briefing Card State Contract — render States A/B/C per the explicit conditions in the UX spec; never render a hollow template
18. Follow the T1 Command Grammar Contract — canonical commands, contextual reply scoping, and fallback behavior per the UX spec
19. No hidden secondary memory stores — all persistent user data lives in the declared SQLite system of record; no undeclared caches, temp files, or debug dumps that contain user memory

## Project Structure & Boundaries

### Complete Project Directory Structure

```
nova/                                    # Repository root
├── pyproject.toml                       # Single project config (uv, dependencies, ruff, mypy, pytest)
├── uv.lock                              # Reproducible dependency lock
├── README.md
├── LICENSE
├── .gitignore
├── .python-version                      # Python 3.12+
├── setup.bat                            # Guided Windows setup script (first-run entrypoint)
│
├── config/                              # SHIPPED DEFAULTS (copied to user data dir on first run)
│   ├── modes/
│   │   ├── coding.yaml                  # Default coding mode template
│   │   └── study.yaml                   # Default study mode template
│   ├── exclusions.yaml                  # Default sensitive-context exclusion list
│   └── settings.defaults.yaml           # Default settings (no API key — that's user-specific)
│
├── src/
│   └── nova/
│       ├── __init__.py
│       ├── cli.py                       # Terminal entrypoint — argument parsing, session lifecycle
│       ├── app.py                       # Composition root — wires ports to adapters, boots monolith
│       │
│       ├── ports/                       # Abstract interfaces (Protocol classes)
│       │   ├── __init__.py
│       │   ├── brain.py                 # BrainPort — memory, sessions, transparency, deletion
│       │   ├── eyes.py                  # EyesPort — context capture, workspace snapshots
│       │   ├── hands.py                 # HandsPort — desktop actions (launch, focus, arrange)
│       │   ├── shield.py                # ShieldPort — focus protection (stubbed in T1)
│       │   ├── voice.py                 # VoicePort — personality text generation
│       │   ├── ritual.py                # RitualPort — briefing, shutdown, seed lifecycle
│       │   ├── skin.py                  # SkinPort — terminal rendering, input collection
│       │   └── nerve.py                 # NervePort — orchestration, tier state, routing
│       │
│       ├── systems/                     # System implementations (business logic)
│       │   ├── __init__.py
│       │   ├── brain/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # BrainSystem — memory operations, transparency model
│       │   │   └── models.py            # Domain types: BriefingAggregate, SessionSummary, ModeInfo, MemoryItem, DeletionPreview, etc.
│       │   ├── eyes/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # EyesSystem — context polling logic, exclusion filtering
│       │   │   └── models.py            # Domain types: WindowContext, WorkspaceSnapshot, etc.
│       │   ├── hands/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # HandsSystem — action execution, result reporting
│       │   │   └── models.py            # Domain types: ActionRequest, ActionResult, etc.
│       │   ├── shield/
│       │   │   ├── __init__.py
│       │   │   └── system.py            # ShieldSystem — no-op stub in T1, interface only
│       │   ├── voice/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # VoiceSystem — personality generation, tone adaptation
│       │   │   └── models.py            # Domain types: BriefingText, ResponseText, ProseEnrichment, etc.
│       │   ├── ritual/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # RitualSystem — briefing assembly, shutdown flow, seed
│       │   │   └── models.py            # Domain types: BriefingViewModel, BriefingState, ShutdownData, SeedData, etc.
│       │   ├── skin/
│       │   │   ├── __init__.py
│       │   │   ├── system.py            # SkinSystem — command parsing, render dispatch
│       │   │   ├── commands.py          # Command dataclass, deterministic parser
│       │   │   └── components.py        # Rich component compositions (Briefing Card, etc.)
│       │   └── nerve/
│       │       ├── __init__.py
│       │       ├── system.py            # NerveSystem — orchestration, policy, command routing
│       │       └── models.py            # Domain types: Command routing tables, policy rules
│       │
│       ├── adapters/                    # Concrete implementations (infrastructure)
│       │   ├── __init__.py
│       │   ├── claude/
│       │   │   ├── __init__.py
│       │   │   └── reasoning.py         # ClaudeReasoningAdapter — Anthropic SDK, prompt caching
│       │   ├── win32/
│       │   │   ├── __init__.py
│       │   │   ├── context.py           # Win32EyesAdapter — GetForegroundWindow, title parsing
│       │   │   └── actions.py           # Win32HandsAdapter — app launch, focus, arrange
│       │   ├── sqlite/
│       │   │   ├── __init__.py
│       │   │   ├── brain.py             # SqliteBrainAdapter — sessions, memory, transparency
│       │   │   └── repository.py        # Low-level SQLite query helpers
│       │   └── rich/
│       │       ├── __init__.py
│       │       └── skin.py              # RichSkinAdapter — Panel, Table, Tree, Progress rendering
│       │
│       ├── core/                        # Shared infrastructure
│       │   ├── __init__.py
│       │   ├── events.py                # EventBus + all typed Event subclasses
│       │   ├── config.py                # NovaConfig — loads all YAML, provides to systems
│       │   ├── tiers.py                 # TierManager — health check, state machine, transitions
│       │   ├── audit.py                 # AuditLogger — single audit interface
│       │   ├── prompt_builder.py        # PromptBuilder — trust boundary, cloud prompt minimization
│       │   ├── exceptions.py            # All domain exception types
│       │   ├── types.py                 # Shared domain types used across systems
│       │   └── storage/
│       │       ├── __init__.py
│       │       ├── engine.py            # SqliteStorageEngine — connection, backup, migration runner
│       │       └── migrations/
│       │           ├── __init__.py
│       │           ├── runner.py        # Migration runner — backup, apply, rollback
│       │           └── 001_initial_schema.py  # T1 schema: sessions, snapshots, memory_items, audit_log
│       │
│       └── setup/                       # First-run setup flow (part of runtime package)
│           ├── __init__.py
│           └── wizard.py                # Guided setup — API key, first mode, first workspace capture
│
├── tests/                               # Mirrors src structure
│   ├── conftest.py                      # Shared fixtures (test db, mock adapters, event bus)
│   ├── unit/
│   │   ├── systems/
│   │   │   ├── test_brain.py
│   │   │   ├── test_eyes.py
│   │   │   ├── test_hands.py
│   │   │   ├── test_voice.py
│   │   │   ├── test_ritual.py
│   │   │   ├── test_skin.py
│   │   │   └── test_nerve.py
│   │   ├── core/
│   │   │   ├── test_events.py
│   │   │   ├── test_config.py
│   │   │   ├── test_tiers.py
│   │   │   ├── test_audit.py
│   │   │   ├── test_prompt_builder.py
│   │   │   └── test_migrations.py
│   │   └── adapters/
│   │       ├── test_sqlite_brain.py
│   │       ├── test_win32_context.py
│   │       └── test_rich_skin.py
│   └── integration/
│       ├── test_continuity_loop.py      # End-to-end: startup → briefing → mode → shutdown → resume
│       ├── test_transparency.py         # Transparency query matches SQLite state
│       ├── test_deletion.py             # Deletion propagation across all tables
│       ├── test_tier_transitions.py     # Full → degraded → offline → recovery
│       └── test_exclusion_boundary.py   # Excluded context stays opaque across capture, storage,
│                                        #   transparency, audit, and prompt building
│
└── docs/                                # Developer documentation (not user-facing)
    └── architecture.md                  # → symlink or reference to planning artifact
```

### Runtime User Data Directory

All user-owned data lives in `%LOCALAPPDATA%/nova/`:

```
%LOCALAPPDATA%/nova/                     # User data directory (created on first run)
├── nova.db                              # SQLite database (sessions, memory, audit, schema_version)
├── settings.yaml                        # User settings (API key, bluntness level, preferences)
├── exclusions.yaml                      # User-editable exclusion list
├── modes/
│   ├── coding.yaml                      # User's coding mode config
│   └── study.yaml                       # User's study mode config
├── backups/                             # Auto-created before schema migrations
│   └── nova_20260413_191500.db          # Timestamped backup
└── logs/
    └── nova.log                         # Structured log file (rotated)
```

**Separation principle:** The repo contains application code and shipped defaults. The user data directory contains all runtime state, user config, and accumulated memory. This separation is load-bearing for local-first trust: users own their data directory, can back it up by copying it, can inspect it with standard tools, and it never mixes with application source.

### Architectural Boundaries

**System Boundaries (port interfaces):**

| Boundary | Port File | What Crosses It | What Does Not |
|----------|-----------|-----------------|---------------|
| Brain ↔ SQLite | `ports/brain.py` | Domain types (Session, MemoryItem) | sqlite3 types, raw SQL, connection objects |
| Eyes ↔ Win32 | `ports/eyes.py` | WindowContext, WorkspaceSnapshot | HWND handles, win32gui calls, psutil Process objects |
| Hands ↔ Win32 | `ports/hands.py` | ActionRequest, ActionResult | subprocess.Popen, ShellExecute calls |
| Voice ↔ Claude | `ports/voice.py` | BriefingText, ResponseText | anthropic.Message, API response objects |
| Skin ↔ Rich | `ports/skin.py` | Render instructions (what to show) | Rich Panel/Table/Tree objects, Console instance |
| Nerve ↔ Systems | `ports/nerve.py` | Events, Commands, tier state | System internals, adapter references |

**Trust Boundaries:**

| Boundary | Where Enforced | What It Protects |
|----------|---------------|-----------------|
| Capture boundary | Eyes adapter | Excluded apps never produce identifiable events |
| Cloud boundary | PromptBuilder | Raw memory never reaches Claude API |
| Audit boundary | AuditLogger | Excluded context details never enter audit trail |
| Transparency boundary | Brain transparency model | Everything stored is shown; nothing hidden |
| Deletion boundary | Brain deletion propagation | Deleted content removed from all representations before transparency re-query |

**Data Boundaries:**

| Data Type | Lives In | Owned By | Accessed By |
|-----------|----------|----------|-------------|
| Sessions, seeds, summaries | SQLite `sessions` | Brain | Ritual (read/write via Brain port), Nerve (read for orchestration decisions) |
| Workspace snapshots | SQLite `workspace_snapshots` | Brain | Eyes (write via Brain port), Ritual (read via Brain port for briefing) |
| Memory items | SQLite `memory_items` | Brain | Voice (read via Brain port for context), Ritual (read via Brain port for briefing) |
| Transparency model | Brain (computed from SQLite) | Brain | Skin renders what Brain provides — Skin never queries storage directly |
| Audit log | SQLite `audit_log` | AuditLogger | Hands (write), Brain (write deletions), Nerve (write tier changes), Brain (read for transparency model) |
| Mode configs | YAML files `modes/*.yaml` | Config module | Nerve (read), Hands (read for restore), Brain (read for transparency model) |
| Exclusion list | YAML file `exclusions.yaml` | Config module | Eyes (read at capture), PromptBuilder (read at cloud boundary) |
| User settings | YAML file `settings.yaml` | Config module | All systems via NovaConfig |

### FR Category to Structure Mapping

| FR Category | Primary Module(s) | Key Files |
|------------|-------------------|-----------|
| **Setup & Onboarding** (FR1-6) | `setup/wizard.py`, `core/config.py` | Setup script, config loader, first-run flow |
| **Workspace Modes** (FR7-13) | `systems/nerve/`, `systems/hands/`, `core/config.py` | Mode config files, Nerve routing, Hands restore |
| **Context Awareness** (FR14-18) | `systems/eyes/`, `adapters/win32/context.py` | Win32 polling adapter, exclusion filtering |
| **Memory & Persistence** (FR19-24) | `systems/brain/`, `adapters/sqlite/brain.py`, `core/storage/` | SQLite adapter, migration runner, storage engine |
| **Session Rituals** (FR25-31) | `systems/ritual/`, `systems/voice/` | Briefing assembly, shutdown flow, seed lifecycle |
| **Transparency & Trust** (FR32-38) | `systems/brain/`, `core/prompt_builder.py`, `core/audit.py` | Transparency model, deletion propagation, audit |
| **Desktop Actions** (FR39-44) | `systems/hands/`, `adapters/win32/actions.py` | Win32 actions adapter, action registry |
| **Privacy & Data Protection** (FR45-52) | `systems/eyes/`, `core/prompt_builder.py`, `core/audit.py` | Exclusion filtering, prompt minimization, opaque audit |
| **Personality** (FR53-56) | `systems/voice/`, `adapters/claude/reasoning.py` | Voice system, Claude adapter |
| **System Management** (FR57-60) | `systems/nerve/`, `core/tiers.py`, `core/storage/` | Tier manager, migration runner, self-update |

### T1 Skeleton — What Exists at First Implementation Milestone

**Active in T1:**
- `cli.py`, `app.py` — entrypoint and composition root
- All 8 port files in `ports/` (Shield is interface-only)
- `systems/brain/`, `systems/eyes/`, `systems/hands/`, `systems/ritual/`, `systems/voice/`, `systems/skin/`, `systems/nerve/` — all with `system.py` and `models.py`
- `systems/shield/system.py` — no-op stub
- `adapters/sqlite/brain.py` — SQLite storage
- `adapters/win32/context.py`, `adapters/win32/actions.py` — Windows integration
- `adapters/claude/reasoning.py` — Claude API
- `adapters/rich/skin.py` — Rich terminal rendering
- `core/events.py`, `core/config.py`, `core/tiers.py`, `core/audit.py`, `core/prompt_builder.py`, `core/exceptions.py`, `core/types.py`
- `core/storage/engine.py`, `core/storage/migrations/runner.py`, `core/storage/migrations/001_initial_schema.py`
- `setup/wizard.py` — guided first-run
- `config/` shipped defaults — one mode template, exclusion defaults, settings defaults

**Not yet active in T1 (exists as stubs or deferred):**
- `systems/shield/system.py` — interface only, no implementation
- No voice STT/TTS adapters
- No Textual skin adapter
- No semantic search / vector storage

### Development Workflow

**Running N.O.V.A. locally:**
```bash
# First time
git clone <repo>
./setup.bat                    # or: uv run python -m nova.setup
# Subsequent sessions
uv run nova                    # or: uv run python -m nova.cli
```

**Running tests:**
```bash
uv run pytest tests/unit/      # Unit tests (fast, no external deps)
uv run pytest tests/integration/  # Integration tests (real SQLite, mock Win32/Claude)
```

**Code quality:**
```bash
uv run ruff check src/ tests/  # Lint
uv run ruff format src/ tests/ # Format
uv run mypy src/               # Type check
```

### Installation & Packaging Specification

**Target user experience:** Clone repo → run one setup command → answer a few prompts → N.O.V.A. starts. Must complete in under 15 minutes on a supported Windows 11 machine (NFR2).

**Primary entrypoint: `setup.bat`**

A single batch script at the repo root. This is the only file users need to know about. It handles everything or fails clearly.

```
setup.bat execution sequence:

1. CHECK PREREQUISITES
   - Verify Windows 11 (winver check)
   - Check for Python 3.12+ (python --version)
     → If missing: print download URL, exit with clear message
   - Check for uv (uv --version)
     → If missing: install uv via official installer (irm https://astral.sh/uv/install.ps1 | iex)
     → Verify install succeeded

2. CREATE ENVIRONMENT
   - Run: uv sync (creates venv, installs all deps from pyproject.toml + uv.lock)
   - Run pywin32 post-install scripts if pywin32 is in deps
     → Failure here: warn but continue (pywin32 post-install is not always needed)

3. CREATE USER DATA DIRECTORY
   - Create %LOCALAPPDATA%/nova/ if it doesn't exist
   - Copy config/modes/*.yaml → %LOCALAPPDATA%/nova/modes/ (only if target doesn't exist)
   - Copy config/exclusions.yaml → %LOCALAPPDATA%/nova/exclusions.yaml (only if target doesn't exist)
   - Copy config/settings.defaults.yaml → %LOCALAPPDATA%/nova/settings.yaml (only if target doesn't exist)
   - Create %LOCALAPPDATA%/nova/backups/ and %LOCALAPPDATA%/nova/logs/

4. LAUNCH FIRST-RUN WIZARD
   - Run: uv run python -m nova.setup
   - Wizard handles: API key prompt, first mode creation, first workspace capture
   - Wizard writes API key to settings.yaml
   - On wizard completion: first N.O.V.A. session starts automatically
```

**Error handling contract for setup.bat:**

| Failure | Behavior |
|---------|----------|
| Not Windows 11 | Print: "N.O.V.A. requires Windows 11. Current OS: {version}" → exit |
| Python not found | Print: "Python 3.12+ is required. Download from https://python.org" → exit |
| Python < 3.12 | Print: "Python 3.12+ is required. Found: {version}" → exit |
| uv install fails | Print: "Could not install uv. Install manually: https://docs.astral.sh/uv/" → exit |
| uv sync fails | Print the uv error output verbatim → exit |
| User data dir creation fails | Print: "Could not create data directory at %LOCALAPPDATA%/nova/ — check permissions" → exit |
| First-run wizard aborted | Print: "Setup cancelled. Run setup.bat again to restart, or uv run nova to start without guided setup." → exit cleanly |

**Rules:**
- setup.bat must be idempotent — running it twice must not corrupt existing user data or overwrite user config
- Config files are copied only if the target does not exist (never overwrite user customizations)
- setup.bat must not require administrator privileges
- All output should be clear, non-technical, and never dump raw stack traces
- The script must work from both cmd.exe and PowerShell

**Fallback path: Manual install**

For power users (Daniel archetype) who prefer to control the process:

```bash
git clone <repo>
cd nova
uv sync
uv run python -m nova.setup    # just the wizard
uv run nova                    # subsequent sessions
```

This is documented in README.md as an alternative, not the primary path.

**Subsequent launches:**

After setup, users run N.O.V.A. with:
```bash
uv run nova
```

This invokes `src/nova/cli.py` which:
1. Loads config from `%LOCALAPPDATA%/nova/settings.yaml`
2. Checks for pending migrations (auto-backup + migrate if needed)
3. Boots the composition root (`app.py`)
4. Starts the interactive session (Briefing Card → prompt loop)

**pyproject.toml entry point:**

```toml
[project.scripts]
nova = "nova.cli:main"
```

This makes `uv run nova` work as the canonical launch command.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All technology choices (Python 3.12+/asyncio, SQLite stdlib, Rich, Anthropic SDK, pywin32/psutil) are compatible and conflict-free. Ports-and-adapters architecture ensures no coupling between adapter implementations. PromptBuilder correctly isolated as a trust-boundary component between Brain and Claude adapter.

**Pattern Consistency:** Naming conventions (snake_case Python, PascalCase classes, snake_case SQL, dot-notation events, `nova verb target` commands) are uniform across all areas. All 12 enforcement guidelines are consistent with the architectural decisions.

**Structure Alignment:** Project directory structure directly maps to the port/system/adapter/core layering. Composition root is the single boundary-crossing point. Trust boundaries are structurally enforced at Eyes (capture), PromptBuilder (cloud), AuditLogger (logging), and Brain (transparency/deletion).

### Requirements Coverage ✅

**All 60 functional requirements are architecturally supported.** Each FR category maps to specific modules with clear ownership. No FR is orphaned or unsupported.

**All 31 non-functional requirements are architecturally addressed.** Performance budgets are supported by the single-process asyncio model, local SQLite queries, and prompt caching. Security/privacy requirements are enforced structurally through trust boundaries. Reliability requirements are met by the three-tier degradation model and local-ops-never-depend-on-cloud principle.

### Implementation Readiness ✅

**Decision completeness:** All 5 critical decision areas documented with rationale, code examples, and anti-patterns. Technology stack fully specified.

**Structure completeness:** Directory tree defined to individual file level. Every system, port, adapter, and core module enumerated. Test structure explicit with integration tests for trust boundaries.

**Pattern completeness:** 19 enforcement guidelines cover all identified conflict points. Conventions defined for ports, events, composition, commands, config, migrations, audit, naming, errors, logging, ownership, write-then-emit, cloud egress, briefing states, and command grammar.

### Gap Analysis

**Critical Gaps:** None. All 8 pre-story gaps resolved (2026-04-14).

**Resolved Gaps (previously important, now addressed):**
1. ~~**Day-1 empty-state briefing spec**~~ — **RESOLVED.** Briefing Card State Contract (States A/B/C) defined in UX spec with explicit render conditions and copy.
2. ~~**Complete command grammar**~~ — **RESOLVED.** T1 Command Grammar Contract in UX spec with three layers (launch/in-session/contextual), partial command behavior, and fallback responses.
3. ~~**Briefing data structure**~~ — **RESOLVED.** Decision 3b defines BriefingAggregate → BriefingViewModel with field mapping, state determination, and render responsibility boundaries.
4. ~~**Config file YAML schemas**~~ — **RESOLVED.** Mode, exclusion, and settings schemas defined in Decision 3 with validation rules and config loading contract.
5. ~~**Personality / Voice Doctrine**~~ — **RESOLVED.** N.O.V.A. Personality & Voice Doctrine section in UX spec with prohibited patterns, bluntness levels, progression, and strategic praise rules.
6. ~~**Installation & Packaging**~~ — **RESOLVED.** Installation & Packaging Specification in this document with setup.bat execution sequence, error handling, and fallback path.
7. ~~**Ad-hoc mode creation flow**~~ — **RESOLVED.** Journey 3 in UX spec with explicit/implicit entry points, flow rules, and differences from first-run wizard.
8. ~~**Critical error scenarios**~~ — **RESOLVED.** Three scenarios (SQLite corruption, malformed API response, partial restore) defined in UX spec with exact UX behavior.

**Remaining Gaps (non-blocking, address during implementation):**
1. **Domain type fields** (Session, MemoryItem, TransparencyModel, etc.) — define in first implementation stories to prevent drift across agents. Note: BriefingAggregate and BriefingViewModel types are already fully defined in Decision 3b.
2. **Event payload schemas for core T1 events** — define alongside domain types early so event-driven implementation stays consistent across agents. The typed event classes in `core/events.py` need concrete payload field definitions, not just dict comments.
3. **Personality prompt engineering strategy** — define during Voice system implementation (how much personality budget, how personality degrades across tiers, what "cached responses" means in degraded mode). The Personality Doctrine in the UX spec provides the behavioral contract; the Claude system prompt construction is the remaining implementation detail.

**Deferred Gaps:**
- PromptBuilder ↔ Claude token budget contract — T2
- Log rotation strategy — implementation detail
- Unexpected termination recovery test — add to integration suite

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] 60 FRs analyzed and categorized
- [x] 31 NFRs with hard budgets identified
- [x] Technical constraints documented (8 hard constraints)
- [x] 6 cross-cutting concerns elevated to architecture level
- [x] T1 defined as primary architecture slice

**✅ Architectural Decisions**
- [x] Module boundaries and ownership for all 8 systems
- [x] Event flow for T1 continuity loop with tier-aware branching
- [x] Data schema (SQLite tables + file-based config separation)
- [x] Capability tier detection and enforcement (tolerant degrade model)
- [x] Trust constraint enforcement (exclusion, minimization, deletion, audit)

**✅ Implementation Patterns**
- [x] Port & adapter convention with examples and anti-patterns
- [x] Event bus convention with typed event classes
- [x] Composition root convention
- [x] Command routing convention
- [x] Config loading, migration, audit, naming, error handling, logging conventions
- [x] 12 enforcement guidelines for AI agents

**✅ Project Structure**
- [x] Complete directory tree to file level
- [x] Runtime user data directory defined
- [x] System, trust, and data boundaries mapped
- [x] FR categories mapped to specific modules
- [x] T1 skeleton explicitly defined
- [x] Development workflow documented

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence Level: HIGH**

The architecture is complete, internally coherent, covers all PRD requirements, and provides sufficient detail for AI agents to implement consistently. The modular monolith with ports-and-adapters ensures T1 ships cleanly while T2/T3/v0.15/v0.2/v1.0 evolve without rewriting core modules.

**Key Strengths:**
- Clean module boundaries with strict port interfaces prevent implementation drift
- Trust boundaries enforced structurally, not by convention
- T1 continuity loop is the organizing principle — no dead weight, no premature complexity
- Three capability tiers as architecture-level behavior, not UI decoration
- Voice/Skin separation preserves personality as a first-class product requirement
- Evolution path is additive (new adapters behind existing ports) — no rewrites needed

**Areas for Future Enhancement:**
- Semantic memory architecture (sqlite-vec / LanceDB) — v0.2 Brain adapter addition
- Voice STT/TTS adapters — v0.2 behind existing Voice port
- Shield implementation — v0.15 behind existing Shield port
- Textual TUI — v0.2 Skin adapter swap
- Tauri GUI — v1.0 Skin adapter swap
- Local LLM — v0.3+ Claude adapter alternative

### Implementation Handoff

**The first implementation slice must produce a runnable end-to-end T1 continuity loop, not just scaffolding:**
1. Scaffold repo (uv init, directory structure, pyproject.toml, core infrastructure)
2. SQLite migration (`001_initial_schema.py`) + migration runner
3. Config loading (modes, exclusions, settings from `%LOCALAPPDATA%/nova/`)
4. One mode (coding) with app set and restore
5. Shutdown seed capture + session persistence
6. Next-session resume (briefing with seed, mode suggestion, workspace restore)
7. Transparency query ("What do you know?" showing all stored state)

The goal is a working loop — startup → briefing → mode → work → shutdown → resume — not isolated modules. The architecture is designed so these can be built incrementally and composed through the event bus and composition root.

**AI Agent Guidelines:**
1. Follow all architectural decisions exactly as documented in this file
2. Use implementation patterns consistently — the 12 enforcement guidelines are mandatory
3. Respect project structure and boundaries — no imports across adapter boundaries except in `app.py`
4. Every system communicates through events via Nerve — no direct system-to-system calls
5. Trust boundaries (exclusion, cloud minimization, audit opacity, deletion propagation) must be enforced at every layer
6. Check tier state before any cloud-dependent operation
7. When in doubt about an architectural question, refer to this document — it is the authoritative source for N.O.V.A.'s architecture
