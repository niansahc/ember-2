**Ember-2 v0.17.0 — Smarter Search, Cleaner Imports, Better Honesty**

**What's new**

Ember is now smarter about when to search the web. Instead of
keyword matching, she uses a three-stage classifier to figure
out whether your message needs a web search, can be answered
from your vault, or is something she should just respond to
directly. This replaces a brittle pattern-matching system that
was getting it wrong too often.

If you've imported your ChatGPT history, that data is now
handled more cleanly. Ember was previously treating imported
assistant responses as if they were live conversation turns,
which caused false state records and poor retrieval. That's
fixed — imported content is processed separately from your
real conversations.

Ember is less likely to soften, hedge, or respond in a
therapeutic register when you don't want that. Anti-sycophancy
rules were added at multiple layers: the instruction section,
the nature layer, and the coaching filter. She's more likely
to hold a position when challenged and less likely to slip into
"I hear you" phrasing on technical questions.

**Testing and infrastructure**

The test suite was rewritten as 22 behavioral acceptance tests
focused on what Ember actually does rather than implementation
details. A CI workflow now runs on every pull request.

**Known issues**

The stop button can take up to 20 seconds to cancel a response
under load. Fix is planned.

Ask-first web search (where Ember asks before searching rather
than searching automatically) has a working backend classifier
but the UI surface isn't wired up yet. It shows as "coming in
a future update" in Settings.