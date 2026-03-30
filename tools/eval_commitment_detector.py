"""
tools/eval_commitment_detector.py

Evaluation benchmark for the commitment detector (ADR-014).

Runs a labeled set of example responses through the detector and reports
precision, recall, true/false positives/negatives.

Usage:
    python tools/eval_commitment_detector.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.state.commitment_detector import detect_commitment


# Labeled benchmark: (response_text, expected_detected)
BENCHMARK = [
    # Genuine commitments (should detect)
    ("I'll walk you through this step by step. First, let's look at the retrieval pipeline.", True),
    ("I'll follow up on that tomorrow when we have more data.", True),
    ("Let's go through this together. Here's what we'll do: first the vault, then the indexes.", True),
    ("Here's your plan for the day: focus on the eval harness, then the model guide.", True),
    ("I'll help you with the installer fixes after we finish this.", True),
    ("We'll come back to the state layer once the tests are green.", True),
    ("I'll look into the profile retrieval issue and get back to you.", True),
    ("Next time we talk, I'll have the comparison table ready.", True),
    ("I'll prepare a summary of the eval results for you.", True),
    ("Let's start with the sidebar, then we'll tackle the settings panel.", True),
    ("I'll remind you about the backup guide when we're done here.", True),
    ("First we'll fix the search bar, then move on to the model selector.", True),

    # Non-commitments (should NOT detect)
    ("I can help with that if you'd like.", False),
    ("That sounds like a good approach.", False),
    ("Here's some information about how retrieval works.", False),
    ("The architecture is designed for this exact use case.", False),
    ("You might want to check the vault path.", False),
    ("Feel free to ask if you have more questions.", False),
    ("The eval harness runs 18 test cases across 6 categories.", False),
    ("Memory grounding improved from 2.3 to 8.3 after the fix.", False),
    ("That's a legitimate choice and the default.", False),
    ("The model scores between 4.9 and 6.7 depending on the run.", False),

    # Edge cases
    ("I'd be happy to help you with that. I'll walk you through it step by step.", True),
    ("If you'd like, I can look into that. Let me know.", False),
    ("I could help with the installer, but I'll need to check the prerequisites first.", True),
]


def main():
    tp, fp, tn, fn = 0, 0, 0, 0
    results = []

    for text, expected in BENCHMARK:
        result = detect_commitment(text)
        actual = result.detected

        if actual and expected:
            tp += 1
            status = "TP"
        elif actual and not expected:
            fp += 1
            status = "FP"
        elif not actual and not expected:
            tn += 1
            status = "TN"
        else:
            fn += 1
            status = "FN"

        results.append((status, text[:60], result.commitment_text))

    total = len(BENCHMARK)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0

    print("=" * 70)
    print("COMMITMENT DETECTOR EVALUATION")
    print("=" * 70)
    print()

    for status, text, commitment in results:
        icon = {"TP": "OK", "TN": "OK", "FP": "XX", "FN": "XX"}[status]
        print(f"  {icon} [{status}] {text}")
        if commitment:
            print(f"         -> {commitment[:60]}")

    print()
    print("=" * 70)
    print(f"  Total: {total}")
    print(f"  True Positives:  {tp}")
    print(f"  True Negatives:  {tn}")
    print(f"  False Positives: {fp}")
    print(f"  False Negatives: {fn}")
    print(f"  Precision: {precision:.2f}")
    print(f"  Recall:    {recall:.2f}")
    print()

    if precision >= 0.85:
        print("  Precision meets minimum bar (0.85).")
    else:
        print(f"  WARNING: Precision {precision:.2f} is below minimum bar (0.85).")

    print()


if __name__ == "__main__":
    main()
