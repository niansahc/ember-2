## Research Validation: Retrieval Policy as Code

CIMemories (Mireshghallah et al., ICLR 2026; arxiv:2511.14937) benchmarks whether LLMs appropriately control information flow when drawing on persistent memory across sessions. Key findings:
- Frontier models show up to 69% attribute-level violations — surfacing sensitive information in contexts where it is inappropriate
- Violations accumulate across tasks: from 0.1% on a single task to 25.1% after repeated sampling across 40 tasks
- Privacy-conscious prompting does not solve the problem — models overgeneralize, sharing everything or nothing
- Qwen-3 32B (same model family as Ember's default qwen3:8b) showed the highest violation rate (69%) in the benchmark

Implication for Ember: Ember does not delegate retrieval scoping to the model. Context selection is implemented as explicit code in ContextRetriever and ContextPolicy. The model receives an assembled context packet; it does not search or select from the vault directly. This is a meaningful architectural distinction — cloud-based personal AI assistants with model-managed memory retrieval are structurally vulnerable to the violation pattern CIMemories documents.

Future consideration: as Ember adds sensitive data integrations (health, email, finance in v0.14.0+), the retrieval policy should be reviewed against contextual integrity principles for each new integration. This is a code review question, not a model behavior question.

Reference: Mireshghallah, N. et al. "CIMemories: A Compositional Benchmark for Contextual Integrity of Persistent Memory in LLMs." ICLR 2026. https://arxiv.org/abs/2511.14937