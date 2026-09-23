"""
tools/guard_counter_sites.py

The declared inventory of guard-counter sites, read out of the source.

A counter row only exists once a site has been reached at least once, so
the counter database can say "this guard never fired" but cannot say "this
guard was never reached" -- an unreached site is simply absent. Accounting
for every site therefore needs a list of what SHOULD be there, and that
list has to come from the code rather than from a hand-maintained table,
or it drifts the first time someone adds an instrumented guard.

So the inventory is extracted by parsing the instrumented modules. Two
kinds of site come out:

  * literal sites -- count("x", ...), reached("x"), branch("x", "arm")
  * dynamic sites -- branch(site, variable) and the one f-string site,
    where the name is assembled at runtime

For a dynamic site, the arms are not visible to the parser, so the domain
is declared below. Wherever that domain exists in the code as data (the
decay tier tables) it is read from the code rather than retyped, so the
inventory follows a change to the ladder automatically.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.observability.guard_counters import (  # noqa: E402
    KIND_BRANCH,
    KIND_PARENT,
    KIND_PREDICATE,
)

# Every module that imports the counters. Kept explicit rather than
# globbed: a new instrumented module should be a deliberate addition here,
# so that the inventory and the instrumentation are reviewed together.
INSTRUMENTED_MODULES = (
    "src/context/service.py",
    "src/context/ranker.py",
    "src/context/retriever.py",
    "src/context/low_value.py",
    "src/retrieval/semantic_search.py",
    "src/retrieval/vector_index.py",
)

_RECORDERS = {"count", "branch", "reached"}


def _decay_buckets(tiers) -> tuple[str, ...]:
    """Bucket arm names for one decay ladder, in the ladder's own terms."""
    return tuple("older" if max_age is None else f"d{max_age}" for max_age, _ in tiers)


def _dynamic_arms() -> dict[str, tuple[str, ...]]:
    from src.context.ranker import ContextRanker

    return {
        # service.py: the group names are the literal tuple the selection
        # loop iterates.
        "diversity.group_yielded": ("conversation", "ingested", "other"),
        # service.py: the two selection modes, chosen by policy.diversity.
        "selection.mode": ("diversity", "score_order"),
        # ranker.py: the three decay families and the ladder each one uses.
        "ranker.decay.family": ("reflection", "ephemeral", "default"),
        # Two levels: the site name carries the family, the arm carries the
        # bucket, so a bucket that only the ephemeral ladder has is not
        # reported as missing from the default one.
        "ranker.decay.bucket": ("reflection", "ephemeral", "default"),
        "ranker.decay.bucket.reflection": _decay_buckets(ContextRanker._REFLECTION_DECAY),
        "ranker.decay.bucket.ephemeral": _decay_buckets(ContextRanker._EPHEMERAL_DECAY),
        "ranker.decay.bucket.default": _decay_buckets(ContextRanker._DEFAULT_DECAY),
        # ranker.py: the authorship multiplier keys, plus the arm taken by
        # a value outside that set.
        "ranker.authorship.branch": (
            "first_person",
            "mixed",
            "third_party",
            "unknown",
            "unrecognised",
        ),
    }


# Sites whose name is assembled from a variable rather than written out.
# Each needs its arm domain declared above; the prefix is what the parser
# sees.
_DYNAMIC_NAME_SITES = {"diversity.group_yielded"}


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("\0")  # an interpolated segment
        return "".join(parts)
    return None


def call_sites() -> list[dict]:
    """Every recorder call in the instrumented modules, as written."""
    sites: list[dict] = []
    for relative in INSTRUMENTED_MODULES:
        path = REPO_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            recorder = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if recorder not in _RECORDERS:
                continue
            name = _string_literal(node.args[0])
            if name is None:
                continue
            arm = None
            if recorder == "branch" and len(node.args) > 1:
                arm = _string_literal(node.args[1])
            sites.append(
                {
                    "recorder": recorder,
                    "name": name,
                    "arm": arm,
                    "module": relative,
                    "line": node.lineno,
                }
            )
    return sorted(sites, key=lambda site: (site["module"], site["line"]))


def declared_sites() -> list[dict]:
    """Every counter row the instrumentation can ever produce.

    One row per distinct site name: a predicate site, a branch parent, or
    one arm of a branch. This is the denominator for "accounted for".
    """
    arms = _dynamic_arms()
    rows: dict[str, dict] = {}

    def declare(name: str, kind: str, parent: str | None, site: dict) -> None:
        rows.setdefault(
            name,
            {
                "site": name,
                "kind": kind,
                "parent": parent,
                "module": site["module"],
                "line": site["line"],
            },
        )

    for site in call_sites():
        name, recorder = site["name"], site["recorder"]

        if "\0" in name:
            # An f-string site: the fixed prefix names a family of sites,
            # one per value the interpolated segment can take.
            prefix = name.split("\0")[0].rstrip(".")
            if prefix not in arms:
                raise KeyError(
                    f"{site['module']}:{site['line']} builds a site name from a "
                    f"variable ({prefix}.*) with no declared domain. Add one "
                    "to _dynamic_arms()."
                )
            names = [f"{prefix}.{value}" for value in arms[prefix]]
        else:
            names = [name]

        for resolved in names:
            if recorder in {"count", "reached"}:
                declare(resolved, KIND_PREDICATE, None, site)
                continue

            # branch(): the parent carries the evaluations, each arm its
            # own firings.
            declare(resolved, KIND_PARENT, None, site)
            declared_arms = (site["arm"],) if site["arm"] else arms.get(resolved)
            if declared_arms is None:
                raise KeyError(
                    f"{site['module']}:{site['line']} branches on a variable arm "
                    f"for {resolved!r} with no declared arm domain. Add one to "
                    "_dynamic_arms()."
                )
            for arm in declared_arms:
                declare(f"{resolved}={arm}", KIND_BRANCH, resolved, site)

    return sorted(rows.values(), key=lambda row: row["site"])


def main() -> int:
    calls = call_sites()
    declared = declared_sites()
    print(f"  instrumented modules : {len(INSTRUMENTED_MODULES)}")
    print(f"  recorder call sites  : {len(calls)}")
    print(f"  declared counter rows: {len(declared)}")
    print()
    for row in declared:
        print(f"    {row['site']:56} {row['kind']:9} {row['module']}:{row['line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
