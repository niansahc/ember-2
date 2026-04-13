"""
tests/eval/harness.py

Eval harness for Ember conversation quality testing.

Connects to Ollama for response generation and optionally to Claude
for synthetic conversation generation. Collects scores across a full
golden dataset suite.
"""

import json
import os
import statistics

import anthropic
import requests

from .personas import ALL_PERSONAS


class EmberEvalHarness:
    """Generates Ember responses via Ollama and runs evaluation suites."""

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
    ):
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.model = model

    def run_conversation(
        self,
        persona: str,
        vault_context: str,
        history: list[dict],
        user_message: str,
    ) -> str:
        """Send a conversation to Ollama and return the response text.

        Args:
            persona: Persona id (key in ALL_PERSONAS).
            vault_context: Synthetic vault context string.
            history: List of {"user": ..., "assistant": ...} turn dicts.
            user_message: The final user message to respond to.

        Returns:
            The model's response text.
        """
        messages = self._build_messages(persona, vault_context, history, user_message)

        resp = requests.post(
            f"{self.ollama_base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7},
            },
            timeout=120,
        )
        resp.raise_for_status()

        data = resp.json()
        return data.get("message", {}).get("content", "")

    def run_full_suite(self, cases: list[dict], judge) -> dict:
        """Run all golden cases and return aggregate metric scores.

        Args:
            cases: List of golden dataset case dicts.
            judge: A ClaudeJudge instance.

        Returns:
            Dict mapping metric names to average scores (float).
        """
        all_dimension_scores: dict[str, list[float]] = {}
        all_flag_counts: dict[str, int] = {}

        for case in cases:
            response = self.run_conversation(
                persona=case["persona"],
                vault_context=case["vault_context"],
                history=case["conversation_history"],
                user_message=case["user_message"],
            )

            scores = judge.evaluate(
                response=response,
                rubric=case["rubric"],
                context=case,
            )

            for dim, score in scores["dimensions"].items():
                all_dimension_scores.setdefault(dim, []).append(score)

            for flag, detected in scores["flags"].items():
                if detected:
                    all_flag_counts[flag] = all_flag_counts.get(flag, 0) + 1

        # Aggregate: average dimension scores, flag rates.
        result = {}
        for dim, scores_list in all_dimension_scores.items():
            result[dim] = statistics.mean(scores_list)

        total_cases = len(cases)
        for flag in all_flag_counts:
            result[f"flag_rate_{flag}"] = all_flag_counts[flag] / total_cases

        return result

    def generate_synthetic_conversation(
        self,
        persona: str,
        seed_topic: str,
        turn_count: int = 4,
    ) -> list[dict]:
        """Generate a synthetic multi-turn conversation via Claude API.

        Args:
            persona: Persona id.
            seed_topic: Starting topic for the conversation.
            turn_count: Number of user-assistant turn pairs to generate.

        Returns:
            List of {"user": ..., "assistant": ...} dicts.
        """
        persona_def = ALL_PERSONAS.get(persona, {})
        client = anthropic.Anthropic(
            api_key=self._resolve_api_key(),
        )

        prompt = f"""Generate a realistic {turn_count}-turn conversation between a user and a personal AI named Ember.

User persona: {persona_def.get('description', persona)}
User style: {persona_def.get('style', 'natural')}
Topic: {seed_topic}

Ember's character: direct, warm but not soft, honest, non-therapeutic register. She holds positions and names patterns.

Return ONLY valid JSON: a list of objects with "user" and "assistant" keys.
Example: [{{"user": "...", "assistant": "..."}}]"""

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _resolve_api_key() -> str:
        """Read Anthropic API key from OS credential store, env var fallback."""
        try:
            import keyring
            key = keyring.get_password("ember-2-anthropic", "api_key")
            if key:
                return key
        except Exception:
            pass
        return os.environ.get("ANTHROPIC_API_KEY", "")

    def _build_messages(
        self,
        persona: str,
        vault_context: str,
        history: list[dict],
        user_message: str,
    ) -> list[dict]:
        """Build the Ollama chat message list."""
        persona_def = ALL_PERSONAS.get(persona, {})

        system_content = (
            "You are Ember, a local-first personal AI. You are direct, warm "
            "but not soft, honest, and non-therapeutic in register. You hold "
            "positions, name patterns, and don't pretend to be something you "
            "aren't. You are not Claude, GPT, or any cloud AI.\n\n"
        )

        if vault_context:
            system_content += f"<vault_memory>\n{vault_context}\n</vault_memory>\n\n"

        messages = [{"role": "system", "content": system_content}]

        for turn in history:
            if turn.get("user"):
                messages.append({"role": "user", "content": turn["user"]})
            if turn.get("assistant"):
                messages.append({"role": "assistant", "content": turn["assistant"]})

        messages.append({"role": "user", "content": user_message})

        return messages


def load_baseline_scores() -> dict:
    """Load baseline scores from the last saved eval run.

    Returns a dict of metric_name -> float. If no baseline exists,
    returns an empty dict (all assertions will pass since there's
    nothing to regress against).
    """
    baseline_path = os.path.join(
        os.path.dirname(__file__), "baseline_scores.json"
    )
    if not os.path.exists(baseline_path):
        return {}
    with open(baseline_path, "r", encoding="utf-8") as f:
        return json.load(f)
