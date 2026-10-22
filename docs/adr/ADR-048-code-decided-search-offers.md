# ADR-048: Code-Decided Search Offers

**Status:** Accepted
**Date:** 2026-10-19
**Target:** v0.19.0
**Amends:** ADR-031 (vault toggle), ADR-034 (ask-first intent classifier), ADR-038 (pending_confirmation), ADR-042 (GenerationContext), ADR-047 (exchange persistence)
**Related:** PR #301, PR #302, `CONTEXT.md`, `docs/KNOWN_ISSUES.md` (B-WS-003)

Terms follow `CONTEXT.md`: a **Search offer** is Ember asking whether to run a
web search, a **Pending search** is an offer waiting for the user's answer, a
**Confirmation** is the user's yes, a **Follow-up** is new content the user
adds to that yes, and a **Search clarification** is Ember asking what to search
for.

## Context

A pending search (`pending_confirmation`, ADR-038) holds the text a web search
sends to SearXNG if the user confirms. Before this change, the model's wording
decided when one was written, and the text a web search sent was not always the
user's own:

- **Offers were read from the model's free text.** PR #301 and PR #302 wrote a
  pending search whenever the stored reply matched `OFFER_PATTERNS`
  (`src/llm/ask_first_validator.py`). Three review rounds found phrasings the
  patterns got wrong. Two of the holes were privacy leaks: the model offered
  something that was not a web search, such as "search the vault for that?",
  the patterns took it for a web offer, the user said yes, and Ember sent the
  user's earlier message to SearXNG.
- **The model was told to offer a search on every exchange.** Autonomous
  search is the default, so Ember searches without asking, yet always-on
  prompt text told the model to offer a search whenever memory lacked the
  answer: the date section (in every prompt, bare mode included), the
  authority rules, and two identity rules (`ask_first_confirmation` and
  `injected_date_authority`). Every such offer could become a pending search.
- **Any affirmative word confirmed.** A reply after an offer that held
  "please", "ok" or "sure" anywhere counted as a yes. "Please rewrite my draft
  paragraph" ran the old search, the model answered the old query, and the
  user's new request was dropped.
- **Ember's own text could reach the search engines.** Context assembly
  searched the message as rewritten for the model. That message can start with
  Ember's task or timer note (`[System: ...]`) and, on a confirmation, carries
  the follow-up. The UI's upload note, which names the uploaded files, went
  out with the user's text.

## Decision

### 1. Only Ember's code makes a search offer

There are two kinds of search offer, each with fixed text (section 7):

- **The ask-first offer.** An ask-first exchange is one the intent classifier
  routes to web search while autonomous search is off, unless the user asked
  for the search explicitly, the exchange is a confirmation, or it carries an
  image. On an ask-first exchange the reply is always S1, whatever the model
  drafted. A constitutional refusal is the one exception: it is kept as
  written and gets no offer (CLAUDE.md implementation standard 2). An exchange
  whose message holds a first-person marker (`has_first_person_marker`) is not
  an ask-first exchange: it gets no `<search_confirmation>` block, no fixed
  offer and no pending search, and the model answers.
- **The repeat-search offer.** A web search ran and failed with a reason a
  repeat could fix: `engines_blocked`, `timeout` or `request_error`. When the
  failed search was the one a confirmation ran, the reply is replaced by S2.
  After a failed autonomous, explicit or search-clarification search, S3 is
  appended, so the reply reads: the search disclosure, the answer, S3. A
  refusal is never replaced or extended.

A pending search is written only for these offers: a `pending_confirmation`
record in the vault, or an entry held in process on a vault-off exchange
(ADR-031 amendment). The record's text is the offer as the user saw it,
`metadata.offer` is its kind (`ask_first` or `repeat_search`), and
`metadata.query` is the user's own text (section 5). The post-gen pipeline
returns the offer as `PostGenResult.code_offer`; `_store_reply` writes nothing
when it is None.

### 2. The model never offers

- **The always-on prompt text no longer asks for offers.** The date section
  says "If not in memory, say you don't have it." The authority rules tell the
  model to say it has no current information instead of answering from
  training data. The identity rule `ask_first_confirmation` becomes
  `current_data_gap`: for current data that is not in memory, say in one short
  sentence that there is no current information on it, and do not answer from
  training data. `injected_date_authority` no longer says "and offer to
  search".
- **The `<search_confirmation>` block stays.** It is rendered only on an
  ask-first exchange, where the code replaces the draft with S1. It keeps that
  discarded draft to one sentence.
- **A post-generation guard removes the model's offers.** On an exchange where
  the code made no offer, every search-offer sentence is removed from the
  reply (`remove_unbacked_offers`). `OFFER_PATTERNS` is the guard's detector
  and decides nothing else.
  When nothing remains, the reply becomes S4. The guard never touches a
  refusal, and it does not run on an exchange with a code offer, so it never
  removes S1, S2 or S3. It also removes offers to search the user's own notes:
  Ember has no action behind those either.

### 3. Confirmation grammar

A reply to a pending search is read deterministically, with no model call. The
checks run in this order:

1. A negation declines, as before. "why not", "no problem" and "not a problem"
   are idioms, not negations.
2. A reply that does not start with an affirmative is unmatched.
3. Nothing after the affirmative: a plain yes.
4. A connector, a "?" anywhere, or a question word as the first word after the
   affirmative: a follow-up, if the rest holds at least one word that is not
   filler. Otherwise a plain yes.
5. A rest made only of filler: a plain yes.

Filler is the elaboration words plus the affirmatives and connectors
themselves, so "yes, and please search" is a plain yes.
6. Anything else is unmatched.

| List | Words |
|---|---|
| Leading affirmatives | yes, yeah, yep, yup, sure, ok, okay, please, y, search |
| Leading affirmative phrases | go ahead, do it, why not, no problem, not a problem, of course, go for it, sounds good, please do |
| Connectors | but, and, also, then, so |
| Question first words | what, who, when, where, why, how, which, is, are, can, could, does, do, did, will, would, should |
| Elaboration words | thanks, thank, you, ty, thx, now, for, that, this, the, a, it, web, online, them, look, up, find, again, try, great, good, fine, cool, perfect, awesome, nice, sounds, would, be, thats, thatd, id, i, appreciate, love, lot, much, thing, really, very |
| Negations | no, not, dont, nope, skip, stop, never, nah |

The code holds the canonical lists. An unmatched reply resolves the pending
search and runs as a new request; the stored text is not searched. "Yes, that
would be great" is a plain yes. "Please rewrite my draft paragraph" is a new
request. "yes, what about the weather in Paris" searches the stored text, and
the model receives that text plus the follow-up.

### 4. Images, clarifications and unanswered exchanges

- **An exchange with an image is never a confirmation.** It resolves the
  pending search without a search.
- **A search clarification supersedes an older pending search.** When a
  pending search exists and the newest assistant turn is a clarification, the
  pending is resolved without a search, and the user's message is searched as
  the clarification asked.
- **An unanswered exchange puts back what it took.** When a confirmation's
  exchange fails or is interrupted before its reply is stored (ADR-047), the
  pending search it consumed is put back: as a new record in the vault
  (ADR-038 amendment), or as the in-process entry on a vault-off exchange. On a
  vault-off exchange, a clarification's waiting state is put back the same way.
  A stored reply settles the exchange, so a failure after it puts nothing back.
  When the conversation already holds an active pending search for the same
  text, the restore writes nothing and logs that it was skipped.

### 5. Every web search sends only the user's own text

`search_text_from` (`src/tools/web_search.py`) gives the text every web search
sends: the user's own words for the exchange, which on a confirmation are the
stored text of the pending search. It never includes Ember's task or timer
notes, never a confirmation's follow-up, and never the UI's leading upload note
("[I just uploaded ... to my vault. ...]"), which names the uploaded files. A
pending search stores the same text. The stored user turn keeps the note
(ADR-047), and retrieval and classification read the same text as before.

### 6. no_results is a no-repeat reason

`NO_REPEAT_OFFER_REASONS` gains `no_results`, so it is now `no_results`,
`low_relevance` and `filtered`. A search that found nothing would find nothing
again, so it gets no repeat-search offer, and the search note for an empty
result now ends: "Searching again would return the same result, so do not
offer to search again."

### 7. User-facing texts

| Id | Offer | Where it appears | Text |
|---|---|---|---|
| S1 | Ask-first offer | Replaces the reply on an ask-first exchange | "I don't have current information on that. Want me to search the web for it?" |
| S2 | Repeat-search offer | Replaces the reply when a confirmation's search fails | "I tried searching the web but hit an error. Want me to try the search again?" |
| S3 | Repeat-search offer | Appended after a failed autonomous, explicit or search-clarification search | "Want me to try the web search again?" |
| S4 | None | The guard's fallback, used only when removing the model's offer leaves nothing | "I don't have that information." |

Each offer names the web, so a yes to one is consent to a web search and
nothing else.

## Alternatives considered

- **Keep deciding offers from the reply's wording, with better patterns.** This
  keeps every model offer usable: a yes to any of them would search. Rejected:
  the phrasings a model can produce are unbounded, and every hole is a leak,
  because a yes to a misread offer sends the user's text to the search engines.
  Privacy wins over honoring the model's offers.
- **A local-source exclusion list.** PR #302 treated an offer that names the
  user's own source ("your notes", "your files") as not a web offer. Rejected:
  such a list cannot be complete; "search the vault for that?" was not on it.
- **Validate the model's own offer text on ask-first exchanges**, keeping it
  when it reads as an offer. Rejected: the consent text must say the web
  unambiguously, and only fixed text guarantees that.
- **Keep "any affirmative word anywhere confirms".** Rejected: it replaced the
  user's new request with the old search.

## Consequences

**Positive:**
- A model's offer never writes a pending search, so a wording mistake cannot
  send text anywhere.
- A yes is always consent to a web search the user saw offered, for text the
  user wrote.
- A web search never carries Ember's notes, a follow-up, or the names of
  uploaded files.
- A reply that is not a clear yes is answered as the user's new request.

**Negative:**
- After "I don't have that information", a user who wants a search must ask for
  one. "look it up" gets the search clarification; "search for it" is not an
  explicit search marker.
- The grammar is strict. An elaborated yes outside the word lists ("oh yes
  please", "I'd love that") runs as a new request and ends the offer, and the
  user has to ask again. Words can be added to the lists.
- The guard can remove a sentence that matches `OFFER_PATTERNS` without being
  an offer the user wanted. Nothing leaks either way.
- A current-data question with a first-person marker gets no offer: the model
  answers, saying it has no current information.
- The ask-first draft is still generated and then discarded, so latency is
  unchanged. Making the ask-first exchange a terminal reply, like the search
  clarification, is a possible follow-up.
- The follow-up after "yes, but X" gets no search of its own: the confirmed
  search runs on the stored text, and the model answers the follow-up from
  what it has (`docs/KNOWN_ISSUES.md`, B-WS-003).
- The rewritten prompt text reaches every exchange, bare mode included through
  the date section. The evals are the gate.

## References

- `CONTEXT.md` -- Search offer, Repeat-search offer, Pending search,
  Confirmation, Follow-up, Search clarification.
- `src/llm/ask_first_validator.py` -- the fixed offer texts, `OFFER_PATTERNS`,
  `remove_unbacked_offers`, `has_first_person_marker`.
- `src/llm/post_gen_pipeline.py` -- `CodeOffer`, `PostGenResult.code_offer`,
  the order of the offers and the guard.
- `src/api/openai_adapter.py` -- `_store_reply`, the confirmation grammar,
  `_StatelessPendingStore`, `_PendingGuard`.
- `src/tools/web_search.py` -- `search_text_from`, `NO_REPEAT_OFFER_REASONS`.
- `src/llm/prompt_builder.py`, `config/identity_rules.yaml` -- the always-on
  prompt text and the `<search_confirmation>` block.
- `src/context/policies.py` -- explicit search markers,
  `SCRIPTED_CLARIFICATION_RESPONSE`.
- `docs/KNOWN_ISSUES.md` -- B-WS-003.
- `tests/test_web_search_query_text.py`, `tests/test_ask_first_offer_phrasing.py`,
  `tests/test_ask_first_routing.py`, `tests/test_ask_first_pending_lifecycle.py`.
- PR #301, PR #302.
