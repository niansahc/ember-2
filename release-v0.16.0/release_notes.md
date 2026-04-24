**Ember-2 v0.16.0 — Vision, Bare Mode, Web Search Hardening**

**What's new**

Ember can now see images you send her. Drop an image into the chat and ask her about it and she'll look at it rather than ignoring it or making something up.

You can now turn off Ember's personality layer for a conversation if you want plain, direct output without her voice. You can also turn off vault writing per conversation if you want a session that leaves no memory behind. Safety logging stays on in both cases.

Web search now happens automatically when Ember thinks it's relevant. You don't have to ask. If you want her to search something specific, just tell her to search.

Responses are less likely to slip into a coaching or therapy register on emotional topics. This was a consistent quality issue and a lot of work went into it this release.

The interface has four visual styles to choose from. Fonts are self-hosted. The time-of-day greeting has 180 variants. Feature status is visible in the top bar.

---

**Technical details**

- Vision pipeline (ADR-032): image_data forwarded through
  LLMAdapter.chat; vision model preprocesses upstream, main
  model runs with full character layer
- Bare mode: personality layers stripped on opt-in; vault
  retrieval, web search, safety logging remain active
- Per-conversation vault toggle (ADR-031): stateless mode per
  conversation; safety logs persist
- Autonomous web search default: web_search_autonomous=True;
  explicit/implicit marker split in context policies; "search
  the web" bypasses ask-first
- Two-stage post-generation coaching filter: therapeutic
  register reduction on emotional/task content; semantic
  rewrite pass for preference-expression identity collapse
- Constitutional review additions: numeric fabrication
  detector, validation-before-correction criterion
- State items in vault citation signal: state records surface
  alongside memory and reflection
- CLI UAT runner, conversation quality eval framework,
  synthetic test profile and test vault
- 30+ UAT hardening fixes: ask-first confirmation flow,
  retrieval leakage, vault badge, source suppression,
  soft-delete cascade, override block SSE streaming, and more

**Known issues**

- cross_domain_leakage retrieval eval case is a pre-existing
  FAIL, stable since April 17
- CHANGELOG.md was not maintained during v0.15.0-v0.15.3;
  this release consolidates those changes
