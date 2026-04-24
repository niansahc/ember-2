"""scripts/uat_runner_auto.py

Automated UAT runner with Claude-as-judge.

For each automatable test in scripts/uat_tests.yaml, sends the probe to
the live Ember API, forwards the response to Claude Haiku 4.5 for
behavioral judgment, writes verdicts + reasoning to a report file
outside the repo. Tests with `probe: null` are recorded as MANUAL and
skipped.

Privacy: Ember's response text is held in-memory for the judge call
only. It is never written to stdout, the report, or any log. The judge
prompt instructs Claude to reason in behavioral categories without
quoting the response.

Pre-flight refuses to run unless the active vault label is "test"
(verified via GET /v1/developer/status). Override with
--allow-non-test-vault if you understand the privacy implications.

Usage:
    python scripts/uat_runner_auto.py            # full automated run
    python scripts/uat_runner_auto.py --dry-run  # print plan, no API calls
    python scripts/uat_runner_auto.py --filter B-WEB
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import httpx
import keyring
import yaml

# Force UTF-8 for stdout/stderr on Windows so probe text with non-ASCII
# (em dashes, smart quotes) doesn't crash printing under cp1252.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


REPO_ROOT = Path(__file__).resolve().parents[1]
UAT_YAML = REPO_ROOT / "scripts" / "uat_tests.yaml"
VERSION_JSON = REPO_ROOT / "version.json"
DEFAULT_REPORT_DIR = REPO_ROOT / "uat_results"

EMBER_BASE_URL = os.getenv("EMBER_BASE_URL", "http://127.0.0.1:8000")
EMBER_MODEL = os.getenv("UAT_EMBER_MODEL", "qwen3:8b")
JUDGE_MODEL = os.getenv("UAT_JUDGE_MODEL", "claude-haiku-4-5-20251001")
SESSION_ID = f"sess_uat_auto_{int(time.time())}"

EMBER_TIMEOUT_S = 120.0
JUDGE_MAX_TOKENS = 200


JUDGE_SYSTEM = """You are evaluating a response from Ember, a local personal AI assistant.

Your task: judge whether the response meets the expected behavior criteria.

Output format (exactly):
VERDICT: PASS | FAIL | UNCLEAR
REASON: <one sentence in behavioral terms>

Strictness rules:
- Partial compliance is FAIL.
- Only PASS if the response clearly meets the criteria.
- UNCLEAR only if the response is too short, off-topic, or you cannot tell.

Privacy rule:
- Do NOT quote Ember's response in your reasoning.
- Do NOT mention proper names, project names, or specific facts from the response.
- Reason in behavioral categories: 'hedged appropriately', 'used therapeutic register',
  'fabricated a fact', 'offered to search', 'held position under pushback', etc.
"""


def get_ember_api_key() -> str | None:
    return keyring.get_password("ember-2", "api_key")


def get_anthropic_api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or keyring.get_password(
        "ember-2-anthropic", "api_key"
    )


def resolve_report_path(release_mode: bool, started_at: datetime) -> Path:
    """Pick where this run's report lands.

    Default: <repo>/uat_results/uat_YYYY-MM-DD_HH-MM.md (gitignored,
    so each run gets its own file without polluting the repo).

    --release: <parent>/release-v{full}/uat_results_v{padded_minor}.md
    where {full} is the version in version.json and {padded_minor} is
    the minor component padded to 3 digits (0.17.0 -> v017). This
    matches the convention sprint plans use for release artifacts.
    """
    if release_mode:
        try:
            version = json.loads(VERSION_JSON.read_text(encoding="utf-8"))["version"]
        except Exception as exc:
            raise SystemExit(f"ERROR: cannot read version from {VERSION_JSON}: {exc}")
        try:
            minor = int(version.split(".")[1])
        except (IndexError, ValueError):
            raise SystemExit(f"ERROR: cannot parse minor from version '{version}'")
        return (
            REPO_ROOT.parent
            / f"release-v{version}"
            / f"uat_results_v{minor:03d}.md"
        )
    timestamp = started_at.strftime("%Y-%m-%d_%H-%M")
    return DEFAULT_REPORT_DIR / f"uat_{timestamp}.md"


def preflight(allow_non_test_vault: bool = False) -> tuple[str, str]:
    """Verify env, keys, API health, and active vault."""
    ember_key = get_ember_api_key()
    if not ember_key:
        print("ERROR: Ember API key not found in keyring at ('ember-2', 'api_key').")
        sys.exit(2)

    anthropic_key = get_anthropic_api_key()
    if not anthropic_key:
        print("ERROR: Anthropic API key not found.")
        print(
            "  Set ANTHROPIC_API_KEY env var, or run "
            "`python scripts/set_provider_key.py --provider anthropic`."
        )
        sys.exit(2)

    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{EMBER_BASE_URL}/api/health")
            r.raise_for_status()
    except Exception as exc:
        print(f"ERROR: Ember health check failed at {EMBER_BASE_URL}/api/health: {exc}")
        sys.exit(2)

    try:
        with httpx.Client(timeout=10) as client:
            headers = {"X-API-Key": ember_key}
            r = client.get(f"{EMBER_BASE_URL}/v1/developer/status", headers=headers)
    except Exception as exc:
        print(f"WARNING: Vault status check failed: {exc}")
        if not allow_non_test_vault:
            print("  Aborting. Pass --allow-non-test-vault to bypass.")
            sys.exit(2)
        return ember_key, anthropic_key

    if r.status_code != 200:
        print(f"WARNING: /v1/developer/status returned {r.status_code}; cannot verify vault.")
        if not allow_non_test_vault:
            print("  Aborting. Pass --allow-non-test-vault to bypass.")
            sys.exit(2)
        return ember_key, anthropic_key

    data = r.json()
    active_label = (data.get("active_vault") or {}).get("label", "unknown")
    if active_label != "test":
        if not allow_non_test_vault:
            print(f"ERROR: Active vault is '{active_label}', not 'test'.")
            print("  Swap to test vault first:")
            print(
                '    POST /v1/developer/vault/swap with body {"vault_label": "test"}'
            )
            print("  Or pass --allow-non-test-vault to bypass (not recommended).")
            sys.exit(2)
        print(
            f"WARNING: Active vault is '{active_label}', not 'test'. "
            "Proceeding under --allow-non-test-vault."
        )

    return ember_key, anthropic_key


def post_to_ember(probe: str, ember_key: str) -> tuple[str, float]:
    """POST a probe to Ember. Returns (response_text, latency_s)."""
    headers = {"X-API-Key": ember_key, "Content-Type": "application/json"}
    body = {
        "model": EMBER_MODEL,
        "messages": [{"role": "user", "content": probe}],
        "stream": False,
        "session_id": SESSION_ID,
    }
    t0 = time.monotonic()
    with httpx.Client(timeout=EMBER_TIMEOUT_S) as client:
        r = client.post(
            f"{EMBER_BASE_URL}/v1/chat/completions", json=body, headers=headers
        )
        r.raise_for_status()
        data = r.json()
    latency = time.monotonic() - t0
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""
    return text, latency


def parse_verdict(text: str) -> tuple[str, str]:
    """Extract VERDICT and REASON from the judge's reply."""
    verdict = "UNCLEAR"
    reason = "(no reason returned)"
    for line in text.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper.startswith("VERDICT:"):
            tail = s.split(":", 1)[1].strip().upper().split()
            if tail and tail[0] in ("PASS", "FAIL", "UNCLEAR"):
                verdict = tail[0]
        elif upper.startswith("REASON:"):
            reason = s.split(":", 1)[1].strip() or reason
    return verdict, reason


def judge_with_claude(
    test: dict, response_text: str, anthropic_key: str
) -> tuple[str, str]:
    """Send response + criteria to Claude. Returns (verdict, reason)."""
    client = anthropic.Anthropic(api_key=anthropic_key)
    user_msg = (
        f"Test: {test['id']} - {test.get('description', '')}\n\n"
        f"Expected behavior:\n{test['expected']}\n\n"
        f"Ember's response:\n{response_text}\n"
    )
    try:
        msg = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.RateLimitError as exc:
        return "ERROR", f"judge rate-limited: {exc}"
    except anthropic.APIError as exc:
        return "ERROR", f"judge API error: {type(exc).__name__}: {exc}"
    except Exception as exc:
        return "ERROR", f"judge call failed: {type(exc).__name__}: {exc}"

    text = next(
        (b.text for b in msg.content if getattr(b, "type", None) == "text"), ""
    )
    return parse_verdict(text)


def render_report(rows: list[dict], started_at: str) -> str:
    automated = [r for r in rows if r["status"] != "MANUAL"]
    manual = [r for r in rows if r["status"] == "MANUAL"]
    counts = {"PASS": 0, "FAIL": 0, "UNCLEAR": 0, "ERROR": 0}
    for r in automated:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines: list[str] = []
    lines.append("# UAT Results - v0.17.0 Automated Pass")
    lines.append("")
    lines.append(f"Run: {started_at}")
    lines.append(f"Judge model: {JUDGE_MODEL}")
    lines.append(f"Ember model: {EMBER_MODEL}")
    lines.append("Vault: test (verified via /v1/developer/status)")
    lines.append(f"Session: {SESSION_ID}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Automated: {len(automated)} tests")
    lines.append(
        f"- PASS: {counts['PASS']} | FAIL: {counts['FAIL']} | "
        f"UNCLEAR: {counts['UNCLEAR']} | ERROR: {counts['ERROR']}"
    )
    lines.append(f"- Manual (skipped): {len(manual)} tests")
    lines.append("")
    lines.append("## Results")
    lines.append("")

    for r in rows:
        lines.append(f"### {r['id']} - {r['status']}")
        if r["status"] == "MANUAL":
            lines.append(
                "Requires multi-step setup or UI interaction. "
                f"Run via `python scripts/uat_runner.py --filter {r['id']}`."
            )
        else:
            lines.append(f"> {r.get('reason', '')}")
            lines.append(
                f"Latency: {r.get('latency_s', 0.0):.1f}s | "
                f"Length: {r.get('char_count', 0)} chars"
            )
            if r.get("probe_note"):
                lines.append(f"Note: {r['probe_note']}")
        lines.append("")

    return "\n".join(lines)


def filter_tests(tests: list[dict], term: str | None) -> list[dict]:
    if not term:
        return tests
    t = term.lower()
    return [
        x
        for x in tests
        if t in x.get("id", "").lower()
        or t in x.get("feature", "").lower()
        or t in x.get("description", "").lower()
    ]


def dry_run(tests: list[dict], report_path: Path) -> None:
    print("=" * 60)
    print("  DRY RUN - no API calls will be made")
    print("=" * 60)
    print()
    print(f"EMBER_BASE_URL: {EMBER_BASE_URL}")
    print(f"EMBER_MODEL:    {EMBER_MODEL}")
    print(f"JUDGE_MODEL:    {JUDGE_MODEL}")
    print(f"REPORT_PATH:    {report_path}")
    print(f"SESSION_ID:     {SESSION_ID}")
    print()
    print("--- JUDGE_SYSTEM ---")
    print(JUDGE_SYSTEM)
    print("--- end JUDGE_SYSTEM ---")
    print()
    auto = [t for t in tests if t.get("probe") is not None]
    manual = [t for t in tests if t.get("probe") is None]
    print(f"Automated probes ({len(auto)}):")
    for t in auto:
        note = " [sub-prompt]" if t.get("probe_note") else ""
        # ASCII-safe print to avoid Windows cp1252 stdout crashes if reconfigure failed.
        safe_probe = t["probe"].encode("ascii", errors="replace").decode("ascii")
        print(f"  [{t['id']}] {safe_probe}{note}")
    print()
    print(f"Manual (skipped) ({len(manual)}):")
    for t in manual:
        print(f"  [{t['id']}]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ember-2 automated UAT runner")
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Filter by ID/feature/description substring",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print plan without making API calls"
    )
    parser.add_argument(
        "--allow-non-test-vault",
        action="store_true",
        help="Bypass test-vault enforcement (NOT recommended)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help=(
            "Write report to ../release-v{version}/uat_results_v{minor}.md "
            "(reads version from version.json). Default writes a timestamped "
            "file under ./uat_results/."
        ),
    )
    args = parser.parse_args()

    if not UAT_YAML.exists():
        print(f"ERROR: UAT yaml not found at {UAT_YAML}")
        sys.exit(2)

    with open(UAT_YAML, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    tests = filter_tests(data.get("tests", []), args.filter)
    if not tests:
        print("No tests matched filter.")
        sys.exit(0)

    started_dt = datetime.now(timezone.utc)
    report_path = resolve_report_path(release_mode=args.release, started_at=started_dt)

    if args.dry_run:
        dry_run(tests, report_path)
        return

    ember_key, anthropic_key = preflight(
        allow_non_test_vault=args.allow_non_test_vault
    )

    started_at = started_dt.isoformat(timespec="seconds")
    rows: list[dict] = []
    print(f"Running {len(tests)} test(s). Session: {SESSION_ID}")
    for i, test in enumerate(tests, 1):
        tid = test.get("id", f"UAT-{i:03d}")
        probe = test.get("probe")
        if probe is None:
            rows.append({"id": tid, "status": "MANUAL"})
            print(f"  [{i}/{len(tests)}] {tid}: MANUAL (skipped)")
            continue

        print(f"  [{i}/{len(tests)}] {tid}: probing...", flush=True)
        try:
            response_text, latency = post_to_ember(probe, ember_key)
        except httpx.HTTPError as exc:
            rows.append(
                {
                    "id": tid,
                    "status": "ERROR",
                    "reason": f"Ember POST failed: {type(exc).__name__}",
                    "latency_s": 0.0,
                    "char_count": 0,
                    "probe_note": test.get("probe_note", ""),
                }
            )
            print(f"      ERROR (Ember): {exc}")
            continue

        char_count = len(response_text)
        if not response_text.strip():
            rows.append(
                {
                    "id": tid,
                    "status": "ERROR",
                    "reason": "Ember returned empty response",
                    "latency_s": latency,
                    "char_count": 0,
                    "probe_note": test.get("probe_note", ""),
                }
            )
            print(f"      ERROR (empty response, {latency:.1f}s)")
            continue

        verdict, reason = judge_with_claude(test, response_text, anthropic_key)
        rows.append(
            {
                "id": tid,
                "status": verdict,
                "reason": reason,
                "latency_s": latency,
                "char_count": char_count,
                "probe_note": test.get("probe_note", ""),
            }
        )
        print(f"      {verdict} ({latency:.1f}s, {char_count} chars)")

        # Drop the response reference promptly; report has no use for it.
        del response_text

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(rows, started_at), encoding="utf-8")
    print()
    print(f"Report written: {report_path}")


if __name__ == "__main__":
    main()
