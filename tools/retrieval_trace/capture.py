"""
tools/retrieval_trace/capture.py

Capture: run real queries through the real pipeline and record, per
candidate, every branch that fired and the score at every stage boundary.

Two rules hold this together.

1. PREDICATES ARE IMPORTED, NEVER REIMPLEMENTED. Every "did this branch
   fire" question is answered by calling the shipped function --
   _looks_like_active_work, _matches_relational_query,
   _parse_age_days, and the rest. Only the CONSTANTS are modeled, in
   params.py. A trace can therefore go stale on a retune, which
   tests/test_retrieval_trace.py catches, but it cannot go stale on a
   change of meaning without the stage check below failing first.

2. EVERY STAGE IS CHECKED AGAINST THE REAL PIPELINE. The stage walk below
   calls ContextRanker.apply_policy, apply_authorship_scoring,
   apply_project_boost and _score_memory_item
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
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.context.policies import _matches_relational_query, classify_query
from src.context import prior, role_predicate
from src.context.ranker import ContextRanker
from src.context.service import ContextService
from src.core.config import get_private_vault_path
from src.retrieval.retrieval_stats import retrieval_stats_disabled
from src.retrieval.semantic_search import (
    extract_query_terms,
    is_reflective_query,
    is_task_or_work_query,
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
    PolicyActivation,
    QueryTrace,
    RankActivation,
    RetrievalActivation,
    TraceRun,
    content_fingerprint,
    group_fingerprint,
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


def _content_prefix(content: str) -> str:
    if content.startswith("user:"):
        return "user"
    if content.startswith("assistant:"):
        return "assistant"
    return "none"


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
        intent_reflective=is_reflective_query(normalized_query),
        intent_task=is_task_or_work_query(normalized_query),
        content_prefix=_content_prefix(normalized_content),
    )


def _policy_activation(item, policy, ranker: ContextRanker) -> PolicyActivation:
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


def _authorship_activation(
    item, query: str, project_id: str | None, channel: str
) -> AuthorshipActivation:
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
        # The stage runs on the memory channel only. project_match is recorded
        # regardless, because apply_project_boost DOES run on both
        # (service.py:272-273) -- which is why this is a per-stage flag and not a
        # per-record one.
        applies=channel != CHANNEL_REFLECTION,
        relational_query=_matches_relational_query(query),
        branch=authorship,
        project_match=bool(project_id and metadata.get("project_id") == project_id),
    )


def _rank_activation(item, channel: str, bucket: str) -> RankActivation:
    """Which prior factors this candidate takes.

    Every branch domain is read from src.context.prior rather than restated
    here. This file used to reimplement the recency ladder, which broke its own
    rule 1 -- predicates are imported, never reimplemented -- and was only
    survivable because the boundaries happened to agree; restating the kind and
    length domains instead would have been the same mistake with the same
    excuse. prior.kind_branch and prior.length_branch exist for this caller.

    The reflection path differs by ONE flag, not by which factors apply. It
    used to be a genuinely separate scoring function -- a 0.95 base discount, a
    short-reflection penalty, recency at half weight -- so this returned early
    with none of the memory path's activations. Under ADR-044 both paths call
    the same prior.assemble, so returning early here dropped the kind and
    length factors from every reflection candidate. capture's own stage check
    caught it: pipeline 1.6860 against model 1.6452, a ratio of exactly
    KIND_USER_CONTENT.
    """
    metadata = getattr(item, "metadata", {}) or {}
    content = (getattr(item, "content", "") or "").strip()

    return RankActivation(
        reflection_path=channel == CHANNEL_REFLECTION,
        kind_branch=prior.kind_branch(metadata.get("content_kind")),
        length_branch=prior.length_branch(len(content)),
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
        # The OUTER metadata, deliberately -- not _retrieval_metadata's
        # profile-aware unwrapping. _diversity_score reads
        # `getattr(candidate, "metadata", {})` directly, and profile items are
        # partitioned out before selection, so the outer dict is what the
        # selector sees for every candidate that can reach it.
        selection_metadata = getattr(item, "metadata", {}) or {}

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
            doc_group=group_fingerprint(selection_metadata.get("doc_id")),
            title_group=group_fingerprint(selection_metadata.get("title")),
            retrieval=_retrieval_activation(item, channel, query),
            policy=_policy_activation(item, policy, ranker),
            author=_authorship_activation(item, query, project_id, channel),
            rank=_rank_activation(item, channel, ranker._bucket_for_age(age_days)),
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

        # The channel weight, passed explicitly as build_context now does.
        # Reading it off item_type here would reproduce the defect the
        # 2026-09-30 amendment fixed, and the stage check would not catch it,
        # because the model would read the same wrong field.
        ranker.apply_policy(
            [item],
            policy,
            channel_weight=(
                policy.reflection_weight
                if channel == CHANNEL_REFLECTION
                else policy.memory_weight
            ),
        )
        trace.stage_scores["policy"] = float(item.score)

        # ADR-044 4a. A membership stage, not a score stage, so it records an
        # outcome flag and no stage_score. build_context applies it here --
        # between apply_policy and apply_authorship_scoring -- and this walk
        # did not, which meant a replay reproduced every score correctly and
        # still over-delivered assistant turns with nothing to explain why.
        #
        # MEMORY CHANNEL ONLY, both of them. service.py:268-269 applies
        # role_predicate.apply and apply_authorship_scoring to `memory_items`
        # and not to `reflection_items`; this walk applied both to every
        # channel. On a relational query a reflection whose authorship column
        # reads `unknown` therefore took a x0.5 the pipeline never applied --
        # measured on one query as a walk score of 0.224991 against the
        # packet's 0.449982, exactly half -- and a reflection could be marked
        # excluded_by_role, which _content_filtered reads, so replay dropped
        # reflections the pipeline keeps.
        #
        # _validate could not catch it. It compares compose() against THIS
        # walk's stage_scores, so a stage the walk applies and the pipeline does
        # not is agreed upon by both sides. Nothing compared the walk to the
        # packet until the render check did.
        if channel != CHANNEL_REFLECTION:
            trace.excluded_by_role = role_predicate.excluded_by_role(item)
            ranker.apply_authorship_scoring([item], query)
        trace.stage_scores["authorship"] = float(item.score)

        ranker.apply_project_boost([item], project_id)
        trace.stage_scores["project"] = float(item.score)

        # The POLICY is passed, because the prior's policy family is
        # conditional on it. Omitting it made the model disagree with the
        # pipeline by exactly POL_PREFER_EXPERIENCE -- caught by the stage
        # check, which is the second time in this refactor it has caught a
        # modelling error rather than a scoring one.
        if channel == CHANNEL_REFLECTION:
            ranker._score_reflection_item(item, policy)
        else:
            ranker._score_memory_item(item, policy)
        trace.stage_scores["rank"] = float(item.score)
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


def _validate_render(trace: QueryTrace, params: ReplayParams) -> list[str]:
    """Does the model reproduce the render the pipeline performed?

    `_validate` does this per candidate for the stage scores, as a float
    comparison. This is the same structure one level up and set-valued: capture
    read the rendered set off the shipped PromptBuilder, replay derives it from
    src/context/render_window.py, and if those disagree the window function has
    drifted from the render it is supposed to describe.

    It lives beside STAGES rather than in it. `_validate` is typed
    dict[str, float] per candidate and tests/test_retrieval_trace.py asserts
    `set(stage_scores) == set(STAGES)`, so a set-valued stage cannot go in that
    table without breaking both.

    The comparison itself is replay.render_mismatches, shared with
    check_fidelity, so the capture-time refusal and the whole-run report cannot
    disagree about what a mismatch is.
    """
    from .replay import render_mismatches, replay_query

    return render_mismatches(trace, replay_query(trace, params))


def _rendered_refs(packet, builder) -> tuple[set[str], set[str]]:
    """The refs the prompt actually renders, per channel.

    Drives the shipped PromptBuilder and reads `packet.delivered_items`, which is
    where `record_rendered` reports. Both channels are rendered because they have
    separate early returns -- a packet can render reflections and no memory, or
    the reverse -- and each is read from a fresh render pass, so attribution needs
    no index arithmetic and does not depend on which section records first.

    The builder is passed in, not constructed here. PromptBuilder.__init__ reads
    and YAML-parses four files -- measured at 10.7ms, of which 10.7ms is
    NatureLoader, IdentityRulesLoader and LodestoneLoader -- so one per query was
    ~288ms over a 27-query run, about 18% of capture, for an object whose state
    none of this touches. Reuse is safe and deliberately scoped to the run rather
    than cached at module level: a long-lived builder owns a mutable
    ConversationBuffer, and run-scoping is what keeps this function's "writes
    nothing" guarantee structural.

    No LLM, no embedding, no vault read: tests/test_incident_reproduction.py takes
    the same route for the same reason, to get the model-visible window from the
    shipped slice rather than a copy that can drift.

    Writes nothing, structurally rather than by care: build_context was called
    with read_only=True so no delivery recorder was ever armed, and
    retrieval_stats_disabled() guards both the service recorder and the store.

    Does NOT model prompt_guardrail.trim_to_fit -- see src/context/render_window.py,
    which owns that caveat.
    """
    def refs(render) -> set[str]:
        packet.begin_render()
        render(packet)
        return {
            i.metadata["_trace_ref"]
            for i in packet.delivered_items
            if (i.metadata or {}).get("_trace_ref")
        }

    return refs(builder._build_context_section), refs(builder._build_reflection_section)


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
    builder=None,
) -> QueryTrace:
    """Trace one query. Read-only; raises TraceValidationError on any drift."""
    from src.llm.prompt_builder import PromptBuilder

    service = service or ContextService()
    ranker = ContextRanker()
    builder = builder if builder is not None else PromptBuilder()
    policy = classify_query(query)

    state_items, task_items, memory_items, reflection_items, _emb = service.retriever.retrieve(
        query
    )

    # Stamp the ref onto each candidate BEFORE the deep copy, so the packet's
    # copies carry it and what the prompt renders can be read back as refs
    # directly. The index is the one _walk_stages will assign, because it
    # enumerates these same lists in this same order.
    #
    # This replaces matching the rendered items back by content fingerprint.
    # Content is the only key two different objects share, so that match had to
    # be a multiset consumed in some order -- and no order was correct: the walk
    # runs in retrieval order while replay sorts by score, so two candidates
    # with the same body resolved to different refs on the two sides. Duplicate
    # content is exactly the dedup case, so a near-duplicate pair in a corpus
    # would have aborted the capture with "the model does not reproduce the
    # render" and named the wrong cause. The mapping was available here all
    # along and was being thrown away.
    for index, item in enumerate(memory_items):
        item.metadata = dict(getattr(item, "metadata", {}) or {}, _trace_ref=f"m{index}")
    for index, item in enumerate(reflection_items):
        item.metadata = dict(getattr(item, "metadata", {}) or {}, _trace_ref=f"r{index}")

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
    # own predicates; what was RENDERED comes from the shipped prompt builder.
    #
    # This used to read `packet.memory_items` and call it delivery. The packet
    # is a candidate set of 4 to 6 non-profile records and the prompt renders 4
    # (src/context/render_window.py), so the delivery endpoint was measuring a
    # set the model never receives -- and worse, it was BLIND to the one
    # boundary that decides whether a record is seen at all. A candidate moving
    # between rank 4 and rank 5 leaves packet membership unchanged, so the
    # measured distance for the most consequential reordering there is was
    # zero. #227 fixed the production stats write the same way and the harness
    # was not touched.
    #
    # Driving the real PromptBuilder rather than applying the window here is
    # the point: it exercises the shipped early returns, so a packet whose
    # memory_items are empty renders and records nothing, which is the correct
    # answer and one a restatement would get wrong. The window function exists
    # for REPLAY, where a full builder is unaffordable -- replay_query runs about
    # 213,000 times in an N=4096 pass and must stay a pure function of (trace,
    # params) with no vault files behind it. Capture runs it 27 times and is the
    # ground truth the model is checked against. Same structure as _validate:
    # pipeline on one side, model on the other.
    normalized_query = service._normalize_text(query)
    rendered_memory, rendered_reflections = _rendered_refs(packet, builder)

    for walk in walked_memory:
        walk.trace.filtered_echo_or_meta = bool(
            service._is_echo_or_meta_memory(walk.item, normalized_query)
        )
        walk.trace.filtered_low_value = bool(service._is_low_value_memory(walk.item))
        walk.trace.rendered = walk.trace.ref in rendered_memory
    for walk in walked_reflections:
        walk.trace.rendered = walk.trace.ref in rendered_reflections

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

    trace = QueryTrace(
        query_id=query_id,
        query=query,
        policy_name=policy.name,
        policy={
            "name": policy.name,
            "memory_weight": policy.memory_weight,
            "reflection_weight": policy.reflection_weight,
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
        rendered_refs=[w.trace.ref for w in walked_memory if w.trace.rendered],
        rendered_reflection_refs=[
            w.trace.ref for w in walked_reflections if w.trace.rendered
        ],
    )

    render_problems = _validate_render(trace, validation_params)
    if render_problems:
        raise TraceValidationError(
            f"[{query_id}] the model does not reproduce the render:\n  "
            + "\n  ".join(render_problems)
        )

    return trace


def discover_project_id(vault: Path) -> str | None:
    """The most-used project id in the corpus, or None.

    `proj.boost` cannot be measured without one. Until this existed, the
    capture CLI had no way to supply a project id, `capture_run` always took
    `project_id=None`, so `project_match` was always False and every
    sensitivity pass reported `proj.boost` as unexercised -- correctly, but
    permanently. ADR-007's boost was the one scoring term the harness could
    say nothing about.

    Resolved at run time and held in memory. It is vault-derived, so it is
    never printed, logged or written into a trace -- the caller only needs to
    pass it in, not to know what it says.

    Ported from tools/traffic_window.py, which had the same need first and
    solved it for itself; that module now imports this one rather than
    keeping a second copy.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    memory = vault / "memory"
    if not memory.is_dir():
        return None
    for path in memory.rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- a malformed record is not our business
            continue
        if not isinstance(record, dict):
            continue
        project_id = (record.get("metadata") or {}).get("project_id")
        if isinstance(project_id, str) and project_id:
            counts[project_id] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


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
    # One builder for the run, hoisted for the same reason `service` is.
    # PromptBuilder.__init__ reads and YAML-parses four files, ~10.7ms, so one
    # per query was ~18% of capture for an object whose state the render never
    # touches. Scoped to the run rather than cached at module level.
    from src.llm.prompt_builder import PromptBuilder

    builder = PromptBuilder()
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
                    builder=builder,
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
