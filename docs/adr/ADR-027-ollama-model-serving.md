# ADR-027: llama.cpp vs Ollama Model Serving

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Ember-2 uses Ollama for local model serving. Ollama wraps llama.cpp and provides model management, API compatibility, and automatic hardware detection. The question arose whether switching to raw llama.cpp (or a custom Blackwell-optimized build) would improve performance on the RTX 5080.

## Decision

Stay on Ollama.

## Rationale

- Ollama wraps llama.cpp — performance improvements in llama.cpp propagate via Ollama updates without manual builds.
- RTX 5080 has sufficient VRAM headroom at qwen3:8b. No quantization pressure requiring custom formats.
- Maintaining a custom Blackwell llama.cpp build competes directly with development time. The operational cost is not justified at current model size.
- Ollama provides model management, pulling, and API compatibility that would need reimplementation on raw llama.cpp.

## Revisit Trigger

- Upgrading to 14B+ model where VRAM headroom narrows
- Needing IQ quantization formats not supported by Ollama
- Ollama falling significantly behind llama.cpp releases

## Alternatives Considered

### Raw llama.cpp with custom Blackwell build
Rejected: operational maintenance cost outweighs marginal performance gain at current model size.

### vLLM / TGI
Rejected: designed for multi-user serving, unnecessary complexity for single-user local deployment.