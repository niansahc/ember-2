# Ember-2 v0.18.0

**TLDR:** Ember, the interface, and the installer now share one version number. Ember stopped inventing links, stopped misreading how old her records are, and holds up better in long conversations. The interface passed an accessibility audit. The installer runs on Linux.

---

## One version number

Ember-2 is three pieces: the engine, the interface, and the installer. Each had its own version number, so "what version am I on" had three answers. Now they share one. The engine was at 0.17.1, the interface and installer at 0.8.1, and all three are now 0.18.0. Nothing was skipped, and from here they move together.

---

## Character and behavior

- Ember stopped inventing links. Asked about something she had no solid records for, she would sometimes produce a plausible URL that went nowhere, usually a fake GitHub path. Links that aren't backed by a real source now come through as `[unverified link]`.
- Coaching phrases like "let's work through this" and "give yourself permission" get caught more often now, along with endless follow-up questions. Short replies are left alone, so answers that were already fine stop getting rewritten.

## Memory and retrieval

- Recent conversations are findable again. Ember was reading a stale index that hadn't refreshed since April, so recent conversations could be invisible to search. That index is gone.
- Record ages are correct. She stopped reporting something written minutes ago as being from hundreds of days back.
- Long conversations hold up. Something you resolved in one turn stops resurfacing later. A conversation that outgrows the model's context window trims the oldest material instead of failing, and starting a new conversation clears the old one's working memory.

## Web search

- Recent sources rank above stale ones.
- Thinking out loud ("that's what I'm trying to figure out") no longer reads as a search request, and images don't trigger one at all.
- A one-word message gets a clarifying question instead of a guess.

## Streaming and privacy

- Streaming is steadier. Empty messages, blocked requests, and the onboarding flow don't come back blank anymore. "Searching" and "reviewing" signals reach the interface, and it displays them.
- Ordinary use writes less to disk. Verbose diagnostics sit behind a debug flag.

## Interface

- The interface meets WCAG 2.1 AA: 12 fixes across contrast, focus, labelling, and keyboard navigation, with an automated check on every change.
- A breathing dot appears while Ember is reviewing a draft, so a pause reads as work rather than a hang.
- Source badges are correct again. A vault answer no longer gets tagged as though it came from the web.
- Assigning a conversation to a project retries once before giving up, and the service status panel closes when you click away from it.

## Installer

- Linux support: build config, CI, and install paths, with startup, Docker, reboot, and self-update handling hardened.
- Update checks work again. Two faults in one feature. The comparison was string-based, which breaks once a version number reaches two digits, and 0.8.1 to 0.18.0 is exactly that jump. Underneath that, it couldn't read the release tags at all after they picked up a repo-name prefix at the end of April, which had left the update banner dead since then. Tags go back to the plain `v0.18.0` form and the comparison is numeric.
- The update panel shows release notes again. They weren't bundled into the build, so it came up empty.
- A fresh install offers the qwen3 family rather than the older curated set.
- Values rendered into the installer window are inserted as text, not markup. Duplicate install log lines are fixed, and a button whose background work fails no longer leaves an unhandled error.

---

## Under the hood

The rest is internal. It doesn't change what you see; it makes the next release easier to build.

- The 1350-line chat request handler was split into named phases: a frozen streaming wire format, an ordered router for the early exits, and a two-phase context pipeline. Behavior is identical. The code is legible.
- Response-quality evals: automated scoring for whether Ember drifts in register over a long conversation, stays grounded in retrieved records, and holds her voice. Baselines are tracked in git, with a release-gate command to run them.
- A UAT runner that walks the release acceptance scenarios from a definition file, so the manual pass before a release is repeatable rather than improvised.
- Resolving a pending item writes a tombstone record instead of editing state in place, which keeps memory append-only.
- The remaining unguarded file reads and writes go through atomic helpers, so a torn write can't leave a truncated record.
- Status frames ship as their own typed frame, reconciling a mismatch between the engine and the interface. The interface side ships here too.
- Constitution v0.8.
- Fixes from the May UAT pass: open-loop suppression, deferred writes, an intent misroute, the status frame, and several detector corrections.
- The cloud model id points at a reachable Sonnet release (`claude-sonnet-4-5-20250929`). The older dated id returned 404 for some keys.
- Interface internals: appearance settings moved into their own context, dropping 16 pass-through props. The PIN flow uses explicit props instead of a window-event bus. Colors are tokenized to semantic names. A unit test layer was added, plus a 16-flow end-to-end suite that runs against the real interface.
- Installer internals: every place the installer shells out to another program goes through one tested helper rather than a dozen hand-rolled call sites.

---

## Known issues

- Tone on emotional queries still runs below target at this model size. The coaching filter reduces it without eliminating it.
- Vault-retrieved content is presented with the same confidence regardless of how well it matches or how old it is.
- The detectors for self-narrative, engagement-closing, and circular dodging match surface patterns, so novel phrasings can slip past. A corpus-driven replacement is targeted for v0.19.0.
- The fast-streaming path can flash a few unfiltered tokens before review runs, on a deliberately crafted identity-override input.
