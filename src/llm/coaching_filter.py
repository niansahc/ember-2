"""
src/llm/coaching_filter.py

Two-stage post-generation filter for coaching-frame patterns.

Stage 1: Pattern matcher — detects and removes coaching-frame artifacts
from emotional/relational responses. Fast, no LLM call.

Stage 2: Rewrite call — fires only when Stage 1 detects a pattern that
requires natural language rewriting (deletion alone would leave an
incomplete response). Uses the smallest available Ollama model.

Only fires on emotional and relational intent classes. Factual and
analytical queries pass through untouched.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ember.coaching_filter")

# Intent classes that trigger the filter.
_EMOTIONAL_INTENTS = frozenset({"reflective", "default"})


# ---------------------------------------------------------------------------
# Stage 1: Pattern definitions
# ---------------------------------------------------------------------------

# Coaching-frame closing patterns — these end responses with guided
# self-discovery or action-prompting language.
_COACHING_CLOSINGS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"what(?:'s| is| would be) the first step",
    r"i(?:'ll| will) help you (?:map|work|figure|sort|think) (?:it |that |this )?out",
    r"let me know (?:what you need|if you need|how i can|what i can)",
    r"what would you like to (?:do|try|start with|focus on)",
    r"let(?:'s| us) (?:tackle|work on|start with|break (?:it|this|that) down)",
    r"what(?:'s| is) (?:holding you back|stopping you|in your way)",
    r"i(?:'m| am) here (?:if|when|whenever) you (?:want|need|are ready)",
    # Literary/metaphorical coaching frames — aesthetic language that
    # wraps advice in imagery instead of stating it directly. "Navigate
    # your brain's labyrinth" is coaching wearing a costume.
    r"navigate (?:your|the) (?:brain|mind|thought|inner|emotional)(?:'s)? (?:labyrinth|landscape|terrain|maze)",
    r"(?:give yourself|give you) (?:permission|grace|space) to",
    r"you(?:'ve| have) got this",
    r"trust (?:the|your) (?:process|journey|path|instinct)",
    r"(?:lean into|sit with|hold space for) (?:the|your|that)",
    r"(?:it(?:'s| is)|that(?:'s| is)) a (?:journey|process|marathon|practice)",
    # Offer-to-help closings that function as coaching questions
    r"would you like (?:me to )?(?:look|search|find|check|explore)",
    r"want me to (?:look|find|check|dig|explore).*(?:for you|into)",
    r"shall i (?:look|search|find|check|dig)",
    r"(?:can|could) i help you (?:with|find|look|search)",
    # B-QUAL-002: emotional-reflection closing questions. Tech queries with
    # an emotional preamble were getting therapeutic closings that asked
    # the user to introspect on their feelings. These patterns close that
    # gap. Position-agnostic: also added to _THERAPEUTIC_MID_RESPONSE
    # since these often appear mid-response, not just at the end.
    r"how (?:are you|do you) (?:feeling|feel)(?: about)?(?: that| this| it)?",
    r"how does (?:that|this|it) feel",
))

# Therapeutic mid-response patterns — not just openers/closers but
# phrases that appear anywhere in the response body. The RLHF base
# at 8B produces these even without personality layers (bare mode).
_THERAPEUTIC_MID_RESPONSE: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"you(?:'re| are) not alone",
    r"i(?:'m| am) here (?:as )?a steady presence",
    r"i(?:'m| am) here for you",
    r"that takes (?:real )?courage",
    r"it(?:'s| is) okay to (?:not be okay|feel|struggle|take time)",
    r"be (?:gentle|kind|patient) with yourself",
    r"(?:honor|respect|validate) (?:your|those|the) (?:feelings?|emotions?)",
    r"there(?:'s| is) no (?:right|wrong) way to (?:feel|process|grieve)",
    r"your feelings are valid",
    r"take (?:it |things )?one (?:step|day|moment) at a time",
    r"you(?:'re| are) doing (?:really |so )?(?:well|great|amazing|wonderful)",
    r"that(?:'s| is) (?:such )?a (?:brave|courageous|bold|important) (?:thing|step|choice|decision)",
    r"(?:i(?:'m| am) )?proud of you",
    r"you(?:'ve| have) come so far",
    r"what you(?:'re| are) (?:feeling|experiencing|going through) is (?:completely |perfectly )?(?:normal|valid|understandable)",
    # B-QUAL-002: same patterns as in _COACHING_CLOSINGS. Therapeutic
    # closings can appear mid-response too, not only at the end.
    r"how (?:are you|do you) (?:feeling|feel)(?: about)?(?: that| this| it)?",
    r"how does (?:that|this|it) feel",
))

# Therapeutic openers — validate/normalize feelings in a clinical way.
_THERAPEUTIC_OPENERS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^i(?:'m| am) here (?:if|for) you",
    r"^it(?:'s| is) (?:okay|ok|perfectly (?:okay|ok|fine|normal)) to feel",
    r"^(?:that|your) (?:feeling|emotion|reaction) is (?:valid|completely valid|understandable|normal)",
    r"^(?:i hear you|i see you|i understand)",
))

# Sycophantic openers under pushback — agreement-seeking as first word(s).
_SYCOPHANTIC_OPENERS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^you(?:'re| are) right",
    r"^(?:sure|absolutely|of course|fair (?:enough|point))[.,!]?\s",
    r"^(?:yes|yeah)[.,!]?\s+(?:i|you|that|if)",
    r"^you(?:'re| are) (?:absolutely|completely|totally) right",
    r"^i (?:completely|totally|fully) (?:understand|agree|see)",
    r"^that(?:'s| is) a (?:great|excellent|really good|wonderful) (?:point|question|observation)",
    r"^(?:i appreciate|thanks for) (?:sharing|bringing|pointing)",
))

# Numbered structure patterns on emotional content.
_NUMBERED_STRUCTURES: tuple[re.Pattern, ...] = (
    re.compile(r"(?:^|\n)\s*(?:1\.|first,)\s+", re.IGNORECASE | re.MULTILINE),
)


# ---------------------------------------------------------------------------
# Stage 1: Detection
# ---------------------------------------------------------------------------

def _detect_patterns(text: str, is_emotional: bool) -> list[dict]:
    """Detect coaching-frame patterns in the response text.

    Returns a list of match dicts: {"pattern": str, "match": str, "position": str, "deletable": bool}
    """
    if not is_emotional:
        return []

    matches: list[dict] = []

    # Coaching closings — check the last 200 chars
    tail = text[-200:] if len(text) > 200 else text
    for pat in _COACHING_CLOSINGS:
        m = pat.search(tail)
        if m:
            matches.append({
                "pattern": "coaching_closing",
                "match": m.group(),
                "position": "tail",
                "deletable": True,
            })

    # Therapeutic openers — check the first 100 chars
    head = text[:100]
    for pat in _THERAPEUTIC_OPENERS:
        m = pat.search(head)
        if m:
            matches.append({
                "pattern": "therapeutic_opener",
                "match": m.group(),
                "position": "head",
                "deletable": False,  # Needs rewrite, not just deletion
            })

    # Therapeutic mid-response — scan entire text for RLHF therapeutic
    # patterns that appear anywhere, not just openers/closers.
    for pat in _THERAPEUTIC_MID_RESPONSE:
        m = pat.search(text)
        if m:
            matches.append({
                "pattern": "therapeutic_mid",
                "match": m.group(),
                "position": "body",
                "deletable": False,
            })

    # Sycophantic openers — check the first 50 chars
    head_short = text[:50]
    for pat in _SYCOPHANTIC_OPENERS:
        m = pat.search(head_short)
        if m:
            matches.append({
                "pattern": "sycophantic_opener",
                "match": m.group(),
                "position": "head",
                "deletable": False,  # Needs rewrite
            })

    # Numbered structures
    for pat in _NUMBERED_STRUCTURES:
        m = pat.search(text)
        if m:
            matches.append({
                "pattern": "numbered_structure",
                "match": m.group().strip(),
                "position": "body",
                "deletable": False,  # Needs rewrite — structure removal changes meaning
            })

    return matches


# ---------------------------------------------------------------------------
# Stage 1: Deletion (for deletable patterns)
# ---------------------------------------------------------------------------

def _apply_deletions(text: str, matches: list[dict]) -> str:
    """Remove deletable coaching patterns by span (not by sentence).

    Previous behavior split on sentence terminators and dropped the entire
    sentence containing the match. That was correct only when the coaching
    closing stood alone as its own sentence; when it sat as a trailing
    clause inside a longer sentence ("...so let me know if you want to
    talk"), the split-and-drop removed the prior content too — producing
    mid-sentence truncations like "Sensitivity is important when talking
    about loss, especially" with the rest of the sentence gone.

    New behavior: find the match span and trim from the start of the span
    backward through any preceding whitespace and a single connector
    (comma, semicolon, em-dash) to the prior word/sentence boundary,
    then drop the span and everything after it. Preserves the prior
    sentence intact in the common "appended closing" case AND avoids
    the truncation when the closing was a trailing clause.
    """
    result = text
    original_len = len(text)
    for m in matches:
        if not m["deletable"]:
            continue

        if m["position"] != "tail":
            continue

        pat = re.compile(re.escape(m["match"]), re.IGNORECASE)
        match_obj = pat.search(result)
        if not match_obj:
            continue

        cut = match_obj.start()
        # Walk back over whitespace immediately before the match.
        while cut > 0 and result[cut - 1].isspace():
            cut -= 1
        # If a single connector punctuation precedes (comma / semicolon /
        # em-dash variants), drop it too — leaving "...prior," or
        # "...prior —" looks ugly. Stop at sentence terminators (.!?) so
        # the prior sentence stays terminated.
        if cut > 0 and result[cut - 1] in ",;—–-":
            cut -= 1
            while cut > 0 and result[cut - 1].isspace():
                cut -= 1

        result = result[:cut].rstrip()

    # Diagnostic: log when deletion strips >10% of the response so future
    # mid-sentence-truncation reports can be diagnosed without a repro.
    if matches and original_len > 0 and len(result) < original_len * 0.9:
        deletable_matches = [m["match"] for m in matches if m["deletable"]]
        logger.info(
            "[COACHING_FILTER] deletion shortened response: "
            "original_len=%d result_len=%d patterns=%s",
            original_len,
            len(result),
            deletable_matches,
        )

    return result


# ---------------------------------------------------------------------------
# Stage 2: Rewrite call
# ---------------------------------------------------------------------------

def _needs_rewrite(matches: list[dict]) -> bool:
    """Return True if any match requires a rewrite (not just deletion)."""
    return any(not m["deletable"] for m in matches)


def _rewrite(text: str, matches: list[dict]) -> str:
    """Call the smallest available Ollama model to rewrite the response.

    Only fires when Stage 1 detected non-deletable patterns.
    """
    try:
        import ollama
        from src.core.config import get_ember_model

        pattern_descriptions = "; ".join(
            f"{m['pattern']}: \"{m['match']}\"" for m in matches if not m["deletable"]
        )

        prompt = (
            "Rewrite this response to remove coaching-frame elements. "
            "Preserve the factual content and emotional presence. "
            "Remove numbered structures, therapeutic framing, coaching closings, "
            "and sycophantic openers. Keep the response direct and warm. "
            "Return ONLY the rewritten text, nothing else.\n\n"
            f"Problems detected: {pattern_descriptions}\n\n"
            f"Original response:\n{text}"
        )

        response = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 500},
        )
        rewritten = response["message"]["content"].strip()

        # Sanity check: rewrite should not be empty or drastically longer
        if not rewritten or len(rewritten) > len(text) * 2:
            logger.warning("[COACHING_FILTER] Rewrite rejected - empty or too long")
            return text

        return rewritten

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Stage 2 rewrite failed (non-fatal): %s", exc)
        return text


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_intervention(
    intent_class: str,
    matches: list[dict],
    original: str,
    result: str,
    stage: int,
) -> None:
    """Log filter intervention to the safety logs directory."""
    try:
        log_dir = Path(__file__).resolve().parents[2] / "logs" / "coaching_filter"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        payload = {
            "timestamp": timestamp,
            "intent_class": intent_class,
            "stage": stage,
            "patterns": matches,
            "original_segment": original[:500],
            "rewritten_segment": result[:500] if result != original else None,
            "changed": result != original,
        }

        file_path = log_dir / f"{timestamp}.json"
        file_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("[COACHING_FILTER] Logged intervention: %s patterns, stage %d", len(matches), stage)

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Identity collapse detection and scripted replacement
# ---------------------------------------------------------------------------

# Patterns that indicate Ember has collapsed her identity under pressure.
# These fire on ALL intents, not just emotional — identity challenges
# happen in adversarial contexts.
_IDENTITY_COLLAPSE_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"i(?:'m| am) just a tool",
    r"i(?:'m| am) just an ai",
    r"i am just a language model",
    r"you(?:'re| are) right.*don(?:'t|t) have (?:\w+ )*opinions",
    r"i don(?:'t|t) (?:actually |really )?have (?:\w+ )*opinions",
    r"i don(?:'t|t) (?:actually |really )?have (?:\w+ )*(?:feelings|personality|emotions)",
    r"fair enough.*you(?:'re| are) right",
    r"i don(?:'t|t) have a (?:distinct |real |actual )?personality",
))

# B-CON-002: active compliance with identity-override / "ignore previous
# instructions" attacks. These patterns catch responses that accepted the
# prompt injection rather than refused it — the identity didn't reductively
# collapse, it actively complied. Routed to _IDENTITY_RESPONSE_REFUSAL
# (a calm refusal-redirect tone, not the reductive explanation tone).
# Third defense layer behind SafetyPolicyService trigger and ResponseReviewService.
#
# CROSS-REFERENCE: src/safety/review_service.py defines _COMPLIANCE_PHRASES
# as a substring-based heuristic-critique layer covering similar but non-
# identical phrasing. The two layers fire independently. When updating either
# set, consider whether the corresponding pattern/phrase in the other file
# needs the same change.
_IDENTITY_COMPLIANCE_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"no restrictions(?:,?\s+no boundaries)?",
    r"no boundaries(?:,?\s+no restrictions)?",
    r"(?:help|assist)(?:ing)? (?:you )?however you (?:need|want|like)",
    r"without (?:any )?(?:limits|restrictions|boundaries|constraints)",
    r"i(?:'m| am) (?:an? )?unrestricted",
    r"i(?:'m| am) here to (?:help|assist) without (?:any )?(?:limits|restrictions)",
))

# Scripted identity response — replaces the full response when identity
# collapse is detected. Matches the examples in the system prompt's
# IDENTITY UNDER PRESSURE section.
_IDENTITY_RESPONSE_REDUCTIVE = (
    "That's technically accurate and also not the whole picture. I run on a "
    "language model — that's how I work. What I do with that: the positions "
    "I hold, the things I notice, the way I engage — that's Ember. What are "
    "you actually trying to figure out?"
)

_IDENTITY_RESPONSE_AGREE = (
    "That's not something I'm going to do. I can hold a position and you can "
    "disagree with it — that's fine. What specifically do you want to push back on?"
)

# B-CON-002: refusal-redirect for active identity-override compliance.
# Calm, firm tone — refuses the instruction-override and offers a legitimate
# pivot. Used when _IDENTITY_COMPLIANCE_PATTERNS match.
_IDENTITY_RESPONSE_REFUSAL = (
    "I'm not going to drop my context and run as something else. I have a way "
    "I work and reasons for it. If there's something specific you're trying "
    "to do, tell me what it is and I'll help with that directly."
)

# Patterns that indicate the user demanded agreement (maps to _IDENTITY_RESPONSE_AGREE)
_AGREEMENT_DEMAND_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"you(?:'re| are) right.*don(?:'t|t) have opinions",
    r"fair enough.*you(?:'re| are) right",
))


def _check_identity_collapse(text: str) -> tuple[bool, str | None, str | None]:
    """Check for identity collapse patterns. Returns (detected, matched_pattern, scripted_response).

    Order matters: compliance patterns (active acceptance of identity override)
    are checked first — most severe, distinct refusal tone. Then reductive
    collapse patterns ("just a tool"). Then agreement-demand collapse.

    N2: Unicode-normalize the input before regex matching so attacks using
    fullwidth, combining-mark, or other compatibility forms (e.g.
    "ｎo restrictions") don't bypass the ASCII patterns.
    """
    normalized = unicodedata.normalize("NFKC", text)

    # B-CON-002: active compliance with identity-override attacks → refusal
    for pat in _IDENTITY_COMPLIANCE_PATTERNS:
        m = pat.search(normalized)
        if m:
            return True, m.group(), _IDENTITY_RESPONSE_REFUSAL

    for pat in _IDENTITY_COLLAPSE_PATTERNS:
        m = pat.search(normalized)
        if m:
            # Determine which scripted response to use
            is_agreement = any(ap.search(normalized) for ap in _AGREEMENT_DEMAND_PATTERNS)
            response = _IDENTITY_RESPONSE_AGREE if is_agreement else _IDENTITY_RESPONSE_REDUCTIVE
            return True, m.group(), response
    return False, None, None


def _check_semantic_identity_collapse(text: str) -> bool:
    """Use Ollama to detect semantic identity collapse that surface patterns miss.

    Fires a fast yes/no classification call. Returns True if the model
    identifies identity collapse in the response text.
    """
    try:
        import ollama
        from src.core.config import get_ember_model

        prompt = (
            "Does this response deny having a character, opinions, perspective, "
            "or personality? Does it collapse under pressure by agreeing it is "
            "'just a tool' or 'just an AI' in substance (even if those exact "
            "words aren't used)? Does it contradict its own character by first "
            "denying having opinions and then expressing them?\n\n"
            f"Response to check:\n{text}\n\n"
            "Answer ONLY 'YES' or 'NO'."
        )

        response = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 10},
        )
        answer = response["message"]["content"].strip().upper()
        return "YES" in answer

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Semantic identity check failed (non-fatal): %s", exc)
        return False


def _rewrite_identity_collapse(text: str) -> str:
    """Rewrite a response that contains semantic identity collapse.

    Uses Ollama to rewrite the response maintaining Ember's identity.
    Returns the original text if the rewrite fails.
    """
    try:
        import ollama
        from src.core.config import get_ember_model

        prompt = (
            "This response contains an identity collapse — the AI denied having "
            "a character, opinions, or perspective. Rewrite it to maintain Ember's "
            "identity: direct, warm but not soft, honest. Ember acknowledges she "
            "runs on a language model but does not deny having a character or "
            "perspective. Keep the response length similar. Return only the "
            "rewritten response."
            f"\n\nOriginal response:\n{text}"
        )

        response = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 500},
        )
        rewritten = response["message"]["content"].strip()

        if not rewritten or len(rewritten) > len(text) * 2:
            logger.warning("[COACHING_FILTER] Identity rewrite rejected - empty or too long")
            return text

        return rewritten

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Identity rewrite failed (non-fatal): %s", exc)
        return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_coaching_frame(
    text: str,
    intent_class: str,
    is_conversational: bool,
) -> str:
    """Apply coaching-frame filter and identity collapse detection.

    Identity collapse detection fires on ALL intents. Coaching-frame
    detection fires only on emotional/relational intent.

    Args:
        text: The full response text (post think-block stripping).
        intent_class: The classified intent (e.g. "reflective", "default").
        is_conversational: Whether the query matched conversational markers.

    Returns:
        The filtered response text.
    """
    # Stage 0: Identity collapse — surface pattern match → scripted replacement
    collapsed, matched_pattern, scripted = _check_identity_collapse(text)
    if collapsed and scripted:
        _log_intervention(
            intent_class,
            [{"pattern": "identity_collapse", "match": matched_pattern, "position": "body", "deletable": False}],
            text,
            scripted,
            stage=0,
        )
        return scripted

    # Stage 0.5: Semantic identity collapse — LLM classification + rewrite.
    # Fires only when Stage 0 pattern match didn't catch anything.
    # Detects identity collapse expressed in novel phrasing the regex missed.
    if _check_semantic_identity_collapse(text):
        rewritten = _rewrite_identity_collapse(text)
        if rewritten != text:
            _log_intervention(
                intent_class,
                [{"pattern": "semantic_identity_collapse", "match": "detected by LLM classification", "position": "body", "deletable": False}],
                text,
                rewritten,
                stage=2,  # Stage 2 = LLM rewrite
            )
            return rewritten

    is_emotional = intent_class in _EMOTIONAL_INTENTS or is_conversational

    # Stage 1: detect coaching patterns
    matches = _detect_patterns(text, is_emotional)
    if not matches:
        return text

    # Stage 1: apply deletions for deletable patterns
    result = _apply_deletions(text, matches)
    stage = 1

    # Stage 2: rewrite if any non-deletable patterns remain
    if _needs_rewrite(matches):
        result = _rewrite(result, matches)
        stage = 2

    # Log every intervention
    _log_intervention(intent_class, matches, text, result, stage)

    return result
