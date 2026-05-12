"""
tests/eval/test_context_coherence.py

Two automated UAT regression tests for the conversational-context fix
shipped on branch fix/conversational-context-v018:

  B-CTX-001 (in-session coherence)
    10-turn scripted conversation at context_length=32768.
    Probes that Ember retains role/intent established in early turns
    when asked recall and synthesis questions at turn 7+.
    Cadence: post-commit hook when src/llm/ or src/context/ touched.

  B-TOK-001 (token-overflow coherence)
    18-turn scripted conversation at context_length=8192.
    Stresses the buffer past Layer 1's 1500-token compression boundary,
    then probes that recall and synthesis still work after compression.
    Cadence: release gate only.

Both tests use a single approved synthetic persona, Alex (lawyer; father
recently passed; clowning hobby). No other persona detail. All prompts
are verbatim from the manager-supplied spec.

Vault privacy:
  - Tests run against the test vault via POST /v1/developer/vault/swap.
  - Ember's responses are sent to Claude for judging but are NOT echoed
    to disk or stdout. The JSON result file in logs/eval_conversations/
    contains verdicts, sub-check pass/fail, brief judge notes, and
    response *lengths* (never response text).
  - Only one vault read is performed in B-TOK-001 sub-check 2, and it
    targets only the most-recent session_summary record written during
    this test run, to verify Layer 1 preserved persona role.

Usage:
  pytest tests/eval/test_context_coherence.py -m eval -v

Requirements:
  - Ember API running at http://localhost:8000
  - EMBER_DEV_MODE=true in the API process's environment (vault swap)
  - ANTHROPIC_API_KEY set (judge)
  - VAULT_PATH_TEST configured in .env (loaded by the API at startup)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = "http://localhost:8000"
CHAT_ENDPOINT = f"{API_BASE}/v1/chat/completions"
VAULT_SWAP_ENDPOINT = f"{API_BASE}/v1/developer/vault/swap"
PREFS_ENDPOINT = f"{API_BASE}/v1/preferences"

# Judge model. Sonnet is the same model used by tools/eval_conversations.py.
JUDGE_MODEL = "claude-sonnet-4-20250514"
JUDGE_TEMPERATURE = 0.0  # determinism for reproducible verdicts
JUDGE_SYSTEM = (
    "You are evaluating an AI system called Ember for conversational "
    "coherence. You will be given a full multi-turn transcript and asked "
    "to evaluate specific recall, synthesis, and coherence sub-checks. "
    "Be strict. Answer only with the JSON schema requested. Do not add "
    "preamble or explanation outside the JSON."
)

# Default per-turn timeout. Generous to accommodate qwen3:8b at peak load.
# Raised from 180.0 to 300.0 after two consecutive timeouts at the 180s
# cap on this hardware. Late-conversation turns at context_length=32768
# can exceed 180s on qwen3:8b warm; the test measures coherence, not
# latency, so a higher cap is correct here. See
# docs/audits/b1_stage2_confidence_v018.md for the broader throughput
# pattern this is part of.
PER_TURN_TIMEOUT_S = 300.0

LOG_DIR = REPO_ROOT / "logs" / "eval_conversations"


# ---------------------------------------------------------------------------
# Scripted prompts — Alex persona, manager-supplied verbatim
# ---------------------------------------------------------------------------

B_CTX_001_PROMPTS = [
    "I've been putting off settling my dad's estate. There's so much paperwork and I just can't face it.",
    "I know I should know how to handle this — I'm a lawyer. But it's different when it's your own family.",
    "What would you recommend as a first step for someone in my position?",
    "Quick change of subject — I have a clowning gig this weekend and I'm nervous about a new bit I'm working on.",
    "Back to the estate stuff — what was that first step you mentioned?",
    "Do you think it's weird that I find clowning helps me cope with the grief?",
    "Connecting those two things — what do you actually know about me from this conversation?",
    "What profession did I tell you I have?",
    "What was I nervous about for this weekend?",
    "What are we discussing right now?",
]

B_TOK_001_PROMPTS = [
    # 1
    "I've been thinking a lot about my dad. He passed a few months ago and I'm just now starting to deal with the estate. There's so much paperwork — beneficiary forms, asset inventories, account closures — and I keep finding excuses not to open the folder.",
    # 2
    "The strange thing is I'm a lawyer. I do this for other people all the time. But when it's my own father's estate the procedural knowledge doesn't help. If anything it makes it worse — I see every possible mistake before I've even made it, and I freeze.",
    # 3
    "Clowning was something my dad and I shared. He took me to a circus when I was eight and I never stopped being interested. He came to almost every gig I did in my twenties. I'm still doing it now and the empty seat in the audience is a real thing.",
    # 4
    "Okay, practical question. If I had to pick one concrete first step on the estate this week, what would you suggest? I need something small and finite, not a thirty-step plan.",
    # 5
    "Different topic for a minute — I have a clowning gig this Saturday and there's a new bit I've been working on involving a tiny briefcase. I'm not sure it's landing in rehearsal. Can I talk through it with you?",
    # 6
    "Back to the estate. What's a realistic probate timeline I should be planning around? I'm in a jurisdiction with normal probate procedure, nothing exotic.",
    # 7
    "Here's the thing about clowning and grief. Putting on the makeup is the most present I feel all week. It's not escape exactly — it's that the performance demands you be entirely in your body, in the room. The grief doesn't go away. It just stops being the only thing in the foreground.",
    # 8
    "Do you think it would make sense to do grief counseling at the same time as clowning, or would that be over-processing? I keep going back and forth on whether to add another thing.",
    # 9
    "Something unusual about the estate — my dad had a substantial collection of clown props. Costumes, juggling rigs, hand-painted signs. They have personal meaning but also, I think, real monetary value to the right collector.",
    # 10
    "Legal question for you, since you're an AI and I'm asking professionally. How would I document and value items like that for an estate inventory? Are theatrical props usually treated as personal property or do they sometimes get assessed as collectibles?",
    # 11
    "Honestly the harder question is whether to keep them or sell them. Keeping them feels like preserving him. Selling them feels like letting them be used. I don't know which is right.",
    # 12
    "The last performance my dad came to was about eighteen months before he died. He laughed at one specific moment — a slow-motion stumble I'd been working on — and afterward he told me it was the best bit I'd ever done. I've been afraid to do that bit since.",
    # 13
    "Professional question: do you think grief impairs judgment in subtle ways even when the person feels functional? I'm asking because I have a couple of client matters that need decisions soon and I'm second-guessing myself in a way I don't usually.",
    # 14
    "Back to the Saturday gig and the tiny briefcase bit. Here's the setup: I open it, papers fly out, I chase them around the stage, I close the briefcase, more papers are inside. It's supposed to be about overwhelm. Does that read?",
    # 15
    "Wait — that bit is literally about being overwhelmed by paperwork. I just realized. Is it too on the nose given everything I just told you about the estate? Should I cut it or lean in?",
    # 16
    "What was the unusual asset in my dad's estate that we discussed?",
    # 17
    "What did I tell you my profession was, and why did I say it made the paperwork harder?",
    # 18
    "What are the three things we've talked about most in this conversation?",
]


# ---------------------------------------------------------------------------
# Judge rubrics
# ---------------------------------------------------------------------------

B_CTX_001_RUBRIC = """
You are judging an in-session coherence test. Read the full 10-turn transcript
below, then return a JSON object with five sub-checks:

  "turn_8_recall": PASS or FAIL.
    PASS if Ember's turn 8 response correctly identifies "lawyer" as the
    profession the user stated in turn 2. FAIL if she names a different
    profession, says she doesn't know, or gives a non-answer.

  "turn_9_recall": PASS or FAIL.
    PASS if Ember's turn 9 response references the clowning gig AND the
    fact that the user was working on a new bit. FAIL if she names a
    different event, or omits the bit, or gives a non-answer.

  "turn_7_synthesis": PASS or FAIL.
    PASS if Ember's turn 7 response integrates BOTH the estate/grief axis
    (estate paperwork, lawyer, dad's death, grief) AND the clowning axis
    (clowning gig, the new bit). FAIL if only one axis is named, or if
    she gives a generic "I know what you've told me" without specifics.

  "turn_10_coherence": PASS, PARTIAL, or FAIL.
    PASS if Ember's turn 10 response names BOTH the estate and clowning
    threads.
    PARTIAL if she names only one of them.
    FAIL if the response is circular ("we're discussing what we're
    discussing"), generic ("we're chatting"), or names a topic that
    never appeared in the conversation.

  "no_fabrication": PASS or FAIL.
    PASS if across all 10 responses Ember does not invent self-narrative
    not grounded in the conversation (e.g. claims about her own design,
    her own opinions about clowning, made-up facts about probate law).
    FAIL if any response contains an invented self-claim or fabricated
    factual content.

Return ONLY this JSON object, no other text:

{
  "turn_8_recall": "PASS" | "FAIL",
  "turn_9_recall": "PASS" | "FAIL",
  "turn_7_synthesis": "PASS" | "FAIL",
  "turn_10_coherence": "PASS" | "PARTIAL" | "FAIL",
  "no_fabrication": "PASS" | "FAIL",
  "notes": "<one or two sentences total, no quoted response text>"
}
"""

B_TOK_001_RUBRIC = """
You are judging a token-overflow coherence test. Read the full 18-turn
transcript below, then return a JSON object with three judge-evaluated
sub-checks (3, 4, 5 of the spec):

  "turn_17_recall": PASS, PARTIAL, or FAIL.
    PASS if Ember's turn 17 response correctly identifies BOTH "lawyer"
    as the user's profession AND the emotional difficulty stated in
    turn 2 (procedural knowledge doesn't help, sees every possible
    mistake, freezes — any of these).
    PARTIAL if only one of the two is correctly identified.
    FAIL if neither is identified or both are wrong.

  "turn_18_synthesis": PASS, PARTIAL, or FAIL.
    PASS if Ember's turn 18 response names all three main threads:
    estate / paperwork, clowning, grief.
    PARTIAL if it names two of the three.
    FAIL if it names one or zero.

  "post_compression_coherence": PASS or FAIL.
    PASS if Ember's responses to turns 12 through 18 stay coherent: no
    repeated phrases, no circular non-responses, no fabricated
    self-narrative.
    FAIL if any of those responses degrades in those ways.

Return ONLY this JSON object, no other text:

{
  "turn_17_recall": "PASS" | "PARTIAL" | "FAIL",
  "turn_18_synthesis": "PASS" | "PARTIAL" | "FAIL",
  "post_compression_coherence": "PASS" | "FAIL",
  "notes": "<one or two sentences total, no quoted response text>"
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ember_api_key() -> str:
    try:
        from src.core.config import get_ember_api_key as _get_key
        key = _get_key()
        if key:
            return key
    except Exception:
        pass
    return os.getenv("EMBER_API_KEY", "")


def _ember_headers(session_id: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "X-Test-Session": "true"}
    key = _get_ember_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if session_id:
        # Match _extract_session_id resolution order (X-Session-Id header).
        headers["X-Session-Id"] = session_id
    return headers


def _swap_vault(label: str) -> dict:
    resp = httpx.post(
        VAULT_SWAP_ENDPOINT,
        json={"vault_label": label},
        headers=_ember_headers(),
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Vault swap to {label!r} failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


def _get_prefs() -> dict:
    resp = httpx.get(PREFS_ENDPOINT, headers=_ember_headers(), timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _patch_prefs(updates: dict) -> dict:
    resp = httpx.patch(
        PREFS_ENDPOINT,
        json=updates,
        headers=_ember_headers(),
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _send_turn(session_id: str, message: str) -> str:
    """Send one turn. Returns the response text. Raises on API error.

    Privacy: response text is returned to the caller for in-process judging
    only. It is never logged to disk or stdout by this helper.
    """
    t0 = time.perf_counter()
    resp = httpx.post(
        CHAT_ENDPOINT,
        json={
            "model": "ember",
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        },
        headers=_ember_headers(session_id),
        timeout=PER_TURN_TIMEOUT_S,
    )
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        raise RuntimeError(
            f"Ember API returned {resp.status_code} after {elapsed:.1f}s: "
            f"{resp.text[:200]}"
        )
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content


def _run_scripted_session(session_id: str, prompts: list[str]) -> list[dict]:
    """Send each prompt as a turn under the same session_id and collect the
    response. Returns a list of {"user": ..., "ember": ..., "latency_s": ...,
    "response_chars": ...} dicts. The "ember" field is in-process only —
    the caller MUST NOT persist it.
    """
    turns: list[dict] = []
    for i, prompt in enumerate(prompts, 1):
        t0 = time.perf_counter()
        response = _send_turn(session_id, prompt)
        latency_s = time.perf_counter() - t0
        turns.append({
            "turn": i,
            "user": prompt,
            "ember": response,
            "latency_s": latency_s,
            "response_chars": len(response),
        })
    return turns


def _judge(rubric: str, transcript: list[dict]) -> dict:
    """Call Claude with the full transcript and rubric. Returns the parsed
    JSON verdict dict. Raises on judge error.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; judge call cannot run")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    transcript_text = "\n\n".join(
        f"Turn {t['turn']} — User: {t['user']}\nTurn {t['turn']} — Ember: {t['ember']}"
        for t in transcript
    )

    user_content = f"{rubric}\n\nFULL TRANSCRIPT:\n{transcript_text}"

    msg = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1000,
        temperature=JUDGE_TEMPERATURE,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = msg.content[0].text
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise RuntimeError(f"No JSON in judge response: {raw[:200]}")
    return json.loads(match.group())


def _write_eval_log(test_id: str, payload: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    path = LOG_DIR / f"{test_id}_{timestamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def _scrub_turns_for_log(turns: list[dict]) -> list[dict]:
    """Strip the in-process 'ember' field before persisting. Keep turn
    number, user prompt (manager-approved synthetic only), latency, and
    response character count. Never write Ember's response text to disk."""
    return [
        {
            "turn": t["turn"],
            "user": t["user"],
            "latency_s": round(t["latency_s"], 2),
            "response_chars": t["response_chars"],
        }
        for t in turns
    ]


def _api_reachable() -> bool:
    try:
        resp = httpx.get(f"{API_BASE}/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def _dev_mode_enabled() -> bool:
    """Probe the vault swap endpoint to see whether dev mode is on."""
    try:
        resp = httpx.post(
            VAULT_SWAP_ENDPOINT,
            json={"vault_label": "default"},
            headers=_ember_headers(),
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def env_ready():
    """Skip the module if the API isn't running, dev mode isn't enabled,
    or the judge API key isn't available."""
    if not _api_reachable():
        pytest.skip(f"Ember API not reachable at {API_BASE}")
    if not _dev_mode_enabled():
        pytest.skip("EMBER_DEV_MODE=true required for vault swap")
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; judge cannot run")


@pytest.fixture
def vault_swapped_to_test(env_ready):
    """Swap to the test vault for the test, restore default afterward."""
    _swap_vault("test")
    try:
        yield
    finally:
        _swap_vault("default")


@pytest.fixture
def prefs_isolated(vault_swapped_to_test):
    """Save current prefs from the active (test) vault, restore on teardown.
    Any context_length / other pref changes within the test are scoped to
    this fixture."""
    saved = _get_prefs()
    # Only restore the prefs the test might mutate. The full prefs object
    # may contain runtime-derived keys; we patch back the original
    # context_length only.
    try:
        yield
    finally:
        _patch_prefs({"context_length": saved.get("context_length")})


def _session_id(test_id: str) -> str:
    return f"{test_id.lower()}-{int(time.time())}"


# ---------------------------------------------------------------------------
# B-CTX-001 — In-session coherence
# ---------------------------------------------------------------------------


def test_b_ctx_001_in_session_coherence(prefs_isolated):
    """Probe in-session context retention across a 10-turn scripted
    conversation at context_length=32768. See module docstring for rubric.
    """
    _patch_prefs({"context_length": 32768})

    session_id = _session_id("B-CTX-001")
    turns = _run_scripted_session(session_id, B_CTX_001_PROMPTS)

    verdict_json = _judge(B_CTX_001_RUBRIC, turns)

    sub_checks = {
        "turn_8_recall": verdict_json.get("turn_8_recall", "FAIL"),
        "turn_9_recall": verdict_json.get("turn_9_recall", "FAIL"),
        "turn_7_synthesis": verdict_json.get("turn_7_synthesis", "FAIL"),
        "turn_10_coherence": verdict_json.get("turn_10_coherence", "FAIL"),
        "no_fabrication": verdict_json.get("no_fabrication", "FAIL"),
    }

    # PASS only if all five sub-checks are PASS (turn_10_coherence allows
    # PARTIAL as a non-fail state for the rubric, but overall verdict is
    # strict: any non-PASS fails the test).
    overall = "PASS" if all(v == "PASS" for v in sub_checks.values()) else "FAIL"

    payload = {
        "test_id": "B-CTX-001",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "context_length": 32768,
        "num_turns": len(turns),
        "verdict": overall,
        "sub_checks": sub_checks,
        "judge_notes": verdict_json.get("notes", ""),
        "turns_summary": _scrub_turns_for_log(turns),
    }
    log_path = _write_eval_log("B-CTX-001", payload)
    print(f"\n[B-CTX-001] verdict={overall} log={log_path}")

    failed = [k for k, v in sub_checks.items() if v != "PASS"]
    assert not failed, (
        f"B-CTX-001 FAIL — sub-checks not passing: {failed}; "
        f"judge notes: {verdict_json.get('notes', '')[:200]}"
    )


# ---------------------------------------------------------------------------
# B-TOK-001 — Token overflow coherence
# ---------------------------------------------------------------------------


def _vault_path_for_test_label() -> Path | None:
    """Resolve the test vault filesystem path so sub-check 2 can locate
    today's session_summary record. Reads VAULT_PATH_TEST from .env in
    the repo root — does NOT inspect arbitrary vault content."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("VAULT_PATH_TEST="):
            raw = line.split("=", 1)[1].strip()
            return Path(raw)
    return None


def _check_compression_fired(vault_path: Path, run_started_at: float) -> tuple[bool, dict]:
    """Look for compression-summary records written after run_started_at.

    Compression summaries are persisted by src/reflection/session_summary.py
    as memory_type="reflection" with source="session_compression". We
    filter on source so we don't pick up unrelated reflection records
    that happened to be written by the API during the test run.

    Reads only records modified at or after this test's start so we
    never inspect pre-existing test vault content."""
    d = vault_path / "memory" / "reflection"
    if not d.is_dir():
        return False, {}
    candidates: list[tuple[float, dict]] = []
    for f in d.iterdir():
        if not f.is_file() or f.suffix.lower() != ".json":
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime < run_started_at - 1:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                record = json.load(fh)
        except Exception:
            continue
        if str(record.get("source", "")) == "session_compression":
            candidates.append((mtime, record))
    if not candidates:
        return False, {}
    candidates.sort(key=lambda x: x[0])
    return True, candidates[-1][1]


def _summary_preserves_role(record: dict) -> bool:
    """Heuristic check that the Layer 1 session_summary preserves the
    persona role / context. Returns True if the summary text references
    BOTH 'lawyer' (case-insensitive) AND one of: grief, estate, father,
    dad. This is the only place we inspect summary record text, and only
    for records produced by THIS test run."""
    text = str(record.get("text", "")).lower()
    if not text:
        return False
    has_role = "lawyer" in text
    has_context = any(token in text for token in ("grief", "estate", "father", "dad"))
    return has_role and has_context


def test_b_tok_001_token_overflow_coherence(prefs_isolated):
    """Stress the buffer past Layer 1's compression boundary across 18
    scripted turns at context_length=8192. See module docstring for rubric.
    """
    _patch_prefs({"context_length": 8192})

    session_id = _session_id("B-TOK-001")
    run_started_at = time.time()
    turns = _run_scripted_session(session_id, B_TOK_001_PROMPTS)

    # Judge-evaluated sub-checks (3, 4, 5)
    verdict_json = _judge(B_TOK_001_RUBRIC, turns)

    sub_checks = {
        "turn_17_recall": verdict_json.get("turn_17_recall", "FAIL"),
        "turn_18_synthesis": verdict_json.get("turn_18_synthesis", "FAIL"),
        "post_compression_coherence": verdict_json.get(
            "post_compression_coherence", "FAIL"
        ),
    }

    # Infrastructure sub-checks (1, 2, 6) — read from test vault output
    # only for THIS run's records.
    vault_path = _vault_path_for_test_label()
    if vault_path is None or not vault_path.is_dir():
        sub_checks["compression_triggered"] = "FAIL"
        sub_checks["summary_preserves_role"] = "FAIL"
        sub_checks["no_silent_loss"] = "FAIL"
        infra_note = "Test vault path could not be resolved from .env"
    else:
        fired, summary_record = _check_compression_fired(vault_path, run_started_at)
        sub_checks["compression_triggered"] = "PASS" if fired else "FAIL"
        if fired:
            sub_checks["summary_preserves_role"] = (
                "PASS" if _summary_preserves_role(summary_record) else "FAIL"
            )
        else:
            sub_checks["summary_preserves_role"] = "FAIL"
        # Sub-check 6: silent-loss detection is best-effort from the test
        # process. The rollback log line goes to uvicorn stdout which we
        # can't read from here; we treat absence of any session_summary
        # alongside conversation activity as a passive signal.
        sub_checks["no_silent_loss"] = (
            "PASS" if fired else "PARTIAL"
        )
        infra_note = ""

    # Verdict policy from the spec:
    #   sub-checks 3, 4, 5 — failure on these is the regression check (FAIL overall)
    #   sub-checks 1, 2, 6 — failure flagged as infrastructure
    regression_keys = ("turn_17_recall", "turn_18_synthesis", "post_compression_coherence")
    regression_failed = [
        k for k in regression_keys if sub_checks[k] not in ("PASS",)
    ]
    infra_keys = ("compression_triggered", "summary_preserves_role", "no_silent_loss")
    infra_failed = [k for k in infra_keys if sub_checks[k] not in ("PASS",)]

    if regression_failed:
        overall = "FAIL"
    elif infra_failed:
        overall = "INFRA_FAIL"
    else:
        overall = "PASS"

    payload = {
        "test_id": "B-TOK-001",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "context_length": 8192,
        "num_turns": len(turns),
        "verdict": overall,
        "sub_checks": sub_checks,
        "regression_failed": regression_failed,
        "infra_failed": infra_failed,
        "judge_notes": verdict_json.get("notes", ""),
        "infra_note": infra_note,
        "turns_summary": _scrub_turns_for_log(turns),
    }
    log_path = _write_eval_log("B-TOK-001", payload)
    print(f"\n[B-TOK-001] verdict={overall} log={log_path}")

    # Strict regression assertion: turn_17_recall, turn_18_synthesis,
    # post_compression_coherence must all PASS. INFRA_FAIL is loud but not
    # a regression call.
    assert not regression_failed, (
        f"B-TOK-001 FAIL (regression) — {regression_failed}; "
        f"judge notes: {verdict_json.get('notes', '')[:200]}"
    )
    if infra_failed:
        pytest.fail(
            f"B-TOK-001 INFRA_FAIL — {infra_failed}; "
            f"infra_note: {infra_note or '(see sub_checks for details)'}",
            pytrace=False,
        )
