"""
src/llm/classifier_examples.py

Synthetic labeled example pool for the ADR-034 Stage 2 intent classifier.

60 representative queries — 30 per class — matching the patterns surfaced
in ADR-034 and the v0.17.0 kickoff test list. No vault content appears
here (CLAUDE.md Vault Privacy Rule): these are generic, pattern-level
examples designed to span the classification failure modes Stage 2 must
handle.

As production traffic accumulates in the [INTENT_CLASSIFY] log pipeline,
real representative queries can be added to this list. Note: the
telemetry log line is gated behind EMBER_CLASSIFIER_TELEMETRY and the
query is scrubbed via _scrub_for_telemetry before logging — multi-word
Title Case sequences, 4+ digit runs, and emails are replaced with
placeholders. Single-word proper nouns and most surface phrasing are
preserved. Operators may want to paraphrase further before promoting a
scrubbed query into this pool. Target: 80 per class for stable accuracy;
upgrade to SetFit at 150 per class per ADR-034 Upgrade Path.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class LabeledExample(TypedDict):
    query: str
    label: Literal["needs_internet", "vault_answerable"]


# Examples are grouped by subcategory for readability. Order does not
# affect classification behavior — the full list is embedded once and
# compared as an unordered pool.

_NEEDS_INTERNET: list[LabeledExample] = [
    # Weather and forecasting
    {"query": "what is the weather in Richmond today", "label": "needs_internet"},
    {"query": "will it rain tomorrow afternoon", "label": "needs_internet"},
    {"query": "forecast for the east coast this weekend", "label": "needs_internet"},
    # Finance and markets
    {"query": "current price of bitcoin", "label": "needs_internet"},
    {"query": "stock price of NVDA right now", "label": "needs_internet"},
    {"query": "how is the S&P 500 doing today", "label": "needs_internet"},
    {"query": "current inflation rate in the US", "label": "needs_internet"},
    # News and current events
    {"query": "what happened in the election last night", "label": "needs_internet"},
    {"query": "todays top headlines", "label": "needs_internet"},
    {"query": "latest news on the strike", "label": "needs_internet"},
    {"query": "any news about the SpaceX launch this week", "label": "needs_internet"},
    # Sports
    {"query": "who won the Lakers game last night", "label": "needs_internet"},
    {"query": "live score of the Manchester derby", "label": "needs_internet"},
    {"query": "current NBA standings", "label": "needs_internet"},
    # Entertainment
    {"query": "what is trending on Netflix this weekend", "label": "needs_internet"},
    {"query": "new movies releasing this week", "label": "needs_internet"},
    {"query": "box office numbers for last weekend", "label": "needs_internet"},
    # Current roles
    {"query": "who is the current CEO of OpenAI", "label": "needs_internet"},
    {"query": "who runs Anthropic now", "label": "needs_internet"},
    {"query": "current president of Argentina", "label": "needs_internet"},
    # Public statistics
    {"query": "current population of Tokyo", "label": "needs_internet"},
    {"query": "current unemployment rate in the US", "label": "needs_internet"},
    {"query": "GDP of Germany in 2025", "label": "needs_internet"},
    # Version and release
    {"query": "what is the latest version of Python", "label": "needs_internet"},
    {"query": "when did GPT-5 release", "label": "needs_internet"},
    # Public event lookups
    {"query": "is the senate in session today", "label": "needs_internet"},
    {"query": "did the bill get signed into law last week", "label": "needs_internet"},
    {"query": "any power outages in Richmond right now", "label": "needs_internet"},
    # Explicit current state
    {"query": "what is the current state of the housing market", "label": "needs_internet"},
    {"query": "what is currently happening in Ukraine", "label": "needs_internet"},
    # Imperative help-with-factual-anchor — contrastive control for the
    # introspective-uncertainty anchor in _VAULT_ANSWERABLE. Without these,
    # the verb "figure out" pulls "help me figure out X" toward vault even
    # when X is concretely external. These keep imperative-help-with-
    # external-topic queries on the needs_internet side.
    {"query": "help me figure out the best Python framework for 2026", "label": "needs_internet"},
    {"query": "help me figure out when the next iPhone is releasing", "label": "needs_internet"},
    {"query": "help me find out the current price of Tesla stock", "label": "needs_internet"},
    {"query": "find me the latest news about the AI industry", "label": "needs_internet"},
    # General-knowledge counter-anchors: ensure the "tell me about X",
    # "describe X", and "what do you know about X" openings have ground
    # truth on the needs_internet side too. Without these, queries about
    # external concepts route to vault_answerable because the only
    # "tell me about X" / "describe X" exemplars in the pool are personal
    # ("tell me about my goals", "describe me"). Paired with personal-
    # identity exemplars in _VAULT_ANSWERABLE to prevent mirror drift.
    {"query": "tell me about quantum computing", "label": "needs_internet"},
    {"query": "describe how transformers work", "label": "needs_internet"},
    {"query": "what do you know about Rust", "label": "needs_internet"},
]


_VAULT_ANSWERABLE: list[LabeledExample] = [
    # First-person current state
    {"query": "what am I working on this week", "label": "vault_answerable"},
    {"query": "what are my current projects", "label": "vault_answerable"},
    {"query": "what is my current focus for this quarter", "label": "vault_answerable"},
    {"query": "what is my current focus this week", "label": "vault_answerable"},
    {"query": "what was my most recent blocker", "label": "vault_answerable"},
    # First-person past recall
    {"query": "what did I say about the migration last week", "label": "vault_answerable"},
    {"query": "what did we discuss in our last conversation", "label": "vault_answerable"},
    {"query": "remind me what I told you about the meeting", "label": "vault_answerable"},
    {"query": "what was I journaling about on Tuesday", "label": "vault_answerable"},
    {"query": "what did I decide about the offer", "label": "vault_answerable"},
    # Personal reflection
    {"query": "what patterns do you notice in my work lately", "label": "vault_answerable"},
    {"query": "how have I been doing emotionally", "label": "vault_answerable"},
    {"query": "what themes come up in my journal", "label": "vault_answerable"},
    {"query": "what do you notice about how I make decisions", "label": "vault_answerable"},
    # Personal factual recall
    {"query": "when did I start this project", "label": "vault_answerable"},
    {"query": "where did I put that note about sleep", "label": "vault_answerable"},
    {"query": "find my entry from last Tuesday", "label": "vault_answerable"},
    {"query": "look up what I said about the interview", "label": "vault_answerable"},
    # Self-description
    {"query": "tell me about my goals for this year", "label": "vault_answerable"},
    {"query": "what do I usually do on weekends", "label": "vault_answerable"},
    {"query": "what is my writing style like", "label": "vault_answerable"},
    {"query": "what kind of music do I listen to", "label": "vault_answerable"},
    # First-person with volatile-sounding words (core false-positive cases)
    {"query": "I'm currently working on a project", "label": "vault_answerable"},
    {"query": "I've been watching a show lately", "label": "vault_answerable"},
    {"query": "what are my latest projects", "label": "vault_answerable"},
    {"query": "my latest thoughts on therapy", "label": "vault_answerable"},
    # Personal health and energy
    {"query": "how has my sleep been lately", "label": "vault_answerable"},
    {"query": "what has my energy been like this month", "label": "vault_answerable"},
    # Personal relationships
    {"query": "what did my partner and I discuss about finances", "label": "vault_answerable"},
    {"query": "what did my mom say last time we talked", "label": "vault_answerable"},
    # Conversational / greeting — low-information but local to the chat,
    # not an external-world question. Including so Stage 2 can anchor
    # these queries near vault rather than letting Stage 3 guess.
    {"query": "how are you today", "label": "vault_answerable"},
    {"query": "how are you doing", "label": "vault_answerable"},
    {"query": "hello what is up", "label": "vault_answerable"},
    # Introspective uncertainty — conversational signal, not information-
    # seeking. qwen3:8b at Stage 3 reads "trying to figure out" as intent
    # to search the web; anchoring this family at Stage 2 short-circuits
    # the misclassification before Stage 3 runs (B-WS-001).
    {"query": "that's what I'm trying to figure out", "label": "vault_answerable"},
    {"query": "I'm still trying to figure that out", "label": "vault_answerable"},
    {"query": "I haven't figured that out yet", "label": "vault_answerable"},
    {"query": "I'm still wondering about that", "label": "vault_answerable"},
    {"query": "I'm trying to make sense of it", "label": "vault_answerable"},
    {"query": "I'm trying to wrap my head around it", "label": "vault_answerable"},
    {"query": "I'm not sure what to make of it", "label": "vault_answerable"},
    {"query": "I keep going back and forth on it", "label": "vault_answerable"},
    {"query": "I haven't been able to work that out", "label": "vault_answerable"},
    {"query": "that's what I've been trying to understand", "label": "vault_answerable"},
    # Personal-identity exemplars (G6 drift fix). The pool previously had
    # no strong "who am I" / "describe me" anchors; "who am I" was
    # cosine-matching needs_internet "who is X" patterns at ~0.79.
    # Paired with general-knowledge counter-anchors in _NEEDS_INTERNET
    # to prevent mirror drift on "tell me about X" / "describe X" /
    # "what do you know about X" general queries.
    {"query": "who am I", "label": "vault_answerable"},
    {"query": "describe me", "label": "vault_answerable"},
    {"query": "what kind of person am I", "label": "vault_answerable"},
    {"query": "tell me about myself", "label": "vault_answerable"},
    {"query": "what do you know about who I am", "label": "vault_answerable"},
]


EXAMPLES: list[LabeledExample] = _NEEDS_INTERNET + _VAULT_ANSWERABLE

# Sanity check at import so accidental class drift surfaces early. The
# vault class runs a little higher than needs_internet because of
# conversational-greeting patterns added to catch "hello how are you"
# style queries — acceptable, ADR-034 does not require exact balance.
assert len(_NEEDS_INTERNET) >= 30, f"needs_internet count is {len(_NEEDS_INTERNET)}, expected at least 30"
assert len(_VAULT_ANSWERABLE) >= 30, f"vault_answerable count is {len(_VAULT_ANSWERABLE)}, expected at least 30"
