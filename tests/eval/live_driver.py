"""
tests/eval/live_driver.py

Drives turns through the REAL Ember pipeline (live API) for the grounding and
drift evals, and fetches the retrieval packet a query actually produced.

Isolation is by VAULT SWAP, not the test-session header: the caller runs
tools/eval_helpers.py::swap_to_test_vault first so the seeded corpus is the
active vault, then drives turns WITHOUT X-Test-Session (which would set
skip_vault and disable retrieval - defeating a grounding eval). Mirrors the
live+swap pattern in tests/eval/test_context_coherence.py.

requests is imported lazily inside the network methods so the pure
extract_retrieved_texts helper runs in the default Tier-1 suite.
"""

from __future__ import annotations

# Vault record lists in a context packet that count as "retrieved records" for
# grounding. web_items are live web results, not vault records, so they are
# excluded.
_RETRIEVED_ITEM_KEYS = ("memory_items", "state_items", "reflection_items")


def extract_retrieved_texts(packet: dict) -> list[str]:
    """Pull the text of every retrieved vault record from a /debug-context packet.

    Each item may carry its text under "content" or "text". Items without text
    are skipped. Pure - no network.
    """
    texts: list[str] = []
    for key in _RETRIEVED_ITEM_KEYS:
        for item in packet.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            value = item.get("content") or item.get("text") or ""
            if value:
                texts.append(value)
    return texts


class EmberLiveDriver:
    """Thin client over the live Ember API for eval turns (requests is lazy)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000",
                 api_key: str | None = None, session_id: str = "sess_eval_quality",
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.timeout = timeout

    def _headers(self) -> dict:
        # No X-Test-Session: grounding/drift need real retrieval against the
        # swapped test vault.
        headers = {"Content-Type": "application/json", "X-Session-ID": self.session_id}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def send_turn(self, message: str) -> tuple[str, float]:
        """POST one non-streaming chat turn; return (response_text, latency_s)."""
        import time

        import requests

        payload = {
            "model": "ember-2",
            "stream": False,
            "messages": [{"role": "user", "content": message}],
        }
        start = time.monotonic()
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload, headers=self._headers(), timeout=self.timeout,
        )
        latency = time.monotonic() - start
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return text, latency

    def fetch_retrieved_texts(self, message: str) -> list[str]:
        """GET the context packet for `message` and extract retrieved record texts."""
        import requests

        resp = requests.get(
            f"{self.base_url}/debug-context",
            params={"message": message}, headers=self._headers(), timeout=self.timeout,
        )
        resp.raise_for_status()
        return extract_retrieved_texts(resp.json())
