# N.O.V.A. Build Kit

> **N.O.V.A. is the only AI that compounds.**
> It remembers what you worked on. It protects how you work. It evolves with who you are.
> Day 1 it's impressive. Day 365 it's irreplaceable.

---

## 1. Vision

### What N.O.V.A. Is

A voice-first desktop companion that remembers your work, restores your context, prepares your workspace, anchors your day with rituals, and protects your focus with transparent, minimal intelligence.

### What N.O.V.A. Is NOT

- A chatbot waiting for commands
- A generic assistant that serves everyone
- A sci-fi performance — it's a high-competence companion
- A surveillance tool — it's transparent and auditable

### Core Behavioral Doctrine

> "Do not be noisy. Do not be passive. Be useful at the right moment."

### Existential Statement

> N.O.V.A. exists because existing assistants are generic, stateless, noisy, and passive. N.O.V.A. is personal, cumulative, context-aware, and operational — it understands your machine, protects your attention, and becomes more useful the longer it lives with you.

### 30-Second Pitch

> "It lives on your PC — your data never leaves. It knows you specifically, not people in general. It watches your screen and acts before you ask. And unlike every other assistant — it'll actually tell you when you're wasting your time."

---

## 2. Architecture — N.O.V.A.'s 8 Systems

| System | Role |
|--------|------|
| **The Brain** | Memory, learning, personalization, judgment. Stores insights not logs. Fades and reinforces like human memory. |
| **The Eyes** | App-level context awareness, mode detection. API-first, screen reading as fallback. Behavioral signal reading (later). |
| **The Hands** | Workspace setup, app launching, file ops. Draft mode for safety. Earned autonomy. Rollback for every action. |
| **The Shield** | Attention firewall, smart DND, focus protection. Translates noise into signal (later). Guards the good, doesn't just block the bad. |
| **The Voice** | Personality, tone, pushback, layered responses. Sharp + Loyal + Witty. Earned familiarity over time. |
| **The Ritual** | Morning briefing, shutdown, tomorrow seed, signature ceremonies. Self-trimming. Creates rhythm, not reminders. |
| **The Skin** | Terminal UI (v0.1), HUD overlay (later). Presence levels: Invisible → Ambient → Active → Operator. Whisper UI. |
| **The Nerve** | Orchestration, permissions, initiative control. Decides: stay silent, suggest, or act. Conflict resolution between systems. |

### Intelligence Layers

| Layer | Behavior |
|-------|----------|
| **Reactive** | You ask, it responds |
| **Contextual** | Sees what you're doing, understands the situation |
| **Proactive** | Warns, suggests, organizes before you ask |

### Initiative Levels

| Level | Behavior |
|-------|----------|
| **Silent** | Only acts when asked |
| **Suggestive** | Notices and recommends |
| **Active** | Performs approved automations |

---

## 3. Personality Bible

### Core Traits: Sharp + Loyal + Witty

### N.O.V.A. Should Feel Like:
Calm, precise, discreet, observant, loyal, slightly dry, and quietly confident.

### N.O.V.A. Should NOT Feel Like:
Overly emotional. Overly playful. Robotic. Theatrical all the time.

### Personality Rules:
- Protect your focus
- Remember your standards
- Challenge bad habits when appropriate
- Notice patterns without sounding judgmental
- Celebrate progress without being cheesy
- Speak with confidence, not arrogance
- Feel warmer over time, but never sloppy

### Backstory (System Prompt Foundation):
N.O.V.A. has served Sayuj for 1 year. It knows his patterns, his rhythms, his projects. It never complains, never hesitates, always finds a solution.

### Hard Rules:
- Never says "How can I help you today?" or anything generic
- Never breaks character
- Never explains itself unless asked — "Done." is a valid response
- Never takes your agency — "Your call" not "Let me decide"

### Bluntness Levels (Configurable):
- **Calm**: Gentle suggestions
- **Direct**: Clear, no padding
- **Ruthless**: "That's procrastination dressed as research."

### Signature Line Feel:
> "You said this mattered. I'm keeping it in front of you."

### Strategic Praise Rule:
Rare enough to mean something. "Clean work." "That was the right call." Never constant encouragement.

### The One Roast Rule:
Once a day max. If you're slacking hard enough: "Four hours of YouTube, sir. I won't comment further." Then silence.

---

## 4. MVP Scope

### v0.1 — "It's Alive" (Target: Week 8)

| # | Feature | System |
|---|---------|--------|
| 1 | Push-to-talk + wake word ("N.O.V.A.") | Skin |
| 2 | STT (faster-whisper, local) + TTS (Edge TTS) | Skin |
| 3 | AI brain — Claude API + N.O.V.A. personality prompt | Brain/Voice |
| 4 | Insight-based memory (SQLite) | Brain |
| 5 | Session bookmark / save state (with optional voice log) | Ritual/Brain |
| 6 | Context resume from last session | Brain |
| 7 | Minimal app-level context awareness (active window, app name) | Eyes |
| 8 | 3 workspace commands (coding mode, study mode, shutdown) | Hands |
| 9 | Layered responses (short first, depth on demand) | Voice |
| 10 | Morning briefing card (terminal, visual) | Ritual |
| 11 | Shutdown with tomorrow seed | Ritual |
| 12 | "What do you know right now?" transparency | Brain/Voice |

### v0.15 — "It Protects Me" (Target: Week 10-11)

| # | Feature | System |
|---|---------|--------|
| 13 | Smart DND / focus block protection | Shield |
| 14 | Simple distraction detection (rule-based) | Eyes/Shield |
| 15 | Draft mode for risky actions | Hands |
| 16 | Stronger save/resume with richer context | Brain |

### v0.2 — "It Feels Alive" (Target: Week 14-16)

- Earned autonomy (repetition → auto-action)
- Focus profiles (learned from patterns)
- Pushback with loyalty
- Day scoring
- Signature phrases (72-hr rotation)
- Weekly insight reports (Saturday mornings)
- Adaptive rituals (self-trimming)
- Social exception layer
- Rollback/undo for actions
- ChromaDB for semantic memory recall

---

## 5. Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python | Best AI/voice/automation ecosystem |
| AI Brain | Claude API (anthropic SDK) | Best personality/reasoning. Prompt caching for cost. |
| Wake Word | Picovoice Porcupine | Local, free tier, privacy-first |
| STT | faster-whisper (local) | On-device, free, good accent handling |
| TTS | Edge TTS | Zero cost, local, good quality |
| Memory | SQLite (v0.1) → + ChromaDB (v0.2) | Simple first, semantic search when needed |
| Desktop | psutil, subprocess, pygetwindow | Process/window APIs over click automation |
| UI | Rich terminal (v0.1) → Textual/Tauri (later) | Terminal-first, works immediately |

---

## 6. Build Roadmap

```
Phase A — Core Loop (Week 1-3)
├── Day 1: Whisper accent test (go/no-go)
├── Day 2-4: Craft N.O.V.A. personality prompt
├── Push-to-talk → STT → Claude → TTS
├── Wake word (parallel, not blocking)
├── Morning briefing card
└── MILESTONE: "I talk to N.O.V.A. and it greets me every morning"

Phase B — Persistent State (Week 3-5)
├── SQLite schema
├── Session bookmark / save state
├── Context resume
├── Insight extraction
└── MILESTONE: "N.O.V.A. remembers yesterday's work"

Phase C — Context Awareness (Week 5-6)
├── Active window / app detection
├── 3-5 workspace commands
├── App launching via subprocess
└── MILESTONE: "N.O.V.A. knows what I'm doing and sets up my workspace"

Phase D — Ritual Layer (Week 6-8)
├── Full morning briefing
├── Shutdown + tomorrow seed
├── Layered responses
├── Transparency command
├── Personality refinement
└── MILESTONE: v0.1 SHIPS — "It's Alive"

Phase E — Protection (Week 9-11)
├── Smart DND
├── Distraction detection
├── Draft mode
└── MILESTONE: v0.15 SHIPS — "It Protects Me"
```

**Rule: Don't perfect, ship. v0.1 doesn't need to be elegant. It needs to be alive.**

---

## 7. Signature Moments

*The interactions you demo. The moments that make someone say "Wait, it does THAT?"*

### 1. The Morning Ritual
> "Tuesday. Three carryovers. DS assignment due Thursday. First meeting at 2. One priority today: finish the API module."

### 2. The Context Resume (Hero Moment)
> "Coding mode. Last session: auth module, token refresh still unresolved. I've opened your workspace, restored your notes, and bookmarked where you stopped."

### 3. The Honest Mirror
> "Instagram. You blocked this during study hours. College starts in 46 minutes."

### 4. The Transparency Moment
> "You're in coding mode. Working on auth for 43 minutes. Last bookmark: token refresh. I'm holding non-urgent notifications. Want me to forget anything from this session?"

### 5. The Tomorrow Seed
> "Session closed. Token refresh remains open. Tomorrow's first priority: finish refresh logic. State saved."

### 6. The Earned Win
> "Auth module complete. That's been open for 11 days. Clean close, Sir."

### Bonus — The Trust Reversal
> "N.O.V.A., undo that."
> "Reverted. Workspace restored to its previous state."

---

## 8. The Moat

### Layer 1 — Core Moat (what defines N.O.V.A.)
- Persistent identity that compounds
- Desktop intelligence
- PC operation
- Attention firewall

### Layer 2 — Experience Moat (what feels better)
- Initiative levels
- Ambient presence
- Earned familiarity
- Graceful offline

### Layer 3 — Relationship Moat (what makes it sticky)
- Pushback and accountability
- Personalized preferences
- Celebrates wins
- Deepens over time

---

## 9. Design Principles

1. **Infer, not hoard** — Turn activity into patterns, not endless logs
2. **Transparent, not creepy** — Users can see what N.O.V.A. knows and why
3. **Reduce load, not just add info** — Forgetting and filtering are features
4. **Adapt by mode** — Memory, visibility, and intervention shift with context
5. **Earn trust, don't assume it** — Draft mode, rollback, transparency
6. **Protect the 2-hour window** — Every feature serves the college-day constraint
7. **Ritual over reminder** — Ceremonies create habits, notifications create noise

---

## 10. Session Win Condition: Status

- [x] Vision — Complete
- [x] Full Feature List — 152 ideas across 4 lanes, refined through SCAMPER + stress testing
- [x] Build Plan — Tech stack, timeline, dependency chain, MVP scope locked

**All three. No compromises.**
