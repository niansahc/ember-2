## Research Validation: Retrieval Policy as Code

CIMemories (Mireshghallah et al., ICLR 2026; arxiv:2511.14937) benchmarks whether LLMs appropriately control information flow when drawing on persistent memory across sessions. Key findings:
- Frontier models show up to 69% attribute-level violations — surfacing sensitive information in contexts where it is inappropriate
- Violations accumulate across tasks: from 0.1% on a single task to 25.1% after repeated sampling across 40 tasks
- Privacy-conscious prompting does not solve the problem — models overgeneralize, sharing everything or nothing
- Qwen-3 32B (same model family as Ember's default qwen3:8b) showed the highest violation rate (69%) in the benchmark

Implication for Ember: Ember does not delegate retrieval scoping to the model. Context selection is implemented as explicit code in ContextRetriever and ContextPolicy. The model receives an assembled context packet; it does not search or select from the vault directly. This is a meaningful architectural distinction — cloud-based personal AI assistants with model-managed memory retrieval are structurally vulnerable to the violation pattern CIMemories documents.

Future consideration: as Ember adds sensitive data integrations (health, email, finance in v0.14.0+), the retrieval policy should be reviewed against contextual integrity principles for each new integration. This is a code review question, not a model behavior question.

Reference: Mireshghallah, N. et al. "CIMemories: A Compositional Benchmark for Contextual Integrity of Persistent Memory in LLMs." ICLR 2026. https://arxiv.org/abs/2511.14937

---

## Vault Encryption Architecture (v0.14.0)

Planned five-layer envelope encryption design. Reference implementation: Cryptomator (open source, well-audited).

Layer 1 -- Master key: 256-bit CSPRNG random value. Never derived from passphrase. Generated once at vault creation. Never stored unwrapped on disk.

Layer 2 -- Key derivation: Argon2id derives KEK from user passphrase (minimum 64MB memory, 3 iterations, ~1-2 second unlock time on target hardware). Argon2id chosen over bcrypt and PBKDF2 for memory-hardness against GPU brute force.

Layer 3 -- Key wrapping: AES Key Wrap (RFC 3394) wraps master key with KEK. Authenticated -- detects tampering on unwrap attempt. KDF parameters stored alongside wrapped key (enables future parameter upgrades without re-keying).

Layer 4 -- Recovery: 128-bit random recovery code encoded as BIP-39 12-word list, issued at vault creation. User stores offline (password manager recommended). Provides alternative unwrapping path for master key. Passphrase reset: present recovery code, unwrap master key, re-wrap with new KEK. Zero record re-encryption required.

Layer 5 -- Session cache: after unlock, unwrapped master key stored in keyring (Windows Credential Manager / macOS Keychain) for session duration. Cleared on idle timeout. DPAPI/keyring is a session cache, not primary protection.

Per-record encryption: AES-256-GCM with ROWID-derived nonce. Content fields encrypted; metadata (timestamps, memory_type, state) plaintext by design (same approach as Signal for database metadata). Append-only architecture makes nonce management straightforward -- records are never overwritten.

Security property: passphrase changes are operationally free. Re-wrap master key with new Argon2id KEK. Zero re-encryption of any record content. This is the central advantage of envelope encryption over direct passphrase-derived encryption.