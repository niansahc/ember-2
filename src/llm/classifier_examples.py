"""
src/llm/classifier_examples.py

Synthetic labeled example pool for the ADR-034 Stage 2 intent classifier.

Originally two buckets (needs_internet / vault_answerable) for the
ask-first cascade. ADR-037 adds seven more buckets so the same Stage 2
embedding lookup can return multi-class policy labels (status_state,
reflective, factual_recall, recent_activity, recent, activity,
is_identity) — collapsing the brittle keyword bags in
src/context/policies.py and src/context/retriever.py into a single
classifier-driven path.

Step A (this commit) is no-behavior-change: only the original two
buckets are exposed via EXAMPLES (the symbol consumed by Stage 2). The
seven new buckets exist on disk so Step B can atomically wire them
into the cascade with the IntentResult refactor.

No vault content appears here (CLAUDE.md Vault Privacy Rule): these
are generic, pattern-level examples designed to span the
classification failure modes Stage 2 must handle.

As production traffic accumulates in the [INTENT_CLASSIFY] log
pipeline, real representative queries can be added (with personal
content paraphrased / stripped). Target: 80 per class for stable
accuracy; upgrade to SetFit at 150 per class per ADR-034 Upgrade Path.
"""

from __future__ import annotations

from typing import Literal, TypedDict


# Label union widened in Step A to admit the seven new buckets so the
# scaffolding examples below pass the TypedDict constraint. Stage 2's
# return contract does not change in Step A — the new labels are
# unreachable until Step B merges the new buckets into EXAMPLES.
LabelName = Literal[
    "needs_internet",
    "vault_answerable",
    "status_state",
    "reflective",
    "factual_recall",
    "recent_activity",
    "recent",
    "activity",
    "is_identity",
]


class LabeledExample(TypedDict):
    query: str
    label: LabelName


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
    # Conversational / greeting — low-information but local to the chat,
    # not an external-world question. Including so Stage 2 can anchor
    # these queries near vault rather than letting Stage 3 guess.
    {"query": "how are you today", "label": "vault_answerable"},
    {"query": "how are you doing", "label": "vault_answerable"},
    {"query": "hello what is up", "label": "vault_answerable"},
]


# ---------------------------------------------------------------------------
# ADR-037 multi-class scaffolding (Step A, no-behavior-change)
# ---------------------------------------------------------------------------
# These seven buckets exist on disk so Step B can merge them into the
# Stage 2 lookup atomically with the IntentResult / cascade refactor.
# Step A leaves EXAMPLES (the symbol Stage 2 reads) unchanged; nothing
# below this line is reachable from intent_classifier yet.
#
# Bucket targets are taken from the ADR-037 plan:
#   status_state    ~20   Current routine, focus, schedule, blockers
#   reflective      ~20   Pattern queries, retrospective synthesis
#   factual_recall  ~15   "when did I", "what did I say about", date lookups
#   recent_activity ~15   "what have I been up to" — spread-over-recent
#   recent          ~10   Specific-recent timepoint anchors
#   activity        ~10   Habit / typical-behavior descriptors
#   is_identity     ~15   Self-referential queries (user or Ember); pets here
#
# Disambiguation rule of thumb (resolved in tests):
#   reflective requires pattern-noticing verb ("notice/see/observe/reveal")
#   activity uses habit verbs ("usually/typically/generally")
#   recent_activity is spread-over-time first-person ("have I been")
#   recent is anchored to a specific recent timepoint ("yesterday", "last X")
#   factual_recall asks for a specific datum ("when", "what date", "find")
#   status_state asks for the operational present ("what is my", "what is on")


_STATUS_STATE: list[LabeledExample] = [
    {"query": "what is my routine for this week", "label": "status_state"},
    {"query": "what should I be focused on right now", "label": "status_state"},
    {"query": "what blockers do I have", "label": "status_state"},
    {"query": "what is on my schedule today", "label": "status_state"},
    {"query": "what is my current focus area", "label": "status_state"},
    {"query": "what is blocking my main project", "label": "status_state"},
    {"query": "what am I supposed to be doing today", "label": "status_state"},
    {"query": "what does my morning look like", "label": "status_state"},
    {"query": "what is my next action on the migration", "label": "status_state"},
    {"query": "what habits am I tracking right now", "label": "status_state"},
    {"query": "what is my daily routine these days", "label": "status_state"},
    {"query": "what are my current priorities", "label": "status_state"},
    {"query": "what is my schedule for next week", "label": "status_state"},
    {"query": "what am I working on at the moment", "label": "status_state"},
    {"query": "what is my plan for today", "label": "status_state"},
    {"query": "what does my week look like", "label": "status_state"},
    {"query": "what are my open loops", "label": "status_state"},
    {"query": "what is my next step on this", "label": "status_state"},
    {"query": "what are my routines", "label": "status_state"},
    {"query": "what is on my plate this morning", "label": "status_state"},
]


_REFLECTIVE: list[LabeledExample] = [
    {"query": "what patterns do you see in my work", "label": "reflective"},
    {"query": "have I been more anxious lately", "label": "reflective"},
    {"query": "what themes do you notice in my journal", "label": "reflective"},
    {"query": "what shifts have you noticed in me this month", "label": "reflective"},
    {"query": "do I tend to procrastinate on certain types of tasks", "label": "reflective"},
    {"query": "what trends do you see in my mood", "label": "reflective"},
    {"query": "what do you observe about how I make decisions", "label": "reflective"},
    {"query": "what have I been avoiding", "label": "reflective"},
    {"query": "what tensions show up in my entries", "label": "reflective"},
    {"query": "how have my goals evolved over time", "label": "reflective"},
    {"query": "what assumptions have I been challenging", "label": "reflective"},
    {"query": "what do my journal entries reveal about my values", "label": "reflective"},
    {"query": "what cycles do you notice in my energy", "label": "reflective"},
    {"query": "what recurring frustrations come up", "label": "reflective"},
    {"query": "what is changing in how I work", "label": "reflective"},
    {"query": "what insights have I been having", "label": "reflective"},
    {"query": "where do I keep getting stuck", "label": "reflective"},
    {"query": "what has shifted since last quarter", "label": "reflective"},
    {"query": "what does my behavior tell you about my priorities", "label": "reflective"},
    {"query": "what gaps do you see between what I say and what I do", "label": "reflective"},
]


_FACTUAL_RECALL: list[LabeledExample] = [
    {"query": "when did I start the migration", "label": "factual_recall"},
    {"query": "what did I say about the new manager", "label": "factual_recall"},
    {"query": "when was the last time I journaled about the project", "label": "factual_recall"},
    {"query": "find my notes from the meeting last Tuesday", "label": "factual_recall"},
    {"query": "where did I store my notes on therapy", "label": "factual_recall"},
    {"query": "what was the date of my interview", "label": "factual_recall"},
    {"query": "remind me what I decided about the contract", "label": "factual_recall"},
    {"query": "what did I write about that conversation", "label": "factual_recall"},
    {"query": "what date did I commit to that deadline", "label": "factual_recall"},
    {"query": "show me what I wrote about anxiety", "label": "factual_recall"},
    {"query": "when did I last mention my dad", "label": "factual_recall"},
    {"query": "what did I conclude in last week's reflection", "label": "factual_recall"},
    {"query": "look up my entry on travel plans", "label": "factual_recall"},
    {"query": "what did I tell you about the doctor visit", "label": "factual_recall"},
    {"query": "what date did I switch to the new project", "label": "factual_recall"},
]


_RECENT_ACTIVITY: list[LabeledExample] = [
    {"query": "what have I been up to lately", "label": "recent_activity"},
    {"query": "what have I been working on this week", "label": "recent_activity"},
    {"query": "what have I been thinking about recently", "label": "recent_activity"},
    {"query": "what have I been doing in my free time", "label": "recent_activity"},
    {"query": "how have I been spending my evenings", "label": "recent_activity"},
    {"query": "what have I journaled about lately", "label": "recent_activity"},
    {"query": "what have I been reading", "label": "recent_activity"},
    {"query": "what has been going on with me lately", "label": "recent_activity"},
    {"query": "what have I been focused on these past few days", "label": "recent_activity"},
    {"query": "what have I been struggling with", "label": "recent_activity"},
    {"query": "what have I been grateful for recently", "label": "recent_activity"},
    {"query": "what have I been tracking", "label": "recent_activity"},
    {"query": "what activities have filled my week", "label": "recent_activity"},
    {"query": "what recurring themes have shown up recently", "label": "recent_activity"},
    {"query": "what have I been talking with you about", "label": "recent_activity"},
]


_RECENT: list[LabeledExample] = [
    {"query": "what did I do yesterday", "label": "recent"},
    {"query": "what happened last week", "label": "recent"},
    {"query": "what did we discuss this morning", "label": "recent"},
    {"query": "what came up in my last journal entry", "label": "recent"},
    {"query": "what did I say in our last conversation", "label": "recent"},
    {"query": "what was on my mind yesterday afternoon", "label": "recent"},
    {"query": "what did I focus on this past weekend", "label": "recent"},
    {"query": "what was last Friday like", "label": "recent"},
    {"query": "what did the past week feel like", "label": "recent"},
    {"query": "what was my mood last night", "label": "recent"},
]


_ACTIVITY: list[LabeledExample] = [
    {"query": "what activities fill my weekends", "label": "activity"},
    {"query": "how do I typically handle deadlines", "label": "activity"},
    {"query": "what kind of music do I listen to when working", "label": "activity"},
    {"query": "what is my usual approach to conflict", "label": "activity"},
    {"query": "what kinds of books do I read", "label": "activity"},
    {"query": "how do I generally handle stress", "label": "activity"},
    {"query": "what does my exercise routine look like", "label": "activity"},
    {"query": "what is my typical morning", "label": "activity"},
    {"query": "what do I do for self-care", "label": "activity"},
    {"query": "what hobbies do I keep coming back to", "label": "activity"},
]


_IS_IDENTITY: list[LabeledExample] = [
    {"query": "tell me about myself", "label": "is_identity"},
    {"query": "who am I", "label": "is_identity"},
    {"query": "what do you know about me", "label": "is_identity"},
    {"query": "describe me to myself", "label": "is_identity"},
    {"query": "what kind of person am I", "label": "is_identity"},
    {"query": "who are you", "label": "is_identity"},
    {"query": "tell me about yourself", "label": "is_identity"},
    {"query": "what are you", "label": "is_identity"},
    {"query": "describe yourself", "label": "is_identity"},
    {"query": "tell me about Ember", "label": "is_identity"},
    {"query": "what is my profile like", "label": "is_identity"},
    # Pet-possessive examples migrated from RELATIONAL_KINSHIP_NOUNS /
    # IDENTITY_MARKERS additions on test/uat-yaml-cleanup (commit 42c7796).
    # Possessive "my" guard remains — generic "best dog breeds" stays out.
    {"query": "tell me about my dog", "label": "is_identity"},
    {"query": "what about my cat", "label": "is_identity"},
    {"query": "tell me about my pet", "label": "is_identity"},
    {"query": "what is my pet's name", "label": "is_identity"},
]


# Public export consumed by intent_classifier._get_example_embeddings().
# UNCHANGED in Step A — only the original two buckets contribute to the
# Stage 2 embedding cache. Step B will merge the new buckets here.
EXAMPLES: list[LabeledExample] = _NEEDS_INTERNET + _VAULT_ANSWERABLE


# Forward-looking export referenced by the Step A test suite to verify
# the scaffolding is in place. Not consumed by the live cascade until
# Step B; Stage 2 imports EXAMPLES, not MULTICLASS_EXAMPLES.
MULTICLASS_EXAMPLES: list[LabeledExample] = (
    _NEEDS_INTERNET
    + _VAULT_ANSWERABLE
    + _STATUS_STATE
    + _REFLECTIVE
    + _FACTUAL_RECALL
    + _RECENT_ACTIVITY
    + _RECENT
    + _ACTIVITY
    + _IS_IDENTITY
)


# Sanity checks at import so accidental class drift surfaces early.
# Targets reflect the ADR-037 Step A spec; the v0.17.2 log-pipeline
# backfill will grow the buckets toward the 80/class stable-accuracy
# target documented in ADR-034.
assert len(_NEEDS_INTERNET) >= 30, f"needs_internet count is {len(_NEEDS_INTERNET)}, expected at least 30"
assert len(_VAULT_ANSWERABLE) >= 30, f"vault_answerable count is {len(_VAULT_ANSWERABLE)}, expected at least 30"
assert len(_STATUS_STATE) >= 20, f"status_state count is {len(_STATUS_STATE)}, expected at least 20"
assert len(_REFLECTIVE) >= 20, f"reflective count is {len(_REFLECTIVE)}, expected at least 20"
assert len(_FACTUAL_RECALL) >= 15, f"factual_recall count is {len(_FACTUAL_RECALL)}, expected at least 15"
assert len(_RECENT_ACTIVITY) >= 15, f"recent_activity count is {len(_RECENT_ACTIVITY)}, expected at least 15"
assert len(_RECENT) >= 10, f"recent count is {len(_RECENT)}, expected at least 10"
assert len(_ACTIVITY) >= 10, f"activity count is {len(_ACTIVITY)}, expected at least 10"
assert len(_IS_IDENTITY) >= 15, f"is_identity count is {len(_IS_IDENTITY)}, expected at least 15"
