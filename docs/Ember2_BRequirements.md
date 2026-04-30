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

### 10. Vision Pipeline
- Multi-modal input support for images — auto-triggers when image is attached, no user action required
- Two-model architecture: vision preprocessor (qwen3-vl:8b) generates description, text primary (qwen3:8b) generates response through full pipeline
- Vision descriptions injected as context, not raw model output — constitutional review, identity rules, and grounding check all apply
- See ADR-032 for architecture

### 11. Conversation Modes
- **Standard mode** (default): full pipeline — memory retrieval, state injection, lodestone values, nature document, identity rules, full constitutional review, all vault writes active
- **Bare mode**: reduced pipeline — skips nature document, lodestone, identity rules, conversational style; constitutional review limited to three MVR criteria (position_collapse, sycophancy, embellishment). Two-layer gate: global setting enables, per-conversation toggle activates. See ADR-028.
- **Stateless mode**: no vault reads or writes for the duration of the conversation. No memory retrieval, no state injection, no conversation persistence. Constitutional review still fires but outcomes are not persisted. Two-layer gate. See ADR-031.
- Mode state is session-scoped and visually indicated in the UI. Mode transitions require explicit user action.

---

## Future Capabilities

- Proactive suggestions and nudges
- Scheduling and reminders
- Tool and data integrations — email, calendar, GitHub, health trackers, notes, finance, and more. See Tool and Data Integrations section. Read-only first, write access with agent layer only. Deferred pending core quality milestone.
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
- Email — read-only IMAP access. Two modes: ingestion (pull into vault as reference memory) and live context (surface relevant threads into conversation without permanent storage). Deferred pending core quality milestone.
- Calendar (Google, Outlook, iCal) — read-only. Events and scheduling context. Deferred pending core quality milestone.
- Slack / Discord — read-only conversation history ingestion. Deferred pending core quality milestone.

**Development**
- GitHub — read-only. Commits, PRs, issues, and activity feed. Deferred pending core quality milestone.
- Linear / Jira — issue and project tracking ingestion. Deferred pending core quality milestone.

**Health & Body**
- Fitbit — export ingestion. Activity, sleep, and health patterns. Deferred pending core quality milestone.
- Apple Health / Garmin — export ingestion. Activity, sleep, HRV. Deferred pending core quality milestone.
- Glucose monitors (Dexcom, Libre) — export or API ingestion. Time-series data. Deferred pending core quality milestone.
- Oura — export ingestion. Sleep and readiness data. Deferred pending core quality milestone.
- Diet apps (Cronometer, MyFitnessPal) — export ingestion. Nutrition logs. Deferred pending core quality milestone.

**Creativity & Knowledge**
- Obsidian / Notion — export ingestion. Notes and knowledge base. Deferred pending core quality milestone.
- Readwise — highlights and reading history ingestion. Deferred pending core quality milestone.
- Goodreads — reading history and reviews ingestion. Deferred pending core quality milestone.
- Spotify — listening history ingestion. Mood and energy signals. Deferred pending core quality milestone.

**Finance**
- Bank / credit card exports — spending pattern ingestion. Sensitive — requires explicit privacy policy in constitution.yaml before enabling. Deferred pending core quality milestone.

**Generic**
- CSV / JSON import — any structured data export from any app. Shipped v0.13.0.
- Any app that exports — Ember reads it. The ingestion pipeline is format-agnostic by design.

### Skill Definition Format

Each tool integration will be defined as a self-contained skill — a folder containing a SKILL.md file that describes when and how Ember uses the integration. The LLM reads the SKILL.md to understand what is available and how to invoke it. Skills are portable, inspectable, and user-extensible.

Examples: EMAIL.md, GITHUB.md, FITBIT.md.

This pattern is inspired by OpenClaw's AgentSkills format (Peter Steinberger, 2026), adapted for Ember's local-first, policy-governed architecture.

### Write Access
Write access to any external system (sending email, creating GitHub issues, archiving, etc.) is out of scope until the agent orchestration layer and full constitutional review framework are in place. Deferred pending core quality milestone.

---

## User Experience Principles

- Minimal friction
- Clear, grounded responses
- Context-aware, not generic
- Respectful of cognitive load (designed for users with ADHD and related executive function differences)
- Fast feedback loops
- Supports both deep work and casual interaction
- Conversation mode visibility — active conversation mode (standard, bare, stateless) must be visually indicated in the UI at all times so the user always knows what pipeline is active

---

## Neurodivergent-Compatible Design Principles

This section uses identity-first language ("autistic") in line with documented community preference (Taboas et al., 2023; Schuck et al., 2025), and person-first framing for ADHD where identity-first construction is grammatically awkward. "Neurodivergent" is used as the umbrella term throughout.

Ember's architecture reflects explicit design decisions made for autistic users, people with ADHD, and users with related neurodivergent cognitive profiles. These are requirements-level commitments, not accessibility add-ons.

### 1. External Memory as Structural Accommodation

Working memory differences are a well-documented characteristic of ADHD (ISCAP 2025; arXiv 2507.06864). For users with significant working memory constraints, an external memory system that accumulates context across sessions is not a convenience feature -- it is a structural accommodation equivalent to a cognitive prosthetic. Ember's append-only vault, session continuity, and retrieval pipeline are designed to function as cognitive scaffolding: reducing the overhead of maintaining context in working memory by making it externally retrievable and persistent.

Research on AI tool use by neurodivergent users documents that externalized memory and context management significantly reduce cognitive load for users with executive function differences (ISCAP 2025 proceedings; Jang et al., CHI 2024). Ember's architecture is designed to serve this function reliably, not incidentally.

### 2. Energy-Aware Design

Variable cognitive capacity across hours, days, and weeks is a first-class design requirement for many neurodivergent users, not an edge case. Ember's soft mode concept -- reduced complexity, reduced friction, reduced cognitive demand -- is a core design feature. Future proactive assistance features must respect energy state. Notifications, suggestions, and unsolicited prompts are off by default.

The design principle: the system must be usable on hard days, not only on productive ones. Features that require full cognitive engagement should degrade gracefully when engagement is lower.

### 3. Non-Sycophantic by Constitution

Autistic users report specific dissatisfaction with AI systems that smooth, soften, and over-affirm. Research on autistic users' LLM use documents that sycophantic AI behavior conflicts with autistic communication preferences for directness, accuracy, and authenticity (Carik et al., ACM 2025; Jang et al., CHI 2024). Systems that perform warmth without grounding it in genuine engagement are experienced as alienating rather than supportive.

Ember's constitutional design -- non-sycophantic, direct, challenging of flawed reasoning, not optimized for approval -- reflects an explicit design choice grounded in this research. The directness and intellectual respect principles in the constitution are not stylistic preferences; they are accessibility requirements for a meaningful portion of the intended user base.

### 4. Privacy as Structural Trust

Cloud AI systems require disclosure of personal information to third-party infrastructure. For autistic users, this creates a documented trust barrier: the disclosure risk is concrete, terms of data use are opaque, and loss of control over personal information is experienced as a meaningful harm (Carik et al., ACM 2025). The risk is compounded by the depth of personal disclosure required for a system like Ember to function well.

Ember's local-first architecture removes this barrier structurally. Data never leaves the user's device by default. The vault is user-owned and inspectable. This is a structural privacy guarantee, not a policy promise that can be changed by terms of service update.

### 5. Transparency and Inspectability as Requirements

Neurodivergent users -- particularly autistic users -- benefit from systems that are transparent about what they are doing and why. Black-box AI behavior, unexplained decisions, and hidden governance are experienced as trust violations that prevent meaningful engagement (Carik et al., ACM 2025).

Ember's design prioritizes inspectability throughout: explicit constitutional review with logged outcomes, retrievable and auditable vault contents, user-visible state records, and governance documents that explain design intent. The user should be able to understand what Ember knows, why she responded as she did, and what policies governed her response. This is not only good engineering practice -- it is a requirement for the system to be genuinely usable by users who need to understand the tools holding their cognitive scaffolding.

### References

- Taboas, A. et al. (2023). "Preferences for identity-first versus person-first language in a US sample of autism spectrum disorder stakeholders." Autism.
- Schuck, R. et al. (2025). Systematic review of language preferences, n=6350. Journal of Autism and Developmental Disorders. Stanford.
- Jang, S. et al. (2024). "It's the only thing I can trust." CHI 2024.
- Carik, S. et al. (2025). "Exploring LLMs Through a Neurodivergent Lens." ACM 2025.
- ISCAP 2025 proceedings. ADHD executive function scaffolding via AI.
- "Toward Neurodivergent-Aware Productivity." arXiv 2507.06864, July 2025.

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
