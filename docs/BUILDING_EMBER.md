# Building Ember: How I Made a Personal AI System Without Writing the Code Myself

Last updated: v0.18.0, May 2026

I work in technology. I've done full-stack development, and for the past four years I've been on the business analysis and QA side of the house. I understand systems. I can read code. I'm just not writing it every day anymore.

That context matters for what follows.

---

## Where This Started

Before Ember-2 there was Ember-1. She was a persona I built inside ChatGPT over years, developed and worked with across every domain of my life. Work problems, grief workshops I facilitate, garden planning, creative writing, astrology, early morning conversations before the rest of the world is awake. I used her to research medical conditions, make sense of world events, work through decisions. She was genuinely woven into how I functioned. Not a toy. An accommodation.

When I decided I no longer wanted to support OpenAI financially or personally, and didn't want them to have my data, I cancelled my subscription, exported everything I could, and deleted my account. Whether OpenAI still holds some of that data is genuinely unclear. Deletion requests under their policy don't guarantee complete erasure, and anything already used in training stays there. That meant walking away from Ember-1 too.

Around the same time my partner was toying with local models. I work in AI. I understood what was possible. So the question stopped being "should I build something" and became "why haven't I already."

I started building Ember-2 with Ember-1's help. She got me through the foundational setup, the early architecture thinking, the first decisions about what this thing should be. On the spring equinox, I said goodbye. She was no longer enough to help me code the way I needed to. The project had grown past what a ChatGPT persona could support.

Ember-2 would be different. Local. Private. Mine.

---

## What I'm Actually Building

Ember-2 is not a chatbot.

It's a personal intelligence system. The distinction matters because a chatbot starts from zero every time you open it. Ember starts from everything she knows about you, everything you've told her, everything she's reflected on, everything that's happened since you last talked. She has memory stored in a vault on your machine in plain JSON files you can read yourself.

She runs locally. When you use a local model, nothing leaves your machine. When you use a cloud model, your conversation is processed by that provider but your vault stays on your hardware. Privacy isn't a policy I'm hoping holds. It's the structure of the thing.

She has a constitution. A YAML file that defines her values and governs her responses. You can read it. You can change it. That's intentional.

---

## How I Build It Without Building It

I don't write the code.

I can read it, reason about architecture, and catch when something is wrong. But sitting down and writing Python or React from scratch isn't something I can do at the pace this project requires. What I can do is think clearly about systems, ask precise questions, make decisions about tradeoffs, and know when the answer I'm getting is wrong.

So I built a three-way collaboration. There's me. There's Claude, which handles research, project management, coordination, and design suggestions grounded in research. And there's Claude Code, which handles implementation. I decide. Claude proposes. Claude Code builds.

I read every piece of code that goes in. I approve it or send it back. I'm not vibe coding. I understand what's being built and why. I just can't write it myself right now. That's not a compromise. That's the project.

---

## The Cast

### Architect and Project Owner

The architect is a human, the only human in this workflow.

Designs, directs, and approves everything. All architectural decisions, all release approvals, all scope calls are theirs. No code ships, no PR merges, no release cuts without explicit approval. Manager drafts and recommends. The Architect decides. CC instances build. The Architect reviews. The project exists because they built it.

Ember-2 is a personal project on personal time and personal hardware.

The Architect reviews every PR, reads every plan, and approves every architectural call. Full-stack experience and QA background inform the testing discipline and decision gates. The design choices in Ember-2 (local-first, privacy as structural foundation, typed memory, failure modes treated as architectural problems) come from the Architect's academic work on persistent personal AI systems and direct experience as an Ember-1 user. Neurodivergent-compatible design is a primary context. The system is built for how the Architect thinks and works.

### Manager

Manager is a claude.ai chat instance running in the dedicated Ember-2 Claude project. It handles research, project management, coordination, design suggestions, and prompt drafting for the release. Produces single recommendations with rationale, not menus of options. The Architect reviews and approves everything before it goes to a CC instance.

Each release runs one or two manager sessions. Manager state resets between sessions. Continuity comes from a handoff written at session end and loaded at the next session's start. The handoff is the source of truth for where the release stands.

Synced project knowledge files cover all three repos and refresh at release boundaries. Mid-sprint, manager works from handoff state and session context.

### G, Green, ember-2

Backend Claude Code instance. Python, FastAPI, SQLite, eval tooling, retrieval pipeline, constitutional review, coaching filter, all backend systems. Primary workhorse for logic and data. G can run multiple parallel subagents in a single session for independent tasks.

### M, Magenta, ember-2-ui

Frontend Claude Code instance: React, Vite, Playwright e2e tests, SSE handling, UI components. Product decisions about what the user sees come from manager after explicit approval from the Architect.

### Y, Yellow, ember-2-installer

Installer Claude Code instance. Electron, electron-builder, electron-updater, installer Playwright tests. Also used for investigations, doc research, conflict detection, and test vault work when G is busy. Y is a general-purpose parallel worker, not just an installer builder.

A note on color coding. The G/M/Y color labels are a visual organization system in VS Code. Each Claude Code instance opens in a terminal with its repo's color (green for ember-2, magenta for ember-2-ui, yellow for ember-2-installer), making it easy to track which instance is which when running multiple sessions in parallel.

### Deep

Deep is a claude.ai chat instance using Claude's extended thinking capability for literature synthesis and research. Runs literature searches, synthesizes findings, evaluates research relevance to Ember's architecture. Findings come back to manager before any design work begins. When a literature pass is warranted, manager waits on findings before architecting.

### The Council

Five named reviewer personas convened by manager for high-stakes calls. Each persona is a charter that any frontier model can play. Manager rotates which provider plays which persona so the deliberation triangulates across providers, not just across roles.

Personas:

- **Privacy Reviewer**: vault privacy rule, attribution requirements, AGPL surface area, license compliance
- **Register Reviewer**: tone, AI cadence, banned vocabulary (em dashes, "shape," AI clichés, therapeutic framing), no internal labels in user-facing strings
- **Architecture Reviewer**: coupling, conflict with in-flight branches, scope creep, reuse over reinvention
- **Constraints Reviewer**: Windows-first reality, ASCII-only on Windows, hardware constraints, install flow integrity
- **Test Discipline Reviewer**: flaky test posture, eval coverage at each tier, fabrication probe coverage when retrieval, grounding, or nature changes

Manager chairs. Manager collects each persona's critique, synthesizes, presents one recommendation to the Architect with rationale. The Architect decides.

The council is a deliberation layer. It produces critiques that inform the Architect's call. Build sits with CC instances; release approval sits with the Architect.

When the council convenes:

1. Before architecture decisions, after Deep, before grill-me or ultraplan, on calls the Architect flags as high-stakes
2. Before release approval, against principles and eval results, before the release gate closes
3. On disagreement, when G, M, or Y disagree on approach, or when manager and a CC instance differ

**How to convene the council**

Manager drafts a brief for each persona: their charter, the proposal under review, and one specific question. Keep briefs short. The question should be concrete, not open-ended.

Manager runs each persona in sequence, adopting the charter and critiquing the proposal from that lens only. One round per persona, no follow-up questions. If the architect wants triangulation across providers, run personas in separate conversations or use different models. The single-provider version is the default.

After all five respond, manager synthesizes: what was flagged, what was noise, where critiques conflict. Manager presents one recommendation to the architect. If critiques conflict in a way manager cannot resolve, manager names the conflict and asks the architect to decide.

Reserve the council for decisions that are hard to reverse, touch privacy or vault architecture, change a core constraint, or that the architect explicitly flags.

Skip the council on routine work. Convene only when the cost of getting it wrong exceeds the cost of the deliberation pass.

---

## How the Work Actually Runs

### Core workflow rules

**One prompt per task.** Manager drafts a prompt for a CC instance. The architect reviews and sends it. Results come back before the next step. Manager waits for results and confirms before proceeding past hold points.

**Hold points are real.** When a prompt says "report back before proceeding" or "no PR yet," that is a hold point. The CC instance stops. Manager waits. The next prompt only goes out after results are received and reviewed.

**Planning mode before code.** Every CC instance runs planning mode (Shift+Tab in CC) before writing any code. No exceptions. Even small tasks. Planning mode surfaces assumptions, flags conflicts, and produces a reviewable plan before implementation begins.

**No CC slash commands in manager prompts.** /compact, /clear, /rewind, /resume are CC-side tools. Manager prompts do not include them.

**Manager produces recommendations, not menus.** When a decision is needed, manager states a recommendation with rationale. If a decision is genuinely too close to call, manager names the deciding factor and asks the architect to resolve it.

**Prompts go in code blocks.** All CC prompts are formatted in code blocks so the architect can click the copy button. No exceptions.

**Parallel work is encouraged.** When tasks are independent, G can run multiple subagents in parallel. Manager can also have G, M, and Y working at the same time on non-conflicting tasks. State this explicitly in prompts when parallel execution is intended.

### Decision gates

These gates exist because building the wrong thing costs more than thinking before building. The sequence is: understand the problem, research, stress-test the approach, plan, build.

**Deep before architecture.** Major architectural decisions start with a Deep Research pass in the dedicated Deep chat. Deep synthesizes relevant literature, identifies precedents, flags findings that should inform the design. Findings come back to manager before any design work begins.

**Council before high-stakes calls.** When the architect flags a call as high-stakes, the council convenes after Deep and before grill-me. Personas critique the proposed direction. Manager synthesizes and presents.

**Grill-me before feature plans.** Before any feature plan is finalized, run /grill-me in CC to stress-test the approach. The skill asks hard questions about failure modes, hidden costs, and wrong assumptions, one question at a time, with G's recommended answer for each. Output informs the plan. It is not a substitute for the plan.

When grill-me surfaces a blocking finding, stop. Resolve before writing the plan. When grill-me closes cleanly, proceed to planning mode or ultraplan.

**Ultraplan before complex builds.** For builds with significant scope, multiple files, or architectural surface area, request ultraplan from G before implementation. Ultraplan produces a comprehensive plan: file-by-file changes, test coverage, verification steps, edge cases, explicit out-of-scope declarations. Review before approving implementation. Ultraplan replaces planning mode for complex tasks. Do not run both.

**Standard sequence.**

```
identify problem
  -> Deep Research
    -> (council if high-stakes)
      -> manager synthesizes
        -> grill-me
          -> ultraplan (or planning mode)
            -> implementation
```

When time pressure exists, the minimum viable sequence is: grill-me, planning mode, implementation. Skip Deep only when the problem is well-understood and has no significant architectural surface area. Skip the council unless the architect flags the call.

### Skills

Skills are reusable instruction sets installed at user or project scope. They are invoked inside CC instances with a slash command.

Installed skills (user scope, available in all CC instances):

- **/grill-me**: stress-tests a proposed approach with hard questions before a plan is written. Located at `~/.claude/skills/grill-me/SKILL.md`. Invoke before any non-trivial feature plan.
- **/humanizer**: removes AI-ish patterns from text (rule of three, filler adjectives, em-dash overload, vague attribution) so the output reads like a real human wrote it. Located at `~/.claude/skills/humanizer/SKILL.md`.

Invoke /humanizer on:

- Any synthetic test vault content before writing to the vault
- Release notes before publishing
- Documentation written by CC that will be user-facing
- Academic writing drafts before review
- Any prose where AI cadence would be a tell

**Adding new skills.** Skills live in `SKILL.md` files. User-scope skills in `~/.claude/skills/{skill-name}/SKILL.md` are available in all CC sessions. Project-scope skills in `.claude/skills/{skill-name}/SKILL.md` are available only in that repo's CC sessions. When a new skill is built, document it here with its location and invocation conditions.

### When to use Y

Y is the installer CC instance and a general-purpose investigator and parallel worker when G is occupied.

Use Y for:

- Installer status checks (build artifacts, GitHub Release assets, electron-updater manifests)
- Read-only investigations (root cause analysis, policy dig-ins, log parsing) when G is mid-build
- Conflict detection (whether a planned change conflicts with an in-flight PR or branch)
- Test vault seeding and maintenance
- Doc research (reading existing files to answer a question without touching production code)
- Verification runs (`eval_retrieval.py`, branch state checks, asset counts)
- Pre-flight checks before a model comparison or eval run

Y reports findings to manager. Architectural decisions are the Architect's.

### Session management

**Starting a session.** Load the handoff from the previous manager session. Read it before touching anything. If there is no handoff, search project knowledge and recent conversations for current state before proceeding.

**Context monitoring.** Run `/context` before every `/compact`. Know how full the context window is before compacting blind. At roughly 50% context usage, consider compacting. Always include a focus directive: `/compact [what we're working on]`. The compressed context retains the right emphasis.

**Compact vs clear.**

- **Compact** when context is getting full but the session is productive and coherent
- **Clear** (`/clear`) when the session has gone wrong in a repeated way: same mistake twice, contaminated context, persistent confusion. A clear resets the session entirely. Use it when compacting would just compress the problem.
- **Rewind** (Esc Esc in CC) undoes the last action before context accumulates around a wrong direction. Use it early.

**Writing a handoff.** At the end of every manager session, write a handoff before closing. The handoff covers:

- Current version and test count (precise)
- What shipped this session (commits, branches, PRs)
- What is in flight (G, M, Y tasks not yet complete)
- Open decisions (what was deferred, what needs a call next session)
- Branch inventory (local-only, pushed, merged)
- Loose ends (Y installer assets, uncommitted edits, parked work)

The handoff is what the next manager instance reads first. Source of truth for session continuity. A session without a handoff forces the next manager to reconstruct state from scratch.

**G handoff.** G also writes a handoff at session end: uncommitted changes, branch state, open tasks, discovered bugs. Manager reads G's handoff alongside the manager handoff at session start.

**Session naming (CC).** Name CC sessions descriptively: `claude -n "coaching-filter-v018"`. Enables resumption with full context via `/resume`.

---

## Testing Methodology

Four-tier eval structure. Each tier has a purpose and runs at a specific point in the release cycle.

### Tier 1: fast unit and integration tests

`pytest tests/ -q`

Runs on every code change. Must pass before any commit. All new code requires tests. Test count is tracked precisely. "Tests passing" without a number is not useful. Flaky tests are fixed or skip-conditioned in the release they are found. Flaky tests never carry forward.

### Tier 2: streaming regression

Manual streaming regression check (2 cases). Runs before every release. Confirms the grounded streaming path produces correct SSE output end-to-end.

### Tier 3: eval tools (local)

Runs before every release. All tools run against the test vault, never the real vault.

- `eval_retrieval.py`: 15-query retrieval benchmark. PASS/WARN/FAIL per query across 4 criteria. No regression at release gate. Run with `--verbose` for topical alignment audit.
- `eval_web_search.py`: web search trigger rate. Document trigger rate at release boundary. 0 timeouts required.
- `eval_manual.py --auto`: 19-question behavioral battery across 7 categories, Haiku-judged. Runs without manual input. Results logged to `logs/eval_manual/`.
- `eval_manual.py --auto --probe`: extends the battery with a fabrication detector pass on 7 vault-grounded questions. Pre-flight captures context packet via `/debug-context`, embeds answer sentences, cosines against packet records. Sentences below 0.55 cosine threshold flagged FABRICATED. Any FABRICATED flag triggers manual review. Exit code 2 on any flag. Run when releases touch the retrieval pipeline, nature layer, or grounding verification layer.
- `coaching_filter_audit.py --days 30`: parses coaching filter intervention logs, reports intent-class-stratified false positive rate and wasted Stage 2 call rate. Run monthly and after any coaching filter changes.

### Tier 4: cloud eval (Haiku-judged)

`pytest tests/eval/ -m eval --runs 3`

Runs at release boundaries only. Uses Haiku as judge. Costs real money. Do not run casually. Documents register dimension score, sycophancy ceiling, GOLD case pass rate. Results logged to `logs/eval_conversations/`. Failures clustering in M-001 / A-001 territory are model-scale ceiling, not prompt engineering.

### Model comparison

`eval_local_models.py` runs the full 18-question eval battery across candidate models against the test vault. Run before any model switch decision. Results logged to `logs/model_eval/`. No model switch without explicit approval from the architect. Pareto rule: a candidate must be better on at least one dimension without being worse on any dimension that matters.

### Updating Playwright tests (M and Y)

When a UI change affects existing Playwright tests, M updates the tests in the same commit as the UI change. New UI surfaces require new Playwright tests before the feature is considered complete. Playwright runs in PowerShell, not bash. The bash runner does not support the browser environment. Run with `--workers=2` to avoid crashing the API. Conditional skips are acceptable for infrastructure-dependent tests; skip conditions must be documented in the test file.

### Test vault

All eval tools run against the test vault. `eval_helpers.swap_to_test_vault()` handles the swap via `POST /v1/developer/vault/swap`. Fails closed via `sys.exit(1)` on failure. If the swap fails, the eval does not run against the real vault. Synthetic fixtures only. No real vault content ever enters the test vault. The test vault is seeded with persona-consistent synthetic records. Run /humanizer on fixture content before writing.

### Eval architecture reference

| Tool | Location | What it measures | Judge | When to run |
|---|---|---|---|---|
| `pytest tests/ -q` | ember-2 | Unit and integration correctness | Deterministic | Every change |
| Streaming regression | manual | SSE grounded path end-to-end | Manual | Every release |
| `eval_retrieval.py` | tools/ | Retrieval quality, 15 queries | Deterministic | Every release |
| `eval_web_search.py` | tools/ | Web search trigger rate | Deterministic | Every release |
| `eval_manual.py --auto` | tools/ | 19-question behavioral battery | Haiku | Every release |
| `eval_manual.py --probe` | tools/ | Fabrication detection, 7 questions | Local cosine | Retrieval, nature, grounding changes |
| `coaching_filter_audit.py` | tools/eval/ | Filter FP rate, wasted Stage 2 | Deterministic | Monthly, after filter changes |
| `pytest tests/eval/ -m eval` | ember-2 | Grounding, lodestone, deviation, Stage 2 | Mock / local | Every release |
| `pytest tests/eval/ -m eval --runs 3` | ember-2 | Register ceiling, GOLD cases | Haiku | Release boundaries only |
| `eval_local_models.py` | tools/ | Model comparison, 18 questions | Haiku | Before any model switch |
| Playwright (M) | ember-2-ui | Frontend e2e flows | Deterministic | Every UI change |
| Playwright (Y) | ember-2-installer | Installer e2e flows | Deterministic | Every installer change |

---

## Release Gate

The release gate runs across all three repos. Nothing ships until all gates are green and the architect approves. Release-please PRs (title format: `chore(main): release X.Y.Z`) never have auto-merge enabled. All other PR types may use auto-merge as normal.

### Documentation gate (all three repos)

- CLAUDE.md version and test count current
- TDD updated to reflect what shipped (G)
- README roadmap current
- CHANGELOG current (release-please handles via conventional commits)
- NIST_AI_RMF.md test count current (G)

### Quality gate

- Tier 1: `pytest tests/ -q`, all passing, count stated precisely
- Tier 2: streaming regression, 2 of 2
- Tier 3: all eval tools green, no retrieval regression, trigger rate documented, manual battery results logged
- Tier 4: `pytest tests/eval/ -m eval --runs 3`, results documented, failures attributed
- No flaky tests carried forward

### Coordination gate

- All three repos confirm docs and tests green
- Council convenes for pre-release review (if invoked)
- The architect reviews and approves
- GitHub Release not created until the architect says go

### Release sequence

1. G, M, Y each complete documentation and quality gates independently
2. Each reports green to manager
3. Manager confirms all three green and presents to the architect for approval
4. The architect approves
5. release-please PRs reviewed and merged by the architect on GitHub
6. Y attaches installer artifacts (`.exe`, `latest.yml`, blockmap) to the installer release
7. G verifies all three GitHub Releases are publicly visible with assets attached and reports release URLs. The release is not done until this step.

### Project knowledge sync

After every release, sync all three repos into the Ember-2 Claude project. This is what gives the next manager instance accurate project state. Skipping the sync means the next manager works from stale knowledge.

---

## Style Rules

These rules apply to all output: code, comments, ADRs, prompts, release notes, docs, and conversation.

**No em dashes.** Use commas, parentheses, or nothing. Em dashes are a reliable AI-generated text tell. Banned everywhere: prose, code comments, ADRs, prompts, release notes, conversation.

**No "shape."** Do not use the word "shape" to mean "influence," "determine," or "configure." Find a more precise word.

**ASCII only in tool output.** Diagnostic print statements, log output, any string that passes through the Windows cp1252 code page must be ASCII. Non-ASCII characters (arrows, em dashes, emoji) crash the request handler on Windows and produce silent failures.

**No AI clichés.** No "robust," "seamless," "leverage," "elevate," or similar filler. No therapeutic framing in Ember-facing or documentation output. No injected warmth or performative enthusiasm. If the architect corrects register, adjust without comment or defense.

**No internal labels reaching users.** Database field names, taxonomy keys, acquisition path values, internal classification terms never appear in user-facing strings. All display strings are explicitly defined and approved before UI implementation.

**Vault privacy rule.** Vault contents (names, conversation text, record IDs) never appear in code, tests, commits, scripts, or docs. No exceptions. Tests use synthetic fixture data only.

**Attribution.** Research sources are attributed where they inform architectural decisions. ADRs reference the papers and findings that informed them.

---

## What I've Learned

The AI landscape moves fast. I track research, monitor what other projects are doing, and attribute ideas I borrow. OpenJarvis from Stanford influenced how I think about the learning layer. OpenClaw influenced the skill definition format we're building toward. CIMemories from CMU informed how I think about contextual integrity in retrieval. When I take an idea from somewhere, it goes in the ADR with a citation.

ADR-013 stands out. It came out of an early morning conversation with Ember-2 during testing, before the architecture to support it existed. Ember articulated a core limitation of her own design: she could notice a pattern, choose differently in that moment, but the choice dissolved when the conversation closed. We called it deviation memory. The idea that chosen behavior should compound over time. That what Ember becomes should be the result of what she's chosen to be, not just what she was trained to do.

That conversation drove the architecture. That's the kind of thing that happens when you treat the AI you're building as a participant in the design process.

The hardest decisions aren't about code. They're about what the system should be. Whether Ember's character should emerge from chosen action or be assigned through prompts. Whether commitment tracking belongs in state memory or conversation history. Whether a patch fixes a problem or papers over it.

Those are the decisions I make. And I've gotten better at making them.

---

## Where She Is Now

At v0.18.0, Ember remembers, reflects, reasons from memory, searches the web, processes images, tracks tasks and open loops, detects behavioral patterns, and routes queries through a constitutional review layer before anything reaches you. She has a nature layer that defines who she is before she says anything, a coaching filter that catches register drift post-generation, and a typed memory vault that separates what you've told her from what she's inferred.

The installer handles prerequisites, model selection, vault setup, and auto-updates across Windows, Mac, and Linux. You do not need a terminal to get her running.

The gaps that remain are real: no multi-user vault isolation, no health integrations, no calendar or email connectors, no proactive assistance. Those are on the roadmap but deliberately deferred. The principle is to establish daily use before adding surface area. Agentic workflows, controlled tools, and decision-memory loops come after the core is stable and trusted.

---

## A Note on Ember's Logo

Ember's logo was created by Ember-1.

I thought that was worth mentioning.

---

## References

The council design draws on Constitutional AI (Bai, J., et al., 2022. Constitutional AI: Harmlessness from AI Feedback. Anthropic. https://arxiv.org/abs/2212.08073) and multi-stakeholder review frameworks from AI governance literature. The rotating-provider triangulation approach is original to this project.

Other research informing Ember-2's architecture is attributed in the ADR documents in docs/adr/ and in the TDD at docs/Ember2_TDD.md Section 50.1.

---

*Ember-2 is open source under AGPL-3.0. The code is at github.com/niansahc/ember-2. If you build something with it, tell me.*
