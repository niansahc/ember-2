# What Replacement Means

**Scoping input for tool integration (Build Order step 9) and agent orchestration (step 10).**
**September 2026. Decisions, not design.**

This records what Ember is being built toward, so the tool layer is designed against the
capability rather than against an imitation of any particular assistant.

---

## 1. The capability, stated generally

The target is not "Ember manages multi-repo software projects." That is one instance. Building
toward the instance would be fitting to a benchmark, the same error as writing code to pass a
test.

The capability is:

- **Hold state across long-running work.** What is in flight, what is blocked on what, what was
  decided and when. Across sessions, not within one.
- **Reach relevant context without being told where to look.** The person should not have to
  remember which project a fact lives in, or open the right container to make it available.
- **Notice contradiction.** When a new claim conflicts with an earlier decision, a stale document,
  or the actual state of things, say so rather than proceeding.
- **Draft the next action.** Not a menu of options. A specific recommendation with reasoning, and
  the artifact needed to act on it.
- **Know what is the person's call.** Distinguish a recommendation from a decision that is not
  Ember's to make, and hand the latter over cleanly rather than pre-empting it.

Software projects exercise all five. So does a manuscript, a shop, a research practice, a garden.
The design target is the capability; the instances are examples.

---

## 2. Memory is one vault, not silos

Project containers that cannot reference each other are a poor fit for how a life actually works.
Writing draws on gardening. A research practice informs a shop listing. The relevant connection is
not predictable in advance, which is precisely why it cannot be pre-partitioned.

**Decision: one vault. Project scope is a retrieval boost, not a wall.**

- Records carry project association where it exists.
- An active project raises the rank of its own records.
- Everything else stays reachable when relevance warrants it.
- Nothing is filtered out on the basis of project membership alone.

Two design questions this leaves open, to be settled in the tool-integration ADR:

- Whether cross-project retrieval should be **visible** in the response. If an unrelated domain
  surfaces in a work session, the person probably wants to know that is what happened.
- How the boost interacts with the existing ranking levers, given that ranking already carries
  type, tier, decay, authorship and lexical terms. A new multiplier added carelessly is how the
  reflection score hardcode happened.

There is no privacy partition inside the vault. The vault is one person's, and all of it is theirs.
Multi-person access, if it arrives, is an access-tier layer above retrieval, not a reason to
fragment the store.

---

## 3. Division of labour

**Ember drafts. The person sends. No computer control.**

This is the existing working pattern and it is retained deliberately. Ember produces the
recommendation and the artifact; the person reviews and acts. The person remains the only actor
with side effects.

Consequences for the tool layer:

- **Read tools first, and they carry most of the value.** Reading state, retrieving across
  projects, searching the web, reading external systems. This is sufficient for the capability in
  section 1.
- **Write governance is deferred, not skipped.** The hard part of the tool research (hard-deny
  gating, destructive-action tiers, meltdown failure modes) is not needed for phase one. It will
  be needed if writes ever land, and the foreclosure flags from that research still apply to how
  the registry is built now.
- **No agent control of the machine.** Ember does not execute, does not edit files, does not act
  on external systems.

---

## 4. Editor integration

Ember already exposes an OpenAI-compatible endpoint. Editor tools that accept an arbitrary
OpenAI-compatible base URL can point at it directly. This is configuration, not a build.

What that gets: local chat and completion in the editor, for work that does not need a frontier
model. What it does not get: an agent that edits files and runs tests. That is a different class of
system and is not in scope.

The realistic division is local for the cheap and the routine, something else for the hard, with
Ember holding the state and drafting the work in both cases.

---

## 5. Two requirements that fall out of this

**Derived-record discipline applies to any project-state layer.**

Holding state across long-running work means writing records that summarise older content. Those
records carry a new timestamp. That is exactly the shape identified in
`docs/research/memory-trust-gap-followup.md` as the trap that produces full reliance on stale
information, and which capability amplifies rather than reduces.

The Recalling Too Well Phase 1 work fixed this class in the reflection and lodestone paths. A
project-state layer would be a new generator of the same record shape. Provenance, authorship and
ranking discipline are requirements at design time, not a later correction.

**Evaluating a plan is a distinct capability from holding a position.**

The existing anti-sycophancy machinery operates on the model's own position under user pushback:
does it capitulate when disagreed with. That is necessary and not sufficient.

What a manager role additionally requires is evaluating something the person proposes and saying it
is wrong, or incomplete, or contradicts an earlier decision, unprompted, before being pushed. The
value of a manager is largely in refusing to rubber-stamp. Nothing in the current constitution
covers this, and it is not an emergent property of a larger model.

Name it as a capability, design for it, and measure it.

---

## 6. Relationship to the shipped product

Ember is built for one person's use. It is released because the need is not unique, not because the
design is a compromise between users.

The practical consequence: decisions are made for the primary deployment, and the public version
inherits them. Capabilities are not withheld because a smaller machine will run them less well.
They ship with honest documentation of what they need, and the person running an 8b-class model
chooses whether to use them.

This is consistent with the existing boundary rule, which keeps hardware-specific and
deployment-specific configuration out of defaults, docs and installer. That rule is about not
assuming the primary deployment's hardware. It is not a rule about restricting what the product can
do.
