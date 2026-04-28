"""
tests/test_classifier_examples.py

ADR-037 Step A — verify the multi-class scaffolding in
src/llm/classifier_examples.py is well-formed without changing
runtime behavior. Step A is no-behavior-change: only EXAMPLES (the
original two buckets) is consumed by Stage 2; the seven new buckets
live in MULTICLASS_EXAMPLES and are unreachable until Step B.

Coverage:
  - bucket size minimums match the ADR-037 plan targets
  - no duplicate queries within a single bucket
  - no query appears in multiple buckets (disambiguation contract)
  - EXAMPLES is the original two buckets only (no-behavior-change)
  - MULTICLASS_EXAMPLES contains all nine buckets
  - LabelName Literal admits the seven new labels
  - is_identity bucket carries pet-possessive examples migrated from
    the v0.17.x RELATIONAL_KINSHIP_NOUNS / IDENTITY_MARKERS additions
"""

from __future__ import annotations

import typing

import pytest

from src.llm import classifier_examples as ce


_NEW_LABELS = (
    "status_state",
    "reflective",
    "factual_recall",
    "recent_activity",
    "recent",
    "activity",
    "is_identity",
)


_BUCKET_TARGETS: dict[str, tuple[str, int]] = {
    "_NEEDS_INTERNET": ("needs_internet", 30),
    "_VAULT_ANSWERABLE": ("vault_answerable", 30),
    "_STATUS_STATE": ("status_state", 20),
    "_REFLECTIVE": ("reflective", 20),
    "_FACTUAL_RECALL": ("factual_recall", 15),
    "_RECENT_ACTIVITY": ("recent_activity", 15),
    "_RECENT": ("recent", 10),
    "_ACTIVITY": ("activity", 10),
    "_IS_IDENTITY": ("is_identity", 15),
}


_BUCKET_PARAMS: list[tuple[str, str, int]] = [
    (attr, label, minimum) for attr, (label, minimum) in _BUCKET_TARGETS.items()
]


# ---------------------------------------------------------------------------
# Bucket size minimums (one parametrized test per bucket)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr_name,expected_label,minimum", _BUCKET_PARAMS)
def test_bucket_meets_minimum_size(
    attr_name: str, expected_label: str, minimum: int
) -> None:
    bucket = getattr(ce, attr_name)
    assert isinstance(bucket, list)
    assert len(bucket) >= minimum, (
        f"{attr_name} has {len(bucket)} entries, expected at least {minimum}"
    )


@pytest.mark.parametrize("attr_name,expected_label,minimum", _BUCKET_PARAMS)
def test_bucket_label_consistency(
    attr_name: str, expected_label: str, minimum: int
) -> None:
    """Every example in a bucket carries the bucket's label — no stray
    needs_internet rows in _STATUS_STATE etc."""
    bucket = getattr(ce, attr_name)
    for example in bucket:
        assert example["label"] == expected_label, (
            f"{attr_name} contains an example labeled {example['label']!r}, "
            f"expected {expected_label!r}: {example['query']!r}"
        )


# ---------------------------------------------------------------------------
# Within-bucket duplicate detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr_name", list(_BUCKET_TARGETS))
def test_no_duplicate_queries_within_bucket(attr_name: str) -> None:
    bucket = getattr(ce, attr_name)
    queries = [ex["query"] for ex in bucket]
    duplicates = {q for q in queries if queries.count(q) > 1}
    assert not duplicates, (
        f"{attr_name} has duplicate queries: {sorted(duplicates)}"
    )


# ---------------------------------------------------------------------------
# Cross-bucket disambiguation
# ---------------------------------------------------------------------------


def test_no_query_appears_in_multiple_buckets() -> None:
    """The whole point of distinct labels is that a single query cannot be
    correctly classified as more than one. If a query appears verbatim in
    two buckets the embedding centroid cannot discriminate them."""
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for attr_name in _BUCKET_TARGETS:
        bucket = getattr(ce, attr_name)
        for example in bucket:
            query = example["query"]
            if query in seen:
                collisions.append((query, seen[query], attr_name))
            else:
                seen[query] = attr_name
    assert not collisions, (
        f"Cross-bucket query collisions found: {collisions}"
    )


# ---------------------------------------------------------------------------
# Public-export contracts
# ---------------------------------------------------------------------------


def test_examples_export_is_original_two_buckets_only() -> None:
    """Step A is no-behavior-change. EXAMPLES must be exactly the
    pre-ADR-037 contents so the Stage 2 cascade behaves identically."""
    assert ce.EXAMPLES == ce._NEEDS_INTERNET + ce._VAULT_ANSWERABLE


def test_examples_does_not_carry_new_labels() -> None:
    """Defense in depth on the no-behavior-change rule: even if someone
    accidentally appended a new bucket to EXAMPLES, this test catches
    it before Stage 2 starts returning unknown labels."""
    labels_in_examples = {ex["label"] for ex in ce.EXAMPLES}
    assert labels_in_examples == {"needs_internet", "vault_answerable"}


def test_multiclass_examples_contains_all_nine_buckets() -> None:
    expected_labels = {"needs_internet", "vault_answerable", *_NEW_LABELS}
    actual_labels = {ex["label"] for ex in ce.MULTICLASS_EXAMPLES}
    assert actual_labels == expected_labels


def test_multiclass_examples_total_count_matches_sum_of_buckets() -> None:
    expected = sum(len(getattr(ce, attr)) for attr in _BUCKET_TARGETS)
    assert len(ce.MULTICLASS_EXAMPLES) == expected


def test_label_name_literal_admits_all_nine_labels() -> None:
    """The TypedDict Literal must accept every label used by the buckets;
    otherwise mypy would reject the example list at module load."""
    args = typing.get_args(ce.LabelName)
    expected = {"needs_internet", "vault_answerable", *_NEW_LABELS}
    assert set(args) == expected


# ---------------------------------------------------------------------------
# is_identity bucket regressions (pet-possessive carryover from v0.17.x)
# ---------------------------------------------------------------------------


def test_is_identity_includes_pet_possessive_examples() -> None:
    """Pet-possessive phrases were added to RELATIONAL_KINSHIP_NOUNS /
    IDENTITY_MARKERS on test/uat-yaml-cleanup (commit 42c7796). When
    those keyword bags are deleted in Step C/D the migration MUST keep
    pet-possessive identity routing — otherwise 'tell me about my dog'
    falls back to the default policy and stops surfacing profile records."""
    queries = {ex["query"].lower() for ex in ce._IS_IDENTITY}
    assert any("my dog" in q for q in queries), (
        "is_identity bucket missing 'my dog' example — pet-possessive "
        "identity routing will regress when IDENTITY_MARKERS is removed"
    )
    assert any("my cat" in q for q in queries)
    assert any("my pet" in q for q in queries)


def test_pet_examples_use_possessive_my_form_only() -> None:
    """Generic pet queries ('best dog breeds', 'is dog food expensive')
    must NOT route to is_identity. Every pet-related example in the
    bucket must carry the possessive 'my' marker."""
    pet_terms = ("dog", "cat", "pet")
    for example in ce._IS_IDENTITY:
        query = example["query"].lower()
        for term in pet_terms:
            if term in query:
                assert "my " in query or "my'" in query, (
                    f"is_identity example references pet term {term!r} "
                    f"without possessive 'my' guard: {example['query']!r}"
                )


# ---------------------------------------------------------------------------
# TypedDict structural integrity
# ---------------------------------------------------------------------------


def test_every_example_has_query_and_label_keys() -> None:
    for attr_name in _BUCKET_TARGETS:
        bucket = getattr(ce, attr_name)
        for example in bucket:
            assert set(example.keys()) == {"query", "label"}, (
                f"{attr_name} example has wrong keys: {set(example.keys())} "
                f"(query={example.get('query')!r})"
            )


def test_every_query_is_non_empty_stripped() -> None:
    for attr_name in _BUCKET_TARGETS:
        bucket = getattr(ce, attr_name)
        for example in bucket:
            query = example["query"]
            assert isinstance(query, str)
            assert query.strip() == query, (
                f"{attr_name} example has surrounding whitespace: {query!r}"
            )
            assert len(query) > 0
