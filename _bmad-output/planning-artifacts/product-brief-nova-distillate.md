---
title: "Product Brief Distillate: N.O.V.A."
type: llm-distillate
source: "product-brief-nova.md"
created: "2026-04-13"
purpose: "Token-efficient context for downstream PRD creation — structured as a decision-oriented handoff"
---

# N.O.V.A. — PRD Handoff Distillate

## 1. Product Brief Expansion

### Core Problem
- **Context loss is the silent tax on fragmented work sessions.** Developers lose 15–30 minutes per session restoring working state. For someone with a 2-hour evening window, that is 12–25% of productive time burned daily.
- Existing tools are stateless (AI assistants forget between sessions), passive (recording tools search but do not restore), manual (PowerToys Workspaces requires user to set up layouts), or blunt (focus apps block without understanding what matters).
- No product combines persistent memory + workspace restore + focus protection + desktop awareness into a single experience.

### Target Users
- **Primary: Solo builders/developers in VS Code** — age 20–30, evening sessions, lose significant time to session recovery. Hook: "Resume my exact coding context."
- **Secondary: Project-heavy students** — age 18–25, juggling coursework + side projects, short work windows. Hook: "One command to enter study mode."
- **Tertiary: Privacy-first desktop power users** — age 25–40, actively avoid cloud AI, willing to invest in local tooling. Hook: "Runs on your machine, remembers on your machine."
- **Explicitly NOT for:** Enterprise teams, general consumers, smart-home users, people who just want a chatbot.

### Value Proposition
- **Day 1 value:** Workspace setup, context awareness, morning briefing, sharp interaction, shutdown flow with tomorrow seed. Memory is empty but the experience is already useful.
- **Day 2+ compounding value:** Session memory and context resume begin to compound. The longer N.O.V.A. runs, the more it knows about projects, patterns, and preferences.
- **Cold-start story (one sentence):** Day one value comes from workspace setup, awareness, and rituals; day two value comes from compounding memory and context continuity.
- **Core hero loop:** Open laptop → see briefing → enter mode → restore context → work → shutdown with tomorrow seed.
- **Core wedge:** N.O.V.A. is not the best chatbot for your desktop. It is the first system designed to preserve continuity of work across days.
- **One-line positioning:** N.O.V.A. is a desktop-native context and focus companion for builders.
- **Privacy line:** Personal memory and workspace data are stored locally by default; cloud reasoning may be used in MVP, with a clear path toward hybrid and local inference.

### Scope Boundaries

**v0.1 — Terminal-First MVP (~8–12 weeks):**
- Rich terminal interface (Rich library, CLI-first)
- Persistent local memory (SQLite on-device; sqlite-vec available as upgrade path for semantic search in v0.15/v0.2, not required for v0.1 core functionality)
- Workspace-level context capture and restore (active apps, window list, workspace mode, session notes — NOT deep per-app state)
- Workspace modes (coding, study, shutdown) with configurable app and behavior profiles
- Morning briefing and shutdown flow with "tomorrow seed"
- App and window awareness via Windows APIs (win32gui, psutil)
- Transparency command: "What do you know right now?"
- Claude API for reasoning (with prompt caching, ~$0.50–2.25/month)
- Safe desktop actions (launch, focus, arrange) with confirmation for sensitive actions
- Guided first-run setup minimizing friction around Python environment and API configuration

**v0.15 — "It Protects Me" (~Week 10–11):**
- Smart DND / focus block protection
- Simple distraction detection
- Draft mode for risky actions
- Stronger save/resume with richer context

**v0.2 — "It Feels Alive" (~Week 14–16):**
- Voice interaction (push-to-talk, wake word, faster-whisper STT, Piper/Kokoro TTS)
- TUI upgrade via Textual
- Deep per-app state restoration (VS Code tabs, terminal history)
- Earned autonomy through repetition
- Focus profiles
- Day scoring (transparent, optional)
- Weekly insight reports
- Semantic memory search (sqlite-vec or LanceDB)

**v1.0 — Full Vision:**
- Desktop GUI (Tauri 2.0)
- Local/hybrid LLM options
- MCP integration for tool ecosystem
- Advanced automation
- Community-contributed modes and rituals

### Explicit Non-Goals
- N.O.V.A. is not a chatbot or general-purpose AI assistant
- Not a code generation tool (that is Claude Code's job)
- Not a replacement for IDE-level copilots
- Not an email/calendar/communication tool
- Not a cross-platform product (Windows 11 first, others later)
- Not a smart-home or IoT controller
- Not a plugin marketplace in MVP
- No multi-device sync
- No cloud storage of personal memory — ever for the core product

---

## 2. System Architecture Summary

### The 8 Systems

| System | Role | MVP? |
|--------|------|------|
| **The Brain** | Memory, learning, personalization, judgment. Stores insights not logs. Fades and reinforces like human memory. | v0.1 (SQLite, basic). sqlite-vec for semantic search is a ready upgrade path for v0.15/v0.2, not a v0.1 requirement. |
| **The Eyes** | App-level context awareness, mode detection. API-first (win32gui), screen reading as fallback only. | v0.1 |
| **The Hands** | Workspace setup, app launching, file ops. Draft mode for safety. Earned autonomy. Rollback per action. | v0.1 (safe tier only) |
| **The Shield** | Attention firewall, smart DND, focus protection. Guards the good, doesn't just block the bad. | v0.15 |
| **The Voice** | Personality, tone, pushback, layered responses. Sharp + Loyal + Witty. Earned familiarity over time. | v0.1 (text personality) |
| **The Ritual** | Morning briefing, shutdown, tomorrow seed, signature ceremonies. Self-trimming. Creates rhythm, not reminders. | v0.1 |
| **The Skin** | Terminal UI (v0.1), TUI (v0.2), HUD/Tauri (v1.0). Presence levels: Invisible → Ambient → Active → Operator. | v0.1 (Rich terminal) |
| **The Nerve** | Orchestration, permissions, initiative control. Decides: stay silent, suggest, or act. Conflict resolution between systems. | v0.1 |

### How They Interact
- **The Nerve** is the central orchestrator — it receives events from all systems and decides what happens next.
- **The Eyes** feed context to **The Brain** (what is the user doing?) and **The Nerve** (should we act?).
- **The Brain** informs **The Voice** (what do we know to say?) and **The Hands** (what context to restore?).
- **The Ritual** triggers at time-based boundaries (morning, shutdown) and coordinates with **Brain** (what happened?), **Eyes** (what's open?), and **Hands** (set up workspace).
- **The Shield** receives signals from **Eyes** and **Brain** to determine when to protect focus, and instructs **Nerve** to suppress or allow interruptions.
- **The Skin** renders whatever **Nerve** decides to surface.

### Intelligence Layers
1. **Reactive:** User asks, N.O.V.A. responds.
2. **Contextual:** N.O.V.A. sees what you're doing, understands the situation.
3. **Proactive:** N.O.V.A. warns, suggests, or organizes before you ask.

### Initiative Levels
1. **Silent:** Only acts when asked.
2. **Suggestive:** Notices and recommends.
3. **Active:** Performs approved automations.

---

## 3. Key Product Decisions Already Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| MVP interface | Terminal-first (Rich library) | Fastest path, fits developer audience, no frontend build step |
| Long-term vision | Voice-first desktop companion | Core product identity — voice layered after core loop proven |
| Privacy model | Local-first, no cloud memory storage | Trust contract, differentiator, regulatory simplification |
| Platform | Windows 11 first | Primary user's platform, underserved market |
| Target user | Builder-first (solo devs, students) | Specific wedge > generic market, authentic story |
| LLM strategy | Claude API for MVP reasoning | Best personality/reasoning quality, prompt caching for cost |
| Memory | SQLite + sqlite-vec, single-file, on-device | Simple, portable, no server, backup = copy one file |
| Context restore (v0.1) | Workspace-level only (apps, windows, mode, notes) | Conservative — deep per-app state is fragile, deferred to v0.2+ |
| Trust model | Tiered: safe actions execute freely, sensitive actions require confirmation | Earn trust over time, never surprise the user. v0.1 minimum: audit trail + confirmation for careful actions. Rollback target: wherever reversible, with broader undo maturing after MVP. |
| Transparency | First-class "What do you know?" command | User always has visibility and control over N.O.V.A.'s knowledge |
| Open-core | Core engine and memory open from day one | Trust signal for privacy-first audience, community growth |
| Monetization | Free local core, premium advanced features later | Never lock users out of their own memory or data |
| Personality | Sharp + Loyal + Witty, behavioral doctrine enforced | Personality is a first-class design constraint, not a cosmetic layer |
| Category positioning | "Desktop companion for builders" (not "Workspace OS" yet) | Earned through execution — "Workspace OS" framing reserved for later |

### Decisions with unresolved tensions (PRD must resolve or acknowledge):
- **Wake word engine:** Build Kit chose Porcupine (free tier). Technical research recommends openWakeWord (fully open source, no API limits). Decision needed for v0.2.
- **TTS engine:** Build Kit chose Edge TTS (zero cost). Technical research recommends Piper (local, better quality). Decision needed for v0.2.
- **Memory framework evolution:** Build Kit says SQLite → ChromaDB. Technical research says SQLite + sqlite-vec → LanceDB. Domain research says Mem0 + ChromaDB → Zep. Alignment needed for v0.2+ roadmap.

---

## 4. Research Findings That Matter to the PRD

### Technical Feasibility
- **STT:** faster-whisper (small.en) + Silero VAD delivers ~300–500ms voice-to-text on CPU. 3x faster than whisper.cpp. Upgrade path to medium.en or distil-large with GPU. HIGH confidence.
- **Context awareness:** `win32gui.GetForegroundWindow()` + title parsing gives rich app context at ~1ms per poll. VS Code titles reveal current file + project. No setup complexity. HIGH confidence.
- **Memory:** SQLite + sqlite-vec for MVP. Single-file, conversations + metadata + vectors. ~30MB RAM. Observational memory patterns reduce token costs up to 90%. HIGH confidence.
- **Desktop automation:** pywinauto with UIA backend. Reliable for app launching, window management. Fragile for deep menu clicks, field fills (per-app variation). MEDIUM confidence for "careful" tier.
- **Claude API cost:** ~$0.50–2.25/month with prompt caching (50 turns/day, Sonnet). Prompt caching yields ~90% savings. HIGH confidence.
- **Hardware baseline:** 16GB RAM Windows 11 PC. ~2GB baseline for STT+TTS+vector+wake word+app overhead. Leaves ~14GB for system + local LLM later.
- **Architecture:** Modular monolith, single Python process, asyncio event loop, ports-and-adapters for swappability. ProcessPoolExecutor for CPU-heavy work (STT, TTS, embeddings).

### Competitive Positioning
- **The "voice-first + memory + local desktop agent" quadrant is virtually empty.** No existing product competes on all three axes.
- **Rewind AI was acquired by Meta (Dec 2025), Mac app killed.** Users forced onto Meta's privacy terms. The "desktop AI that remembers your work" category has no leader. N.O.V.A. is NOT a Rewind clone — it is the next step: active orchestration, not passive recording.
- **Screenpipe** (50+ pipes, MIT, 50K+ stars) is the closest open-source project. Passive recording/search layer, not proactive orchestrator. N.O.V.A. is complementary, not competitive.
- **OpenClaw** (60K+ stars) is the strongest category energy. But security risks (7.6% of ClawHub skills contain dangerous patterns, ClawHavoc malware), memory in plain Markdown. N.O.V.A. wins on trust, safety, focus-oriented design.
- **Microsoft Copilot** is evolving toward an "AI execution layer" but remains cloud-first, generic, and plagued by Recall privacy backlash. Major competitive threat if Microsoft ships workspace orchestration, but unlikely to serve builder-first niche.
- **PowerToys Workspaces** restores window layouts manually. No AI, no voice, no memory, no modes. Validates the concept but does not compete on intelligence.
- **The Windows-first local AI desktop space is overwhelmingly Mac-first** (Enclave, Rewind was Mac-only, Screenpipe is Mac-primary). Windows developers and power users are underserved.

### Customer Pain Points (validated by market research)
1. Context loss across sessions — #1 pain, every session starts as blank slate
2. Context switching destroys focus — 15–30 min recovery per major switch
3. AI doesn't work where builders work — browser-based, no desktop presence
4. Users don't trust AI to act without oversight — desktop actions scarier than code suggestions
5. AI doesn't learn patterns — tools are stateless, every conversation restarts
6. Voice latency too high in existing tools — Home Assistant had ~5-second STT delays

### Decision-Process Insights
- **Adoption is retrospective, not prospective.** Users realize they've been using it daily for two weeks. They don't decide upfront.
- **Critical adoption gate:** Setup must take <15 minutes. First session must deliver immediate visible payoff. Both gates must pass or user abandons.
- **Discovery channels:** Reddit (r/LocalLLaMA, r/selfhosted), Hacker News, YouTube, GitHub READMEs > landing pages, peer recommendation.
- **Cost sensitivity:** Students and solo devs expect free core + optional API costs. Predictable cost matters more than absolute cost.

### Regulatory/Trust Constraints
- **EU AI Act (fully applicable Aug 2, 2026):** N.O.V.A. = limited risk. Primary obligation: transparency (users must know they're interacting with AI). Open-source protections exist.
- **Emotion detection from biometrics is strictly prohibited** under EU AI Act Article 5(1)(f). N.O.V.A. must never attempt this.
- **Illinois BIPA:** Voiceprint extraction carries $5,000/reckless violation. N.O.V.A. must process voice ephemerally — no voiceprint storage.
- **11 US states require all-party consent for recording.** Wake-word activation (not always-on) mitigates this.
- **Overall regulatory risk: LOW** for local-first architecture. Main attention areas: voice data handling and AI transparency disclosure.
- **Open-source model licensing:** faster-whisper (MIT), Piper (MIT), sqlite-vec (MIT), openWakeWord (Apache 2.0) — all permit commercial use. Llama 4 community license requires special permission above 700M MAU (not a concern for MVP).

---

## 5. Rejected / Deferred Ideas

### Rejected (Avoid)
| Idea | Why Rejected | Status |
|------|-------------|--------|
| Emotion detection from typing speed or voice tone | Unreliable, creepy, prohibited under EU AI Act Article 5(1)(f) | **AVOID** |
| Full screen OCR as primary context source | Unreliable, heavy, privacy risk. Win32 API metadata is better. | **AVOID** |
| Full session replay | Too heavy, privacy risk, not aligned with "insights not logs" principle | **AVOID** |
| Always-on deep monitoring | Privacy violation, unnecessary CPU load, trust-breaking | **AVOID** |
| General-purpose chatbot behavior | Contradicts core identity — N.O.V.A. is an operator, not a chatbot | **AVOID** |
| Code generation as a feature | Claude Code's job, not N.O.V.A.'s — avoid scope creep into IDE territory | **AVOID** |
| Overly dynamic Shield (aggressive intervention) | Fails the "would this annoy me after 6 months?" stress test | **AVOID** |

### Deferred (Later)
| Idea | Why Deferred | Target |
|------|-------------|--------|
| Voice interaction (STT/TTS) | Prove core loop first, then layer voice | v0.2 |
| Deep per-app state restoration | Fragile per-app automation, high implementation cost for marginal v0.1 gain | v0.2+ |
| Wake word detection | Depends on voice pipeline | v0.2 |
| Teach-by-observation automation | Research problem — watches once, automates forever. High potential, hard to build. | v1.0+ |
| GUI / desktop overlay / HUD | Terminal-first MVP, Tauri 2.0 when ready | v1.0 |
| Local LLM inference | Claude API handles MVP reasoning. Local models (7B–14B) feasible but not needed yet. | v0.3+ |
| MCP integration | 200+ tool servers available. Design with compatibility but don't implement until v1.0. | v1.0 |
| Notification rewriting ("translate noise into signal") | Best SCAMPER idea for Shield, but fails "build as solo dev" filter for MVP | v0.2+ |
| Event-based ceremonies | Fails "2-hour window" stress test for MVP | v0.2 |
| Weekly insight reports | Fails "2-hour window" stress test for MVP | v0.2 |
| Social exception layer | Non-negotiable eventually, but not for v0.1 | v0.15+ |
| Plugin/extension system | Community-contributed modes and rituals are the long-term play | v1.0 |
| Multi-device sync | Local-first means single device for now | Future |
| Cross-platform (macOS, Linux) | Windows-first. Others only after Windows is solid. | Future |

---

## 6. Signature Moments

These are the moments that define N.O.V.A.'s identity. Each must feel distinct, intentional, and earned. The PRD must preserve these as first-class experiences, not generic features.

### 1. The Morning Ritual
- **What:** On first activation each day, N.O.V.A. presents a visual briefing: what you left yesterday, what's pending, what you said you'd do today, relevant context.
- **Why it matters:** Creates rhythm and continuity. Eliminates the "where was I?" cold start.
- **Design rules:** Visual briefing card first, voice on demand. Self-trimming (rituals that lose relevance fade). Must feel natural at 6 AM half asleep.
- **Moat demonstration:** No other tool connects yesterday's shutdown to today's start.

### 2. The Context Resume (Hero Moment)
- **What:** "Resume my session" → N.O.V.A. restores workspace context: launches apps, sets up workspace mode, surfaces session notes and tomorrow seed from last time.
- **Why it matters:** The single most demoable moment. Zero context-rebuild time.
- **Design rules:** v0.1 restores workspace-level context (apps, windows, mode, notes). Deeper per-app state in v0.2+. Must work reliably — trust is earned here.
- **Moat demonstration:** Memory + context + action combined. No single competitor does all three.

### 3. The Honest Mirror
- **What:** Accountability with controlled bluntness. "That's procrastination dressed as research." Three levels: Calm, Direct, Ruthless.
- **Why it matters:** Protects focus by being honest, not just blocking.
- **Design rules:** One roast per day max. Bluntness level is user-controllable. Never mean, always loyal. Must survive the "would this annoy me after 6 months?" test.
- **Moat demonstration:** No AI tool tells you the truth. N.O.V.A. does.

### 4. The Transparency Moment
- **What:** "What do you know right now?" → N.O.V.A. shows exactly what it remembers, what context it has, what assumptions it's making. "Want me to forget anything?"
- **Why it matters:** Rewrites the AI-human trust relationship. User always has control.
- **Design rules:** Must be a first-class command, not buried in settings. Must show everything — no hidden state. "Help you forget" is the dark horse feature.
- **Moat demonstration:** No ambient AI tool offers this level of transparency.

### 5. The Tomorrow Seed
- **What:** During shutdown flow, N.O.V.A. captures your state, progress, and a note to your future self about what to do next.
- **Why it matters:** Creates continuity across days. Direction planted while still in flow.
- **Design rules:** Short, low-friction. Captured during shutdown when context is fresh. Surfaces in the next morning ritual.
- **Moat demonstration:** Bridges the overnight gap that kills context.

### 6. The Earned Win
- **What:** When N.O.V.A. detects meaningful progress — a feature shipped, a long session completed, a goal met — it marks the moment. "Clean work." Not constant encouragement.
- **Why it matters:** Strategic praise, rare enough to mean something.
- **Design rules:** Praise must be earned and rare. Never constant encouragement. Must feel like N.O.V.A. actually tracked the work, not just guessed.

### 7. The Trust Reversal (Bonus)
- **What:** "Undo that." → "Reverted." Safe autonomy demonstrated.
- **Why it matters:** Makes automation safe. If the user can always undo, they can trust N.O.V.A. to act.
- **Design rules:** Every automated action must be reversible or explicitly flagged as non-reversible before execution.

---

## 7. Technical Constraints and Open Questions

### Resolved Constraints
- **Hardware baseline:** 16GB RAM Windows 11 PC. ~2GB runtime baseline. Must run on mid-range laptop, not just high-end.
- **Setup time:** Under 15 minutes including guided first-run experience. Must minimize friction around Python environment and API key configuration.
- **API cost:** Claude API with prompt caching, targeting ~$0.50–2.25/month. Must preserve path toward local/hybrid operation.
- **Architecture:** Modular monolith, single Python process, asyncio event loop, ports-and-adapters. Every subsystem defines a port (abstract interface) and adapters (concrete implementations).
- **Concurrency:** asyncio event loop as coordinator. CPU-heavy work (STT, TTS, embeddings) offloaded via ProcessPoolExecutor.
- **Context awareness:** win32gui.GetForegroundWindow() + title parsing. Poll every 500ms–1s. VS Code titles reveal current file + project.
- **Automation safety:** v0.1 ships safe-only actions (launch, focus, arrange). All "careful" actions require confirmation. Action registry (YAML/JSON), confirmation gate, audit log, kill switch (global hotkey).
- **Failure/fallback:** Every subsystem exposes a health() method. Graceful degradation: STT fails → text input, wake word weak → push-to-talk, cloud LLM down → queue + retry, context unavailable → manual mode.

### Open Questions for PRD Resolution

| Question | Context | Options |
|----------|---------|---------|
| **openWakeWord vs Porcupine** | Build Kit chose Porcupine (commercial quality, free tier). Technical research recommends openWakeWord (fully open source, no API limits, train custom "Hey Nova"). | Recommend openWakeWord for v0.2 — aligns with open-core philosophy. Test both. |
| **sqlite-vec vs ChromaDB vs LanceDB for v0.2 memory** | sqlite-vec carries v0.1. Three options for scale path: ChromaDB (quick start, higher RAM), LanceDB (disk-speed, multimodal), sqlite-vec continued (single-file simplicity). | Technical research recommends LanceDB. Build Kit says ChromaDB. Domain research says Mem0+ChromaDB. |
| **Piper vs Edge TTS vs Kokoro for v0.2** | Build Kit says Edge TTS (zero cost). Technical research says Piper (local, MIT, ~80ms latency). Kokoro (~4.0 MOS, near real-time) as upgrade. | Recommend Piper for v0.2 (local-first alignment), Kokoro as upgrade path. |
| **Workspace mode configuration UX** | Modes must be configurable per-user. No default mode will match every user's apps. How does the user define their modes? Setup wizard? YAML config? Interactive first-run? | PRD must specify the mode configuration experience. |
| **Cold-start onboarding flow** | Day 1, memory is empty. The "it remembered where I left off" hook cannot fire. What does the first-session experience look like? | PRD must design a first-session that delivers immediate value (workspace setup, context awareness, sharp interaction, rituals) and primes the memory system so session #2 is already warm. |
| **Personality prompt engineering** | The personality bible is detailed (sharp, loyal, witty, bluntness levels, earned familiarity, strategic praise). How is this implemented in Claude API system prompts? How much prompt budget does it consume? | Build Kit allocates 2–3 days for dedicated personality prompt engineering. PRD should specify personality as a testable requirement. |
| **Offline behavior** | What happens when Claude API is unreachable? N.O.V.A. can still do local operations (launch apps, restore workspace, read memory) but cannot reason or generate responses. | PRD must define three explicit capability tiers: **Full** (cloud API reachable, all features available), **Degraded** (API intermittent or rate-limited — queue requests, use cached responses, surface warnings), **Offline-local-only** (no API — workspace restore, mode switching, memory reads, app launching all work; no reasoning, no briefing generation, no new memory synthesis). These tiers make requirements and test cases cleaner. |

### Fragile Windows Dependencies
- **pywinauto:** Works well for standard Win32 apps. Fragile for modern Electron/WPF apps (incomplete automation trees). Test per-app.
- **win32gui:** Stable API but elevated-process windows cannot be read without UAC. School/enterprise-managed machines may block certain calls.
- **Notification suppression:** Requires Focus Assist or Windows-level API access that may need admin rights. Consider deferring aggressive notification management to v0.15.
- **App launching:** subprocess/ShellExecute is reliable. Restoring specific app state (VS Code project, specific tabs) is per-app fragile work.

---

## 8. PRD Writer Guidance

### What the PRD Must Preserve
- **The 2-hour work-window constraint is the primary design filter.** Every feature must answer: "Does this make 7–9 PM more productive, or does it consume from it?"
- **The behavioral doctrine:** "Do not be noisy. Do not be passive. Be useful at the right moment." This is the north star for all feature prioritization.
- **Personality is a first-class requirement**, not a cosmetic layer. The PRD must specify personality traits, bluntness levels, and behavioral rules as testable acceptance criteria.
- **The 5 persona stress test filters** apply to every feature: (1) Natural at 6 AM half asleep, (2) Not annoying after 6 months, (3) Still feels like N.O.V.A., (4) Works in a 2-hour window, (5) Buildable by a solo dev.
- **The compound effect narrative:** Day 1 impressive, Day 365 irreplaceable. The PRD should describe the value arc explicitly.

### What Must Not Be Watered Down
- **Local-first is not "local-optional."** Personal memory and workspace data are stored locally by default. Cloud reasoning may be used in MVP, with a clear path toward hybrid and local inference. Core memory and personal data must never leave the device.
- **Transparency command is not a settings page.** It is a first-class conversational interface that shows everything N.O.V.A. knows in real time.
- **Trust model is not a checkbox.** It is a behavioral system: safe actions execute, sensitive actions confirm, user can always undo, user can always inspect.
- **"Desktop companion for builders" is the positioning.** Not "AI assistant." Not "productivity tool." Not "Workspace OS" (yet). The sharper wedge: "N.O.V.A. is not the best chatbot for your desktop; it is the first system designed to preserve continuity of work across days." The language matters.
- **The signature moments are the product.** If the morning ritual, context resume, transparency moment, and tomorrow seed don't feel great, nothing else matters.

### Cold-Start Framing
- **The one-sentence story:** Day one value comes from workspace setup, awareness, and rituals; day two value comes from compounding memory and context continuity.
- **Day 1:** N.O.V.A. delivers value through workspace setup, context awareness, morning briefing structure, sharp interaction, and shutdown flow with tomorrow seed. Memory is empty but the experience is already useful.
- **Day 2+:** Session memory and context resume begin to compound. Each session makes the next one warmer. By day 30, N.O.V.A. knows your project rhythms and pre-loads modes. By day 365, it is irreplaceable.
- **The PRD must design the first-run experience as a standalone product moment**, not a hollow shell waiting for data to accumulate.
- **The hero loop must be visible in the PRD:** Open laptop → see briefing → enter mode → restore context → work → shutdown with tomorrow seed.

### Measurable Success Criteria
- Workspace restore completes in under 30 seconds
- Guided setup completes in under 15 minutes (including API key configuration)
- Stable operation on a mid-range 16GB RAM Windows 11 laptop
- Claude API cost under $2.50/month with prompt caching at 50 turns/day
- Morning briefing surfaces relevant context from prior sessions by session #3
- Users can inspect all stored memory via transparency command
- All automated actions are logged in audit trail
- Every "careful" tier action requires explicit confirmation before execution

### Build Feasibility Notes (from review panel)
- **Feasibility verdict: STRETCH.** Core loop is buildable in 8–12 weeks, but the full v0.1 scope bundles several independent hard subsystems. One bad week can collapse the schedule.
- **Hardest part:** Context restoration. Capturing what was open is straightforward. Re-opening apps in the right state requires per-app logic.
- **Recommended scope cut if time runs short:** (1) Cut focus protection to v0.15, (2) Ship safe-only actions (no tiered trust model complexity), (3) Cut per-app context depth — ship window list restore only.
- **Day 1 spike needed:** Windows API integration should be validated in week 1 before any other work starts.
- **Setup friction risk:** Python environment on Windows (PATH issues, pywin32 post-install scripts). Consider uv for package management and a setup.bat or installer script.

---

## 9. Non-Negotiables

These principles are load-bearing. The PRD must respect all of them. If a feature conflicts with any of these, the feature loses.

1. **Local-first trust.** Memory, context, and all personal data stay on the user's machine. No cloud storage of personal data. No telemetry on work. No exceptions.

2. **Desktop-native context awareness.** N.O.V.A. knows what is happening on the desktop — which apps are open, which project is active, what mode the user is in. This is not screen recording. It is lightweight, real-time, API-level awareness.

3. **Compounding memory.** Every session makes the next one better. The system accumulates knowledge of projects, preferences, patterns, and decisions. Day 1 impressive. Day 365 irreplaceable.

4. **Focus protection.** N.O.V.A. actively protects the user's limited work window. It does not just block — it understands context and intervenes at the right moment. The doctrine: do not be noisy, do not be passive, be useful at the right moment.

5. **Ritual continuity.** Morning briefing, shutdown flow, tomorrow seed. These rituals create continuity across sessions and across days. They are not reminders — they are ceremonies that anchor the user's rhythm.

6. **Transparency and user control.** The user can always ask "What do you know right now?" and get a complete, honest answer. The user can always ask N.O.V.A. to forget. The user can always undo. There is no hidden state.

7. **Terminal-first MVP, voice layered after core loop is proven.** The product vision is voice-first. The first implementation is terminal-first. Voice is the near-term evolution, not the MVP requirement. This gives the right sequencing without weakening the long-term vision.
