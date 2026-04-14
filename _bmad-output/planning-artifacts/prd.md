---
stepsCompleted:
  - "step-01-init"
  - "step-02-discovery"
  - "step-02b-vision"
  - "step-02c-executive-summary"
  - "step-03-success"
  - "step-04-journeys"
  - "step-05-domain"
  - "step-06-innovation"
  - "step-07-project-type"
  - "step-08-scoping"
  - "step-09-functional"
  - "step-10-nonfunctional"
  - "step-11-polish"
  - "step-12-complete"
inputDocuments:
  - "product-brief-nova.md"
  - "product-brief-nova-distillate.md"
  - "research/market-nova-desktop-ai-assistant-research-2026-04-13.md"
  - "research/domain-local-first-personal-ai-agents-research-2026-04-13.md"
  - "research/technical-local-first-windows-ai-assistant-stack-research-2026-04-13.md"
documentCounts:
  briefs: 2
  research: 3
  brainstorming: 0
  projectDocs: 0
classification:
  projectType: "Desktop-native AI companion"
  domain: "Developer productivity / personal AI"
  domainSecondaryConcerns:
    - "Privacy-first software design"
    - "Desktop automation safety"
    - "Voice interaction systems"
  complexity: "High"
  projectContext: "Greenfield"
  category: "Emerging product category — desktop-native context and focus companion for builders"
workflowType: 'prd'
---

# Product Requirements Document - N.O.V.A.

**Author:** Sayuj
**Date:** 2026-04-13

## Executive Summary

N.O.V.A. is a desktop-native AI companion for Windows 11 that eliminates the daily tax of re-entering your own work. Solo developers and project-heavy students working in fragmented evening sessions lose 15–30 minutes per session — 12–25% of a two-hour work window — just reconstructing where they left off. Existing AI tools are stateless, browser-bound, and passive. They help inside a moment but abandon the user between moments. No product preserves continuity across sessions, days, and modes of work.

N.O.V.A. solves this with a persistent, context-aware layer that sits between the user and their Windows desktop. The core loop: open laptop → see session briefing → enter workspace mode → restore context → work → shutdown with tomorrow seed. The system captures workspace state, maintains local memory (SQLite, on-device only), orchestrates workspace modes (coding, study, shutdown), protects focus, and anchors each day with rituals that bridge sessions. Personal data never leaves the machine. Cloud reasoning (Claude API) handles inference for the terminal-first MVP, with a clear path toward hybrid and local LLM operation. The long-term product is voice-first; the first implementation is terminal-first so the core loop can be proven quickly and cleanly.

The real product is not chat — it is re-entry into meaningful work. Day one delivers value through workspace setup, context awareness, and rituals. Day two, compounding memory begins. By day thirty, N.O.V.A. knows your project rhythms. By day 365, it is irreplaceable — not through lock-in, but because the user begins to feel: "this system understands how I work."

### What Makes This Special

**The hero moment is context resume.** "You were here, this is what mattered, I've restored the workspace, and you can continue." No greeting, no chat response — a system that restores where you were, what mattered, and what comes next, so the session starts warm instead of cold. No existing product combines persistent memory, workspace orchestration, and desktop awareness to deliver this.

**Continuity is more valuable than novelty.** Better automation can be copied. Better integrations can be matched. N.O.V.A. compounds context, rituals, and trust over time until it feels less like a tool and more like a personal working memory layer. The moat grows with every session — not through vendor lock-in, but through accumulated value the user does not want to lose.

**Local-first is a trust contract, not a feature.** In a market reeling from the Recall privacy backlash and Rewind's forced migration to Meta's terms, N.O.V.A. stores all memory and workspace data on the user's machine. The transparency command — "What do you know right now?" — is a first-class interface, not a settings page. Every automated action is logged and inspectable; reversible where supported, with confirmation for sensitive actions. Trust is earned through behavior, not claimed in marketing.

**The market gap is real.** The "voice-first + memory + local desktop agent" quadrant is virtually empty. Rewind was acquired and killed. Screenpipe records but does not orchestrate. OpenClaw has security risks and no focus model. Microsoft Copilot is cloud-first and generic. Windows developers are underserved — the local AI desktop space is overwhelmingly Mac-first. N.O.V.A. occupies an open position with no category leader.

## Project Classification

| Attribute | Value |
|-----------|-------|
| **Project Type** | Desktop-native AI companion (CLI-first MVP → native GUI via Tauri 2.0) |
| **Domain** | Developer productivity / personal AI |
| **Secondary Concerns** | Privacy-first software design, desktop automation safety, voice interaction systems |
| **Complexity** | High — new interaction model, persistent memory, desktop integration, safety/trust requirements, local-first constraints, personality as product requirement, regulatory implications |
| **Project Context** | Greenfield |
| **Category** | Emerging product category — desktop-native context and focus companion for builders |

## Success Criteria

### User Success

N.O.V.A. succeeds when users consistently experience these outcomes:

- **"It remembered exactly where I left off."** Context restoration works reliably and saves real time every session. The workspace resumes warm, not cold.
- **"It got me into work mode instantly."** Workspace modes eliminate manual setup. One command switches the environment and the user is working within seconds.
- **"It actually helped me protect my focus."** Users notice fewer unproductive context switches during their work window. Focus protection is useful without being annoying.
- **"I trust it because I can see what it knows and control it."** The transparency command works. Memory is inspectable. Users feel in control of their data and N.O.V.A.'s behavior.

**The day 30 test:** N.O.V.A. is no longer generic. It restores the right workspace modes for the user's typical sessions. Briefings reflect actual patterns. Context resume feels sharper because it remembers what tends to matter. The proof is not "it knows facts about me" — the proof is it helps the user get back into meaningful work with less friction than they could on their own.

### Business Success

N.O.V.A. is a solo-built product. Success is measured by personal pull first, external validation second.

**3-month success (personal validation):**
- The builder uses N.O.V.A. in their own workflow multiple times per week, ideally daily
- It reliably saves time at the start of work sessions
- Evening work windows feel less fragmented
- Trust in memory and transparency is high enough for frictionless continued use
- Signal: "I reach for it naturally, not because I built it"

**12-month success (product validation):**
- N.O.V.A. is something the builder would genuinely miss if it disappeared — a personal working memory layer, not a tool occasionally tested
- A small but highly engaged early user community exists
- Repeat usage by target users: builders, students, privacy-first desktop users
- Organic word-of-mouth driven by demoable moments (context resume, session briefing, transparency command)
- GitHub traction and community interest as supporting evidence, not primary proof

**Core business metric: retention.** The moat is compounding memory. If users do not return, the product never compounds value. The key question: *Do users who complete 5 meaningful sessions continue using N.O.V.A. regularly afterward?* Operational target to be defined: e.g., users who complete 5+ meaningful sessions return at least 3 times in the following 7 days.

### Technical Success

- Workspace restore completes in under 30 seconds
- Guided first-run setup completes in under 15 minutes (including API key configuration)
- Stable operation on a mid-range 16GB RAM Windows 11 laptop
- Claude API cost under $2.50/month with prompt caching at 50 turns/day
- Session briefing surfaces relevant context from prior sessions by session #3
- All stored memory inspectable via transparency command
- All automated actions logged in audit trail
- Every "careful" tier action requires explicit confirmation before execution
- Graceful degradation: local workspace restore, mode switching, and memory reads remain available when cloud reasoning is unavailable

### Measurable Outcomes

| Outcome | Target | Timeframe |
|---------|--------|-----------|
| Context restore time | < 30 seconds | v0.1 launch |
| First-run setup time | < 15 minutes | v0.1 launch |
| API cost per user/month | < $2.50 (50 turns/day, prompt caching) | v0.1 launch |
| Briefing relevance | Surfaces prior session context by session #3 | v0.1 launch |
| Personal daily use | Multiple times per week, ideally daily | 3 months post-build |
| Retention signal | Users with 5+ sessions continue regular use (operational definition TBD) | 6 months post-launch |
| Day 30 compounding proof | Workspace and briefing reflect user patterns | 30 days of use |
| "Would miss it" threshold | Builder would genuinely miss N.O.V.A. if removed | 12 months |

## User Journeys

### Journey 1: Solo Builder — First Session

**Meet Sayuj.** College student, solo dev, ships side projects in evening windows between gym and coursework. He's been using Claude Code in VS Code but every session starts cold — reopening files, retracing decisions, remembering what he was trying to solve. He heard about N.O.V.A. on r/LocalLLaMA and cloned the repo.

**Opening Scene:** It's 7:15 PM. Sayuj runs the setup script. N.O.V.A. walks him through API key configuration, scans his desktop, and asks a few questions: "What apps do you usually have open when you're coding? What about when you're studying?" He names VS Code, Chrome, Spotify for coding mode. Notion, Chrome, Spotify for study mode. Setup takes 8 minutes.

**Rising Action:** N.O.V.A. captures a snapshot of his current workspace — what's open, what's focused, what project is active in VS Code. It creates his first workspace mode profiles. Then it says: "I don't know your patterns yet, but I can already help. Want me to set up coding mode?" He says yes. N.O.V.A. launches VS Code with his current project, opens Chrome, starts Spotify. It's not magic — it's setup he could have done manually. But it happened in one command. N.O.V.A. adds: "You can ask me what I know at any time."

**Climax:** At 9:00 PM, Sayuj wraps up. N.O.V.A. runs the shutdown flow: "What were you working on? What should you pick up tomorrow?" He types: "Got the auth middleware working, need to write tests next." N.O.V.A. stores the tomorrow seed. The session is over, but for the first time, tomorrow's session has a warm start waiting.

**Resolution:** Day one value is real but modest — workspace setup, one-command mode switching, and a shutdown ritual that plants a seed. Memory is empty, but the tomorrow seed means session #2 won't start cold. The hook isn't "it knows me." The hook is: "it already made my evening window feel less wasteful."

### Journey 2: Solo Builder — Daily Return (Day 1 → Day 7 → Day 30)

**Day 1 return.** It's 7:10 PM the next evening. Sayuj opens his terminal. N.O.V.A. presents the session briefing: "Yesterday you got the auth middleware working. You said you'd write tests next. Coding mode?" He says yes. N.O.V.A. restores the workspace — VS Code opens, Chrome opens, Spotify starts. He glances at the briefing and he's working within 2 minutes instead of 15. The tomorrow seed worked.

**Day 7.** The briefing is richer now. N.O.V.A. knows his recent project, knows he's been in coding mode every evening this week, and remembers the arc of work: auth middleware → tests → API routes. The briefing connects the dots: "You finished auth tests on Tuesday. You started API routes yesterday and left off at the GET endpoint. Resume coding mode?" Context resume feels sharper because it's not just restoring apps — it's restoring the thread of work.

**Day 30.** N.O.V.A. has 30 sessions of accumulated context. The briefing doesn't just say what happened yesterday — it reflects patterns. It knows Tuesday and Thursday evenings are usually coding, weekends are sometimes study mode. It pre-suggests the right mode before being asked. Context resume includes not just what was open, but what tends to matter in this project. When Sayuj asks "What do you know right now?", N.O.V.A. shows a month of accumulated working memory — projects, decisions, patterns, rhythms. He realizes that with any other tool, he would have to reconstruct this thread himself.

**Resolution:** The progression is the product. Day 1 is useful. Day 7 is noticeably better. Day 30 is where N.O.V.A. crosses from "tool I use" to "system I rely on." The moat is not a feature — it's 30 days of compounded continuity that the user doesn't want to lose.

### Journey 3: Student — Mode Switching

**Meet Priya.** Second-year CS student, 21, juggling a data structures assignment due Thursday, a React side project she's building for her portfolio, and a study group session she needs to prep for. She has from 6:30 PM to 9:30 PM tonight — three hours, three different kinds of work. She installed N.O.V.A. last week after seeing a demo of the context resume on GitHub.

**Opening Scene:** It's 6:35 PM. Priya opens N.O.V.A. The session briefing shows: "Last session you were in study mode working on the data structures assignment. You left off on the AVL tree implementation. You also have coding mode configured for your React project." She needs to finish the AVL tree work first, then switch to the React project, then prep notes for the study group. Three modes, one evening.

**Rising Action:** She says "study mode." N.O.V.A. opens Notion with her DS notes, Chrome with the assignment spec, VS Code with her Java project. Spotify starts her focus playlist. She works for 50 minutes and finishes the AVL implementation. Then: "switch to coding mode." N.O.V.A. bookmarks her study session state — what was open, where she left off, what she accomplished — and pivots the workspace. VS Code switches to the React project. Chrome opens her component library docs. The study mode bookmark means she can return to exactly this point if needed. The environment reshapes in seconds, not the 5–10 minutes of manual closing, opening, finding, and remembering.

**Climax:** At 8:40 PM she realizes she needs to prep for the study group. She hasn't configured a "study group" mode yet. She tells N.O.V.A. what she needs open — Notion for shared notes, Chrome for the course slides, Discord for the group chat. N.O.V.A. creates the mode on the fly and sets it up. The product adapts to her, not the other way around.

**Resolution:** Three hours, three completely different work contexts, zero time lost to manual reconstruction between them. The value isn't that N.O.V.A. is smart — it's that each switch preserved the previous state and eliminated the friction of the next one. Her 3-hour window felt like 3 hours of work, not 2 hours of work and 1 hour of setup. At shutdown, N.O.V.A. captures all three threads with their bookmarks: "AVL tree done. React project: left off on the navbar component. Study group prep: reviewed slides 1–12." Tomorrow, all three threads are warm.

### Journey 4: Privacy-First Power User — Trust & Transparency

**Meet Daniel.** 32, backend developer at a mid-size company, builds open-source tools on evenings and weekends. He self-hosts everything — Nextcloud for files, Gitea for repos, Pi-hole for DNS. He tried Copilot and dropped it over telemetry concerns. He read about N.O.V.A. on Hacker News and was skeptical but interested. The local-first claim is what got him to try it. The transparency command is what will determine whether he stays.

**Opening Scene:** Daniel installs N.O.V.A. and immediately checks the data directory. SQLite file, local, no network calls except Claude API for reasoning. He runs through first-run setup — configures his API key, sets up coding mode with his preferred layout (two VS Code windows, a terminal, Firefox with docs). N.O.V.A. tells him: "You can ask me what I know at any time."

**Rising Action:** He uses N.O.V.A. for a week. It learns his project structure, his preferred mode, his working patterns. On day 8, he runs the transparency command: "What do you know right now?" N.O.V.A. surfaces everything — his workspace modes, his project history, the patterns it's detected, the tomorrow seeds he's planted. Every piece of stored knowledge is visible, structured, and deletable. No hidden state. No inference he can't inspect.

**Climax:** Daniel finds something he doesn't want stored — N.O.V.A. remembered the name of a client project from a window title during a work session. He says: "Forget everything about Meridian." N.O.V.A. confirms what it will delete, shows him the specific memory entries, and removes them. He checks the SQLite file directly to verify. It's gone. The "forget" command isn't a soft delete or a UI trick — it's real deletion, and he can prove it because the database is a local file he owns.

**Resolution:** Daniel doesn't trust N.O.V.A. because it promised privacy. He trusts it because he verified privacy — inspected the data, tested the forget command, confirmed the network behavior. The transparency command isn't a feature he uses once. It becomes part of his routine — a periodic check that reinforces trust. After three weeks, he recommends N.O.V.A. on r/selfhosted. His endorsement: "It's the first AI tool where I can actually verify every claim it makes about my data."

### Journey 5: Any User — Trust Under Failure

**Meet anyone using N.O.V.A. on a Tuesday evening when things go wrong.**

**Opening Scene:** It's 7:20 PM. The user opens N.O.V.A. and asks to resume their session. But the Claude API is returning 503 errors — Anthropic is experiencing an outage. N.O.V.A. cannot generate a session briefing, cannot synthesize context, cannot produce natural language responses. The reasoning layer is gone.

**Rising Action:** N.O.V.A. does not crash. It does not show a generic error page. It tells the user exactly what happened: "Cloud reasoning is currently unavailable. Local capabilities remain active — I can restore your workspace, switch modes, read your stored memory, and launch apps. Briefing generation, conversational responses, and new memory synthesis are paused until the connection is restored." Then it does what it can: restores the workspace mode from last session, launches the right apps, shows the raw tomorrow seed from yesterday. The local systems — memory reads, workspace orchestration, mode switching, safe app launching — continue to function because they never depended on the cloud.

**Climax:** Midway through the session, the user tries to launch a "careful" tier action — rearranging windows into a layout that requires confirmation. N.O.V.A. cannot reason about whether this is safe without the LLM. Instead of guessing or silently failing, it says: "I can't evaluate this action without cloud reasoning. Would you like to proceed manually, or wait until the connection is restored?" The user proceeds manually. N.O.V.A. logs the event and moves on.

**Resolution:** The API comes back 40 minutes later. N.O.V.A. acknowledges the restoration: "Cloud reasoning is back. I've logged what happened during the outage. Would you like a briefing catch-up?" The user says yes. N.O.V.A. synthesizes what it observed locally — which apps were used, how long the session ran, what mode was active — and produces a partial briefing. The session wasn't perfect, but trust was maintained because N.O.V.A. was honest about what it could and couldn't do, never pretended, and never failed silently. The principle: *when something breaks, the user should trust N.O.V.A. more, not less, because of how it handled the failure.*

### Journey Requirements Summary

| Journey | Primary Capabilities Revealed |
|---------|-------------------------------|
| **First Session** | Guided setup, workspace capture, mode creation, shutdown flow, tomorrow seed, transparency seeding |
| **Daily Return (Day 1→30)** | Session briefing, context resume, memory accumulation, pattern detection, compounding value arc |
| **Mode Switching** | Multi-mode management, state preservation and bookmarking across switches, on-the-fly mode creation, multi-thread shutdown capture |
| **Trust & Transparency** | Transparency command, memory inspection, selective forgetting, data verification, local-first proof |
| **Trust Under Failure** | Graceful degradation tiers (full / degraded / offline-local-only), honest failure communication, local-only operation, outage logging, recovery briefing |

## Domain-Specific Requirements

### 1. Cloud Prompt Data Minimization

N.O.V.A. uses Claude API for reasoning, but local-first trust is weakened if raw context is routinely sent upstream. The PRD defines a clear boundary between local-only data and cloud-eligible derived context.

**Cloud prompt rules:**
- Send summaries and derived context, not raw local memory
- Send only the minimum context required for the current reasoning task
- Never send full local memory stores or raw audit logs
- Never send raw audio
- Never send sensitive window/app data from excluded categories (see §3) unless the user explicitly allows it
- Prefer local preprocessing and redaction before cloud reasoning
- N.O.V.A. must maintain a clear internal distinction between **local-only data** and **cloud-eligible derived context**

**Trust-preserving failure rule:** If N.O.V.A. cannot safely minimize, classify, or protect a piece of context, it must not send it to cloud reasoning. It must fall back to local-only behavior or ask the user before proceeding.

### 2. Voice Data Lifecycle (v0.2)

"Ephemeral" is not sufficient for a PRD. The voice data lifecycle must be testable.

**Voice data rules:**
- Audio buffers exist in memory only during active capture and transcription
- No audio is persisted to disk by default
- Audio buffers are deleted immediately after transcription completes or fails
- No voiceprint extraction
- No speaker identification
- No biometric inference of any kind (prohibited under EU AI Act Article 5(1)(f))
- No training or fine-tuning on user audio
- Optional debug recording, if ever implemented, must be: opt-in, time-bounded, visibly enabled while active, and automatically purged after the debug window closes

### 3. Sensitive-Context Exclusion Boundaries

Because N.O.V.A. reads app and window context via Windows APIs, the PRD defines a capture boundary for sensitive environments.

**Excluded-by-default app categories:**
- Password managers (e.g., 1Password, Bitwarden, KeePass)
- Banking and financial applications
- Health portals and medical applications
- Private/incognito browser windows (where detectable)
- Secure corporate applications (user-flagged)

**Exclusion behavior:**
- Excluded contexts are not stored in memory
- Excluded contexts are not sent to cloud reasoning
- Excluded contexts are not used for pattern detection or insight extraction
- Excluded contexts are omitted from session briefings and transparency summaries except as generic opaque placeholders (e.g., "A protected app was active" — not the app name or title)
- When an excluded app is in the foreground, N.O.V.A. treats the context as opaque — it knows *an app* is focused but captures no identifying details
- Users can inspect and modify the exclusion list at any time
- The exclusion list is stored locally and ships with sensible defaults

### 4. Deletion Propagation

Selective forgetting must apply to all stored and derived representations, not just the original memory entry.

**When a user requests deletion (e.g., "Forget Meridian"), N.O.V.A. must remove the target from:**
- Raw memory entries
- Summaries and synthesized context that reference the target
- Embeddings and vector entries associated with the target
- Bookmarks and tomorrow seeds that reference the target
- Any persisted cache or stored derived context that references the target (not ephemeral in-memory token buffers already discarded)

**Audit trail behavior:**
- The deletion event itself is logged (what was deleted, when, by user request)
- The deleted content is not preserved in the audit trail
- The audit log records the action, not the data

### Regulatory Summary

| Regulation | Applicability | N.O.V.A. Obligation | Risk Level |
|------------|--------------|---------------------|------------|
| **EU AI Act** (Aug 2026) | Limited risk — transparency required | Disclose AI interaction; no emotion detection from biometrics | Low |
| **Illinois BIPA** | Applies if voice features used | Ephemeral voice processing; no voiceprint storage | Low (with compliance) |
| **All-party consent laws** (11 US states) | Applies to audio capture | Wake-word activation mitigates; no always-on recording | Low |
| **GDPR** | Applies to EU users | Local-first architecture inherently satisfies most requirements; deletion propagation covers right-to-erasure | Low |

**Overall regulatory risk: LOW** — local-first architecture is the strongest compliance posture. Primary attention areas: voice data handling, AI transparency disclosure, and cloud prompt data minimization.

## Innovation & Novel Patterns

### Detected Innovation Areas

N.O.V.A. operates in an emerging product category with five distinct innovation patterns:

**1. Category Creation — The Empty Quadrant.** No existing product combines voice-first interaction, persistent cross-session memory, and local desktop agent capabilities into a single experience. The "voice-first + memory + local desktop agent" quadrant is virtually empty. Rewind was acquired and killed. Screenpipe records but does not orchestrate. OpenClaw has security risks and no focus model. This is a positioning opportunity, not just a feature set. *Risk: Low (positioning can evolve if the core loop is strong).*

**2. Compounding Memory as Moat.** Most AI products compete on model quality — a commodity that improves for everyone simultaneously. N.O.V.A. competes on accumulated personal context that grows with every session. The moat is not a feature; it is 30 days of compounded continuity that the user doesn't want to lose. No competitor can replicate a user's personal memory store. *Risk: Highest. The moat only forms if users return often enough for memory to compound and if recalled context is materially helpful.*

**3. Ritual-Driven Continuity.** Session briefing → work → shutdown with tomorrow seed is a behavioral design pattern, not a feature list. The ritual creates the bridge between sessions that makes compounding memory actually work. Without rituals, memory accumulates but has no delivery mechanism. *Risk: High. Rituals can become sticky habits or feel like unnecessary ceremony. Must survive the "would this annoy me after 6 months?" test.*

**4. Trust-Through-Transparency as UX Pattern.** The "What do you know right now?" command, selective forgetting with full deletion propagation, and trust-under-failure design are novel interaction patterns for desktop AI. No existing product treats transparency as a first-class conversational interface. *Risk: Moderate. Users may not value it until something goes wrong — but when they need it, it must work perfectly.*

**5. Continuity Over Novelty as Design Philosophy.** N.O.V.A. challenges the prevailing AI product assumption that smarter responses equal a better product. The core thesis: remembering across sessions matters more than being smarter within one session. The real product is not chat — it is re-entry into meaningful work. *Risk: Low-moderate. This is a design philosophy, not a feature to validate independently.*

### Validation Approach

**Core validation principle:** Treat compounding memory as earned, not assumed. The sequence is: prove utility → prove repeat usage → prove compounding. Do not over-invest in rich memory infrastructure until simpler prerequisites are met.

**Stage 1 — Prove Day-1 Value Without Memory**

The first session must be useful when memory is empty. N.O.V.A. delivers value through workspace setup, one-command mode switching, context awareness, and shutdown with tomorrow seed. Success signal: the user reaches real work faster than usual, and shutdown captures enough context to make tomorrow easier. *If day 1 has no pull, compounding will never matter.*

**Stage 2 — Prove Session-2 Warmth**

The first real memory test. Does the second session start warmer than the first? Measure: time from open to productive state, whether the user accepts the suggested resume, whether the tomorrow seed is useful, whether the user confirms "yes, that's where I was." *This is the first non-theoretical proof of the thesis.*

**Stage 3 — Prove Week-2 Habit Formation**

After 5 meaningful sessions, does the user start reaching for N.O.V.A. naturally? Track: return frequency after the fifth session, number of context resume invocations, shutdown/tomorrow seed usage rate, whether work-start friction is falling over time. *This is the early retention checkpoint that matches the moat logic.*

**Stage 4 — Prove Day-30 Personalization**

Does N.O.V.A. now help the user re-enter work faster than they could on their own? The proof is not "it knows facts" — it is better mode suggestions, more relevant briefings, sharper context resume, less manual reconstruction, and visible pattern recognition that feels helpful, not creepy.

### Validation Metrics

| Category | Metric | Target |
|----------|--------|--------|
| **Activation** | Setup completed | >90% successful guided setup on supported Windows 11 machines |
| **Activation** | First workspace mode created | Within first session |
| **Activation** | First shutdown/tomorrow seed completed | Within first session |
| **Early Value** | Median time to productive workspace | Measurably faster than manual setup |
| **Early Value** | Session-2 resume acceptance rate (among users who completed shutdown/seed in session 1) | >80% |
| **Early Value** | Sessions ending with a saved seed | >70% |
| **Compounding** | After 5 sessions, returns 3+ times in following week | Target to be validated |
| **Compounding** | Time-to-start improvement from session 1 to session 7 | Measurable reduction |
| **Compounding** | Briefing/resume acceptance without heavy editing | Increasing over time |
| **Subjective** | "Did this help you get back into work faster?" (asked at session 2, 7, 30) | Consistently "yes" |

### Risk Mitigation

**Compounding memory risk mitigation:**
- Do not overbuild semantic memory, complex embedding pipelines, deep pattern inference, or heavy "AI insight" features until three simpler things are proven: users complete shutdown with tomorrow seed, users return and use resume, users perceive session restarts as meaningfully easier
- If those do not happen, richer memory will not save the product
- The real MVP question is not "Can N.O.V.A. remember a lot?" — it is "Can N.O.V.A. make tomorrow's session easier than today's?"

**Ritual risk mitigation:**
- Rituals must be low-friction and self-trimming (rituals that lose relevance fade)
- Apply the "6 months" stress test: would this still feel natural, not annoying?
- Shutdown flow must capture value in under 30 seconds — if it feels like homework, users will skip it

**Trust-through-transparency risk mitigation:**
- Transparency command must work perfectly on first use — trust is binary at this stage
- Selective forgetting must be verifiable (user can check the SQLite file directly)
- Trust under failure must be tested explicitly — the degraded-state journey is a testable scenario

**Category creation risk mitigation:**
- Category language ("desktop companion for builders") can evolve based on how early users actually describe the product
- Do not over-invest in positioning before the core loop is validated
- Let demoable moments (context resume, session briefing) do the positioning work

## Desktop-Native AI Companion — Technical Requirements

### Platform Support

**v0.1:** Windows 11 only. No cross-platform commitment in MVP. Architecture should use ports-and-adapters so platform-specific code (win32gui, pywinauto, psutil) is isolated behind interfaces, making macOS/Linux possible later without rewriting core logic. No PRD energy spent optimizing for other platforms now.

### Offline Capability Tiers

N.O.V.A. defines three explicit capability tiers based on cloud API availability:

| Tier | Condition | Available Capabilities | Unavailable |
|------|-----------|----------------------|-------------|
| **Full** | Cloud API reachable | Local memory, context awareness, workspace restore, mode switching, cloud reasoning, briefing generation, conversational synthesis | — |
| **Degraded** | API intermittent or rate-limited | Local memory reads, workspace restore, mode switching, raw tomorrow seed / raw session notes, previously stored briefings/notes shown verbatim, limited non-LLM responses, queue + retry for cloud requests | New synthesized briefings, conversational synthesis, new memory synthesis |
| **Offline-local-only** | No API connectivity | Safe local actions, mode switching, session save/resume, transparency over stored local data, memory reads | Cloud reasoning, generated briefings, conversational synthesis |

Tier transitions must be communicated honestly to the user (per Journey 5: Trust Under Failure). N.O.V.A. must never silently degrade or pretend capabilities are available when they are not.

### System Integration

v0.1 integration is deep but narrow:

- **Active window detection:** `win32gui.GetForegroundWindow()` + window title + process name via `psutil`. Poll every 500ms–1s, emit events only on change.
- **App launching and focusing:** `subprocess` / `ShellExecute` for launch, `win32gui` for focus and arrange. Reliable across standard Win32 and Electron apps.
- **Safe-only automation by default:** Launch app, focus window, arrange windows. No deep menu clicks, no field fills, no keystrokes in v0.1.
- **Careful actions behind confirmation:** Any action beyond safe tier requires explicit user approval before execution. Logged in audit trail.
- **No OCR-first design.** Window titles and API metadata are the primary context source. Screen reading is a fallback path, not the default.
- **No deep per-app state restoration in v0.1.** Workspace-level restore (which apps, which mode, session notes) — not VS Code tabs, terminal history, or cursor position. That is v0.2+ scope.

**Known fragile areas:**
- Elevated-process windows cannot be read without UAC
- Modern Electron/WPF apps may have incomplete automation trees via pywinauto
- Notification suppression may require admin rights — consider deferring aggressive notification management to v0.15

### Update Strategy

**v0.1: User-triggered updates with automatic local backup and safe schema migration.** No silent auto-update.

- Expose a command: `nova self-update` (or equivalent)
- Before any schema migration, automatically create a timestamped backup of the SQLite memory file
- Show migration notes clearly if a release changes storage shape
- Never modify local memory without the user initiating the update
- Background auto-update deferred to later GUI/system-tray versions

**Rationale:** This is an open-core, trust-first local product. Silent updates are the wrong trust posture early. SQLite-backed memory makes backup-before-migrate essential.

### Installation & Environment Management

**Primary path: One guided Windows setup script.** Do not make the MVP depend on users knowing Python packaging. Setup friction is a top adoption risk.

The setup script (PowerShell or .bat):
1. Checks for or installs `uv` (Python package/project manager)
2. Creates virtual environment / tool environment
3. Installs all dependencies
4. Runs pywin32 post-install scripts if needed
5. Prompts for Claude API key
6. Creates initial local config and data directories
7. Launches first-run setup (workspace capture and mode creation)

**Target user experience:** Clone repo → run one setup command → answer a few prompts → N.O.V.A. starts.

**Fallback path:** Raw `pip install` documented as an advanced/manual option, not the primary path.

**Success target:** Guided setup completes in under 15 minutes on a supported Windows 11 machine.

### Background Presence & Startup Model

**v0.1: Session-based only. No always-on background daemon by default.**

- N.O.V.A. runs as an active session tool, not a resident agent
- Context capture happens during active N.O.V.A. sessions only
- Shutdown flow writes the handoff (tomorrow seed, workspace state) for next session
- No system tray icon, no startup task, no continuous background capture in MVP

**Rationale:** Lower complexity, lower privacy anxiety, easier debugging, less resource overhead. Continuous capture is unnecessary before voice and Shield are mature.

**Future path (v0.2+):**
- Optional system tray / startup helper
- Optional background mode for wake word or passive context awareness
- Tied to voice interaction and richer focus protection features

### Workspace Mode Configuration UX

**v0.1: Guided interactive setup that writes to a local editable config file.**

Not just YAML (too much friction for first-time users). Not fully conversational-only (too magical and brittle for MVP).

**First-run wizard asks per mode:**
- Mode name (e.g., "coding", "study", "research")
- Apps to open
- Optional folders/projects to associate
- Optional URLs to launch
- Optional behavior flags (focus level, notification preferences)

**Starter templates:** The wizard ships with suggested starter templates (e.g., "coding" and "study") with common defaults. Users can accept as-is, modify, or skip them. This reduces setup friction and helps meet the under-15-minute setup target.

**Storage:** Local JSON or YAML config file, human-readable and editable.

**Ongoing mode management:**
- `nova mode edit coding` for conversational refinement
- Direct file editing for power users
- `nova mode create` for adding new modes on the fly (as shown in Journey 3)

**Design principle:** Easy start, inspectable config, no hidden magic. Fits the builder audience — wizard for onboarding, file-backed underneath, conversational refinement later.

### Sections Skipped (Per Project Type)

The following sections are not applicable to this product type and are intentionally omitted:
- Web SEO strategy
- Mobile-specific features and requirements

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP Approach: Minimum Viable Continuity Loop.**

N.O.V.A.'s MVP is not a minimum viable feature list — it is a minimum viable continuity loop. The product is only real when the whole sequence works end to end: setup → work session → shutdown seed → next-session resume. Individual pieces can be simple, but the loop must feel complete. The ship test is not "does it have enough features?" — it is "can N.O.V.A. make tomorrow's session easier than today's?"

**Resource Reality:** Solo developer, evening work windows. The timeline is measured in thresholds, not a single deadline.

### Development Thresholds

| Threshold | Target | What Ships | Ship Test |
|-----------|--------|-----------|-----------|
| **T1 — "Alive enough to use myself"** | ~2–4 weeks | Text/terminal only, one mode (coding), shutdown with tomorrow seed, next-session resume, SQLite memory, basic transparency command | Does session 2 start warmer than session 1? |
| **T2 — "Credible v0.1"** | ~6–8 weeks | Three modes (coding, study, shutdown), stronger restore, richer session briefing, stable guided setup, cleaner local state handling | Does a week of use feel like real continuity? |
| **T3 — "Feels complete"** | ~8–12 weeks | Full v0.1 polish, better trust behavior, personality tuning, fewer rough edges, audit trail, robust mode config UX | Would you recommend this to another builder? |

**Decision rule:** Ship the smallest threshold that passes its test. Start compounding real usage data immediately. A working loop in your own life is worth more than a polished demo that hasn't been used.

### MVP Feature Set (Phase 1 — v0.1)

**Core Continuity Loop (must work end-to-end):**
- Guided first-run setup with workspace capture and mode creation
- Workspace modes (coding, study, shutdown) with starter templates
- Session briefing on return (surfaces tomorrow seed, last mode, recent context)
- Context resume: restore last mode, open app set, surface session notes
- Shutdown flow with tomorrow seed capture
- Persistent local memory (SQLite, on-device only)
- Transparency command: "What do you know right now?"

**Supporting Capabilities:**
- Rich terminal interface (CLI-first via Rich library)
- App and window awareness via Windows APIs (win32gui, psutil)
- Safe desktop actions only (launch, focus, arrange) — no careful-tier complexity unless very stable
- Claude API for reasoning with prompt caching
- Cloud prompt data minimization (summaries, not raw memory)
- Sensitive-context exclusion with sensible defaults
- Guided Windows setup script (clone → run → answer prompts → start)
- User-triggered updates with backup-before-migrate

**Core User Journeys Supported in MVP:**
- Journey 1: First Session (setup, first mode, first tomorrow seed)
- Journey 2: Daily Return — Day 1, Day 7, and Day 30 progression (session briefing, context resume, compounding memory)
- Journey 3: Mode Switching (at least two modes with state bookmarking)
- Journey 4: Trust & Transparency (transparency command, basic memory inspection)
- Journey 5: Trust Under Failure (graceful degradation to offline-local-only tier)

### Absolute Floor — Below This, Do Not Ship

The minimum below which the product is not N.O.V.A.:
- Guided setup that creates at least one useful mode
- Shutdown with tomorrow seed
- Next-session resume that uses the seed
- Local memory in SQLite
- A basic transparency command showing what was stored from the current and prior sessions

If it cannot make tomorrow's session easier than today's, it is not ready. Below this floor, it is an assistant shell, not a continuity product.

### Scope-Cut Ladder (If Time Is Tight)

Ordered from first cut to last. Cut from the top down:

1. **Cut focus protection entirely to v0.15.** It is valuable but not the first proof of N.O.V.A. The first proof is warm re-entry into work.
2. **Simplify Hands to safe-only actions.** Launch, focus, and basic window arrange. No careful-tier confirmation complexity unless it is very stable.
3. **Reduce restore depth before reducing loop completeness.** Ship: last mode, last open app set, last session note / tomorrow seed, one command to restore the workspace shell. That is enough to make session 2 warmer.
4. **Reduce personality polish.** Ship with functional personality prompts, defer tuning and bluntness levels.
5. **Reduce mode count.** Ship with one mode (coding) if needed. Two is better. Three is the target.

**Never cut:** The continuity loop itself (shutdown seed → resume), local memory, the transparency command, or guided setup. If setup friction breaks, the continuity loop may exist technically but still fails as a product.

### Post-MVP Features

**v0.15 — Focus Protection (~Week 10–11):**
- Smart DND / focus block protection
- Simple distraction detection
- Draft mode for risky actions
- Richer save/resume context
- Social exception layer

**v0.2 — Voice & Polish (~Week 14–16):**
- Voice interaction (push-to-talk, wake word, faster-whisper STT, Piper TTS)
- TUI upgrade via Textual
- Deep per-app state restoration (VS Code tabs, terminal history)
- Earned autonomy through repetition
- Semantic memory search (sqlite-vec or LanceDB)
- Weekly insight reports
- Day scoring (transparent, optional)
- Optional system tray / background presence

**v1.0 — Full Desktop Companion:**
- Desktop GUI (Tauri 2.0)
- Local/hybrid LLM options for reasoning
- MCP integration for tool ecosystem
- Advanced automation with teach-by-observation
- Community-contributed modes and rituals
- Cross-platform expansion (only after Windows is solid)

### Risk Mitigation Strategy

**Technical Risks:**
| Risk | Impact | Mitigation |
|------|--------|------------|
| Windows API integration fragility (pywinauto, elevated processes) | Could block context awareness | Day 1 spike: validate win32gui integration in week 1 before other work |
| Setup friction (Python PATH, pywin32 post-install) | Kills adoption at the gate | Guided setup script with `uv`; test on clean Windows installs |
| Context restoration per-app variance | Different apps behave differently | Ship workspace-level restore only; defer per-app depth to v0.2 |
| Claude API dependency for core reasoning | Single point of failure for reasoning | Three capability tiers; local operations never depend on cloud |

**Market Risks:**
| Risk | Impact | Mitigation |
|------|--------|------------|
| Compounding memory thesis is wrong | Moat never forms | Staged validation: prove day-1 value → session-2 warmth → week-2 habit before investing in rich memory |
| Rituals feel like ceremony, not continuity | Users skip shutdown, seeds never plant | Shutdown must capture value in <30 seconds; self-trimming rituals that fade if unused |
| Category is too niche for traction | Small addressable audience | Start with personal use; validate before optimizing for distribution |

**Resource Risks:**
| Risk | Impact | Mitigation |
|------|--------|------------|
| Solo dev, evening windows, 8–12 week stretch | Schedule collapse from one bad week | Three thresholds instead of one deadline; ship T1 early and iterate |
| Scope creep from rich vision | Never ships | Scope-cut ladder defined; never cut the continuity loop |
| Burnout from ambitious project | Development stalls | Ship smallest viable threshold; real usage creates motivation |

## Functional Requirements

### 1. Setup & Onboarding

- **FR1:** User can install N.O.V.A. by running a single guided setup script that handles environment, dependencies, and initial configuration
- **FR2:** User can configure their Claude API key during guided setup with validation that the key works
- **FR3:** User can create workspace modes during first-run setup through an interactive wizard that asks for mode name, apps, folders, URLs, and behavior flags
- **FR4:** System provides at least one starter mode template during first-run setup (e.g., "coding", "study"), with ability to accept, modify, or skip
- **FR5:** System can capture an initial workspace snapshot (open apps, active windows, focused project) during first-run setup
- **FR6:** User can complete guided setup in under 15 minutes on a supported Windows 11 machine

### 2. Workspace Modes & Orchestration

- **FR7:** User can switch between workspace modes with a single command
- **FR8:** User can define multiple workspace modes, each with its own app set, folder/project associations, URLs, and behavior flags
- **FR9:** System can restore a workspace mode by launching configured apps, focusing the right windows, and setting the mode state
- **FR10:** System can bookmark the current mode state when the user switches to a different mode, preserving what was open and where they left off
- **FR11:** User can create new workspace modes on the fly during an active session
- **FR12:** User can edit existing workspace modes via command or by directly editing the local config file
- **FR13:** User can view all configured workspace modes and their contents

### 3. Context Awareness & Capture

- **FR14:** System can detect the active foreground window, its title, and the owning process during active sessions
- **FR15:** System can track window/app context changes and maintain a recent context buffer
- **FR16:** System can extract meaningful context from window titles (e.g., VS Code project name, browser page title, document name)
- **FR17:** System can capture workspace state on demand or during shutdown (active apps, window list, current mode, focused window)
- **FR18:** System can infer a likely workspace mode from open apps and active context, and suggest it to the user

### 4. Memory & Persistence

- **FR19:** System can store session data, user preferences, workspace states, and accumulated context in a local SQLite database on the user's machine
- **FR20:** System can accumulate knowledge across sessions — projects worked on, mode usage patterns, decisions recorded, recurring contexts
- **FR21:** System can retrieve relevant prior session context when generating briefings or responding to user queries
- **FR22:** System can detect usage patterns over time (e.g., typical mode by day of week, recurring project focus, session timing)
- **FR23:** User can back up their memory database by copying a single local file
- **FR24:** System can create automatic timestamped backups of the memory database before schema migrations

### 5. Session Rituals (Briefing & Shutdown)

- **FR25:** System can present a session briefing on return that surfaces: last session's tomorrow seed, last active mode, recent context, and relevant prior session information
- **FR26:** Session briefings can improve in relevance over time as memory accumulates (day 1 vs day 7 vs day 30 progression)
- **FR27:** User can initiate a shutdown flow that captures current state, progress summary, and a "tomorrow seed" note
- **FR28:** User can write a tomorrow seed — a short note to their future self about what to pick up next
- **FR29:** System can capture shutdown state including active mode, open apps, session notes, and the tomorrow seed
- **FR30:** System can surface the tomorrow seed from the previous session during the next session's briefing
- **FR31:** System can suppress or de-emphasize ritual elements that repeatedly go unused, without permanently deleting them unless the user chooses to

### 6. Transparency & Trust

- **FR32:** User can ask "What do you know right now?" and receive a complete, structured view of all stored knowledge — modes, project history, patterns detected, session seeds, and accumulated context
- **FR33:** User can selectively forget specific topics, projects, or data points (e.g., "Forget Meridian") with deletion propagated across all stored and derived representations
- **FR34:** User can inspect the audit trail of automated actions N.O.V.A. has taken
- **FR35:** System can communicate its current capability tier (full / degraded / offline-local-only) honestly when conditions change
- **FR36:** System can explain what it can and cannot do in the current capability tier
- **FR37:** System can recover from cloud API outages and offer a briefing catch-up when connectivity is restored
- **FR38:** User can verify the result of a forget/delete action through the transparency command immediately after deletion

### 7. Desktop Actions & Automation

- **FR39:** System can launch applications configured in workspace modes
- **FR40:** System can focus (bring to foreground) a running application window
- **FR41:** System can arrange windows in basic layouts as part of mode restoration
- **FR42:** All automated desktop actions are logged in an audit trail
- **FR43:** Any action beyond the safe tier (launch, focus, arrange) requires explicit user confirmation before execution
- **FR44:** User can invoke a context resume command that restores the last workspace mode, launches the configured app set, and surfaces session notes

### 8. Privacy & Data Protection

- **FR45:** All personal memory and workspace data is stored locally on the user's machine — never in the cloud
- **FR46:** System can distinguish between local-only data and cloud-eligible derived context, sending only minimized summaries to Claude API
- **FR47:** System can maintain a sensitive-context exclusion list (password managers, banking apps, health portals, incognito windows, user-flagged apps) with sensible defaults
- **FR48:** System can treat excluded app contexts as opaque — detecting that an app is focused without capturing identifying details
- **FR49:** Excluded contexts are omitted from memory, cloud reasoning, pattern detection, session briefings, and transparency summaries (shown only as generic opaque placeholders)
- **FR50:** User can inspect and modify the sensitive-context exclusion list
- **FR51:** When deletion is requested, system can remove the target from raw entries, summaries, embeddings, bookmarks, seeds, and persisted cached context — audit trail logs the deletion event without preserving deleted content
- **FR52:** If the system cannot safely minimize or classify a piece of context for cloud reasoning, it falls back to local-only behavior or asks the user before proceeding

### 9. Personality & Interaction

- **FR53:** System can respond with a consistent personality (sharp, loyal, witty) that follows N.O.V.A.'s behavioral doctrine
- **FR54:** System can adapt interaction style based on context — concise during work sessions, more detailed when asked for explanation
- **FR55:** System can provide honest, direct feedback about user behavior when contextually appropriate (e.g., "That's procrastination dressed as research") with user-controllable bluntness levels
- **FR56:** System can mark meaningful progress moments with strategic, earned praise — rare enough to mean something

### 10. System Management

- **FR57:** User can trigger a self-update command that checks for new versions, backs up the memory database, and applies updates with visible migration notes
- **FR58:** System can operate in three explicit capability tiers (full / degraded / offline-local-only) based on cloud API availability, with local operations never depending on cloud connectivity
- **FR59:** System can queue cloud reasoning requests during intermittent connectivity and retry when available (degraded tier)
- **FR60:** User can access previously stored briefings and session notes verbatim during degraded or offline operation

## Non-Functional Requirements

### Performance

| Requirement | Target | Context |
|------------|--------|---------|
| **NFR1:** Workspace restore (mode switch + app launch) | < 30 seconds | The hero moment. If restore feels slow, the product fails its core promise. |
| **NFR2:** Guided first-run setup | < 15 minutes end-to-end | Critical adoption gate. Users who can't set up in one sitting abandon. |
| **NFR3:** Session briefing generation | < 5 seconds after N.O.V.A. starts | Briefing must feel immediate on return, not like waiting for a loading screen. |
| **NFR4:** Shutdown flow completion | < 30 seconds of active user time | If shutdown feels like homework, users skip it and the continuity loop breaks. |
| **NFR5:** Active window context detection | < 100ms per poll cycle | Context awareness must be invisible — no perceptible lag or CPU spike from polling. |
| **NFR6:** Transparency command response | < 3 seconds | Trust requires instant access to what N.O.V.A. knows. Delay undermines the promise. |
| **NFR7:** Claude API round-trip (with prompt caching) | < 3 seconds typical for conversational responses | Interaction must feel responsive, not like waiting for a remote server. |

### Security & Privacy

- **NFR8:** All personal memory and workspace data must be stored in a local SQLite file with no network transmission of raw memory content
- **NFR9:** Claude API prompts must contain only minimized, derived context — never full memory stores, raw audit logs, raw audio, or sensitive-context data
- **NFR10:** Sensitive-context exclusion must be enforced at the capture layer — excluded app data must never reach memory, cloud reasoning, or briefing generation
- **NFR11:** Deletion propagation must complete fully before the transparency command can be invoked post-deletion — partial deletion states must not be visible to the user
- **NFR12:** The SQLite memory file must be readable and verifiable by the user using standard SQLite tools (no proprietary encryption that prevents user inspection)
- **NFR13:** No telemetry, usage analytics, or crash reporting transmitted without explicit user opt-in
- **NFR14:** API key must be stored locally in a protected configuration file, not embedded in source code or transmitted beyond Claude API authentication

### Reliability

- **NFR15:** The continuity loop (shutdown seed → resume) must be highly reliable; failures in shutdown seed capture or resume are critical-severity defects
- **NFR16:** Local operations (memory reads, mode switching, workspace restore, transparency command) must function without cloud API connectivity — no single points of failure for the core loop
- **NFR17:** Capability tier transitions (full → degraded → offline) must be detected and communicated within 5 seconds of connectivity change
- **NFR18:** Schema migrations must be non-destructive — automatic backup before migration, rollback path if migration fails, no data loss under any migration scenario
- **NFR19:** Graceful shutdown must capture state even on unexpected termination (e.g., system crash, power loss) to the extent possible — at minimum, the last known good state should be recoverable
- **NFR20:** N.O.V.A. must not interfere with the user's active work — no modal dialogs that block input, no stealing focus from the user's current application, no actions that modify the user's files or documents

### Resource Efficiency

- **NFR21:** Total runtime memory footprint should target under 750MB during an active session on a system with 16GB RAM (excluding Claude API network buffers)
- **NFR22:** CPU usage during idle active session (polling, no active interaction) must remain under 2% on a mid-range processor
- **NFR23:** SQLite database size must remain manageable — target under 100MB after 6 months of daily use with typical session patterns
- **NFR24:** N.O.V.A. must not noticeably degrade the performance of the user's primary work applications (VS Code, browser, etc.)
- **NFR25:** Claude API cost must remain under $2.50/month at 50 conversational turns per day with prompt caching enabled

### Auditability & Transparency

- **NFR26:** Every automated desktop action (app launch, window focus, window arrange) must be logged with timestamp, action type, target, and result
- **NFR27:** The transparency command must show a complete, accurate representation of all stored knowledge — no hidden state, no omitted categories, no stale cache presented as current
- **NFR28:** Audit trail must be queryable by the user — at minimum, viewable through N.O.V.A. commands, ideally also inspectable in the SQLite file directly
- **NFR29:** Deletion events must be logged in the audit trail (what was deleted, when, by user request) without preserving deleted content
- **NFR30:** Capability tier status must always be accessible to the user and proactively surfaced when it changes

### Backup & Recovery

- **NFR31:** User must be able to back up and restore N.O.V.A.'s local memory and state without specialized tooling — documented commands or file copy must be sufficient
