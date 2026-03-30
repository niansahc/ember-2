# Ember-2 Business Requirements Document (BRD)

## Purpose

This document captures the vision, goals, and user requirements for Ember-2 as a personal intelligence system.

Ember-2 is intended to support life, work, health, creativity, and long-term decision-making through structured memory, reflection, and intelligent assistance.

---

## Vision

Ember-2 is a private, evolving system that:

- understands context across time
- remembers what matters
- identifies patterns and trends
- assists with decisions and planning
- reduces cognitive load
- supports both analytical and creative thinking

---

## Primary Goals

- Reduce mental overhead and decision fatigue
- Provide context-aware assistance across domains
- Enable long-term pattern recognition
- Support project and life management
- Maintain a private, local-first knowledge system
- Evolve into a proactive assistant over time

---

## Core Capabilities

### 1. Memory & Recall
- Store conversations, notes, and external data
- Retrieve relevant information by meaning, not just keywords
- Reconstruct timelines and past decisions

### 2. Reflection & Insight
- Detect patterns in behavior, work, and health
- Generate daily and weekly summaries
- Identify trends and recurring themes

### 3. Context-Aware Conversation
- Answer questions using past context
- Provide grounded, personalized responses
- Avoid generic or stateless outputs

### 4. Project & Work Support
- Track ongoing initiatives (e.g., Ember-2, work projects)
- Summarize progress and blockers
- Assist with planning and prioritization

### 5. Life Management
- Support routines, habits, and goals
- Help track health, energy, and patterns
- Provide reminders and structured thinking support (future)

### 6. Creative & Personal Expression
- Support writing, ideation, and creative work
- Assist with structuring ideas and projects
- Maintain continuity of personal themes and interests

### 7. Journal Entry
- Accept first-person journal entries as a direct input method (CLI and API)
- Store entries as typed memory records, immediately searchable and retrievable
- Include mood and tags as structured metadata for filtering and pattern recognition
- Journal content feeds into daily and weekly reflection alongside other memory sources

### 8. Constitutional Response Governance
- All responses subject to an explicit, inspectable governance layer
- Triggered post-draft — does not interfere with normal conversation flow
- Outcomes are allow, revise, or refuse with redirect — never silent suppression
- Governance rules live in external config, not buried in prompts or model behavior
- Review decisions are logged and auditable by the user
- Social engineering detection — safety trigger layer detects identity override, intimacy exploitation, false urgency, pretexting, and persona override attempts; routes to constitutional review without interfering with normal conversation

### 9. Cloud Model Support
- Opt-in cloud model support (Anthropic Claude, OpenAI GPT) — user-controlled, API key stored in system credential manager, never required, never default; local models remain the primary path

---

## Future Capabilities

- Task and goal tracking (state layer)
- Proactive suggestions and nudges
- Scheduling and reminders
- Tool and data integrations — email, calendar, GitHub, health trackers, notes, finance, and more. See Tool and Data Integrations section. Read-only first, write access with agent layer only.
- Multi-modal inputs (documents, images, etc.)
- Agent-style workflows
- Shareability — Ember's persona, governance config, and retrieval logic are the shareable artifacts; user data never leaves the local vault; two distribution paths: (1) non-technical user path with one-click installer, no CLI required, and an onboarding conversation flow that seeds identity context through conversation rather than scripts; (2) technical user path with clean setup docs, seed_identity_template.py, and API-first configuration
- Proactive / heartbeat mode — Ember wakes on a configurable schedule and pushes context or summaries without being prompted. Examples: Monday week preview, energy check-ins, task reminders. (Inspired by OpenClaw, Peter Steinberger, 2026)
- Trace-driven learning — interaction traces inform retrieval routing and agent behavior over time; Ember gets better at serving this specific user through accumulated experience rather than retraining. Local only. (Inspired by OpenJarvis Learning primitive, Stanford Scaling Intelligence Lab, Saad-Falcon et al., 2026. See also ADR-013.)

---

## Tool and Data Integrations

Ember is designed to ingest and read data from external sources. All integrations are:

- **Read-only first.** Write access requires the full agent orchestration layer and constitutional review framework. It is never added before read-only is stable.
- **Local processing only.** Data from external sources is processed on-device. Nothing leaves the machine.
- **Explicit opt-in.** Each integration is configured individually. Nothing is connected by default.
- **User-controlled scope.** The user decides what gets ingested into the vault permanently vs what is read as live context for a single conversation.
- **Sensitive data governed explicitly.** Health and finance data require explicit privacy policy entries in constitution.yaml before ingestion is enabled.

### Integration Categories

**Communication**
- Email — read-only IMAP access. Two modes: ingestion (pull into vault as reference memory) and live context (surface relevant threads into conversation without permanent storage). Planned v0.13.0.
- Calendar (Google, Outlook, iCal) — read-only. Events and scheduling context. Planned v0.14.0.
- Slack / Discord — read-only conversation history ingestion. Planned v0.15.0.

**Development**
- GitHub — read-only. Commits, PRs, issues, and activity feed. Enables coders to use Ember as a development context layer — project history, blockers, and arcs are retrievable in conversation. Live context mode supported alongside ingestion. Planned v0.13.0.
- Linear / Jira — issue and project tracking ingestion. Planned v0.14.0.

**Health & Body**
- Fitbit — export ingestion. Activity, sleep, and health patterns. Planned v0.13.0.
- Apple Health / Garmin — export ingestion. Activity, sleep, HRV. Planned v0.13.0.
- Glucose monitors (Dexcom, Libre) — export or API ingestion. Time-series data. Planned v0.14.0.
- Oura — export ingestion. Sleep and readiness data. Planned v0.14.0.
- Diet apps (Cronometer, MyFitnessPal) — export ingestion. Nutrition logs. Planned v0.14.0.

**Creativity & Knowledge**
- Obsidian / Notion — export ingestion. Notes and knowledge base. Planned v0.14.0.
- Readwise — highlights and reading history ingestion. Planned v0.14.0.
- Goodreads — reading history and reviews ingestion. Planned v0.14.0.
- Spotify — listening history ingestion. Mood and energy signals. Planned v0.14.0.

**Finance**
- Bank / credit card exports — spending pattern ingestion. Sensitive — requires explicit privacy policy in constitution.yaml before enabling. Planned v0.15.0.

**Generic**
- CSV / JSON import — any structured data export from any app. Planned v0.13.0.
- Any app that exports — Ember reads it. The ingestion pipeline is format-agnostic by design.

### Skill Definition Format

Each tool integration will be defined as a self-contained skill — a folder containing a SKILL.md file that describes when and how Ember uses the integration. The LLM reads the SKILL.md to understand what is available and how to invoke it. Skills are portable, inspectable, and user-extensible.

Examples: EMAIL.md, GITHUB.md, FITBIT.md.

This pattern is inspired by OpenClaw's AgentSkills format (Peter Steinberger, 2026), adapted for Ember's local-first, policy-governed architecture.

### Write Access
Write access to any external system (sending email, creating GitHub issues, archiving, etc.) is out of scope until the agent orchestration layer and full constitutional review framework are in place. Planned v0.15.0.

---

## User Experience Principles

- Minimal friction
- Clear, grounded responses
- Context-aware, not generic
- Respectful of cognitive load (ADHD-friendly)
- Fast feedback loops
- Supports both deep work and casual interaction

---

## Constraints

- Must run locally (privacy-first)
- Must be rebuildable from raw data
- Must avoid data corruption or loss
- Must not rely on external APIs long-term
- Must scale with increasing memory volume

---

## Risks

- Memory contamination (low-quality ingestion)
- Retrieval drift or bias
- Overfitting to specific topics
- System complexity becoming unmanageable
- Performance degradation at scale

---

## Success Criteria

- Answers improve over time with memory accumulation
- System can summarize past work accurately
- Patterns identified are meaningful and useful
- User relies on system for real decisions and tracking
- System remains stable and maintainable as it grows

---

## Long-Term Direction

Ember-2 evolves from:

1. Memory system
2. Reflection system
3. Context-aware assistant
4. State-aware assistant
5. Proactive personal intelligence system

The goal is not just to answer questions, but to become a trusted system for thinking, tracking, and navigating life.
