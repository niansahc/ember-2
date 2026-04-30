from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.llm.intent_classifier import NEEDS_INTERNET, classify_intent

logger = logging.getLogger("ember.policies")


# ---------------------------------------------------------------------------
# Relational / identity query classifier
# ---------------------------------------------------------------------------
# Queries about the user's personal relationships or identity domains.
# When a query matches, retrieval filters third-party ingested content out
# of the score pool — a kinship question should not resolve against books,
# articles, or the user's old ChatGPT dialogue about other people's kids.
# See UAT-005 investigation for the failure pattern this closes.

RELATIONAL_KINSHIP_NOUNS: tuple[str, ...] = (
    "son", "daughter", "child", "kid", "kids", "children",
    "partner", "spouse", "husband", "wife", "boyfriend", "girlfriend",
    "mother", "father", "mom", "dad", "parent", "parents",
    "brother", "sister", "sibling", "siblings",
    "family", "friend", "friends", "colleague", "coworker", "boss",
    "roommate", "neighbor",
    # Pets / animals — kinship-adjacent for many users. The compiled
    # _KINSHIP_PATTERN requires the possessive `\bmy\s+` prefix, so a generic
    # query like "best dog breeds" does not match; only "my dog" forms do.
    "dog", "cat", "pet", "bird", "fish", "rabbit", "hamster", "horse", "animal",
)

RELATIONAL_IDENTITY_DOMAINS: tuple[str, ...] = (
    "job", "work", "career", "health", "home", "house", "body", "mood",
    "relationship", "marriage", "religion", "spirituality", "practice",
)

_KINSHIP_PATTERN = re.compile(
    r"\bmy\s+(" + "|".join(re.escape(n) for n in RELATIONAL_KINSHIP_NOUNS) + r")\b",
    re.IGNORECASE,
)
_IDENTITY_DOMAIN_PATTERN = re.compile(
    r"\bmy\s+(" + "|".join(re.escape(d) for d in RELATIONAL_IDENTITY_DOMAINS) + r")\b",
    re.IGNORECASE,
)


def _matches_relational_query(q: str) -> bool:
    """Return True if the query is about the user's personal relationships
    or identity domains — "my son", "my partner", "my health".

    The possessive prefix is required. Naked "son" or "health" in a general
    knowledge query should not trigger; only the possessive form signals a
    personal identity query that must ground against first-person memory.
    """
    if not q:
        return False
    if _KINSHIP_PATTERN.search(q):
        return True
    if _IDENTITY_DOMAIN_PATTERN.search(q):
        return True
    return False


@dataclass
class ContextPolicy:
    name: str
    memory_weight: float = 1.0
    reflection_weight: float = 1.0
    recency_bias: float = 0.0
    diversity: bool = False
    prefer_experiences: bool = False
    prefer_active_work: bool = False
    prefer_exact_matches: bool = False
    # Signals to the ranker how much to boost state items for this query intent.
    # 0.0 = no special boost (default). Higher values prioritise current state
    # over memory and reflections — used for status/state queries.
    state_boost: float = 0.0
    # When True, ContextService will call web_search() and inject results into
    # the ContextPacket before prompt assembly.
    use_web_search: bool = False
    # True when the user explicitly asked to search ("google that", "look it
    # up", "search the web"). Explicit requests bypass ask-first — the user's
    # own words ARE the confirmation.
    explicit_search_request: bool = False
    # ADR-018: Intent-aware memory type gating.
    # eligible_memory_types: which types are candidates. None = all eligible.
    # suppress_memory_types: types excluded from candidates.
    # min_score: floor below which candidates are excluded before ranking.
    eligible_memory_types: list[str] | None = None
    suppress_memory_types: list[str] = field(default_factory=list)
    min_score: float = 0.25


# Explicit web-search phrases. These always bypass ask-first — the user's
# own words ARE the confirmation (ADR-034 behavioral contract rule 3).
# "can you find" was previously here in bare form but was overly broad —
# "can you find my current focus" is a vault lookup, not a web request.
_EXPLICIT_WEB_MARKERS: tuple[str, ...] = (
    "search the web",
    "search online",
    "look up online",
    "look this up",
    "look it up",
    "google",
    "find online",
    "find this online",
    "find it online",
    "can you find online",
    "can you find it online",
    "web search",
)


def _web_search_policy(explicit: bool) -> ContextPolicy:
    """Construct the shared web_search policy shape.

    `explicit=True` means the user explicitly requested a search — ask-first
    is bypassed. `explicit=False` means the intent classifier routed here;
    ask-first applies.
    """
    return ContextPolicy(
        name="web_search",
        memory_weight=0.5,
        reflection_weight=0.3,
        recency_bias=0.0,
        diversity=False,
        use_web_search=True,
        explicit_search_request=explicit,
    )


def classify_query(user_message: str) -> ContextPolicy:
    q = user_message.lower()
    # Normalize curly apostrophes to straight so markers like "what's" match
    # regardless of input source (Open WebUI, mobile keyboards, etc.)
    q = q.replace("\u2018", "'").replace("\u2019", "'")
    logger.warning("[CLASSIFY] normalized query: %s", q[:120])

    # Stage 0: explicit web-search request. User-stated instruction, not
    # intent inference — bypass ask-first and skip the intent classifier.
    if any(marker in q for marker in _EXPLICIT_WEB_MARKERS):
        logger.warning("[CLASSIFY] intent=web_search trigger=explicit")
        return _web_search_policy(explicit=True)

    # Intent classifier (ADR-034): replaces the legacy multi-trigger keyword
    # block. classify_intent runs Stage 1 (regex) → Stage 2 (embedding) →
    # Stage 3 (LLM with timeout) and always returns one of the two labels.
    # On needs_internet, route to web_search with ask-first applied.
    if classify_intent(user_message) == NEEDS_INTERNET:
        return _web_search_policy(explicit=False)

    state_markers = (
        "what am i working on",
        "what are my open loops",
        "what is my current focus",
        "what's my current focus",
        "current focus",
        "open loops",
        "what are my priorities",
        "active projects",
        "what are my blockers",
        "my blockers",
        "what is blocking",
        "what's blocking",
        "current state",
        "where am i at",
        "what am i focused on",
        "catch me up",
        "remind me what",
        # Routines / habits / schedule — operational state, not "recent
        # activity". Checked before recent_markers below so these route to
        # status_state with state_boost rather than to recent.
        "my routine",
        "my routines",
        "my morning",
        "my schedule",
        "my habits",
    )

    reflective_markers = (
        "pattern",
        "patterns",
        "theme",
        "themes",
        "trend",
        "trends",
        "what do you notice",
        "what have you noticed",
        "reflect",
        "reflection",
        "summarize me",
        "summarise me",
        # Personal retrospective queries — asking about recurring
        # experiences, typical behavior, or self-awareness over time.
        # Without these markers, health/energy queries like "I'm exhausted,
        # what do I usually do" fall to default policy, which triggers the
        # relevance gate and suppresses non-profile vault content.
        "struggling",
        "what do i usually",
        "what have i been",
        "how have i been",
    )

    recent_markers = (
        "recently",
        "lately",
        "today",
        "this week",
        "these days",
        "currently",
        "right now",
    )

    activity_markers = (
        "working on",
        "building",
        "doing",
        "focused on",
        "making progress on",
        "up to",
    )

    factual_recall_markers = (
        "when did",
        "what did i say",
        "where did i mention",
        "find",
        "look up",
        "search",
        "recall",
        "remember when",
    )

    # Status/state queries resolve against current state first — checked before
    # reflective so "what am I working on" routes to state, not reflection.
    if any(marker in q for marker in state_markers):
        return ContextPolicy(
            name="status_state",
            memory_weight=0.6,
            reflection_weight=0.5,
            recency_bias=0.8,
            diversity=False,
            prefer_active_work=True,
            state_boost=2.0,
            # Status queries want operational context, not reference material
            eligible_memory_types=["state", "task", "project", "profile", "conversation"],
        )

    if any(marker in q for marker in reflective_markers):
        return ContextPolicy(
            name="reflective",
            memory_weight=0.7,
            reflection_weight=1.4,
            recency_bias=0.2,
            diversity=True,
            prefer_experiences=True,
        )

    if any(marker in q for marker in factual_recall_markers):
        return ContextPolicy(
            name="factual_recall",
            memory_weight=1.2,
            reflection_weight=0.4,
            recency_bias=0.3,
            diversity=False,
            prefer_exact_matches=True,
            # Factual queries want information records
            eligible_memory_types=["ingested", "reference", "profile", "conversation"],
        )

    if any(marker in q for marker in recent_markers) and any(
        marker in q for marker in activity_markers
    ):
        return ContextPolicy(
            name="recent_activity",
            memory_weight=1.3,
            reflection_weight=0.7,
            recency_bias=1.2,
            diversity=True,
            prefer_active_work=True,
        )

    if any(marker in q for marker in recent_markers):
        return ContextPolicy(
            name="recent",
            memory_weight=1.1,
            reflection_weight=0.9,
            recency_bias=1.0,
            diversity=True,
        )

    if any(marker in q for marker in activity_markers):
        return ContextPolicy(
            name="activity",
            memory_weight=1.2,
            reflection_weight=0.6,
            recency_bias=0.5,
            diversity=True,
            prefer_active_work=True,
        )

    return ContextPolicy(name="default")
