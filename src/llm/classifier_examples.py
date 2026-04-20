"""
src/llm/classifier_examples.py

Synthetic labeled example pool for the ADR-034 Stage 2 intent classifier.

60 representative queries — 30 per class — matching the patterns surfaced
in ADR-034 and the v0.17.0 kickoff test list. No vault content appears
here (CLAUDE.md Vault Privacy Rule): these are generic, pattern-level
examples designed to span the classification failure modes Stage 2 must
handle.

As production traffic accumulates in the [INTENT_CLASSIFY] log pipeline,
real representative queries can be added to this list (with any personal
content paraphrased / stripped). Target: 80 per class for stable accuracy;
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
]


EXAMPLES: list[LabeledExample] = _NEEDS_INTERNET + _VAULT_ANSWERABLE

# Sanity check at import so accidental class imbalance surfaces early.
assert len(_NEEDS_INTERNET) == 30, f"needs_internet count is {len(_NEEDS_INTERNET)}, expected 30"
assert len(_VAULT_ANSWERABLE) == 30, f"vault_answerable count is {len(_VAULT_ANSWERABLE)}, expected 30"
assert len(EXAMPLES) == 60
