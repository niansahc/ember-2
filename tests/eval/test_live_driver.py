"""
tests/eval/test_live_driver.py

Unit tests for the pure parts of the live driver (packet extraction). The
network calls (send_turn / fetch_packet) import requests lazily and run only in
the live release-gate run.
"""

from tests.eval.live_driver import extract_retrieved_texts


def test_extract_pulls_text_from_all_retrieved_item_lists():
    packet = {
        "memory_items": [{"content": "deadline is Friday"}, {"content": "sync is Tuesday"}],
        "state_items": [{"text": "current focus: migration"}],
        "reflection_items": [],
        "web_items": [{"content": "ignored - not a vault record"}],
    }
    texts = extract_retrieved_texts(packet)
    assert "deadline is Friday" in texts
    assert "sync is Tuesday" in texts
    assert "current focus: migration" in texts
    # web items are not retrieved vault records for grounding purposes
    assert "ignored - not a vault record" not in texts


def test_extract_tolerates_missing_keys_and_empty():
    assert extract_retrieved_texts({}) == []
    assert extract_retrieved_texts({"memory_items": []}) == []


def test_extract_skips_items_without_text():
    packet = {"memory_items": [{"score": 0.9}, {"content": ""}, {"content": "real"}]}
    assert extract_retrieved_texts(packet) == ["real"]
