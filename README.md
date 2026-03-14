# Ember-2

A private, locally hosted AI companion and memory system designed to support reasoning, reflection, and long-term knowledge accumulation.

Ember-2 runs locally using Ollama and Open WebUI and evolves through curated memory, structured reflection, and modular capability expansion.

This repository tracks the architecture, requirements, and development roadmap.

---

# Project Goals

Ember-2 aims to become a personal AI environment that supports:

- local AI reasoning
- persistent memory
- reflective journaling
- knowledge retrieval
- multimodal interaction
- privacy-first architecture

The system is built in phases to keep progress achievable and enjoyable.

---

# System Overview

Core components include:

- **Open WebUI** — interface and orchestration
- **Ollama** — local model server
- **Local LLM** — reasoning engine
- **Memory Vault** — structured knowledge archive
- **RAG Retrieval** — contextual memory lookup
- **Reflection Engine** — journaling and summaries
- **Future tools** — web search, vision, voice

---

# Development Roadmap

## Phase 1 — Foundation (Hearth)

Goal: establish stable infrastructure and project structure.

Tasks:

- [ ] Create project root folder
- [ ] Create full folder structure
- [ ] Create GitHub repository
- [x] Write README
- [x] Write requirements document
- [x] Write architecture document
- [x] Save system prompt configuration
- [ ] Create static override folder for Open WebUI
- [ ] Replace default Open WebUI branding
- [ ] Configure splash screen logo
- [ ] Configure sidebar logo
- [ ] Verify Docker container persistence
- [ ] Document environment setup

Deliverable:

Working Ember-2 environment with project documentation.

---

## Phase 2 — Identity

Goal: define Ember-2 personality and interaction behavior.

Tasks:

- [x] Finalize system prompt
- [ ] Create `ember_role.md`
- [ ] Create `interaction_preferences.md`
- [ ] Create `user_profile.md`
- [x] Add example conversations for tone shaping
- [ ] Test prompt behavior across different tasks

Deliverable:

Stable interaction style and system identity.

---

## Phase 3 — Memory Spine (Grimoire)

Goal: create the structured memory vault.

Tasks:

- [ ] Create memory vault folder structure
- [ ] Create profile memory files
- [ ] Create working context file
- [ ] Create project status file
- [ ] Create journal template
- [ ] Create conversation summary template
- [ ] Create reference document folder
- [ ] Populate initial memory files
- [ ] Upload memory files into Open WebUI knowledge base
- [ ] Verify RAG retrieval

Deliverable:

Working persistent memory system.

---

## Phase 4 — Retrieval Intelligence (Lantern)

Goal: integrate memory retrieval into everyday conversations.

Tasks:

- [ ] Test RAG retrieval across documents
- [ ] Tune chunk sizes
- [ ] Test multi-document retrieval
- [ ] Validate prompt injection behavior
- [ ] Test memory recall during conversations
- [ ] Document retrieval workflow

Deliverable:

Ember-2 can recall relevant knowledge during conversation.

---

## Phase 5 — Reflection Engine (Mirror)

Goal: allow Ember-2 to accumulate insight over time.

Tasks:

- [ ] Create reflection workflow
- [ ] Generate conversation summaries
- [ ] Store summaries in memory vault
- [ ] Create journal entry process
- [ ] Test reflection prompts
- [ ] Test pattern detection prompts
- [ ] Document reflection workflow

Deliverable:

Ember-2 can accumulate curated knowledge over time.

---

## Phase 6 — Tool Integration (Workshop)

Goal: allow Ember-2 to interact with external information sources.

Tasks:

- [ ] Evaluate tool architecture
- [ ] Add controlled web search
- [ ] Add document search tools
- [ ] Test research workflows
- [ ] Implement tool governance rules
- [ ] Document tool behavior

Deliverable:

Expanded information capabilities.

---

## Phase 7 — Multimodal Capabilities (Observatory)

Goal: enable visual understanding and generation.

Tasks:

- [ ] Add image understanding
- [ ] Test screenshot interpretation
- [ ] Add diagram analysis
- [ ] Add image generation capability
- [ ] Test concept art generation
- [ ] Document multimodal workflows

Deliverable:

Ember-2 can process and generate images.

---

## Phase 8 — Voice Interface (Bell Tower)

Goal: enable conversational voice interaction.

Tasks:

- [ ] Evaluate speech-to-text solutions
- [ ] Implement speech input
- [ ] Implement voice output
- [ ] Test conversational voice mode
- [ ] Optimize latency
- [ ] Document voice configuration

Deliverable:

Voice interaction with Ember-2.

---

## Phase 9 — Resilience and Backup (Reliquary)

Goal: ensure system durability and data protection.

Tasks:

- [ ] Create backup workflow
- [ ] Backup Open WebUI data
- [ ] Backup memory vault
- [ ] Backup system prompts
- [ ] Backup configuration files
- [ ] Document restore procedure
- [ ] Purchase external backup drive

Deliverable:

Recoverable system environment.

---

# Future Experiments

Possible enhancements:

- automated conversation summarization
- memory promotion workflow
- pattern analysis across journals
- research assistant tools
- creative generation workflows
- dashboard for system state
- long-term knowledge graph

---

# Repository Structure
# Repository Structure

```
ember-2
│
├ README.md
├ requirements.md
├ architecture.md
├ project-plan.md
└ docs/
```

Sensitive data such as personal memory and journals remain **local and are not stored in this repository**.

---

# Design Philosophy

Ember-2 is built on three principles:

**Reasoning**  
Local models provide analysis and synthesis.

**Memory**  
Structured retrieval provides continuity.

**Reflection**  
Summaries and journals create long-term insight.

Together these form a personal AI environment rather than a stateless chatbot.

---

# Current Status

Phase: **Foundation**

Next milestone:

Build the **memory vault and RAG retrieval system**.

