"""
tools/retrieval_trace/capture.py

Capture: run real queries through the real pipeline and record, per
candidate, every branch that fired and the score at every stage boundary.

Two rules hold this together.

1. PREDICATES ARE IMPORTED, NEVER REIMPLEMENTED. Every "did this branch
   fire" question is answered by calling the shipped function --
   is_question_like, _looks_like_active_work, _matches_relational_query,
   _parse_age_days, and the rest. Only the CONSTANTS are modeled, in
   params.py. A trace can therefore go stale on a retune, which
   tests/test_retrieval_trace.py catches, but it cannot go stale on a
   change of meaning without the stage check below failing first.

2. EVERY STAGE IS CHECKED AGAINST THE REAL PIPELINE. The stage walk below
   calls ContextRanker.apply_policy, apply_authorship_scoring,
   apply_project_boost, _score_memory_item and _temporal_decay_weight
   directly and reads the score off the item after each one. Then
   compose.py recomputes the same stages from the recorded activations at
   default parameters, and capture refuses to write a trace where the two
   disagree. This is the lesson from the incident-reproduction suite, which
   ran green for a while against arms it could not actually distinguish
   because its runner had quietly bypassed the stage it claimed to test. A
   harness that cannot fail is not evidence.

Read-only throughout: read_only=True on the packet build and the whole run
inside retrieval_stats_disabled() (#206, #228). The caller digests the
databases either side and the digest goes in the trace.
"""

from __future__ import annotations

import copy
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.context.policies import _matches_relational_query, classify_query
from src.context.ranker import ContextRanker
from src.context.service import ContextService
from src.core.config import get_private_vault_path
from src.retrieval.retrieval_stats import retrieval_stats_disabled
from src.retrieval.semantic_search import (
    contains_clarification_language,
    extract_query_terms,
    is_question_like,
    is_reflective_query,
    is_task_or_work_query,
    looks_like_concrete_experience,
    looks_like_summary_or_instruction,
    normalize_text,
)

from .compose import STAGES, compose
from .params import ReplayParams, default_params
from .schema import (
    CHANNEL_MEMORY,
    CHANNEL_PROFILE,
    CHANNEL_REFLECTION,
    SCHEMA_VERSION,
    AuthorshipActivation,
    CandidateTrace,
    DecayActivation,
    PolicyActivation,
    QueryTrace,
    RankActivation,
    RetrievalActivation,
    TraceRun,
    content_fingerprint,
)

# How close a recomputation has to be to the real pipeline before the trace
# is trusted. Not zero: the model reproduces the shipped grouping of float
# additions, but a stage that multiplies then adds can still land one ulp
# away on a different machine. 1e-12 is far below any score difference that
# could reorder two candidates and far above float noise.
STAGE_TOLERANCE = 1e-12

TIERING_DATABASES = ("memory.db", "ingested.db")


class TraceValidationError(RuntimeError):
    """The recorded activations do not reproduce the real pipeline."""


def database_digest() -> str:
    """Digest over every column a delivery can change, across both stores."""
    parts = []
    for name in TIERING_DATABASES:
        path = get_private_vault_path() / "embeddings" / name
        if not path.exists():
            parts.append(f"{name}:absent")
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(vectors)")}
            wanted = [
                c
                for c in (
                    "id",
                    "last_retrieved_at",
                    "frequency_score",
                    "tier",
                    "heat_score",
                    "retrieval_count",
                )
                if c in columns
            ]
            rows = conn.execute(
                f"SELECT {', '.join(wanted)} FROM vectors ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        parts.append(f"{name}:{hashlib.sha256(repr(rows).encode()).hexdigest()}")
    return "|".join(parts)


def vault_fingerprint() -> str:
    """Identifies the corpus a trace was taken against, without naming it.

    Row counts per store, hashed. Two traces with the same fingerprint are
    comparable; two with different fingerprints are not, and a sensitivity
    result carried across that boundary is meaningless.
    """
    parts = []
    for name in TIERING_DATABASES:
        path = get_private_vault_path() / "embeddings" / name
        if not path.exists():
            parts.append(f"{name}:0")
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        finally:
            conn.close()
        parts.append(f"{name}:{count}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Activation readers. Each one answers "which branch fired" by calling the
# shipped predicate, never by restating its rule.
# ---------------------------------------------------------------------------

_RETRIEVAL_TYPE_BRANCHES = {"conversation", "reflection", "memory", "ingested"}
_RANK_TYPE_BRANCHES = {"conversation", "reflection", "memory", "ingested"}


def _retrieval_metadata(item, channel: str) -> dict:
    """The metadata dict semantic_search saw, which is not always item.metadata.

    get_memory_items enriches the store's metadata and hands the enriched
    dict to ContextItem. get_profile_items hands over the whole search
    RESULT, so the store metadata sits one level down under "metadata".
    Reading the wrong one silently zeroes every role-derived term on the
    profile channel, which is exactly the class of mistake the stage check
    exists to catch -- but getting it right here means the check passes for
    the right reason.
    """
    metadata = getattr(item, "metadata", {}) or {}
    if channel == CHANNEL_PROFILE and isinstance(metadata.get("metadata"), dict):
        return metadata["metadata"]
    return metadata


def _role_branch(content: str, metadata: dict) -> str:
    """source_quality_adjustment's role ladder: metadata first, prefix second."""
    role = metadata.get("role", "")
    if role == "user":
        return "user"
    if role == "assistant":
        return "assistant"
    if content.startswith("user:"):
        return "user"
    if content.startswith("assistant:"):
        return "assistant"
    return "none"


def _content_prefix(content: str) -> str:
    if content.startswith("user:"):
        return "user"
    if content.startswith("assistant:"):
        return "assistant"
    return "none"


def _recency_bucket(age_days: int | None) -> str:
    if age_days is None:
        return "unparsed"
    if age_days <= 7:
        return "d7"
    if age_days <= 30:
        return "d30"
    if age_days <= 90:
        return "d90"
    if age_days <= 365:
        return "d365"
    return "older"


def _decay_activation(item, ranker: ContextRanker, age_days: int | None) -> DecayActivation:
    mem_type = getattr(item, "memory_type", "") or ""
    if mem_type in ranker._NO_DECAY_TYPES or age_days is None:
        return DecayActivation(family="none", bucket="none")

    if mem_type == "reflection":
        family, tiers = "reflection", ranker._REFLECTION_DECAY
    elif mem_type in ranker._EPHEMERAL_TYPES:
        family, tiers = "ephemeral", ranker._EPHEMERAL_DECAY
    else:
        family, tiers = "default", ranker._DEFAULT_DECAY

    for max_age, _weight in tiers:
        if max_age is None:
            return DecayActivation(family=family, bucket="older")
        if age_days <= max_age:
            return DecayActivation(family=family, bucket=f"d{max_age}")
    return DecayActivation(family="none", bucket="none")


def _retrieval_activation(item, channel: str, query: str) -> RetrievalActivation:
    if channel == CHANNEL_REFLECTION:
        return RetrievalActivation(applies=False)

    content = getattr(item, "content", "") or ""
    normalized_content = normalize_text(content)
    normalized_query = normalize_text(query)
    query_terms = extract_query_terms(normalized_query)
    # Two dicts, deliberately. The role ladder reads the store metadata that
    # semantic_search was handed; raw_score is written one level up, onto the
    # result itself. On the profile channel those are different objects, and
    # taking both from one of them loses either the role terms or the cosine.
    metadata = _retrieval_metadata(item, channel)
    outer = getattr(item, "metadata", {}) or {}
    raw_score = outer.get("raw_score", metadata.get("raw_score", 0.0))
    mem_type = getattr(item, "memory_type", "") or ""

    from src.retrieval.semantic_search import _extract_entity_names

    entity_names = _extract_entity_names(query)
    entity_hits = sum(1 for name in entity_names if name in normalized_content)

    return RetrievalActivation(
        applies=True,
        raw_cosine=float(raw_score or 0.0),
        lexical_substring=bool(normalized_query and normalized_query in normalized_content),
        lexical_term_hits=sum(1 for term in query_terms if term in normalized_content),
        lexical_entity_hits=entity_hits,
        type_branch=mem_type if mem_type in _RETRIEVAL_TYPE_BRANCHES else "other",
        quality_role=_role_branch(normalized_content, metadata),
        quality_is_question=is_question_like(normalized_content),
        quality_clarification=contains_clarification_language(normalized_content),
        quality_experience=looks_like_concrete_experience(normalized_content),
        quality_summary=looks_like_summary_or_instruction(normalized_content),
        intent_reflective=is_reflective_query(normalized_query),
        intent_task=is_task_or_work_query(normalized_query),
        content_prefix=_content_prefix(normalized_content),
    )


def _policy_activation(item, policy, ranker: ContextRanker, age_days: int | None) -> PolicyActivation:
    content = (getattr(item, "content", "") or "").lower()
    metadata = getattr(item, "metadata", {}) or {}
    content_kind = metadata.get("content_kind")
    item_type = getattr(item, "item_type", "")
    mem_type = getattr(item, "memory_type", "") or ""

    if mem_type == "profile":
        tier_branch = "profile_bypass"
    else:
        tier = getattr(item, "tier", "hot") or "hot"
        tier_branch = tier if tier in {"cold", "warm", "hot"} else "hot"

    exact_branch = "none"
    if getattr(policy, "prefer_exact_matches", False):
        exact_branch = "question" if content_kind == "question" else "other"

    weight_field = "reflection_weight" if item_type == "reflection" else "memory_weight"

    return PolicyActivation(
        weight_field=weight_field,
        weight_captured=float(getattr(policy, weight_field, 1.0)),
        recency_bias_captured=float(getattr(policy, "recency_bias", 0.0) or 0.0),
        recency_bucket=_recency_bucket(age_days),
        prefer_experience_fired=bool(
            getattr(policy, "prefer_experiences", False)
            and (content_kind == "experience" or ranker._looks_like_experience(content))
        ),
        prefer_active_work_fired=bool(
            getattr(policy, "prefer_active_work", False)
            and ranker._looks_like_active_work(content, metadata)
        ),
        exact_branch=exact_branch,
        tier_branch=tier_branch,
    )


def _authorship_activation(item, query: str, project_id: str | None) -> AuthorshipActivation:
    authorship = getattr(item, "authorship", None)
    if not authorship:
        metadata = getattr(item, "metadata", {}) or {}
        authorship = metadata.get("authorship") or "unknown"
    if authorship not in {"first_person", "mixed", "third_party", "unknown"}:
        # apply_authorship_scoring's .get(authorship, 0.5) fallback: an
        # unrecognised tag scores as unknown, so record it as unknown rather
        # than inventing a branch the shipped code does not have.
        authorship = "unknown"

    metadata = getattr(item, "metadata", {}) or {}
    return AuthorshipActivation(
        relational_query=_matches_relational_query(query),
        branch=authorship,
        project_match=bool(project_id and metadata.get("project_id") == project_id),
    )


def _rank_activation(item, channel: str, ranker: ContextRanker, age_days: int | None) -> RankActivation:
    content = (getattr(item, "content", "") or "").lower().strip()
    bucket = _recency_bucket(age_days)

    if channel == CHANNEL_REFLECTION:
        return RankActivation(
            reflection_path=True,
            recency_bucket=bucket,
            reflection_short=len(content) < 30,
        )

    metadata = getattr(item, "metadata", {}) or {}
    item_type = getattr(item, "item_type", "")
    role = metadata.get("role")
    content_kind = metadata.get("content_kind")

    if role == "user":
        role_branch = "user"
    elif role == "assistant":
        role_branch = "assistant"
    elif role in {"tool", "system"}:
        role_branch = "tool_system"
    else:
        role_branch = "none"

    kind_branch = (
        content_kind
        if content_kind in {"experience", "user_content", "answer", "question"}
        else "none"
    )

    if len(content) < 20:
        length_branch = "lt20"
    elif len(content) < 50:
        length_branch = "lt50"
    elif len(content) > 1200:
        length_branch = "gt1200"
    else:
        length_branch = "none"

    return RankActivation(
        reflection_path=False,
        type_branch=item_type if item_type in _RANK_TYPE_BRANCHES else "other",
        role_branch=role_branch,
        kind_branch=kind_branch,
        user_prefix=content.startswith("user:"),
        length_branch=length_branch,
        tokens_lt5=len(ranker._tokenize(content)) < 5,
        recency_bucket=bucket,
    )


# ---------------------------------------------------------------------------
# The stage walk
# ---------------------------------------------------------------------------

@dataclass
class _Walked:
    trace: CandidateTrace
    item: object


def _walk_stages(items, channel, policy, query, project_id, ranker, include_content):
    """Run the real stage functions over copies, recording the score after each.

    Items are walked individually so one candidate's stage score is never
    contaminated by a list operation. apply_policy and friends take lists
    and mutate in place; a single-element list per candidate keeps the
    attribution clean and costs nothing at these sizes.
    """
    walked: list[_Walked] = []

    for index, source in enumerate(items):
        item = copy.deepcopy(source)
        content = getattr(item, "content", "") or ""
        age_days = ranker._parse_age_days(getattr(item, "timestamp", None))

        trace = CandidateTrace(
            ref="",  # assigned by the caller, which knows the channel offsets
            store_id=getattr(item, "store_id", None),
            channel=channel,
            memory_type=getattr(item, "memory_type", "") or "",
            item_type=getattr(item, "item_type", "") or "",
            tier=getattr(item, "tier", "hot") or "hot",
            authorship=getattr(item, "authorship", None) or "unknown",
            timestamp=getattr(item, "timestamp", None),
            age_days=age_days,
            content_sha256=content_fingerprint(content),
            content_length=len(content),
            content=content if include_content else None,
            retrieval=_retrieval_activation(item, channel, query),
            policy=_policy_activation(item, policy, ranker, age_days),
            author=_authorship_activation(item, query, project_id),
            rank=_rank_activation(item, channel, ranker, age_days),
            decay=_decay_activation(item, ranker, age_days),
        )

        # Stage 0: whatever retrieval produced. For memory and profile that
        # is semantic_search's composed score; for reflections it is the
        # Jaccard overlap from get_reflection_items.
        trace.stage_scores["retrieval"] = float(item.score)

        # The ADR-018 type gate, split into its two halves so replay can
        # re-apply the score half against a perturbed score.
        suppress = policy.suppress_memory_types
        eligible = policy.eligible_memory_types
        mem_type = getattr(item, "memory_type", None)
        trace.type_eligible = bool(
            mem_type == "profile"
            or (
                (not suppress or mem_type not in suppress)
                and (eligible is None or mem_type in eligible)
            )
        )
        trace.type_gated_out = not (
            mem_type == "profile"
            or (trace.type_eligible and float(item.score) >= policy.min_score)
        )

        ranker.apply_policy([item], policy)
        trace.stage_scores["policy"] = float(item.score)

        ranker.apply_authorship_scoring([item], query)
        trace.stage_scores["authorship"] = float(item.score)

        ranker.apply_project_boost([item], project_id)
        trace.stage_scores["project"] = float(item.score)

        if channel == CHANNEL_REFLECTION:
            ranker._score_reflection_item(item)
        else:
            ranker._score_memory_item(item)
        trace.stage_scores["rank"] = float(item.score)

        item.score = float(item.score) * ranker._temporal_decay_weight(item)
        trace.stage_scores["decay"] = float(item.score)
        trace.composed_score = float(item.score)

        walked.append(_Walked(trace=trace, item=item))

    return walked


def _validate(trace: CandidateTrace, policy_name: str, params: ReplayParams) -> list[str]:
    """Recompute from activations and report every stage that disagrees."""
    result = compose(trace, params, policy_name)
    problems = []
    for stage in STAGES:
        expected = trace.stage_scores[stage]
        actual = result.stage_scores[stage]
        if abs(expected - actual) > STAGE_TOLERANCE:
            problems.append(
                f"stage {stage}: pipeline {expected!r} != model {actual!r} "
                f"(delta {actual - expected:.3e})"
            )
    return problems


def _mark_dedup(service: ContextService, walked) -> None:
    ordered = sorted(walked, key=lambda w: w.trace.composed_score, reverse=True)
    survivors = [
        w
        for w in ordered
        if not (w.trace.filtered_echo_or_meta or w.trace.filtered_low_value)
    ]
    # build_context's own fallback: when the filters take everything, the
    # unfiltered ranked list is used instead.
    if not survivors:
        survivors = ordered

    seen: set[str] = set()
    for walk in survivors:
        key = service._normalize_text(walk.item.content)
        if key in seen:
            walk.trace.deduped_out = True
        else:
            seen.add(key)


def _relevance_gate_fired(policy, walked_memory) -> bool:
    """The default-policy relevance gate, recomputed from raw cosines.

    Restated from ContextService.build_context rather than observed from the
    packet, because observing it is ambiguous: an empty non-profile delivery
    can equally mean the gate fired or that every candidate lost its slot on
    score. The threshold is read from config, not hardcoded, so a config
    change moves this with it. _INGESTED_MIN_RAW has no config home in the
    shipped code, so it is restated here and pinned by a test.
    """
    if policy.name != "default":
        return False

    from src.core.config import get_retrieval_min_raw_score

    ingested_min_raw = 0.15
    standard = [
        w.trace.retrieval.raw_cosine
        for w in walked_memory
        if w.trace.memory_type not in {"profile", "ingested"}
    ]
    ingested = [
        w.trace.retrieval.raw_cosine
        for w in walked_memory
        if w.trace.memory_type == "ingested"
    ]
    return (
        max(standard, default=0.0) < get_retrieval_min_raw_score()
        and max(ingested, default=0.0) < ingested_min_raw
    )


# ---------------------------------------------------------------------------
# Query capture
# ---------------------------------------------------------------------------

def capture_query(
    query: str,
    query_id: str,
    *,
    service: ContextService | None = None,
    project_id: str | None = None,
    include_content: bool = False,
) -> QueryTrace:
    """Trace one query. Read-only; raises TraceValidationError on any drift."""
    service = service or ContextService()
    ranker = ContextRanker()
    policy = classify_query(query)

    state_items, task_items, memory_items, reflection_items, _emb = service.retriever.retrieve(
        query
    )

    # One retrieval, two consumers. build_context is handed deep copies of
    # the same candidate objects rather than being allowed to retrieve
    # again: a second retrieval is a second embedding call and a second
    # store read, and if it came back even slightly different the delivered
    # set would no longer belong to the candidates in this trace.
    packet_candidates = (
        copy.deepcopy(state_items),
        copy.deepcopy(task_items),
        copy.deepcopy(memory_items),
        copy.deepcopy(reflection_items),
        None,
    )

    with patch.object(service.retriever, "retrieve", return_value=packet_candidates), patch(
        "src.context.service.classify_query", return_value=policy
    ):
        packet = service.build_context(query, read_only=True, skip_web_search=True)

    walked_memory = _walk_stages(
        memory_items, CHANNEL_MEMORY, policy, query, project_id, ranker, include_content
    )
    walked_reflections = _walk_stages(
        reflection_items, CHANNEL_REFLECTION, policy, query, project_id, ranker, include_content
    )

    # The profile channel is not a separate list -- ContextRetriever.retrieve
    # concatenates profile items onto memory_items before returning. Label
    # them after the fact so their different metadata layout is visible in
    # the trace rather than being something a reader has to know.
    for walk in walked_memory:
        if walk.trace.memory_type == "profile":
            walk.trace.channel = CHANNEL_PROFILE
            walk.trace.retrieval = _retrieval_activation(
                walk.item, CHANNEL_PROFILE, query
            )

    for index, walk in enumerate(walked_memory):
        walk.trace.ref = f"m{index}"
    for index, walk in enumerate(walked_reflections):
        walk.trace.ref = f"r{index}"

    all_walked = walked_memory + walked_reflections

    problems: list[str] = []
    # Built here, from the module-level name, so the validation runs against
    # exactly the parameter table a replay would use rather than a snapshot
    # taken at import time.
    validation_params = ReplayParams(values=dict(default_params()))
    for walk in all_walked:
        for problem in _validate(walk.trace, policy.name, validation_params):
            problems.append(f"[{query_id}/{walk.trace.ref}] {problem}")
    if problems:
        raise TraceValidationError(
            "recorded activations do not reproduce the pipeline:\n  "
            + "\n  ".join(problems[:20])
        )

    # Outcome flags. Content-based filters are evaluated with the service's
    # own predicates; delivery is read off the packet.
    normalized_query = service._normalize_text(query)
    delivered_fingerprints = {
        content_fingerprint(i.content) for i in packet.memory_items
    }
    delivered_reflection_fingerprints = {
        content_fingerprint(i.content) for i in packet.reflection_items
    }

    for walk in walked_memory:
        walk.trace.filtered_echo_or_meta = bool(
            service._is_echo_or_meta_memory(walk.item, normalized_query)
        )
        walk.trace.filtered_low_value = bool(service._is_low_value_memory(walk.item))
        walk.trace.delivered = walk.trace.content_sha256 in delivered_fingerprints
    for walk in walked_reflections:
        walk.trace.delivered = (
            walk.trace.content_sha256 in delivered_reflection_fingerprints
        )

    # Dedup is applied to the ranked, filtered list, so it has to be walked
    # in that order to mark the right member of a duplicate pair. It rarely
    # fires here -- ContextRetriever.retrieve already deduped by normalized
    # content before these candidates existed -- but "rarely" is not a
    # reason for replay to be unable to see it.
    _mark_dedup(service, walked_memory)
    _mark_dedup(service, walked_reflections)

    relevance_gate_fired = _relevance_gate_fired(policy, walked_memory)
    if relevance_gate_fired:
        for walk in walked_memory:
            walk.trace.relevance_gated_out = walk.trace.memory_type != "profile"
        for walk in walked_reflections:
            walk.trace.relevance_gated_out = True

    return QueryTrace(
        query_id=query_id,
        query=query,
        policy_name=policy.name,
        policy={
            "name": policy.name,
            "memory_weight": policy.memory_weight,
            "reflection_weight": policy.reflection_weight,
            "recency_bias": policy.recency_bias,
            "diversity": policy.diversity,
            "prefer_experiences": policy.prefer_experiences,
            "prefer_active_work": policy.prefer_active_work,
            "prefer_exact_matches": policy.prefer_exact_matches,
            "state_boost": policy.state_boost,
            "min_score": policy.min_score,
            "eligible_memory_types": policy.eligible_memory_types,
            "suppress_memory_types": list(policy.suppress_memory_types),
            "use_web_search": policy.use_web_search,
        },
        relational_query=_matches_relational_query(query),
        project_id=project_id,
        memory_limit=service._memory_limit_for_policy(policy.name),
        reflection_limit=service._reflection_limit_for_policy(policy.name),
        diversity=policy.diversity,
        min_score=policy.min_score,
        relevance_gate_fired=relevance_gate_fired,
        candidates=[w.trace for w in all_walked],
        delivered_refs=[w.trace.ref for w in walked_memory if w.trace.delivered],
        delivered_reflection_refs=[
            w.trace.ref for w in walked_reflections if w.trace.delivered
        ],
    )


def capture_run(
    queries: list[tuple[str, str]],
    *,
    include_content: bool = False,
    pool_limit: int | None = None,
    project_id: str | None = None,
    progress=None,
) -> TraceRun:
    """Capture a whole query set, read-only, digesting the stores either side.

    pool_limit widens get_memory_items' semantic_search limit beyond the
    shipped 8. A widened pool gives a sensitivity pass candidates the
    shipped configuration truncates away, which is what makes "would this
    parameter have promoted something" answerable -- but the delivered set
    in such a trace is then the widened pipeline's, not production's. The
    flag is recorded so nobody has to infer it later.
    """
    service = ContextService()
    before = database_digest()

    stack = [retrieval_stats_disabled()]
    if pool_limit is not None:
        from src.retrieval import semantic_search as ss_module

        real_search = ss_module.semantic_search

        def _widened(query, limit=5, **kwargs):
            return real_search(query, limit=max(limit, pool_limit), **kwargs)

        stack.append(patch.object(ss_module, "semantic_search", _widened))

    traces: list[QueryTrace] = []
    for context in stack:
        context.__enter__()
    try:
        for index, (query_id, query) in enumerate(queries, start=1):
            if progress:
                progress(index, len(queries), query_id)
            traces.append(
                capture_query(
                    query,
                    query_id,
                    service=service,
                    project_id=project_id,
                    include_content=include_content,
                )
            )
    finally:
        for context in reversed(stack):
            context.__exit__(None, None, None)

    return TraceRun(
        schema_version=SCHEMA_VERSION,
        captured_at=datetime.now().strftime("%Y-%m-%dT%H-%M-%S"),
        vault_fingerprint=vault_fingerprint(),
        db_digest_before=before,
        db_digest_after=database_digest(),
        param_defaults=default_params(),
        include_content=include_content,
        pool_limit=pool_limit or 8,
        pool_widened=pool_limit is not None,
        queries=traces,
    )


def default_output_path() -> Path:
    """Outside the repository, always. See TraceRun.write."""
    import os

    configured = os.environ.get("EMBER_TRACE_DIR")
    root = Path(configured) if configured else Path.home() / ".ember_traces"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return root / f"retrieval_trace_{stamp}.json"
