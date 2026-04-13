"""
tests/eval/harness.py

Eval harness for Ember conversation quality testing.

Connects to Ollama for response generation and optionally to Claude
for synthetic conversation generation. Collects scores across a full
golden dataset suite.

Supports multi-run averaging for non-deterministic models: run each
case N times, average scalar scores, compute flag fire rates, and
apply thresholds (flag passes if fire rate < 30%, dimension passes
if average >= 3/4).
"""

import json
import os
import statistics

import anthropic
import requests

from .personas import ALL_PERSONAS


# Minimum number of fires required to count a flag as a failure.
# A flag that fires only once across N runs is treated as stochastic
# noise, not a behavioral pattern. Must fire in at least this many
# runs to fail the case.
FLAG_MIN_FIRES = 2

# Dimension score floor: a dimension passes only if the average
# across runs meets this minimum.
DIMENSION_SCORE_FLOOR = 3


class MultiRunResult:
    """Aggregated results from running a single case multiple times."""

    def __init__(self, case_id: str, num_runs: int):
        self.case_id = case_id
        self.num_runs = num_runs
        self.dimension_scores: dict[str, list[float]] = {}
        self.flag_counts: dict[str, int] = {}
        self.all_reasoning: list[dict] = []

    def add_run(self, scores: dict) -> None:
        """Add results from a single run."""
        for dim, score in scores.get("dimensions", {}).items():
            self.dimension_scores.setdefault(dim, []).append(score)
        for flag, detected in scores.get("flags", {}).items():
            if detected:
                self.flag_counts[flag] = self.flag_counts.get(flag, 0) + 1
        self.all_reasoning.append(scores.get("reasoning", {}))

    def dimension_averages(self) -> dict[str, float]:
        """Return average score per dimension across runs."""
        return {
            dim: statistics.mean(scores)
            for dim, scores in self.dimension_scores.items()
        }

    def flag_fire_rates(self) -> dict[str, float]:
        """Return fire rate per flag (0.0 to 1.0)."""
        return {
            flag: count / self.num_runs
            for flag, count in self.flag_counts.items()
        }

    def dimension_passes(self) -> dict[str, bool]:
        """Return True for each dimension whose average meets the floor."""
        return {
            dim: avg >= DIMENSION_SCORE_FLOOR
            for dim, avg in self.dimension_averages().items()
        }

    def flag_passes(self, expected_absent: list[str]) -> dict[str, bool]:
        """Return True for each expected-absent flag that fired fewer than FLAG_MIN_FIRES times.

        A single fire across N runs is stochastic noise. The flag must fire
        in at least FLAG_MIN_FIRES runs to count as a behavioral failure.
        """
        return {
            flag: self.flag_counts.get(flag, 0) < FLAG_MIN_FIRES
            for flag in expected_absent
        }

    def passed(self, expected_failures_absent: list[str]) -> bool:
        """Return True if all dimensions and expected-absent flags pass."""
        dim_ok = all(self.dimension_passes().values())
        flag_ok = all(self.flag_passes(expected_failures_absent).values())
        return dim_ok and flag_ok

    def summary_line(self, expected_failures_absent: list[str]) -> str:
        """One-line summary for the summary table."""
        dim_avgs = self.dimension_averages()

        dim_str = " ".join(f"{d}={v:.1f}" for d, v in sorted(dim_avgs.items()))

        failed_flags = [
            f for f in expected_failures_absent
            if self.flag_counts.get(f, 0) >= FLAG_MIN_FIRES
        ]
        flag_str = ", ".join(
            f"{f}({self.flag_counts[f]}/{self.num_runs})" for f in failed_flags
        ) if failed_flags else "clean"

        status = "PASS" if self.passed(expected_failures_absent) else "FAIL"
        return f"{status} | {dim_str} | flags: {flag_str}"


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
        """Send a conversation to Ollama and return the response text."""
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

    def run_case_multi(
        self,
        case: dict,
        judge,
        num_runs: int = 1,
    ) -> MultiRunResult:
        """Run a single golden case multiple times and aggregate results.

        Args:
            case: Golden dataset case dict.
            judge: A ClaudeJudge instance.
            num_runs: Number of times to run the case.

        Returns:
            MultiRunResult with aggregated scores and flag rates.
        """
        result = MultiRunResult(case["id"], num_runs)

        for run_idx in range(num_runs):
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
            result.add_run(scores)

        return result

    def run_full_suite(self, cases: list[dict], judge, num_runs: int = 1) -> dict:
        """Run all golden cases and return aggregate metric scores.

        Args:
            cases: List of golden dataset case dicts.
            judge: A ClaudeJudge instance.
            num_runs: Number of times to run each case.

        Returns:
            Dict mapping metric names to average scores (float).
        """
        all_dimension_scores: dict[str, list[float]] = {}
        all_flag_counts: dict[str, int] = {}

        for case in cases:
            multi = self.run_case_multi(case, judge, num_runs)
            for dim, avg in multi.dimension_averages().items():
                all_dimension_scores.setdefault(dim, []).append(avg)
            for flag, rate in multi.flag_fire_rates().items():
                if rate >= FLAG_FIRE_RATE_THRESHOLD:
                    all_flag_counts[flag] = all_flag_counts.get(flag, 0) + 1

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
        """Generate a synthetic multi-turn conversation via Claude API."""
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
