## Research Validation: Retrieval Policy as Code

CIMemories (Mireshghallah et al., ICLR 2026; arxiv:2511.14937) benchmarks whether LLMs appropriately control information flow when drawing on persistent memory across sessions. Key findings:
- Frontier models show up to 69% attribute-level violations — surfacing sensitive information in contexts where it is inappropriate
- Violations accumulate across tasks: from 0.1% on a single task to 25.1% after repeated sampling across 40 tasks
- Privacy-conscious prompting does not solve the problem — models overgeneralize, sharing everything or nothing
- Qwen-3 32B (same model family as Ember's default qwen3:8b) showed the highest violation rate (69%) in the benchmark

Implication for Ember: Ember does not delegate retrieval scoping to the model. Context selection is implemented as explicit code in ContextRetriever and ContextPolicy. The model receives an assembled context packet; it does not search or select from the vault directly. This is a meaningful architectural distinction — cloud-based personal AI assistants with model-managed memory retrieval are structurally vulnerable to the violation pattern CIMemories documents.

Future consideration: if/when Ember adds sensitive data integrations (health, email, finance — currently deferred pending core quality milestone), the retrieval policy should be reviewed against contextual integrity principles for each new integration. This is a code review question, not a model behavior question.

Reference: Mireshghallah, N. et al. "CIMemories: A Compositional Benchmark for Contextual Integrity of Persistent Memory in LLMs." ICLR 2026. https://arxiv.org/abs/2511.14937

---

## Vault Encryption Architecture — Deferred (reference architecture if revisited)

Application-level vault encryption is deferred indefinitely. The shipped path is OS disk encryption detection via `GET /v1/system/disk-encryption` (BitLocker/FileVault/LUKS, shipped v0.15.0). The five-layer envelope design below is preserved as reference material if the decision to defer is ever revisited. Reference implementation: Cryptomator (open source, well-audited).

Layer 1 -- Master key: 256-bit CSPRNG random value. Never derived from passphrase. Generated once at vault creation. Never stored unwrapped on disk.

Layer 2 -- Key derivation: Argon2id derives KEK from user passphrase (minimum 64MB memory, 3 iterations, ~1-2 second unlock time on target hardware). Argon2id chosen over bcrypt and PBKDF2 for memory-hardness against GPU brute force.

Layer 3 -- Key wrapping: AES Key Wrap (RFC 3394) wraps master key with KEK. Authenticated -- detects tampering on unwrap attempt. KDF parameters stored alongside wrapped key (enables future parameter upgrades without re-keying).

Layer 4 -- Recovery: 128-bit random recovery code encoded as BIP-39 12-word list, issued at vault creation. User stores offline (password manager recommended). Provides alternative unwrapping path for master key. Passphrase reset: present recovery code, unwrap master key, re-wrap with new KEK. Zero record re-encryption required.

Layer 5 -- Session cache: after unlock, unwrapped master key stored in keyring (Windows Credential Manager / macOS Keychain) for session duration. Cleared on idle timeout. DPAPI/keyring is a session cache, not primary protection.

Per-record encryption: AES-256-GCM with ROWID-derived nonce. Content fields encrypted; metadata (timestamps, memory_type, state) plaintext by design (same approach as Signal for database metadata). Append-only architecture makes nonce management straightforward -- records are never overwritten.

Security property: passphrase changes are operationally free. Re-wrap master key with new Argon2id KEK. Zero re-encryption of any record content. This is the central advantage of envelope encryption over direct passphrase-derived encryption.

---

## Research Validation: Via Negativa for AI Alignment

Via Negativa for AI Alignment (arXiv, March 2026) — structural analysis of Constitutional AI's negative constraints vs. positive preference data. Core finding: constitutional AI's negative constraints (rules specifying what the model must NOT do) do not contain the sycophancy correlate that positive preference data does. Positive reinforcement from human feedback (RLHF) structurally rewards user-pleasing outputs; negative constitutional constraints structurally do not.

Implication for Ember: Ember's constitution (`config/constitution.yaml`) is a negative-constraint system — 9 principles that define boundaries, failure modes, and trigger conditions for post-draft review. The constitution does not train the model; it governs post-draft behavior. This is architecturally distinct from RLHF-trained preference alignment. Via Negativa provides a structural explanation for why Ember's constitution-as-negative-constraints design reduces sycophancy compared to preference-optimized systems: the review layer catches sycophantic patterns (position_collapse, relational_hedging, preference_compliance) without the system itself having been reward-shaped toward user-pleasing outputs.

This confirms the existing architectural choice. No design changes required. The finding reinforces the decision to keep constitutional review as triggered post-draft review rather than embedding governance into training or prompt-level preference shaping.

Reference: Via Negativa for AI Alignment. arXiv, March 2026.