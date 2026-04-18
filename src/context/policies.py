from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("ember.policies")


# ---------------------------------------------------------------------------
# Layer 1: Entity-type web search triggers (volatile entity + state query)
# ---------------------------------------------------------------------------
# Dual-condition: query must match BOTH a volatile entity signal AND a
# state query pattern to trigger web search. This prevents false positives
# on vault-answerable questions while catching current-state questions
# about external, time-sensitive entities.
#
# Layer 2 (future, TDD watch item): prompt-based pre-classifier using
# a 50-token Ollama call when Layer 1 is uncertain. Not implemented.
# ---------------------------------------------------------------------------

VOLATILE_ENTITY_SIGNALS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    # Finance
    r"\b(?:price|cost|trading|stock|crypto|bitcoin|btc|ethereum|eth)\b",
    r"\b(?:interest rate|inflation|fed(?:eral)?|gdp|s&p|nasdaq|dow)\b",
    r"\b(?:market|earnings|ipo|dividend|yield|forex)\b",
    # Current roles
    r"\b(?:ceo|president|prime minister|chancellor)\b",
    r"\bwho (?:is|runs|leads|heads|founded)\b",
    # Culture / entertainment
    r"\b(?:trending|popular|top rated|number one|box office)\b",
    r"\b(?:billboard|grammy|oscar|emmy|golden globe)\b",
    r"\b(?:movies?|shows?|albums?|games?|songs?|episodes?|series)\b",
    r"\b(?:streaming|netflix|spotify|disney|hbo|amazon prime)\b",
    # Current events
    r"\b(?:war|election|vote|policy|legislation|law|sanction)\b",
    r"\b(?:weather|forecast|temperature|hurricane|earthquake|wildfire)\b",
    r"\b(?:score|match|standings|playoff|championship|tournament)\b",
    r"\b(?:nba|nfl|mlb|nhl|premier league|formula 1|f1|wimbledon)\b",
    # Current state markers — "current" added because "what
    # is the current population of Tokyo" was missed because the word list
    # had "currently" but not the bare adjective form.
    r"\b(?:current|currently|still|now|latest|newest|most recent|right now|these days)\b",
    # Demographics / statistics — volatile at national/city scale
    r"\b(?:population|gdp per capita|unemployment rate|birth rate|death rate|census)\b",
    # Version / release queries — facts that change frequently
    r"\bwhat version\b",
    r"\blatest (?:version|release|update)\b",
    r"\bcurrent (?:version|release|update)\b",
    r"\bwhen did .* (?:release|launch|ship|come out)\b",
))

STATE_QUERY_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    # What/who/where/how + auxiliary or common past-tense event verb
    # Added "trading" and "currently" to catch "what is NVDA trading at"
    r"(?:^|\b)(?:what|who|where|when|how|how much|how many)\b.*\b(?:is|are|does|do|has|have|was|were|did|won|released|announced|scored|traded|happened|trading|currently)\b",
    # Auxiliary-first questions (yes/no form)
    r"^(?:is|are|does|do|has|have|can|will|did)\b",
    # Contractions
    r"^(?:what's|who's|where's|how's)\b",
    # Short queries with time/state marker (e.g. "bitcoin now", "weather today")
    # Excludes possessive forms (my, our) which indicate vault queries
    r"(?<!\bmy\s)(?<!\bour\s)\b(?:now|today|right now|this (?:week|month|year))\b",
))


def _matches_volatile_entity(q: str) -> bool:
    """Return True if the query references a volatile external entity."""
    return any(p.search(q) for p in VOLATILE_ENTITY_SIGNALS)


def _matches_state_query(q: str) -> bool:
    """Return True if the query is a present-tense state question."""
    return any(p.search(q) for p in STATE_QUERY_PATTERNS)


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


# ---------------------------------------------------------------------------
# Tier 2: Implicit recency — always triggers web search alone
# ---------------------------------------------------------------------------

IMPLICIT_RECENCY_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:this (?:month|week|weekend|season|year|quarter))\b",
    r"\b(?:last (?:month|week|weekend|few weeks|few days))\b",
    r"\b(?:upcoming|scheduled)\b",
    r"\bjust (?:announced|released|launched)\b",
    r"\bnewly (?:released|launched|opened)\b",
))

# ---------------------------------------------------------------------------
# Tier 3: Episodic event domains — require event-class structure to trigger
# ---------------------------------------------------------------------------

EPISODIC_EVENT_DOMAINS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    # Business
    r"\b(?:layoffs?|merger|acquisition|bankruptcy|funding round|scandal|recall|lawsuit)\b",
    # Entertainment
    r"\b(?:premiere|premiered|new (?:season|episode|film)|box office|award show|cancelled|renewed)\b",
    # Sports
    r"\b(?:standings|draft|trade|transfer)\b",
    # Political
    r"\b(?:election|voted?|passed|signed into law|appointed|resigned)\b",
))

# ---------------------------------------------------------------------------
# Tier 3b: "What happened" syntactic patterns — always trigger
# ---------------------------------------------------------------------------

WHAT_HAPPENED_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bwhat\b.*\b(?:happened|is happening|are happening|going on)\b",
    r"\bwhat\b.*\bto (?:watch|see|know|read)\b",
    r"\b(?:this|next) (?:weekend|week|month|season)\b",
))


def _matches_implicit_recency(q: str) -> bool:
    return any(p.search(q) for p in IMPLICIT_RECENCY_PATTERNS)


def _matches_episodic_event(q: str) -> bool:
    return any(p.search(q) for p in EPISODIC_EVENT_DOMAINS)


def _matches_what_happened(q: str) -> bool:
    return any(p.search(q) for p in WHAT_HAPPENED_PATTERNS)


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


def classify_query(user_message: str) -> ContextPolicy:
    q = user_message.lower()
    # Normalize curly apostrophes to straight so markers like "what's" match
    # regardless of input source (Open WebUI, mobile keyboards, etc.)
    q = q.replace("\u2018", "'").replace("\u2019", "'")
    logger.warning("[CLASSIFY] normalized query: %s", q[:120])

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

    # Checked before factual_recall — "search the web" is more specific than
    # the bare "search" marker in factual_recall_markers.
    web_search_markers = (
        "search the web",
        "search online",
        "look up online",
        "look this up",
        "look it up",
        "google",
        "what's the latest",
        "what is the latest",
        "current news",
        "news about",
        "find online",
        "find this online",
        "find it online",
        "can you find",
        "web search",
    )

    # Factual uncertainty — the user is asking whether something external
    # is true. These never resolve from the vault.
    factual_uncertainty_markers = (
        "is it true that",
        "has there been",
        "did they announce",
        "have they released",
        "is there a new",
        "what's happening with",
        "what is happening with",
        "has anyone",
        "have they",
    )

    # Temporal currency — temporal word + external-event context word.
    # "What happened yesterday" → web search.
    # "What did I do yesterday" → vault (recent_markers handles this).
    # The compound requirement prevents false positives on personal
    # temporal queries. Ask-first mode is the safety net.
    temporal_currency_words = (
        "yesterday", "last night", "this morning",
        "last week", "this month", "over the weekend",
    )
    event_context_words = (
        "happened", "news", "announced", "released", "launched",
        "update on", "latest on", "going on with", "situation with",
        "election", "game", "match", "score", "weather",
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

    # Web search: three trigger paths, all route to the same policy.
    # 1. Explicit markers ("search the web", "google", etc.)
    _explicit_web = any(marker in q for marker in web_search_markers)
    # 2. Factual uncertainty ("is it true that", "has there been", etc.)
    _factual_uncertainty = any(marker in q for marker in factual_uncertainty_markers)
    # 3. Temporal currency compound: temporal word + event context word.
    #    Requires both — "yesterday" alone is personal (vault), but
    #    "what happened yesterday" is external (web).
    _temporal_currency = (
        any(t in q for t in temporal_currency_words)
        and any(e in q for e in event_context_words)
    )
    # 4. Layer 1 entity-type: volatile entity signal + state query pattern.
    #    Dual-condition prevents false positives on vault-answerable
    #    questions while catching "What is the current price of Bitcoin?"
    #    or "Who is the CEO of OpenAI?"
    _entity_trigger = _matches_volatile_entity(q) and _matches_state_query(q)
    # 5. Implicit recency: "this week", "last month", "just announced", etc.
    #    Always triggers — any implicit recency marker = web search.
    _implicit_recency = _matches_implicit_recency(q)
    # 6. Episodic event domain + state query: "layoffs", "premiere", etc.
    #    Requires event-class structure (episodic domain word present) to
    #    reduce false positives on vault-answerable election/trade discussions.
    _episodic_event = _matches_episodic_event(q) and _matches_state_query(q)
    # 7. "What happened" syntactic patterns: always triggers.
    _what_happened = _matches_what_happened(q)

    if (_explicit_web or _factual_uncertainty or _temporal_currency
            or _entity_trigger or _implicit_recency or _episodic_event or _what_happened):
        _trigger = (
            "explicit" if _explicit_web
            else "factual_uncertainty" if _factual_uncertainty
            else "temporal_currency" if _temporal_currency
            else "entity_type" if _entity_trigger
            else "implicit_recency" if _implicit_recency
            else "episodic_event" if _episodic_event
            else "what_happened"
        )
        logger.warning("[CLASSIFY] intent=web_search trigger=%s", _trigger)
        return ContextPolicy(
            name="web_search",
            memory_weight=0.5,
            reflection_weight=0.3,
            recency_bias=0.0,
            diversity=False,
            use_web_search=True,
            explicit_search_request=_explicit_web,
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
