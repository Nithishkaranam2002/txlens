"""Scoring and rendering of the note-NLP evaluation (no database, no model)."""

from pathlib import Path

from txlens_backend.notes.fallback import FallbackRun
from txlens_backend.notes.nlp import Mention
from txlens_evals.notes_nlp import (
    Fact,
    Scores,
    Split,
    llm_rows,
    prf,
    render,
    score_note,
    split_of,
    untracked_spans,
)
from txlens_pipelines.deid_notes import Replacement

ORIGINAL = "Patient: Oakdale\n\nPMH: ESRD 2/2 IgA nephropathy; HTN. Denies edema."
# "Oakdale" (7 characters) was replaced by "Hollis" (6): later offsets shift by one.
SPANS = [Replacement(9, 15, 9, 16, "name", "Oakdale")]


def deid_mention(text: str, **kw: object) -> Mention:
    start = ORIGINAL.index(text) - 1
    values: dict[str, object] = {"negated": False, "temporality": "current",
                                 "experiencer": "patient"} | kw  # fmt: skip
    return Mention(start, start + len(text), text, "condition", 1, bool(values["negated"]),
                   values["temporality"], values["experiencer"], None, 0, 0,  # type: ignore[arg-type]
                   "curated", 1.0)  # fmt: skip


def fact(text: str, concept: str, **kw: object) -> Fact:
    start = ORIGINAL.index(text)
    values: dict[str, object] = {"negated": False, "temporality": "current",
                                 "experiencer": "patient"} | kw  # fmt: skip
    return Fact("N1", concept, "condition", text, start, start + len(text),
                bool(values["negated"]), str(values["temporality"]),
                str(values["experiencer"]))  # fmt: skip


def test_offsets_are_mapped_back_and_attributes_scored() -> None:
    scores = Scores()
    mentions = [
        deid_mention("ESRD", temporality="historical"),
        deid_mention("HTN"),
        deid_mention("edema", negated=False),  # wrong: the note denies it
        deid_mention("IgA nephropathy"),  # real, but the ground truth does not list it
    ]
    facts = [fact("ESRD", "esrd", temporality="historical"), fact("HTN", "hypertension"),
             fact("edema", "edema", negated=True)]  # fmt: skip
    expected = {"esrd": frozenset({1}), "hypertension": frozenset({1}), "edema": frozenset({1})}
    score_note(scores, mentions, facts, SPANS, untracked_spans(ORIGINAL), expected)
    assert (scores.tp["condition"], scores.fp["condition"], scores.fn["condition"]) == (3, 1, 0)
    assert scores.fp_untracked["condition"] == 1
    assert scores.attribute_right["negation"] == 2 and scores.attribute_right["concept"] == 3


def test_missed_fact_is_a_false_negative() -> None:
    scores = Scores()
    score_note(scores, [], [fact("HTN", "hypertension")], SPANS, [], {})
    assert scores.fn["condition"] == 1
    assert ("missed", "condition", "htn") in scores.errors


def test_prf() -> None:
    assert prf(3, 1, 0) == (0.75, 1.0, 6 / 7)
    assert prf(0, 0, 0) == (0.0, 0.0, 0.0)


def test_report_shows_systems_and_llm_cost() -> None:
    scores = {"medspaCy": Scores()}
    scores["medspaCy"].tp["condition"] = 9
    scores["medspaCy"].fn["condition"] = 1
    run = FallbackRun(calls=3, cached=1, input_tokens=1000, output_tokens=100, cost_usd=0.002)
    llm = llm_rows(run, (0.002, 1.0), 10, 100, "ollama/llama3.1:8b")
    report = render([Split("sample", scores, 10, 10)], {"Sample": "10 notes"}, llm)
    assert "| medspaCy | condition | 100.0% | 100.0% | 90.0% | 94.7% |" in report
    assert "2 paid, 1 answered from the cache" in report
    assert "## Honest limits" in report


def test_held_out_split_comes_first_and_keeps_tuning_notes_apart() -> None:
    chosen = {"center_a": ["N1", "N2", "N3"], "center_b": ["N4"]}
    splits = split_of(chosen, tuning={"N2", "N4"})
    assert list(splits) == ["held-out", "tuning"]
    assert splits == {"held-out": {"center_a": ["N1", "N3"]},
                      "tuning": {"center_a": ["N2"], "center_b": ["N4"]}}  # fmt: skip


def test_report_headline_is_the_held_out_split() -> None:
    held_out, tuning = Scores(), Scores()
    held_out.tp["lab"], tuning.tp["lab"] = 8, 2
    report = render(
        [
            Split("held-out", {"medspaCy": held_out}, 8, 8),
            Split("tuning", {"medspaCy": tuning}, 2, 2),
        ],
        {"Sample": "all notes"},
    )
    assert report.index("## Held-out notes") < report.index("## Tuning notes (300 notes, seed 7")
    assert "| Notes | 10 |" in report


def test_attribute_errors_name_their_direction_and_keep_examples() -> None:
    scores = Scores(examples=[])
    text = "x" * 200
    truth = [fact("HTN", "hypertension", temporality="historical")]
    score_note(scores, [deid_mention("HTN")], truth, SPANS, [],
               {"hypertension": frozenset({1})}, text)  # fmt: skip
    assert ("wrong temporality (historical→current)", "hypertension", "htn") in scores.errors
    assert scores.examples and scores.examples[0][0] == "wrong temporality (historical→current)"


def test_review_flags_are_counted_by_true_temporality() -> None:
    from dataclasses import replace

    scores = Scores()
    flagged = replace(deid_mention("HTN"), review_reason="check")
    score_note(scores, [flagged], [fact("HTN", "hypertension", temporality="historical")],
               SPANS, [], {"hypertension": frozenset({1})})  # fmt: skip
    assert scores.review_flagged == {"historical": 1}


def test_call_latency_is_recorded_when_measured_and_read_back_when_cached(
    tmp_path: Path,
) -> None:
    from txlens_evals.notes_nlp import call_latency

    path = tmp_path / "latency.json"
    assert call_latency([], "m", path) is None  # nothing measured yet
    assert call_latency([1000, 3000, 2000], "m", path) == 2000
    assert call_latency([], "m", path) == 2000  # a cached rerun reuses the measurement
