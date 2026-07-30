# Ember-2 v0.18.0

**TLDR:** v0.18.0 puts Ember, the interface, and the installer on one version number. Ember stopped inventing links, stopped misreading how old her own records are, and holds up better in long conversations. The interface passed an accessibility audit. The installer runs on Linux.

---

## One version number

Ember-2 is three pieces: the engine that does the thinking, the interface you talk to, and the installer that sets it all up and keeps it current. Each carried its own version number until now, which made "what version am I on" a question with three answers.

From this release they share one. The engine was at 0.17.1, the interface and installer at 0.8.1, and all three are now 0.18.0. Nothing was skipped. The interface and installer numbers jumped to catch up, and from here the three move together.

---

## Character and behavior

- No more invented links. Asked about a project or product she didn't have solid records for, qwen3:8b would sometimes produce a plausible-looking URL that led nowhere, usually a fake GitHub path. A post-generation validator now strips any link that isn't backed by an actual source. What you get instead is `[unverified link]`.
- Less coaching register. The filter that catches "let's work through this", "give yourself permission", and endless follow-up questions got broader, trained on the exact phrasings that leaked through in testing. It checks intent before firing now, and it leaves very short replies alone, which stops it rewriting answers that were already fine. Still not perfect at qwen3:8b scale. But there's less of it.

## Memory and retrieval

- Recent conversations are findable again. Ember was reading from an index that hadn't refreshed since April, which could make recent conversations invisible to semantic search. That index is retired. Everything reads from the live store.
- Record ages are correct. She stopped reporting something written minutes ago as being from hundreds of days back.
- Long conversations hold up. A conversation that outgrows the model's context window now trims the oldest material in stages rather than failing the turn. State resolved in one turn stops resurfacing later, and starting a new conversation clears the previous one's working memory.

## Web search

- Results carry a publication date signal, so recent sources rank above stale ones.
- Thinking-out-loud phrases like "that's what I'm trying to figure out" don't read as a search request anymore, and image turns don't trigger a search at all.
- A one-word or fragment message gets a short clarifying question rather than a guess.

## Streaming and privacy

- Early-return responses (an empty message, a blocked request, the onboarding flow) don't occasionally come back blank anymore. Status signals like "searching" and "reviewing" travel on a clean, versioned wire format, and the interface displays them now.
- Verbose diagnostic output sits behind a debug flag rather than running all the time, so ordinary use writes less about your conversations to disk.

## Interface

- WCAG 2.1 AA pass: 12 fixes across contrast, focus handling, labelling, and keyboard navigation. An automated accessibility check runs on every change to keep it from quietly regressing.
- A breathing dot appears while constitutional review runs on a draft, so a pause reads as work rather than a hang.
- Vault-sourced and web-sourced answers are told apart properly again. A vault answer doesn't get tagged as though it came from the web.
- Smaller fixes: assigning a conversation to a project retries once before giving up, and the service status panel closes when you click away from it.

## Installer

- Linux support: build configuration, CI, and platform-gated install paths, with startup task, Docker, reboot, and self-update handling hardened for Linux.
- Update checks work again. Two faults in one feature. The comparison was string-based, which breaks once a version number reaches two digits, and 0.8.1 to 0.18.0 is exactly that jump. Underneath that, it couldn't read the release tags at all after they picked up a repo-name prefix at the end of April, which had left the update banner dead since then. Tags go back to the plain `v0.18.0` form and the comparison is numeric.
- Release notes render again. The notes file wasn't being bundled into the build, so the update panel came up empty.
- A fresh install offers the qwen3 family rather than the older curated set.
- Security and logs: values rendered into the installer window are inserted as text, not markup. Duplicate install log lines are fixed, and a button whose background work fails no longer leaves an unhandled error.

---

## Under the hood

The rest is internal. It doesn't change what you see; it makes the next release easier to build.

- **chat_completions decomposition** (issue #93, three parts): the ~1350-line request handler was broken into named phases. Part (a) consolidated the streaming wire format and froze it as a contract (ADR-040). Part (b) pulled the terminal short-circuits (empty / override / onboarding) into an ordered router (ADR-041). Part (c) extracted a GenerationContext and a two-phase enrichment pipeline, migrating the clarification path onto it (ADR-042). Behavior is identical. The code is now legible.
- **Response-quality eval framework:** automated scoring on whether register and honesty drift over a long conversation, whether Ember stays grounded in retrieved records or confabulates, and whether she holds her voice. Built on a Claude judge with retry and transient-failure tolerance, provenance-stamped baselines tracked in git, and a local release-gate command. Register, grounding, drift, and user-expectation baselines are established.
- **UAT acceptance runner:** an interactive script that walks the release acceptance scenarios from a YAML definition. The manual pass before a release is now repeatable rather than improvised.
- **Append-only state resolution** (A2, ADR-038): resolving a pending item writes a tombstone record instead of mutating state in place, which preserves the append-only guarantee.
- **Safe JSON I/O** (A3, ADR-039): the remaining unguarded file read/write sites route through atomic, corruption-aware helpers. A torn write can't leave a truncated record behind.
- **SSE wire contract v2** (ADR-040): status frames ship as a top-level typed frame, reconciling a backend/interface mismatch under a documented change procedure. The interface side of that reconciliation ships in this release too.
- **Constitution v0.8.**
- **UAT workstream B fixes:** open-loop suppression (B-STATE-001), deferred I/O sites (B-IO-001), a Stage-2 intent misroute (B-CTX-001), the SSE status frame (B-SSE-001), and several self-narrative, engagement-closing, and circular-dodge detector fixes from the 2026-05-11 UAT pass.
- **CLOUD_MODELS** points at a reachable Sonnet id (`claude-sonnet-4-5-20250929`). The older dated id returned 404 for some keys.
- **Interface internals:** appearance settings moved into their own context (dropping 16 pass-through props), the PIN flow replaced a window-event bus with explicit props, colors were tokenized to semantic names, a Vitest unit layer was added, and a 16-flow end-to-end user journey suite now runs against the real interface.
- **Installer internals:** every place the installer shells out to another program now goes through one tested helper rather than a dozen hand-rolled call sites.

---

## Known issues

- Register and tone at qwen3:8b scale stay below target on emotional queries (M-001, A-001). Documented model-scale ceiling. The coaching filter mitigates it but doesn't eliminate it.
- Vault-retrieved content is presented with uniform confidence regardless of match quality or age. No uncertainty signal yet.
- The self-narrative, engagement-closing, and circular-dodge detectors are surface-pattern matchers. Novel phrasings can slip past until a corpus-driven detector lands (targeted v0.19.0).
- The fast-streaming path can flash a few unfiltered tokens before constitutional review runs on a deliberately crafted identity-override input.
