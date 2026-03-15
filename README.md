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

# Current

Phase: **Reflection Engine**

Current milestone:
**Stable append-only memory vault with semantic retrieval and reflection system.**

Next milestone:
**Implement the Context Builder so Ember-2 can reason using stored memory.**


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

# Memory Schema

All Ember-2 memories are stores as structured JSON objects inside the private vault.

Example memory: 

{
  "timestamp": "2026-03-14T19-50-54",
  "type": "reflection",
  "text": "Reflection over 10 journal memories...",
  "source": "reflection_engine",
  "tags": ["reflection"],
  "metadata": {
    "cadence": "weekly"
  }
}

Fields

timestamp
ISO-style timestamp used for chronological ordering.

type
Memory category (journal, reflection, project, etc.).

text
Primary memory content.

source
System component that created the memory.

Examples:
   api
   reflection_engine
   future tools
   
tags
Optional classification labels used for filtering or grouping.

Example:
   tags: ["reflection"]


metadata
Optional structured metadata used for system logic.

Example:

Daily reflection

metadata:
  cadence: daily


Weekly reflection

metadata:
  cadence: weekly


Metadata allows Ember-2 to support structured behaviors without modifying the core memory schema.

---

# Development Roadmap

## Phase 1 — Foundation (Hearth)

Goal: establish stable infrastructure and project structure.

Tasks:

- [x] Create project root folder
- [x ] Create full folder structure
- [x] Create GitHub repository
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

- [x] Create memory vault folder structure
- [x] Implement append-only JSON memory storage
- [x] Implement MemoryService write interface
- [x] Implement vector embedding generation
- [x] Implement vector index storage
- [x] Implement semantic search retrieval
- [x] Implement keyword search retrieval
- [x] Implement chronological recall
- [x] Add metadata support to memory schema
- [x] Add tag support to memory schema
- [ ] Create profile memory files
- [ ] Create working context file
- [ ] Create project status file
- [ ] Create journal template
- [ ] Create conversation summary template
- [ ] Create reference document folder
- [ ] Populate initial curated memory
- [ ] Integrate vault into Open WebUI knowledge base


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

## Phase 4.5 — Context Builder (Bridge)

Goal: allow Ember-2 to reason using stored memory.

Tasks:

- [ ] Implement context builder module
- [ ] Retrieve relevant memories using semantic search
- [ ] Retrieve recent reflections
- [ ] Assemble structured context block
- [ ] Pass context into LLM prompts
- [ ] Test context recall during conversations
- [ ] Document context construction rules

Deliverable:

Ember-2 can reason using stored memory and reflections.

---

## Phase 5 — Reflection Engine (Mirror)

Goal: allow Ember-2 to accumulate insight over time.

Tasks:

- [x] Implement reflection engine
- [x] Implement reflection memory storage
- [x] Implement daily reflection runner
- [x] Implement weekly reflection runner
- [x] Add reflection metadata (cadence)
- [x] Add reflection tagging
- [ ] Create reflection prompt templates
- [ ] Implement pattern detection prompts
- [ ] Document reflection workflows


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
- context builder for memory-aware reasoning
- reflection ranking and importance scoring
- memory graph visualization
- semantic clustering of journal entries
- automated project retrospectives
- decision tracking and reasoning history

---

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

## Pattern Analysis

**Goal:** Enable Ember-2 to identify recurring themes, habits, concerns, and decision patterns across stored memory.

**Purpose**

Pattern analysis allows Ember-2 to move beyond simple recall. Instead of only retrieving stored facts, the system can synthesize recurring signals across journals, conversation summaries, and project notes.

**Potential Uses**

- Detect recurring themes in journal entries  
- Identify repeated blockers or stressors  
- Recognize productive workflows or helpful routines  
- Surface common decision-making patterns  
- Highlight ideas or concerns that repeatedly appear  

**Example Prompts**

- “What themes have shown up repeatedly in my recent journal entries?”  
- “What kinds of problems tend to drain my energy?”  
- “What patterns do you notice in how I approach projects?”  
- “What topics have I revisited multiple times over the last month?”  

**Implementation Notes**

- Pattern analysis should operate on curated memory rather than raw conversation logs  
- Journal entries and summaries should follow consistent templates  
- Reflection prompts should run periodically rather than continuously  
- Outputs should be interpreted as insights rather than definitive conclusions  

**Tasks**

- [ ] Create reflection prompt for recurring themes  
- [ ] Create reflection prompt for repeated blockers  
- [ ] Create reflection prompt for decision patterns  
- [ ] Test pattern analysis across journal entries  
- [ ] Test pattern analysis across conversation summaries  
- [ ] Implement periodic pattern summary generation  


---

## Timeline Memory

**Goal:** Allow Ember-2 to reconstruct and reason about sequences of events over time.

**Purpose**

Timeline memory helps Ember-2 understand **how events relate chronologically**, which allows it to track progress, identify turning points, and reconstruct project development.

**Potential Uses**

- Track project evolution  
- Trace changes in goals or interests  
- Reconstruct major system milestones  
- Review progress across phases  
- Understand how decisions developed over time  

**Example Prompts**

- “What happened in the Ember-2 project over the last two weeks?”  
- “When did I first set up local hosting?”  
- “What were the major architectural decisions for Ember-2?”  
- “Show me the timeline of the system’s development.”  

**Implementation Notes**

- All memory artifacts should include a date  
- Journal entries should preserve chronological order  
- Major system events should be recorded in the project log  
- Conversation summaries should include timestamps  

**Recommended Timeline Sources**

- Cathedral log  
- Project milestone notes  
- Dated conversation summaries  
- Journal entries  

**Tasks**

- [ ] Standardize date format across memory documents  
- [ ] Create timeline prompt for project milestones  
- [ ] Create timeline prompt for decision tracking  
- [ ] Test timeline reconstruction from conversation summaries  
- [ ] Test timeline reconstruction from journal entries  
- [ ] Create a milestone log for Ember-2 development  


---

## Long-Term Reflections

**Goal:** Enable Ember-2 to generate higher-level reflections from accumulated memory over weeks and months.

**Purpose**

Long-term reflections allow Ember-2 to synthesize meaning across time. Instead of responding only to immediate prompts, the system can identify trends, shifts in thinking, and broader narratives.

**Potential Uses**

- Monthly reflection summaries  
- Project retrospectives  
- Changes in focus or motivation  
- Recurring identity themes  
- Long-term creative patterns  

**Example Prompts**

- “What changed in my thinking this month?”  
- “What themes have defined the last six weeks?”  
- “What progress has the Ember-2 project made over time?”  
- “What have I been circling around without resolving?”  
- “What insights emerge from my recent reflections?”  

**Implementation Notes**

- Long-term reflections should use curated memory sources  
- Reflections should synthesize ideas rather than merely summarize  
- Monthly cadence is typically more useful than daily reflections  
- Meaningful reflections can be stored as new memory artifacts  

**Suggested Outputs**

- Monthly reflection notes  
- Project retrospectives  
- Quarterly system evolution summaries  
- Thematic analysis across journal entries  

**Tasks**

- [ ] Create monthly reflection template  
- [ ] Create project retrospective template  
- [ ] Define reflection cadence  
- [ ] Test monthly reflection prompts  
- [ ] Store useful reflections back into the memory vault  
- [ ] Create an Ember monthly summary artifact  


---

## Reflective Intelligence Roadmap

**Goal:** Transform Ember-2 from a memory-enabled assistant into a system capable of interpreting patterns and meaning across time.

This capability is built from three interconnected systems:

1. **Pattern Analysis**  
   Identifying recurring themes, behaviors, and signals across memory.

2. **Timeline Memory**  
   Organizing events and decisions in chronological context.

3. **Long-Term Reflection**  
   Synthesizing meaning across extended periods.

**Design Principle**

Ember-2 should not simply remember more information.  
It should become better at helping interpret what that information means.

**Tasks**

- [ ] Define reflection workflows  
- [ ] Define pattern analysis prompts  
- [ ] Define timeline reconstruction prompts  
- [ ] Define monthly reflection prompts  
- [ ] Evaluate reflection usefulness and accuracy  
- [ ] Store validated reflections in the memory vault

**Deliverable**

A reflection layer that converts stored memory into insight.

## Memory Hygiene & Memory Decay

**Goal:** Maintain a high-quality memory vault by preventing clutter, outdated context, and redundant information from degrading retrieval quality.

**Purpose**

Over time, accumulated memory can become noisy. Without maintenance, retrieval systems may surface outdated, irrelevant, or redundant information. Memory hygiene ensures Ember-2’s knowledge base remains useful and coherent.

This system treats memory as **curated knowledge**, not permanent storage of everything.

---

### Core Principles

**Selective Storage**

Only meaningful interactions should be promoted into long-term memory.

Examples of useful memory artifacts:

- important insights
- decisions
- recurring themes
- project milestones
- long-term reflections

Examples of things that should **not** become memory:

- casual conversation
- temporary questions
- routine daily chatter
- low-signal brainstorming

---

**Memory Review**

Memory should be reviewed periodically to ensure it remains relevant.

Recommended cadence:

- light review monthly
- deeper review quarterly

Review questions:

- Is this memory still useful?
- Is this duplicated elsewhere?
- Has this idea evolved since it was written?
- Should this memory be summarized into a higher-level insight?

---

**Memory Consolidation**

When multiple related entries exist, they should be merged into a stronger summary.

Example:

Five separate notes about a recurring issue can become:

> "Recurring friction occurs when project planning lacks defined milestones."

Consolidated memory improves retrieval accuracy and reduces noise.

---

**Memory Aging**

Older memories may gradually lose relevance.

Possible actions for aging memory:

- archive
- summarize
- merge
- delete

This prevents the vault from growing endlessly.

---

### Memory Lifecycle

The lifecycle of a memory artifact follows this progression:

Conversation │ ▼ Candidate Memory │ ▼ User Review │ ▼ Stored in Memory Vault │ ▼ Periodic Review │ ▼ Consolidate / Archive / Delete

This lifecycle ensures that stored knowledge remains meaningful over time.

---

### Memory Categories and Expected Lifetimes

| Memory Type | Expected Lifetime |
|--------------|------------------|
| Profile | long-term |
| Projects | medium to long-term |
| Conversation Summaries | medium |
| Journal Entries | long-term |
| Current Context | short-term |
| Reference Documents | varies |

---

### Archive Strategy

Older or inactive memory can be moved to an archive folder.

Example:
ember2-memory │ ├ profile ├ current_context ├ projects ├ journal ├ conversation_summaries ├ reference └ archive
Archived memory remains accessible but is not prioritized during retrieval.

---

### Tasks

- [ ] Define memory promotion criteria
- [ ] Define monthly memory review workflow
- [ ] Define consolidation process
- [ ] Create archive folder
- [ ] Implement archival tagging system
- [ ] Define deletion policy
- [ ] Test retrieval quality after pruning memory

---

### Design Principle

Ember-2 should maintain **a small, high-quality memory vault** rather than a massive unfiltered archive.

The goal is not maximum storage.

The goal is **maximum clarity**.

Ember-2 follows an **append-only memory architecture.**

Memories are never overwritten or modified.

New understanding is recorded as new memory artifacts.

This approach **preserves a chronological knowledge history and enables reflection, pattern detection, and timeline reconstruction.**


