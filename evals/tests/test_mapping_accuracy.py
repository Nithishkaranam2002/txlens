"""Condition mapping vs the sent codes: outcomes and the report (no database)."""

import json
from pathlib import Path

from txlens_evals.mapping_accuracy import (
    Coded,
    Truth,
    outcome,
    read_coded,
    render,
    score,
)
from txlens_pipelines.textmatch.matcher import ConceptMatcher, Match, Term

TRUTH = Truth(
    code_id={"44054006": 10, "46177005": 20},
    accepted={"44054006": frozenset({10, 11}), "46177005": frozenset({20, 21, 22})},
)


def test_outcomes() -> None:
    assert outcome(Match(10, "exact", 1.0), "44054006", TRUTH) == "exact"
    assert outcome(Match(11, "exact", 1.0), "44054006", TRUTH) == "more specific"
    assert outcome(Match(22, "exact", 1.0), "46177005", TRUTH) == "in hierarchy"
    assert outcome(Match(99, "fuzzy", 0.9), "44054006", TRUTH) == "wrong"
    assert outcome(Match(None, "review", 0.0), "44054006", TRUTH) == "unmapped"


def test_coded_conditions_are_read_and_scored(tmp_path: Path) -> None:
    folder = tmp_path / "center_a"
    folder.mkdir()
    bundle = {"entry": [
        {"resource": {"resourceType": "Condition", "code": {"text": "Type 2 diabetes",
                      "coding": [{"code": "44054006"}]}}},
        {"resource": {"resourceType": "Condition", "code": {"text": "uncoded"}}},
    ]}  # fmt: skip
    (folder / "p.json").write_text(json.dumps(bundle))
    coded = read_coded(tmp_path)
    assert coded == [Coded("center_a", "44054006", "Type 2 diabetes")]
    matcher = ConceptMatcher([Term(11, "Type 2 diabetes")], {11: "Type 2 diabetes"}, {})
    results = {"vocabulary only": score(coded, matcher, TRUTH),
               "curated + vocabulary": score(coded, matcher, TRUTH)}  # fmt: skip
    assert results["vocabulary only"][0].outcome == "more specific"
    assert "| vocabulary only | 100.0% (100.0%-100.0%) |" in render(coded, results)
