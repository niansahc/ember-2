# Ember-2 v0.18.0

**TLDR:** v0.18.0 puts Ember, the interface, and the installer on one version number. Ember stopped inventing links, stopped misreading how old her own records are, and holds up better in long conversations. The interface passed an accessibility audit. The installer runs on Linux.

---

## One Version Number

Ember-2 is three pieces: the engine that does the thinking, the interface you talk to, and the installer that sets it all up and keeps it current. Until now each carried its own version number, which made "what version am I on" a question with three answers.

From this release they share one. The engine was at 0.17.1, the interface and installer were at 0.8.1, and all three are now 0.18.0. Nothing was skipped and nothing is missing. The interface and installer numbers jumped to catch up, and from here the three move together.

---

## Character and Behavior

- **No more invented links:** Asked about a project or product she did not have solid records for, qwen3:8b would sometimes produce a plausible-looking URL that led nowhere (a fake GitHub path, for example). A post-generation validator now strips any link not backed by an actual source, so a made-up link becomes `[unverified link]`.
- **Less coaching register:** The filter that catches "let's work through this", "give yourself permission", and endless follow-up questions got broader, trained on the exact phrasings that leaked through in testing. It now checks intent before firing and leaves very short replies alone, so it stops rewriting answers that were already fine. Not perfect at qwen3:8b scale, but there is less of it.

## Memory and Retrieval

- **Recent conversations are findable again:** Ember was reading from an index that had not refreshed since April, so recent conversations could be invisible to semantic search. That index is retired and everything now reads from the live store.
- **Correct record ages:** She no longer reports something written minutes ago as being from hundreds of days back.
- **Long conversations hold up:** Conversations that outgrow the model's context window now trim the oldest material in stages instead of failing the turn. State resolved in one turn stops resurfacing in later turns, and starting a new conversation clears the previous one's working memory.

## Web Search

- **Freshness ranking:** Results carry a publication date signal, so recent sources rank above stale ones.
- **Fewer wrong triggers:** Thinking-out-loud phrases like "that's what I'm trying to figure out" no longer read as a search request, and image turns no longer trigger a search.
- **Clarification on fragments:** A one-word or fragment message now gets a short clarifying question instead of a guess.

## Streaming and Privacy

- **Steadier streaming:** Early-return responses (an empty message, a blocked request, the onboarding flow) no longer occasionally come back blank. Status signals like "searching" and "reviewing" travel on a clean, versioned wire format, and the interface now displays them.
- **Quieter logging:** Verbose diagnostic output sits behind a debug flag rather than running all the time, so ordinary use writes less about your conversations to disk.

## Interface

- **Accessibility:** WCAG 2.1 AA pass. 12 fixes across contrast, focus handling, labelling, and keyboard navigation, with an automated accessibility check on every change so it does not quietly regress.
- **Review is visible:** A breathing dot appears while constitutional review runs on a draft, so a pause reads as work rather than a hang.
- **Correct source badges:** Vault-sourced and web-sourced answers are told apart properly, so a vault answer no longer gets tagged as though it came from the web.
- **Smaller fixes:** Assigning a conversation to a project retries once instead of silently dropping, and the service status panel closes when you click away from it.

## Installer

- **Linux support:** Build configuration, CI, and platform-gated install paths, with startup task, Docker, reboot, and self-update handling hardened for Linux.
- **Update checks compare numbers:** Version comparison was string-based, which is fragile once numbers reach two digits. That is exactly the jump this release makes.
- **Release notes render again:** The notes file was not being bundled into the build, so the update panel came up empty.
- **Current model lineup:** A fresh install offers the qwen3 family rather than the older curated set.
- **Security and logs:** Values rendered into the installer window are inserted as text, not markup. Duplicate install log lines are fixed, and a button whose background work fails no longer leaves an unhandled error.

---

## Under the Hood

Most of the rest of this release is internal, the kind of work that does not change what you see but makes everything more reliable and easier to build on.

- **chat_completions decomposition** (issue #93, three parts): the ~1350-line request handler was broken into named phases. Part (a) consolidated the streaming wire format and froze it as a contract (ADR-040). Part (b) pulled the terminal short-circuits (empty / override / onboarding) into an ordered router (ADR-041). Part (c) extracted a GenerationContext and a two-phase enrichment pipeline, migrating the clarification path onto it (ADR-042). Behavior is identical; the code is now legible.
- **Response-quality eval framework:** automated scoring on whether register and honesty drift over a long conversation, whether Ember stays grounded in retrieved records or confabulates, and whether she holds her voice. Built on a Claude judge with retry and transient-failure tolerance, provenance-stamped baselines tracked in git, and a local release-gate command. Register, grounding, drift, and user-expectation baselines are established.
- **UAT acceptance runner:** an interactive script that walks the release acceptance scenarios from a YAML definition, so the manual pass before a release is repeatable rather than improvised.
- **Append-only state resolution** (A2, ADR-038): resolving a pending item writes a tombstone record instead of mutating state in place, preserving the append-only guarantee.
- **Safe JSON I/O** (A3, ADR-039): the remaining unguarded file read/write sites route through atomic, corruption-aware helpers, so a torn write cannot leave a truncated record behind.
- **SSE wire contract v2** (ADR-040): status frames ship as a top-level typed frame, reconciling a backend/interface mismatch under a documented change procedure. The interface side of that reconciliation ships in this release too.
- **Constitution v0.8.**
- **UAT workstream B fixes:** open-loop suppression (B-STATE-001), deferred I/O sites (B-IO-001), a Stage-2 intent misroute (B-CTX-001), the SSE status frame (B-SSE-001), and several self-narrative, engagement-closing, and circular-dodge detector fixes from the 2026-05-11 UAT pass.
- **CLOUD_MODELS** points at a reachable Sonnet id (`claude-sonnet-4-5-20250929`); the older dated id returned 404 for some keys.
- **Interface internals:** appearance settings moved into their own context (dropping 16 pass-through props), the PIN flow replaced a window-event bus with explicit props, colors were tokenized to semantic names, a Vitest unit layer was added, and a 16-flow end-to-end user journey suite now runs against the real interface.
- **Installer internals:** every place the installer shells out to another program now goes through one tested helper instead of a dozen hand-rolled call sites.

---

## Known Issues

- Register and tone at qwen3:8b scale stay below target on emotional queries (M-001, A-001). Documented model-scale ceiling; the coaching filter mitigates but does not eliminate it.
- Vault-retrieved content is presented with uniform confidence regardless of match quality or age. There is no uncertainty signal yet.
- The self-narrative, engagement-closing, and circular-dodge detectors are surface-pattern matchers; novel phrasings can slip past until a corpus-driven detector lands (targeted v0.19.0).
- The fast-streaming path can flash a few unfiltered tokens before constitutional review runs on a deliberately crafted identity-override input.
