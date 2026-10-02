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

# Bumped to 2 for ADR-044. load_run refuses anything but exact equality and
# reconstructs every sub-record by keyword, so removing DecayActivation and
# the dead activation fields makes older traces unloadable -- which is the
# intended behaviour, not a migration to write: a trace captured under the
# old composition describes a pipeline that no longer exists, and
# reinterpreting it would produce numbers about nothing.
#
# Bumped to 3 for #227, and this one is a SEMANTIC change with no structural
# signature, which is the reason the fields were renamed rather than
# reinterpreted. `delivered_refs` held the context packet -- 4 to 6 non-profile
# memory records -- while the prompt renders 4. The layout of a v2 trace under
# the new meaning is byte-identical: same names, same types, same lengths. It
# would have loaded cleanly and reported numbers about a set the model never
# receives. `rendered_refs` / `rendered_reflection_refs` / `rendered` make the
# keyword reconstruction below fail on an old trace instead, which is the only
# detector available: the one test that compares the two definitions is
# xfail'd under #244.
#
# Bumped to 4 for #255, and this is the EASY kind of bump, in deliberate contrast
# to the one above: `doc_group` and `title_group` are additive optional fields, so
# the change has a structural signature. A version-3 trace lacks the keys and
# `load_run`'s keyword reconstruction fails on it, which is the detector working
# rather than a reinterpretation to guard against.
SCHEMA_VERSION = 4

# Channels a candidate can arrive through. They score differently and must
# not be pooled: the reflection channel never passes through
# semantic_search, so it has no retrieval terms and its base score is a
# Jaccard overlap on a different scale entirely.
CHANNEL_MEMORY = "memory"
CHANNEL_PROFILE = "profile"
CHANNEL_REFLECTION = "reflection"


def content_fingerprint(content: str) -> str:
    return hashlib.sha256(" ".join((content or "").split()).lower().encode()).hexdigest()


def group_fingerprint(value: str | None) -> str | None:
    """A document or title identity, as a hash rather than the value.

    `_diversity_score` only ever tests these for EQUALITY between two candidates
    (service.py's `existing_metadata.get("doc_id") == candidate_doc_id`), so a
    hash carries everything the selector needs and nothing it does not. The
    values themselves are vault content -- a title is free text and a doc_id can
    be a path -- and a trace is a file on disk.

    None in, None out: absent is a different fact from present-and-unmatched, and
    the penalty's guard is `if candidate_doc_id and ...`, so collapsing absence to
    a hash of the empty string would make every doc-less candidate share a group.
    """
    if not value:
        return None
    return hashlib.sha256(f"group:{value}".encode()).hexdigest()[:16]


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
    # type_branch survives the removal of ret.type.* because
    # query_intent_adjustment still keys on it. It is no longer a selector for
    # a type term of its own.
    type_branch: str = "other"           # conversation|reflection|memory|ingested|other
    # No role field. The four quality_* booleans went with
    # source_quality_adjustment, and role followed them: under ADR-044 4a role
    # is a SELECTION decision, and CandidateTrace.excluded_by_role records its
    # outcome from the shipped predicate. A descriptive copy of the role column
    # here would be a second reading of it that nothing consumes.
    intent_reflective: bool = False
    intent_task: bool = False
    content_prefix: str = "none"         # user|assistant|none


@dataclass
class PolicyActivation:
    weight_field: str = "memory_weight"  # memory_weight|reflection_weight
    weight_captured: float = 1.0
    # recency_bias_captured and a policy-stage recency_bucket stood here. Both
    # existed only for the `recency * recency_bias` term, which ADR-044
    # removed along with ContextPolicy.recency_bias.
    prefer_experience_fired: bool = False
    prefer_active_work_fired: bool = False
    exact_branch: str = "none"           # question|other|none
    tier_branch: str = "hot"             # cold|warm|hot|profile_bypass


@dataclass
class AuthorshipActivation:
    # Whether the authorship stage runs on this candidate's channel at all.
    # False for reflections: service.py:268-269 applies role_predicate.apply and
    # apply_authorship_scoring to memory_items only. Same role `applies` plays on
    # RetrievalActivation, and here for the same reason -- a stage that does not
    # run on a channel has to be recorded as absent rather than as an identity,
    # or the model has no way to tell "ran and did nothing" from "never ran".
    applies: bool = True
    relational_query: bool = False
    branch: str = "first_person"         # first_person|mixed|third_party|unknown
    project_match: bool = False


@dataclass
class RankActivation:
    """_score_memory_item, or _score_reflection_item on the reflection channel."""

    reflection_path: bool = False
    kind_branch: str = "none"            # experience|user_content|answer|question|none
    length_branch: str = "none"          # lt50|gt1200|none
    recency_bucket: str = "unparsed"


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

    # The two identities `_diversity_score` compares between candidates, hashed.
    # #255: without these the selector ran in replay with `same_doc_penalty` and
    # `same_title_penalty` pinned to zero, because _DiversityShim had no metadata
    # to read and the trace had nothing to give it. content_sha256 cannot stand in
    # -- two chunks of one document have different bodies, so different
    # fingerprints, which is precisely the case the 0.22/chunk term exists for.
    doc_group: str | None = None
    title_group: str | None = None

    retrieval: RetrievalActivation = field(default_factory=RetrievalActivation)
    policy: PolicyActivation = field(default_factory=PolicyActivation)
    author: AuthorshipActivation = field(default_factory=AuthorshipActivation)
    rank: RankActivation = field(default_factory=RankActivation)

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
    # ADR-044 4a. The role predicate drops assistant-authored records between
    # apply_policy and the authorship multiplier, which is a membership change
    # with no score signature -- so without this flag a replay reconstructs
    # the scores correctly and over-delivers, and the fidelity check cannot
    # see why.
    excluded_by_role: bool = False
    deduped_out: bool = False
    rendered: bool = False


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
    rendered_refs: list[str] = field(default_factory=list)
    rendered_reflection_refs: list[str] = field(default_factory=list)


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
        """Write the trace, refusing any destination inside ANY git work tree.

        The refusal is not politeness. A trace carries real query text and
        real content, and the one mistake that turns this tool into a
        vault-privacy incident is writing it somewhere a commit can pick it
        up.

        "Inside the repository" was too narrow, and the gap was live. The
        documented default output directory is `~/.ember_traces`, and on the
        reference machine the home directory is itself the work tree of an
        unrelated repository whose ignore rules do not match `.ember_traces`.
        A trace written to the default location was therefore one `git add .`
        in a different project away from being staged, and this guard -- which
        only knew about ember-2 -- said nothing. Untracked is not safe;
        untracked-and-unignored is the state `git add .` sweeps up.

        So the test is "is any ancestor a git work tree", not "is it this
        repo". The presence of `.git` in a directory is the check: one walk up
        the path, no subprocess, so it cannot fail open because git is absent
        from PATH.
        """
        path = Path(path).resolve()

        # One walk, no special case for this repo: ember-2's own root contains
        # .git, so the general check subsumes the narrower one that stood here.
        for ancestor in [path, *path.parents]:
            if (ancestor / ".git").exists():
                raise ValueError(
                    f"refusing to write a trace inside a git work tree: "
                    f"{ancestor} contains .git (target was {path}). A trace "
                    "carries real query text and content. Write it somewhere "
                    "no repository can reach -- being untracked is not enough, "
                    "because `git add .` stages untracked files. See CLAUDE.md "
                    "Vault Privacy Rule."
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
                    }
            )
            for c in q.pop("candidates", [])
        ]
        queries.append(QueryTrace(candidates=candidates, **q))
    return TraceRun(queries=queries, **raw)
