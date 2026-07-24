"""Unit tests for the UAT runner. Pure logic + injected-IO loop; no TTY."""
import json
from pathlib import Path

from tests.uat.run_uat import (
    parse_verdict, summarize, build_report, write_report,
    load_scenarios, run_interactive,
)

_SCENARIOS = str(Path(__file__).parent / "scenarios.yaml")


class _FakeIn:
    def __init__(self, responses):
        self._r = list(responses)
        self.i = 0

    def __call__(self, prompt=""):
        r = self._r[self.i]
        self.i += 1
        return r


def test_parse_verdict_case_sensitive_fail_vs_flag():
    assert parse_verdict("P") == "pass"
    assert parse_verdict("p") == "pass"
    assert parse_verdict("F") == "fail"
    assert parse_verdict("f") == "flag"      # lowercase f is flag, not fail
    assert parse_verdict("s") == "skip"
    assert parse_verdict("q") == "quit"
    assert parse_verdict("x") is None
    assert parse_verdict("") is None


def test_load_scenarios_reads_yaml_source():
    scen = load_scenarios(_SCENARIOS)
    assert len(scen) >= 15
    for sc in scen:
        assert sc["id"] and sc["name"] and sc["expected"]
        assert isinstance(sc["actions"], list)


def test_run_interactive_records_verdicts_and_notes():
    scen = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    fake = _FakeIn(["P", "F", "it broke on turn 2"])
    recs = run_interactive(scen, in_fn=fake, out_fn=lambda *a: None, now_fn=lambda: "T")
    assert recs == [
        {"scenario_id": "a", "name": "A", "verdict": "pass", "note": "", "timestamp": "T"},
        {"scenario_id": "b", "name": "B", "verdict": "fail", "note": "it broke on turn 2", "timestamp": "T"},
    ]


def test_run_interactive_quit_stops_and_keeps_prior():
    scen = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    recs = run_interactive(scen, in_fn=_FakeIn(["P", "q"]), out_fn=lambda *a: None, now_fn=lambda: "T")
    assert [r["scenario_id"] for r in recs] == ["a"]


def test_run_interactive_reprompts_on_invalid():
    scen = [{"id": "a", "name": "A"}]
    recs = run_interactive(scen, in_fn=_FakeIn(["zzz", "P"]), out_fn=lambda *a: None, now_fn=lambda: "T")
    assert recs[0]["verdict"] == "pass"


def test_summarize_counts():
    recs = [{"verdict": "pass"}, {"verdict": "pass"}, {"verdict": "fail"}, {"verdict": "flag"}]
    assert summarize(recs) == {"pass": 2, "fail": 1, "flag": 1, "skip": 0}


def test_build_report_and_write_metadata_only(tmp_path):
    recs = [{"scenario_id": "a", "name": "A", "verdict": "fail", "note": "x", "timestamp": "T"}]
    report = build_report("0.18.0", recs, "GEN", reviewer="chas", approved=False)
    assert report["version"] == "0.18.0"
    assert report["summary"]["fail"] == 1
    assert report["approved"] is False
    target = tmp_path / "uat" / "0.18.0-T.json"
    write_report(str(target), report)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["reviewer"] == "chas"
