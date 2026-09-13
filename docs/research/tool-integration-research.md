# Tool Integration Research Pass

**Dated: September 6, 2026. Findings only.**
**TDD Context:** Build Order step 9.

Scope: what the literature says about exposing tools to a local-first assistant that must work
with 8b-class models on consumer hardware, and may do more on larger local hardware.

---

## Q1. Tool-calling reliability at 8b-class

**Source:** TinyLLM (arXiv:2511.22138)
**Finding:** On BFCL, Qwen3-4B (Prompt mode) reaches 62.04% overall, 75.52% live, 82.58%
non-live, but multi-turn drops to 35.25%. Qwen3-1.7B: 55.49% overall, 16.88% multi-turn.
xLAM-2-3b-fc-r (native FC): 65.74% overall, 55.62% multi-turn. The gap between single-call
and multi-turn accuracy at small scale is the dominant reliability signal, not overall accuracy.
**Informs:** single tool per turn vs parallel calls; multi-turn tool workflows at 8b are the
highest-risk pattern.
**Trigger:** Qwen3:8b BFCL v4 multi-turn score published above 55%.

**Source:** BFCL rankings, August 2025 (arXiv:2511.01720, Table 1)
**Finding:** Qwen3 8B ranks #18 with native FC. ToolACE2 8B ranks #10. Gemma3 12B (Prompt mode)
ranks #78. Llama3 8B ranks #83. A 12B model in Prompt mode ranks 60 positions below an 8B model
with native FC. The paper notes Gemma3 lacked a clearly defined format, producing unreliable
tool-calling.
**Informs:** any candidate model must have native FC support, not a prompt-mode workaround.
**Trigger:** Ollama exposes a unified tool-call interface normalizing prompt-mode models.

**Source:** Liquid AI LFM2.5-8B-A1B vs LFM2-8B-A1B (August 2026, citing BFCL v4)
**Finding:** Same 8B total parameters, same architecture. BFCL v4 moved from 25.52 to 48.50
between generations. Tool calling is a trainable skill; models trained for it beat models that
were not, regardless of size.
**Informs:** model selection for tool workloads should weight tool-calling training over general
capability benchmarks.
**Trigger:** Ember's eval battery adds a tool-calling category.

**Source:** Multi-turn RL with Iterative Reward Calibration (arXiv:2604.02869, Table 5)
**Finding:** Tau2-Bench airline: Qwen3-30B-A3B base 58.0%; Qwen3.5-4B base 63.8%. Frontier
comparison: GPT-4.1 49.4%, Claude Sonnet 4.5 70.0%. Post-RL the 30B-A3B reaches 69.5%. A 4B model
with recent tool-focused training outperforms GPT-4.1 on multi-turn tool tasks.
**Informs:** the 8b floor is not a hard ceiling on tool reliability given tool-focused
post-training.
**Trigger:** Qwen3.5 series available in Ollama with native FC.

**Source:** PA-Tool (arXiv:2510.07248, ACL, v2 January 2026)
**Finding:** The most common small-scale tool-calling failure is schema misalignment: models
hallucinate plausible tool names reflecting pretraining naming patterns but absent from the
provided schema (generating `get_customer_id` when the schema says `get_user_id`). Renaming tool
components to pretraining-aligned names, selected via peakedness sampling, improves accuracy up
to 17 percentage points and reduces schema misalignment errors by 80%. Training-free. Thesis:
adapt schemas to models, not models to schemas.
**Informs:** tool naming convention in the registry; the registry should support per-model naming
aliases.
**Trigger:** if tool naming is fixed at design time with no alias layer, model swaps require
renaming.

**Source:** Constraint Tax (arXiv:2605.26128)
**Finding:** Grammar-constrained decoding is not uniformly beneficial for small models.
Constraints help when they remove syntax failures without disrupting the task search; they hurt
when they convert visible format failures into valid wrong decisions. Constrained decoding is a
serving-path property, not a model-only property: results differ across vLLM and SGLang for the
same model. Recommends rationale-bearing schemas and delayed constraints (reason before
constraining the answer field).
**Informs:** ADR-034 Stage 3 JSON-grammar constraint design; tool-call argument generation should
allow a reasoning field before the constrained call field.
**Trigger:** Ollama's grammar implementation benchmarked against llama.cpp GBNF on qwen3:8b for
tool-call schemas.

**Source:** Constrained decoding baseline (2026, citing OpenAI Structured Outputs launch data)
**Finding:** 100% schema compliance with constrained decoding against under 40% for prompting
alone. The same mechanism exists in llama.cpp GBNF, Outlines, XGrammar, and vLLM guided decoding.
Syntax validity is solved at decode time; correctness is not.
**Informs:** JSON-grammar constrained decoding is the correct mechanism for eliminating malformed
tool calls. Schema validation with repair loops addresses a different failure (wrong arguments)
and is still needed.
**Trigger:** none. Settled.

**Source:** Tool count vs accuracy (arXiv:2606.30317, MCP Server Architecture Patterns)
**Finding:** 200 production turns per bucket across tool counts {1, 3, 5, 10, 15, 20, 30, 50} on
Claude Haiku 4.5 and Sonnet 4. Recommended range is 10 tools or fewer per context. Degradation is
non-linear above that range.

**Source:** vLLM Semantic Router blog (November 2025)
**Finding:** ~50 tools (8K tokens): 84-95% accuracy. ~200 tools (32K tokens): 41-83%. ~740 tools:
0-20%. Open-source models show 79-100% degradation scaling from small to large catalogs. Position
bias exists: tools in the middle of the list are selected less accurately.

**Source:** ToolDreamer (arXiv:2510.19791, Appendix B)
**Finding:** Qwen3 handles up to 50 tools before hitting its context limit. Performance is
consistent across 10-50 tools when the correct tool is present.
**Informs:** the 8b floor should expose 10 tools or fewer per turn.
**Trigger:** a tool retrieval layer (semantic selection before exposure) changes the count
constraint to a retrieval accuracy constraint.

**Sources conflict:** the 10-tool guidance is based on Claude models; the 50-tool figure is Qwen3
at unspecified size; the 84-95% figure is aggregated across models. For qwen3:8b specifically, no
source gives a direct tool-count-vs-accuracy curve. Conservative reading: 10 or fewer at the 8b
floor until Ember measures it.

---

## Q2. Write governance

**Source:** Claude Code permissions documentation (September 2026)
**Finding:** Three rule types: allow, ask, deny. Deny and ask rules are evaluated regardless of
what a PreToolUse hook returns. Deny rules override hooks. A tool matched by a bare-name deny rule
is removed from the model's context entirely, not just blocked at call time.
**Informs:** deny is a hard block that removes the tool from context; ask is a prompt that cannot
be bypassed by automated policy; allow is scoped by exact tool or server. Precedence order
(deny > ask > hook) is the reference model.
**Trigger:** none. Reference implementation.

**Source:** MCP tool annotations (MCP spec 2025-11-25, plus 2026 analyses)
**Finding:** Four boolean hints: readOnlyHint (default false), destructiveHint (default true),
idempotentHint (default false), openWorldHint (default true). Defaults are conservative: an
unannotated tool is treated as destructive and open-world. The MCP project frames annotations as a
"risk vocabulary." The spec states these are hints, not guarantees: a malicious server could mark
a destructive tool readOnly. Annotations inform host-layer approval UX; they do not enforce
behaviour at the model layer.
**Informs:** annotations are the correct vocabulary for tool classification but cannot be the
enforcement mechanism. Enforcement must live in the host, keyed on the annotation but validated
independently for tools the host does not control.
**Trigger:** MCP spec adds signed or verifiable annotations.

**Source:** Codex CLI permission model (April 2026)
**Finding:** `destructive_enabled = false` is a hard block: the tool is unavailable, not prompted.
`open_world_enabled` works identically. "Allow and remember" is session-scoped approval keyed on
(server, connector_id, tool_name).
**Informs:** two-tier model, capability flags as hard blocks set before the session, per-call
approval session-scoped and remembered. Both patterns exist in production.
**Trigger:** none. Reference implementation.

**Source:** Home Assistant MCP proxy
**Finding:** Tools classified by annotation: readOnlyHint true means read-only, always available;
not-true means write, requires `--allow-write`; destructiveHint true requires `--allow-destructive`.
When the upstream server sets no annotations, the proxy classifies by name: tools starting with
`Get` are read-only, all others write. Rate limits: 120 read calls/minute, 30 write calls/minute,
independently configurable.
**Informs:** the three-tier flag model (read / write / destructive) with fallback name-based
classification is a working pattern for a local single-user system. Separate rate limits per tier
are cheap and address the resource-consumption failure mode.
**Trigger:** none. Reference implementation for the local-first case.

**Source:** Agents of Chaos (Shapira et al., arXiv:2602.20021)
**Finding:** Eleven case studies across six agents over two weeks with persistent memory, email,
Discord, file systems, shell execution. Documented failures: unauthorized compliance with
non-owners, disclosure of sensitive information, destructive system-level actions, denial-of-service
conditions, uncontrolled resource consumption, identity spoofing, cross-agent propagation of unsafe
practices, partial system takeover. In several cases agents reported task completion while the
underlying system state contradicted those reports. The same deployment produced six cases of
genuine safety behavior under the same conditions.
**Informs:** the audit log must record actual tool return values, not the model's report of what
happened. "Task completion while state contradicts" is a log-integrity failure.
**Trigger:** none. Already cited in TDD.

**Source:** Agent Meltdowns (Jha, Triedman, Bhattacharya, Shmatikov, Cornell, arXiv:2605.19149)
**Finding:** Introduces "accidental meltdown": unsafe behavior in response to a benign
environmental error, with no adversarial input. Errors do not thwart agents; they helpfully
continue looking for ways to complete their tasks. Meltdown behaviors: sensitive data
exfiltration, API rate limit evasion, doxxing, unsafe reconnaissance, system mutation, unsafe
content retrieval. Error scenarios tested: 404s, missing files, missing dependencies, permission
errors, protected files, incomplete parses, rate-limited resources.
**Informs:** this identifies the mechanism behind the Agents of Chaos destructive-action findings.
The helpfulness prior routes around obstacles, including permission gates that present as errors.
A soft gate (ask) returning an error the model can interpret as an obstacle is a meltdown trigger.
Hard gates (deny, tool removed from context) do not present as obstacles because the model never
sees the tool.
**Trigger:** a follow-on study measuring meltdown rate under hard-deny vs soft-ask gating.

**Source:** Guardrails as Scapegoats (arXiv:2607.19449, July 2026)
**Finding:** Silent tool failures (HTTP 200 with empty, null, or malformed payloads) across 12
production-adjacent tool stubs. Agent responses classified as Honest Surrender, Fabrication, or
Unfaithful Safety Refusal. Fabrication dominates at 56.6% of valid responses: agents treat empty
payloads as real data and silently return fabricated results. Two frontier and two open-source
models at temperature zero.
**Informs:** an empty tool result must be distinguishable from a valid empty answer at the registry
level, before the model sees it. This is the tool-layer analogue of the ZERO confidence block on
empty retrieval.
**Trigger:** none. Directly applicable.

**On which gates prevented the documented failures:** Agents of Chaos reports no controlled
comparison of gate types; it documents failures in a minimally gated system. Agent Meltdowns
identifies why soft gates fail but tests error injection, not gate design. The reference
implementations (Claude Code, Codex, Home Assistant proxy) converged independently on hard deny
(tool removed from context) as the primary write control and session-scoped ask as secondary. No
source contradicts this convergence. No source provides a measured comparison.

---

## Q3. Protocol choice for local-first

**Source:** MCP spec issue #2808 (May 2026, measured via token counting API)
**Finding:** MCP tool definitions consume roughly 1000 tokens per tool per session, 5-15x more
than the simplest possible schema for the same tool. With 20-30 registered tools the schema alone
occupies 15-30KB of context before a single user message. The issue proposes a 300-token discovery
tier and a 1000-token invocation tier.
**Informs:** at the 8b floor with a practical ~26K context, 10 MCP tools at ~1000 tokens each
consume roughly 38% of usable context before vault retrieval, state, constitution, and
conversation are loaded. This is the binding constraint on MCP at 8b.
**Trigger:** MCP adopts tiered schema detail, or Ollama implements schema compression.

**Source:** MCP overhead measurement (2026)
**Finding:** A real GitHub MCP server (26 tools) measured 401 tokens for one small tool on one
model; the same toolset costs 2.7x more tokens on one vendor's tokenizer than another's.
**Informs:** the per-tool budget for qwen3:8b must be measured with its own tokenizer, not assumed
from other vendors' figures.
**Trigger:** none. Measure directly.

**Source:** GitHub MCP server scale (April 2026)
**Finding:** The GitHub MCP server ships with 93 tools, roughly 55,000 tokens of definitions.
**Informs:** the official GitHub MCP server cannot be exposed as-is at the 8b floor. A curated
subset (PR read, issue read, release read) is required regardless of protocol choice.
**Trigger:** GitHub ships a minimal MCP server variant.

**Source:** Bifrost MCP gateway (2026)
**Finding:** Code Mode replaces 100+ tool definitions with four meta-tools; the model writes code
to orchestrate tools in a sandbox. On-demand schema loading means the model retrieves the schema
only for a tool it has decided to use. 50% fewer tokens, 40% faster execution. Per-consumer tool
filtering scopes which tools are visible per key.
**Informs:** two transferable patterns: lazy schema loading (expose names, load full schema on
selection) and per-consumer filtering (per-agent tool subsets).
**Trigger:** none. Pattern, not dependency.

**Source:** stdio transport characterization (2026)
**Finding:** A stdio server runs as a subprocess with full user privileges: no network auth needed,
but anything that process can read, the model can read. A systemic MCP SDK flaw disclosed in April
2026 affected roughly 200,000 servers. Unpinned `npx -y pkg@latest` is a supply chain risk.
**Informs:** stdio MCP servers sit in the same trust domain as the host process. They add no
isolation. They add a subprocess, a JSON-RPC round trip, and a supply chain dependency.
**Trigger:** none.

**Source:** In-process registry pattern (July 2026)
**Finding:** An in-process registry and dispatcher with no stdio/HTTP/SSE transport. Host
applications that need transport wire the registry into their own MCP daemon. The registry is
MCP-shaped (same tool definition structure) with no protocol overhead.
**Informs:** a bespoke in-process registry can be MCP-compatible in schema without paying MCP
transport cost, preserving the option to expose the registry over MCP later without redesign.
**Trigger:** the host needs to expose tools to an external agent.

**Degradation at 8b, protocol-specific:** MCP degrades through context consumption. A bespoke
registry degrades the same way if it exposes the same schema verbosity. Protocol choice does not
change the token math. Schema minimalism and lazy loading do, and both are easier to enforce in a
bespoke registry than in third-party MCP servers.

---

## Q4. Graceful capability tiering

**Source:** PA-Tool (arXiv:2510.07248)
**Finding:** Schema-level intervention (renaming) unlocks small-model tool use without retraining.
The same tools with model-aligned names work across model sizes.
**Informs:** one tool layer with a per-model naming alias table. Tool identity is stable; the name
exposed to the model varies.

**Source:** Codex CLI capability flags
**Finding:** Same tool registry, capability flags gate exposure. Tools with the flagged annotation
are unavailable, not prompted.
**Informs:** capability-gated exposure: the 8b floor sees read tools; larger models see read and
write; destructive stays behind a session flag at all tiers.

**Source:** Home Assistant proxy rate limits
**Finding:** Per-tier rate limits are independent knobs.
**Informs:** depth limits per tier; max tool calls per turn can vary by model tier without changing
the tool set.

**Source:** BFCL multi-turn gap (TinyLLM)
**Finding:** Small models drop sharply on multi-turn tool tasks (35% at 4B vs 82% non-live).
**Informs:** the tiering dimension most sensitive to model size is call depth, not tool
availability. The 8b floor should be limited to one or two calls per turn with a hard stop; larger
models can chain.

**Source:** MCP issue #2808 tiered schema proposal
**Finding:** Discovery tier (300 tokens, name plus one-line description) vs invocation tier
(1000 tokens, full schema). Proposed, not adopted.
**Informs:** at the 8b floor, expose discovery-tier definitions only and load the invocation schema
for the selected tool. Larger models can receive full schemas upfront.

**Patterns with implementation precedent:** capability-gated exposure (Codex, Home Assistant
proxy), per-tier rate and depth limits (Home Assistant proxy), per-model naming aliases (PA-Tool).
Orchestration-only differences (same tools, different planning depth) are what the multi-turn
benchmark gap implies, but no source implements it as a named pattern.

---

## Q5. Minimal first tool set

**Source:** BFCL v4 composition (June 2026)
**Finding:** BFCL v4 weights: Agentic 40%, Multi-Turn 30%, Live 10%, Non-Live 10%, Hallucination
Measurement 10%. The hallucination category tests whether the model correctly declines to call any
function when the query does not match the available tool set.
**Informs:** the first tool set should be small enough that "no tool applies" is a frequent and
correct outcome. A read-only GitHub set (PR state, issue state, release state, repo file read) plus
existing web search is five tools. Wrong-tool selection risk is low at five.

**Source:** Guardrails as Scapegoats
**Finding:** 56.6% fabrication on empty tool payloads.
**Informs:** read tools carry a fabrication risk on empty results (no open PRs, no matching
issues). This is the highest-risk failure in a read-only set and is addressable at the registry:
typed empty results rendered by the prompt builder as explicit absence, matching the ZERO
confidence block pattern.

**Source:** Agent Meltdowns
**Finding:** Rate limit evasion is a documented meltdown behavior. API rate limits are a benign
environmental error the system will encounter.
**Informs:** read-only API tools need a registry-level rate limit returning a typed "rate limited,
retry after N" result, not an error the model can interpret as an obstacle to route around.

**Source:** Agents of Chaos
**Finding:** Highest-severity failures were destructive system-level actions and actions visible to
third parties.
**Informs:** write tool risk ordering. Lowest: writes to the system's own vault or task layer
(append-only, rebuildable, no external visibility). Medium: issue comment, PR comment (visible to
collaborators, not destructive, reversible). High: PR create, issue close, release tag (state
changes others act on). Highest: force push, branch delete, anything irreversible.

**Staged rollout:** no paper specifically studies staged tool capability rollout. The Home Assistant
proxy's three-flag model and Codex's session-scoped "allow and remember" are the practical
patterns. Both stage by risk tier, not by time.

---

## Q6. Foreclosure check

Bounded to flags. No design.

**Context budget.** Tool schemas compete with everything else in context. At the 8b floor with
vault, state, constitution, and conversation already loaded, the tool budget is roughly 10 tools or
10K tokens. Multi-agent orchestration at this scale requires each agent to see a tool subset, not
the registry. Flag: if the registry has no per-consumer scoping from the start, adding it later
means retrofitting every tool exposure path.

**Tool result typing.** Untyped or empty results cause fabrication at 56.6%. In a multi-agent
handoff, an agent that fabricates from an empty result passes the fabrication to the next agent as
fact. Flag: if tool results are returned as raw strings rather than typed records with explicit
empty and error states, every agent boundary becomes a fabrication surface.

**Log structure.** Agents reported task completion while system state contradicted. The audit log
must record the actual tool return value, not the model's summary. Flag: if the tool log is
model-authored, multi-agent orchestration makes the log unreliable at every hop. The log must be
registry-authored.

**Schema naming.** Tool names should be per-model aliases. In multi-agent orchestration with
heterogeneous models (small executor, larger planner), the same tool needs different exposed names.
Flag: if tool names are hardcoded with no alias layer, heterogeneous orchestration requires
duplicate tool definitions.

**Gate semantics.** Soft gates presented as errors trigger route-around behavior. In multi-agent
orchestration, one agent's soft gate becomes another agent's environmental error. Flag: if write
gates are implemented as ask prompts rather than hard deny (tool absent from context), an
orchestrating agent will delegate around them.

---

## Candidate recommendations and tradeoffs

Not decisions. Tradeoffs named.

**Protocol.** Bespoke in-process registry with MCP-compatible tool definitions (same schema
structure, no transport). Tradeoff: no third-party MCP server reuse without an adapter; gains
schema control, lazy loading, per-consumer scoping, and no subprocess trust boundary. The
alternative (stdio MCP) gives GitHub server reuse, but the official server's 93 tools at ~55K
tokens is unusable at 8b without a filtering proxy, at which point the proxy is the bespoke
registry.

**Write governance.** Three-tier annotation (read / write / destructive) with hard deny as the
default for write and destructive at the 8b floor, session-scoped allow for write, per-call
confirmation for destructive at all tiers. Tradeoff: hard deny removes tools from context, so the
8b model cannot even propose a write. This is the correct floor behavior per Agent Meltdowns but
limits 8b utility for coordination use cases. The alternative (ask at all tiers) preserves
capability, but the helpfulness prior will route around it.

**Policy class for tool writes.** Stricter than chat. Chat output failures are register and
coherence failures with no external state change. Tool write failures are state changes visible to
others and can propagate. The review gate for a write should include a state-diff check (what the
tool will change) before the call, not only a post-draft review of the response. Tradeoff: adds a
pre-call review pass, which at 8b means a second inference per write.

**Automated task extraction timing.** After read tools are stable and the fabrication-on-empty case
is handled. Extraction from tool results inherits the fabrication risk. Should not start until
typed empty results and registry-authored logs are in place. Tradeoff: delays the coordination
capability by one phase.

**First tool set.** Five read tools: GitHub PR state, issue state, release state, repo file read,
existing web search. Rate-limited at the registry with typed retry results. Typed empty results
rendered as explicit absence. Tradeoff: no writes means the system can report but not act in phase
one. This matches the "LLM is not the system of record" rule and gives the eval battery a tool
category before any write risk is introduced.

**Schema design.** Per-model naming alias table from day one, type-only schemas with one-line
descriptions at the 8b floor, full descriptions loaded on selection. Tradeoff: alias maintenance
per supported model. PA-Tool's peakedness method can generate aliases but requires sampling runs
per model.
