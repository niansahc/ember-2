# ADR-003: Modular Service-Oriented Local Architecture

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 must remain:
- local-first
- maintainable
- extensible
- independent of external platforms

A monolithic design would limit flexibility and increase coupling.

## Decision

The system will be organized into modular services:

- context service (retrieval, ranking, formatting)
- prompt builder
- model interface (Ollama)
- safety review service
- memory storage + vector index
- API layer (FastAPI)

## Rationale

- clear separation of concerns
- easier testing and iteration
- components can evolve independently
- supports future capabilities (agents, multimodal, scheduling)

## Consequences

+ improved maintainability
+ easier debugging
+ scalable architecture

- increased coordination between modules
- requires well-defined interfaces

## Alternatives Considered

### Monolithic architecture
Rejected:
- harder to evolve
- tightly coupled components
- difficult to test in isolation