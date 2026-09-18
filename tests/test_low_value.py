"""
tests/test_low_value.py

Unit tests for src/context/low_value.py.

These cover the three content classes that replaced hardcoded verbatim user
utterances in ContextRanker, ContextService and generate_reflection. All
fixtures here are synthetic (CLAUDE.md Vault Privacy Rule) -- the point of
the module under test is that the behaviour is expressed as a pattern over
a class, so no real utterance is needed to exercise it.

The false-negative tests matter as much as the positive ones. Both call
sites that consume is_low_value_content apply it as a hard drop, and on a
corpus where most records are already hard to reach, wrongly classifying a
substantive record as commentary is the more expensive error.
"""

from src.context.low_value import (
    MAX_META_LENGTH,
    is_assistant_meta_prompt,
    is_low_value_content,
    is_model_non_answer,
    is_style_feedback,
)


# ---------------------------------------------------------------------------
# Style feedback
# ---------------------------------------------------------------------------

def test_adjective_before_output_noun():
    assert is_style_feedback("shorter replies please") is True


def test_adjective_with_intervening_words():
    assert is_style_feedback("give me shorter bullet-point answers") is True


def test_output_noun_before_complaint():
    assert is_style_feedback("your answers are too long") is True


def test_style_feedback_mid_sentence():
    assert is_style_feedback("i keep asking for briefer answers and it never sticks") is True


def test_verbosity_adjectives():
    for phrase in ("that was a verbose response", "your replies are too wordy", "a rambling answer"):
        assert is_style_feedback(phrase) is True, phrase


def test_first_person_authorship_is_not_style_feedback():
    # The user describing output they wrote themselves is a memory about
    # their day, not commentary on the assistant.
    assert is_style_feedback("i wrote a long response to the review") is False
    assert is_style_feedback("i spent the morning drafting a long reply") is False


def test_hyphenated_compound_does_not_match():
    # "long-standing bug" must not read as "long ... answer".
    assert is_style_feedback("fixed a long-standing bug in the context service") is False


def test_length_adjective_without_output_noun():
    assert is_style_feedback("it was a long day and i was tired") is False


def test_style_feedback_respects_length_bound():
    filler = "and then a great deal of other substantive detail followed. "
    long_text = "your answers are too long. " + filler * 5
    assert len(long_text) > MAX_META_LENGTH
    assert is_style_feedback(long_text) is False


# ---------------------------------------------------------------------------
# Assistant-meta prompts
# ---------------------------------------------------------------------------

def test_directive_meta_prompt():
    assert is_assistant_meta_prompt("tell me everything you see") is True


def test_interrogative_meta_prompt():
    assert is_assistant_meta_prompt("do you think i am on track") is True


def test_memory_meta_prompt():
    assert is_assistant_meta_prompt("what do you remember about my week") is True


def test_meta_prompt_with_intervening_clause():
    assert is_assistant_meta_prompt("can you tell me what you know about my projects") is True


def test_third_party_question_is_not_meta():
    # No second-person address: this is content, not a prompt about the
    # assistant.
    assert is_assistant_meta_prompt("i asked her what she thought of the plan") is False


def test_ordinary_first_person_statement_is_not_meta():
    assert is_assistant_meta_prompt("i finished the migration and it ran clean") is False


def test_meta_prompt_respects_length_bound():
    long_text = "what do you think " + ("about the proposal in some detail " * 5)
    assert len(long_text) > MAX_META_LENGTH
    assert is_assistant_meta_prompt(long_text) is False


# ---------------------------------------------------------------------------
# Model boilerplate
# ---------------------------------------------------------------------------

def test_model_non_answer():
    assert is_model_non_answer("As an AI, I don't have personal experiences or memories.") is True


def test_model_non_answer_is_case_insensitive():
    assert is_model_non_answer("as an ai language model, i cannot do that") is True


def test_ordinary_content_is_not_model_boilerplate():
    assert is_model_non_answer("the deployment finished without incident") is False


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def test_combined_covers_all_three_classes():
    assert is_low_value_content("shorter replies please") is True
    assert is_low_value_content("what do you remember about my week") is True
    assert is_low_value_content("as an ai language model, i cannot do that") is True


def test_combined_passes_substantive_content():
    for text in (
        "we agreed the deadline would slip to next quarter",
        "i finished the migration script and it ran clean on the first try",
        "the appointment went fine and the results came back normal",
    ):
        assert is_low_value_content(text) is False, text


def test_empty_content_is_not_classified():
    # Callers handle empty separately; these predicates must not claim it.
    assert is_low_value_content("") is False
    assert is_style_feedback("") is False
    assert is_assistant_meta_prompt("") is False
    assert is_model_non_answer("") is False


def test_a_question_about_the_users_own_work_is_not_low_value():
    # Regression guard for the behaviour change this module introduced: an
    # ordinary question about the user's own activity used to be dropped by
    # an exact-match literal. It is content, not commentary, and it stays.
    assert is_low_value_content("what have i been focused on this week") is False
