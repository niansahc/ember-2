"""
src/memory/third_party_detection.py

Lightweight heuristic for detecting whether a conversation record
mentions a named third party (someone other than the user). Sets the
`contains_named_third_party` metadata flag at write time, used by the
ADR-021 cross-session pattern detector to enforce the privacy boundary
in the relational_honesty behavioral sequence.

The detection is intentionally conservative:
- False positives (flagging when no real third party is present) are
  cheap — they only cause Ember to name patterns in structural terms
  ("this comes up when you describe interactions with someone specific")
  rather than identifying terms ("this comes up when you talk about
  your partner"). Both are valid Ember behaviors.
- False negatives are higher cost — Ember might name a third party
  identifier she shouldn't.

Three patterns trigger the flag:
1. Possessive third-party kinship (e.g. "my partner said")
2. Reported speech about non-self pronouns (e.g. "she said hi")
3. Capitalized proper-noun + reported-speech verb (e.g. "Sam told me")

NER (e.g. spaCy) is the right long-term answer but adds a dependency
and inference cost on every conversation write. This regex MVP is
v0.17.x scope; a future enhancement can swap in NER without changing
the function signature.
"""

from __future__ import annotations

import re

# 1. Possessive third-party kinship — "my mom", "my partner", etc.
# Bounded to a closed list of common kinship/relationship terms to avoid
# false positives on phrases like "my opinion", "my plan".
_KINSHIP_PATTERN = re.compile(
    r"\bmy\s+("
    r"mom|dad|mother|father|parent|parents|"
    r"partner|wife|husband|spouse|girlfriend|boyfriend|ex|"
    r"sister|brother|sibling|siblings|"
    r"son|daughter|kid|kids|child|children|"
    r"friend|friends|bestie|best\s+friend|"
    r"boss|coworker|coworkers|colleague|colleagues|manager|teammate|"
    r"therapist|doctor|teacher|professor|advisor|mentor|coach|"
    r"roommate|neighbor"
    r")\b",
    re.IGNORECASE,
)

# 2. Reported speech about non-self pronouns — "she said", "they told",
# "he believes", etc. The verb list covers reporting and propositional
# attitude verbs; conjugation is handled with optional 's'/'ed' tails.
_REPORTED_SPEECH_PATTERN = re.compile(
    r"\b(she|he|they)\s+"
    r"(said|told|asked|thinks?|thought|feels?|felt|wants?|wanted|"
    r"believes?|believed|knows?|knew|did|does|mentioned|"
    r"replied|answered|claimed|insisted|admitted|agreed|disagreed)\b",
    re.IGNORECASE,
)

# 3. Capitalized proper-noun token followed by a reported-speech verb.
# Matches "Sam said", "Alex told me", etc. Two-character minimum on the
# name token to avoid matching sentence-initial articles like "A said"
# (rare but possible in fragmented text). Word-internal capitalization
# (CamelCase) is not matched — only sentence-style capitalized names.
_NAMED_SPEECH_PATTERN = re.compile(
    r"\b[A-Z][a-z]+\s+"
    r"(said|told|asked|thinks?|thought|feels?|felt|wants?|wanted|"
    r"believes?|believed|did|does|mentioned)\b"
)


def contains_named_third_party(text: str) -> bool:
    """Return True if the text appears to reference a named third party.

    Conservative regex heuristic — see module docstring for the design
    rationale. Pure function: no I/O, no state, safe to call inline at
    every conversation write.
    """
    if not text:
        return False
    if _KINSHIP_PATTERN.search(text):
        return True
    if _REPORTED_SPEECH_PATTERN.search(text):
        return True
    if _NAMED_SPEECH_PATTERN.search(text):
        return True
    return False
