---
title: "Product Brief: N.O.V.A."
status: "complete"
created: "2026-04-13"
updated: "2026-04-13T21:00:00Z"
inputs:
  - "_bmad-output/brainstorming/NOVA-BUILD-KIT.md"
  - "_bmad-output/brainstorming/brainstorming-session-2026-04-13-0830.md"
  - "_bmad-output/planning-artifacts/research/market-nova-desktop-ai-assistant-research-2026-04-13.md"
  - "_bmad-output/planning-artifacts/research/domain-local-first-personal-ai-agents-research-2026-04-13.md"
  - "_bmad-output/planning-artifacts/research/technical-local-first-windows-ai-assistant-stack-research-2026-04-13.md"
---

# Product Brief: N.O.V.A.

## Executive Summary

Every evening, solo developers and project-heavy students sit down at their laptops with maybe two hours of real work time. They spend the first twenty minutes remembering what they were doing — reopening files, retracing their last train of thought, reconstructing a workspace their OS forgot. Existing AI tools do not help. They are stateless, fragmented, and passive — trapped inside a browser tab or a chat window, with no memory of yesterday and no awareness of the desktop in front of them.

N.O.V.A. is a desktop-native context and focus companion for builders. Day one value comes from workspace setup, awareness, and rituals; day two value comes from compounding memory and context continuity. It is not a chatbot. It is a personal AI operating layer that sits between you and your Windows desktop — setting up your workspace with one command, shielding your limited work window from distractions, and anchoring your day with rituals that create continuity across sessions. The core loop is simple: open laptop → see briefing → enter mode → restore context → work → shutdown with tomorrow seed. Personal memory and workspace data are stored locally by default; cloud reasoning may be used in MVP, with a clear path toward hybrid and local inference.

The long-term product is voice-first. The first implementation is terminal-first: a lean, behavior-first MVP that proves workspace orchestration, persistent memory, workspace modes, and daily rituals — the core loop that makes N.O.V.A. genuinely useful from session one. Voice is layered on once the foundation is solid. Over time, N.O.V.A. evolves toward a personal Workspace OS — but the first version earns that ambition by delivering real, immediate utility. The market timing is right: the personal AI assistant market is $4.84B and growing at 42–46% CAGR, the "desktop AI that remembers your work" space has no category leader since Rewind's acquisition and shutdown, and the voice-first + memory + local desktop agent quadrant is virtually empty.

## The Problem

Context loss is the silent tax on every fragmented work session. Developers lose 15–30 minutes per session just restoring their working state — reopening VS Code with the right project, finding the file they were editing, remembering what they were trying to solve. For someone with a two-hour evening window, that is 12–25% of their productive time, burned every single day.

Today's tools do not solve this:

- **AI assistants** (Copilot, ChatGPT, Claude) are stateless — they help inside one conversation but forget everything between sessions. They have no idea what was on your screen yesterday.
- **Desktop tools** (PowerToys Workspaces) can restore window layouts, but they are manual, unaware of your actual work context, and have no concept of modes or focus protection.
- **Recording tools** (Screenpipe) capture what happened on your screen, but they are passive search engines — they do not proactively restore your state or orchestrate your workspace.
- **Focus apps** (Cold Turkey, Freedom) block distractions, but bluntly — they do not know what you are working on, what matters right now, or when to intervene versus stay silent.

The result: builders work in a fragmented, amnesiac environment where every tool is isolated, every session starts cold, and nobody is protecting the work window as a whole.

## The Solution

N.O.V.A. is a desktop-native AI companion that operates as a persistent, context-aware layer across your Windows workspace.

**Context Restoration.** N.O.V.A. remembers your last session — which apps were open, what workspace mode you were in, what you said you would do next. On resume, it restores your workspace context so you pick up where you left off. v0.1 restores workspace-level context: active apps, window list, workspace mode, and session notes. Deeper per-app state restoration (specific VS Code tabs, terminal history, cursor position) evolves in v0.2+.

**Persistent Memory.** Every session builds on the last. N.O.V.A. maintains a local memory store (SQLite, on your machine, never in the cloud) that accumulates knowledge of your projects, preferences, working patterns, and decisions. On day one, N.O.V.A. delivers value through workspace setup, context awareness, sharp interaction, and rituals. By day thirty, it knows your Tuesday evenings are always the React project and pre-loads that mode before you ask. Day 365, it is irreplaceable.

**Workspace Modes.** One command switches your environment between coding, study, or shutdown mode — each with its own app layout, focus rules, and behavioral posture. Modes are not just window arrangements; they shape what N.O.V.A. pays attention to and how it intervenes.

**Focus Protection.** N.O.V.A. monitors your workspace and actively shields your focus window. It suppresses non-essential notifications, flags context switches, and can block distracting apps — but only when appropriate. The behavioral doctrine is: *do not be noisy, do not be passive, be useful at the right moment.*

**Rituals.** A morning briefing shows what you left yesterday, what is pending, and what you said you would do today. A shutdown flow captures your state, your progress, and a "tomorrow seed" — a note to your future self that makes the next session start warm instead of cold.

**Transparency.** At any point, you can ask: "What do you know right now?" N.O.V.A. shows you exactly what it remembers, what context it has, and what assumptions it is making. You always have control. You always have visibility.

## What Makes This Different

N.O.V.A. is not the best chatbot for your desktop. It is the first system designed to preserve continuity of work across days. No single feature here is unprecedented — the differentiator is the combination, and the design philosophy behind it.

**Local-first privacy.** Memory, context, and all personal data stay on your machine. No cloud sync, no telemetry on your work, no corporate data mining. In a market reeling from the Recall privacy backlash and Rewind's forced migration to Meta's terms, this is not just a feature — it is a trust contract.

**Compounding memory.** Most AI tools are stateless by design. N.O.V.A. is stateful by design. Every session makes the next one better — learning your project rhythms, your preferred workspace configurations, your working patterns. After 90 days, N.O.V.A. holds 90 sessions of accumulated context. Switching to any other tool means starting cold again. This creates a natural moat that grows with time — not through vendor lock-in, but through accumulated value that users do not want to lose.

**Desktop-native awareness.** N.O.V.A. does not live in a browser tab. It knows which apps are open, which VS Code project is active, what window you are focused on. This is not screen recording — it is lightweight, real-time workspace awareness via Windows APIs.

**Proactive, not reactive.** Screenpipe records and lets you search. PowerToys saves layouts for you to manually restore. N.O.V.A. *acts* — it restores context, sets up workspaces, and protects focus without waiting to be asked. It is an orchestrator, not a search engine.

**Trust-first design.** Every action is transparent and reversible. Automation operates on a tiered trust model: safe actions (launch app, focus window) execute freely; sensitive actions require confirmation. The "What do you know?" command — a first-class transparency interface — ensures the user is never in the dark about what N.O.V.A. remembers, what context it has, and what assumptions it is making. In a landscape where ambient AI tools are met with justified suspicion, this is not a feature — it is a design principle.

## Who This Serves

**Solo builders and developers** working in VS Code, shipping side projects or professional work in fragmented evening sessions. They need an environment that remembers their state and gets them into flow instantly. *Hook: "It remembered exactly where I left off."*

**Project-heavy students** juggling coursework, research, and side projects on a single laptop with short work windows. They need modes that switch their entire environment in one command. *Hook: "One command and I am in study mode with everything I need."*

**Privacy-first desktop power users** who actively avoid cloud-dependent AI. They want a capable local AI that earns trust through transparency and never phones home with their data. *Hook: "It runs on my machine. My data stays on my machine."*

N.O.V.A. is **not** for enterprise teams seeking collaboration tools, general consumers wanting a smart-home voice assistant, or casual users looking for a chatbot.

## Success Criteria

N.O.V.A. v0.1 succeeds if users consistently say:

- **"It remembered exactly where I left off."** — Context restoration works reliably and saves real time every session.
- **"It got me into work mode instantly."** — Workspace modes eliminate manual setup and reduce time-to-flow.
- **"It actually helped me protect my focus."** — Focus protection is useful without being annoying; users notice fewer unproductive context switches.
- **"I trust it because I can see what it knows and control it."** — Transparency commands work, memory is inspectable, and users feel in control of their data.

Quantitative signals: workspace restore under 30 seconds, guided setup under 15 minutes, stable operation on a mid-range 16GB Windows 11 laptop, Claude API cost under $2.50/month for personal use.

## Scope

### v0.1 — Terminal-First MVP (~8–12 weeks)

**In scope:**
- Rich terminal interface (CLI-first, not GUI-first)
- Persistent local memory (SQLite, on-device only)
- Workspace-level context capture and restore (active apps, window list, workspace mode, session notes — not deep per-app state like specific tabs or cursor positions)
- Workspace modes (coding, study, shutdown) with configurable app and behavior profiles
- Morning briefing and shutdown flow with "tomorrow seed"
- App and window awareness via Windows APIs
- Transparency command: "What do you know right now?"
- Claude API for reasoning (with prompt caching)
- Safe desktop actions (launch, focus, arrange) — sensitive actions require confirmation
- Guided first-run setup that minimizes friction around Python environment, API configuration, and initial workspace capture
- A clear path toward local/hybrid LLM operation so the product is not permanently dependent on a single cloud API

**Out of scope for v0.1:**
- Voice interaction (STT/TTS) — deferred to v0.2
- Wake word detection
- Deep per-app state restoration (VS Code tabs, terminal history, cursor position) — v0.2+
- GUI / desktop overlay
- Local LLM inference
- Plugin or extension system
- Multi-device sync
- Teach-by-observation automation

### v0.2 — Voice & Polish

Voice interaction (push-to-talk, wake word, faster-whisper STT, Piper/Kokoro TTS), TUI upgrade via Textual, expanded mode library, richer memory with semantic search.

### v1.0 — Full Vision

Desktop GUI (Tauri 2.0), local/hybrid LLM options, MCP integration for tool ecosystem, advanced automation, community-contributed modes and rituals.

## Distribution

N.O.V.A. does not need mass marketing. It needs to reach the right 100 users first.

**Initial wedge:** Solo builders and VS Code developers with fragmented work windows — the people who feel the context-loss problem daily and are already looking for solutions.

**Launch surfaces:** Show HN, r/selfhosted, r/LocalLLaMA, GitHub README with a compelling demo, and build-in-public development logs. These communities are both the target user and the distribution channel.

**Growth thesis:** N.O.V.A.'s signature moments — the instant workspace restore, the morning briefing, the "What do you know?" transparency command — are inherently demoable. A 60-second screen recording of a real session resume is more persuasive than any landing page. Word of mouth among builders who recognize the problem carries the product from early adopters to community traction.

## Vision

If N.O.V.A. succeeds, it becomes the default answer to a question nobody has solved cleanly: *"How do I make my computer remember who I am and what I was doing?"*

The long-term vision is a personal AI operating layer for Windows — voice-first, memory-rich, desktop-aware, and deeply trusted — that compounds in value the longer it lives with you. Not a chatbot you visit. Not a tool you open. A companion that is always present, always aware, and always working in your interest. Over time, N.O.V.A. evolves toward a personal Workspace OS — but that ambition is earned through execution, not claimed in advance.

The approach is open-core: the local engine, memory, and core behaviors stay open and community-owned from day one. This is a deliberate trust signal — in a product built on local-first privacy, the code itself should be verifiable. Premium features — advanced voice models, hybrid cloud reasoning, specialized workspace integrations — sit on top without locking users out of their own data. Monetization comes from advanced capabilities, never from restricting access to personal memory.

N.O.V.A. is a personal system that gets sharper, more useful, and more irreplaceable every day you use it. That is the product. That is the moat.
