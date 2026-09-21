"""
tools/retrieval_trace/schema.py

What a trace stores, and what it deliberately does not.

A trace records ACTIVATIONS, not term values: "the role branch that fired
was user", "three query terms hit", "the decay family was ephemeral and the
bucket was d14". The value of a term is then params[name] * activation,
computed at replay time. Storing the value instead would freeze the
parameter into the data and make the whole exercise circular -- you cannot
perturb a number you have already multiplied in.

Vault privacy (CLAUDE.md). A trace carries the real query text, because the
query drives every lexical and intent branch and a sensitivity pass over
synthetic queries would be measuring a different system. Candidate content
is NOT carried by default: every predicate the pipeline evaluates over
content is already reduced to a boolean or a count by the time it reaches
this record, so the content itself is redundant for replay. What is stored
in its place is a SHA-256 of the normalized text and its length, which is
enough to join two traces of the same corpus without carrying the corpus.
`include_content=True` overrides this for a debugging run; traces are
written outside the repo either way and the writer refuses a path inside it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

# Channels a candidate can arrive through. They score differently and must
# not be pooled: the reflection channel never passes through
# semantic_search, so it has no retrieval terms and its base score is a
# Jaccard overlap on a different scale entirely.
CHANNEL_MEMORY = "memory"
CHANNEL_PROFILE = "profile"
CHANNEL_REFLECTION = "reflection"


def content_fingerprint(content: str) -> str:
    return hashlib.sha256(" ".join((content or "").split()).lower().encode()).hexdigest()


@dataclass
class RetrievalActivation:
    """Which retrieval-stage branches fired, for one candidate.

    Empty for the reflection channel, which does not pass through
    semantic_search at all.
    """

    applies: bool = False
    raw_cosine: float = 0.0
    lexical_substring: bool = False
    lexical_term_hits: int = 0
    lexical_entity_hits: int = 0
    type_branch: str = "other"           # conversation|reflection|memory|ingested|other
    quality_role: str = "none"           # user|assistant|none
    quality_is_question: bool = False
    quality_clarification: bool = False
    quality_experience: bool = False
    quality_summary: bool = False
    intent_reflective: bool = False
    intent_task: bool = False
    content_prefix: str = "none"         # user|assistant|none


@dataclass
class PolicyActivation:
    weight_field: str = "memory_weight"  # memory_weight|reflection_weight
    weight_captured: float = 1.0
    recency_bias_captured: float = 0.0
    recency_bucket: str = "unparsed"     # d7|d30|d90|d365|older|unparsed
    prefer_experience_fired: bool = False
    prefer_active_work_fired: bool = False
    exact_branch: str = "none"           # question|other|none
    tier_branch: str = "hot"             # cold|warm|hot|profile_bypass


@dataclass
class AuthorshipActivation:
    relational_query: bool = False
    branch: str = "first_person"         # first_person|mixed|third_party|unknown
    project_match: bool = False


@dataclass
class RankActivation:
    """_score_memory_item, or _score_reflection_item on the reflection channel."""

    reflection_path: bool = False
    type_branch: str = "other"
    role_branch: str = "none"            # user|assistant|tool_system|none
    kind_branch: str = "none"            # experience|user_content|answer|question|none
    user_prefix: bool = False
    length_branch: str = "none"          # lt20|lt50|gt1200|none
    tokens_lt5: bool = False
    recency_bucket: str = "unparsed"
    reflection_short: bool = False


@dataclass
class DecayActivation:
    family: str = "none"                 # none|reflection|ephemeral|default
    bucket: str = "none"                 # d3|d7|d14|d30|d90|older|none


@dataclass
class CandidateTrace:
    """One candidate, fully attributable.

    stage_scores are GROUND TRUTH, read off the real pipeline functions
    rather than reconstructed. The activations above are the model of how
    those numbers arose. Capture checks the model against the ground truth
    per stage and refuses to write a trace where they disagree, which is
    the only reason to trust a replay at all.
    """

    ref: str
    store_id: str | None
    channel: str
    memory_type: str
    item_type: str
    tier: str
    authorship: str
    timestamp: str | None
    age_days: int | None
    content_sha256: str
    content_length: int
    content: str | None = None

    retrieval: RetrievalActivation = field(default_factory=RetrievalActivation)
    policy: PolicyActivation = field(default_factory=PolicyActivation)
    author: AuthorshipActivation = field(default_factory=AuthorshipActivation)
    rank: RankActivation = field(default_factory=RankActivation)
    decay: DecayActivation = field(default_factory=DecayActivation)

    # Ground truth, one entry per stage boundary.
    stage_scores: dict[str, float] = field(default_factory=dict)
    composed_score: float = 0.0

    # Outcome flags. These are content-based, not score-based (the echo,
    # meta, low-value and dedup filters never read a score), so replay can
    # treat them as fixed while it perturbs the scoring parameters.
    type_eligible: bool = True           # the type half of the ADR-018 gate
    type_gated_out: bool = False         # type half AND the min_score half
    relevance_gated_out: bool = False
    filtered_echo_or_meta: bool = False
    filtered_low_value: bool = False
    deduped_out: bool = False
    delivered: bool = False


@dataclass
class QueryTrace:
    query_id: str
    query: str
    policy_name: str
    policy: dict
    relational_query: bool
    project_id: str | None
    memory_limit: int
    reflection_limit: int
    diversity: bool
    min_score: float
    relevance_gate_fired: bool
    candidates: list[CandidateTrace] = field(default_factory=list)
    delivered_refs: list[str] = field(default_factory=list)
    delivered_reflection_refs: list[str] = field(default_factory=list)


@dataclass
class TraceRun:
    schema_version: int
    captured_at: str
    vault_fingerprint: str
    db_digest_before: str
    db_digest_after: str
    param_defaults: dict[str, float]
    include_content: bool
    pool_limit: int
    pool_widened: bool
    queries: list[QueryTrace] = field(default_factory=list)

    @property
    def digest_unchanged(self) -> bool:
        return self.db_digest_before == self.db_digest_after

    def policies_covered(self) -> set[str]:
        return {q.policy_name for q in self.queries}

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=True)

    def write(self, path: Path) -> Path:
        """Write the trace, refusing any destination inside the repository.

        The refusal is not politeness. A trace carries real query text, and
        the one mistake that turns this tool into a vault-privacy incident
        is writing it somewhere a commit can pick it up.
        """
        path = Path(path).resolve()
        repo_root = Path(__file__).resolve().parents[2]
        if repo_root == path or repo_root in path.parents:
            raise ValueError(
                f"refusing to write a trace inside the repository ({path}). "
                "Traces carry real query text; keep them outside the working "
                "tree. See CLAUDE.md Vault Privacy Rule."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def load_run(path: Path) -> TraceRun:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"trace schema {raw.get('schema_version')} != {SCHEMA_VERSION}; "
            "recapture rather than reinterpreting an older layout"
        )
    queries = []
    for q in raw.pop("queries", []):
        candidates = [
            CandidateTrace(
                **{
                    **c,
                    "retrieval": RetrievalActivation(**c["retrieval"]),
                    "policy": PolicyActivation(**c["policy"]),
                    "author": AuthorshipActivation(**c["author"]),
                    "rank": RankActivation(**c["rank"]),
                    "decay": DecayActivation(**c["decay"]),
                }
            )
            for c in q.pop("candidates", [])
        ]
        queries.append(QueryTrace(candidates=candidates, **q))
    return TraceRun(queries=queries, **raw)
